"""按知识库隔离的 FAISS 索引（正文仍在 SQLite chunks）。

P3-D11～D12：默认增量增删（IndexIDMap2）；失败或关闭开关时全量 rebuild 兜底。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Chunk, Document
from app.rag.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    """检索命中。"""

    chunk_id: str
    document_id: str
    score: float
    text: str
    document_title: str
    chunk_index: int


def _faiss_dir() -> Path:
    settings = get_settings()
    root = Path(settings.faiss_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path(kb_id: str) -> Path:
    return _faiss_dir() / f"{kb_id}.index"


def _map_path(kb_id: str) -> Path:
    return _faiss_dir() / f"{kb_id}.id_map.json"


def drop_kb_index(kb_id: str) -> None:
    """删除某知识库的 FAISS 落盘文件（删库时用）。"""
    for path in (_index_path(kb_id), _map_path(kb_id)):
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("删除 FAISS 文件失败 %s: %s", path, exc)
    logger.info("faiss dropped kb=%s", kb_id)


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """行向量 L2 归一化，配合 Inner Product ≈ 余弦相似度。"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return matrix / norms


def _is_id_map(index: Any) -> bool:
    return isinstance(index, (faiss.IndexIDMap, faiss.IndexIDMap2))


def _new_id_map_index(dim: int) -> faiss.IndexIDMap2:
    return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))


def _save_index(kb_id: str, index: Any, id_map: list[dict[str, Any]]) -> None:
    index_file = _index_path(kb_id)
    map_file = _map_path(kb_id)
    if not id_map or index.ntotal == 0:
        for path in (index_file, map_file):
            if path.is_file():
                path.unlink(missing_ok=True)
        return
    faiss.write_index(index, str(index_file))
    map_file.write_text(
        json.dumps(id_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_index_and_map(
    kb_id: str,
) -> tuple[Any | None, list[dict[str, Any]]]:
    """加载索引与 id_map；旧版 IndexFlatIP 自动升级为 IndexIDMap2。"""
    index_file = _index_path(kb_id)
    map_file = _map_path(kb_id)
    if not index_file.is_file() or not map_file.is_file():
        return None, []

    id_map: list[dict[str, Any]] = json.loads(map_file.read_text(encoding="utf-8"))
    index = faiss.read_index(str(index_file))
    if _is_id_map(index):
        return index, id_map

    # 兼容旧 FlatIP：按 faissId 顺序 reconstruct 后写入 IDMap
    n = int(index.ntotal)
    if n <= 0 or not id_map:
        return None, []
    dim = int(index.d)
    vecs = np.vstack([index.reconstruct(i) for i in range(n)]).astype(np.float32)
    ids = np.asarray([int(m["faissId"]) for m in id_map[:n]], dtype=np.int64)
    if len(ids) != n:
        # map 与向量不一致 → 交给调用方全量 rebuild
        logger.warning(
            "faiss map/index size mismatch kb=%s map=%s ntotal=%s",
            kb_id,
            len(id_map),
            n,
        )
        return None, []
    upgraded = _new_id_map_index(dim)
    upgraded.add_with_ids(vecs, ids)
    _save_index(kb_id, upgraded, id_map[:n])
    logger.info("faiss upgraded FlatIP→IDMap2 kb=%s vectors=%s", kb_id, n)
    return upgraded, id_map[:n]


def _count_ready_chunks(db: Session, kb_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.knowledge_base_id == kb_id, Document.status == "ready")
        )
        or 0
    )


