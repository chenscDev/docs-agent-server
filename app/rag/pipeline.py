"""文档解析流水线：pending → parsing → indexing → ready | failed。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_id
from app.db import session as db_session
from app.db.models import Chunk, Document
from app.rag.chunker import split_text
from app.rag.extract import ExtractError, extract_text
from app.rag.faiss_store import rebuild_kb_index, upsert_document_index

logger = logging.getLogger(__name__)


def extracted_text_path(doc_id: str) -> Path:
    """抽取出的纯文本落盘路径。"""
    settings = get_settings()
    root = Path(settings.extracted_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{doc_id}.txt"


def resolve_storage_path(storage_key: str) -> Path:
    """将 storage_key 转为绝对路径。"""
    path = Path(storage_key)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def delete_chunks_for_document(db: Session, doc_id: str) -> None:
    """删除某文档下全部 chunks（reparse / 删文档前调用）。"""
    db.execute(delete(Chunk).where(Chunk.document_id == doc_id))


def run_parse_job(doc_id: str) -> None:
    """
    后台解析任务。

    成功：chunks + FAISS 重建后 status=ready。
    失败：status=failed，并尽量重建索引去掉脏数据。
    """
    db_session.get_engine()
    factory = db_session.SessionLocal
    if factory is None:
        raise RuntimeError("数据库 Session 未初始化")

    settings = get_settings()

    with factory() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            logger.warning("parse job skipped, document missing: %s", doc_id)
            return

        kb_id = doc.knowledge_base_id

        delete_chunks_for_document(db, doc_id)
        doc.chunk_count = 0
        doc.status = "parsing"
        doc.progress = 0.1
        doc.stage_message = "正在提取文本…"
        doc.error_code = None
        doc.error_message = None
        db.commit()

        try:
            file_path = resolve_storage_path(doc.storage_key)

            def on_stage(message: str, progress: float | None) -> None:
                """OCR / 提取阶段刷新文档进度，供端上轮询展示。"""
                doc.stage_message = message
                if progress is not None:
                    doc.progress = progress
                db.commit()

            text = extract_text(file_path, doc.title, on_stage=on_stage)
            out = extracted_text_path(doc_id)
            out.write_text(text, encoding="utf-8")

            doc.progress = 0.5
            doc.stage_message = "正在切分文本…"
            doc.parsed_at = datetime.now(timezone.utc)
            doc.status = "parsing"
            db.commit()

            pieces = split_text(
                text,
                chunk_size=settings.chunk_size,
                overlap=settings.chunk_overlap,
            )
            if not pieces:
                raise ExtractError("PARSE_EMPTY", "切分后无有效片段")

            for piece in pieces:
                meta = None
                if piece.heading:
                    meta = json.dumps({"heading": piece.heading}, ensure_ascii=False)
                db.add(
                    Chunk(
                        id=new_id("chk"),
                        document_id=doc.id,
                        knowledge_base_id=doc.knowledge_base_id,
                        chunk_index=piece.index,
                        content=piece.content,
                        token_estimate=piece.token_estimate,
                        metadata_json=meta,
                        content_hash=piece.content_hash,
                    )
                )

            doc.chunk_count = len(pieces)
            doc.status = "indexing"
            doc.progress = 0.7
            doc.stage_message = "正在建立向量索引…"
            db.commit()

            vector_count = upsert_document_index(db, kb_id, doc_id)
            if vector_count <= 0:
                raise RuntimeError("向量索引写入为空")

            doc.status = "ready"
            doc.progress = 1.0
            doc.stage_message = "已就绪，可问答"
            db.commit()
            logger.info(
                "ready doc=%s chunks=%s vectors=%s",
                doc_id,
                len(pieces),
                vector_count,
            )
        except ExtractError as exc:
            _mark_failed(db, doc, exc.code, exc.message)
            _safe_rebuild(db, kb_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("parse unexpected error doc=%s", doc_id)
            _mark_failed(db, doc, "EMBEDDING_FAILED", f"索引失败: {exc}")
            _safe_rebuild(db, kb_id)


def _safe_rebuild(db: Session, kb_id: str) -> None:
    """失败后尽量用剩余 ready 文档重建索引。"""
    try:
        rebuild_kb_index(db, kb_id)
    except Exception:  # noqa: BLE001
        logger.exception("rebuild after failure also failed kb=%s", kb_id)


def _mark_failed(db: Session, doc: Document, code: str, message: str) -> None:
    delete_chunks_for_document(db, doc.id)
    doc.chunk_count = 0
    doc.status = "failed"
    doc.progress = None
    doc.stage_message = None
    doc.error_code = code
    doc.error_message = message
    db.commit()
