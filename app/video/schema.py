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
        "description": "竖屏大字幕 + 底部说明，适合一句话口播",
        "coverColor": "#0F172A",
        "defaultAspect": "9:16",
    },
    {
        "id": "kinetic-text",
        "name": "图文快闪",
        "description": "短句快切、强节奏，适合卖点罗列",
        "coverColor": "#1E1B4B",
        "defaultAspect": "9:16",
    },
    {
        "id": "brand-intro",
        "name": "品牌片头",
        "description": "标题强调 + 品牌色块，适合开场",
        "coverColor": "#022C22",
        "defaultAspect": "9:16",
    },
]
