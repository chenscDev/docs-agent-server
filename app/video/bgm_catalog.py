"""BGM 曲库：内置轨 + 用户上传自定义配乐；优先用本地文件，缺文件时回退 lavfi。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "bgm"
_REGISTRY_NAME = "registry.json"
_MAX_BGM_BYTES = 20 * 1024 * 1024  # 20MB
_ALLOWED_EXT = frozenset({".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"})

# id → 展示名 / 描述 / 推荐模板 / 本地文件 / lavfi 兜底
BGM_TRACKS: list[dict[str, Any]] = [
    {
        "id": "soft-pink",
        "name": "柔和铺底",
        "description": "三和弦轻垫，适合口播解说",
        "mood": "轻松 · 解说",
        "templates": ["talking-captions", "brand-intro", "kinetic-text"],
        "defaultFor": ["talking-captions"],
        "file": "soft-pink.wav",
        "lavfi": "anoisesrc=color=pink:amplitude=0.32",
        "afExtra": "lowpass=f=1200",
        "volume": 0.14,
    },
    {
        "id": "bright-pulse",
        "name": "轻快脉冲",
        "description": "带轻微颤音，适合卖点快闪",
        "mood": "轻快 · 节奏",
        "templates": ["kinetic-text", "talking-captions"],
        "defaultFor": ["kinetic-text"],
        "file": "bright-pulse.wav",
        "lavfi": "sine=frequency=220:sample_rate=16000",
        "afExtra": "tremolo=f=4:d=0.35,lowpass=f=900,highpass=f=80",
        "volume": 0.11,
    },
    {
        "id": "warm-pad",
        "name": "暖垫氛围",
        "description": "偏低暖色和弦，适合品牌开场",
        "mood": "温暖 · 品牌",
        "templates": ["brand-intro", "talking-captions"],
        "defaultFor": ["brand-intro"],
        "file": "warm-pad.wav",
        "lavfi": "sine=frequency=110:sample_rate=16000",
        "afExtra": "tremolo=f=0.35:d=0.55,lowpass=f=600",
        "volume": 0.13,
    },
    {
        "id": "off",
        "name": "无配乐",
        "description": "仅口播，不混 BGM",
        "mood": "静音",
        "templates": ["talking-captions", "kinetic-text", "brand-intro"],
        "defaultFor": [],
        "file": "",
        "lavfi": "",
        "afExtra": "",
        "volume": 0.0,
    },
]


def bgm_assets_dir() -> Path:
    return _ASSETS_DIR


def custom_bgm_dir() -> Path:
    """用户上传配乐目录：assets/bgm/custom/。"""
    dest = _ASSETS_DIR / "custom"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _registry_path() -> Path:
    return custom_bgm_dir() / _REGISTRY_NAME


def _load_custom_registry() -> list[dict[str, Any]]:
    path = _registry_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取自定义 BGM registry 失败: %s", exc)
        return []
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for row in raw:
        if isinstance(row, dict) and str(row.get("id") or "").startswith("custom-"):
            items.append(row)
    return items


def _save_custom_registry(items: list[dict[str, Any]]) -> None:
    path = _registry_path()
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _infer_audio_ext(filename: str | None, content_type: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in _ALLOWED_EXT:
        return ext
    ct = (content_type or "").lower()
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "wav" in ct:
        return ".wav"
    if "mp4" in ct or "m4a" in ct or "aac" in ct:
        return ".m4a"
    if "ogg" in ct:
        return ".ogg"
    if "flac" in ct:
        return ".flac"
    return ""


def _display_name_from_filename(filename: str | None) -> str:
    stem = Path(filename or "").stem.strip()
    if not stem:
        return "自定义配乐"
    # 去掉过长或奇怪字符，保留可读标题
    cleaned = re.sub(r"[\s_]+", " ", stem).strip()
    return (cleaned[:40] or "自定义配乐")


def save_custom_bgm(
    *,
    data: bytes,
    filename: str | None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """保存上传配乐并写入 registry；返回列表项形态（含 custom/previewUrl）。"""
    if not data:
        raise ValueError("空文件")
    if len(data) > _MAX_BGM_BYTES:
        raise ValueError(f"配乐超过 {_MAX_BGM_BYTES // (1024 * 1024)}MB 限制")

    ext = _infer_audio_ext(filename, content_type)
    if ext not in _ALLOWED_EXT:
        raise ValueError("仅支持 mp3/wav/m4a/aac/ogg/flac 音频")

    # 统一为 custom-xxxxxxxxxxxx，便于 resolve / 前端识别
    track_id = f"custom-{uuid.uuid4().hex[:12]}"
    out_name = f"{track_id}{ext}"
    dest = custom_bgm_dir() / out_name
    dest.write_bytes(data)

    name = _display_name_from_filename(filename)
    record: dict[str, Any] = {
        "id": track_id,
        "name": name,
        "description": "用户上传的自定义配乐",
        "mood": "自定义",
        "templates": ["talking-captions", "kinetic-text", "brand-intro"],
        "defaultFor": [],
        "file": out_name,
        "lavfi": "",
        "afExtra": "lowpass=f=1200",
        "volume": 0.14,
        "custom": True,
    }

    registry = _load_custom_registry()
    registry = [r for r in registry if str(r.get("id")) != track_id]
    registry.insert(0, record)
    _save_custom_registry(registry)
    logger.info("saved custom bgm id=%s bytes=%s", track_id, len(data))
    return _to_list_item(record, template_id=None, is_custom=True)


def resolve_track_file(track: dict[str, Any]) -> Path | None:
    """解析曲目本地文件；自定义轨在 custom/ 下；不存在则 None。"""
    name = str(track.get("file") or "").strip()
    if not name:
        return None
    path = Path(name)
    if path.is_absolute():
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None

    # 自定义：优先 custom/ 目录
    is_custom = bool(track.get("custom")) or str(track.get("id") or "").startswith(
        "custom-"
    )
    candidates: list[Path] = []
    if is_custom:
        candidates.append(custom_bgm_dir() / Path(name).name)
        # 兼容 registry 里写成 custom/xxx
        if name.startswith("custom/"):
            candidates.append(_ASSETS_DIR / name)
    candidates.append(_ASSETS_DIR / Path(name).name)
    if "/" in name:
        candidates.append(_ASSETS_DIR / name)

    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _to_list_item(
    t: dict[str, Any],
    *,
    template_id: str | None,
    is_custom: bool,
) -> dict[str, Any]:
    tid = (template_id or "").strip()
    has_file = resolve_track_file(t) is not None
    track_id = str(t["id"])
    preview_url = ""
    if has_file and track_id != "off":
        fname = Path(str(t.get("file") or "").strip()).name
        if fname:
            if is_custom:
                # 与 StaticFiles(/cdn/video/bgm) + custom/ 子目录对齐
                preview_url = f"/cdn/video/bgm/custom/{fname}"
            else:
                preview_url = f"/cdn/video/bgm/{fname}"
    return {
        "id": track_id,
        "name": t["name"],
        "description": t.get("description") or "",
        "templates": list(t.get("templates") or []),
        "isDefault": tid in (t.get("defaultFor") or []),
        "hasFile": has_file,
        "previewUrl": preview_url,
        "mood": str(t.get("mood") or ""),
        "custom": is_custom,
    }


def list_bgm_tracks(*, template_id: str | None = None) -> list[dict[str, Any]]:
    """列出曲目：自定义在前，再内置；可按模板过滤内置推荐。"""
    tid = (template_id or "").strip()
    items: list[dict[str, Any]] = []

    # 自定义轨始终置顶展示
    for t in _load_custom_registry():
        items.append(_to_list_item(t, template_id=tid, is_custom=True))

    for t in BGM_TRACKS:
        if tid and tid not in (t.get("templates") or []):
            continue
        items.append(_to_list_item(t, template_id=tid, is_custom=False))
    return items


def default_bgm_track_id(template_id: str) -> str:
    for t in BGM_TRACKS:
        if template_id in (t.get("defaultFor") or []):
            return str(t["id"])
    return "bright-pulse"


def resolve_bgm_track(track_id: str | None) -> dict[str, Any] | None:
    """按 id 解析曲目；支持 custom-*；未知则回退 bright-pulse。"""
    tid = (track_id or "").strip() or "bright-pulse"
    if tid == "off":
        for t in BGM_TRACKS:
            if t["id"] == "off":
                return t
        return None

    if tid.startswith("custom-"):
        for t in _load_custom_registry():
            if str(t.get("id")) == tid:
                # 渲染侧需要完整字段
                out = dict(t)
                out["custom"] = True
                return out
        logger.warning("自定义 BGM 不存在: %s，回退 bright-pulse", tid)
        tid = "bright-pulse"

    for t in BGM_TRACKS:
        if t["id"] == tid:
            return t

    for t in BGM_TRACKS:
        if t["id"] == "bright-pulse":
            return t
    return BGM_TRACKS[0] if BGM_TRACKS else None
