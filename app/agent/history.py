"""会话历史窗口截断。"""

from __future__ import annotations

from app.db.models import Message

# 与约定对齐
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_CHARS = 3000  # 中文粗估 token ≈ 字符数


def estimate_chars(text: str) -> int:
    return len(text or "")


def select_history_window(messages: list[Message]) -> list[Message]:
    """
    从旧到新的全量 messages 中，取最近窗口。

    规则：最多 MAX_HISTORY_MESSAGES 条，且累计字符 ≤ MAX_HISTORY_CHARS；
    丢最旧、留最近；仅使用 role+content。
    """
    if not messages:
        return []

    selected: list[Message] = []
    total = 0
    for msg in reversed(messages):
        cost = estimate_chars(msg.content)
        if selected and (
            len(selected) >= MAX_HISTORY_MESSAGES or total + cost > MAX_HISTORY_CHARS
        ):
            break
        selected.append(msg)
        total += cost

    selected.reverse()
    return selected


def to_chat_messages(messages: list[Message]) -> list[dict[str, str]]:
    """转为 OpenAI messages（不含 system）。"""
    out: list[dict[str, str]] = []
    for msg in messages:
        if msg.role not in ("user", "assistant"):
            continue
        out.append({"role": msg.role, "content": msg.content})
    return out
