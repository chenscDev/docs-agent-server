"""API Token 鉴权中间件（P2-D4）。

约定：
- 环境变量 API_TOKEN 非空时，业务接口须带 Authorization: Bearer <token>
- /health 始终放行（探活）
- API_TOKEN 为空时不强制鉴权（便于首次起服；验收前请在 .env 配置）
"""

from __future__ import annotations

import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.errors import error_detail

logger = logging.getLogger("docs_agent.auth")

# 探活放行；其余（含 /docs、/debug、/v1/*）均需 Token（若已配置）
_PUBLIC_PATHS = frozenset({"/health"})


def _extract_bearer(authorization: str | None) -> str | None:
    """从 Authorization 头解析 Bearer token；格式非法返回 None。"""
    if not authorization:
        return None
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        return None
    return credential.strip()


class ApiTokenAuthMiddleware(BaseHTTPMiddleware):
    """校验 Bearer Token；未配置 API_TOKEN 时跳过。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        expected = (get_settings().api_token or "").strip()
        if not expected:
            return await call_next(request)

        provided = _extract_bearer(request.headers.get("authorization"))
        if provided is None:
            return _unauthorized(
                request,
                code="AUTH_REQUIRED",
                message="缺少 Authorization: Bearer <token>",
            )
        if not secrets.compare_digest(provided, expected):
            return _unauthorized(
                request,
                code="AUTH_INVALID",
                message="API Token 无效",
            )
        return await call_next(request)


def _unauthorized(request: Request, *, code: str, message: str) -> JSONResponse:
    """返回统一错误结构的 401。"""
    body = error_detail(code, message, retryable=False)
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["requestId"] = request_id
    return JSONResponse(status_code=401, content={"detail": body})
