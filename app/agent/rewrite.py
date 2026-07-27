"""检索 query 改写：空命中二次检索（P2）+ 多轮指代改写（P3-D7）。"""

from __future__ import annotations

import logging
import re

from app.agent.cancel_registry import GenerationCancelled
from app.core.llm import LLMClient
from app.db.models import Message

logger = logging.getLogger(__name__)

_REWRITE_SYSTEM = (
    "你是向量检索查询改写器。根据用户问题和首次失败的检索词，"
    "输出一条更适合在制度/手册类文档中检索的中文短查询。"
    "要求：保留专有名词与制度关键词；去掉口语与语气词；不要解释；只输出一行查询文本。"
)

_FOLLOWUP_SYSTEM = (
    "你是检索查询改写器。用户在多轮对话中追问，问题可能含代词或省略主语。"
    "请结合最近对话，输出一条可独立用于向量检索的中文短查询："
    "自包含、无「那个/这个/它」等悬空指代；保留专有名词与制度关键词；不要解释；只输出一行。"
)

# 常见追问信号（短句或代词/承接）
_FOLLOWUP_MARKERS = re.compile(
    r"(那个|这个|这些|那些|它|他|她|其|上述|刚才|上面|还有|呢\s*$|吗\s*$|"
    r"多少|怎么办|怎么算|上限|下限|呢？|吗？)"
)


def looks_like_followup(user_text: str, *, has_history: bool) -> bool:
    """粗判是否像依赖上文的追问。"""
    if not has_history:
        return False
    text = (user_text or "").strip()
    if not text:
        return False
    if _FOLLOWUP_MARKERS.search(text):
        return True
    # 极短追问几乎总依赖上文
    if len(text) <= 12:
        return True
    # 中短句且以呢/吗/问号收尾
    if len(text) <= 20 and re.search(r"[呢吗？?]\s*$", text):
        return True
    return False


def _clean_query_line(raw: str, *, forbid_equal: str | None = None) -> str | None:
    """清洗模型输出的单行 query。"""
    line = (raw or "").strip()
    if not line:
        return None
    line = line.splitlines()[0].strip().strip("「」\"'")
    line = re.sub(r"^(改写后的?查询|query)\s*[:：]\s*", "", line, flags=re.I)
    if not line or len(line) > 200:
        return None
    if forbid_equal and line == forbid_equal.strip():
        return None
    return line


def rewrite_search_query(
    client: LLMClient,
    *,
    user_text: str,
    failed_query: str,
    request_id: str | None = None,
) -> str | None:
    """
    空命中后改写检索 query（reason=empty_recall）。

    失败或无有效改写时返回 None。
    """
    user_text = (user_text or "").strip()
    failed_query = (failed_query or "").strip()
    if not user_text and not failed_query:
        return None

    messages = [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"用户问题：{user_text or '(无)'}\n"
                f"首次检索 query：{failed_query or '(无)'}\n"
                "请输出改写后的检索 query："
            ),
        },
    ]
    try:
        raw = client.chat(messages, request_id=request_id).strip()
    except GenerationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("rewrite_query failed: %s", exc)
        return None

    return _clean_query_line(raw, forbid_equal=failed_query or None)


def rewrite_followup_query(
    client: LLMClient,
    *,
    user_text: str,
    history: list[Message],
    request_id: str | None = None,
) -> str | None:
    """
    多轮指代改写：结合历史把追问改成独立检索句（reason=followup）。

    无历史或不像追问时返回 None。
    """
    user_text = (user_text or "").strip()
    if not user_text:
        return None
    if not looks_like_followup(user_text, has_history=bool(history)):
        return None

    # 取最近若干轮，控制成本
    recent = history[-6:] if len(history) > 6 else history
    lines: list[str] = []
    for msg in recent:
        if msg.role not in ("user", "assistant"):
            continue
        role = "用户" if msg.role == "user" else "助手"
        content = (msg.content or "").strip()
        if not content:
            continue
        # 截断过长助手回复，保留主题线索
        if len(content) > 280:
            content = content[:280] + "…"
        lines.append(f"{role}：{content}")

    if not lines:
        return None

    messages = [
        {"role": "system", "content": _FOLLOWUP_SYSTEM},
        {
            "role": "user",
            "content": (
                "最近对话：\n"
                + "\n".join(lines)
                + f"\n\n当前追问：{user_text}\n请输出独立检索 query："
            ),
        },
    ]
    try:
        raw = client.chat(messages, request_id=request_id).strip()
    except GenerationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("followup_rewrite failed: %s", exc)
        return None

    cleaned = _clean_query_line(raw, forbid_equal=user_text)
    if cleaned:
        logger.info(
            "followup_rewrite from=%r to=%r",
            user_text[:80],
            cleaned[:80],
        )
    return cleaned
