#!/usr/bin/env python3
"""生成内置 BGM wav（无外置版权素材时用和弦铺底）。"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "video" / "assets" / "bgm"


def write_wav(
    path: Path,
    freqs: list[float],
    *,
    dur: float = 30.0,
    vol: float = 0.2,
    tremolo: float = 0.0,
    trem_f: float = 0.0,
    sr: int = 16000,
) -> None:
    n = int(sr * dur)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            t = i / sr
            fade = 1.0
            if t < 1.0:
                fade = t / 1.0
            elif t > dur - 1.5:
                fade = max(0.0, (dur - t) / 1.5)
            s = 0.0
            for f in freqs:
                s += math.sin(2 * math.pi * f * t)
            s /= max(1, len(freqs))
            if tremolo > 0 and trem_f > 0:
                s *= (1 - tremolo) + tremolo * math.sin(2 * math.pi * trem_f * t)
            val = int(max(-1.0, min(1.0, s * vol * fade)) * 32767)
            w.writeframes(struct.pack("<h", val))


def main() -> None:
    write_wav(ROOT / "soft-pink.wav", [196.0, 246.94, 293.66], vol=0.2)
    write_wav(
        ROOT / "bright-pulse.wav",
        [329.63, 392.0],
        vol=0.16,
        tremolo=0.35,
        trem_f=4.5,
    )
    write_wav(
        ROOT / "warm-pad.wav",
        [130.81, 164.81, 196.0],
        vol=0.18,
        tremolo=0.25,
        trem_f=0.4,
    )
    print("written:", sorted(p.name for p in ROOT.glob("*.wav")))


if __name__ == "__main__":
    main()
