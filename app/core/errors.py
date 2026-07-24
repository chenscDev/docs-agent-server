"""统一 API 错误结构：{ code, message, retryable }。"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException


def error_detail(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造标准 detail 对象。"""
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if extra:
        body.update(extra)
    return body


def raise_api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    extra: dict[str, Any] | None = None,
) -> NoReturn:
    """抛出带标准 detail 的 HTTPException。"""
    raise HTTPException(
        status_code=status_code,
        detail=error_detail(code, message, retryable=retryable, extra=extra),
    )


# 常见业务错误码（面试可讲：统一契约，端上按 code 分支）
# AUTH: AUTH_REQUIRED / AUTH_INVALID
# UPLOAD: UNSUPPORTED_TYPE / TOO_LARGE / PARSE_EMPTY / PDF_ENCRYPTED / PDF_TOO_MANY_PAGES / PARSE_BINARY
# DOC: DOC_NOT_FOUND / KB_NOT_FOUND / NO_READY_DOC
# CHAT: SESSION_NOT_FOUND / AGENT_FAILED / LLM_ERROR
# CHUNK: CHUNK_GONE
# HTTP: VALIDATION_ERROR
