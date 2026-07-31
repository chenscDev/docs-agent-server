"""视频任务业务逻辑。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_id
from app.db.models import VideoJob
from app.video.materials import normalize_materials
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
    data: dict[str, Any] = {
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
        "materials": _job_materials(job),
        "autoGenerateSceneImages": bool(
            getattr(job, "auto_generate_scene_images", 0)
        ),
        "outputUrl": job.output_url,
        "coverUrl": job.cover_url,
        "durationSec": job.duration_sec,
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "ownerId": getattr(job, "owner_id", None),
        "publishStatus": getattr(job, "publish_status", None) or "draft",
        "publishedAt": (
            job.published_at.isoformat()
            if getattr(job, "published_at", None)
            else None
        ),
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "updatedAt": job.updated_at.isoformat() if job.updated_at else None,
    }
    # 排队位次（仅进行中任务有意义）
    if job.status in {"pending", "scripting", "rendering"}:
        try:
            from app.video.render_queue import get_queue_info

            q = get_queue_info(job.id)
            data["queuePosition"] = q.get("position") or 0
            data["queueAhead"] = q.get("ahead") or 0
            data["queueEtaSec"] = q.get("etaSec") or 0
            data["queueLabel"] = q.get("label") or ""
        except Exception:  # noqa: BLE001
            data["queuePosition"] = 0
            data["queueAhead"] = 0
            data["queueEtaSec"] = 0
            data["queueLabel"] = ""
    return data


def _job_materials(job: VideoJob) -> list[dict[str, str]]:
    """从任务读取规范化素材列表。"""
    raw = getattr(job, "materials_json", None)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return normalize_materials(data)


def create_job(
    db: Session,
    *,
    prompt: str,
    template_id: TemplateId = "talking-captions",
    knowledge_base_id: str | None = None,
    parent_job_id: str | None = None,
    storyboard: Storyboard | None = None,
    owner_id: str | None = None,
    materials: list[dict[str, Any]] | None = None,
    auto_generate_scene_images: bool = False,
    auto_understand_materials: bool = True,
) -> VideoJob:
    settings = get_settings()
    owner = (owner_id or settings.video_default_owner_id or "").strip() or None
    mats = normalize_materials(materials)
    # 识图放在 plan SSE / pipeline，避免创建接口被 VL 长时间阻塞
    _ = auto_understand_materials
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
        owner_id=owner,
        publish_status="draft",
        materials_json=json.dumps(mats, ensure_ascii=False) if mats else None,
        auto_generate_scene_images=1 if auto_generate_scene_images else 0,
    )
    if storyboard is not None:
        # 若客户端分镜未带素材，用 materials 补齐
        if mats:
            from app.video.materials import attach_materials_to_scenes

            storyboard = attach_materials_to_scenes(storyboard, mats)
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


def list_jobs(
    db: Session,
    *,
    limit: int = 50,
    status: str | None = None,
    owner_id: str | None = None,
) -> list[VideoJob]:
    stmt = select(VideoJob).order_by(VideoJob.created_at.desc())
    status_norm = (status or "").strip().lower()
    if status_norm and status_norm != "all":
        stmt = stmt.where(VideoJob.status == status_norm)
    owner = (owner_id or "").strip()
    if owner:
        stmt = stmt.where(VideoJob.owner_id == owner)
    stmt = stmt.limit(max(1, min(limit, 100)))
    return list(db.scalars(stmt).all())


def duplicate_job(db: Session, source: VideoJob) -> VideoJob:
    """复制分镜为新草稿任务（不自动开渲，便于再编辑）。"""
    board = None
    if source.storyboard_json:
        board = validate_storyboard(json.loads(source.storyboard_json))
        board = board.model_copy(
            update={"version": 1, "title": f"{board.title}（副本）"}
        )
    child = create_job(
        db,
        prompt=source.prompt,
        template_id=source.template_id,  # type: ignore[arg-type]
        knowledge_base_id=source.knowledge_base_id,
        parent_job_id=source.id,
        storyboard=board,
        owner_id=source.owner_id,
    )
    child.status = "scripting" if board else "pending"
    child.stage_message = "已复制，可编辑后重新生成"
    child.progress = 0.35 if board else 0.0
    db.commit()
    db.refresh(child)
    return child


_ALLOWED_ASPECTS = ("9:16", "16:9", "1:1")


def export_multi_ratio(
    db: Session,
    source: VideoJob,
    *,
    ratios: list[str] | None = None,
    auto_start: bool = True,
) -> list[VideoJob]:
    """
    同脚本一键多比例导出：为每个目标画幅创建子任务并（可选）入队重渲。

    默认导出与当前不同的另外两种比例；当前比例本身不重复创建。
    """
    if not source.storyboard_json:
        raise ValueError("源任务尚无分镜，无法多比例导出")
    board = validate_storyboard(json.loads(source.storyboard_json))
    current = board.aspectRatio
    wanted = ratios or [r for r in _ALLOWED_ASPECTS if r != current]
    cleaned: list[str] = []
    for raw in wanted:
        r = str(raw or "").strip()
        if r in _ALLOWED_ASPECTS and r not in cleaned:
            cleaned.append(r)
    if not cleaned:
        raise ValueError("没有可导出的目标比例")

    from app.video.render_queue import enqueue_video_job

    children: list[VideoJob] = []
    for ratio in cleaned:
        if ratio == current:
            continue
        child_board = board.model_copy(
            update={
                "version": 1,
                "aspectRatio": ratio,  # type: ignore[arg-type]
                "title": f"{board.title}（{ratio}）",
            }
        )
        child = create_job(
            db,
            prompt=source.prompt,
            template_id=source.template_id,  # type: ignore[arg-type]
            knowledge_base_id=source.knowledge_base_id,
            parent_job_id=source.id,
            storyboard=child_board,
            owner_id=source.owner_id,
        )
        child.stage_message = f"多比例导出 {ratio}，等待渲染"
        db.commit()
        db.refresh(child)
        if auto_start:
            enqueue_video_job(child.id)
            child.stage_message = f"多比例导出 {ratio} 已入队"
            db.commit()
            db.refresh(child)
        children.append(child)
    if not children:
        raise ValueError("目标比例与当前成片相同，无需导出")
    return children


def publish_job(db: Session, job: VideoJob) -> VideoJob:
    """
    标记作品为已发布（仅改库内 publish_status / published_at）。

    说明：当前不对接抖音 / 视频号等外部渠道，属于作品库状态位；
    后续接开放平台时再在此触发实际上传。
    """
    from datetime import datetime, timezone

    if job.status != "ready" or not job.output_url:
        raise ValueError("仅成功成片可发布")
    job.publish_status = "published"
    job.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


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
    return format_knowledge_hint(resolve_knowledge_refs(db, knowledge_base_id))


def normalize_knowledge_base_ids(
    knowledge_base_id: str | None = None,
    knowledge_base_ids: list[str] | None = None,
) -> str | None:
    """合并单选 / 多选知识库 ID，逗号拼接后落库（空则不使用）。"""
    ids: list[str] = []
    if knowledge_base_ids:
        for raw in knowledge_base_ids:
            kid = (raw or "").strip()
            if kid and kid not in ids:
                ids.append(kid)
    if knowledge_base_id:
        for part in knowledge_base_id.split(","):
            kid = part.strip()
            if kid and kid not in ids:
                ids.append(kid)
    if not ids:
        return None
    return ",".join(ids[:8])


def resolve_knowledge_refs(
    db: Session, knowledge_base_id: str | None
) -> list[dict[str, Any]]:
    """
    拉取知识库片段并编号，供分镜强制引用。

    每项：index / chunkId / documentId / documentTitle / snippet
    """
    if not knowledge_base_id:
        return []
    try:
        from app.db.models import Chunk, Document

        ids = [p.strip() for p in knowledge_base_id.split(",") if p.strip()][:5]
        refs: list[dict[str, Any]] = []
        for kid in ids:
            rows = db.scalars(
                select(Chunk)
                .where(Chunk.knowledge_base_id == kid)
                .limit(4)
            ).all()
            for row in rows:
                if not (row.content or "").strip():
                    continue
                doc = db.get(Document, row.document_id)
                title = (doc.title if doc else "") or "知识库文档"
                refs.append(
                    {
                        "index": len(refs) + 1,
                        "chunkId": row.id,
                        "documentId": row.document_id,
                        "documentTitle": title[:80],
                        "snippet": row.content.strip()[:220],
                    }
                )
                if len(refs) >= 8:
                    return refs
        return refs
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb refs skip: %s", exc)
        return []


def format_knowledge_hint(refs: list[dict[str, Any]]) -> str:
    """把编号引用格式化为规划用约束文本。"""
    if not refs:
        return ""
    lines: list[str] = []
    for r in refs:
        title = str(r.get("documentTitle") or "知识库文档")
        snip = str(r.get("snippet") or "").strip()
        idx = int(r.get("index") or 0)
        lines.append(f"[{idx}] 《{title}》：{snip}")
    return "\n".join(lines)


def refs_from_knowledge_hint(hint: str) -> list[dict[str, Any]]:
    """从格式化 hint 反解出引用列表（用于 plan SSE 等仅有文本的场景）。"""
    text = (hint or "").strip()
    if not text:
        return []
    refs: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s*(?:《([^》]*)》[：:]?)?\s*(.+)$", line)
        if not m:
            continue
        refs.append(
            {
                "index": int(m.group(1)),
                "chunkId": "",
                "documentId": "",
                "documentTitle": (m.group(2) or "知识库文档").strip()[:80] or "知识库文档",
                "snippet": (m.group(3) or "").strip()[:220],
            }
        )
    return refs


def apply_knowledge_sources(
    board: Storyboard, refs: list[dict[str, Any]]
) -> Storyboard:
    """
    强制给每个镜头打上来源标签。

    - 若 LLM 已写 sourceIndex，按编号映射；
    - 否则按镜头顺序轮转分配引用。
    """
    if not refs:
        return board
    by_idx = {int(r["index"]): r for r in refs if r.get("index")}
    data = board.model_dump()
    for i, sc in enumerate(data.get("scenes") or []):
        raw_idx = sc.get("sourceIndex")
        ref = None
        if raw_idx is not None:
            try:
                ref = by_idx.get(int(raw_idx))
            except (TypeError, ValueError):
                ref = None
        if ref is None:
            ref = refs[i % len(refs)]
        sc["sourceIndex"] = int(ref["index"])
        sc["sourceChunkId"] = str(ref.get("chunkId") or "")
        title = str(ref.get("documentTitle") or "知识库")
        sc["sourceLabel"] = f"[{ref['index']}] {title}"[:120]
    return validate_storyboard(data)


def delete_job(db: Session, job_id: str) -> bool:
    """删除视频任务及其本地成片目录（若存在）。"""
    job = get_job(db, job_id)
    if job is None:
        return False
    # 尽量清理成片文件，失败不阻塞删库
    if job.output_path:
        try:
            out = Path(job.output_path)
            if out.is_file():
                out.unlink(missing_ok=True)
            parent = out.parent
            if parent.is_dir() and parent.name == job_id:
                shutil.rmtree(parent, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete job files skip id=%s: %s", job_id, exc)
    db.delete(job)
    db.commit()
    return True


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

                refs = resolve_knowledge_refs(db, job.knowledge_base_id)
                hint = format_knowledge_hint(refs)
                mats = _job_materials(job)
                # 兜底：若 materials 尚无 caption，渲染前再识图一次
                if (
                    mats
                    and getattr(settings, "video_vision_enabled", True)
                    and any(not (m.get("caption") or "").strip() for m in mats)
                ):
                    try:
                        from app.video.vision_caption import understand_materials

                        job.stage_message = "正在理解素材画面…"
                        job.progress = 0.15
                        db.commit()

                        def report_vision(message: str, progress: float | None) -> None:
                            if cancelled():
                                return
                            job.stage_message = (message or "")[:500]
                            if progress is not None:
                                job.progress = float(progress)
                            try:
                                db.commit()
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("写入识图进度失败: %s", exc)
                                db.rollback()

                        mats = understand_materials(mats, on_progress=report_vision)
                        job.materials_json = json.dumps(mats, ensure_ascii=False)
                        db.commit()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("pipeline understand_materials failed: %s", exc)

                if cancelled():
                    job.status = "cancelled"
                    db.commit()
                    return

                job.stage_message = "正在规划分镜…"
                job.progress = 0.25
                db.commit()
                board = plan_storyboard(
                    job.prompt,
                    template_id=job.template_id,  # type: ignore[arg-type]
                    knowledge_hint=hint,
                    materials=mats,
                )
                if refs:
                    board = apply_knowledge_sources(board, refs)
                job.storyboard_json = json.dumps(
                    board.to_public_dict(),
                    ensure_ascii=False,
                )
                job.title = board.title
                job.version = board.version
                job.duration_sec = board.total_duration_sec
                job.progress = 0.4
                if refs:
                    job.stage_message = (
                        f"分镜完成（已标注 {len(refs)} 条知识库来源），开始渲染"
                    )
                else:
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

            # 1.5) 可选：按画面说明自动配图（已有 image/video 的镜跳过）
            if int(getattr(job, "auto_generate_scene_images", 0) or 0):
                from app.video.scene_image import fill_missing_scene_images

                def report_t2i(message: str, progress: float) -> None:
                    if cancelled():
                        return
                    job.progress = float(progress)
                    job.stage_message = (message or "")[:500]
                    try:
                        db.commit()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("写入生图进度失败: %s", exc)
                        db.rollback()

                job.status = "scripting"
                job.stage_message = "按画面说明生成分镜配图…"
                job.progress = 0.42
                db.commit()
                board = fill_missing_scene_images(
                    board,
                    on_progress=report_t2i,
                    cancel_check=cancelled,
                )
                job.storyboard_json = json.dumps(
                    board.to_public_dict(),
                    ensure_ascii=False,
                )
                job.stage_message = "配图完成，开始渲染"
                job.progress = 0.48
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
                parent_job_id=job.parent_job_id,
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
