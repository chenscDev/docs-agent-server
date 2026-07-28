"""分镜 TTS：DashScope CosyVoice，失败时返回 None（调用方降级静音）。"""

from __future__ import annotations

import logging
import wave
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def scene_narration_text(*, headline: str, body: str = "") -> str:
    """口播文案：标题 + 说明。"""
    parts = [headline.strip()]
    if body and body.strip():
        parts.append(body.strip())
    text = "。".join(p for p in parts if p)
    return text[:200] if text else ""


def synthesize_to_file(text: str, output_path: Path) -> Path | None:
    """
    合成语音到 output_path（wav/mp3，由 SDK 决定）。
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
    model = (settings.video_tts_model or "cosyvoice-v3-flash").strip()
    voice = (settings.video_tts_voice or "longxiaochun_v2").strip()

    try:
        synthesizer = SpeechSynthesizer(
            model=model,
            voice=voice,
            format=AudioFormat.WAV_16000HZ_MONO_16BIT,
        )
        audio = synthesizer.call(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS 调用失败: %s", exc)
        return None

    if not audio:
        logger.warning("TTS 返回空音频 text=%s", text[:40])
        return None

    raw = audio if isinstance(audio, (bytes, bytearray)) else None
    if raw is None and hasattr(audio, "get_audio_data"):
        try:
            raw = audio.get_audio_data()
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None and isinstance(audio, str) and Path(audio).is_file():
        # 少数版本返回临时路径
        data = Path(audio).read_bytes()
        output_path.write_bytes(data)
        return output_path if output_path.is_file() else None

    if not raw:
        logger.warning("TTS 无法解析音频数据 type=%s", type(audio))
        return None

    out = output_path if output_path.suffix.lower() == ".wav" else output_path.with_suffix(".wav")
    out.write_bytes(bytes(raw))
    if not out.is_file() or out.stat().st_size < 64:
        logger.warning("TTS 写入失败或过小: %s", out)
        return None
    return out


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
