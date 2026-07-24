"""手写 Agent 循环 + SSE 事件生成（D6）。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.citations import build_citations
from app.agent.history import select_history_window, to_chat_messages
from app.agent.prompt import REFUSAL_TEXT, build_scene_line
from app.agent.sse import make_event
from app.agent.tools import (
    TOOL_DEFINITIONS,
    count_ready,
    execute_tool,
    parse_tool_args,
)
from app.agent.cancel_registry import (
    GenerationCancelled,
    is_cancelled,
    register as register_cancel,
    unregister as unregister_cancel,
)
from app.agent.rewrite import rewrite_search_query
from app.core.ids import new_id
from app.core.llm import LLMClient
from app.db.models import Message, Session as ChatSession

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """你是「文档问答助手」，只能依据工具返回的文档内容回答事实问题。
你不是通用百科，不要编造知识库中不存在的信息。

工具使用：
- 事实性、条款、数字、流程类问题：先调用 search_docs
- 「有哪些文档 / 手册在不在」：用 list_documents
- search_docs 无命中时：必须改写 query（换同义词/制度关键词/去掉口语）再搜 1 次（合计最多 2 次）；仍无则明确拒答
- 服务端可能在空命中后自动补一次改写检索，请优先依据最新 hits 作答
- 不要假装已经检索过

回答与引用：
- 依据 search_docs 返回的 hits；每条有 index（从 1 起）
- 正文中对文档事实添加 [1]、[2] 等引用
- 禁止使用不存在的序号；禁止杜撰文档名
- 简洁中文，默认不超过 400 字
- 不要向用户输出 JSON 或原始 tool 结果

