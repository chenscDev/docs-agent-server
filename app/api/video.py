"""AI 短视频 API：/v1/video/*（不改动文档问答 /v1/chat）。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agent.cancel_registry import request_cancel
from app.core.config import get_settings
from app.core.errors import raise_api_error
from app.core.ids import new_id
from app.db.models import VideoJob
from app.db.session import get_db
from app.video.assets import save_uploaded_asset
from app.video.creative_agent import iter_creative_agent_sse, iter_creative_plan_sse
from app.video.events import format_sse, make_video_event
from app.video.render_queue import enqueue_video_job
from app.video.schema import TEMPLATE_CATALOG, TemplateId, validate_storyboard
from app.video.preview_page import build_preview_html
from app.video.service import (
    create_job,
    delete_job,
    duplicate_job,
    get_job,
    job_to_dict,
    list_jobs,
    normalize_knowledge_base_ids,
    publish_job,
    remix_job,
    request_job_cancel,
)

router = APIRouter(prefix="/v1/video", tags=["video"])


class CreateJobBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    template_id: TemplateId = Field(default="talking-captions", alias="templateId")
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    knowledge_base_ids: list[str] | None = Field(default=None, alias="knowledgeBaseIds")
    auto_start: bool = Field(default=True, alias="autoStart")
    storyboard: dict[str, Any] | None = None
    owner_id: str | None = Field(default=None, alias="ownerId")

    model_config = ConfigDict(populate_by_name=True)


class PlanBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    template_id: TemplateId = Field(default="talking-captions", alias="templateId")
    brand_notes: str = Field(default="", alias="brandNotes")
    knowledge_hint: str = Field(default="", alias="knowledgeHint")
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    knowledge_base_ids: list[str] | None = Field(default=None, alias="knowledgeBaseIds")

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


@router.post("/assets")
async def upload_video_asset(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传分镜配图 / Logo，返回可公网访问 URL。"""
    data = await file.read()
    try:
        saved = save_uploaded_asset(
            data=data,
            filename=file.filename,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise_api_error(400, "ASSET_INVALID", str(exc))
    return saved


@router.get("/jobs")
def get_jobs(
    limit: int = 50,
    status: str | None = None,
    owner_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items = [
        job_to_dict(j)
        for j in list_jobs(db, limit=limit, status=status, owner_id=owner_id)
    ]
    return {"items": items}


@router.get("/jobs/{job_id}")
def get_job_detail(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    return job_to_dict(job)


@router.delete("/jobs/{job_id}")
def remove_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """删除草稿 / 作品。"""
    ok = delete_job(db, job_id)
    if not ok:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    return {"ok": True, "id": job_id}


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
        knowledge_base_id=normalize_knowledge_base_ids(
            body.knowledge_base_id,
            body.knowledge_base_ids,
        ),
        storyboard=board,
        owner_id=body.owner_id,
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


@router.post("/jobs/{job_id}/duplicate")
def post_duplicate(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """复制为新草稿（不自动开渲）。"""
    source = get_job(db, job_id)
    if source is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    try:
        child = duplicate_job(db, source)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(400, "DUPLICATE_FAILED", str(exc)[:200])
    return job_to_dict(child)


@router.post("/jobs/{job_id}/publish")
def post_publish(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """标记发布（长期渠道对接占位）。"""
    job = get_job(db, job_id)
    if job is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")
    try:
        published = publish_job(db, job)
    except ValueError as exc:
        raise_api_error(400, "PUBLISH_FAILED", str(exc))
    return job_to_dict(published)


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
async def job_events(job_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """任务进度 SSE；断线后可轮询 GET /jobs/{id}。"""
    import asyncio

    job = get_job(db, job_id)
    if job is None:
        raise_api_error(404, "VIDEO_JOB_NOT_FOUND", "视频任务不存在")

    stream_id = new_id("vjobstream")
    job_id_fixed = job.id

    async def gen() -> AsyncIterator[str]:
        seq = 0
        last_status = ""
        last_progress = -1.0
        last_stage = ""
        # 最长跟 10 分钟
        deadline = time.time() + 600
        while time.time() < deadline:
            # 每次开新 session，避免长连接占着 SQLite 锁拖垮其它请求
            from app.db.session import SessionLocal, get_engine

            get_engine()
            assert SessionLocal is not None
            with SessionLocal() as s:
                row = s.get(VideoJob, job_id_fixed)
                if row is None:
                    seq += 1
                    yield format_sse(
                        make_video_event(
                            job_id=job_id_fixed,
                            stream_id=stream_id,
                            seq=seq,
                            event_type="error",
                            payload={
                                "code": "NOT_FOUND",
                                "message": "视频任务不存在",
                            },
                        )
                    )
                    return
                stage = row.stage_message or ""
                changed = (
                    row.status != last_status
                    or (row.progress or 0) != last_progress
                    or stage != last_stage
                )
                if changed:
                    seq += 1
                    last_status = row.status
                    last_progress = float(row.progress or 0)
                    last_stage = stage
                    yield format_sse(
                        make_video_event(
                            job_id=row.id,
                            stream_id=stream_id,
                            seq=seq,
                            event_type="job_progress",
                            payload={
                                "status": row.status,
                                "progress": row.progress,
                                "stageMessage": row.stage_message,
                                "outputUrl": row.output_url,
                                "errorCode": row.error_code,
                                "errorMessage": row.error_message,
                                "storyboard": (
                                    json.loads(row.storyboard_json)
                                    if row.storyboard_json
                                    else None
                                ),
                            },
                        )
                    )
                if row.status in ("ready", "failed", "cancelled"):
                    seq += 1
                    yield format_sse(
                        make_video_event(
                            job_id=row.id,
                            stream_id=stream_id,
                            seq=seq,
                            event_type="done",
                            payload=job_to_dict(row),
                        )
                    )
                    return
            await asyncio.sleep(0.35)

        seq += 1
        yield format_sse(
            make_video_event(
                job_id=job_id_fixed,
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
    kb_ids = normalize_knowledge_base_ids(
        body.knowledge_base_id,
        body.knowledge_base_ids,
    )
    if not hint and kb_ids:
        hint = resolve_knowledge_hint(db, kb_ids)

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
        "remotionLambdaEnabled": bool(settings.remotion_lambda_enabled),
        "remotionPreferLambda": bool(settings.remotion_prefer_lambda),
        "remotionLambdaConfigured": bool(
            settings.remotion_lambda_enabled
            and settings.remotion_lambda_region
            and settings.remotion_lambda_function_name
            and settings.remotion_lambda_serve_url
        ),
        "templates": len(TEMPLATE_CATALOG),
        "cdnPath": "/cdn/video/",
        "ttsEnabled": settings.video_tts_enabled,
        "previewPath": "/v1/video/preview",
    }


@router.get("/preview", response_class=HTMLResponse)
def remotion_style_preview() -> HTMLResponse:
    """
    RN WebView 嵌入的分镜实时预览页。
    协议：preview/update | preview/seek | preview/play | preview/pause
    （与后续 @remotion/player 桥接保持同一消息类型）
    """
    # 去掉无效的 X-Frame-Options；WebView 直接 load URL 不依赖 iframe
    return HTMLResponse(
        content=build_preview_html(),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/player/{job_id}", response_class=HTMLResponse)
def video_player_page(
    job_id: str,
    embed: int = 0,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """免鉴权 HTML5 播放页；embed=1 供 App WebView 全幅内嵌（无标题/下载栏）。"""
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
    cover = job.cover_url or ""
    if cover.startswith("/") and base:
        cover = f"{base}{cover}"
    poster_attr = f' poster="{cover}"' if cover else ""
    # App 内嵌：全屏 video，自动尝试播放，并向 RN 回传状态
    if embed:
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <title>{title}</title>
  <style>
    html,body{{margin:0;padding:0;width:100%;height:100%;background:#000;overflow:hidden;}}
    video{{width:100%;height:100%;object-fit:contain;background:#000;display:block;}}
  </style>
</head>
<body>
  <video id="v" controls playsinline webkit-playsinline preload="auto"{poster_attr} src="{url}"></video>
  <script>
  (function(){{
    var v=document.getElementById('v');
    function post(t){{try{{window.ReactNativeWebView&&window.ReactNativeWebView.postMessage(t);}}catch(e){{}}}}
    if(!v){{return;}}
    v.addEventListener('play',function(){{post('play');}});
    v.addEventListener('pause',function(){{post('pause');}});
    v.addEventListener('ended',function(){{post('ended');}});
    v.addEventListener('loadeddata',function(){{post('ready');}});
    v.addEventListener('canplay',function(){{post('ready');}});
    v.addEventListener('error',function(){{post('mediaerror');}});
    var p=v.play();
    if(p&&p.catch){{p.catch(function(){{post('needtap');}});}}
  }})();
  </script>
</body>
</html>"""
        return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
  <meta property="og:title" content="{title}" />
  <meta property="og:type" content="video.other" />
  <meta property="og:video" content="{url}" />
  {f'<meta property="og:image" content="{cover}" />' if cover else ''}
  <title>{title}</title>
  <style>
    body {{ margin:0; background:#0B1220; color:#E2E8F0; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }}
    .wrap {{ max-width:480px; margin:0 auto; padding:16px; }}
    h1 {{ font-size:18px; margin:12px 0 8px; }}
    /* 视频置顶全宽，封面仅作 poster，避免上方静态图 + 下方视频双层堆叠 */
    video {{ width:100%; max-height:80vh; border-radius:12px; background:#000; display:block; }}
    .meta {{ margin-top:10px; font-size:13px; color:#94A3B8; word-break:break-all; }}
    .share {{ margin-top:14px; display:flex; gap:8px; }}
    .share a {{ flex:1; text-align:center; padding:10px; border-radius:10px; background:#0284C7; color:#fff; text-decoration:none; font-weight:700; }}
  </style>
</head>
<body>
  <div class="wrap">
    <video controls autoplay playsinline webkit-playsinline{poster_attr} src="{url}"></video>
    <h1>{title}</h1>
    <p class="meta">{url}</p>
    <div class="share">
      <a href="{url}" download>下载成片</a>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)
