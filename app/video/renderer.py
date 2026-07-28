"""视频渲染：优先 Remotion CLI，其次 FFmpeg 字幕条；可选 TTS 配音与缩略图。"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.video.schema import Scene, Storyboard
from app.video.tts import (
    probe_wav_duration_sec,
    scene_narration_text,
    synthesize_to_file,
)

logger = logging.getLogger(__name__)


@dataclass
class RenderResult:
    """渲染产物。"""

    output_path: Path
    cover_path: Path | None = None
    scene_thumbs: dict[str, Path] = field(default_factory=dict)
    has_audio: bool = False


def render_storyboard_to_mp4(
    storyboard: Storyboard,
    *,
    output_path: Path,
    job_id: str,
    cancel_check=None,
) -> RenderResult:
    """
    渲染分镜为 MP4，并尽量生成封面/分镜缩略图与配音。

    顺序：Remotion → FFmpeg → 纯色兜底；TTS 失败不阻断成片。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if cancel_check and cancel_check():
        raise RuntimeError("CANCELLED")

    settings = get_settings()
    mode = (settings.video_renderer or "auto").strip().lower()
    result: RenderResult | None = None

    if mode in ("remotion", "auto"):
        result = _try_remotion(storyboard, output_path, job_id=job_id)
        if result is None and mode == "remotion":
            raise RuntimeError("Remotion 渲染失败且 VIDEO_RENDERER=remotion")

    if result is None and mode in ("ffmpeg", "auto"):
        result = _try_ffmpeg(storyboard, output_path, cancel_check=cancel_check)
        if result is None and mode == "ffmpeg":
            raise RuntimeError("FFmpeg 渲染失败且 VIDEO_RENDERER=ffmpeg")

    if result is None:
        result = _try_ffmpeg_solid(storyboard, output_path)
    if result is None:
        raise RuntimeError(
            "无法渲染视频：请安装 ffmpeg，或配置 Remotion（video-renderer）"
        )

    # Remotion / 纯色兜底后：尝试整片配音 + 抽帧缩略图
    if not result.has_audio:
        voiced = _try_attach_full_narration(storyboard, result.output_path)
        if voiced:
            result.has_audio = True
            result.output_path = voiced

    if not result.scene_thumbs:
        result.scene_thumbs = _extract_scene_thumbs_from_mp4(
            storyboard,
            result.output_path,
            out_dir=output_path.parent,
            job_id=job_id,
        )
    if result.cover_path is None and result.scene_thumbs:
        first_id = storyboard.scenes[0].id if storyboard.scenes else None
        if first_id and first_id in result.scene_thumbs:
            cover = output_path.parent / f"{job_id}_v{storyboard.version}_cover.jpg"
            try:
                shutil.copyfile(result.scene_thumbs[first_id], cover)
                result.cover_path = cover
            except OSError as exc:
                logger.warning("复制封面失败: %s", exc)
    elif result.cover_path is None:
        cover = _extract_single_thumb(
            result.output_path,
            output_path.parent / f"{job_id}_v{storyboard.version}_cover.jpg",
        )
        result.cover_path = cover

    return result


