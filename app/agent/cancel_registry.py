"""进行中的生成取消标记（进程内，按 requestId / sessionId）。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# requestId -> Event（set 表示已请求取消）
_flags: dict[str, threading.Event] = {}
# sessionId -> 当前活跃 requestId
_session_active: dict[str, str] = {}
# requestId -> 打断回调（关闭进行中的 HTTP 连接等）
_aborts: dict[str, Callable[[], None]] = {}


class GenerationCancelled(Exception):
    """生成被用户取消。"""


def register(request_id: str, session_id: str) -> threading.Event:
    """登记一次流式生成；同会话新请求会覆盖活跃映射。"""
    ev = threading.Event()
    with _lock:
        _flags[request_id] = ev
        _session_active[session_id] = request_id
    return ev


def bind_abort(request_id: str, abort_fn: Callable[[], None]) -> None:
    """
    绑定当前 LLM HTTP 的打断函数（通常为 httpx.Client.close）。

    取消时会先 set Event，再调用本回调以尽快掐断阻塞中的厂商请求。
    """
    with _lock:
        _aborts[request_id] = abort_fn


def unbind_abort(request_id: str) -> None:
    """LLM 调用结束后解除打断绑定，避免误关下一轮连接。"""
    with _lock:
        _aborts.pop(request_id, None)


def request_cancel(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
) -> tuple[bool, Optional[str]]:
    """
    标记取消，并尝试打断进行中的 LLM HTTP。

    返回 (是否找到活跃任务, 实际 requestId)。
    """
    abort_fn: Callable[[], None] | None = None
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
        abort_fn = _aborts.get(rid)

    if abort_fn is not None:
        try:
            abort_fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel abort failed requestId=%s: %s", rid, exc)
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
        _aborts.pop(request_id, None)
        if session_id and _session_active.get(session_id) == request_id:
            _session_active.pop(session_id, None)
