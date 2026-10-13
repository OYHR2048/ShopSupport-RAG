"""不依赖模型权重的失败闭环与轨迹隔离测试。"""

import asyncio
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.core.exceptions import LLMProviderError
from app.guardrails.hallucination import FaithfulnessChecker
from app.observability.trace import Trace, TrackedLLM, current_trace
from app.providers.fake import FakeLLMProvider
from app.retrieval.chunking import document_id_from_path
from eval.metrics import recall_at_k


def test_document_id_is_portable_and_subdirectories_distinct():
    one = document_id_from_path(Path("machine_a/knowledge/return.md"), Path("machine_a/knowledge"))
    two = document_id_from_path(Path("machine_b/knowledge/return.md"), Path("machine_b/knowledge"))
    assert one == two
    assert one != document_id_from_path(
        Path("machine_a/knowledge/other/return.md"), Path("machine_a/knowledge")
    )


def test_duplicate_document_cannot_inflate_recall():
    assert recall_at_k({"a", "b"}, ["a", "a", "a"], 3) == 0.5


@pytest.mark.asyncio
async def test_checker_failure_fails_closed():
    class Broken:
        async def generate(self, *args, **kwargs):
            raise LLMProviderError("unavailable")

    result = await FaithfulnessChecker(Broken()).check(
        query="q", answer="不可信", chunks=[], tool_results_text=""
    )
    assert not result.supported and result.rationale == "checker_unavailable"


@pytest.mark.asyncio
async def test_request_call_counts_do_not_leak_between_concurrent_tasks():
    llm = TrackedLLM(FakeLLMProvider())

    async def one(n):
        trace = Trace()
        token = current_trace.set(trace)
        try:
            for _ in range(n):
                await llm.generate([{"role": "user", "content": "q"}])
            return len(trace.calls), trace.usage.total_tokens
        finally:
            current_trace.reset(token)

    assert await asyncio.gather(one(1), one(3)) == [(1, 8), (3, 24)]


@pytest.mark.asyncio
async def test_self_rag_executes_all_rewrites_and_keeps_original_goal():
    from app.providers.base import LLMResponse
    from app.retrieval.self_rag import SelfRAGLoop
    from app.schemas.retrieval import RetrievedChunk

    class Retriever:
        def __init__(self):
            self.calls = []

        async def retrieve(self, q, **kwargs):
            self.calls.append(q)
            return [RetrievedChunk(chunk_id=q, document_id=q, title=q, content=q, score=1)]

    class Reranker:
        async def rerank(self, q, chunks, **kwargs):
            return chunks

    class Rewriter:
        async def rewrite(self, q, missing_aspect=""):
            return ["子问题A", "子问题B"] if not missing_aspect else ["缺失条件"]

    class Grader:
        def __init__(self):
            self.messages = []

        async def generate(self, messages, **kwargs):
            self.messages.append(messages)
            import json

            return LLMResponse(
                json.dumps(
                    {
                        "relevant": True,
                        "sufficient": len(self.messages) == 2,
                        "missing_aspect": "缺失条件",
                        "rationale": "stub",
                    }
                )
            )

    retriever, grader = Retriever(), Grader()
    loop = SelfRAGLoop(retriever, Reranker(), Rewriter(), grader, Settings(max_retrieval_hops=3))
    trace = Trace()
    token = current_trace.set(trace)
    try:
        chunks = await loop.retrieve("原问题", top_k=5)
        assert retriever.calls == ["子问题A", "子问题B", "原问题", "缺失条件", "原问题"]
        assert {c.chunk_id for c in chunks} == {"子问题A", "子问题B", "原问题", "缺失条件"}
        assert all("原问题" in messages[1]["content"] for messages in grader.messages)
        assert trace.stop_reason == "evidence_sufficient" and len(trace.retrieval) == 2
    finally:
        current_trace.reset(token)


@pytest.mark.asyncio
async def test_answer_still_unsupported_after_correction_is_refused():
    import json

    from app.agents.nodes.faithfulness import faithfulness_node
    from app.core.enums import RetrievalStrategy
    from app.providers.base import LLMResponse
    from app.schemas.retrieval import Citation, RetrievedChunk

    class Model:
        def __init__(self):
            self.calls = 0

        async def generate(self, messages, **kwargs):
            self.calls += 1
            data = (
                {"answer": "仍然不可信", "citation_chunk_ids": []}
                if self.calls == 2
                else {"supported": False, "rationale": "unsupported"}
            )
            return LLMResponse(json.dumps(data))

    llm = Model()
    chunk = RetrievedChunk(
        chunk_id="c1", document_id="d1", title="政策", content="只支持7天退货", score=1
    )
    state = {
        "user_query": "可以永久退货吗",
        "final_answer": "永久可退",
        "strategy": RetrievalStrategy.SINGLE,
        "retrieved_chunks": [chunk],
        "citations": [Citation(chunk_id="c1", document_id="d1", title="政策")],
    }
    result = await faithfulness_node(state, FaithfulnessChecker(llm), llm)
    assert result["refused"] and result["citations"] == []
    assert not result["extra"]["faithfulness"]["supported"]
    assert llm.calls == 4
