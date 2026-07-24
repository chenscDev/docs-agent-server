"""知识库 CRUD（P2-D2）。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import raise_api_error
from app.core.ids import new_id
from app.db.models import Document, KnowledgeBase, Message, Session as ChatSession
from app.db.session import DEFAULT_KB_ID, get_db
from app.rag.faiss_store import drop_kb_index
from app.rag.pipeline import delete_chunks_for_document, extracted_text_path, resolve_storage_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge-bases"])


class CreateKbRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

    model_config = ConfigDict(populate_by_name=True)


class UpdateKbRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

    model_config = ConfigDict(populate_by_name=True)


def _kb_item(db: Session, kb: KnowledgeBase) -> dict:
    total = int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.knowledge_base_id == kb.id)
        )
        or 0
    )
    ready = int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.knowledge_base_id == kb.id,
                Document.status == "ready",
            )
        )
        or 0
    )
    return {
        "id": kb.id,
        "name": kb.name,
        "documentCount": total,
        "readyCount": ready,
        "isDefault": kb.id == DEFAULT_KB_ID,
        "createdAt": kb.created_at,
        "updatedAt": kb.updated_at,
    }


@router.get("")
def list_knowledge_bases(db: Session = Depends(get_db)) -> dict:
    """知识库列表。"""
    rows = db.scalars(
        select(KnowledgeBase).order_by(KnowledgeBase.created_at.asc())
    ).all()
    return {"items": [_kb_item(db, kb) for kb in rows]}


@router.post("")
def create_knowledge_base(
    body: CreateKbRequest,
    db: Session = Depends(get_db),
) -> dict:
    """新建知识库。"""
    name = body.name.strip()
    if not name:
        raise_api_error(400, "KB_NAME_REQUIRED", "知识库名称不能为空")

    kb = KnowledgeBase(id=new_id("kb"), name=name)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _kb_item(db, kb)


@router.get("/{kb_id}")
def get_knowledge_base(kb_id: str, db: Session = Depends(get_db)) -> dict:
    """知识库详情。"""
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise_api_error(404, "KB_NOT_FOUND", f"知识库不存在: {kb_id}")
    return _kb_item(db, kb)


@router.patch("/{kb_id}")
def update_knowledge_base(
    kb_id: str,
    body: UpdateKbRequest,
    db: Session = Depends(get_db),
) -> dict:
    """重命名知识库。"""
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise_api_error(404, "KB_NOT_FOUND", f"知识库不存在: {kb_id}")

    name = body.name.strip()
    if not name:
        raise_api_error(400, "KB_NAME_REQUIRED", "知识库名称不能为空")

    kb.name = name
    kb.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(kb)
    return _kb_item(db, kb)


@router.delete("/{kb_id}")
def delete_knowledge_base(kb_id: str, db: Session = Depends(get_db)) -> dict:
    """
    删除知识库：级联文档/chunks/会话消息，并移除 FAISS 文件。

    默认库 kb_default 禁止删除（启动会重新 seed）。
    """
    if kb_id == DEFAULT_KB_ID:
        raise_api_error(
            400,
            "KB_DEFAULT_PROTECTED",
            "默认知识库不可删除",
        )

    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise_api_error(404, "KB_NOT_FOUND", f"知识库不存在: {kb_id}")

    docs = db.scalars(
        select(Document).where(Document.knowledge_base_id == kb_id)
    ).all()
    file_paths = []
    for doc in docs:
        file_paths.append(resolve_storage_path(doc.storage_key))
        file_paths.append(extracted_text_path(doc.id))
        delete_chunks_for_document(db, doc.id)
        db.delete(doc)

    # 先删消息再删会话
    sessions = db.scalars(
        select(ChatSession).where(ChatSession.knowledge_base_id == kb_id)
    ).all()
    for ses in sessions:
        db.execute(delete(Message).where(Message.session_id == ses.id))
        db.delete(ses)

    db.delete(kb)
    db.commit()

    for path in file_paths:
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("删除知识库文件失败 %s: %s", path, exc)

    drop_kb_index(kb_id)
    logger.info("knowledge_base deleted id=%s docs=%s", kb_id, len(docs))
    return {"status": "deleted", "id": kb_id}
