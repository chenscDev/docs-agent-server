"""解析任务队列（P2-D5～D6）。

为什么不用 FastAPI BackgroundTasks：
任务只活在进程内存里，uvicorn 被杀 / --reload 重启后「解析中」文档会永远卡死。

本方案（轻量、无 Redis）：
1. 上传时 status=pending 已落 SQLite → 持久化「至少有一条待做任务」
2. 进程内 ThreadPoolExecutor（串行）执行 run_parse_job
3. 启动时扫描 pending/parsing/indexing，中断态重置为 pending 后重新入队

语义：至少一次（at-least-once）。同一 doc 可能因重启被跑两次；
run_parse_job 开头会清 chunks 再跑，结果幂等到 ready/failed。
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import select

from app.db import session as db_session
from app.db.models import Document
from app.rag.pipeline import delete_chunks_for_document, run_parse_job

logger = logging.getLogger(__name__)

# 可恢复：排队中 / 被杀时卡在半路
_RECOVERABLE_STATUSES = frozenset({"pending", "parsing", "indexing"})

_lock = threading.Lock()
_queued_or_running: set[str] = set()
_executor: ThreadPoolExecutor | None = None
_started = False


def enqueue_parse(doc_id: str) -> bool:
    """
    投递解析任务。

    已在队列或执行中则跳过（去重）。返回是否新入队。
    若工作线程尚未 start，仍先记入集合，start 时一并提交。
    """
    doc_id = (doc_id or "").strip()
    if not doc_id:
        return False

    with _lock:
        if doc_id in _queued_or_running:
            logger.info("parse skip duplicate doc=%s", doc_id)
            return False
        _queued_or_running.add(doc_id)
        if _executor is not None:
            _submit_locked(doc_id)
    return True


def start_parse_queue(*, recover: bool = True) -> int:
    """
    启动串行 worker；可选恢复未完成文档。

    返回本次恢复入队的文档数量。
    """
    global _executor, _started
    recovered = 0
    with _lock:
        if _executor is None:
            # 串行：避免同 KB 并发 rebuild FAISS / SQLite 写冲突
            _executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="parse-worker",
            )
            _started = True
            logger.info("parse queue started (workers=1)")
            # 把 start 之前仅登记、未提交的任务补交（只在首次启动时）
            for doc_id in list(_queued_or_running):
                _submit_locked(doc_id)

    if recover:
        recovered = recover_incomplete_parses()
    return recovered


def stop_parse_queue(*, wait: bool = False) -> None:
    """关闭 worker（进程退出时调用）。"""
    global _executor, _started
    with _lock:
        ex = _executor
        _executor = None
        _started = False
        _queued_or_running.clear()
    if ex is not None:
        ex.shutdown(wait=wait, cancel_futures=False)
        logger.info("parse queue stopped")


def recover_incomplete_parses() -> int:
    """
    扫描 DB 中未完成文档并重新入队。

    parsing/indexing：清半成品 chunks，重置为 pending 再跑。
    pending：直接入队。
    """
    db_session.get_engine()
    factory = db_session.SessionLocal
    if factory is None:
        logger.error("recover skipped: SessionLocal not ready")
        return 0

    to_enqueue: list[str] = []
    with factory() as db:
        rows = db.scalars(
            select(Document)
            .where(Document.status.in_(sorted(_RECOVERABLE_STATUSES)))
            .order_by(Document.created_at.asc())
        ).all()
        for doc in rows:
            if doc.status in ("parsing", "indexing"):
                prev_status = doc.status
                delete_chunks_for_document(db, doc.id)
                doc.chunk_count = 0
                doc.status = "pending"
                doc.progress = 0.0
                doc.stage_message = "服务重启，重新排队解析"
                doc.error_code = None
                doc.error_message = None
                logger.warning(
                    "recover reset mid-parse doc=%s was=%s",
                    doc.id,
                    prev_status,
                )
            else:
                doc.stage_message = doc.stage_message or "排队等待解析"
            to_enqueue.append(doc.id)
        if to_enqueue:
            db.commit()

    for doc_id in to_enqueue:
        enqueue_parse(doc_id)

    if to_enqueue:
        logger.info("parse recover enqueued n=%s ids=%s", len(to_enqueue), to_enqueue)
    return len(to_enqueue)


def queue_snapshot() -> dict[str, object]:
    """调试用：当前队列快照。"""
    with _lock:
        return {
            "started": _started,
            "queuedOrRunning": sorted(_queued_or_running),
            "size": len(_queued_or_running),
        }


def _submit_locked(doc_id: str) -> Future[None] | None:
    """持锁调用：向 executor 提交任务。"""
    assert _executor is not None
    return _executor.submit(_run_safe, doc_id)


def _run_safe(doc_id: str) -> None:
    """执行解析并保证从排队集合中移除。"""
    try:
        logger.info("parse job start doc=%s", doc_id)
        run_parse_job(doc_id)
        logger.info("parse job done doc=%s", doc_id)
    except Exception:  # noqa: BLE001
        logger.exception("parse job crashed doc=%s", doc_id)
    finally:
        with _lock:
            _queued_or_running.discard(doc_id)
