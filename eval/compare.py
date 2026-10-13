"""独立评估：文档级检索 + 固定/自适应证据路径，逐题保存，不推断生成正确率。"""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.config.settings import Settings
from app.observability.trace import Trace, TrackedLLM, current_trace
from app.providers.deepseek import DeepSeekProvider
from app.providers.demo import DemoLLMProvider
from app.retrieval.cleaning import normalize_text
from app.retrieval.query_rewrite import QueryRewriter
from app.retrieval.self_rag import SelfRAGLoop
from app.services.container import build_retrieval
from eval.metrics import mrr, precision_at_k, recall_at_k


def document_names(chunks):
    return list(dict.fromkeys(c.document_id.rsplit("_", 1)[0] for c in chunks))


async def run(args):
    settings = Settings(
        runtime_mode=args.mode,
        app_env="development",
        seed_demo=False,
        model_download_allowed=False,
        local_vector_path="",
        retrieval_cache_ttl_seconds=0,
        local_embedding_model="data/models/bge-small-zh-v1.5"
        if args.local_models
        else "BAAI/bge-small-zh-v1.5",
        cross_encoder_model="data/models/bge-reranker-base"
        if args.local_models
        else "BAAI/bge-reranker-base",
    )
    # 本地模型评估无需付费生成；只有双重显式开关才使用付费模型做改写和裁判。
    if not args.allow_paid:
        from pydantic import SecretStr

        settings.embedding_api_key = SecretStr("")
        settings.embedding_base_url = ""
    if args.allow_paid and (args.mode != "model" or not settings.llm_configured):
        raise SystemExit("--allow-paid 要求 mode=model 且提供生成模型密钥")
    provider = DeepSeekProvider(settings) if args.allow_paid else DemoLLMProvider()
    llm = TrackedLLM(provider)
    hybrid, reranker, store = await build_retrieval(settings, None)
    hybrid._cache = None
    rows = [
        json.loads(line)
        for line in Path("eval/dataset/reviewed.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for row in rows:
        for span in row["evidence_spans"]:
            raw = Path("knowledge", span["document"] + ".md").read_text(encoding="utf-8")
            if span["text"] not in raw:
                raise ValueError("标注原文不存在：" + row["id"] + ":" + span["text"])
    rows = [row for row in rows if row["split"] == args.split][: args.limit]
    backend = {
        "embedding": hybrid._vector.embedder.backend,
        "embedding_model": settings.local_embedding_model,
        "reranker": reranker._provider.backend,
        "reranker_model": settings.cross_encoder_model,
        "llm": type(provider).__name__,
        "generation_evaluated": False,
        "grader": "llm" if args.allow_paid else "demo_lexical",
    }
    if args.require_real and (
        backend["embedding"] == "hash" or backend["reranker"] != "cross-encoder"
    ):
        raise SystemExit("真实模型未加载，禁止将本次结果标为真实模型评估：" + json.dumps(backend))
    results = []
    strategies = ["vector", "hybrid", "hybrid_rerank", "fixed_single", "adaptive"]
    loop = SelfRAGLoop(hybrid, reranker, QueryRewriter(llm), llm, settings)
    for row in rows:
        for method in strategies:
            trace = Trace()
            token = current_trace.set(trace)
            started = perf_counter()
            try:
                if method == "vector":
                    chunks = await hybrid._vector.search(row["query"], top_k=5)
                elif method == "hybrid":
                    chunks = await hybrid.retrieve(row["query"], top_k=5)
                elif method in {"hybrid_rerank", "fixed_single"}:
                    candidates = await hybrid.retrieve(row["query"], top_k=8)
                    chunks = await reranker.rerank(row["query"], candidates, top_n=5)
                    if method == "fixed_single":
                        grade = await loop._grade(row["query"], chunks)
                        trace.retrieval.append(
                            {"hop": 1, "queries": [row["query"]], "grade": grade.model_dump()}
                        )
                        trace.stop_reason = (
                            "evidence_sufficient" if grade.sufficient else "single_insufficient"
                        )
                else:
                    chunks = await loop.retrieve(row["query"], top_k=8)
                docs = document_names(chunks)
                relevant = set(row["relevant_doc_ids"])
                coverage = [
                    any(
                        normalize_text(s["text"], join_hard_breaks=True) in c.content
                        and c.document_id.rsplit("_", 1)[0] == s["document"]
                        for c in chunks
                    )
                    for s in row["evidence_spans"]
                ]
                verdict = trace.retrieval[-1]["grade"]["sufficient"] if trace.retrieval else None
                results.append(
                    {
                        "id": row["id"],
                        "query": row["query"],
                        "method": method,
                        "gold": row,
                        "documents": docs,
                        "chunk_ids": [c.chunk_id for c in chunks],
                        "evidence": [c.model_dump() for c in chunks],
                        "precision_at_1": precision_at_k(relevant, docs, 1) if relevant else None,
                        "recall_at_3": recall_at_k(relevant, docs, 3) if relevant else None,
                        "mrr": mrr(relevant, docs) if relevant else None,
                        "all_annotated_spans_retrieved": all(coverage) if coverage else None,
                        "sufficient": verdict,
                        "decision_correct": verdict == row["answerable"]
                        if verdict is not None
                        else None,
                        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                        "llm_calls": len(trace.calls),
                        "network_attempts": trace.network_attempts,
                        "backend_calls": trace.backend_calls,
                        "token_usage": trace.usage.model_dump(),
                        "hops": trace.retrieval,
                        "stop_reason": trace.stop_reason,
                        "warnings": trace.warnings,
                    }
                )
            finally:
                current_trace.reset(token)
    summary = {}
    for method in strategies:
        group = [r for r in results if r["method"] == method]

        def mean(field, group=group):
            xs = [float(r[field]) for r in group if r[field] is not None]
            return round(sum(xs) / len(xs), 4) if xs else None

        summary[method] = {
            field: mean(field)
            for field in [
                "precision_at_1",
                "recall_at_3",
                "mrr",
                "all_annotated_spans_retrieved",
                "decision_correct",
                "elapsed_ms",
                "llm_calls",
            ]
        }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "split": args.split,
        "backend": backend,
        "parameters": {
            "candidate_k": 8,
            "vector_k": 5,
            "rerank_n": 5,
            "rrf_k": settings.rrf_k,
            "max_hops": settings.max_retrieval_hops,
            "cache": False,
            "limit": args.limit,
        },
        "corpus_sha256": hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted(Path("knowledge").glob("*.md")))
        ).hexdigest(),
        "dataset_sha256": hashlib.sha256(
            Path("eval/dataset/reviewed.jsonl").read_bytes()
        ).hexdigest(),
        "summary": summary,
        "results": results,
        "limitations": [
            "标注由源码检查建立，可按原文人工复核；未声称独立人工审核已完成。",
            "文档级相关集合可能包含替代依据，span coverage 更适合复合问题；本报告不测生成答案。",
            "未开启 allow-paid 时自适应裁判为演示词面检查，不能证明真实语义自适应效果。",
        ],
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    await llm.aclose()
    closer = getattr(hybrid._vector.embedder, "aclose", None)
    if closer:
        await closer()
    print(
        json.dumps(
            {"path": str(path), "backend": backend, "summary": summary},
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "model"], default="demo")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--output", default="reports/eval_dev_demo.json")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--require-real", action="store_true")
    parser.add_argument(
        "--local-models", action="store_true", help="使用 download_models.py 下载的项目内模型"
    )
    parser.add_argument("--allow-paid", action="store_true", help="显式授权本次改写/裁判 API 费用")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
