"""问答 API：非流式 + SSE Agent。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agent.agent_stream import iter_agent_sse
from app.agent.cancel_registry import request_cancel
from app.agent.rag_chat import run_rag_chat
from app.agent.sse import format_sse, make_event
from app.core.errors import raise_api_error
from app.db import session as db_session
from app.db.models import Session as ChatSession
from app.db.session import get_db

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str = Field(alias="sessionId")
    message: str = Field(..., min_length=1)
    client_message_id: str | None = Field(default=None, alias="clientMessageId")

    model_config = ConfigDict(populate_by_name=True)


class CancelRequest(BaseModel):
    """停止生成：传 requestId 或 sessionId（取该会话当前活跃请求）。"""

    request_id: str | None = Field(default=None, alias="requestId")
    session_id: str | None = Field(default=None, alias="sessionId")

    model_config = ConfigDict(populate_by_name=True)


@router.post("/chat")
def chat(body: ChatRequest, db: Session = Depends(get_db)) -> dict:
    """非流式文档问答（D5 路径，便于对比）。"""
    session = db.get(ChatSession, body.session_id)
    if session is None:
        raise_api_error(404, "SESSION_NOT_FOUND", "会话不存在")

    try:
        result = run_rag_chat(
            db,
            session,
            body.message.strip(),
            client_message_id=body.client_message_id,
        )
    except ValueError as exc:
        if str(exc) == "NO_READY_DOC":
            raise_api_error(
                409,
                "NO_READY_DOC",
                "请至少等待一份文档显示为可问答（ready）后再提问",
                retryable=False,
            )
        raise_api_error(400, "CHAT_BAD_REQUEST", str(exc))
    except RuntimeError as exc:
        raise_api_error(502, "LLM_ERROR", str(exc), retryable=True)

    return {
        "requestId": result.request_id,
        "userMessageId": result.user_message_id,
        "assistantMessageId": result.assistant_message_id,
        "answer": result.answer,
        "citations": result.citations,
        "toolTrace": result.tool_trace,
        "usage": result.usage,
    }


@router.post("/chat/cancel")
def cancel_chat(body: CancelRequest) -> dict:
    """
    请求停止正在进行的 SSE 生成（D19）。

    端上 Abort 仍可用；本接口让服务端在 tool/delta 间隙停止并落库 cancelled。
    注意：正在阻塞的单次 LLM HTTP 调用无法立刻掐断，会在返回后生效。
    """
    if not body.request_id and not body.session_id:
        raise_api_error(
            400,
            "CANCEL_BAD_REQUEST",
            "请提供 requestId 或 sessionId",
        )

    found, rid = request_cancel(
        request_id=body.request_id,
        session_id=body.session_id,
    )
    return {
        "ok": found,
        "requestId": rid,
        "message": "已标记取消" if found else "未找到进行中的生成",
    }


@router.post("/chat/stream")
def chat_stream(body: ChatRequest) -> StreamingResponse:
    """
    SSE 流式 Agent 问答（D6）。

    事件：message.accepted / tool.started / tool.completed /
    message.delta / error / message.completed
    """

    def event_generator():
        # 流式响应期间不能依赖 get_db 的短生命周期 Session
        db_session.get_engine()
        factory = db_session.SessionLocal
        if factory is None:
            ev = make_event(
                request_id="req_none",
                session_id=body.session_id,
                seq=0,
                event_type="error",
                payload={
                    "code": "DB_NOT_READY",
                    "message": "数据库未初始化",
                    "retryable": True,
                },
            )
            yield format_sse(ev)
            return

        db = factory()
        try:
            session = db.get(ChatSession, body.session_id)
            if session is None:
                ev = make_event(
                    request_id="req_none",
                    session_id=body.session_id,
                    seq=0,
                    event_type="error",
                    payload={
                        "code": "SESSION_NOT_FOUND",
                        "message": "会话不存在",
                        "retryable": False,
                    },
                )
                yield format_sse(ev)
                done = make_event(
                    request_id="req_none",
                    session_id=body.session_id,
                    seq=1,
                    event_type="message.completed",
                    payload={"status": "failed", "answer": "", "citations": []},
                )
                yield format_sse(done)
                return

            for event in iter_agent_sse(
                db,
                session,
                body.message.strip(),
                client_message_id=body.client_message_id,
            ):
                yield format_sse(event)
        finally:
            db.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
