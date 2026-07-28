"""视频渲染队列（进程内串行，语义对齐文档解析队列）。"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.db import session as db_session
from app.db.models import VideoJob
from app.video.service import run_job_pipeline

logger = logging.getLogger(__name__)

_RECOVERABLE = frozenset({"pending", "scripting", "rendering"})

_lock = threading.Lock()
_queued_or_running: set[str] = set()
_executor: ThreadPoolExecutor | None = None


def enqueue_video_job(job_id: str) -> bool:
    job_id = (job_id or "").strip()
    if not job_id:
        return False
    with _lock:
        if job_id in _queued_or_running:
            return False
        _queued_or_running.add(job_id)
        if _executor is not None:
            _submit_locked(job_id)
    return True


def start_video_queue(*, recover: bool = True) -> int:
    global _executor
    recovered = 0
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="video-worker",
            )
            logger.info("video queue started")
            for jid in list(_queued_or_running):
                _submit_locked(jid)
    if recover:
        recovered = recover_incomplete_jobs()
    return recovered


def stop_video_queue(*, wait: bool = False) -> None:
    global _executor
    with _lock:
        ex = _executor
        _executor = None
        _queued_or_running.clear()
    if ex is not None:
        ex.shutdown(wait=wait, cancel_futures=False)
        logger.info("video queue stopped")


def recover_incomplete_jobs() -> int:
    db_session.get_engine()
    assert db_session.SessionLocal is not None
    n = 0
    with db_session.SessionLocal() as db:
        rows = db.scalars(
            select(VideoJob).where(VideoJob.status.in_(tuple(_RECOVERABLE)))
        ).all()
        for job in rows:
            if job.cancel_requested:
                job.status = "cancelled"
                continue
            # 中断态重置为 pending 再入队
            job.status = "pending"
            job.stage_message = "服务重启，重新排队"
            job.progress = 0.0
            n += 1
            enqueue_video_job(job.id)
        db.commit()
    logger.info("video recover enqueued=%s", n)
    return n


def _submit_locked(job_id: str) -> None:
    assert _executor is not None

    def _run() -> None:
        try:
            run_job_pipeline(job_id)
            # 失败自动再试一次（弱网 / 瞬时 ffmpeg 错误）
            db_session.get_engine()
            assert db_session.SessionLocal is not None
            with db_session.SessionLocal() as db:
                job = db.get(VideoJob, job_id)
                if (
                    job is not None
                    and job.status == "failed"
                    and not job.cancel_requested
                    and (job.error_code or "") == "RENDER_FAILED"
                    and "auto-retry" not in (job.stage_message or "")
                ):
                    job.status = "pending"
                    job.progress = 0.0
                    job.error_code = None
                    job.error_message = None
                    job.stage_message = "渲染失败，自动重试中…"
                    db.commit()
                    logger.warning("video auto-retry id=%s", job_id)
                    run_job_pipeline(job_id)
                    with db_session.SessionLocal() as db2:
                        job2 = db2.get(VideoJob, job_id)
                        if job2 is not None and job2.status == "failed":
                            job2.stage_message = (
                                (job2.stage_message or "渲染失败") + "（已 auto-retry）"
                            )
                            db2.commit()
        finally:
            with _lock:
                _queued_or_running.discard(job_id)

    _executor.submit(_run)
