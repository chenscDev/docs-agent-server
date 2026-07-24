"""调试接口：裸对话 + 向量检索。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.llm import LLMClient
from app.db.session import DEFAULT_KB_ID, get_db
from app.rag.faiss_store import search_kb

router = APIRouter(prefix="/debug", tags=["debug"])


class DebugChatRequest(BaseModel):
    """调试对话请求。"""

    message: str = Field(..., min_length=1, description="用户输入")


class DebugChatResponse(BaseModel):
    """调试对话响应。"""

    reply: str


class DebugSearchRequest(BaseModel):
    """调试检索请求。"""

    query: str = Field(..., min_length=1)
    knowledge_base_id: str = Field(default=DEFAULT_KB_ID, alias="knowledgeBaseId")
    top_k: int = Field(default=5, ge=1, le=8, alias="topK")

    model_config = {"populate_by_name": True}


@router.post("/chat", response_model=DebugChatResponse)
def debug_chat(body: DebugChatRequest) -> DebugChatResponse:
    """非流式调用千问，用于验证 Key 与网络。"""
    try:
        client = LLMClient()
        reply = client.chat(
            [
                {
                    "role": "system",
                    "content": "你是简洁的中文助手。用一两句话回答即可。",
                },
                {"role": "user", "content": body.message},
            ]
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return DebugChatResponse(reply=reply)


@router.post("/search")
def debug_search(body: DebugSearchRequest, db: Session = Depends(get_db)) -> dict:
    """
    调试向量检索（D4）。

    用于确认 FAISS + Embedding 是否工作，不是最终 Agent 接口。
    """
    try:
        hits = search_kb(
            db,
            body.knowledge_base_id,
            body.query,
            top_k=body.top_k,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"检索失败: {exc}") from exc

    return {
        "query": body.query,
        "hitCount": len(hits),
        "hits": [
            {
                "chunkId": h.chunk_id,
                "documentId": h.document_id,
                "documentTitle": h.document_title,
                "chunkIndex": h.chunk_index,
                "score": round(h.score, 4),
                "text": h.text[:400],
            }
            for h in hits
        ],
    }


@router.get("/parse-queue")
def debug_parse_queue() -> dict:
    """查看解析队列快照（P2-D5～D6 排障）。"""
    from app.rag.parse_queue import queue_snapshot

    return queue_snapshot()
