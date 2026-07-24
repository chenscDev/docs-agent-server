"""检索 query 改写（二期：空命中后二次检索）。"""

from __future__ import annotations

import logging
import re

from app.core.llm import LLMClient

logger = logging.getLogger(__name__)

_REWRITE_SYSTEM = (
    "你是向量检索查询改写器。根据用户问题和首次失败的检索词，"
    "输出一条更适合在制度/手册类文档中检索的中文短查询。"
    "要求：保留专有名词与制度关键词；去掉口语与语气词；不要解释；只输出一行查询文本。"
)


def rewrite_search_query(
    client: LLMClient,
    *,
    user_text: str,
    failed_query: str,
) -> str | None:
    """
    改写检索 query。

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
        raw = client.chat(messages).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rewrite_query failed: %s", exc)
        return None

    # 只取第一行，去掉引号
    line = raw.splitlines()[0].strip().strip("「」\"'")
    line = re.sub(r"^(改写后的?查询|query)\s*[:：]\s*", "", line, flags=re.I)
    if not line or len(line) > 200:
        return None
    if failed_query and line == failed_query:
        return None
    return line
