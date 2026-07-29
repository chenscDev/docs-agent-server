"""Storyboard JSON Schema（结构化分镜，供 Remotion / FFmpeg 渲染）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


TemplateId = Literal["talking-captions", "kinetic-text", "brand-intro"]


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


class Storyboard(BaseModel):
    """完整分镜（渲染输入）。"""

    version: int = Field(1, ge=1)
    title: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1, max_length=2000)
    templateId: TemplateId = "talking-captions"
    aspectRatio: Literal["9:16", "16:9", "1:1"] = "9:16"
    fps: int = Field(30, ge=24, le=60)
    scenes: list[Scene] = Field(..., min_length=1, max_length=12)
    brandNotes: str = Field("", max_length=500)
    # 品牌 Logo 叠层（成片角标）
    logoUrl: str = Field("", max_length=500)
    logoPosition: Literal["top-right", "top-left", "bottom-right"] = "top-right"
    # 渲染选项（客户端可改；缺省走服务端配置）
    speechRate: float = Field(1.0, ge=0.5, le=2.0)
    bgmEnabled: bool = True
    bgmVolume: float = Field(0.18, ge=0.0, le=1.0)

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


# 模板元数据（端上展示）
TEMPLATE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "talking-captions",
        "name": "口播字幕条",
        "description": "底部字幕条 + 左侧强调色，适合一句话口播解说",
        "coverColor": "#0F172A",
        "defaultAspect": "9:16",
    },
    {
        "id": "kinetic-text",
        "name": "图文快闪",
        "description": "顶部色带 + 大号标题弹出，适合卖点快切罗列",
        "coverColor": "#1E1B4B",
        "defaultAspect": "9:16",
    },
    {
        "id": "brand-intro",
        "name": "品牌片头",
        "description": "居中描边品牌框 + 渐入，适合开场品牌印象",
        "coverColor": "#022C22",
        "defaultAspect": "9:16",
    },
]
