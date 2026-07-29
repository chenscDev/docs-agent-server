"""口播音色目录（CosyVoice 常用 voice）。"""

from __future__ import annotations

from typing import Any

# 展示用；实际合成时 model+voice 配对，失败仍走 tts.py 内置兜底
TTS_VOICES: list[dict[str, Any]] = [
    {
        "id": "longxiaochun_v2",
        "name": "晓春（女声·清晰）",
        "model": "cosyvoice-v2",
        "voice": "longxiaochun_v2",
        "description": "默认口播，清晰自然",
    },
    {
        "id": "longanyang",
        "name": "安阳（男声·稳）",
        "model": "cosyvoice-v3-flash",
        "voice": "longanyang",
        "description": "偏稳重讲解",
    },
    {
        "id": "longxiaochun",
        "name": "晓春经典",
        "model": "cosyvoice-v1",
        "voice": "longxiaochun",
        "description": "兼容旧版音色",
    },
]


def list_tts_voices() -> list[dict[str, Any]]:
    return [
        {
            "id": v["id"],
            "name": v["name"],
            "description": v["description"],
            "model": v["model"],
            "voice": v["voice"],
        }
        for v in TTS_VOICES
    ]


def resolve_tts_voice(voice_id: str | None) -> tuple[str, str] | None:
    """返回 (model, voice)；未知则 None（走服务端默认）。"""
    vid = (voice_id or "").strip()
    if not vid:
        return None
    for v in TTS_VOICES:
        if v["id"] == vid or v["voice"] == vid:
            return str(v["model"]), str(v["voice"])
    return None
