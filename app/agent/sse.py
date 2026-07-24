"""SSE 事件封装。"""

from __future__ import annotations

import json
import time
from typing import Any


def make_event(
    *,
    request_id: str,
    session_id: str,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """统一 envelope。"""
    return {
        "v": 1,
        "requestId": request_id,
        "sessionId": session_id,
        "seq": seq,
        "ts": int(time.time() * 1000),
        "type": event_type,
        "payload": payload,
    }


def format_sse(event: dict[str, Any]) -> str:
    """编码为 SSE 文本。"""
    event_type = event.get("type") or "message"
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"
