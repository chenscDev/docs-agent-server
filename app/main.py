"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    chat,
    chunks,
    debug,
    documents,
    feedback,
    health,
    knowledge_bases,
    meta,
    sessions,
    video,
)
from app.core.auth import ApiTokenAuthMiddleware
from app.core.config import get_settings
from app.core.errors import error_detail
from app.core.request_log import RequestLogMiddleware
from app.db.session import init_db
from app.rag.parse_queue import start_parse_queue, stop_parse_queue
from app.video.render_queue import start_video_queue, stop_video_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动：建表 → 解析队列 + 视频队列；关闭时停 worker。"""
    init_db()
    n = start_parse_queue(recover=True)
    vn = start_video_queue(recover=True)
    logging.getLogger("docs_agent.startup").info(
        "parse queue ready recovered=%s; video queue recovered=%s",
        n,
        vn,
    )
    try:
        yield
    finally:
        # 等当前成片尽量跑完再退出，避免部署重启时客户端在配音后拿到 nginx 502
        stop_video_queue(wait=True, timeout_sec=150.0)
        stop_parse_queue(wait=False)


app = FastAPI(
    title="docs-agent-server",
    description="文档问答 Agent + AI 短视频创作后端",
    version="0.11.0",
    lifespan=lifespan,
)

# 先加内层鉴权，再加外层请求日志（探活仍由鉴权中间件放行 /health）
app.add_middleware(ApiTokenAuthMiddleware)
app.add_middleware(RequestLogMiddleware)


def _normalize_http_detail(detail: Any) -> dict[str, Any]:
    """把 HTTPException.detail 规范成 {code,message,retryable}。"""
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        body = dict(detail)
        body.setdefault("retryable", False)
        return body
    if isinstance(detail, str):
        return error_detail("HTTP_ERROR", detail, retryable=False)
    return error_detail("HTTP_ERROR", str(detail), retryable=False)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    request_id = getattr(request.state, "request_id", None)
    body = _normalize_http_detail(exc.detail)
    if request_id:
        body["requestId"] = request_id
    return JSONResponse(status_code=exc.status_code, content={"detail": body})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", None)
    body = error_detail(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        retryable=False,
        extra={"errors": exc.errors()},
    )
    if request_id:
        body["requestId"] = request_id
    return JSONResponse(status_code=422, content={"detail": body})


app.include_router(health.router)
app.include_router(debug.router)
app.include_router(meta.router)
app.include_router(knowledge_bases.router)
app.include_router(documents.router)
app.include_router(sessions.router)
app.include_router(feedback.router)
app.include_router(chat.router)
app.include_router(chunks.router)
app.include_router(video.router)

# 本地/演示：输出 MP4 静态目录 → /cdn/video/
_settings = get_settings()
_video_dir = Path(_settings.video_output_dir)
_video_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/cdn/video",
    StaticFiles(directory=str(_video_dir)),
    name="cdn-video",
)


if __name__ == "__main__":
    # IDE 可对本模块使用「Run Python File」；推荐直接运行仓库根目录 run.py
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )

