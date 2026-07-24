"""会话与消息 API。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import raise_api_error
from app.core.ids import new_id
from app.db.models import KnowledgeBase, Message, Session as ChatSession
from app.db.session import DEFAULT_KB_ID, get_db

router = APIRouter(prefix="/v1", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    title: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class SessionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    knowledge_base_id: str = Field(serialization_alias="knowledgeBaseId")
    title: str | None = None
    last_preview: str | None = Field(default=None, serialization_alias="lastPreview")
    created_at: object | None = Field(default=None, serialization_alias="createdAt")
    updated_at: object | None = Field(default=None, serialization_alias="updatedAt")


def _session_out(s: ChatSession) -> SessionOut:
    return SessionOut(
        id=s.id,
        knowledge_base_id=s.knowledge_base_id,
        title=s.title,
        last_preview=s.last_preview,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.post("/sessions", response_model=SessionOut, response_model_by_alias=True)
def create_session(
    body: CreateSessionRequest,
    db: Session = Depends(get_db),
) -> SessionOut:
    """创建会话。"""
    kb_id = body.knowledge_base_id or DEFAULT_KB_ID
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise_api_error(404, "KB_NOT_FOUND", "知识库不存在")

    session = ChatSession(
        id=new_id("ses"),
        knowledge_base_id=kb_id,
        title=body.title,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(session)


@router.get("/sessions")
def list_sessions(
    limit: int = 20,
    knowledge_base_id: str | None = Query(default=None, alias="knowledgeBaseId"),
    db: Session = Depends(get_db),
) -> dict:
    """会话列表；可按 knowledgeBaseId 过滤。"""
    limit = max(1, min(limit, 100))
    stmt = select(ChatSession).order_by(ChatSession.updated_at.desc())
    if knowledge_base_id:
        stmt = stmt.where(ChatSession.knowledge_base_id == knowledge_base_id)
    rows = db.scalars(stmt.limit(limit)).all()
    return {
        "items": [
            _session_out(s).model_dump(by_alias=True, mode="json") for s in rows
        ]
    }


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """删除会话及其消息。"""
    session = db.get(ChatSession, session_id)
    if session is None:
        raise_api_error(404, "SESSION_NOT_FOUND", "会话不存在")

    db.execute(delete(Message).where(Message.session_id == session_id))
    db.delete(session)
    db.commit()
    return {"status": "deleted", "id": session_id}


@router.get("/sessions/{session_id}/messages")
def list_messages(
    session_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    """拉取会话消息历史。"""
    session = db.get(ChatSession, session_id)
    if session is None:
        raise_api_error(404, "SESSION_NOT_FOUND", "会话不存在")

    limit = max(1, min(limit, 200))
    rows = db.scalars(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    ).all()

    items = []
    for m in rows:
        item = {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "status": m.status,
            "requestId": m.request_id,
            "createdAt": m.created_at,
            "citations": json.loads(m.citations_json) if m.citations_json else None,
            "toolTrace": json.loads(m.tool_trace_json) if m.tool_trace_json else None,
            "usage": json.loads(m.usage_json) if m.usage_json else None,
        }
        items.append(item)
    return {"items": items}
