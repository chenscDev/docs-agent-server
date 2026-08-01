"""创作助手：独立于文档问答的有界 Agent（规划 / 改镜 / 压时长 / 换风格 / 出片）。"""

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
from app.video.planner import (
    compress_storyboard_duration,
    patch_storyboard,
    plan_storyboard,
    refine_scene,
)
from app.video.schema import (
    apply_generation_type_defaults,
    Storyboard,
    TemplateId,
    validate_storyboard,
)
from app.video.service import apply_knowledge_sources, refs_from_knowledge_hint

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
            "description": (
                "按指令修改某一镜头的字幕/口播/画面说明。"
                "sceneId 必须来自当前分镜 JSON 的 scenes[].id。"
            ),
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
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": (
                "对当前分镜做结构化 patch（浅合并）。"
                "可改：title、templateId（talking-captions|kinetic-text|brand-intro）、"
                "generationType（narration|kinetic|brand|visual-cut）、"
                "bgmTrackId、ttsVoice、logoUrl、captionPosition、"
                "scenes（按 id 改 durationSec/headline/body，或整表替换/删减镜头）。"
                "换风格优先改 templateId+generationType；改单镜时长写 scenes[{id,durationSec}]。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patches": {
                        "type": "object",
                        "description": "要合并进分镜的字段",
                    },
                },
                "required": ["patches"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compress_duration",
            "description": (
                "把当前分镜总时长压缩/拉长到约 targetSec 秒（等比缩放各镜 durationSec）。"
                "用户说「压到 10 秒 / 缩短一点 / 压缩总时长」时优先调用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "targetSec": {
                        "type": "number",
                        "description": "目标总时长（秒），建议 8～30",
                    },
                },
                "required": ["targetSec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_render",
            "description": (
                "将当前分镜提交为渲染任务并入队。"
                "用户说「去生成 / 渲染 / 出片 / 确认重渲 / 开始做视频」时必须调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "autoStart": {"type": "boolean"},
                },
            },
        },
    },
]


