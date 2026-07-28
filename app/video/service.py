"""视频任务业务逻辑。"""

from __future__ import annotations

import json
import logging
import shutil
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


def resolve_knowledge_hint(db: Session, knowledge_base_id: str | None) -> str:
    """供 API / pipeline 复用：从知识库抽若干 chunk 作为创作约束。"""
    return _kb_hint(db, knowledge_base_id)


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

        def public_url(rel_or_name: str) -> str:
            name = Path(rel_or_name).name
            rel = f"/cdn/video/{name}"
            base = (settings.video_public_base_url or "").rstrip("/")
            return f"{base}{rel}" if base else rel

        def mirror_file(src: Path) -> None:
            mirror = (settings.video_cdn_mirror_dir or "").strip()
            if not mirror or not src.is_file():
                return
            dest_dir = Path(mirror)
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / src.name
                if dest.resolve() != src.resolve():
                    shutil.copyfile(src, dest)
            except OSError as exc:
                logger.warning("镜像 CDN 文件失败: %s", exc)

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
            job.stage_message = "开始渲染成片…"
            db.commit()

            def report_render(message: str, progress: float) -> None:
                """渲染过程中刷新进度，供 SSE / 轮询展示。"""
                if cancelled():
                    return
                job.progress = float(progress)
                job.stage_message = (message or "")[:500]
                try:
                    db.commit()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("写入渲染进度失败: %s", exc)
                    db.rollback()

            out_file = out_dir / f"{job.id}_v{job.version}.mp4"
            render = render_storyboard_to_mp4(
                board,
                output_path=out_file,
                job_id=job.id,
                cancel_check=cancelled,
                on_progress=report_render,
            )

            if cancelled():
                job.status = "cancelled"
                db.commit()
                return

            # 回写分镜缩略图 URL
            for scene in board.scenes:
                thumb = render.scene_thumbs.get(scene.id)
                if thumb is not None and thumb.is_file():
                    scene.thumbUrl = public_url(thumb.name)
                    mirror_file(thumb)

            job.storyboard_json = json.dumps(
                board.to_public_dict(),
                ensure_ascii=False,
            )
            job.output_path = str(render.output_path)
            job.output_url = public_url(render.output_path.name)
            mirror_file(render.output_path)

            if render.cover_path is not None and render.cover_path.is_file():
                job.cover_url = public_url(render.cover_path.name)
                mirror_file(render.cover_path)

            job.duration_sec = board.total_duration_sec
            job.status = "ready"
            job.progress = 1.0
            audio_hint = "含配音" if render.has_audio else "静音片（TTS 未启用或失败）"
            job.stage_message = f"渲染完成（{audio_hint}）"
            job.error_code = None
            job.error_message = None
            db.commit()
            logger.info(
                "video job ready id=%s url=%s audio=%s",
                job.id,
                job.output_url,
                render.has_audio,
            )
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
    if patches:
        board = patch_storyboard(board, patches=patches)
    if scene_id and instruction:
        board = refine_scene(board, scene_id=scene_id, instruction=instruction)
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
