"""文档上传 / 列表 / 详情 / 删除 / 重试解析。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import raise_api_error
from app.core.ids import new_id
from app.db.models import Chunk, Document, KnowledgeBase
from app.db.session import get_db
from app.rag.document_events import iter_document_parse_sse
from app.rag.extract import ALLOWED_EXTENSIONS, guess_mime, normalize_extension
from app.rag.faiss_store import delete_document_index, rebuild_kb_index
from app.rag.parse_queue import enqueue_parse
from app.rag.pipeline import (
    delete_chunks_for_document,
    extracted_text_path,
    resolve_storage_path,
)
from app.schemas.chunks import ChunkListOut, ChunkOut
from app.schemas.documents import DocumentListOut, DocumentOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["documents"])


def _to_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        knowledge_base_id=doc.knowledge_base_id,
        title=doc.title,
        mime_type=doc.mime_type,
        byte_size=doc.byte_size,
        status=doc.status,
        progress=doc.progress,
        stage_message=doc.stage_message,
        chunk_count=doc.chunk_count,
        error_code=doc.error_code,
        error_message=doc.error_message,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _ensure_kb(db: Session, kb_id: str) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise_api_error(404, "KB_NOT_FOUND", f"知识库不存在: {kb_id}")
    return kb


def _upload_root() -> Path:
    settings = get_settings()
    root = Path(settings.upload_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    response_model=DocumentOut,
    response_model_by_alias=True,
)
async def upload_document(
    kb_id: str,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
) -> DocumentOut:
    """上传文档：校验 → 落盘 → pending → 解析队列。"""
    _ensure_kb(db, kb_id)

    raw_name = file.filename or "unnamed"
    ext = normalize_extension(raw_name)
    if ext not in ALLOWED_EXTENSIONS:
        raise_api_error(
            400,
            "UNSUPPORTED_TYPE",
            f"仅支持 {sorted(ALLOWED_EXTENSIONS)}",
        )

    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read()
    if len(data) == 0:
        raise_api_error(400, "PARSE_EMPTY", "空文件")
    if len(data) > max_bytes:
        raise_api_error(
            400,
            "TOO_LARGE",
            f"文件超过 {settings.max_upload_mb}MB 限制",
        )

    display_title = (title or raw_name).strip() or raw_name
    return _enqueue_document(
        db,
        kb_id=kb_id,
        title=display_title,
        ext=ext,
        data=data,
        mime_type=file.content_type or guess_mime(raw_name),
    )


class TextDocumentRequest(BaseModel):
    """RN 端粘贴文本上传（免文件选择器）。"""

    title: str = Field(default="粘贴文档.md", min_length=1)
    content: str = Field(..., min_length=1)


@router.post(
    "/knowledge-bases/{kb_id}/documents/text",
    response_model=DocumentOut,
    response_model_by_alias=True,
)
def upload_text_document(
    kb_id: str,
    body: TextDocumentRequest,
    db: Session = Depends(get_db),
) -> DocumentOut:
    """以 Markdown/纯文本内容创建文档并进入解析流水线。"""
    _ensure_kb(db, kb_id)
    settings = get_settings()
    data = body.content.encode("utf-8")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise_api_error(
            400,
            "TOO_LARGE",
            f"内容超过 {settings.max_upload_mb}MB 限制",
        )
    title = body.title.strip() or "粘贴文档.md"
    if not title.lower().endswith((".md", ".txt", ".markdown")):
        title = f"{title}.md"
    ext = normalize_extension(title)
    return _enqueue_document(
        db,
        kb_id=kb_id,
        title=title,
        ext=ext if ext in ALLOWED_EXTENSIONS else ".md",
        data=data,
        mime_type=guess_mime(title),
    )


def _enqueue_document(
    db: Session,
    *,
    kb_id: str,
    title: str,
    ext: str,
    data: bytes,
    mime_type: str,
) -> DocumentOut:
    """落盘 + 入库 + 投递解析队列。"""
    doc_id = new_id("doc")
    dest = _upload_root() / f"{doc_id}{ext}"
    dest.write_bytes(data)

    try:
        storage_key = str(dest.relative_to(Path.cwd()))
    except ValueError:
        storage_key = str(dest)

    doc = Document(
        id=doc_id,
        knowledge_base_id=kb_id,
        title=title,
        mime_type=mime_type,
        byte_size=len(data),
        storage_key=storage_key,
        status="pending",
        progress=0.0,
        stage_message="排队等待解析",
        chunk_count=0,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # status 已落库；入队失败也不丢任务（重启 recover 会扫到）
    enqueue_parse(doc_id)
    return _to_out(doc)


@router.get(
    "/knowledge-bases/{kb_id}/documents",
    response_model=DocumentListOut,
    response_model_by_alias=True,
)
def list_documents(kb_id: str, db: Session = Depends(get_db)) -> DocumentListOut:
    """列出知识库下文档。"""
    _ensure_kb(db, kb_id)
    rows = db.scalars(
        select(Document)
        .where(Document.knowledge_base_id == kb_id)
        .order_by(Document.created_at.desc())
    ).all()
    return DocumentListOut(items=[_to_out(d) for d in rows])


@router.get(
    "/documents/{doc_id}",
    response_model=DocumentOut,
    response_model_by_alias=True,
)
def get_document(doc_id: str, db: Session = Depends(get_db)) -> DocumentOut:
    """文档详情（供轮询状态 / SSE 降级）。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise_api_error(404, "DOC_NOT_FOUND", "文档不存在")
    return _to_out(doc)