def iter_creative_plan_sse(
    *,
    prompt: str,
    template_id: TemplateId = "talking-captions",
    generation_type: str | None = None,
    brand_notes: str = "",
    knowledge_hint: str = "",
    knowledge_refs: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    auto_understand_materials: bool = True,
) -> Iterator[str]:
    """
    流式推送分镜规划过程（不依赖会话表，供 home 创作页使用）。

    事件：stage / scene_delta / storyboard / done / error
    """
    from app.core.config import get_settings
    from app.video.materials import normalize_materials

    stream_id = new_id("vstream")
    job_placeholder = new_id("plan")
    seq = 0
    cancel_ev = register(stream_id, stream_id)
    settings = get_settings()
    kb_refs = list(knowledge_refs or [])

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

        mats = normalize_materials(materials)
        # 有素材且开启识图：逐条理解并推送进度
        if (
            auto_understand_materials
            and mats
            and getattr(settings, "video_vision_enabled", True)
        ):
            need_idx = [
                i for i, m in enumerate(mats) if not (m.get("caption") or "").strip()
            ]
            if need_idx:
                from app.video.vision_caption import caption_image, caption_video

                total = len(need_idx)
                yield emit(
                    "stage",
                    {
                        "stage": "scripting",
                        "message": f"正在理解素材画面（0/{total}）…",
                        "progress": 0.12,
                    },
                )
                for n, idx in enumerate(need_idx):
                    if cancel_ev.is_set():
                        yield emit("cancelled", {"message": "已取消"})
                        return
                    yield emit(
                        "stage",
                        {
                            "stage": "scripting",
                            "message": f"正在理解素材 {n + 1}/{total}…",
                            "progress": 0.12 + 0.1 * (n / max(1, total)),
                        },
                    )
                    m = mats[idx]
                    try:
                        if m.get("kind") == "video":
                            cap = caption_video(m["url"])
                        else:
                            cap = caption_image(m["url"])
                        if cap:
                            mats[idx] = {**m, "caption": cap[:200]}
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "plan vision material %s failed: %s", idx, exc
                        )
                yield emit(
                    "stage",
                    {
                        "stage": "scripting",
                        "message": "素材理解完成，开始规划分镜…",
                        "progress": 0.22,
                    },
                )

        if cancel_ev.is_set():
            yield emit("cancelled", {"message": "已取消"})
            return

        # 先推送规则骨架，再（可选）用最终分镜覆盖——保证弱网也有反馈
        draft = plan_storyboard(
            prompt,
            template_id=template_id,
            generation_type=generation_type,
            brand_notes=brand_notes,
            knowledge_hint="",
            materials=mats,
            prefer_rules=True,
        )
        for scene in draft.scenes:
            if cancel_ev.is_set():
                yield emit("cancelled", {"message": "已取消"})
                return
            yield emit(
                "scene_delta",
                {"scene": scene.model_dump(mode="json"), "progress": 0.2 + 0.1 * scene.index},
            )

        # 最终稿：知识库 / 素材约束 refining
        final = draft
        need_refine = bool(knowledge_hint.strip()) or bool(mats)
        if need_refine:
            yield emit(
                "stage",
                {
                    "stage": "scripting",
                    "message": (
                        "结合素材与知识库规划分镜…"
                        if mats
                        else "结合品牌/知识库约束 refining…"
                    ),
                    "progress": 0.55,
                },
            )
            final = plan_storyboard(
                prompt,
                template_id=template_id,
                generation_type=generation_type,
                brand_notes=brand_notes,
                knowledge_hint=knowledge_hint,
                materials=mats,
            )
            refs = kb_refs or refs_from_knowledge_hint(knowledge_hint)
            if refs:
                final = apply_knowledge_sources(final, refs)

        if cancel_ev.is_set():
            yield emit("cancelled", {"message": "已取消"})
            return

        warnings = list(getattr(final, "complianceWarnings", None) or [])
        yield emit(
            "storyboard",
            {
                "storyboard": final.to_public_dict(),
                "materials": mats,
                "complianceWarnings": warnings,
                "progress": 0.9,
            },
        )
        yield emit(
            "done",
            {
                "storyboard": final.to_public_dict(),
                "materials": mats,
                "complianceWarnings": warnings,
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
    parent_job_id: str | None = None,
) -> Iterator[str]:
    """
    创作模式对话 SSE（独立路由，不改 /v1/chat）。

    工具：plan_storyboard / refine_scene / apply_patch /
    compress_duration / submit_render。
    """
    request_id = new_id("req")
    seq = 0
    register(request_id, session_id)
    parent_id = (parent_job_id or "").strip() or None
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

    def board_summary(b: Storyboard) -> dict[str, Any]:
        total = round(float(sum(s.durationSec for s in b.scenes)), 1)
        return {
            "version": b.version,
            "sceneCount": len(b.scenes),
            "title": b.title,
            "templateId": b.templateId,
            "generationType": getattr(b, "generationType", None),
            "totalDurationSec": total,
        }

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
            refs = refs_from_knowledge_hint(knowledge_hint)
            if refs:
                board = apply_knowledge_sources(board, refs)
            yield emit(
                "tool_result",
                {
                    "name": "plan_storyboard",
                    "ok": True,
                    "summary": board_summary(board),
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
            "可用工具：plan_storyboard / refine_scene / apply_patch / "
            "compress_duration / submit_render。"
            "规则："
            "1) 改某一镜文案→refine_scene（sceneId 必须用当前 JSON 里的 id）；"
            "2) 压缩/拉长总时长→compress_duration(targetSec)；"
            "3) 换风格（口播字幕条/图文快闪/品牌片头/纯画面）→apply_patch 改 "
            "templateId+generationType（必要时改 bgmTrackId）；"
            "4) 改单镜秒数或删减镜头→apply_patch.scenes；"
            "5) 用户确认出片/重渲→必须调用 submit_render，不要只口头说已提交。"
            "改完后用一句话说明镜数与大约总时长，等待用户确认再 submit_render（除非用户已明确说立刻出片）。"
            "不要编造渲染结果；submit_render 返回的 jobId 才是真实任务。"
            "回答简洁，中文。"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": message
                + (
                    f"\n\n当前分镜 JSON：{json.dumps(board.to_public_dict(), ensure_ascii=False)[:2400]}"
                    if board
                    else ""
                )
                + (f"\n\n知识库约束：{knowledge_hint[:800]}" if knowledge_hint else "")
                + (
                    f"\n\n来源成片 jobId={parent_id}（重渲时可复用片段）"
                    if parent_id
                    else ""
                ),
            },
        ]

        for _round in range(4):
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
                        refs = refs_from_knowledge_hint(knowledge_hint)
                        if refs:
                            board = apply_knowledge_sources(board, refs)
                        summary = board_summary(board)
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
                        summary = board_summary(board)
                        yield emit(
                            "storyboard",
                            {"storyboard": board.to_public_dict()},
                        )
                    elif name == "apply_patch" and board is not None:
                        patches = args.get("patches")
                        if not isinstance(patches, dict):
                            raise ValueError("patches 必须是对象")
                        # 换风格时同步 generationType → template/tts
                        if "generationType" in patches or "templateId" in patches:
                            merged = apply_generation_type_defaults(
                                {**board.model_dump(), **patches},
                                patches.get("generationType")
                                or board.generationType,
                            )
                            # 保留 scenes 等未在 defaults 覆盖的字段
                            for k, v in patches.items():
                                if k not in (
                                    "templateId",
                                    "generationType",
                                    "ttsEnabled",
                                    "bgmTrackId",
                                ):
                                    merged[k] = v
                            board = patch_storyboard(
                                board,
                                patches={
                                    k: merged[k]
                                    for k in (
                                        "templateId",
                                        "generationType",
                                        "ttsEnabled",
                                        "bgmTrackId",
                                        "bgmEnabled",
                                    )
                                    if k in merged
                                }
                                | {
                                    k: v
                                    for k, v in patches.items()
                                    if k
                                    not in (
                                        "templateId",
                                        "generationType",
                                        "ttsEnabled",
                                        "bgmTrackId",
                                        "bgmEnabled",
                                    )
                                },
                            )
                        else:
                            board = patch_storyboard(board, patches=patches)
                        summary = {
                            **board_summary(board),
                            "keys": list(patches.keys())[:12],
                        }
                        yield emit(
                            "storyboard",
                            {"storyboard": board.to_public_dict()},
                        )
                    elif name == "compress_duration" and board is not None:
                        target = float(args.get("targetSec") or 12)
                        board = compress_storyboard_duration(
                            board, target_sec=target
                        )
                        summary = board_summary(board)
                        yield emit(
                            "storyboard",
                            {"storyboard": board.to_public_dict()},
                        )
                    elif name == "submit_render" and board is not None:
                        from app.db import session as db_session
                        from app.video.render_queue import enqueue_video_job
                        from app.video.service import create_job

                        db_session.get_engine()
                        assert db_session.SessionLocal is not None
                        auto_start = args.get("autoStart", True) is not False
                        with db_session.SessionLocal() as db:
                            job = create_job(
                                db,
                                prompt=board.prompt,
                                template_id=board.templateId,
                                storyboard=board,
                                parent_job_id=parent_id,
                                generation_type=getattr(
                                    board, "generationType", None
                                ),
                            )
                            if auto_start:
                                enqueue_video_job(job.id)
                            summary = {
                                "jobId": job.id,
                                "status": job.status,
                                "enqueued": auto_start,
                                "parentJobId": parent_id,
                                **board_summary(board),
                            }
                        yield emit("render_submitted", summary)
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
