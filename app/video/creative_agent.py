"""创作助手：独立于文档问答的有界 Agent（plan_storyboard / refine_scene）。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from app.agent.cancel_registry import (
    GenerationCancelled,
    is_cancelled,
    register,
    unregister,
)
from app.agent.sse import format_sse, make_event
from app.core.ids import new_id
from app.core.llm import LLMClient
from app.video.events import format_sse as format_video_sse
from app.video.events import make_video_event
from app.video.planner import plan_storyboard, refine_scene
from app.video.schema import Storyboard, TemplateId, validate_storyboard

logger = logging.getLogger(__name__)

CREATIVE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "plan_storyboard",
            "description": "根据一句话创意生成结构化短视频分镜 JSON",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "templateId": {
                        "type": "string",
                        "enum": [
                            "talking-captions",
                            "kinetic-text",
                            "brand-intro",
                        ],
                    },
                    "brandNotes": {"type": "string"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refine_scene",
            "description": "按指令修改某一镜头字幕/文案",
            "parameters": {
                "type": "object",
                "properties": {
                    "sceneId": {"type": "string"},
                    "instruction": {"type": "string"},
                },
                "required": ["sceneId", "instruction"],
            },
        },
    },
]


def iter_creative_plan_sse(
    *,
    prompt: str,
    template_id: TemplateId = "talking-captions",
    brand_notes: str = "",
    knowledge_hint: str = "",
) -> Iterator[str]:
    """
    流式推送分镜规划过程（不依赖会话表，供 home 创作页使用）。

    事件：stage / scene_delta / storyboard / done / error
    """
    stream_id = new_id("vstream")
    job_placeholder = new_id("plan")
    seq = 0
    cancel_ev = register(stream_id, stream_id)

    def emit(event_type: str, payload: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        return format_video_sse(
            make_video_event(
                job_id=job_placeholder,
                stream_id=stream_id,
                seq=seq,
                event_type=event_type,
                payload=payload,
            )
        )

    try:
        yield emit(
            "stage",
            {"stage": "scripting", "message": "正在理解创意并规划分镜…", "progress": 0.1},
        )
        if cancel_ev.is_set():
            yield emit("cancelled", {"message": "已取消"})
            return

        # 先推送规则骨架，再（可选）用最终分镜覆盖——保证弱网也有反馈
        draft = plan_storyboard(
            prompt,
            template_id=template_id,
            brand_notes=brand_notes,
            knowledge_hint="",
        )
        for scene in draft.scenes:
            if cancel_ev.is_set():
                yield emit("cancelled", {"message": "已取消"})
                return
            yield emit(
                "scene_delta",
                {"scene": scene.model_dump(mode="json"), "progress": 0.2 + 0.1 * scene.index},
            )

        # 若有知识库约束，再规划一版最终稿
        final = draft
        if knowledge_hint.strip():
            yield emit(
                "stage",
                {
                    "stage": "scripting",
                    "message": "结合品牌/知识库约束 refining…",
                    "progress": 0.55,
                },
            )
            final = plan_storyboard(
                prompt,
                template_id=template_id,
                brand_notes=brand_notes,
                knowledge_hint=knowledge_hint,
            )

        if cancel_ev.is_set():
            yield emit("cancelled", {"message": "已取消"})
            return

        yield emit(
            "storyboard",
            {
                "storyboard": final.to_public_dict(),
                "progress": 0.9,
            },
        )
        yield emit(
            "done",
            {
                "storyboard": final.to_public_dict(),
                "progress": 1.0,
                "streamId": stream_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("creative plan failed")
        yield emit("error", {"code": "PLAN_FAILED", "message": str(exc)[:300]})
    finally:
        unregister(stream_id)


def iter_creative_agent_sse(
    *,
    message: str,
    session_id: str,
    template_id: TemplateId = "talking-captions",
    storyboard: dict[str, Any] | None = None,
    knowledge_hint: str = "",
) -> Iterator[str]:
    """
    创作模式对话 SSE（独立路由，不改 /v1/chat）。

    工具白名单仅 plan_storyboard / refine_scene。
    """
    request_id = new_id("req")
    seq = 0
    register(request_id, session_id)
    board: Storyboard | None = None
    if storyboard:
        try:
            board = validate_storyboard(storyboard)
        except Exception:  # noqa: BLE001
            board = None

    def emit(event_type: str, payload: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        return format_sse(
            make_event(
                request_id=request_id,
                session_id=session_id,
                seq=seq,
                event_type=event_type,
                payload=payload,
            )
        )

    try:
        yield emit("request_started", {"requestId": request_id})

        # 无 Key 时直接规则规划
        try:
            llm = LLMClient()
        except ValueError:
            board = plan_storyboard(
                message,
                template_id=template_id,
                knowledge_hint=knowledge_hint,
            )
            yield emit(
                "tool_result",
                {
                    "name": "plan_storyboard",
                    "ok": True,
                    "summary": {"sceneCount": len(board.scenes)},
                },
            )
            yield emit("storyboard", {"storyboard": board.to_public_dict()})
            yield emit(
                "answer_delta",
                {"text": f"已生成分镜「{board.title}」，共 {len(board.scenes)} 镜。"},
            )
            yield emit("done", {"storyboard": board.to_public_dict()})
            return

        system = (
            "你是移动端 AI 短视频创作助手。"
            "优先调用 plan_storyboard 或 refine_scene；不要编造渲染结果。"
            "回答简洁，中文。"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": message
                + (
                    f"\n\n当前分镜 JSON：{json.dumps(board.to_public_dict(), ensure_ascii=False)[:2000]}"
                    if board
                    else ""
                )
                + (f"\n\n知识库约束：{knowledge_hint[:800]}" if knowledge_hint else ""),
            },
        ]

        for _round in range(3):
            if is_cancelled(request_id):
                raise GenerationCancelled()
            msg = llm.chat_with_tools(
                messages,
                CREATIVE_TOOLS,
                request_id=request_id,
            )
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                text = (msg.content or "").strip()
                if text:
                    yield emit("answer_delta", {"text": text})
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield emit(
                    "tool_call",
                    {"toolCallId": tc.id, "name": name, "arguments": args},
                )
                ok = True
                summary: dict[str, Any] = {}
                try:
                    if name == "plan_storyboard":
                        board = plan_storyboard(
                            str(args.get("prompt") or message),
                            template_id=args.get("templateId") or template_id,
                            brand_notes=str(args.get("brandNotes") or ""),
                            knowledge_hint=knowledge_hint,
                        )
                        summary = {"sceneCount": len(board.scenes), "title": board.title}
                        yield emit(
                            "storyboard",
                            {"storyboard": board.to_public_dict()},
                        )
                    elif name == "refine_scene" and board is not None:
                        board = refine_scene(
                            board,
                            scene_id=str(args.get("sceneId") or ""),
                            instruction=str(args.get("instruction") or ""),
                        )
                        summary = {"version": board.version}
                        yield emit(
                            "storyboard",
                            {"storyboard": board.to_public_dict()},
                        )
                    else:
                        ok = False
                        summary = {"error": "unknown tool or missing storyboard"}
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    summary = {"error": str(exc)[:200]}
                yield emit(
                    "tool_result",
                    {
                        "toolCallId": tc.id,
                        "name": name,
                        "ok": ok,
                        "summary": summary,
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(summary, ensure_ascii=False),
                    }
                )

        if board is not None:
            yield emit(
                "done",
                {
                    "storyboard": board.to_public_dict(),
                    "requestId": request_id,
                },
            )
        else:
            yield emit("done", {"requestId": request_id})
    except GenerationCancelled:
        yield emit("cancelled", {"message": "已取消"})
    except Exception as exc:  # noqa: BLE001
        logger.exception("creative agent failed")
        yield emit("error", {"code": "CREATIVE_AGENT_FAILED", "message": str(exc)[:300]})
    finally:
        unregister(request_id)