@router.get("/documents/{doc_id}/events")
def document_parse_events(doc_id: str) -> StreamingResponse:
    """
    文档解析进度 SSE（P3-D6）。

    事件：document.snapshot / document.progress / document.completed / error
    鉴权走全局 Bearer；断线后客户端应回退 GET /documents/{id} 轮询。
    """
    return StreamingResponse(
        iter_document_parse_sse(doc_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/documents/{doc_id}/chunks",
    response_model=ChunkListOut,
    response_model_by_alias=True,
)
def list_document_chunks(
    doc_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> ChunkListOut:
    """文档片段列表（仅 snippet，便于 DocDetail 调试）。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise_api_error(404, "DOC_NOT_FOUND", "文档不存在")

    limit = max(1, min(limit, 200))
    rows = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == doc_id)
        .order_by(Chunk.chunk_index.asc())
        .limit(limit)
    ).all()

    items: list[ChunkOut] = []
    for row in rows:
        snippet = row.content[:120] + ("…" if len(row.content) > 120 else "")
        items.append(
            ChunkOut(
                id=row.id,
                index=row.chunk_index,
                snippet=snippet,
                token_estimate=row.token_estimate,
            )
        )
    return ChunkListOut(items=items, next_cursor=None)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """删除文档：chunks + DB + 原文件 + 提取文本，并重建 FAISS。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise_api_error(404, "DOC_NOT_FOUND", "文档不存在")

    kb_id = doc.knowledge_base_id
    storage = resolve_storage_path(doc.storage_key)
    extracted = extracted_text_path(doc_id)

    delete_chunks_for_document(db, doc_id)
    db.delete(doc)
    db.commit()

    for path in (storage, extracted):
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("删除文件失败 %s: %s", path, exc)

    try:
        delete_document_index(db, kb_id, doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("删除后更新索引失败 kb=%s: %s", kb_id, exc)
        try:
            rebuild_kb_index(db, kb_id)
        except Exception as exc2:  # noqa: BLE001
            logger.exception("删除后全量重建也失败 kb=%s: %s", kb_id, exc2)

    return {"status": "deleted", "id": doc_id}


@router.post(
    "/documents/{doc_id}/reparse",
    response_model=DocumentOut,
    response_model_by_alias=True,
)
def reparse_document(
    doc_id: str,
    db: Session = Depends(get_db),
) -> DocumentOut:
    """重新解析（清 extracted / chunks，再走流水线）。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise_api_error(404, "DOC_NOT_FOUND", "文档不存在")

    extracted = extracted_text_path(doc_id)
    if extracted.is_file():
        extracted.unlink(missing_ok=True)

    delete_chunks_for_document(db, doc_id)
    doc.chunk_count = 0
    doc.status = "pending"
    doc.progress = 0.0
    doc.stage_message = "排队等待重新解析"
    doc.error_code = None
    doc.error_message = None
    doc.parsed_at = None
    db.commit()
    db.refresh(doc)

    enqueue_parse(doc_id)
    return _to_out(doc)
