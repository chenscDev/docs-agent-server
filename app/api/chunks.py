"""Chunk 全文 API（Citation 二次查询）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import raise_api_error
from app.db.models import Chunk, Document
from app.db.session import get_db

router = APIRouter(prefix="/v1", tags=["chunks"])


@router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str, db: Session = Depends(get_db)) -> dict:
    """获取 chunk 全文。"""
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        raise_api_error(404, "CHUNK_GONE", "原文已删除或不可用")

    doc = db.get(Document, chunk.document_id)
    title = doc.title if doc else ""
    metadata = None
    if chunk.metadata_json:
        try:
            metadata = json.loads(chunk.metadata_json)
        except json.JSONDecodeError:
            metadata = None

    return {
        "id": chunk.id,
        "documentId": chunk.document_id,
        "documentTitle": title,
        "index": chunk.chunk_index,
        "content": chunk.content,
        "metadata": metadata,
    }
