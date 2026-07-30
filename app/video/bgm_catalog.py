"""BGM 曲库：按模板绑定短音频；优先用内置 wav/mp3，缺文件时回退 lavfi。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "bgm"

# id → 展示名 / 描述 / 推荐模板 / 本地文件 / lavfi 兜底
BGM_TRACKS: list[dict[str, Any]] = [
    {
        "id": "soft-pink",
        "name": "柔和铺底",
        "description": "三和弦轻垫，适合口播解说",
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


def resolve_track_file(track: dict[str, Any]) -> Path | None:
    """解析曲目本地文件；不存在则 None。"""
    name = str(track.get("file") or "").strip()
    if not name:
        return None
    path = Path(name)
    if not path.is_absolute():
        path = _ASSETS_DIR / name
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def list_bgm_tracks(*, template_id: str | None = None) -> list[dict[str, Any]]:
    tid = (template_id or "").strip()
    items: list[dict[str, Any]] = []
    for t in BGM_TRACKS:
        if tid and tid not in (t.get("templates") or []):
            continue
        has_file = resolve_track_file(t) is not None
        items.append(
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "templates": list(t.get("templates") or []),
                "isDefault": tid in (t.get("defaultFor") or []),
                "hasFile": has_file,
            }
        )
    return items


def default_bgm_track_id(template_id: str) -> str:
    for t in BGM_TRACKS:
        if template_id in (t.get("defaultFor") or []):
            return str(t["id"])
    return "soft-pink"


def resolve_bgm_track(track_id: str | None) -> dict[str, Any] | None:
    tid = (track_id or "").strip() or "soft-pink"
    if tid == "off":
        for t in BGM_TRACKS:
            if t["id"] == "off":
                return t
        return None
    for t in BGM_TRACKS:
        if t["id"] == tid:
            return t
    return BGM_TRACKS[0]