def _try_remotion(
    storyboard: Storyboard, output_path: Path, *, job_id: str
) -> RenderResult | None:
    settings = get_settings()
    root = Path(settings.remotion_project_dir)
    if not root.is_dir():
        logger.info("Remotion 项目不存在: %s", root)
        return None
    npx = shutil.which("npx")
    if not npx:
        logger.info("未找到 npx，跳过 Remotion")
        return None

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
        return None

    if proc.returncode != 0 or not output_path.is_file():
        logger.warning(
            "Remotion 失败 code=%s stderr=%s",
            proc.returncode,
            (proc.stderr or "")[-800:],
        )
        return None
    return RenderResult(output_path=output_path, has_audio=False)


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
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
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
) -> RenderResult | None:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        logger.info("未找到 ffmpeg")
        return None

    w, h = _resolution(storyboard.aspectRatio)
    fps = storyboard.fps
    font = _find_font()
    thumbs: dict[str, Path] = {}
    has_audio = False
    job_stem = output_path.stem

    with tempfile.TemporaryDirectory(prefix="ai-video-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        for scene in storyboard.scenes:
            if cancel_check and cancel_check():
                raise RuntimeError("CANCELLED")
            part = tmp_path / f"{scene.index:02d}.mp4"
            if not _render_scene_clip(
                ffmpeg,
                scene=scene,
                output=part,
                width=w,
                height=h,
                fps=fps,
                font=font,
            ):
                return None

            # 分镜缩略图
            thumb = output_path.parent / f"{job_stem}_s{scene.index}.jpg"
            if _extract_single_thumb(part, thumb):
                thumbs[scene.id] = thumb

            # 单镜 TTS 配音
            voiced = _mux_scene_tts(ffmpeg, scene=scene, video_path=part, tmp_dir=tmp_path)
            if voiced is not None:
                part = voiced
                has_audio = True
            parts.append(part)

        if not parts:
            return None

        list_file = tmp_path / "list.txt"
        list_file.write_text(
            "\n".join(f"file '{p.name}'" for p in parts) + "\n",
            encoding="utf-8",
        )
        # 有音频时需统一重编码，避免 concat copy 丢轨
        concat_cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
        ]
        if has_audio:
            concat_cmd.extend(
                [
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(output_path),
                ]
            )
        else:
            concat_cmd.extend(["-c", "copy", str(output_path)])

        proc = subprocess.run(
            concat_cmd,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not output_path.is_file():
            logger.warning("ffmpeg concat failed: %s", (proc.stderr or "")[-400:])
            return None

        cover = None
        if storyboard.scenes and storyboard.scenes[0].id in thumbs:
            cover_path = output_path.parent / f"{job_stem}_cover.jpg"
            try:
                shutil.copyfile(thumbs[storyboard.scenes[0].id], cover_path)
                cover = cover_path
            except OSError:
                cover = None

        return RenderResult(
            output_path=output_path,
            cover_path=cover,
            scene_thumbs=thumbs,
            has_audio=has_audio,
        )


def _render_scene_clip(
    ffmpeg: str,
    *,
    scene: Scene,
    output: Path,
    width: int,
    height: int,
    fps: int,
    font: str | None,
) -> bool:
    color = scene.bgColor.lstrip("#")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x{color}:s={width}x{height}:d={scene.durationSec}:r={fps}",
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
            str(output),
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not output.is_file():
        logger.warning("ffmpeg scene failed: %s", (proc.stderr or "")[-400:])
        return False
    return True


def _mux_scene_tts(
    ffmpeg: str,
    *,
    scene: Scene,
    video_path: Path,
    tmp_dir: Path,
) -> Path | None:
    """为单镜合成配音并混入；失败返回 None（保留静音片）。"""
    text = scene_narration_text(headline=scene.headline, body=scene.body or "")
    if not text:
        return None
    audio_path = synthesize_to_file(text, tmp_dir / f"{scene.index:02d}.wav")
    if audio_path is None:
        return None

    # 若语音更长，拉长画面；更短则循环/补静音由 -shortest 截断音频侧，这里用 apad
    duration = probe_wav_duration_sec(audio_path) or scene.durationSec
    target_dur = max(scene.durationSec, min(duration + 0.3, 15.0))

    out = tmp_dir / f"{scene.index:02d}_voiced.mp4"
    # 先把视频时长对齐到 target_dur（循环或截断）
    stretched = tmp_dir / f"{scene.index:02d}_len.mp4"
    stretch_cmd = [
        ffmpeg,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(video_path),
        "-t",
        f"{target_dur:.2f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(stretched),
    ]
    proc = subprocess.run(stretch_cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not stretched.is_file():
        logger.warning("对齐镜头时长失败: %s", (proc.stderr or "")[-300:])
        stretched = video_path

    mux_cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(stretched),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(out),
    ]
    proc = subprocess.run(mux_cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.is_file():
        logger.warning("混音失败: %s", (proc.stderr or "")[-300:])
        return None
    return out


def _try_attach_full_narration(storyboard: Storyboard, video_path: Path) -> Path | None:
    """整片旁白（Remotion/兜底路径）：拼接各镜文案一次合成。"""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None
    lines = [
        scene_narration_text(headline=s.headline, body=s.body or "")
        for s in storyboard.scenes
    ]
    text = "。".join(t for t in lines if t)
    if not text:
        return None
    with tempfile.TemporaryDirectory(prefix="ai-video-narr-") as tmp:
        tmp_path = Path(tmp)
        audio = synthesize_to_file(text[:400], tmp_path / "full.wav")
        if audio is None:
            return None
        out = video_path.with_name(video_path.stem + "_voiced.mp4")
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not out.is_file():
            logger.warning("整片配音失败: %s", (proc.stderr or "")[-300:])
            return None
        try:
            shutil.move(str(out), str(video_path))
        except OSError:
            return out if out.is_file() else None
        return video_path


def _extract_single_thumb(video_path: Path, thumb_path: Path) -> Path | None:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg or not video_path.is_file():
        return None
    thumb_path = Path(thumb_path)
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        "0.25",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(thumb_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not thumb_path.is_file():
        return None
    return thumb_path


def _extract_scene_thumbs_from_mp4(
    storyboard: Storyboard,
    video_path: Path,
    *,
    out_dir: Path,
    job_id: str,
) -> dict[str, Path]:
    """按累计时长从成片抽各镜缩略图。"""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg or not video_path.is_file():
        return {}
    thumbs: dict[str, Path] = {}
    t = 0.0
    for scene in storyboard.scenes:
        ss = t + min(0.4, max(0.1, scene.durationSec * 0.2))
        thumb = out_dir / f"{job_id}_v{storyboard.version}_s{scene.index}.jpg"
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{ss:.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(thumb),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0 and thumb.is_file():
            thumbs[scene.id] = thumb
        t += scene.durationSec
    return thumbs


def _try_ffmpeg_solid(storyboard: Storyboard, output_path: Path) -> RenderResult | None:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None
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
    if proc.returncode != 0 or not output_path.is_file():
        return None
    return RenderResult(output_path=output_path, has_audio=False)


def _resolution(aspect: str) -> tuple[int, int]:
    if aspect == "16:9":
        return 1280, 720
    if aspect == "1:1":
        return 720, 720
    return 720, 1280  # 9:16
