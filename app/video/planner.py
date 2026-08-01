"""分镜规划：优先 LLM 结构化输出，失败则规则兜底。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.ids import new_id
from app.core.llm import LLMClient
from app.video.materials import (
    attach_materials_to_scenes,
    clamp_storyboard_duration,
    normalize_materials,
    parse_requested_duration_sec,
    target_scene_count,
    target_total_duration_sec,
)
from app.video.schema import (
    Scene,
    Storyboard,
    TemplateId,
    apply_generation_type_defaults,
    resolve_generation_type,
    validate_storyboard,
)

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
    generation_type: str | None = None,
    brand_notes: str = "",
    knowledge_hint: str = "",
    materials: list[dict[str, Any]] | None = None,
    prefer_rules: bool = False,
    industry_hint: str = "",
) -> Storyboard:
    """根据一句话生成分镜；LLM 不可用或校验失败时走规则模板。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")

    gmeta = resolve_generation_type(generation_type)
    # 生成类型锁定默认模板（客户端传了 template 也以类型为准，避免错配）
    template_id = gmeta["defaultTemplateId"]  # type: ignore[assignment]
    type_hint = str(gmeta.get("plannerHint") or "")
    extra = (industry_hint or "").strip()
    if extra:
        type_hint = f"{type_hint} {extra}".strip()

    mats = normalize_materials(materials)
    board: Storyboard | None = None
    if not prefer_rules:
        try:
            board = _plan_with_llm(
                prompt,
                template_id=template_id,
                brand_notes=brand_notes,
                knowledge_hint=knowledge_hint,
                materials=mats,
                generation_hint=type_hint,
                generation_type=str(gmeta["id"]),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 分镜失败，回退规则: %s", exc)
            board = None

    if board is None:
        board = _plan_with_rules(
            prompt,
            template_id=template_id,
            brand_notes=brand_notes,
            knowledge_hint=knowledge_hint,
            materials=mats,
            generation_type=str(gmeta["id"]),
        )
    board = attach_materials_to_scenes(board, mats)
    # 写回生成类型与 TTS 开关
    data = apply_generation_type_defaults(
        board.model_dump(), generation_type=str(gmeta["id"])
    )
    # 纯画面：清空 body，避免残留口播文案进字幕
    if not bool(gmeta.get("ttsEnabled", True)):
        for sc in data.get("scenes") or []:
            sc["body"] = ""
            hl = (sc.get("headline") or "")[:10]
            sc["headline"] = hl or "画面"
    board = validate_storyboard(data)
    # 默认成片 10～15 秒；描述里写了明确时长则尊重该目标
    target = target_total_duration_sec(mats, prompt)
    requested = parse_requested_duration_sec(prompt)
    max_sec = None if requested is not None else 15.0
    return clamp_storyboard_duration(
        board, target_sec=target, max_sec=max_sec
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


def compress_storyboard_duration(
    storyboard: Storyboard,
    *,
    target_sec: float,
) -> Storyboard:
    """
    按目标总时长等比缩放各镜 durationSec（夹在 1～15 秒）。
    用于创作助手「压到约 Ns」快捷能力。
    """
    scenes = list(storyboard.scenes)
    if not scenes:
        return storyboard
    target = max(3.0, min(float(target_sec), 90.0))
    current = float(sum(s.durationSec for s in scenes)) or 1.0
    ratio = target / current
    next_scenes = []
    for s in scenes:
        dur = max(1.0, min(15.0, round(float(s.durationSec) * ratio, 1)))
        next_scenes.append(s.model_copy(update={"durationSec": dur}))
    # 微调末镜，使总和更接近目标
    total = float(sum(s.durationSec for s in next_scenes))
    delta = round(target - total, 1)
    if abs(delta) >= 0.1:
        last = next_scenes[-1]
        next_scenes[-1] = last.model_copy(
            update={
                "durationSec": max(1.0, min(15.0, round(last.durationSec + delta, 1)))
            }
        )
    data = storyboard.model_dump()
    data["scenes"] = [s.model_dump() for s in next_scenes]
    data["version"] = int(data.get("version") or 1) + 1
    return validate_storyboard(data)


def _plan_with_llm(
    prompt: str,
    *,
    template_id: TemplateId,
    brand_notes: str,
    knowledge_hint: str,
    materials: list[dict[str, str]] | None = None,
    generation_hint: str = "",
    generation_type: str = "narration",
) -> Storyboard | None:
    try:
        llm = LLMClient()
    except ValueError:
        return None

    mats = normalize_materials(materials)
    scene_n = target_scene_count(mats, default=4, generation_type=generation_type)
    total_sec = target_total_duration_sec(mats, prompt)
    per_sec = round(total_sec / max(1, scene_n), 1)
    requested = parse_requested_duration_sec(prompt)
    dur_rule = (
        f"用户明确要求约 {requested:.0f} 秒，请按此总时长规划。"
        if requested is not None
        else "默认总时长约 10～15 秒（目标约 12 秒），除非用户写了秒数否则不要超过 15 秒。"
    )
    system = (
        "你是短视频分镜导演。只输出 JSON，不要 Markdown。"
        "字段：title, templateId, aspectRatio, fps, scenes[], brandNotes, logoUrl。"
        "scenes 每项：id, index, durationSec, headline, body, visualHint, "
        "bgColor, accentColor, imageUrl, videoUrl, sourceIndex。"
        "imageUrl/videoUrl 默认可留空（服务端会按素材列表自动填充）。"
        f"竖屏 9:16，镜头约 {scene_n} 个，总时长约 {total_sec:.0f} 秒"
        f"（每镜 durationSec 约 {per_sec}，范围 2～5）。"
        f"{dur_rule}"
        "颜色用 #RRGGBB。"
        "文案语气：生活化口语，像朋友发朋友圈/短视频口播，可以俏皮、调侃、抓反差；"
        "少用「培养」「陪伴成长」「温馨时光」「品质生活」等正式广告腔。"
        "若画面里孩子调皮、爬人、搞怪，要突出好笑/调皮的一面，不要写成严肃育儿。"
        "若提供知识库约束（带 [n] 编号）："
        "1) 卖点/合规句必须改写自这些条目，禁止编造未出现的承诺；"
        "2) 每个镜头必须填 sourceIndex=所用条目编号；"
        "3) headline/body 要能对应到该编号内容。"
        "若提供用户素材列表：按素材顺序讲故事；"
        "若素材带「内容描述」：headline/body/visualHint 必须贴合该描述的可见内容与情绪，"
        "禁止编造素材里没有的物体或情节；无内容描述时 visualHint 简述该素材如何出镜。"
        "按模板差异化："
        "talking-captions→headline 口语短句（≤16字）、body 口播按镜长估算（约每秒 3～4 字），须能正常语速说完；"
        "kinetic-text→headline 极短有力（≤14字）、body 可空或一句、durationSec 偏 2～3；"
        "brand-intro→第1镜偏品牌开场（稍长）、末镜收束口号，中间镜讲卖点。"
    )
    if generation_hint.strip():
        system += f"当前生成类型要求：{generation_hint.strip()}"
    user_parts = [
        f"用户一句话：{prompt}",
        f"模板：{template_id}",
        f"生成类型：{generation_type}",
        f"建议镜头数：{scene_n}",
    ]
    if brand_notes.strip():
        user_parts.append(f"品牌备注：{brand_notes.strip()}")
    if knowledge_hint.strip():
        user_parts.append(
            "知识库约束（必须遵守，按编号引用）：\n"
            + knowledge_hint.strip()[:1600]
        )
    if mats:
        lines = []
        for i, m in enumerate(mats):
            cap = (m.get("caption") or "").strip()
            if cap:
                lines.append(
                    f"[{i + 1}] kind={m['kind']} 内容描述：{cap}"
                )
            else:
                lines.append(
                    f"[{i + 1}] kind={m['kind']} url={m['url'][:120]}"
                )
        user_parts.append(
            "用户上传素材（按顺序对应镜头，服务端会写入 imageUrl/videoUrl）：\n"
            + "\n".join(lines)
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
    materials: list[dict[str, str]] | None = None,
    generation_type: str = "narration",
) -> Storyboard:
    """无 LLM 时的确定性分镜（演示/评测可用）。"""
    bg, accent = _PALETTES.get(template_id, _PALETTES["talking-captions"])
    title = _short_title(prompt)
    mats = normalize_materials(materials)
    no_tts = generation_type == "visual-cut"
    kb_beats = _parse_kb_beats(knowledge_hint)
    if kb_beats:
        beats = [b["text"] for b in kb_beats[:5]]
        source_indices = [b["index"] for b in kb_beats[:5]]
    else:
        beats = _split_beats(prompt)
        source_indices = [None] * len(beats)

    want = target_scene_count(
        mats,
        default=min(5, max(3, len(beats))),
        generation_type=generation_type,
    )
    # 镜头数对齐素材：不够则循环文案节拍
    while len(beats) < want:
        beats.append(beats[len(beats) % max(1, len(_split_beats(prompt)))][:40])
        source_indices.append(None)
    beats = beats[:want]
    source_indices = source_indices[:want]
    total_sec = target_total_duration_sec(mats, prompt)
    base_dur = round(total_sec / max(1, want), 2)

    scenes: list[Scene] = []
    for i, beat in enumerate(beats):
        src_idx = source_indices[i] if i < len(source_indices) else None
        if no_tts or template_id == "kinetic-text":
            duration = min(3.0, max(2.0, base_dur))
            headline = beat[:10] if no_tts else beat[:14]
            body = ""
            hint = "纯画面快切" if no_tts else "快切大标题弹入"
        elif template_id == "brand-intro":
            duration = min(4.5, max(2.5, base_dur + (0.4 if i == 0 else 0)))
            headline = beat[:36]
            body = (brand_notes or "品牌印象 · AI 成片")[:60]
            hint = (
                "品牌框开场"
                if i == 0
                else ("品牌框收束" if i == len(beats) - 1 else "品牌框卖点")
            )
        else:
            duration = min(4.5, max(2.5, base_dur))
            headline = beat[:40]
            # 规则兜底也尽量口语化，避免广告腔
            body = (brand_notes or beat[:80] or "这画面也太真实了吧")[:80]
            hint = "底部口播字幕条滑入"
        if mats:
            mat = mats[i % len(mats)]
            kind = mat["kind"]
            cap = (mat.get("caption") or "").strip()
            if cap:
                hint = cap[:120]
                if no_tts:
                    headline = (cap[:10] or headline)
                    body = ""
                # 有内容描述时，规则兜底也尽量把口播贴近画面
                elif not body or body == (brand_notes or beat[:80] or "口播解说 · 字幕条")[:80]:
                    body = cap[:80]
                if template_id == "kinetic-text" and not no_tts:
                    headline = (cap[:14] or headline)
                elif len(headline) < 4:
                    headline = cap[:36]
            else:
                hint = "用户短视频素材" if kind == "video" else "用户图片素材"
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
        generationType=generation_type,  # type: ignore[arg-type]
        aspectRatio="9:16",
        fps=30,
        scenes=scenes,
        brandNotes=brand_notes or "",
        ttsEnabled=not no_tts,
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
