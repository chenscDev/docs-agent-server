#!/usr/bin/env python3
"""P3-D15：取消绑定打断回调的单元自检（不依赖真实 LLM）。"""

from __future__ import annotations

import threading
import time

from app.agent.cancel_registry import (
    GenerationCancelled,
    bind_abort,
    is_cancelled,
    register,
    request_cancel,
    unbind_abort,
    unregister,
)


def test_cancel_invokes_abort() -> None:
    rid = "req_test_abort"
    sid = "ses_test_abort"
    register(rid, sid)
    hit = threading.Event()

    def abort() -> None:
        hit.set()

    bind_abort(rid, abort)
    found, got = request_cancel(request_id=rid)
    assert found and got == rid
    assert is_cancelled(rid)
    assert hit.wait(timeout=1.0), "abort 回调未被调用"
    unbind_abort(rid)
    unregister(rid, sid)
    print("ok cancel_invokes_abort")


def test_session_cancel() -> None:
    rid = "req_test_ses"
    sid = "ses_test_ses"
    register(rid, sid)
    hit = threading.Event()
    bind_abort(rid, hit.set)
    found, got = request_cancel(session_id=sid)
    assert found and got == rid
    assert hit.is_set()
    unregister(rid, sid)
    print("ok session_cancel")


def test_llm_reraise_pattern() -> None:
    """模拟 close 后业务层把异常转成 GenerationCancelled。"""
    rid = "req_test_reraise"
    sid = "ses_test_reraise"
    register(rid, sid)
    closed = threading.Event()

    def abort() -> None:
        closed.set()

    bind_abort(rid, abort)

    def worker() -> None:
        time.sleep(0.05)
        request_cancel(request_id=rid)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    # 等待取消
    deadline = time.time() + 2
    while time.time() < deadline and not is_cancelled(rid):
        time.sleep(0.01)
    assert is_cancelled(rid)
    assert closed.is_set()
    try:
        if is_cancelled(rid):
            raise GenerationCancelled()
        raise AssertionError("should have cancelled")
    except GenerationCancelled:
        pass
    finally:
        unregister(rid, sid)
    print("ok llm_reraise_pattern")


if __name__ == "__main__":
    test_cancel_invokes_abort()
    test_session_cancel()
    test_llm_reraise_pattern()
    print("all passed")
