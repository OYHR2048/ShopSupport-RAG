"""Answer generation and refusal nodes."""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, Field

from app.agents.state import AgentState
from app.core.enums import RetrievalStrategy
from app.core.exceptions import LLMOutputError, LLMProviderError
from app.core.streaming import get_token_sink
from app.observability.trace import current_trace
from app.prompts.answer import ANSWER_SYSTEM, ANSWER_USER
from app.providers.base import LLMProvider, LLMResponse
from app.providers.json_parser import parse_model
from app.schemas.common import LatencyBreakdown, TokenUsage
from app.schemas.retrieval import Citation


class AnswerPayload(BaseModel):
    answer: str
    citation_chunk_ids: list[str] = Field(default_factory=list)
    refused: bool = False


async def generate_node(state: AgentState, llm: LLMProvider) -> AgentState:
    started = time.perf_counter()
    chunks = state.get("retrieved_chunks") or []
    if state.get("refused"):
        return {
            "final_answer": "现有证据不足或证据检查不可用，暂时无法给出可靠结论。请补充商品、时间等信息或转人工。",
            "refused": True,
            "citations": [],
        }
    tools = state.get("tool_results") or []
    pending = state.get("pending_action")
    if pending:
        answer = pending.summary
        latency = state.get("latency") or LatencyBreakdown()
        latency.llm_ms = (time.perf_counter() - started) * 1000
        return {
            "final_answer": answer,
            "citations": [],
            "refused": False,
            "latency": latency,
        }
    evidence = (
        "\n\n".join(f"[{c.chunk_id}] {c.title}\n{c.content}" for c in chunks[:10]) or "(无检索结果)"
    )
    tool_text = json.dumps([t.model_dump() for t in tools], ensure_ascii=False) if tools else "(无)"
    intent = state.get("intent")
    strategy = state.get("strategy") or RetrievalStrategy.DIRECT
    if strategy == RetrievalStrategy.DIRECT:
        return {
            "final_answer": "你好，我是演示商店客服。可以咨询政策，或查询模拟订单 ORD10001 的物流。",
            "citations": [],
            "refused": False,
        }
    provider = getattr(llm, "provider", llm)
    is_demo = type(provider).__name__ in {"FakeLLMProvider", "DemoLLMProvider"} and not getattr(
        provider, "canned", {}
    )
    if is_demo:
        # 摘录按完整 chunk 生成，引用与使用的片段保持一一对应。
        used = chunks[:10]
        if tools:
            answer = "模拟业务结果（不代表真实支付或物流）：\n" + "\n".join(
                f"{t.name}: "
                + (json.dumps(t.output, ensure_ascii=False) if t.success else (t.error or "失败"))
                for t in tools
            )
        elif used:
            answer = "【离线演示：以下为政策原文摘录】\n" + "\n\n".join(
                f"[{c.chunk_id}] {c.content}" for c in used
            )
        else:
            return {
                "final_answer": "未找到可靠证据，请补充订单号、商品等信息或转人工。",
                "refused": True,
                "citations": [],
            }
        return {
            "final_answer": answer,
            "refused": False,
            "citations": [
                Citation(
                    document_id=c.document_id,
                    title=c.title,
                    chunk_id=c.chunk_id,
                    section=c.section,
                    source=c.metadata.get("source", ""),
                    excerpt=c.content,
                )
                for c in used
            ]
            if not tools
            else [],
            "extra": {**(state.get("extra") or {}), "answer_backend": "demo-extractive"},
        }
    messages = [
        {"role": "system", "content": ANSWER_SYSTEM},
        *(
            [{"role": "system", "content": "历史摘要：" + state["summary"]}]
            if state.get("summary")
            else []
        ),
        *[{"role": m.role, "content": m.content} for m in (state.get("messages") or [])[-8:]],
        {
            "role": "user",
            "content": ANSWER_USER.format(
                query=state["user_query"],
                intent=intent.label.value if intent else "unknown",
                strategy=strategy.value,
                evidence=evidence,
                tool_results=tool_text,
            ),
        },
    ]
    try:
        response = await _generate_or_stream(llm, messages)
        payload = parse_model(response.content, AnswerPayload)
        usage = response.usage
    except (LLMProviderError, LLMOutputError):
        if (trace := current_trace.get()) is not None:
            trace.warnings.append("生成失败，尝试证据摘录后备。")
        payload = AnswerPayload(
            answer=_extractive_fallback(state["user_query"], evidence, tool_text),
            citation_chunk_ids=[c.chunk_id for c in chunks[:1]],
        )
        usage = TokenUsage()
    citations = [
        Citation(
            document_id=chunk.document_id,
            title=chunk.title,
            chunk_id=chunk.chunk_id,
            section=chunk.section,
            source=chunk.metadata.get("source", ""),
            excerpt=chunk.content,
        )
        for chunk in chunks
        if chunk.chunk_id in set(payload.citation_chunk_ids)
    ]
    latency = state.get("latency") or LatencyBreakdown()
    latency.llm_ms = (time.perf_counter() - started) * 1000
    prev_usage = state.get("token_usage") or TokenUsage()
    merged = TokenUsage(
        prompt_tokens=prev_usage.prompt_tokens + usage.prompt_tokens,
        completion_tokens=prev_usage.completion_tokens + usage.completion_tokens,
        total_tokens=prev_usage.total_tokens + usage.total_tokens,
    )
    return {
        "final_answer": payload.answer,
        "citations": citations,
        "refused": payload.refused,
        "latency": latency,
        "token_usage": merged,
    }


async def refuse_node(state: AgentState) -> AgentState:
    return {
        "final_answer": "这个问题超出电商客服范围，我无法协助。如需购物、订单、物流或售后帮助，请换个问题。",
        "refused": True,
        "citations": [],
    }


async def _generate_or_stream(llm: LLMProvider, messages: list[dict[str, str]]):
    sink = get_token_sink()
    stream_fn = getattr(llm, "stream", None)
    if sink is None or stream_fn is None:
        return await llm.generate(
            messages,
            temperature=0.2,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
    pieces: list[str] = []
    async for token in stream_fn(
        messages,
        temperature=0.2,
        max_tokens=700,
        response_format={"type": "json_object"},
    ):
        if token:
            pieces.append(token)
            await sink(token)
    return LLMResponse(content="".join(pieces), usage=TokenUsage(), model="")


def _extractive_fallback(query: str, evidence: str, tool_text: str) -> str:
    if evidence and evidence != "(无检索结果)":
        # 仅摘录首个 chunk，并保留它的 ID；不能跳过首段后仍引用首段。
        block = evidence.split("\n\n[", 1)[0]
        heading, _, body = block.partition("\n")
        marker = heading.split(" ", 1)[0]
        return f"根据资料 {marker}：{body[:240]}\n如需更精确答复，请补充信息。"
    if tool_text and tool_text != "(无)":
        return "已查询到相关系统数据，但生成模型暂时不可用。请稍后重试或转人工。"
    return "暂时无法基于现有信息给出可靠答复，请补充订单号或转人工。"
