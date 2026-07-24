"""Agent 工具定义与执行。"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Document
from app.rag.retrieve import retrieve_for_query

# OpenAI 兼容 tools schema
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": (
                "在当前会话绑定的知识库中检索与问题相关的文档片段。"
                "回答事实性问题前应先调用。若结果不足可改写 query 再搜一次。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索查询句，应具体、含可能出现的关键词",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 5，最大 8",
                        "minimum": 1,
                        "maximum": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "列出当前知识库中已解析完成、可用于检索的文档。"
                "当用户问「有哪些资料」时使用；普通事实问答优先用 search_docs。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


def execute_tool(
    db: Session,
    kb_id: str,
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]] | None]:
    """
    执行工具。

    返回：(给模型的 result, 给前端的 summary, 若为 search 则 hits 否则 None)
    hits 项含 index/chunk_id/document_id/document_title/score/text
    """
    t0 = time.perf_counter()

    if name == "search_docs":
        query = str(arguments.get("query") or "").strip()
        top_k = int(arguments.get("top_k") or 5)
        top_k = max(1, min(top_k, 8))
        if not query:
            result = {"ok": False, "error": "EMPTY_QUERY", "message": "query 不能为空"}
            summary = {"query": "", "hitCount": 0, "documents": []}
            return result, summary, None

        raw, rerank_used = retrieve_for_query(db, kb_id, query, top_k=top_k)
        hits: list[dict[str, Any]] = []
        for i, h in enumerate(raw, start=1):
            hits.append(
                {
                    "index": i,
                    "chunkId": h.chunk_id,
                    "chunk_id": h.chunk_id,
                    "documentId": h.document_id,
                    "document_id": h.document_id,
                    "documentTitle": h.document_title,
                    "document_title": h.document_title,
                    "score": h.score,
                    "text": h.text[:400],
                }
            )
        result = {
            "ok": True,
            "query": query,
            "totalHits": len(hits),
            "rerankUsed": rerank_used,
            "hits": [
                {
                    "chunkId": h["chunk_id"],
                    "documentId": h["document_id"],
                    "documentTitle": h["document_title"],
                    "score": h["score"],
                    "index": h["index"],
                    "text": h["text"],
                }
                for h in hits
            ],
            "hint": None
            if hits
            else (
                "未检索到相关片段。请改写 query：换制度同义词、专有名词或去掉口语后，"
                "再调用一次 search_docs（全会话最多 2 次）；仍无则明确拒答，勿编造。"
            ),
        }
        summary = {
            "query": query,
            "hitCount": len(hits),
            "rerankUsed": rerank_used,
            "documents": sorted(
                {h["document_title"] for h in hits if h["document_title"]}
            ),
        }
        summary["durationMs"] = int((time.perf_counter() - t0) * 1000)
        return result, summary, hits

    if name == "list_documents":
        rows = db.scalars(
            select(Document).where(
                Document.knowledge_base_id == kb_id,
                Document.status == "ready",
            )
        ).all()
        docs = [
            {
                "documentId": d.id,
                "title": d.title,
                "chunkCount": d.chunk_count,
            }
            for d in rows
        ]
        result = {"ok": True, "documents": docs, "readyCount": len(docs)}
        summary = {
            "readyCount": len(docs),
            "titles": [d["title"] for d in docs],
            "durationMs": int((time.perf_counter() - t0) * 1000),
        }
        return result, summary, None

    result = {"ok": False, "error": "UNKNOWN_TOOL", "message": f"未知工具: {name}"}
    summary = {"error": result["error"]}
    return result, summary, None


def count_ready(db: Session, kb_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.knowledge_base_id == kb_id, Document.status == "ready")
        )
        or 0
    )


def parse_tool_args(raw: str | None) -> dict[str, Any]:
    """解析 tool_call arguments JSON。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
