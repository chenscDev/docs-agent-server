"""视频任务 SSE envelope。"""

from __future__ import annotations

import json
import time
from typing import Any


def make_video_event(
    *,
    job_id: str,
    stream_id: str,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "v": 1,
        "jobId": job_id,
        "streamId": stream_id,
        "seq": seq,
        "ts": int(time.time() * 1000),
        "type": event_type,
        "payload": payload,
    }


def format_sse(event: dict[str, Any]) -> str:
    event_type = event.get("type") or "message"
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"
