"""暴露实际加载的 backend，不以配置声明代替运行状态。"""

from app.observability.trace import current_trace


def runtime_status(service):
    settings = service._settings
    embedder = service._retriever._vector.embedder
    ce = service._reranker._provider
    llm = getattr(service._llm, "provider", service._llm)
    embedding = getattr(embedder, "backend", type(embedder).__name__)
    rerank = getattr(ce, "backend", type(ce).__name__)
    trace = current_trace.get()
    model_llm = type(llm).__name__ == "DeepSeekProvider"
    warnings = list(trace.warnings) if trace else []
    if embedding == "hash":
        warnings.append("Hash embedding 是测试占位，未验证中文语义向量效果。")
    if rerank == "lexical":
        warnings.append("词面重排后备；真实 Cross-Encoder 未加载。")
    if settings.runtime_mode == "model" and not model_llm:
        warnings.append("未配置生成模型密钥，使用离线演示后备。")
    mode = (
        "demo"
        if settings.runtime_mode == "demo"
        else (
            "model"
            if model_llm and embedding != "hash" and rerank == "cross-encoder" and not warnings
            else "degraded"
        )
    )
    if trace and trace.warnings:
        mode = "degraded"
    return {
        "mode": mode,
        "llm": type(llm).__name__,
        "model": settings.deepseek_model if model_llm else "demo-rules-extractive",
        "embedding": embedding,
        "embedding_model": settings.local_embedding_model,
        "reranker": rerank,
        "reranker_model": settings.cross_encoder_model,
        "vector_store": type(service._retriever._vector.store).__name__,
        "warnings": list(dict.fromkeys(warnings)),
        "business_data": "演示商店政策与模拟订单；无真实支付/退款/物流",
    }
