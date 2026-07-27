"""非流式问答：复用 Agent SSE 编排（P3-D14），聚合为一次 JSON 响应。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.agent_stream import iter_agent_sse
from app.db.models import Session as ChatSession

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


def run_rag_chat(
    db: Session,
    session: ChatSession,
    user_text: str,
    *,
    client_message_id: str | None = None,
    top_k: int = 5,
) -> ChatResult:
    """
    执行与 /v1/chat/stream 同一套 Agent loop，再聚合成非流式结果。

    top_k 保留参数以兼容旧调用；实际 top_k 由模型 tool args / 默认值决定。
    """
    _ = top_k  # Agent 路径由 search_docs 参数控制

    user_message_id = ""
    assistant_message_id = ""
    request_id = ""
    answer = ""
    citations: list[dict[str, Any]] = []
    tool_trace: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    last_error: dict[str, Any] | None = None
    completed: dict[str, Any] | None = None

    for event in iter_agent_sse(
        db,
        session,
        user_text,
        client_message_id=client_message_id,
        stream_text=False,
    ):
        etype = event.get("type") or ""
        payload = event.get("payload") or {}
        if event.get("requestId"):
            request_id = str(event["requestId"])

        if etype == "message.accepted":
            user_message_id = str(payload.get("userMessageId") or "")
        elif etype == "error":
            last_error = dict(payload)
        elif etype == "message.completed":
            completed = dict(payload)
            answer = str(payload.get("answer") or "")
            citations = list(payload.get("citations") or [])
            tool_trace = list(payload.get("toolTrace") or [])
            usage = dict(payload.get("usage") or {})
            aid = payload.get("assistantMessageId")
            assistant_message_id = str(aid) if aid else ""

    if last_error and last_error.get("code") == "NO_READY_DOC":
        raise ValueError("NO_READY_DOC")

    if completed is None:
        raise RuntimeError("AGENT_FAILED: 未收到 message.completed")

    status = str(completed.get("status") or "")
    if status == "failed":
        code = (last_error or {}).get("code") or "AGENT_FAILED"
        if code == "NO_READY_DOC":
            raise ValueError("NO_READY_DOC")
        message = str((last_error or {}).get("message") or code)
        raise RuntimeError(message)

    if status == "cancelled":
        # 非流式路径一般不会取消；若发生仍返回已生成部分
        logger.info(
            "nonstream cancelled requestId=%s partialChars=%s",
            request_id,
            len(answer),
        )

    logger.info(
        "rag chat (agent) session=%s req=%s status=%s citations=%s tools=%s",
        session.id,
        request_id,
        status,
        len(citations),
        len(tool_trace),
    )

    return ChatResult(
        request_id=request_id or "req_unknown",
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        answer=answer,
        citations=citations,
        tool_trace=tool_trace,
        usage=usage,
    )
