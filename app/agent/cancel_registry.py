"""进行中的生成取消标记（进程内，按 requestId / sessionId）。"""

from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()
# requestId -> Event（set 表示已请求取消）
_flags: dict[str, threading.Event] = {}
# sessionId -> 当前活跃 requestId
_session_active: dict[str, str] = {}


class GenerationCancelled(Exception):
    """生成被用户取消。"""


def register(request_id: str, session_id: str) -> threading.Event:
    """登记一次流式生成；同会话新请求会覆盖活跃映射。"""
    ev = threading.Event()
    with _lock:
        _flags[request_id] = ev
        _session_active[session_id] = request_id
    return ev


def request_cancel(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
) -> tuple[bool, Optional[str]]:
    """
    标记取消。

    返回 (是否找到活跃任务, 实际 requestId)。
    """
    with _lock:
        rid = request_id
        if not rid and session_id:
            rid = _session_active.get(session_id)
        if not rid:
            return False, None
        ev = _flags.get(rid)
        if ev is None:
            return False, rid
        ev.set()
        return True, rid


def is_cancelled(request_id: str) -> bool:
    """查询是否已取消。"""
    with _lock:
        ev = _flags.get(request_id)
        return bool(ev is not None and ev.is_set())


def unregister(request_id: str, session_id: str | None = None) -> None:
    """生成结束时清理，避免泄漏。"""
    with _lock:
        _flags.pop(request_id, None)
        if session_id and _session_active.get(session_id) == request_id:
            _session_active.pop(session_id, None)
