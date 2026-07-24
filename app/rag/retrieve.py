"""统一检索：FAISS 召回 + 可选 Rerank。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.rag.faiss_store import SearchHit, search_kb
from app.rag.rerank import rerank_hits


def retrieve_for_query(
    db: Session,
    kb_id: str,
    query: str,
    *,
    top_k: int = 5,
) -> tuple[list[SearchHit], bool]:
    """
    返回 (hits, rerank_used)。

    开启 rerank 时先拉 candidate_k，再压到 top_k。
    """
    settings = get_settings()
    top_k = max(1, top_k)
    if settings.rerank_enabled:
        candidate_k = max(top_k, int(settings.rerank_candidate_k))
        raw = search_kb(db, kb_id, query, top_k=candidate_k)
        return rerank_hits(query, raw, top_n=top_k)
    return search_kb(db, kb_id, query, top_k=top_k), False
