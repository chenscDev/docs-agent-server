"""视频任务业务逻辑。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_id
from app.db.models import VideoJob
from app.video.planner import patch_storyboard, plan_storyboard, refine_scene
from app.video.renderer import render_storyboard_to_mp4
from app.video.schema import Storyboard, TemplateId, validate_storyboard

logger = logging.getLogger(__name__)


def job_to_dict(job: VideoJob) -> dict[str, Any]:
    storyboard = None
    if job.storyboard_json:
        try:
            storyboard = json.loads(job.storyboard_json)
        except json.JSONDecodeError:
            storyboard = None
    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "stageMessage": job.stage_message,
        "prompt": job.prompt,
        "templateId": job.template_id,
        "title": job.title,
        "version": job.version,
        "parentJobId": job.parent_job_id,
        "knowledgeBaseId": job.knowledge_base_id,
        "storyboard": storyboard,
        "outputUrl": job.output_url,
        "coverUrl": job.cover_url,
        "durationSec": job.duration_sec,
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "updatedAt": job.updated_at.isoformat() if job.updated_at else None,
    }


def create_job(
    db: Session,
    *,
    prompt: str,
    template_id: TemplateId = "talking-captions",
    knowledge_base_id: str | None = None,
    parent_job_id: str | None = None,
    storyboard: Storyboard | None = None,
) -> VideoJob:
    job = VideoJob(
        id=new_id("vjob"),
        status="pending",
        progress=0.0,
        stage_message="已创建，等待规划分镜",
        prompt=prompt.strip(),
        template_id=template_id,
        knowledge_base_id=knowledge_base_id,
        parent_job_id=parent_job_id,
        version=1,
    )
    if storyboard is not None:
        job.storyboard_json = json.dumps(
            storyboard.to_public_dict(),
            ensure_ascii=False,
        )
        job.title = storyboard.title
        job.version = storyboard.version
        job.status = "scripting"
        job.stage_message = "分镜已就绪，等待渲染"
        job.progress = 0.35
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_jobs(db: Session, *, limit: int = 50) -> list[VideoJob]:
    stmt = (
        select(VideoJob)
        .order_by(VideoJob.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return list(db.scalars(stmt).all())


def get_job(db: Session, job_id: str) -> VideoJob | None:
    return db.get(VideoJob, job_id)


def request_job_cancel(db: Session, job_id: str) -> VideoJob | None:
    job = db.get(VideoJob, job_id)
    if job is None:
        return None
    job.cancel_requested = 1
    if job.status in ("pending", "scripting", "rendering"):
        job.status = "cancelled"
        job.stage_message = "已取消"
        job.error_code = "CANCELLED"
        job.error_message = "用户取消"
    db.commit()
    db.refresh(job)
    return job


def _kb_hint(db: Session, knowledge_base_id: str | None) -> str:
    """可选：从 KB 取若干 chunk 作品牌约束（轻量，不跑完整 Agent）。"""
    if not knowledge_base_id:
        return ""
    try:
        from app.db.models import Chunk

        rows = db.scalars(
            select(Chunk)
            .where(Chunk.knowledge_base_id == knowledge_base_id)
            .limit(3)
        ).all()
        texts = [r.content.strip()[:200] for r in rows if r.content]
        return "\n".join(texts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb hint skip: %s", exc)
        return ""


def run_job_pipeline(job_id: str) -> None:
    """在 worker 线程中执行：scripting → rendering → ready/failed。"""
    from app.db import session as db_session

    db_session.get_engine()
    assert db_session.SessionLocal is not None
    with db_session.SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if job is None:
            return
        if job.cancel_requested or job.status == "cancelled":
            return

        settings = get_settings()
        out_dir = Path(settings.video_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        def cancelled() -> bool:
            db.refresh(job)
            return bool(job.cancel_requested) or job.status == "cancelled"

        try:
            # 1) 分镜
            if not job.storyboard_json:
                job.status = "scripting"
                job.progress = 0.1
                job.stage_message = "正在规划分镜…"
                db.commit()

                if cancelled():
                    job.status = "cancelled"
                    db.commit()
                    return

                hint = _kb_hint(db, job.knowledge_base_id)
                board = plan_storyboard(
                    job.prompt,
                    template_id=job.template_id,  # type: ignore[arg-type]
                    knowledge_hint=hint,
                )
                job.storyboard_json = json.dumps(
                    board.to_public_dict(),
                    ensure_ascii=False,
                )
                job.title = board.title
                job.version = board.version
                job.duration_sec = board.total_duration_sec
                job.progress = 0.4
                job.stage_message = "分镜完成，开始渲染"
                db.commit()
            else:
                board = validate_storyboard(json.loads(job.storyboard_json))
                job.duration_sec = board.total_duration_sec
                job.title = job.title or board.title
                db.commit()

            if cancelled():
                job.status = "cancelled"
                db.commit()
                return

            # 2) 渲染
            job.status = "rendering"
            job.progress = 0.5
            job.stage_message = "渲染中（Remotion/FFmpeg）…"
            db.commit()

            out_file = out_dir / f"{job.id}_v{job.version}.mp4"
            render_storyboard_to_mp4(
                board,
                output_path=out_file,
                job_id=job.id,
                cancel_check=cancelled,
            )

            if cancelled():
                job.status = "cancelled"
                db.commit()
                return

            public_base = (settings.video_public_base_url or "").rstrip("/")
            rel = f"/cdn/video/{out_file.name}"
            job.output_path = str(out_file)
            job.output_url = f"{public_base}{rel}" if public_base else rel
            job.status = "ready"
            job.progress = 1.0
            job.stage_message = "渲染完成"
            job.error_code = None
            job.error_message = None
            db.commit()
            logger.info("video job ready id=%s url=%s", job.id, job.output_url)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if msg == "CANCELLED" or cancelled():
                job.status = "cancelled"
                job.error_code = "CANCELLED"
                job.error_message = "用户取消"
            else:
                job.status = "failed"
                job.error_code = "RENDER_FAILED"
                job.error_message = msg[:500]
                job.stage_message = "渲染失败"
            db.commit()
            logger.exception("video job failed id=%s", job_id)


def remix_job(
    db: Session,
    parent: VideoJob,
    *,
    scene_id: str | None = None,
    instruction: str | None = None,
    patches: dict[str, Any] | None = None,
    new_prompt: str | None = None,
) -> VideoJob:
    """基于已有分镜创建新版本任务并入队渲染。"""
    if not parent.storyboard_json:
        raise ValueError("父任务尚无分镜，无法 Remix")
    board = validate_storyboard(json.loads(parent.storyboard_json))
    if scene_id and instruction:
        board = refine_scene(board, scene_id=scene_id, instruction=instruction)
    if patches:
        board = patch_storyboard(board, patches=patches)
    prompt = (new_prompt or parent.prompt).strip()
    child = create_job(
        db,
        prompt=prompt,
        template_id=board.templateId,
        knowledge_base_id=parent.knowledge_base_id,
        parent_job_id=parent.id,
        storyboard=board,
    )
    child.version = max(parent.version + 1, board.version)
    child.title = board.title
    db.commit()
    db.refresh(child)
    return child
