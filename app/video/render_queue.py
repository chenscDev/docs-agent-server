"""视频渲染队列（进程内串行，语义对齐文档解析队列）。"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.db import session as db_session
from app.db.models import VideoJob
from app.video.service import run_job_pipeline

logger = logging.getLogger(__name__)

_RECOVERABLE = frozenset({"pending", "scripting", "rendering"})

_lock = threading.Lock()
_queued_or_running: set[str] = set()
# 排队顺序（含当前正在跑的队首）；用于位次 / 预计等待
_queue_order: list[str] = []
_executor: ThreadPoolExecutor | None = None

# 单任务粗估秒数（演示机串行渲染）
_ETA_SEC_PER_JOB = 50


def enqueue_video_job(job_id: str) -> bool:
    job_id = (job_id or "").strip()
    if not job_id:
        return False
    with _lock:
        if job_id in _queued_or_running:
            return False
        _queued_or_running.add(job_id)
        _queue_order.append(job_id)
        if _executor is not None:
            _submit_locked(job_id)
    _refresh_queue_stage_messages()
    return True


def get_queue_info(job_id: str) -> dict[str, int | str]:
    """返回排队位次（1 起算）、前方人数、预计等待秒。"""
    jid = (job_id or "").strip()
    with _lock:
        order = list(_queue_order)
    total = len(order)
    if not jid or jid not in order:
        return {
            "position": 0,
            "ahead": 0,
            "total": total,
            "etaSec": 0,
            "label": "",
        }
    pos = order.index(jid) + 1
    ahead = pos - 1
    eta = ahead * _ETA_SEC_PER_JOB
    if ahead <= 0:
        label = "正在渲染…"
    else:
        mins = max(1, (eta + 29) // 60)
        label = f"排队中：第 {pos}/{total} 位，前方 {ahead} 个，约 {mins} 分钟"
    return {
        "position": pos,
        "ahead": ahead,
        "total": total,
        "etaSec": eta,
        "label": label,
    }


def _refresh_queue_stage_messages() -> None:
    """把排队位次写回尚未开渲的 pending 任务 stage_message。"""
    try:
        db_session.get_engine()
        assert db_session.SessionLocal is not None
        with _lock:
            order = list(_queue_order)
        if not order:
            return
        with db_session.SessionLocal() as db:
            for i, jid in enumerate(order):
                job = db.get(VideoJob, jid)
                if job is None:
                    continue
                if job.status not in {"pending", "scripting"}:
                    continue
                ahead = i
                total = len(order)
                if ahead <= 0 and job.status == "pending":
                    # 队首可能马上开渲，保留已有文案或给轻提示
                    if not (job.stage_message or "").strip():
                        job.stage_message = "即将开始渲染…"
                elif ahead > 0:
                    eta = ahead * _ETA_SEC_PER_JOB
                    mins = max(1, (eta + 29) // 60)
                    job.stage_message = (
                        f"排队中：第 {i + 1}/{total} 位，前方 {ahead} 个，约 {mins} 分钟"
                    )
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("refresh queue stage messages failed")


def start_video_queue(*, recover: bool = True, force: bool = False) -> int:
    """启动进程内渲染 worker；VIDEO_QUEUE_EXTERNAL=true 时跳过（由独立 worker 消费）。"""
    global _executor
    from app.core.config import get_settings

    if get_settings().video_queue_external and not force:
        logger.info("video queue external mode: skip in-process worker")
        if recover:
            return recover_incomplete_jobs()
        return 0
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


def video_queue_busy() -> bool:
    """是否有任务正在渲染 / 排队（供优雅停机判断）。"""
    with _lock:
        return bool(_queued_or_running)


def stop_video_queue(*, wait: bool = False, timeout_sec: float = 120.0) -> None:
    """
    停止队列。

    wait=True 时尽量等当前渲染结束，避免部署重启卡在「配音后拼接」窗口导致客户端 502。
    """
    global _executor
    with _lock:
        ex = _executor
        _executor = None
        busy = list(_queued_or_running)
        # 不清空 busy 标记：worker finally 里仍会 discard；此处仅停止接新任务
    if ex is None:
        return
    if wait and busy:
        logger.info(
            "video queue draining jobs=%s timeout=%.0fs",
            busy,
            timeout_sec,
        )
        deadline = time.time() + max(5.0, float(timeout_sec))
        while time.time() < deadline:
            with _lock:
                still = bool(_queued_or_running)
            if not still:
                break
            time.sleep(0.5)
        with _lock:
            left = list(_queued_or_running)
        if left:
            logger.warning("video queue drain timeout leftover=%s", left)
            ex.shutdown(wait=False, cancel_futures=False)
        else:
            ex.shutdown(wait=True, cancel_futures=False)
    else:
        ex.shutdown(wait=wait, cancel_futures=False)
    with _lock:
        _queued_or_running.clear()
        _queue_order.clear()
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
                try:
                    _queue_order.remove(job_id)
                except ValueError:
                    pass
            _refresh_queue_stage_messages()

    _executor.submit(_run)
