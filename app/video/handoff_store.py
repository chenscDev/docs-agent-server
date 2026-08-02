"""一键出片跨分包交接：内存暂存，短 TTL，供首页 Tab 拉取。"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.core.ids import new_id

_LOCK = threading.Lock()
# id → payload；另按 token 记最新一条，方便 Tab 切换后无 id 拉取
_BY_ID: dict[str, dict[str, Any]] = {}
_BY_TOKEN: dict[str, str] = {}
_TTL_SEC = 15 * 60


def _purge_locked(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    expired = [
        hid
        for hid, row in _BY_ID.items()
        if float(row.get("expiresAt") or 0) <= ts
    ]
    for hid in expired:
        _BY_ID.pop(hid, None)
    dead_tokens = [tok for tok, hid in _BY_TOKEN.items() if hid not in _BY_ID]
    for tok in dead_tokens:
        _BY_TOKEN.pop(tok, None)


def put_handoff(
    *,
    prompt: str,
    knowledge_base_id: str | None = None,
    source: str = "docs-agent",
    token_key: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    scene_hint: str | None = None,
    generation_type: str | None = None,
) -> dict[str, Any]:
    """写入交接草稿，返回公开字段。"""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt 不能为空")
    hid = new_id("vh")
    now = time.time()
    title_v = (title or "").strip()[:80] or None
    summary_v = (summary or "").strip()[:280] or None
    scene_v = (scene_hint or "").strip()[:80] or None
    gen_v = (generation_type or "visual-cut").strip()[:40] or "visual-cut"
    row = {
        "id": hid,
        "prompt": text[:2000],
        "knowledgeBaseId": (knowledge_base_id or "").strip() or None,
        "source": (source or "docs-agent").strip() or "docs-agent",
        "title": title_v,
        "summary": summary_v,
        "sceneHint": scene_v,
        "generationType": gen_v,
        "createdAt": now,
        "expiresAt": now + _TTL_SEC,
    }
    with _LOCK:
        _purge_locked(now)
        _BY_ID[hid] = row
        key = (token_key or "").strip() or "_anon"
        _BY_TOKEN[key] = hid
    return {
        "id": hid,
        "prompt": row["prompt"],
        "knowledgeBaseId": row["knowledgeBaseId"],
        "source": row["source"],
        "title": row["title"],
        "summary": row["summary"],
        "sceneHint": row["sceneHint"],
        "generationType": row["generationType"],
        "expiresInSec": _TTL_SEC,
    }


def consume_handoff(
    *,
    handoff_id: str | None = None,
    token_key: str | None = None,
) -> dict[str, Any] | None:
    """读取并消费交接（一次性）。优先 id，否则取该 token 最新一条。"""
    with _LOCK:
        _purge_locked()
        hid = (handoff_id or "").strip()
        if not hid:
            key = (token_key or "").strip() or "_anon"
            hid = _BY_TOKEN.get(key) or ""
        if not hid:
            return None
        row = _BY_ID.pop(hid, None)
        # 清理 token 指向
        for tok, cur in list(_BY_TOKEN.items()):
            if cur == hid:
                _BY_TOKEN.pop(tok, None)
        if row is None:
            return None
        return {
            "id": row["id"],
            "prompt": row["prompt"],
            "knowledgeBaseId": row.get("knowledgeBaseId"),
            "source": row.get("source") or "docs-agent",
            "title": row.get("title"),
            "summary": row.get("summary"),
            "sceneHint": row.get("sceneHint"),
            "generationType": row.get("generationType") or "visual-cut",
        }