def rebuild_kb_index(
    db: Session,
    kb_id: str,
    *,
    extra_document_id: str | None = None,
) -> int:
    """
    全量重建某知识库的 FAISS 索引（IndexIDMap2）。

    纳入：status=ready 的文档 chunks；以及 extra_document_id（即将 ready 的当前文档）。
    返回写入的向量条数；0 表示空索引（删除落盘文件）。
    """
    t0 = time.perf_counter()
    status_filter = Document.status == "ready"
    if extra_document_id:
        status_filter = or_(Document.status == "ready", Document.id == extra_document_id)

    stmt = (
        select(Chunk, Document.title)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.knowledge_base_id == kb_id)
        .where(status_filter)
        .order_by(Chunk.document_id.asc(), Chunk.chunk_index.asc())
    )
    rows = db.execute(stmt).all()

    if not rows:
        drop_kb_index(kb_id)
        logger.info("faiss rebuild empty kb=%s ms=%s", kb_id, int((time.perf_counter() - t0) * 1000))
        return 0

    texts = [chunk.content for chunk, _title in rows]
    embedder = EmbeddingClient()
    vectors = embedder.embed_texts(texts)
    if len(vectors) != len(rows):
        raise RuntimeError("Embedding 数量与 chunk 数量不一致")

    matrix = np.asarray(vectors, dtype=np.float32)
    matrix = _l2_normalize(matrix)
    dim = matrix.shape[1]
    ids = np.arange(len(rows), dtype=np.int64)

    index = _new_id_map_index(dim)
    index.add_with_ids(matrix, ids)

    id_map: list[dict[str, Any]] = []
    for faiss_id, (chunk, title) in enumerate(rows):
        id_map.append(
            {
                "faissId": int(faiss_id),
                "chunkId": chunk.id,
                "documentId": chunk.document_id,
                "documentTitle": title,
                "chunkIndex": chunk.chunk_index,
            }
        )

    _save_index(kb_id, index, id_map)
    ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "faiss rebuild kb=%s vectors=%s dim=%s ms=%s",
        kb_id,
        len(id_map),
        dim,
        ms,
    )
    return len(id_map)


def add_document_to_index(db: Session, kb_id: str, document_id: str) -> int:
    """
    增量写入某文档的全部 chunk 向量。

    若同文档已有向量会先删再加。失败抛异常，由调用方 rebuild。
    返回本次写入条数。
    """
    settings = get_settings()
    if not settings.faiss_incremental:
        return rebuild_kb_index(db, kb_id, extra_document_id=document_id)

    t0 = time.perf_counter()
    ready_chunks = _count_ready_chunks(db, kb_id)

    doc = db.get(Document, document_id)
    if doc is None:
        raise RuntimeError(f"文档不存在: {document_id}")

    stmt = (
        select(Chunk)
        .where(Chunk.document_id == document_id, Chunk.knowledge_base_id == kb_id)
        .order_by(Chunk.chunk_index.asc())
    )
    chunks = list(db.scalars(stmt).all())
    if not chunks:
        # 无片段：至少去掉旧向量
        remove_document_from_index(kb_id, document_id)
        return 0

    # 先移除同文档旧向量（reparse / 重复索引）
    remove_document_from_index(kb_id, document_id)

    index, id_map = _load_index_and_map(kb_id)
    texts = [c.content for c in chunks]
    embedder = EmbeddingClient()
    vectors = embedder.embed_texts(texts)
    matrix = _l2_normalize(np.asarray(vectors, dtype=np.float32))
    dim = int(matrix.shape[1])

    if index is None:
        index = _new_id_map_index(dim)
        id_map = []
    elif int(index.d) != dim:
        raise RuntimeError(f"向量维度不一致 index.d={index.d} new={dim}，请全量 rebuild")

    next_id = (max((int(m["faissId"]) for m in id_map), default=-1) + 1) if id_map else 0
    new_ids = np.arange(next_id, next_id + len(chunks), dtype=np.int64)
    index.add_with_ids(matrix, new_ids)

    title = doc.title
    for i, chunk in enumerate(chunks):
        id_map.append(
            {
                "faissId": int(new_ids[i]),
                "chunkId": chunk.id,
                "documentId": chunk.document_id,
                "documentTitle": title,
                "chunkIndex": chunk.chunk_index,
            }
        )

    _save_index(kb_id, index, id_map)
    ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "faiss incremental_add kb=%s doc=%s added=%s total=%s ms=%s "
        "(ready_chunks≈%s full_rebuild会重嵌全部)",
        kb_id,
        document_id,
        len(chunks),
        len(id_map),
        ms,
        ready_chunks,
    )
    return len(chunks)


