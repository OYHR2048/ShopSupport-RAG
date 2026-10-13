"""Self-RAG style retrieve → grade → optionally hop again.

Reflection is implemented as structured LLM JSON, not special tokens,
so it stays schema-validated and testable.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.config.settings import Settings
from app.core.exceptions import LLMOutputError, LLMProviderError
from app.observability.trace import current_trace
from app.prompts.self_rag import GRADE_SYSTEM, GRADE_USER
from app.providers.base import LLMProvider
from app.providers.json_parser import parse_model
from app.retrieval.evidence import lexical_check
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.query_rewrite import QueryRewriter
from app.retrieval.reranker import DocumentReranker
from app.schemas.retrieval import RetrievedChunk


class GradeResult(BaseModel):
    relevant: bool
    sufficient: bool
    missing_aspect: str = ""
    rationale: str = ""


class SelfRAGLoop:
    def __init__(
        self,
        retriever: HybridRetriever,
        reranker: DocumentReranker,
        rewriter: QueryRewriter,
        llm: LLMProvider,
        settings: Settings,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker
        self._rewriter = rewriter
        self._llm = llm
        self._settings = settings

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
    ) -> list[RetrievedChunk]:
        accumulated: dict[str, RetrievedChunk] = {}
        current_query = query
        missing = ""
        trace = current_trace.get()
        for _hop in range(self._settings.max_retrieval_hops):
            rewritten = await self._rewriter.rewrite(current_query, missing_aspect=missing)
            # 保留原问题，执行所有改写；后续缺失方面只补充检索，不替换原始目标。
            queries = list(dict.fromkeys(rewritten + ([query] if query not in rewritten else [])))[
                :4
            ]
            for hop_query in queries:
                candidates = await self._retriever.retrieve(hop_query, top_k=top_k)
                reranked = await self._reranker.rerank(
                    hop_query, candidates, top_n=self._settings.rerank_top_n
                )
                for chunk in reranked:
                    accumulated[chunk.chunk_id] = chunk
            chunks = list(accumulated.values())[: self._settings.rerank_top_n * 2]
            grade = await self._grade(query, chunks)
            if trace:
                trace.retrieval.append(
                    {
                        "hop": _hop + 1,
                        "queries": queries,
                        "chunk_ids": [c.chunk_id for c in chunks],
                        "grade": grade.model_dump(),
                    }
                )
            if grade.sufficient:
                if trace:
                    trace.stop_reason = "evidence_sufficient"
                break
            if grade.rationale == "grader_unavailable":
                if trace:
                    trace.stop_reason = "grader_unavailable"
                break
            missing = grade.missing_aspect or "more specific policy conditions"
            current_query = query
        else:
            if trace:
                trace.stop_reason = "max_hops_insufficient"
        return list(accumulated.values())[: self._settings.rerank_top_n * 2]

    async def _grade(self, query: str, chunks: list[RetrievedChunk]) -> GradeResult:
        evidence = "\n\n".join(f"[{c.chunk_id}] {c.content}" for c in chunks) or "(empty)"
        provider = getattr(self._llm, "provider", self._llm)
        if type(provider).__name__ in {"DemoLLMProvider", "FakeLLMProvider"} and not getattr(
            provider, "canned", {}
        ):
            return GradeResult(**lexical_check(query, chunks))
        try:
            response = await self._llm.generate(
                [
                    {"role": "system", "content": GRADE_SYSTEM},
                    {"role": "user", "content": GRADE_USER.format(query=query, evidence=evidence)},
                ],
                temperature=0.0,
                max_tokens=256,
                response_format={"type": "json_object"},
            )
            return parse_model(response.content, GradeResult)
        except (LLMProviderError, LLMOutputError):
            if (trace := current_trace.get()) is not None:
                trace.warnings.append("证据充分性裁判不可用。")
            return GradeResult(
                relevant=bool(chunks), sufficient=False, rationale="grader_unavailable"
            )
