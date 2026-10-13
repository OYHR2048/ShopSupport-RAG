"""Document retrieval node."""

from __future__ import annotations

import time

from app.agents.state import AgentState
from app.config.settings import Settings
from app.core.enums import RetrievalStrategy
from app.observability.trace import current_trace
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.query_rewrite import QueryRewriter
from app.retrieval.reranker import DocumentReranker
from app.retrieval.self_rag import SelfRAGLoop
from app.schemas.common import LatencyBreakdown


async def retrieval_node(
    state: AgentState,
    *,
    retriever: HybridRetriever,
    reranker: DocumentReranker,
    rewriter: QueryRewriter,
    self_rag: SelfRAGLoop,
    settings: Settings,
) -> dict:
    started = time.perf_counter()
    query = state["user_query"]
    strategy = state.get("strategy")
    top_k = (
        settings.retrieval_top_k_complex
        if strategy == RetrievalStrategy.MULTI
        else settings.retrieval_top_k_simple
    )
    try:
        if strategy == RetrievalStrategy.MULTI:
            chunks = await self_rag.retrieve(query, top_k=top_k)
            trace = current_trace.get()
            rewritten = [q for hop in trace.retrieval for q in hop["queries"]] if trace else [query]
        else:
            rewritten = await rewriter.rewrite(query)
            candidates = await retriever.retrieve(rewritten[0], top_k=top_k)
            chunks = await reranker.rerank(rewritten[0], candidates, top_n=settings.rerank_top_n)
            grade = await self_rag._grade(query, chunks)
            trace = current_trace.get()
            if trace:
                trace.retrieval.append(
                    {
                        "hop": 1,
                        "queries": rewritten,
                        "chunk_ids": [c.chunk_id for c in chunks],
                        "grade": grade.model_dump(),
                    }
                )
                trace.stop_reason = (
                    "evidence_sufficient"
                    if grade.sufficient
                    else (
                        "grader_unavailable"
                        if grade.rationale == "grader_unavailable"
                        else "single_insufficient"
                    )
                )
    except Exception as exc:
        # 失败以状态返回，不能当成检索成功，也不能遗漏后端降级原因。
        trace = current_trace.get()
        if trace:
            trace.warnings.append("检索或重排失败：" + type(exc).__name__)
            trace.stop_reason = "retrieval_error"
        chunks, rewritten = [], [query]
    trace = current_trace.get()
    sufficient = (
        trace.retrieval[-1]["grade"]["sufficient"] if trace and trace.retrieval else bool(chunks)
    )
    latency = state.get("latency") or LatencyBreakdown()
    latency.retrieval_ms = (time.perf_counter() - started) * 1000
    return {
        "retrieved_chunks": chunks,
        "rewritten_queries": rewritten,
        "latency": latency,
        "refused": not sufficient,
    }
