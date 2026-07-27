"""文档解析进度 SSE（P3-D6）：轮询 DB 变更并推送。"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

from app.agent.sse import format_sse, make_doc_event
from app.core.ids import new_id
from app.db import session as db_session
from app.db.models import Document
from app.schemas.documents import DocumentOut

logger = logging.getLogger(__name__)

# 服务端内部轮询间隔（秒）；端上收到的是推送事件
_POLL_INTERVAL_SEC = 0.6
# 单次订阅最长等待（秒），防止僵尸连接
_MAX_WAIT_SEC = 30 * 60
# 心跳间隔（秒）
_HEARTBEAT_SEC = 12.0

_TERMINAL = frozenset({"ready", "failed"})


def _to_payload(doc: Document) -> dict[str, Any]:
    """与 DocumentOut 驼峰字段对齐，便于 RN 直接当 DocumentItem 用。"""
    out = DocumentOut(
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
    return out.model_dump(by_alias=True, mode="json")


def _fingerprint(doc: Document) -> tuple[Any, ...]:
    return (
        doc.status,
        doc.progress,
        doc.stage_message,
        doc.chunk_count,
        doc.error_code,
        doc.error_message,
    )


def iter_document_parse_sse(doc_id: str) -> Iterator[str]:
    """
    产出 SSE 文本帧。

    事件：document.snapshot → document.progress* → document.completed
    或 error（文档不存在 / 超时）。
    """
    stream_id = new_id("docevt")
    seq = 0

    def emit(event_type: str, payload: dict[str, Any]) -> str:
        nonlocal seq
        ev = make_doc_event(
            stream_id=stream_id,
            document_id=doc_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
        )
        seq += 1
        return format_sse(ev)

    db_session.get_engine()
    factory = db_session.SessionLocal
    if factory is None:
        yield emit(
            "error",
            {"code": "DB_NOT_READY", "message": "数据库未初始化", "retryable": True},
        )
        return

    started = time.monotonic()
    last_beat = started
    last_fp: tuple[Any, ...] | None = None

    with factory() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            yield emit(
                "error",
                {
                    "code": "DOC_NOT_FOUND",
                    "message": "文档不存在",
                    "retryable": False,
                },
            )
            return

        payload = _to_payload(doc)
        last_fp = _fingerprint(doc)
        yield emit("document.snapshot", payload)

        if doc.status in _TERMINAL:
            yield emit("document.completed", payload)
            return

        while True:
            elapsed = time.monotonic() - started
            if elapsed > _MAX_WAIT_SEC:
                yield emit(
                    "error",
                    {
                        "code": "PARSE_TIMEOUT",
                        "message": "等待解析超时，请改为轮询或重新打开页面",
                        "retryable": True,
                    },
                )
                return

            time.sleep(_POLL_INTERVAL_SEC)
            db.expire_all()
            doc = db.get(Document, doc_id)
            if doc is None:
                yield emit(
                    "error",
                    {
                        "code": "DOC_NOT_FOUND",
                        "message": "文档已被删除",
                        "retryable": False,
                    },
                )
                return

            fp = _fingerprint(doc)
            if fp != last_fp:
                last_fp = fp
                payload = _to_payload(doc)
                if doc.status in _TERMINAL:
                    yield emit("document.completed", payload)
                    return
                yield emit("document.progress", payload)
                last_beat = time.monotonic()
                continue

            # 无变更：定期心跳，避免中间层掐长连接
            now = time.monotonic()
            if now - last_beat >= _HEARTBEAT_SEC:
                yield ": keepalive\n\n"
                last_beat = now
