"""Document reranking over fused retrieval results."""

from __future__ import annotations

from app.providers.base import RerankProvider
from app.schemas.retrieval import RetrievedChunk


class DocumentReranker:
    def __init__(self, provider: RerankProvider) -> None:
        self._provider = provider

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        *,
        top_n: int,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        documents = [f"{chunk.title} {chunk.section or ''}\n{chunk.content}" for chunk in chunks]
        from time import perf_counter

        from app.observability.trace import current_trace

        started = perf_counter()
        try:
            ranked = await self._provider.rerank(query, documents, top_n=min(top_n, len(chunks)))
        finally:
            if (trace := current_trace.get()) is not None:
                trace.backend_calls.append(
                    {
                        "operation": "rerank",
                        "backend": getattr(self._provider, "backend", "unknown"),
                        "documents": len(documents),
                        "ms": (perf_counter() - started) * 1000,
                    }
                )
        results: list[RetrievedChunk] = []
        for new_rank, (original_index, score) in enumerate(ranked, start=1):
            chunk = chunks[original_index].model_copy(deep=True)
            chunk.score = score
            chunk.rank = new_rank
            chunk.source = "rerank"
            results.append(chunk)
        return results
