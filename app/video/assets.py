"""视频素材上传：分镜配图 / Logo，落盘到 video_out/assets。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import get_settings
from app.core.ids import new_id

logger = logging.getLogger(__name__)

_ALLOWED_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_MAX_BYTES = 8 * 1024 * 1024  # 8MB


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


def save_uploaded_asset(
    *,
    data: bytes,
    filename: str | None,
    content_type: str | None = None,
) -> dict[str, str]:
    """校验并保存上传文件，返回 {id, url, filename}。"""
    if not data:
        raise ValueError("空文件")
    if len(data) > _MAX_BYTES:
        raise ValueError(f"文件超过 {_MAX_BYTES // (1024 * 1024)}MB 限制")

    ext = Path(filename or "").suffix.lower()
    if ext not in _ALLOWED_EXT:
        # 尝试从 content-type 推断
        ct = (content_type or "").lower()
        if "png" in ct:
            ext = ".png"
        elif "jpeg" in ct or "jpg" in ct:
            ext = ".jpg"
        elif "webp" in ct:
            ext = ".webp"
        elif "gif" in ct:
            ext = ".gif"
        else:
            raise ValueError("仅支持 png/jpg/webp/gif")

    asset_id = new_id("vass")
    out_name = f"{asset_id}{ext}"
    dest = assets_dir() / out_name
    dest.write_bytes(data)
    logger.info("saved video asset id=%s bytes=%s", asset_id, len(data))
    return {
        "id": asset_id,
        "filename": out_name,
        "url": public_asset_url(out_name),
    }
