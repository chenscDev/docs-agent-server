"""BGM 曲库：按模板推荐氛围轨（无外置 mp3 时用 lavfi 合成不同气质）。"""

from __future__ import annotations

from typing import Any

# id → 展示名 / 描述 / 推荐模板 / lavfi 配方
BGM_TRACKS: list[dict[str, Any]] = [
    {
        "id": "soft-pink",
        "name": "轻粉噪",
        "description": "低干扰氛围，适合口播解说",
        "templates": ["talking-captions", "brand-intro", "kinetic-text"],
        "defaultFor": ["talking-captions"],
        "lavfi": "anoisesrc=color=pink:amplitude=0.32",
        "afExtra": "lowpass=f=1200",
        "volume": 0.16,
    },
    {
        "id": "bright-pulse",
        "name": "轻快脉冲",
        "description": "节奏感稍强，适合卖点快闪",
        "templates": ["kinetic-text", "talking-captions"],
        "defaultFor": ["kinetic-text"],
        "lavfi": "sine=frequency=220:sample_rate=16000",
        "afExtra": "tremolo=f=4:d=0.35,lowpass=f=900,highpass=f=80",
        "volume": 0.10,
    },
    {
        "id": "warm-pad",
        "name": "暖垫氛围",
        "description": "偏品牌开场，更柔和",
        "templates": ["brand-intro", "talking-captions"],
        "defaultFor": ["brand-intro"],
        "lavfi": "sine=frequency=110:sample_rate=16000",
        "afExtra": "tremolo=f=0.35:d=0.55,lowpass=f=600",
        "volume": 0.12,
    },
    {
        "id": "off",
        "name": "无配乐",
        "description": "仅口播，不混 BGM",
        "templates": ["talking-captions", "kinetic-text", "brand-intro"],
        "defaultFor": [],
        "lavfi": "",
        "afExtra": "",
        "volume": 0.0,
    },
]


def list_bgm_tracks(*, template_id: str | None = None) -> list[dict[str, Any]]:
    tid = (template_id or "").strip()
    items: list[dict[str, Any]] = []
    for t in BGM_TRACKS:
        if tid and tid not in (t.get("templates") or []):
            continue
        items.append(
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "templates": list(t.get("templates") or []),
                "isDefault": tid in (t.get("defaultFor") or []),
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
    for t in BGM_TRACKS:
        if t["id"] == tid:
            return t
    return BGM_TRACKS[0]
