"""分镜 TTS：DashScope CosyVoice，失败时返回 None（调用方降级静音）。"""

from __future__ import annotations

import logging
import wave
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 常见可用组合（按优先级）；418/InvalidParameter 时自动换下一组
_TTS_FALLBACKS: list[tuple[str, str]] = [
    ("cosyvoice-v2", "longxiaochun_v2"),
    ("cosyvoice-v1", "longxiaochun"),
    ("cosyvoice-v3-flash", "longanyang"),
    ("cosyvoice-v3-flash", "longxiaochun"),
]


def scene_narration_text(*, headline: str, body: str = "") -> str:
    """口播文案：标题 + 说明。"""
    parts = [headline.strip()]
    if body and body.strip():
        parts.append(body.strip())
    text = "。".join(p for p in parts if p)
    return text[:200] if text else ""


def fit_narration_to_duration(
    text: str,
    duration_sec: float,
    *,
    speech_rate: float = 1.0,
) -> str:
    """
    按镜头时长精简口播，避免合成后再大幅 atempo 加速听起来「赶」。

    中文口播约 3.8～4.2 字/秒（语速 1.0）；speech_rate>1 时可多留一点字。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    dur = max(1.0, float(duration_sec))
    rate = max(0.5, min(2.0, float(speech_rate or 1.0)))
    # 留 0.4s 呼吸；语速越快，同时间内可容纳字数略增
    cps = 3.9 * rate
    budget = int(max(10, (dur - 0.4) * cps))
    if len(raw) <= budget:
        return raw
    cut = raw[:budget]
    best = cut
    for sep in ("。", "！", "？", "；", "，", "、", " "):
        idx = cut.rfind(sep)
        if idx >= max(8, budget // 2):
            best = cut[: idx + (0 if sep == " " else 1)]
            break
    # 句读截得太短则保留硬截断，避免只剩半句标题
    if len(best) < max(10, budget // 2):
        best = cut.rstrip("，、； ")
    best = best.rstrip("，、； ")
    if best and not best.endswith(("。", "！", "？", "…")):
        best = best + "…"
    return best


def synthesize_to_file(
    text: str,
    output_path: Path,
    *,
    speech_rate: float | None = None,
    volume: int | None = None,
    voice_id: str | None = None,
) -> Path | None:
    """
    合成语音到 output_path（wav）。
    成功返回路径；关闭 TTS / 无 Key / 调用失败返回 None。
    """
    settings = get_settings()
    if not settings.video_tts_enabled:
        return None
    api_key = (settings.llm_api_key or "").strip()
    if not api_key:
        logger.info("TTS 跳过：未配置 LLM_API_KEY")
        return None
    text = (text or "").strip()
    if not text:
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer
    except ImportError:
        logger.warning("未安装 dashscope，TTS 不可用（pip install dashscope）")
        return None

    from app.video.tts_catalog import resolve_tts_voice

    dashscope.api_key = api_key
    preferred = (
        (settings.video_tts_model or "").strip(),
        (settings.video_tts_voice or "").strip(),
    )
    combos: list[tuple[str, str]] = []
    picked = resolve_tts_voice(voice_id)
    if picked:
        combos.append(picked)
    if preferred[0] and preferred[1] and preferred not in combos:
        combos.append(preferred)
    for item in _TTS_FALLBACKS:
        if item not in combos:
            combos.append(item)

    rate = speech_rate if speech_rate is not None else settings.video_tts_speech_rate
    rate = max(0.5, min(2.0, float(rate or 1.0)))
    vol = volume if volume is not None else settings.video_tts_volume
    vol = max(0, min(100, int(vol or 50)))

    last_err = ""
    for model, voice in combos:
        try:
            synthesizer = SpeechSynthesizer(
                model=model,
                voice=voice,
                format=AudioFormat.WAV_16000HZ_MONO_16BIT,
                speech_rate=rate,
                volume=vol,
            )
            audio = synthesizer.call(text)
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            logger.warning("TTS 调用失败 model=%s voice=%s: %s", model, voice, exc)
            continue

        raw = _extract_audio_bytes(audio)
        if not raw:
            last_err = f"empty audio model={model} voice={voice}"
            logger.warning("TTS 空音频 model=%s voice=%s text=%s", model, voice, text[:40])
            continue

        out = (
            output_path
            if output_path.suffix.lower() == ".wav"
            else output_path.with_suffix(".wav")
        )
        out.write_bytes(raw)
        if out.is_file() and out.stat().st_size >= 64:
            logger.info("TTS 成功 model=%s voice=%s bytes=%s", model, voice, out.stat().st_size)
            return out
        last_err = "write failed"

    logger.warning("TTS 全部组合失败 last=%s text=%s", last_err[:200], text[:40])
    return None


def _extract_audio_bytes(audio: object) -> bytes | None:
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio)
    if hasattr(audio, "get_audio_data"):
        try:
            data = audio.get_audio_data()
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
        except Exception:  # noqa: BLE001
            pass
    if isinstance(audio, str) and Path(audio).is_file():
        return Path(audio).read_bytes()
    return None


def probe_wav_duration_sec(path: Path) -> float | None:
    """读取 wav 时长；失败返回 None。"""
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate <= 0:
                return None
            return max(0.5, frames / float(rate))
    except Exception:  # noqa: BLE001
        return None
