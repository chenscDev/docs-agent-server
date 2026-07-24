"""检索重排：FAISS Top-N 后调用通义 Rerank（失败则本地启发式兜底）。"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.core.config import get_settings
from app.rag.faiss_store import SearchHit

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+")


def rerank_hits(
    query: str,
    hits: list[SearchHit],
    *,
    top_n: int,
) -> tuple[list[SearchHit], bool]:
    """
    对候选 hits 重排，返回 (截断后的列表, 是否实际发生重排)。

    云 API 失败时用关键词重叠启发式，仍算 rerankUsed=True（可观测）。
    候选 ≤1 或关闭开关时原样截断，rerankUsed=False。
    """
    settings = get_settings()
    if not settings.rerank_enabled or not hits:
        return hits[: max(top_n, 0)], False
    if len(hits) == 1:
        return hits[:1], False

    top_n = max(1, min(top_n, len(hits)))
    documents = [h.text for h in hits]

    try:
        order = _dashscope_rerank(query, documents, top_n=top_n)
        if order:
            reranked = [_with_score(hits[i], score) for i, score in order if 0 <= i < len(hits)]
            if reranked:
                logger.info(
                    "rerank_cloud ok query_len=%s candidates=%s top_n=%s",
                    len(query),
                    len(hits),
                    len(reranked),
                )
                return reranked, True
    except Exception as exc:  # noqa: BLE001
        logger.warning("rerank_cloud failed, fallback local: %s", exc)

    local = _local_rerank(query, hits, top_n=top_n)
    logger.info(
        "rerank_local ok query_len=%s candidates=%s top_n=%s",
        len(query),
        len(hits),
        len(local),
    )
    return local, True


def _with_score(hit: SearchHit, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        score=float(score),
        text=hit.text,
        document_title=hit.document_title,
        chunk_index=hit.chunk_index,
    )


def _dashscope_rerank(
    query: str,
    documents: list[str],
    *,
    top_n: int,
) -> list[tuple[int, float]]:
    """调用 DashScope text-rerank，返回 [(原下标, relevance_score), ...]。"""
    settings = get_settings()
    api_key = (settings.llm_api_key or "").strip()
    if not api_key:
        raise RuntimeError("缺少 LLM_API_KEY，无法调用云 Rerank")

    # 截断过长文档，控制费用与超时（2C2G 友好）
    max_chars = max(200, int(settings.rerank_max_doc_chars))
    trimmed = [(d or "")[:max_chars] for d in documents]

    payload: dict[str, Any] = {
        "model": settings.rerank_model,
        "input": {
            "query": query,
            "documents": trimmed,
        },
        "parameters": {
            "return_documents": False,
            "top_n": top_n,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.rerank_timeout_sec)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(settings.rerank_api_url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    # 兼容 output.results / results
    results = None
    if isinstance(data, dict):
        output = data.get("output")
        if isinstance(output, dict):
            results = output.get("results")
        if results is None:
            results = data.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError(f"rerank 响应无 results: {str(data)[:200]}")

    ordered: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if idx is None:
            continue
        score = item.get("relevance_score")
        if score is None:
            score = item.get("score")
        try:
            ordered.append((int(idx), float(score if score is not None else 0.0)))
        except (TypeError, ValueError):
            continue
    return ordered


def _tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def _local_rerank(query: str, hits: list[SearchHit], *, top_n: int) -> list[SearchHit]:
    """无云 API 时的轻量重排：FAISS 分 + 词重叠。"""
    q_tokens = _tokenize(query)
    scored: list[tuple[float, SearchHit]] = []
    for h in hits:
        overlap = 0.0
        if q_tokens:
            d_tokens = _tokenize(h.text)
            if d_tokens:
                overlap = len(q_tokens & d_tokens) / max(len(q_tokens), 1)
        # FAISS 已是相似度，约在 [-1,1] 或 [0,1]；与 overlap 加权
        combined = float(h.score) * 0.7 + overlap * 0.3
        scored.append((combined, h))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [_with_score(h, s) for s, h in scored[:top_n]]
