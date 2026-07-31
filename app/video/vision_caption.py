"""多模态理解素材内容：图片/短视频 → 中文 caption，供分镜规划贴合画面。"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.core.config import get_settings
from app.video.materials import normalize_materials

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float | None], None]

_IMAGE_PROMPT = (
    "用中文简要描述这张图片的可见内容，供短视频口播写作参考。"
    "包含：主体、场景、氛围、显著文字（若有）。"
    "只写画面里能看到的，不要编造；2～4 句，不超过 120 字。"
)

_VIDEO_FRAME_PROMPT = (
    "这是短视频中的一帧。用中文一句话描述画面可见内容（主体+动作/场景），"
    "不要编造，不超过 40 字。"
)


def understand_materials(
    materials: list[dict[str, Any]] | None,
    *,
    on_progress: ProgressCallback | None = None,
    force: bool = False,
) -> list[dict[str, str]]:
    """
    为缺少 caption 的素材补全内容描述。

    force=True 时强制重跑；关闭 VIDEO_VISION_ENABLED 时原样返回。
    单条失败不中断整批。
    """
    mats = normalize_materials(materials)
    if not mats:
        return []

    settings = get_settings()
    if not getattr(settings, "video_vision_enabled", True):
        return mats

    need_idx = [
        i
        for i, m in enumerate(mats)
        if force or not (m.get("caption") or "").strip()
    ]
    if not need_idx:
        return mats

    api_key = (settings.llm_api_key or "").strip()
    if not api_key:
        logger.warning("无 LLM_API_KEY，跳过素材理解")
        return mats

    model = (
        getattr(settings, "video_vision_model", None) or "qwen-vl-plus"
    ).strip()
    client = OpenAI(api_key=api_key, base_url=settings.llm_base_url)
    total = len(need_idx)

    for n, idx in enumerate(need_idx):
        m = mats[idx]
        if on_progress:
            try:
                on_progress(
                    f"正在理解素材 {n + 1}/{total}…",
                    0.08 + 0.18 * (n / max(1, total)),
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            if m.get("kind") == "video":
                cap = caption_video(m["url"], client=client, model=model)
            else:
                cap = caption_image(m["url"], client=client, model=model)
            if cap:
                mats[idx] = {**m, "caption": cap[:200]}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "素材理解失败 kind=%s url=%s: %s",
                m.get("kind"),
                (m.get("url") or "")[:80],
                exc,
            )

    return mats


def caption_image(
    url_or_path: str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> str:
    """理解单张图片，返回中文描述。"""
    settings = get_settings()
    cli = client or OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    mdl = (model or getattr(settings, "video_vision_model", None) or "qwen-vl-plus").strip()
    image_url = _to_vision_image_url(url_or_path)
    if not image_url:
        return ""
    return _chat_vision(
        cli,
        mdl,
        image_url=image_url,
        prompt=_IMAGE_PROMPT,
        timeout=float(getattr(settings, "video_vision_timeout_sec", 60) or 60),
    )


def caption_video(
    url_or_path: str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> str:
    """抽若干帧理解短视频，合并为一条描述。"""
    settings = get_settings()
    cli = client or OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    mdl = (model or getattr(settings, "video_vision_model", None) or "qwen-vl-plus").strip()
    n_frames = max(1, min(5, int(getattr(settings, "video_vision_video_frames", 3) or 3)))
    frames = _extract_video_frames(url_or_path, count=n_frames)
    if not frames:
        # 退化为当图片试一次（部分封面 jpg）
        return caption_image(url_or_path, client=cli, model=mdl)

    parts: list[str] = []
    timeout = float(getattr(settings, "video_vision_timeout_sec", 60) or 60)
    try:
        for i, frame in enumerate(frames):
            b64 = base64.b64encode(frame).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"
            text = _chat_vision(
                cli,
                mdl,
                image_url=data_url,
                prompt=_VIDEO_FRAME_PROMPT,
                timeout=timeout,
            )
            if text:
                parts.append(f"帧{i + 1}：{text}")
    finally:
        pass

    if not parts:
        return ""
    joined = "；".join(parts)
    # 再压成一句总述（可选二次调用；为省成本直接截断合并）
    return ("短视频画面：" + joined)[:200]


def _chat_vision(
    client: OpenAI,
    model: str,
    *,
    image_url: str,
    prompt: str,
    timeout: float,
) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        timeout=timeout,
    )
    content = (response.choices[0].message.content or "").strip()
    return content[:200]


def _to_vision_image_url(url_or_path: str) -> str | None:
    """本地文件转 data URL；http(s) 直接给模型；本站 /cdn 优先读盘。"""
    raw = (url_or_path or "").strip()
    if not raw:
        return None
    local = _resolve_local_media(raw)
    if local is not None and local.is_file():
        suffix = local.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(suffix, "image/jpeg")
        b64 = base64.b64encode(local.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("/cdn/"):
        public = getattr(get_settings(), "video_public_base_url", "") or ""
        if public:
            return public.rstrip("/") + raw
    return None


def _resolve_local_media(url_or_path: str) -> Path | None:
    """复用渲染侧路径约定，解析本站素材。"""
    try:
        from app.video.renderer import _resolve_media_file

        return _resolve_media_file(url_or_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolve media failed: %s", exc)
        return None


def _ffmpeg_bin() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def _extract_video_frames(url_or_path: str, *, count: int) -> list[bytes]:
    """用 ffmpeg 抽 JPEG 帧字节列表。"""
    local = _resolve_local_media(url_or_path)
    if local is None or not local.is_file():
        return []
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        logger.warning("无 ffmpeg，无法抽视频帧")
        return []

    # 取时长粗估：ffprobe 可选；简化用固定时间点
    # 0.5s / 中段用 2s / 4s —— 短视频够用；失败则只抽首帧
    timestamps = ["0.3"]
    if count >= 2:
        timestamps.append("1.5")
    if count >= 3:
        timestamps.append("3.0")
    timestamps = timestamps[:count]

    out: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="vcap-") as tmp:
        tmp_path = Path(tmp)
        for i, ts in enumerate(timestamps):
            dest = tmp_path / f"f{i}.jpg"
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                ts,
                "-i",
                str(local),
                "-frames:v",
                "1",
                "-q:v",
                "5",
                str(dest),
            ]
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("抽帧失败 ts=%s: %s", ts, exc)
                continue
            if dest.is_file() and dest.stat().st_size > 0:
                out.append(dest.read_bytes())
    return out
