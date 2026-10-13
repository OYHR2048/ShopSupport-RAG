"""Self-RAG faithfulness check and answer self-correction."""

from __future__ import annotations

from app.agents.nodes.generate import AnswerPayload, _extractive_fallback
from app.agents.state import AgentState
from app.core.exceptions import LLMOutputError, LLMProviderError
from app.guardrails.hallucination import FaithfulnessChecker, FaithfulnessResult
from app.observability.metrics import HALLUCINATION_FLAGS
from app.prompts.faithfulness import SELF_CORRECT_SYSTEM, SELF_CORRECT_USER
from app.providers.base import LLMProvider
from app.providers.json_parser import parse_model
from app.schemas.retrieval import RetrievedChunk


async def self_correct_answer(
    *,
    llm: LLMProvider,
    checker: FaithfulnessChecker,
    query: str,
    answer: str,
    chunks: list[RetrievedChunk],
    tool_results_text: str,
    first_result: FaithfulnessResult,
) -> tuple[str, FaithfulnessResult, bool]:
    """LLM rewrite once, then extractive fallback. Returns (answer, judge, corrected)."""
    evidence = (
        "\n\n".join(f"[{c.chunk_id}] {c.title}\n{c.content}" for c in chunks[:10]) or "(无检索结果)"
    )
    try:
        response = await llm.generate(
            [
                {"role": "system", "content": SELF_CORRECT_SYSTEM},
                {
                    "role": "user",
                    "content": SELF_CORRECT_USER.format(
                        query=query,
                        answer=answer,
                        critique=first_result.rationale
                        or ",".join(first_result.hallucinated_spans),
                        evidence=evidence,
                        tool_results=tool_results_text or "(无)",
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        payload = parse_model(response.content, AnswerPayload)
        rewritten = payload.answer
        # 保留修正响应声明的有效来源，使后续引用重建不会丢掉结构化 citation ID。
        valid_ids = {c.chunk_id for c in chunks}
        markers = [
            f"[{cid}]"
            for cid in payload.citation_chunk_ids
            if cid in valid_ids and f"[{cid}]" not in rewritten
        ]
        if markers:
            rewritten += "\n来源：" + " ".join(markers)
        judged = await checker.check(
            query=query,
            answer=rewritten,
            chunks=chunks,
            tool_results_text=tool_results_text,
        )
        if judged.supported:
            return rewritten, judged, True
    except (LLMProviderError, LLMOutputError):
        pass
    patched = _extractive_fallback(query, evidence, tool_results_text)
    judged = await checker.check(
        query=query,
        answer=patched,
        chunks=chunks,
        tool_results_text=tool_results_text,
    )
    return patched, judged, True


async def faithfulness_node(
    state: AgentState, checker: FaithfulnessChecker, llm: LLMProvider
) -> AgentState:
    answer = state.get("final_answer") or ""
    if state.get("refused") or state.get("pending_action"):
        return {}
    strategy = state.get("strategy")
    if strategy and strategy.value == "direct":
        return {}
    if (state.get("extra") or {}).get("answer_backend") == "demo-extractive":
        return {
            "extra": {
                **state["extra"],
                "faithfulness": {
                    "supported": True,
                    "rationale": "原文摘录/工具结果直出；未使用模型裁判",
                },
            }
        }
    chunks = state.get("retrieved_chunks") or []
    if chunks:
        cited = {c.chunk_id for c in state.get("citations") or []}
        chunks = [c for c in chunks if c.chunk_id in cited]
        if not chunks:
            return {
                "final_answer": "答案缺少有效证据引用，请补充信息或转人工。",
                "refused": True,
                "citations": [],
            }
    tools = state.get("tool_results") or []
    tool_text = str([t.model_dump() for t in tools])
    result = await checker.check(
        query=state["user_query"],
        answer=answer,
        chunks=chunks,
        tool_results_text=tool_text,
    )
    extra = {
        **(state.get("extra") or {}),
        "faithfulness": result.model_dump(),
        "self_corrected": False,
    }
    if result.supported:
        return {"extra": extra}
    HALLUCINATION_FLAGS.inc()
    patched, judged, corrected = await self_correct_answer(
        llm=llm,
        checker=checker,
        query=state["user_query"],
        answer=answer,
        chunks=chunks,
        tool_results_text=tool_text,
        first_result=result,
    )
    extra = {
        **(state.get("extra") or {}),
        "faithfulness": judged.model_dump(),
        "self_corrected": corrected,
    }
    # 修正后的引用不可沿用旧答案。提取修正答案实际标记的 chunk，并拒绝无依据的结果。
    from app.schemas.retrieval import Citation

    used = [c for c in chunks if f"[{c.chunk_id}]" in patched]
    if not judged.supported or (chunks and not used):
        return {
            "final_answer": "修正后仍未得到可靠且可引用的答案，请补充信息或转人工。",
            "refused": True,
            "citations": [],
            "extra": extra,
        }
    return {
        "final_answer": patched,
        "extra": extra,
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
        ],
    }
