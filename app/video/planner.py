"""分镜规划：优先 LLM 结构化输出，失败则规则兜底。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.ids import new_id
from app.core.llm import LLMClient
from app.video.schema import Scene, Storyboard, TemplateId, validate_storyboard

logger = logging.getLogger(__name__)

_PALETTES: dict[str, tuple[str, str]] = {
    "talking-captions": ("#0F172A", "#38BDF8"),
    "kinetic-text": ("#1E1B4B", "#A78BFA"),
    "brand-intro": ("#022C22", "#34D399"),
}


def plan_storyboard(
    prompt: str,
    *,
    template_id: TemplateId = "talking-captions",
    brand_notes: str = "",
    knowledge_hint: str = "",
    prefer_rules: bool = False,
) -> Storyboard:
    """根据一句话生成分镜；LLM 不可用或校验失败时走规则模板。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")

    if not prefer_rules:
        try:
            board = _plan_with_llm(
                prompt,
                template_id=template_id,
                brand_notes=brand_notes,
                knowledge_hint=knowledge_hint,
            )
            if board is not None:
                return board
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 分镜失败，回退规则: %s", exc)

    return _plan_with_rules(
        prompt,
        template_id=template_id,
        brand_notes=brand_notes,
        knowledge_hint=knowledge_hint,
    )


def refine_scene(
    storyboard: Storyboard,
    *,
    scene_id: str,
    instruction: str,
) -> Storyboard:
    """按自然语言局部修改某一镜。"""
    instruction = (instruction or "").strip()
    if not instruction:
        return storyboard

    scenes: list[Scene] = []
    for scene in storyboard.scenes:
        if scene.id != scene_id:
            scenes.append(scene)
            continue
        # 简单规则：缩短/改写 headline；复杂指令可再走 LLM
        headline = scene.headline
        body = scene.body
        if "短" in instruction or "精简" in instruction:
            headline = headline[:18] + ("…" if len(headline) > 18 else "")
            body = body[:40]
        elif "长" in instruction:
            body = (body + " " + instruction).strip()[:200]
        else:
            # 把指令当作新字幕主体
            headline = instruction[:80] if len(instruction) <= 80 else instruction[:77] + "…"
        scenes.append(
            scene.model_copy(
                update={
                    "headline": headline,
                    "body": body,
                }
            )
        )
    data = storyboard.model_dump()
    data["scenes"] = [s.model_dump() for s in scenes]
    data["version"] = storyboard.version + 1
    return validate_storyboard(data)


def patch_storyboard(
    storyboard: Storyboard,
    *,
    patches: dict[str, Any],
) -> Storyboard:
    """浅合并 patch（title/templateId/scenes 等）。"""
    data = storyboard.model_dump()
    for key, value in patches.items():
        if key == "scenes" and isinstance(value, list):
            data["scenes"] = value
        elif key in data and key != "version":
            data[key] = value
    data["version"] = int(data.get("version") or 1) + 1
    return validate_storyboard(data)


def _plan_with_llm(
    prompt: str,
    *,
    template_id: TemplateId,
    brand_notes: str,
    knowledge_hint: str,
) -> Storyboard | None:
    try:
        llm = LLMClient()
    except ValueError:
        return None

    system = (
        "你是短视频分镜导演。只输出 JSON，不要 Markdown。"
        "字段：title, templateId, aspectRatio, fps, scenes[], brandNotes。"
        "scenes 每项：id, index, durationSec(2-5), headline, body, visualHint, bgColor, accentColor。"
        "竖屏 9:16，镜头 3～6 个，总时长约 12～24 秒。"
        "颜色用 #RRGGBB。"
    )
    user_parts = [
        f"用户一句话：{prompt}",
        f"模板：{template_id}",
    ]
    if brand_notes.strip():
        user_parts.append(f"品牌备注：{brand_notes.strip()}")
    if knowledge_hint.strip():
        user_parts.append(f"知识库约束（必须遵守）：{knowledge_hint.strip()[:1200]}")

    content = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ]
    ).strip()
    # 去掉偶发 ```json 包裹
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    if not content:
        return None
    raw = json.loads(content)
    raw.setdefault("prompt", prompt)
    raw.setdefault("templateId", template_id)
    raw.setdefault("brandNotes", brand_notes)
    for i, sc in enumerate(raw.get("scenes") or []):
        if not sc.get("id"):
            sc["id"] = f"sc_{i}"
        sc.setdefault("index", i)
    return validate_storyboard(raw)


def _plan_with_rules(
    prompt: str,
    *,
    template_id: TemplateId,
    brand_notes: str,
    knowledge_hint: str,
) -> Storyboard:
    """无 LLM 时的确定性分镜（演示/评测可用）。"""
    bg, accent = _PALETTES.get(template_id, _PALETTES["talking-captions"])
    title = _short_title(prompt)
    beats = _split_beats(prompt)
    if knowledge_hint.strip():
        beats.append(f"规范：{knowledge_hint.strip()[:40]}")

    scenes: list[Scene] = []
    for i, beat in enumerate(beats[:5]):
        scenes.append(
            Scene(
                id=new_id("sc"),
                index=i,
                durationSec=3.5 if i == 0 else 3.0,
                headline=beat[:40],
                body=(brand_notes or "AI 短视频 · Remotion/模板渲染")[:80],
                visualHint="大字幕居中" if template_id != "kinetic-text" else "快切文字",
                bgColor=bg,
                accentColor=accent,
            )
        )
    if len(scenes) < 3:
        while len(scenes) < 3:
            idx = len(scenes)
            scenes.append(
                Scene(
                    id=new_id("sc"),
                    index=idx,
                    durationSec=3.0,
                    headline=["开始创作", "打磨表达", "一键导出"][idx],
                    body=title[:60],
                    visualHint="字幕",
                    bgColor=bg,
                    accentColor=accent,
                )
            )

    return Storyboard(
        version=1,
        title=title,
        prompt=prompt,
        templateId=template_id,
        aspectRatio="9:16",
        fps=30,
        scenes=scenes,
        brandNotes=brand_notes or "",
    )


def _short_title(prompt: str) -> str:
    t = re.sub(r"\s+", " ", prompt).strip()
    return t[:24] + ("…" if len(t) > 24 else "")


def _split_beats(prompt: str) -> list[str]:
    parts = re.split(r"[，。！？；、\n]+", prompt)
    beats = [p.strip() for p in parts if p.strip()]
    if not beats:
        return [prompt[:40] or "创意短视频"]
    if len(beats) == 1:
        # 拆成开场 / 核心 / 收尾
        core = beats[0]
        return [f"开场：{core[:20]}", core[:40], "马上分享给你"]
    return beats
