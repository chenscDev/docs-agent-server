"""非流式 RAG 问答编排（D5：服务端固定先检索再生成，事件化留给 D6）。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.citations import build_citations
from app.agent.history import select_history_window, to_chat_messages
from app.agent.prompt import (
    REFUSAL_TEXT,
    SYSTEM_PROMPT,
    build_context_block,
    build_scene_line,
)
from app.core.ids import new_id
from app.core.llm import LLMClient
from app.db.models import Document, Message, Session as ChatSession
from app.rag.retrieve import retrieve_for_query

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """一次问答结果。"""

    request_id: str
    user_message_id: str
    assistant_message_id: str
    answer: str
    citations: list[dict[str, Any]]
    tool_trace: list[dict[str, Any]]
    usage: dict[str, Any]


def count_ready_documents(db: Session, kb_id: str) -> int:
    """统计已就绪文档数。"""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.knowledge_base_id == kb_id, Document.status == "ready")
        )
        or 0
    )


def run_rag_chat(
    db: Session,
    session: ChatSession,
    user_text: str,
    *,
    client_message_id: str | None = None,
    top_k: int = 5,
) -> ChatResult:
    """执行：历史窗口 → 检索 → 生成 → 落库。"""
    request_id = new_id("req")
    ready_count = count_ready_documents(db, session.knowledge_base_id)
    if ready_count < 1:
        raise ValueError("NO_READY_DOC")

    # 历史（不含本轮 user）
    prior = db.scalars(
        select(Message)
        .where(Message.session_id == session.id)
        .order_by(Message.created_at.asc())
    ).all()
    window = select_history_window(list(prior))

    user_msg = Message(
        id=new_id("msg"),
        session_id=session.id,
        role="user",
        content=user_text,
        status="completed",
        client_message_id=client_message_id,
        request_id=request_id,
    )
    db.add(user_msg)
    db.flush()

    # 检索（FAISS + 可选 Rerank）
    t0 = time.perf_counter()
    raw_hits, rerank_used = retrieve_for_query(
        db, session.knowledge_base_id, user_text, top_k=top_k
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)

    hits: list[dict[str, Any]] = []
    for i, h in enumerate(raw_hits, start=1):
        hits.append(
            {
                "index": i,
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "document_title": h.document_title,
                "score": h.score,
                "text": h.text[:400],
            }
        )

    tool_trace = [
        {
            "toolCallId": "call_search_1",
            "name": "search_docs",
            "ok": True,
            "durationMs": duration_ms,
            "summary": {
                "query": user_text,
                "hitCount": len(hits),
                "rerankUsed": rerank_used,
                "documents": sorted({h["document_title"] for h in hits if h["document_title"]}),
            },
        }
    ]

    if not hits:
        answer = REFUSAL_TEXT
        citations: list[dict[str, Any]] = []
        usage = {
            "promptTokens": None,
            "completionTokens": None,
            "latencyMs": duration_ms,
            "rerankUsed": rerank_used,
            "searchCalls": 1,
            "citationCount": 0,
        }
    else:
        llm_messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": build_scene_line(ready_count=ready_count)
                + "\n\n"
                + build_context_block(hits),
            },
        ]
        llm_messages.extend(to_chat_messages(window))
        llm_messages.append({"role": "user", "content": user_text})

        t1 = time.perf_counter()
        client = LLMClient()
        answer = client.chat(llm_messages)
        gen_ms = int((time.perf_counter() - t1) * 1000)
        citations = build_citations(answer, hits)
        usage = {
            "promptTokens": None,
            "completionTokens": None,
            "latencyMs": duration_ms + gen_ms,
            "rerankUsed": rerank_used,
            "searchCalls": 1,
            "citationCount": len(citations),
        }

    assistant_msg = Message(
        id=new_id("msg"),
        session_id=session.id,
        role="assistant",
        content=answer,
        status="completed",
        request_id=request_id,
        citations_json=json.dumps(citations, ensure_ascii=False),
        tool_trace_json=json.dumps(tool_trace, ensure_ascii=False),
        usage_json=json.dumps(usage, ensure_ascii=False),
    )
    db.add(assistant_msg)

    # 更新会话预览与标题
    session.last_preview = answer[:80]
    session.updated_at = datetime.now(timezone.utc)
    if not session.title:
        session.title = user_text[:40]
    db.commit()

    logger.info(
        "rag chat session=%s req=%s hits=%s citations=%s",
        session.id,
        request_id,
        len(hits),
        len(citations),
    )

    return ChatResult(
        request_id=request_id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant_msg.id,
        answer=answer,
        citations=citations,
        tool_trace=tool_trace,
        usage=usage,
    )
