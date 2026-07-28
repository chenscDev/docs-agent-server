"""API Token 鉴权中间件（P2-D4 / P3-D13 多 Token）。

约定：
- API_TOKEN / API_TOKENS 解析出至少一个有效 Token 时，业务接口须 Bearer
- API_TOKENS_REVOKED 与 data/api_tokens_revoked.local 中的 Token 不可用
- /health 始终放行
- 无有效 Token 时不强制鉴权（便于首次起服）
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.errors import error_detail
from app.core.tokens import auth_enabled, is_api_token_valid

logger = logging.getLogger("docs_agent.auth")

# 探活放行；其余（含 /docs、/debug、/v1/*）均需 Token（若已配置）
_PUBLIC_PATHS = frozenset({"/health"})
# 成片 / 封面 / 分镜缩略图：系统播放器与 <Image> 无法带 Bearer
# HTML 播放页同样免鉴权，便于 App Linking 打开
_PUBLIC_PREFIXES = ("/cdn/video", "/v1/video/player", "/v1/video/preview")


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path == p or path.startswith(f"{p}/") for p in _PUBLIC_PREFIXES)


def _extract_bearer(authorization: str | None) -> str | None:
    """从 Authorization 头解析 Bearer token；格式非法返回 None。"""
    if not authorization:
        return None
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        return None
    return credential.strip()


class ApiTokenAuthMiddleware(BaseHTTPMiddleware):
    """校验 Bearer Token；未配置任何有效 Token 时跳过。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        if not auth_enabled():
            return await call_next(request)

        provided = _extract_bearer(request.headers.get("authorization"))
        if provided is None:
            return _unauthorized(
                request,
                code="AUTH_REQUIRED",
                message="缺少 Authorization: Bearer <token>",
            )
        if not is_api_token_valid(provided):
            return _unauthorized(
                request,
                code="AUTH_INVALID",
                message="API Token 无效或已作废",
            )
        return await call_next(request)


def _unauthorized(request: Request, *, code: str, message: str) -> JSONResponse:
    """返回统一错误结构的 401。"""
    body = error_detail(code, message, retryable=False)
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["requestId"] = request_id
    return JSONResponse(status_code=401, content={"detail": body})
