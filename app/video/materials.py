"""多素材（图/短视频）规范化与分镜绑定。"""

from __future__ import annotations

from typing import Any, Literal

from app.video.schema import Storyboard, validate_storyboard

MaterialKind = Literal["image", "video"]

_MAX_MATERIALS = 9


def normalize_materials(raw: list[Any] | None) -> list[dict[str, str]]:
    """
    规范化客户端 materials。

    每项：{ url, kind: image|video }
    最多 9 条；非法项丢弃。
    """
    if not raw:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in ("image", "video"):
            # 按后缀猜测
            lower = url.lower().split("?")[0]
            if any(lower.endswith(ext) for ext in (".mp4", ".webm", ".mov")):
                kind = "video"
            else:
                kind = "image"
        # 轻量白名单：本站 assets 或 http(s)
        if not (
            url.startswith("/cdn/video/")
            or url.startswith("http://")
            or url.startswith("https://")
        ):
            continue
        seen.add(url)
        out.append({"url": url[:500], "kind": kind})
        if len(out) >= _MAX_MATERIALS:
            break
    return out


def attach_materials_to_scenes(
    board: Storyboard,
    materials: list[dict[str, str]] | None,
) -> Storyboard:
    """
    按顺序把素材写入各镜：video → videoUrl，image → imageUrl。

    - 素材多于镜头：只绑定前 N 镜
    - 镜头多于素材：循环复用素材
    - 无素材：原样返回
    """
    mats = normalize_materials(materials)
    if not mats:
        return board
    data = board.model_dump()
    scenes = list(data.get("scenes") or [])
    if not scenes:
        return board
    n = len(mats)
    for i, sc in enumerate(scenes):
        mat = mats[i % n]
        url = mat["url"]
        if mat["kind"] == "video":
            sc["videoUrl"] = url
            # 有视频时清空配图，避免渲染歧义
            sc["imageUrl"] = ""
        else:
            sc["imageUrl"] = url
            sc["videoUrl"] = ""
        # 补充画面说明，便于 Remix / 生图
        if not (sc.get("visualHint") or "").strip():
            sc["visualHint"] = (
                "用户短视频素材" if mat["kind"] == "video" else "用户图片素材"
            )[:120]
    data["scenes"] = scenes
    return validate_storyboard(data)


def target_scene_count(materials: list[dict[str, str]] | None, default: int = 4) -> int:
    """有素材时镜头数贴近素材数，夹在 3～9。"""
    mats = normalize_materials(materials)
    if not mats:
        return max(3, min(6, default))
    return max(3, min(_MAX_MATERIALS, len(mats)))
