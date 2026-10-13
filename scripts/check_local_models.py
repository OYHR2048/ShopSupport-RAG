"""验证真实本地检索接入聊天链路；明确禁止付费生成和外部服务。"""

import asyncio
import json
from pathlib import Path

from pydantic import SecretStr

from app.config.settings import Settings
from app.schemas.chat import ChatRequest
from app.services.container import build_container


async def main():
    settings = Settings(
        app_env="development",
        runtime_mode="model",
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="",
        qdrant_url="",
        local_vector_path="",
        mcp_enabled=False,
        deepseek_api_key=SecretStr(""),
        embedding_api_key=SecretStr(""),
        embedding_base_url="",
        local_embedding_model="data/models/bge-small-zh-v1.5",
        cross_encoder_model="data/models/bge-reranker-base",
        model_download_allowed=False,
    )
    container = await build_container(settings, seed=True)
    responses = []
    try:
        for query in [
            "七天无理由退货需要满足什么条件？",
            "定制马克杯过了7天但是有质量问题，还能退吗，运费谁承担？",
            "支持火星配送吗？",
        ]:
            response = await container.chat_service.chat(
                ChatRequest(message=query, user_id="u_demo"), trace_id="local_model_smoke"
            )
            assert response.runtime["embedding"] != "hash"
            assert response.runtime["reranker"] == "cross-encoder"
            assert response.runtime["mode"] == "degraded"
            assert response.diagnostics["network_attempts"] == 0
            responses.append(response.model_dump(mode="json"))
        assert not responses[0]["refused"] and responses[0]["citations"]
        assert "签收次日起 7 个自然日内" in responses[0]["answer"]
        assert responses[1]["strategy"] == "multi" and not responses[1]["refused"]
        assert responses[2]["refused"]
    finally:
        await container.aclose()
    Path("reports/local_model_smoke.json").write_text(
        json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "Local BGE + Cross-Encoder chat smoke passed; generation/grading are explicit demo fallbacks."
    )


if __name__ == "__main__":
    asyncio.run(main())