def remove_document_from_index(kb_id: str, document_id: str) -> int:
    """
    增量删除某文档在索引中的向量。

    返回删除条数；索引不存在视为 0。异常时抛出，由调用方 rebuild。
    """
    settings = get_settings()
    t0 = time.perf_counter()

    index, id_map = _load_index_and_map(kb_id)
    if index is None or not id_map:
        return 0

    to_remove = [m for m in id_map if m.get("documentId") == document_id]
    if not to_remove:
        return 0

    if not settings.faiss_incremental:
        # 关闭增量时不做半截删除，交给全量
        raise RuntimeError("FAISS_INCREMENTAL=false，请使用 rebuild_kb_index")

    ids = np.asarray([int(m["faissId"]) for m in to_remove], dtype=np.int64)
    index.remove_ids(ids)
    remain = [m for m in id_map if m.get("documentId") != document_id]
    _save_index(kb_id, index, remain)
    ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "faiss incremental_remove kb=%s doc=%s removed=%s remain=%s ms=%s",
        kb_id,
        document_id,
        len(to_remove),
        len(remain),
        ms,
    )
    return len(to_remove)


def upsert_document_index(db: Session, kb_id: str, document_id: str) -> int:
    """
    解析成功后写入索引：优先增量，失败则全量 rebuild。

    返回索引中与该写入相关的向量数（增量=本文档条数；rebuild=全库条数）。
    """
    settings = get_settings()
    if not settings.faiss_incremental:
        return rebuild_kb_index(db, kb_id, extra_document_id=document_id)

    try:
        n = add_document_to_index(db, kb_id, document_id)
        if n <= 0:
            # 空文档不应发生；兜底 rebuild 确认一致性
            return rebuild_kb_index(db, kb_id, extra_document_id=document_id)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "faiss incremental_add failed, fallback rebuild kb=%s doc=%s err=%s",
            kb_id,
            document_id,
            exc,
        )
        return rebuild_kb_index(db, kb_id, extra_document_id=document_id)


def delete_document_index(db: Session, kb_id: str, document_id: str) -> None:
    """删文档后更新索引：优先增量删除，失败则全量 rebuild。"""
    settings = get_settings()
    if not settings.faiss_incremental:
        rebuild_kb_index(db, kb_id)
        return
    try:
        remove_document_from_index(kb_id, document_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "faiss incremental_remove failed, fallback rebuild kb=%s doc=%s err=%s",
            kb_id,
            document_id,
            exc,
        )
        rebuild_kb_index(db, kb_id)


def search_kb(
    db: Session,
    kb_id: str,
    query: str,
    *,
    top_k: int = 5,
) -> list[SearchHit]:
    """在指定知识库检索，回表取 chunk 正文。"""
    index, id_map = _load_index_and_map(kb_id)
    if index is None or not id_map:
        return []

    by_id = {int(m["faissId"]): m for m in id_map}
    embedder = EmbeddingClient()
    q_vec = np.asarray(embedder.embed_texts([query]), dtype=np.float32)
    q_vec = _l2_normalize(q_vec)

    k = min(max(top_k, 1), len(id_map))
    scores, indices = index.search(q_vec, k)

    hits: list[SearchHit] = []
    for score, faiss_id in zip(scores[0].tolist(), indices[0].tolist(), strict=True):
        if faiss_id < 0:
            continue
        meta = by_id.get(int(faiss_id))
        if meta is None:
            continue
        chunk = db.get(Chunk, meta["chunkId"])
        if chunk is None:
            continue
        hits.append(
            SearchHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                score=float(score),
                text=chunk.content,
                document_title=meta.get("documentTitle") or "",
                chunk_index=int(meta.get("chunkIndex") or chunk.chunk_index),
            )
        )
    return hits
