"""视频渲染：优先 Remotion CLI，其次 FFmpeg 字幕条，最后最小占位 MP4。"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.video.schema import Storyboard

logger = logging.getLogger(__name__)

# 极小合法 MP4 占位已改为 imageio-ffmpeg / 系统 ffmpeg 兜底


def render_storyboard_to_mp4(
    storyboard: Storyboard,
    *,
    output_path: Path,
    job_id: str,
    cancel_check=None,
) -> Path:
    """
    渲染分镜为 MP4。

    顺序：Remotion → FFmpeg → 写失败说明文件并抛错（调用方标记 failed）。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if cancel_check and cancel_check():
        raise RuntimeError("CANCELLED")

    settings = get_settings()
    mode = (settings.video_renderer or "auto").strip().lower()

    if mode in ("remotion", "auto"):
        if _try_remotion(storyboard, output_path, job_id=job_id):
            return output_path
        if mode == "remotion":
            raise RuntimeError("Remotion 渲染失败且 VIDEO_RENDERER=remotion")

    if mode in ("ffmpeg", "auto"):
        if _try_ffmpeg(storyboard, output_path, cancel_check=cancel_check):
            return output_path
        if mode == "ffmpeg":
            raise RuntimeError("FFmpeg 渲染失败且 VIDEO_RENDERER=ffmpeg")

    # 最终兜底：生成静音色块（仍尽量用 ffmpeg）；否则写占位并失败
    if _try_ffmpeg_solid(storyboard, output_path):
        return output_path

    raise RuntimeError(
        "无法渲染视频：请安装 ffmpeg，或配置 Remotion（video-renderer）"
    )


def _try_remotion(storyboard: Storyboard, output_path: Path, *, job_id: str) -> bool:
    settings = get_settings()
    root = Path(settings.remotion_project_dir)
    if not root.is_dir():
        logger.info("Remotion 项目不存在: %s", root)
        return False
    npx = shutil.which("npx")
    if not npx:
        logger.info("未找到 npx，跳过 Remotion")
        return False

    props_path = output_path.with_suffix(".props.json")
    props_path.write_text(
        json.dumps(storyboard.to_public_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    composition = {
        "talking-captions": "TalkingCaptions",
        "kinetic-text": "KineticText",
        "brand-intro": "BrandIntro",
    }.get(storyboard.templateId, "TalkingCaptions")

    cmd = [
        npx,
        "remotion",
        "render",
        composition,
        str(output_path),
        f"--props={props_path}",
    ]
    logger.info("Remotion render job=%s composition=%s", job_id, composition)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=int(settings.video_render_timeout_sec),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Remotion 执行异常: %s", exc)
        return False

    if proc.returncode != 0 or not output_path.is_file():
        logger.warning(
            "Remotion 失败 code=%s stderr=%s",
            proc.returncode,
            (proc.stderr or "")[-800:],
        )
        return False
    return True


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def _ffmpeg_bin() -> str | None:
    """系统 ffmpeg 优先，其次 imageio-ffmpeg 自带二进制。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        logger.info("imageio-ffmpeg 不可用: %s", exc)
        return None


def _find_font() -> str | None:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


def _try_ffmpeg(
    storyboard: Storyboard,
    output_path: Path,
    *,
    cancel_check=None,
) -> bool:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        logger.info("未找到 ffmpeg")
        return False

    w, h = _resolution(storyboard.aspectRatio)
    fps = storyboard.fps
    font = _find_font()
    with tempfile.TemporaryDirectory(prefix="ai-video-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        for scene in storyboard.scenes:
            if cancel_check and cancel_check():
                raise RuntimeError("CANCELLED")
            part = tmp_path / f"{scene.index:02d}.mp4"
            color = scene.bgColor.lstrip("#")
            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x{color}:s={w}x{h}:d={scene.durationSec}:r={fps}",
            ]
            if font:
                headline = _escape_drawtext(scene.headline[:40])
                body = _escape_drawtext((scene.body or "")[:60])
                vf = (
                    f"drawtext=fontfile='{font}':text='{headline}':fontsize=42:"
                    f"fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-40:"
                    f"box=1:boxcolor=black@0.35:boxborderw=16,"
                    f"drawtext=fontfile='{font}':text='{body}':fontsize=24:"
                    f"fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2+40"
                )
                cmd.extend(["-vf", vf])
            cmd.extend(
                [
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(part),
                ]
            )
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not part.is_file():
                logger.warning(
                    "ffmpeg scene failed: %s",
                    (proc.stderr or "")[-400:],
                )
                return False
            parts.append(part)

        if not parts:
            return False

        list_file = tmp_path / "list.txt"
        list_file.write_text(
            "\n".join(f"file '{p.name}'" for p in parts) + "\n",
            encoding="utf-8",
        )
        concat_cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output_path),
        ]
        proc = subprocess.run(
            concat_cmd,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not output_path.is_file():
            logger.warning("ffmpeg concat failed: %s", (proc.stderr or "")[-400:])
            return False
    return True


def _try_ffmpeg_solid(storyboard: Storyboard, output_path: Path) -> bool:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return False
    w, h = _resolution(storyboard.aspectRatio)
    duration = max(3.0, min(storyboard.total_duration_sec, 30.0))
    color = storyboard.scenes[0].bgColor.lstrip("#") if storyboard.scenes else "0F172A"
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x{color}:s={w}x{h}:d={duration}:r=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode == 0 and output_path.is_file()


def _resolution(aspect: str) -> tuple[int, int]:
    if aspect == "16:9":
        return 1280, 720
    if aspect == "1:1":
        return 720, 720
    return 720, 1280  # 9:16
