"""按知识库隔离的 FAISS 索引（正文仍在 SQLite chunks）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np
from sqlalchemy import or_, select
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


def rebuild_kb_index(
    db: Session,
    kb_id: str,
    *,
    extra_document_id: str | None = None,
) -> int:
    """
    重建某知识库的 FAISS 索引。

    纳入：status=ready 的文档 chunks；以及 extra_document_id（即将 ready 的当前文档）。
    返回写入的向量条数；0 表示空索引（删除落盘文件）。
    """
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

    index_file = _index_path(kb_id)
    map_file = _map_path(kb_id)

    if not rows:
        for path in (index_file, map_file):
            if path.is_file():
                path.unlink()
        logger.info("faiss rebuild empty kb=%s", kb_id)
        return 0

    texts = [chunk.content for chunk, _title in rows]
    embedder = EmbeddingClient()
    vectors = embedder.embed_texts(texts)
    if len(vectors) != len(rows):
        raise RuntimeError("Embedding 数量与 chunk 数量不一致")

    matrix = np.asarray(vectors, dtype=np.float32)
    matrix = _l2_normalize(matrix)
    dim = matrix.shape[1]

    index = faiss.IndexFlatIP(dim)
    index.add(matrix)

    id_map = []
    for faiss_id, (chunk, title) in enumerate(rows):
        id_map.append(
            {
                "faissId": faiss_id,
                "chunkId": chunk.id,
                "documentId": chunk.document_id,
                "documentTitle": title,
                "chunkIndex": chunk.chunk_index,
            }
        )

    faiss.write_index(index, str(index_file))
    map_file.write_text(
        json.dumps(id_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("faiss rebuild kb=%s vectors=%s dim=%s", kb_id, len(id_map), dim)
    return len(id_map)


def search_kb(
    db: Session,
    kb_id: str,
    query: str,
    *,
    top_k: int = 5,
) -> list[SearchHit]:
    """在指定知识库检索，回表取 chunk 正文。"""
    index_file = _index_path(kb_id)
    map_file = _map_path(kb_id)
    if not index_file.is_file() or not map_file.is_file():
        return []

    id_map = json.loads(map_file.read_text(encoding="utf-8"))
    if not id_map:
        return []

    index = faiss.read_index(str(index_file))
    embedder = EmbeddingClient()
    q_vec = np.asarray(embedder.embed_texts([query]), dtype=np.float32)
    q_vec = _l2_normalize(q_vec)

    k = min(max(top_k, 1), len(id_map))
    scores, indices = index.search(q_vec, k)

    hits: list[SearchHit] = []
    for score, faiss_id in zip(scores[0].tolist(), indices[0].tolist(), strict=True):
        if faiss_id < 0:
            continue
        meta = next((m for m in id_map if m["faissId"] == faiss_id), None)
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
