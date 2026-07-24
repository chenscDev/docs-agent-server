"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import chat, chunks, debug, documents, health, knowledge_bases, meta, sessions
from app.core.auth import ApiTokenAuthMiddleware
from app.core.errors import error_detail
from app.core.request_log import RequestLogMiddleware
from app.db.session import init_db
from app.rag.parse_queue import start_parse_queue, stop_parse_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动：建表 → 解析队列 + 恢复未完成任务；关闭时停 worker。"""
    init_db()
    n = start_parse_queue(recover=True)
    logging.getLogger("docs_agent.startup").info(
        "parse queue ready, recovered=%s",
        n,
    )
    try:
        yield
    finally:
        stop_parse_queue(wait=False)


app = FastAPI(
    title="docs-agent-server",
    description="文档问答 Agent 后端（P2：多知识库 / Token / 解析队列）",
    version="0.9.3",
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
app.include_router(chat.router)
app.include_router(chunks.router)


if __name__ == "__main__":
    # IDE 可对本模块使用「Run Python File」；推荐直接运行仓库根目录 run.py
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )

