"""HTTP 请求日志与 X-Request-Id。"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.ids import new_id

logger = logging.getLogger("docs_agent.http")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    为每个 HTTP 请求注入 / 回传 X-Request-Id，并打结构化访问日志。

    说明：业务侧 Agent 的 requestId（req_xxx）仍由 chat/stream 自行生成；
    本中间件的 http_xxx 用于串联网关与接口层排障。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = (
            request.headers.get("x-request-id")
            or request.headers.get("X-Request-Id")
            or new_id("http")
        )
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception:
            logger.exception(
                "http_error method=%s path=%s requestId=%s",
                request.method,
                request.url.path,
                request_id,
            )
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            # 健康检查降噪
            if request.url.path != "/health":
                logger.info(
                    "http method=%s path=%s status=%s requestId=%s durationMs=%s",
                    request.method,
                    request.url.path,
                    status_code,
                    request_id,
                    duration_ms,
                )
