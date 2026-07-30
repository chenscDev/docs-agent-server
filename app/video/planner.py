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


def _looks_like_refine_command(instruction: str) -> bool:
    """判断是否为「改写指令」而非「直接给出的新文案」。"""
    command_hints = (
        "一点",
        "一下",
        "一些",
        "更",
        "再",
        "请",
        "帮",
        "改成",
        "改为",
        "调整",
        "优化",
        "口语",
        "精简",
        "缩短",
        "加长",
        "详细",
        "展开",
        "压缩",
        "简洁",
    )
    return any(h in instruction for h in command_hints) and len(instruction) < 48


def refine_scene(
    storyboard: Storyboard,
    *,
    scene_id: str,
    instruction: str,
) -> Storyboard:
    """按自然语言局部修改某一镜（默认改正文/口播，不覆盖标题）。"""
    instruction = (instruction or "").strip()
    if not instruction:
        return storyboard

    scenes: list[Scene] = []
    for scene in storyboard.scenes:
        if scene.id != scene_id:
            scenes.append(scene)
            continue
        headline = scene.headline
        body = scene.body or ""
        visual_hint = scene.visualHint or ""
        wants_short = any(k in instruction for k in ("短", "精简", "压缩", "简洁", "缩短"))
        wants_long = any(k in instruction for k in ("长", "详细", "展开", "丰富", "加长"))
        wants_title = any(k in instruction for k in ("标题", "主句", "headline"))
        wants_visual = any(k in instruction for k in ("画面", "视觉", "镜头感"))

        if wants_title and not wants_short and not wants_long:
            # 显式改标题：去掉提示词后写入 headline
            refined = instruction
            for token in ("标题", "主句", "改成", "改为", "改一下", "改成：", "改为："):
                refined = refined.replace(token, " ")
            refined = " ".join(refined.split()).strip(" ：:，,")
            if refined:
                headline = refined[:80]
        elif wants_visual and not wants_short and not wants_long:
            visual_hint = instruction[:120]
        elif wants_short:
            # 「字幕/正文短一点」优先压正文；标题仅在偏长时截断
            if "标题" in instruction and len(headline) > 12:
                headline = headline[:12] + "…"
            elif len(headline) > 24:
                headline = headline[:18] + "…"
            if body:
                # 相对缩短：至少砍到约 60%，且不超过硬上限
                hard = 24 if ("精简" in instruction or "压缩" in instruction) else 32
                target = min(hard, max(12, int(len(body) * 0.6)))
                if len(body) > target:
                    body = body[:target] + "…"
        elif wants_long:
            # 加长：把指令里的补充内容并入正文，不替换标题
            suffix = instruction
            for token in ("长一点", "详细一点", "展开", "更丰富", "加长", "长一些"):
                suffix = suffix.replace(token, "")
            suffix = suffix.strip(" ：:，,")
            body = (body + (" " + suffix if suffix else " " + instruction)).strip()[:200]
        elif _looks_like_refine_command(instruction):
            # 命令型（如「更口语化」）：只做轻量规则，绝不把指令本身写成标题
            if "口语" in instruction and body:
                body = body.replace("，", " ").replace("。", " ").strip()[:200]
            if wants_short:
                body = body[:40]
        else:
            # 内容型：用户直接给出新口播/正文 → 写入 body，保留原标题
            refined = instruction
            for prefix in ("改成", "改为", "改成：", "改为：", "改成:", "改为:"):
                if refined.startswith(prefix):
                    refined = refined[len(prefix) :].strip()
                    break
            body = (refined or instruction)[:200]
        scenes.append(
            scene.model_copy(
                update={
                    "headline": headline,
                    "body": body,
                    "visualHint": visual_hint,
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
    """浅合并 patch；scenes 支持整表替换或按 id 合并字段。"""
    data = storyboard.model_dump()
    for key, value in patches.items():
        if key == "scenes" and isinstance(value, list):
            if value and all(isinstance(x, dict) and x.get("id") for x in value):
                # 若提交了完整镜头列表（含 index），整表替换；否则按 id 合并
                if all("index" in x and "headline" in x for x in value):
                    data["scenes"] = value
                else:
                    by_id = {s["id"]: s for s in data["scenes"]}
                    for item in value:
                        sid = str(item["id"])
                        if sid in by_id:
                            by_id[sid] = {**by_id[sid], **item}
                        else:
                            data["scenes"].append(item)
                    data["scenes"] = list(by_id.values())
            else:
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
        "字段：title, templateId, aspectRatio, fps, scenes[], brandNotes, logoUrl。"
        "scenes 每项：id, index, durationSec(2-5), headline, body, visualHint, "
        "bgColor, accentColor, imageUrl, videoUrl, sourceIndex。"
        "imageUrl/videoUrl 默认可留空；有短视频素材时填 videoUrl，优先于 imageUrl。"
        "竖屏 9:16，镜头 3～6 个，总时长约 12～24 秒。"
        "颜色用 #RRGGBB。"
        "若提供知识库约束（带 [n] 编号）："
        "1) 卖点/合规句必须改写自这些条目，禁止编造未出现的承诺；"
        "2) 每个镜头必须填 sourceIndex=所用条目编号；"
        "3) headline/body 要能对应到该编号内容。"
        "按模板差异化："
        "talking-captions→headline 口语短句、body 稍长便于口播、durationSec 偏 3.5～4.5；"
        "kinetic-text→headline 极短有力（≤14字）、body 可空或一句、durationSec 偏 2～3；"
        "brand-intro→第1镜偏品牌开场（稍长）、末镜收束口号，中间镜讲卖点。"
    )
    user_parts = [
        f"用户一句话：{prompt}",
        f"模板：{template_id}",
    ]
    if brand_notes.strip():
        user_parts.append(f"品牌备注：{brand_notes.strip()}")
    if knowledge_hint.strip():
        user_parts.append(
            "知识库约束（必须遵守，按编号引用）：\n"
            + knowledge_hint.strip()[:1600]
        )

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
    kb_beats = _parse_kb_beats(knowledge_hint)
    if kb_beats:
        # 有知识库时优先用编号条目做镜，强制引用
        beats = [b["text"] for b in kb_beats[:5]]
        source_indices = [b["index"] for b in kb_beats[:5]]
    else:
        beats = _split_beats(prompt)
        source_indices = [None] * len(beats)

    scenes: list[Scene] = []
    for i, beat in enumerate(beats[:5]):
        src_idx = source_indices[i] if i < len(source_indices) else None
        if template_id == "kinetic-text":
            duration = 2.4 if i > 0 else 2.8
            headline = beat[:14]
            body = ""
            hint = "快切大标题弹入"
        elif template_id == "brand-intro":
            duration = 4.2 if i == 0 else (3.8 if i == min(4, len(beats) - 1) else 3.2)
            headline = beat[:36]
            body = (brand_notes or "品牌印象 · AI 成片")[:60]
            hint = "品牌框开场" if i == 0 else ("品牌框收束" if i == min(4, len(beats) - 1) else "品牌框卖点")
        else:
            duration = 4.0 if i == 0 else 3.5
            headline = beat[:40]
            body = (brand_notes or beat[:80] or "口播解说 · 字幕条")[:80]
            hint = "底部口播字幕条滑入"
        scenes.append(
            Scene(
                id=new_id("sc"),
                index=i,
                durationSec=duration,
                headline=headline,
                body=body,
                visualHint=hint,
                bgColor=bg,
                accentColor=accent,
                sourceIndex=src_idx,
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


def _parse_kb_beats(knowledge_hint: str) -> list[dict[str, Any]]:
    """从「[n] 《标题》：摘要」格式解析规则分镜用的卖点。"""
    text = (knowledge_hint or "").strip()
    if not text:
        return []
    beats: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s*(?:《([^》]*)》[：:]?)?\s*(.+)$", line)
        if not m:
            continue
        idx = int(m.group(1))
        title = (m.group(2) or "").strip()
        snip = (m.group(3) or "").strip()
        # 镜头标题优先用摘要前半，带上来源感
        head = snip[:36] if snip else (title or f"要点{idx}")
        beats.append({"index": idx, "text": head, "title": title})
    return beats


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
