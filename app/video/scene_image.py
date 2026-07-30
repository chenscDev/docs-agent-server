"""根据画面说明（visualHint）文生图，落盘为分镜配图。"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any
from urllib.request import Request, urlopen

from app.core.config import get_settings
from app.video.assets import save_uploaded_asset

logger = logging.getLogger(__name__)


def build_scene_image_prompt(
    *,
    visual_hint: str,
    headline: str = "",
    body: str = "",
) -> str:
    """把分镜字段拼成文生图提示词（偏竖屏短视频背景，避免大段汉字）。"""
    hint = (visual_hint or "").strip()
    title = (headline or "").strip()
    detail = (body or "").strip()
    parts: list[str] = [
        "竖屏短视频背景图，9:16 构图，电影感光影，高质量，无水印，不要大段汉字字幕，",
        "适合口播叠加字幕，主体清晰，背景略虚化。",
    ]
    if hint:
        parts.append(f"画面内容：{hint}。")
    if title:
        parts.append(f"主题氛围：{title}。")
    if detail and len(detail) <= 80:
        parts.append(f"补充：{detail}。")
    return "".join(parts)[:800]


def generate_scene_image(
    *,
    visual_hint: str,
    headline: str = "",
    body: str = "",
    size: str = "720*1280",
) -> dict[str, Any]:
    """
    根据画面说明生成配图并保存到素材目录。

    返回：{ url, filename, id, kind, prompt, source }
    source = wanx | placeholder
    """
    hint = (visual_hint or "").strip()
    if not hint and not (headline or "").strip():
        raise ValueError("请先填写画面说明或镜头标题")

    prompt = build_scene_image_prompt(
        visual_hint=hint or headline,
        headline=headline,
        body=body,
    )
    settings = get_settings()
    if not getattr(settings, "video_t2i_enabled", True):
        return _save_placeholder(prompt, headline=headline, hint=hint)

    api_key = (settings.llm_api_key or "").strip()
    if not api_key:
        logger.warning("无 LLM_API_KEY，画面说明生图走占位图")
        return _save_placeholder(prompt, headline=headline, hint=hint)

    try:
        data, content_type = _synthesize_with_wanx(
            api_key=api_key,
            model=getattr(settings, "video_t2i_model", None) or "wanx-v1",
            prompt=prompt,
            size=size or getattr(settings, "video_t2i_size", None) or "720*1280",
        )
        saved = save_uploaded_asset(
            data=data,
            filename="scene.png",
            content_type=content_type or "image/png",
        )
        return {
            **saved,
            "prompt": prompt,
            "source": "wanx",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("通义万相生图失败，回退占位图: %s", exc)
        out = _save_placeholder(prompt, headline=headline, hint=hint)
        out["error"] = str(exc)[:200]
        return out


def _synthesize_with_wanx(
    *,
    api_key: str,
    model: str,
    prompt: str,
    size: str,
) -> tuple[bytes, str]:
    """调用 DashScope ImageSynthesis，下载结果图字节。"""
    try:
        import dashscope
        from dashscope import ImageSynthesis
        from http import HTTPStatus
    except ImportError as exc:
        raise RuntimeError("未安装 dashscope，无法文生图") from exc

    dashscope.api_key = api_key
    rsp = ImageSynthesis.call(
        model=model,
        prompt=prompt,
        n=1,
        size=size,
    )
    status = getattr(rsp, "status_code", None)
    if status != HTTPStatus.OK:
        code = getattr(rsp, "code", "")
        message = getattr(rsp, "message", "") or str(rsp)
        raise RuntimeError(f"生图失败 code={code} message={message}")

    output = getattr(rsp, "output", None)
    results = getattr(output, "results", None) if output is not None else None
    if not results:
        # 部分 SDK 版本 output 是 dict
        if isinstance(output, dict):
            results = output.get("results") or []
        else:
            results = []
    if not results:
        raise RuntimeError("生图成功但未返回图片")

    first = results[0]
    url = ""
    if isinstance(first, dict):
        url = str(first.get("url") or "")
    else:
        url = str(getattr(first, "url", "") or "")
    if not url:
        raise RuntimeError("生图结果缺少 url")

    req = Request(url, headers={"User-Agent": "docs-agent-server/scene-t2i"})
    with urlopen(req, timeout=60) as resp:  # noqa: S310
        data = resp.read()
        ctype = resp.headers.get("Content-Type") or "image/png"
    if not data:
        raise RuntimeError("下载生成图为空")
    return data, ctype


def _save_placeholder(
    prompt: str,
    *,
    headline: str,
    hint: str,
) -> dict[str, Any]:
    """无 Key / 生图失败时的竖屏占位图（仍可演示配图链路）。"""
    try:
        data = _pillow_placeholder_png(headline=headline, hint=hint)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pillow placeholder skip: %s", exc)
        data = _minimal_png_bytes(720, 1280, rgb=(15, 23, 42))
    saved = save_uploaded_asset(
        data=data,
        filename="scene-placeholder.png",
        content_type="image/png",
    )
    return {
        **saved,
        "prompt": prompt,
        "source": "placeholder",
    }


def _pillow_placeholder_png(*, headline: str, hint: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    w, h = 720, 1280
    img = Image.new("RGB", (w, h), "#0F172A")
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(15 + 40 * t)
        g = int(23 + 60 * (1 - t))
        b = int(42 + 80 * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    font = ImageFont.load_default()
    title = (headline or "分镜配图")[:28]
    body = (hint or "")[:60]
    draw.rectangle([40, h // 2 - 120, w - 40, h // 2 + 120], fill=(15, 23, 42))
    draw.text((60, h // 2 - 80), "AI 画面占位", fill="#7DD3FC", font=font)
    draw.text((60, h // 2 - 40), title, fill="#F8FAFC", font=font)
    draw.text((60, h // 2 + 10), body, fill="#CBD5E1", font=font)
    draw.text(
        (60, h // 2 + 70),
        "配置 LLM_API_KEY 后可生成真实配图",
        fill="#94A3B8",
        font=font,
    )
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _minimal_png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """无 Pillow 时写一张纯色 PNG（stdlib）。"""
    import struct
    import zlib

    r, g, b = rgb
    raw = b"".join(
        b"\x00" + bytes([r, g, b]) * width for _ in range(height)
    )
    compressed = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )
