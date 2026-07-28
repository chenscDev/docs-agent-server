"""AI 短视频 API：/v1/video/*（不改动文档问答 /v1/chat）。"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agent.cancel_registry import request_cancel
from app.core.config import get_settings
from app.core.errors import raise_api_error
from app.core.ids import new_id
from app.db.session import get_db
from app.video.creative_agent import iter_creative_agent_sse, iter_creative_plan_sse
from app.video.events import format_sse, make_video_event
from app.video.render_queue import enqueue_video_job
from app.video.schema import TEMPLATE_CATALOG, TemplateId, validate_storyboard
from app.video.service import (
    create_job,
    get_job,
    job_to_dict,
    list_jobs,
    remix_job,
    request_job_cancel,
)

router = APIRouter(prefix="/v1/video", tags=["video"])


class CreateJobBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    template_id: TemplateId = Field(default="talking-captions", alias="templateId")
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    auto_start: bool = Field(default=True, alias="autoStart")
    storyboard: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class PlanBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    template_id: TemplateId = Field(default="talking-captions", alias="templateId")
    brand_notes: str = Field(default="", alias="brandNotes")
    knowledge_hint: str = Field(default="", alias="knowledgeHint")
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")

    model_config = ConfigDict(populate_by_name=True)


class CreativeAgentBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(default_factory=lambda: new_id("vcre"), alias="sessionId")
    template_id: TemplateId = Field(default="talking-captions", alias="templateId")
    storyboard: dict[str, Any] | None = None
    knowledge_hint: str = Field(default="", alias="knowledgeHint")

    model_config = ConfigDict(populate_by_name=True)


class RemixBody(BaseModel):
    scene_id: str | None = Field(default=None, alias="sceneId")
    instruction: str | None = None
    patches: dict[str, Any] | None = None
    prompt: str | None = None
    auto_start: bool = Field(default=True, alias="autoStart")

    model_config = ConfigDict(populate_by_name=True)


class CancelJobBody(BaseModel):
    job_id: str | None = Field(default=None, alias="jobId")
    stream_id: str | None = Field(default=None, alias="streamId")
    request_id: str | None = Field(default=None, alias="requestId")

    model_config = ConfigDict(populate_by_name=True)


@router.get("/templates")
def get_templates() -> dict[str, Any]:
    return {"items": TEMPLATE_CATALOG}


@router.get("/jobs")
def get_jobs(limit: int = 50, db: Session = Depends(get_db)) -> dict[str, Any]:
    items = [job_to_dict(j) for j in list_jobs(db, limit=limit)]
    return {"items": items}


@router.get("/jobs/{job_id}")
def get_job_detail(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    return job_to_dict(job)


@router.post("/jobs")
def post_job(body: CreateJobBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    board = None
    if body.storyboard:
        try:
            board = validate_storyboard(body.storyboard)
        except Exception as exc:  # noqa: BLE001
            raise_api_error(400, "STORYBOARD_INVALID", str(exc)[:200])

    job = create_job(
        db,
        prompt=body.prompt,
        template_id=body.template_id,
        knowledge_base_id=body.knowledge_base_id,
        storyboard=board,
    )
    if body.auto_start:
        enqueue_video_job(job.id)
        job.stage_message = job.stage_message or "已入队"
        db.commit()
        db.refresh(job)
    return job_to_dict(job)


@router.post("/jobs/{job_id}/start")
def start_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    if job.status in ("ready", "cancelled"):
        raise_api_error(409, "VIDEO_JOB_DONE", f"任务已结束: {job.status}")
    enqueue_video_job(job.id)
    return job_to_dict(job)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    job.status = "pending"
    job.cancel_requested = 0
    job.error_code = None
    job.error_message = None
    job.progress = 0.0
    job.stage_message = "重试排队中"
    # 保留分镜：仅重新渲染；无分镜则整条 pipeline
    db.commit()
    enqueue_video_job(job.id)
    db.refresh(job)
    return job_to_dict(job)


@router.post("/jobs/{job_id}/remix")
def post_remix(
    job_id: str,
    body: RemixBody,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    parent = get_job(db, job_id)
    if parent is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    try:
        child = remix_job(
            db,
            parent,
            scene_id=body.scene_id,
            instruction=body.instruction,
            patches=body.patches,
            new_prompt=body.prompt,
        )
    except ValueError as exc:
        raise_api_error(409, "REMIX_NOT_READY", str(exc))
    if body.auto_start:
        enqueue_video_job(child.id)
    return job_to_dict(child)


@router.post("/cancel")
def cancel_video(body: CancelJobBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    """取消渲染任务和/或创作流。"""
    results: dict[str, Any] = {"job": None, "stream": None}
    if body.job_id:
        job = request_job_cancel(db, body.job_id)
        results["job"] = job_to_dict(job) if job else None
    if body.stream_id or body.request_id:
        found, rid = request_cancel(
            request_id=body.request_id or body.stream_id,
            session_id=body.stream_id,
        )
        results["stream"] = {"ok": found, "requestId": rid}
    return results


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """任务进度 SSE；断线后可轮询 GET /jobs/{id}。"""
    job = get_job(db, job_id)
    if job is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")

    stream_id = new_id("vjobstream")

    def gen() -> Iterator[str]:
        seq = 0
        last_status = ""
        last_progress = -1.0
        # 最长跟 10 分钟
        deadline = time.time() + 600
        while time.time() < deadline:
            db.refresh(job)
            changed = job.status != last_status or (job.progress or 0) != last_progress
            if changed:
                seq += 1
                last_status = job.status
                last_progress = float(job.progress or 0)
                yield format_sse(
                    make_video_event(
                        job_id=job.id,
                        stream_id=stream_id,
                        seq=seq,
                        event_type="job_progress",
                        payload={
                            "status": job.status,
                            "progress": job.progress,
                            "stageMessage": job.stage_message,
                            "outputUrl": job.output_url,
                            "errorCode": job.error_code,
                            "errorMessage": job.error_message,
                            "storyboard": (
                                json.loads(job.storyboard_json)
                                if job.storyboard_json
                                else None
                            ),
                        },
                    )
                )
            if job.status in ("ready", "failed", "cancelled"):
                seq += 1
                yield format_sse(
                    make_video_event(
                        job_id=job.id,
                        stream_id=stream_id,
                        seq=seq,
                        event_type="done",
                        payload=job_to_dict(job),
                    )
                )
                return
            time.sleep(0.6)

        seq += 1
        yield format_sse(
            make_video_event(
                job_id=job.id,
                stream_id=stream_id,
                seq=seq,
                event_type="error",
                payload={"code": "TIMEOUT", "message": "进度订阅超时，请轮询任务详情"},
            )
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/plan/stream")
def plan_stream(body: PlanBody, db: Session = Depends(get_db)) -> StreamingResponse:
    """流式分镜规划（不落库；确认后再 POST /jobs）。"""
    from app.video.service import resolve_knowledge_hint

    hint = (body.knowledge_hint or "").strip()
    if not hint and body.knowledge_base_id:
        hint = resolve_knowledge_hint(db, body.knowledge_base_id)

    def gen() -> Iterator[str]:
        yield from iter_creative_plan_sse(
            prompt=body.prompt,
            template_id=body.template_id,
            brand_notes=body.brand_notes,
            knowledge_hint=hint,
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/creative/stream")
def creative_stream(body: CreativeAgentBody) -> StreamingResponse:
    """创作助手 Agent SSE（独立于 /v1/chat）。"""

    def gen() -> Iterator[str]:
        yield from iter_creative_agent_sse(
            message=body.message,
            session_id=body.session_id,
            template_id=body.template_id,
            storyboard=body.storyboard,
            knowledge_hint=body.knowledge_hint,
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/meta")
def video_meta() -> dict[str, Any]:
    settings = get_settings()
    out = Path(settings.video_output_dir)
    return {
        "renderer": settings.video_renderer,
        "outputDir": str(out),
        "remotionProject": settings.remotion_project_dir,
        "templates": len(TEMPLATE_CATALOG),
        "cdnPath": "/cdn/video/",
        "ttsEnabled": settings.video_tts_enabled,
    }


@router.get("/player/{job_id}", response_class=HTMLResponse)
def video_player_page(job_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """免鉴权 HTML5 播放页（App 内 Linking 打开即可看片+听声）。"""
    job = get_job(db, job_id)
    if job is None or not job.output_url:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在或尚未成片")
    settings = get_settings()
    base = (settings.video_public_base_url or "").rstrip("/")
    url = job.output_url
    if url.startswith("/") and base:
        url = f"{base}{url}"
    elif url.startswith("/"):
        # 相对路径：浏览器同 Host 访问即可
        pass
    title = (job.title or "AI 短视频").replace("<", "").replace(">", "")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
  <title>{title}</title>
  <style>
    body {{ margin:0; background:#0B1220; color:#E2E8F0; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }}
    .wrap {{ max-width:480px; margin:0 auto; padding:16px; }}
    h1 {{ font-size:18px; margin:0 0 12px; }}
    video {{ width:100%; border-radius:12px; background:#000; }}
    .meta {{ margin-top:10px; font-size:13px; color:#94A3B8; word-break:break-all; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{title}</h1>
    <video controls autoplay playsinline src="{url}"></video>
    <p class="meta">{url}</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)
