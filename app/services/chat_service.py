"""Chat orchestration: graph execution, streaming and persistence."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.graph import build_graph
from app.agents.state import AgentState
from app.config.settings import Settings
from app.core.exceptions import AppError, GuardrailError, RateLimitError
from app.core.ids import new_id
from app.core.request_scope import request_session
from app.infra.rate_limit import RateLimiter
from app.observability.metrics import REQUEST_LATENCY, REQUESTS, ROUTING, STAGE_LATENCY, TOKEN_USAGE
from app.providers.base import LLMProvider
from app.repositories.session_repository import SessionRepository
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.reranker import DocumentReranker
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.common import LatencyBreakdown, TokenUsage
from app.tools.factory import build_tool_registry
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        *,
        settings: Settings,
        llm: LLMProvider,
        retriever: HybridRetriever,
        reranker: DocumentReranker,
        session_factory: async_sessionmaker[AsyncSession],
        rate_limiter: RateLimiter,
        registry: ToolRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._retriever = retriever
        self._reranker = reranker
        self._session_factory = session_factory
        self._rate_limiter = rate_limiter
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.registry = registry or build_tool_registry(retriever)
        self._graph = build_graph(
            llm=llm,
            settings=settings,
            retriever=retriever,
            reranker=reranker,
            registry=self.registry,
        )

    async def chat(self, request: ChatRequest, *, trace_id: str) -> ChatResponse:
        # 确认与数据库提交处于同一会话锁内；基础版本只支持一个服务进程。
        request = request.model_copy(update={"session_id": request.session_id or new_id("sess_")})
        assert request.session_id is not None
        async with self._locks[request.session_id]:
            from app.observability.trace import Trace, current_trace

            token = current_trace.set(Trace())
            try:
                return await self._chat_unlocked(request, trace_id=trace_id)
            finally:
                current_trace.reset(token)

    async def _chat_unlocked(self, request: ChatRequest, *, trace_id: str) -> ChatResponse:
        await self._enforce_rate_limit(request.user_id)
        started = time.perf_counter()
        session_id = request.session_id or new_id("sess_")
        async with request_session(self._session_factory) as db:
            state = await self._initial_state(db, request, session_id, trace_id)
            from app.agents.nodes.executor import executor_node
            from app.agents.nodes.generate import generate_node
            from app.core.enums import RetrievalStrategy
            from app.repositories.actions import load_action, save_action
            from app.schemas.agent import AgentPlan, PlanStep

            if request.confirm_action_id:
                row, proposal = await load_action(
                    db, request.confirm_action_id, session_id, request.user_id
                )
                if row.result_json:
                    replay = ChatResponse.model_validate_json(row.result_json)
                    replay.trace_id = trace_id
                    replay.diagnostics["idempotent_replay"] = True
                    replay.diagnostics["llm_calls"] = 0
                    replay.diagnostics["network_attempts"] = 0
                    replay.diagnostics["model_calls"] = []
                    replay.diagnostics["backend_calls"] = []
                    replay.diagnostics["retrieval"] = []
                    replay.diagnostics["retry_count"] = 0
                    replay.latency = LatencyBreakdown()
                    from app.services.runtime import runtime_status

                    replay.runtime = runtime_status(self)
                    replay.token_usage = TokenUsage()
                    self._observe(replay, started)
                    return replay
                state["strategy"] = RetrievalStrategy.AGENT
                state["user_query"] = "用户已确认以下模拟操作：" + proposal.summary
                state["extra"]["resolved_query"] = state["user_query"]
                state["plan"] = AgentPlan(
                    steps=[
                        PlanStep(
                            step_id=1,
                            goal="confirmed action",
                            tool_name=proposal.tool_name,
                            arguments=proposal.arguments,
                        )
                    ]
                )
                state.update(
                    await executor_node(state, registry=self.registry, settings=self._settings)
                )
                state.update(await generate_node(state, llm=self._llm))
                from app.agents.nodes.faithfulness import faithfulness_node
                from app.guardrails.hallucination import FaithfulnessChecker

                state.update(
                    await faithfulness_node(
                        state, checker=FaithfulnessChecker(self._llm), llm=self._llm
                    )
                )
                result = state
            else:
                result = await self._invoke(state)
            response = self._to_response(result, session_id=session_id, trace_id=trace_id)
            if response.pending_action:
                await save_action(db, response.pending_action, session_id, request.user_id)
            if request.confirm_action_id:
                row.result_json = response.model_dump_json()
            await self._persist(db, request, response, summary=result.get("summary") or "")
            await db.commit()
        self._observe(response, started)
        return response

    async def stream(self, request: ChatRequest, *, trace_id: str) -> AsyncIterator[dict]:
        """缓冲到忠实度检查结束再输出，避免先泄露未通过检查的答案。"""
        yield {"event": "started", "trace_id": trace_id}
        response = await self.chat(request, trace_id=trace_id)
        yield {
            "event": "node",
            "node": "completed",
            "strategy": response.strategy.value if response.strategy else None,
        }
        yield {"event": "answer", "data": response.model_dump(mode="json")}
        yield {"event": "done", "trace_id": trace_id}

    async def _enforce_rate_limit(self, user_id: str) -> None:
        allowed = await self._rate_limiter.allow(f"user:{user_id}")
        if not allowed:
            raise RateLimitError()

    async def _initial_state(
        self,
        db: AsyncSession,
        request: ChatRequest,
        session_id: str,
        trace_id: str,
    ) -> AgentState:
        sessions = SessionRepository(db)
        session = await sessions.get_or_create(session_id, request.user_id)
        history = await sessions.recent_messages(session_id)
        from app.memory.compression import apply_compression

        history, _, _, _ = apply_compression(history, [], session.summary, self._settings)
        query = request.message
        # 只对明确追问补足必要上下文，保持当前输入和原问题均可审计。
        import re

        if len(query) < 35 and any(
            cue in query for cue in ("那", "它", "这个", "运费", "确认", "到哪")
        ):
            previous = next((m.content for m in reversed(history) if m.role == "user"), "")
            if not re.search(r"ORD\d+", query, re.I):
                order_context = next(
                    (
                        m.content
                        for m in reversed(history)
                        if m.role == "user" and re.search(r"ORD\d+", m.content, re.I)
                    ),
                    previous,
                )
                order = re.search(r"ORD\d+", order_context, re.I)
                if order:
                    query += " " + order.group(0)
                elif previous and any(cue in query for cue in ("那", "它", "这个", "运费")):
                    query = previous + "；追问：" + query
        return {
            "trace_id": trace_id,
            "session_id": session_id,
            "user_id": request.user_id,
            "user_query": query,
            "messages": history,
            "summary": session.summary,
            "retrieved_chunks": [],
            "tool_results": [],
            "citations": [],
            "refused": False,
            "step_count": 0,
            "token_usage": TokenUsage(),
            "latency": LatencyBreakdown(),
            "confirm_action_id": request.confirm_action_id,
            "idempotency_key": request.idempotency_key,
            "extra": {"original_query": request.message, "resolved_query": query},
        }

    async def _invoke(self, state: AgentState) -> AgentState:
        try:
            # LangGraph widens the state to dict[str, Any]; the graph contract is AgentState.
            return cast(AgentState, await self._graph.ainvoke(state))
        except GuardrailError as exc:
            return {
                **state,
                "final_answer": exc.message,
                "refused": True,
                "error": exc.error_code.value,
            }
        except AppError:
            raise

    async def _persist(
        self,
        db: AsyncSession,
        request: ChatRequest,
        response: ChatResponse,
        *,
        summary: str = "",
    ) -> None:
        sessions = SessionRepository(db)
        await sessions.append_message(response.session_id, "user", request.message)
        await sessions.append_message(response.session_id, "assistant", response.answer)
        if summary:
            await sessions.save_summary(response.session_id, summary)

    def _to_response(
        self,
        result: AgentState,
        *,
        session_id: str,
        trace_id: str,
    ) -> ChatResponse:
        answer = result.get("final_answer") or "暂时无法回答，请稍后重试。"
        strategy = result.get("strategy")
        intent = result.get("intent")
        from app.observability.trace import current_trace
        from app.services.runtime import runtime_status

        trace = current_trace.get()
        diagnostics = dict(result.get("extra") or {})
        diagnostics.update(
            {
                "rewritten_queries": result.get("rewritten_queries") or [],
                "evidence": [c.model_dump() for c in result.get("retrieved_chunks") or []],
                "tools": [t.model_dump() for t in result.get("tool_results") or []],
                "llm_calls": len(trace.calls) if trace else 0,
                "network_attempts": trace.network_attempts if trace else 0,
                "backend_calls": trace.backend_calls if trace else [],
                "model_calls": trace.calls if trace else [],
                "retrieval": trace.retrieval if trace else [],
                "stop_reason": trace.stop_reason if trace else "",
                "retry_count": max(0, len({x.get("hop", 1) for x in trace.retrieval}) - 1)
                if trace
                else 0,
            }
        )
        return ChatResponse(
            answer=answer,
            session_id=session_id,
            trace_id=trace_id,
            intent=intent.label if intent else None,
            complexity=result.get("query_complexity"),
            strategy=strategy,
            citations=result.get("citations") or [],
            pending_action=result.get("pending_action"),
            refused=bool(result.get("refused")),
            error_code=result.get("error"),
            token_usage=trace.usage if trace else (result.get("token_usage") or TokenUsage()),
            latency=result.get("latency") or LatencyBreakdown(),
            runtime=runtime_status(self),
            diagnostics=diagnostics,
        )

    def _observe(self, response: ChatResponse, started: float) -> None:
        response.latency.llm_ms = sum(c["ms"] for c in response.diagnostics.get("model_calls", []))
        response.latency.rerank_ms = sum(
            c["ms"]
            for c in response.diagnostics.get("backend_calls", [])
            if c["operation"] == "rerank"
        )
        total_ms = (time.perf_counter() - started) * 1000
        response.latency.total_ms = total_ms
        REQUESTS.labels(
            strategy=(response.strategy.value if response.strategy else "unknown"),
            intent=(response.intent.value if response.intent else "unknown"),
            status="ok" if not response.refused else "refused",
        ).inc()
        if response.strategy:
            ROUTING.labels(strategy=response.strategy.value).inc()
        REQUEST_LATENCY.observe(total_ms / 1000)
        STAGE_LATENCY.labels(stage="intent").observe(response.latency.intent_ms / 1000)
        STAGE_LATENCY.labels(stage="retrieval").observe(response.latency.retrieval_ms / 1000)
        STAGE_LATENCY.labels(stage="llm").observe(response.latency.llm_ms / 1000)
        TOKEN_USAGE.labels(kind="total").inc(response.token_usage.total_tokens)
        logger.info(
            "chat_completed",
            extra={
                "trace_id": response.trace_id,
                "strategy": response.strategy.value if response.strategy else None,
                "intent": response.intent.value if response.intent else None,
                "latency_ms": round(total_ms, 1),
            },
        )