若两次检索皆空或不足以支撑结论：说明当前知识库未找到相关内容。"""

MAX_TOOL_ROUNDS = 6
MAX_SEARCH_CALLS = 2


def iter_agent_sse(
    db: Session,
    session: ChatSession,
    user_text: str,
    *,
    client_message_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """
    产出 SSE envelope 字典（由路由再 format_sse）。

    事件：message.accepted → tool.* → message.delta* → message.completed
    出错时：error → message.completed(failed)
    """
    request_id = new_id("req")
    seq = 0
    register_cancel(request_id, session.id)

    def emit(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal seq
        ev = make_event(
            request_id=request_id,
            session_id=session.id,
            seq=seq,
            event_type=event_type,
            payload=payload,
        )
        seq += 1
        return ev

    def ensure_not_cancelled() -> None:
        if is_cancelled(request_id):
            raise GenerationCancelled()

    ready_count = count_ready(db, session.knowledge_base_id)
    if ready_count < 1:
        unregister_cancel(request_id, session.id)
        yield emit(
            "error",
            {
                "code": "NO_READY_DOC",
                "message": "请至少等待一份文档显示为可问答后再提问",
                "retryable": False,
            },
        )
        yield emit(
            "message.completed",
            {"status": "failed", "assistantMessageId": None, "answer": "", "citations": []},
        )
        return

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
    db.commit()

    yield emit(
        "message.accepted",
        {
            "userMessageId": user_msg.id,
            "clientMessageId": client_message_id,
        },
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": build_scene_line(ready_count=ready_count),
        },
    ]
    messages.extend(to_chat_messages(window))
    messages.append({"role": "user", "content": user_text})

    client = LLMClient()
    tool_trace: list[dict[str, Any]] = []
    last_hits: list[dict[str, Any]] = []
    search_calls = 0
    search_queries: list[str] = []
    rewrite_used = False
    answer = ""
    emitted_parts: list[str] = []
    t_start = time.perf_counter()

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            ensure_not_cancelled()
            msg = client.chat_with_tools(messages, TOOL_DEFINITIONS)
            ensure_not_cancelled()
            tool_calls = getattr(msg, "tool_calls", None) or []

            if tool_calls:
                # 写入 assistant tool_calls 消息
                tc_payload = []
                for tc in tool_calls:
                    tc_payload.append(
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or None,
                        "tool_calls": tc_payload,
                    }
                )

                for tc in tool_calls:
                    ensure_not_cancelled()
                    name = tc.function.name
                    args = parse_tool_args(tc.function.arguments)
                    yield emit(
                        "tool.started",
                        {
                            "toolCallId": tc.id,
                            "name": name,
                            "args": args,
                        },
                    )

                    if name == "search_docs":
                        search_calls += 1
                        if search_calls > MAX_SEARCH_CALLS:
                            result = {
                                "ok": False,
                                "error": "SEARCH_LIMIT",
                                "message": "search_docs 已达上限（2 次），请基于已有结果作答",
                            }
                            summary = {"hitCount": 0, "documents": [], "limited": True}
                            hits = None
                            ok = False
                            duration_ms = 0
                        else:
                            result, summary, hits = execute_tool(
                                db, session.knowledge_base_id, name, args
                            )
                            ok = bool(result.get("ok"))
                            duration_ms = int(summary.get("durationMs") or 0)
                            q = str(args.get("query") or summary.get("query") or "").strip()
                            if q:
                                search_queries.append(q)
                            if hits is not None:
                                last_hits = hits
                    else:
                        result, summary, hits = execute_tool(
                            db, session.knowledge_base_id, name, args
                        )
                        ok = bool(result.get("ok"))
                        duration_ms = int(summary.get("durationMs") or 0)

                    tool_trace.append(
                        {
                            "toolCallId": tc.id,
                            "name": name,
                            "ok": ok,
                            "durationMs": duration_ms,
                            "summary": summary,
                        }
                    )
                    yield emit(
                        "tool.completed",
                        {
                            "toolCallId": tc.id,
                            "name": name,
                            "ok": ok,
                            "durationMs": duration_ms,
                            "summary": summary,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )

                    # 二期：首次 search 空命中 → 服务端改写后再搜一次（仍计入上限）
                    if (
                        name == "search_docs"
                        and hits is not None
                        and len(hits) == 0
                        and search_calls < MAX_SEARCH_CALLS
                        and not rewrite_used
                    ):
                        failed_q = str(
                            args.get("query") or summary.get("query") or ""
                        ).strip()
                        ensure_not_cancelled()
                        rewritten = rewrite_search_query(
                            client,
                            user_text=user_text,
                            failed_query=failed_q,
                        )
                        if rewritten:
                            rewrite_used = True
                            search_calls += 1
                            search_queries.append(rewritten)
                            syn_id = new_id("call")
                            syn_args = {"query": rewritten, "top_k": int(args.get("top_k") or 5)}
                            yield emit(
                                "tool.started",
                                {
                                    "toolCallId": syn_id,
                                    "name": "search_docs",
                                    "args": {
                                        **syn_args,
                                        "rewritten": True,
                                        "originalQuery": failed_q,
                                    },
                                },
                            )
                            result2, summary2, hits2 = execute_tool(
                                db,
                                session.knowledge_base_id,
                                "search_docs",
                                syn_args,
                            )
                            summary2 = dict(summary2)
                            summary2["rewritten"] = True
                            summary2["originalQuery"] = failed_q
                            if result2.get("ok") and not (hits2 or []):
                                result2 = dict(result2)
                                result2["hint"] = (
                                    "改写后仍未命中。请明确告知用户知识库中未找到相关内容，不要编造。"
                                )
                            ok2 = bool(result2.get("ok"))
                            dur2 = int(summary2.get("durationMs") or 0)
                            if hits2 is not None:
                                last_hits = hits2
                            tool_trace.append(
                                {
                                    "toolCallId": syn_id,
                                    "name": "search_docs",
                                    "ok": ok2,
                                    "durationMs": dur2,
                                    "summary": summary2,
                                }
                            )
                            yield emit(
                                "tool.completed",
                                {
                                    "toolCallId": syn_id,
                                    "name": "search_docs",
                                    "ok": ok2,
                                    "durationMs": dur2,
                                    "summary": summary2,
                                },
                            )
                            # 写入对话历史，供后续生成引用
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": syn_id,
                                            "type": "function",
                                            "function": {
                                                "name": "search_docs",
                                                "arguments": json.dumps(
                                                    syn_args, ensure_ascii=False
                                                ),
                                            },
                                        }
                                    ],
                                }
                            )
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": syn_id,
                                    "content": json.dumps(result2, ensure_ascii=False),
                                }
                            )
                            logger.info(
                                "rewrite_search requestId=%s from=%r to=%r hits=%s",
                                request_id,
                                failed_q,
                                rewritten,
                                len(hits2 or []),
                            )
                continue

            # 无 tool_calls：进入最终回答
            if msg.content:
                full = msg.content
                # 伪流式拆分；稍作间隔便于网关/RN XHR 增量刷新
                for i in range(0, len(full), 18):
                    ensure_not_cancelled()
                    piece = full[i : i + 18]
                    emitted_parts.append(piece)
                    yield emit("message.delta", {"text": piece})
                    time.sleep(0.025)
                answer = full
            else:
                # 再开一轮纯流式生成（无 tools）
                stream_messages = list(messages)
                stream_messages.append(
                    {
                        "role": "user",
                        "content": "请基于以上工具结果给出最终中文回答，并正确添加 [n] 引用。",
                    }
                )
                for delta in client.stream_chat(stream_messages):
                    ensure_not_cancelled()
                    emitted_parts.append(delta)
                    yield emit("message.delta", {"text": delta})
                answer = "".join(emitted_parts)

            break
        else:
            answer = answer or REFUSAL_TEXT
            if not answer:
                emitted_parts.append(REFUSAL_TEXT)
                yield emit("message.delta", {"text": REFUSAL_TEXT})
                answer = REFUSAL_TEXT

        ensure_not_cancelled()

        if not answer.strip():
            answer = REFUSAL_TEXT
            emitted_parts.append(answer)
            yield emit("message.delta", {"text": answer})

        # 若从未检索且也不是列目录类，仍允许模型回答；citations 仅来自 last_hits
        citations = build_citations(answer, last_hits)

        latency_ms = int((time.perf_counter() - t_start) * 1000)
        usage = {
            "promptTokens": None,
            "completionTokens": None,
            "completionChars": len(answer),
            "latencyMs": latency_ms,
            "searchCalls": search_calls,
            "searchQueries": search_queries,
            "rewriteUsed": rewrite_used,
            "toolCallCount": len(tool_trace),
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
        session.last_preview = answer[:80]
        session.updated_at = datetime.now(timezone.utc)
        if not session.title:
            session.title = user_text[:40]
        db.commit()

        logger.info(
            "agent_ok requestId=%s sessionId=%s latencyMs=%s searchCalls=%s "
            "rewriteUsed=%s tools=%s citations=%s chars=%s",
            request_id,
            session.id,
            latency_ms,
            search_calls,
            rewrite_used,
            len(tool_trace),
            len(citations),
            len(answer),
        )

        yield emit(
            "message.completed",
            {
                "status": "ok",
                "assistantMessageId": assistant_msg.id,
                "answer": answer,
                "citations": citations,
                "toolTrace": tool_trace,
                "usage": usage,
                "requestId": request_id,
            },
        )
    except GenerationCancelled:
        # 仅落库已推给端上的部分，避免「端上半截、库里全文」
        partial = "".join(emitted_parts) if emitted_parts else answer
        logger.info(
            "agent_cancelled requestId=%s sessionId=%s partialChars=%s",
            request_id,
            session.id,
            len(partial),
        )
        db.rollback()
        citations = build_citations(partial, last_hits) if partial else []
        latency_ms = int((time.perf_counter() - t_start) * 1000)
        usage = {
            "promptTokens": None,
            "completionTokens": None,
            "completionChars": len(partial),
            "latencyMs": latency_ms,
            "searchCalls": search_calls,
            "searchQueries": search_queries,
            "rewriteUsed": rewrite_used,
            "toolCallCount": len(tool_trace),
            "citationCount": len(citations),
            "cancelled": True,
        }
        assistant_id = None
        try:
            cancel_msg = Message(
                id=new_id("msg"),
                session_id=session.id,
                role="assistant",
                content=partial or "",
                status="cancelled",
                request_id=request_id,
                citations_json=json.dumps(citations, ensure_ascii=False),
                tool_trace_json=json.dumps(tool_trace, ensure_ascii=False),
                usage_json=json.dumps(usage, ensure_ascii=False),
                error_code="CANCELLED",
                error_message="用户停止生成",
            )
            db.add(cancel_msg)
            session.updated_at = datetime.now(timezone.utc)
            db.commit()
            assistant_id = cancel_msg.id
        except Exception:  # noqa: BLE001
            db.rollback()

        yield emit(
            "error",
            {
                "code": "CANCELLED",
                "message": "已停止生成",
                "retryable": False,
            },
        )
        yield emit(
            "message.completed",
            {
                "status": "cancelled",
                "assistantMessageId": assistant_id,
                "answer": partial,
                "citations": citations,
                "toolTrace": tool_trace,
                "usage": usage,
                "requestId": request_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent stream failed req=%s", request_id)
        db.rollback()
        # 尽量记一条失败助手消息
        try:
            fail_msg = Message(
                id=new_id("msg"),
                session_id=session.id,
                role="assistant",
                content="",
                status="failed",
                request_id=request_id,
                error_code="AGENT_FAILED",
                error_message=str(exc),
            )
            db.add(fail_msg)
            db.commit()
            assistant_id = fail_msg.id
        except Exception:  # noqa: BLE001
            db.rollback()
            assistant_id = None

        yield emit(
            "error",
            {
                "code": "AGENT_FAILED",
                "message": str(exc),
                "retryable": True,
            },
        )
        yield emit(
            "message.completed",
            {
                "status": "failed",
                "assistantMessageId": assistant_id,
                "answer": "",
                "citations": [],
                "toolTrace": tool_trace,
            },
        )
    finally:
        unregister_cancel(request_id, session.id)