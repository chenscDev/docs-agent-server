"""视频素材上传：分镜配图 / 短视频 / Logo，落盘到 video_out/assets。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import get_settings
from app.core.ids import new_id

logger = logging.getLogger(__name__)

_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_VIDEO_EXT = frozenset({".mp4", ".webm", ".mov"})
_ALLOWED_EXT = _IMAGE_EXT | _VIDEO_EXT
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB
_MAX_VIDEO_BYTES = 40 * 1024 * 1024  # 40MB 短视频


def assets_dir() -> Path:
    settings = get_settings()
    root = Path(settings.video_output_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    dest = root / "assets"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def public_asset_url(filename: str) -> str:
    """返回可公开访问的素材 URL（挂载在 /cdn/video）。"""
    name = Path(filename).name
    rel = f"/cdn/video/assets/{name}"
    base = (get_settings().video_public_base_url or "").rstrip("/")
    return f"{base}{rel}" if base else rel


def _infer_ext(filename: str | None, content_type: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in _ALLOWED_EXT:
        return ext
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "mp4" in ct or "mpeg" in ct:
        return ".mp4"
    if "webm" in ct:
        return ".webm"
    if "quicktime" in ct or "mov" in ct:
        return ".mov"
    return ""


def save_uploaded_asset(
    *,
    data: bytes,
    filename: str | None,
    content_type: str | None = None,
) -> dict[str, str]:
    """校验并保存上传文件，返回 {id, url, filename, kind}。"""
    if not data:
        raise ValueError("空文件")

    ext = _infer_ext(filename, content_type)
    if ext not in _ALLOWED_EXT:
        raise ValueError("仅支持 png/jpg/webp/gif 或短视频 mp4/webm/mov")

    kind = "video" if ext in _VIDEO_EXT else "image"
    max_bytes = _MAX_VIDEO_BYTES if kind == "video" else _MAX_IMAGE_BYTES
    if len(data) > max_bytes:
        raise ValueError(f"文件超过 {max_bytes // (1024 * 1024)}MB 限制")

    asset_id = new_id("vass")
    out_name = f"{asset_id}{ext}"
    dest = assets_dir() / out_name
    dest.write_bytes(data)
    logger.info(
        "saved video asset id=%s kind=%s bytes=%s",
        asset_id,
        kind,
        len(data),
    )
    return {
        "id": asset_id,
        "filename": out_name,
        "url": public_asset_url(out_name),
        "kind": kind,
    }
