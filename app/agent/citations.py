"""从助手回答中解析 [n] 并映射为 citations。"""

from __future__ import annotations

import re
from typing import Any

_CITE_RE = re.compile(r"\[(\d+)\]")


def _hit_to_citation(n: int, h: dict[str, Any]) -> dict[str, Any]:
    text = h.get("text") or ""
    snippet = text[:120] + ("…" if len(text) > 120 else "")
    chunk_id = h.get("chunk_id") or h.get("chunkId")
    return {
        "index": n,
        "documentId": h.get("document_id") or h.get("documentId"),
        "documentTitle": h.get("document_title") or h.get("documentTitle"),
        "chunkId": chunk_id,
        "snippet": snippet,
        "score": h.get("score"),
    }


def build_citations(answer: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    根据答案中的 [n] 与 hits（含 index）生成 citations。

    无效序号丢弃；snippet 截断约 120 字。
    若正文未写 [n] 但有检索命中：回退附上前 3 条，保证端上可点开原文。
    """
    if not hits:
        return []

    by_index = {int(h["index"]): h for h in hits if "index" in h}
    used: list[int] = []
    if answer:
        for match in _CITE_RE.finditer(answer):
            n = int(match.group(1))
            if n in by_index and n not in used:
                used.append(n)

    if not used:
        # 模型偶发不写 [n]：仍返回命中，方便演示引用跳转
        used = [int(h["index"]) for h in hits[:3] if "index" in h]

    citations: list[dict[str, Any]] = []
    for n in used:
        h = by_index.get(n)
        if not h:
            continue
        citations.append(_hit_to_citation(n, h))
    return citations
