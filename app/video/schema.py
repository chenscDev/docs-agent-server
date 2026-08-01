"""Storyboard JSON Schema（结构化分镜，供 Remotion / FFmpeg 渲染）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


TemplateId = Literal["talking-captions", "kinetic-text", "brand-intro"]
GenerationType = Literal["narration", "kinetic", "brand", "visual-cut"]


class Scene(BaseModel):
    """单个镜头。"""

    id: str = Field(..., min_length=1)
    index: int = Field(..., ge=0)
    durationSec: float = Field(3.0, ge=1.0, le=15.0)
    headline: str = Field(..., min_length=1, max_length=80)
    body: str = Field("", max_length=200)
    visualHint: str = Field("", max_length=120)
    bgColor: str = Field("#0F172A", pattern=r"^#[0-9A-Fa-f]{6}$")
    accentColor: str = Field("#38BDF8", pattern=r"^#[0-9A-Fa-f]{6}$")
    # 渲染后写入：分镜缩略图相对/绝对 URL
    thumbUrl: str = Field("", max_length=500)
    # 分镜配图（用户上传或 URL）；空则走色块模板
    imageUrl: str = Field("", max_length=500)
    # 分镜短视频素材；有值时优先于 imageUrl
    videoUrl: str = Field("", max_length=500)
    # 从原视频第几秒开始播（连续分镜错开，避免每镜都从 0 秒重头播）
    videoTrimStartSec: float = Field(0.0, ge=0.0, le=600.0)
    # 知识库引用：编号（与 hint 中 [n] 对应）
    sourceIndex: int | None = Field(None, ge=1, le=32)
    # 知识库 chunk id（便于端上跳转原文）
    sourceChunkId: str = Field("", max_length=64)
    # 展示用来源标签，如「[1] 品牌手册」
    sourceLabel: str = Field("", max_length=120)
    # 写入口播的引用短句（便于端上展示「引用」）
    sourceSnippet: str = Field("", max_length=120)


class Storyboard(BaseModel):
    """完整分镜（渲染输入）。"""

    version: int = Field(1, ge=1)
    title: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1, max_length=2000)
    templateId: TemplateId = "talking-captions"
    # 生成类型：决定是否口播、默认模板与端上设置项
    generationType: GenerationType = "narration"
    aspectRatio: Literal["9:16", "16:9", "1:1"] = "9:16"
    fps: int = Field(30, ge=24, le=60)
    scenes: list[Scene] = Field(..., min_length=1, max_length=12)
    brandNotes: str = Field("", max_length=500)
    # 品牌 Logo 叠层（成片角标）
    logoUrl: str = Field("", max_length=500)
    logoPosition: Literal[
        "top-right", "top-left", "bottom-right", "bottom-left"
    ] = "top-right"
    # 口播字幕条位置（主要影响 talking-captions）
    captionPosition: Literal["bottom", "top", "center"] = "bottom"
    # 渲染选项（客户端可改；缺省走服务端配置）
    speechRate: float = Field(1.0, ge=0.5, le=2.0)
    bgmEnabled: bool = True
    bgmVolume: float = Field(0.18, ge=0.0, le=1.0)
    # BGM 曲库 id（soft-pink / bright-pulse / warm-pad / off）
    bgmTrackId: str = Field("soft-pink", max_length=64)
    # 口播音色 id（见 tts_catalog）
    ttsVoice: str = Field("longxiaochun_v2", max_length=64)
    # 是否合成 TTS 口播（纯画面剪辑为 False）
    ttsEnabled: bool = True
    # 合规警告（禁词改写等，仅展示不参与渲染）
    complianceWarnings: list[str] = Field(default_factory=list)

    @field_validator("scenes")
    @classmethod
    def _sorted_scenes(cls, scenes: list[Scene]) -> list[Scene]:
        return sorted(scenes, key=lambda s: s.index)

    @property
    def total_duration_sec(self) -> float:
        return float(sum(s.durationSec for s in self.scenes))

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def validate_storyboard(data: dict[str, Any] | Storyboard) -> Storyboard:
    """校验并规范化分镜；失败抛 ValidationError。"""
    if isinstance(data, Storyboard):
        return data
    return Storyboard.model_validate(data)


# 模板元数据（端上展示 / Remotion 构图）
TEMPLATE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "talking-captions",
        "name": "口播字幕条",
        "description": "字幕条滑入 + 左侧强调色，适合一句话口播解说",
        "effectLabel": "字幕滑入",
        "effectHint": "底部字幕条自下而上滑入，口播节奏清晰",
        "coverColor": "#0F172A",
        "defaultAspect": "9:16",
        "defaultBgmTrackId": "soft-pink",
    },
    {
        "id": "kinetic-text",
        "name": "图文快闪",
        "description": "色带 + 标题弹入上移，适合卖点快切罗列",
        "effectLabel": "文字快闪",
        "effectHint": "大标题弹入上移，卖点节奏更醒目",
        "coverColor": "#1E1B4B",
        "defaultAspect": "9:16",
        "defaultBgmTrackId": "bright-pulse",
    },
    {
        "id": "brand-intro",
        "name": "品牌片头",
        "description": "首镜放大入场、末镜收束淡出，适合开场品牌印象",
        "effectLabel": "品牌开场",
        "effectHint": "首镜放大入场、末镜淡出收束，品牌感更强",
        "coverColor": "#022C22",
        "defaultAspect": "9:16",
        "defaultBgmTrackId": "warm-pad",
    },
]

# 生成类型（产品模式）：决定口播开关、默认模板与设置项显隐
# 首位默认：纯画面剪辑
GENERATION_TYPE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "visual-cut",
        "name": "纯画面剪辑",
        "description": "无口播，素材快切 + 配乐，适合氛围片",
        "emoji": "🎬",
        "coverColor": "#334155",
        "defaultTemplateId": "kinetic-text",
        "defaultBgmTrackId": "bright-pulse",
        "ttsEnabled": False,
        "showTts": False,
        "showCaptionPosition": False,
        "showLogo": True,
        "plannerHint": (
            "纯画面剪辑：不要旁白口播；headline 极短标题（≤10字），"
            "body 必须为空；靠素材节奏与配乐表达，不要写长解说。"
        ),
    },
    {
        "id": "narration",
        "name": "口播解说",
        "description": "字幕条 + 旁白口播，适合讲故事/种草",
        "emoji": "🗣️",
        "coverColor": "#0F172A",
        "defaultTemplateId": "talking-captions",
        "defaultBgmTrackId": "soft-pink",
        "ttsEnabled": True,
        "showTts": True,
        "showCaptionPosition": True,
        "showLogo": True,
        "plannerHint": (
            "口播解说：headline 口语短句，body 为旁白；"
            "须能在本镜时长内正常语速说完。"
        ),
    },
    {
        "id": "kinetic",
        "name": "图文快闪",
        "description": "大标题快切弹入，轻口播或短句旁白",
        "emoji": "⚡",
        "coverColor": "#1E1B4B",
        "defaultTemplateId": "kinetic-text",
        "defaultBgmTrackId": "bright-pulse",
        "ttsEnabled": True,
        "showTts": True,
        "showCaptionPosition": False,
        "showLogo": True,
        "plannerHint": (
            "图文快闪：headline 极短有力（≤14字），body 可空或一句短旁白；"
            "durationSec 偏 2～3 秒。"
        ),
    },
    {
        "id": "brand",
        "name": "品牌片头",
        "description": "开场放大、收束口号，适合品牌印象",
        "emoji": "✨",
        "coverColor": "#022C22",
        "defaultTemplateId": "brand-intro",
        "defaultBgmTrackId": "warm-pad",
        "ttsEnabled": True,
        "showTts": True,
        "showCaptionPosition": False,
        "showLogo": True,
        "plannerHint": (
            "品牌片头：第1镜偏品牌开场，末镜收束口号；"
            "中间镜讲卖点，可结合 Logo/品牌备注。"
        ),
    },
]


def resolve_generation_type(type_id: str | None) -> dict[str, Any]:
    """解析生成类型；未知则回落纯画面剪辑（目录首位）。"""
    tid = (type_id or "").strip() or "visual-cut"
    for item in GENERATION_TYPE_CATALOG:
        if item["id"] == tid:
            return item
    return GENERATION_TYPE_CATALOG[0]


def apply_generation_type_defaults(
    data: dict[str, Any],
    generation_type: str | None = None,
) -> dict[str, Any]:
    """按生成类型补齐 templateId / ttsEnabled / 默认 BGM。"""
    out = dict(data or {})
    gtid = generation_type or out.get("generationType") or "visual-cut"
    meta = resolve_generation_type(str(gtid))
    out["generationType"] = meta["id"]
    out["templateId"] = meta["defaultTemplateId"]
    out["ttsEnabled"] = bool(meta.get("ttsEnabled", True))
    if not out.get("bgmTrackId"):
        out["bgmTrackId"] = meta.get("defaultBgmTrackId") or "soft-pink"
    # 纯画面默认开配乐
    if meta["id"] == "visual-cut":
        out["bgmEnabled"] = True
        if out.get("bgmTrackId") == "off":
            out["bgmTrackId"] = meta.get("defaultBgmTrackId") or "bright-pulse"
    return out
