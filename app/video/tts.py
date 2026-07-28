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


def synthesize_to_file(text: str, output_path: Path) -> Path | None:
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

    dashscope.api_key = api_key
    preferred = (
        (settings.video_tts_model or "").strip(),
        (settings.video_tts_voice or "").strip(),
    )
    combos: list[tuple[str, str]] = []
    if preferred[0] and preferred[1]:
        combos.append(preferred)
    for item in _TTS_FALLBACKS:
        if item not in combos:
            combos.append(item)

    last_err = ""
    for model, voice in combos:
        try:
            synthesizer = SpeechSynthesizer(
                model=model,
                voice=voice,
                format=AudioFormat.WAV_16000HZ_MONO_16BIT,
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
