"""本次新增：与真实后端同源的浏览器演示入口。"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_container
from app.services.runtime import runtime_status

router = APIRouter(tags=["demo"])


@router.get("/runtime")
async def runtime(container=Depends(get_container)):
    return runtime_status(container.chat_service)


@router.get("/sources/{chunk_id}")
async def source(chunk_id: str, container=Depends(get_container)):
    # 只按已 ingest 的 ID 查找，不允许客户端传文件路径。
    for chunk in container.retriever._bm25._chunks:
        if chunk.chunk_id == chunk_id:
            return chunk.model_dump()
    raise HTTPException(404, "source not found")
