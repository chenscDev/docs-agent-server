"""视频渲染：优先 Remotion CLI，其次 FFmpeg 字幕条；可选 TTS 配音与缩略图。"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
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

# message, progress(0~1)
ProgressCallback = Callable[[str, float], None]


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
    on_progress: ProgressCallback | None = None,
) -> RenderResult:
    """
    渲染分镜为 MP4，并尽量生成封面/分镜缩略图与配音。

    顺序：Remotion → FFmpeg → 纯色兜底；TTS 失败不阻断成片。
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def report(message: str, progress: float) -> None:
        if on_progress is not None:
            try:
                on_progress(message, max(0.0, min(0.99, progress)))
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_progress 回调失败: %s", exc)

    if cancel_check and cancel_check():
        raise RuntimeError("CANCELLED")

    settings = get_settings()
    mode = (settings.video_renderer or "auto").strip().lower()
    result: RenderResult | None = None

    report("准备渲染引擎…", 0.50)

    if mode in ("remotion", "auto"):
        report("尝试 Remotion 高质量渲染…", 0.52)
        result = _try_remotion(storyboard, output_path, job_id=job_id)
        if result is None and mode == "remotion":
            raise RuntimeError("Remotion 渲染失败且 VIDEO_RENDERER=remotion")
        if result is not None:
            report("Remotion 成片完成，后处理中…", 0.88)

    if result is None and mode in ("ffmpeg", "auto"):
        report("使用 FFmpeg 逐镜合成画面与配音…", 0.54)
        result = _try_ffmpeg(
            storyboard,
            output_path,
            cancel_check=cancel_check,
            on_progress=on_progress,
        )
        if result is None and mode == "ffmpeg":
            logger.warning("FFmpeg 主路径失败，尝试纯色兜底")

    if result is None:
        report("主路径失败，使用纯色兜底渲染…", 0.56)
        result = _try_ffmpeg_solid(storyboard, output_path)
    if result is None:
        raise RuntimeError(
            "无法渲染视频：请安装 ffmpeg，或配置 Remotion（video-renderer）"
        )

    # Remotion / 纯色兜底后：尝试整片配音 + 抽帧缩略图
    if not result.has_audio:
        report("为成片合成旁白配音…", 0.90)
        voiced = _try_attach_full_narration(storyboard, result.output_path)
        if voiced:
            result.has_audio = True
            result.output_path = voiced

    if not result.scene_thumbs:
        report("抽取分镜缩略图…", 0.93)
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
    if result.cover_path is None:
        report("生成封面图…", 0.95)
        cover = _extract_single_thumb(
            result.output_path,
            output_path.parent / f"{job_id}_v{storyboard.version}_cover.jpg",
        )
        result.cover_path = cover

    # 混入 BGM（失败不阻断）
    report("混入背景音乐…", 0.97)
    mixed = _try_mix_bgm(storyboard, result.output_path)
    if mixed is not None:
        result.output_path = mixed
        result.has_audio = True

    report("渲染收尾…", 0.99)
    return result


def _try_remotion(
    storyboard: Storyboard, output_path: Path, *, job_id: str
) -> RenderResult | None:
    settings = get_settings()
    root = Path(settings.remotion_project_dir)
    if not root.is_dir():
        logger.info("Remotion 项目不存在: %s", root)
        return None
    # 缺 tsconfig 时 Remotion CLI 会直接失败并刷屏，提前跳过走 FFmpeg
    if not (root / "tsconfig.json").is_file():
        logger.info("Remotion 缺少 tsconfig.json，跳过: %s", root)
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
    on_progress: ProgressCallback | None = None,
) -> RenderResult | None:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        logger.info("未找到 ffmpeg")
        return None

    def report(message: str, progress: float) -> None:
        if on_progress is not None:
            try:
                on_progress(message, max(0.0, min(0.99, progress)))
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_progress 回调失败: %s", exc)

    w, h = _resolution(storyboard.aspectRatio)
    fps = storyboard.fps
    font = _find_font()
    use_drawtext = _ffmpeg_supports_drawtext(ffmpeg)
    thumbs: dict[str, Path] = {}
    has_audio = False
    job_stem = output_path.stem
    total = max(1, len(storyboard.scenes))

    with tempfile.TemporaryDirectory(prefix="ai-video-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        for scene in storyboard.scenes:
            if cancel_check and cancel_check():
                raise RuntimeError("CANCELLED")
            # 画面合成约占 0.55~0.82，配音约占每镜后半
            base = 0.55 + 0.30 * (scene.index / total)
            report(
                f"绘制画面 {scene.index + 1}/{total}：{scene.headline[:24]}",
                base,
            )
            part = tmp_path / f"{scene.index:02d}.mp4"
            if not _render_scene_clip(
                ffmpeg,
                scene=scene,
                output=part,
                width=w,
                height=h,
                fps=fps,
                font=font,
                use_drawtext=use_drawtext,
                template_id=storyboard.templateId,
            ):
                return None

            # 分镜缩略图
            thumb = output_path.parent / f"{job_stem}_s{scene.index}.jpg"
            if _extract_single_thumb(part, thumb):
                thumbs[scene.id] = thumb

            # 单镜 TTS 配音
            report(
                f"生成配音 {scene.index + 1}/{total}…",
                base + 0.30 / total * 0.5,
            )
            voiced = _mux_scene_tts(
                ffmpeg,
                scene=scene,
                video_path=part,
                tmp_dir=tmp_path,
                speech_rate=storyboard.speechRate,
            )
            if voiced is not None:
                part = voiced
                has_audio = True
            parts.append(part)

        if not parts:
            return None

        report("拼接全部分镜成片…", 0.88)
        list_file = tmp_path / "list.txt"
        list_file.write_text(
            "\n".join(f"file '{p.name}'" for p in parts) + "\n",
            encoding="utf-8",
        )
        # 优先 stream copy（镜头已是统一 libx264/aac），降低 1.5G 机器在配音后的内存尖峰
        concat_copy = [
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
        if not has_audio:
            concat_copy = [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c:v",
                "copy",
                "-an",
                str(output_path),
            ]
        proc = subprocess.run(
            concat_copy,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not output_path.is_file():
            logger.warning(
                "ffmpeg concat copy 失败，回退重编码: %s",
                (proc.stderr or "")[-400:],
            )
            if output_path.is_file():
                output_path.unlink(missing_ok=True)
            concat_cmd = [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
            ]
            if has_audio:
                concat_cmd.extend(["-c:a", "aac", "-shortest"])
            else:
                concat_cmd.append("-an")
            concat_cmd.append(str(output_path))
            proc = subprocess.run(
                concat_cmd,
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0 or not output_path.is_file():
                logger.warning(
                    "ffmpeg concat failed: %s", (proc.stderr or "")[-800:]
                )
                return None

        report("拼接完成，生成封面与背景音乐…", 0.92)
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


def _ffmpeg_supports_drawtext(ffmpeg: str) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        return "drawtext" in (proc.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return False


def _make_scene_card_png(
    scene: Scene,
    *,
    width: int,
    height: int,
    output: Path,
    template_id: str = "talking-captions",
) -> Path | None:
    """用 Pillow 画字幕卡；不同模板布局/装饰不同。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("未安装 Pillow，无法生成字幕卡")
        return None

    bg = scene.bgColor.lstrip("#")
    accent = (scene.accentColor or "#38BDF8").lstrip("#")
    try:
        rgb = tuple(int(bg[i : i + 2], 16) for i in (0, 2, 4))
        accent_rgb = tuple(int(accent[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        rgb = (15, 23, 42)
        accent_rgb = (56, 189, 248)

    img = Image.new("RGB", (width, height), rgb)
    draw = ImageDraw.Draw(img)
    font_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font_large = ImageFont.load_default()
    font_small = ImageFont.load_default()
    for fp in font_paths:
        if Path(fp).is_file():
            try:
                font_large = ImageFont.truetype(fp, 48)
                font_small = ImageFont.truetype(fp, 28)
                break
            except OSError:
                continue

    headline = (scene.headline or "")[:40]
    body = (scene.body or "")[:60]
    tid = (template_id or "talking-captions").strip()

    # 模板装饰差异
    if tid == "kinetic-text":
        # 斜切色块 + 左上角编号
        draw.polygon(
            [(0, 0), (width, 0), (width, int(height * 0.22)), (0, int(height * 0.32))],
            fill=accent_rgb,
        )
        draw.rectangle([0, height - 18, width, height], fill=accent_rgb)
        draw.text((32, 36), f"#{scene.index + 1}", fill=(255, 255, 255), font=font_small)
        title_y = int(height * 0.42)
        body_y = int(height * 0.58)
    elif tid == "brand-intro":
        # 居中品牌框
        margin = int(width * 0.1)
        box = [margin, int(height * 0.32), width - margin, int(height * 0.68)]
        try:
            draw.rounded_rectangle(box, radius=28, outline=accent_rgb, width=6)
        except AttributeError:
            draw.rectangle(box, outline=accent_rgb, width=6)
        draw.ellipse(
            [width // 2 - 28, int(height * 0.22) - 28, width // 2 + 28, int(height * 0.22) + 28],
            fill=accent_rgb,
        )
        title_y = int(height * 0.42)
        body_y = int(height * 0.55)
    else:
        # talking-captions：底部字幕条风格
        bar_h = int(height * 0.28)
        draw.rectangle([0, height - bar_h, width, height], fill=(0, 0, 0))
        draw.rectangle([0, height - bar_h, 12, height], fill=accent_rgb)
        title_y = height - bar_h + 36
        body_y = height - bar_h + 100

    def _draw_text(text: str, font, y: int, *, boxed: bool = True) -> None:
        if not text:
            return
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = max(16, (width - tw) // 2)
        if boxed and tid != "talking-captions":
            pad = 16
            draw.rectangle(
                [x - pad, y - 6, x + tw + pad, y + (bbox[3] - bbox[1]) + 10],
                fill=(0, 0, 0),
            )
        draw.text((x, y), text, fill=(255, 255, 255), font=font)

    _draw_text(headline, font_large, title_y, boxed=True)
    _draw_text(body, font_small, body_y, boxed=tid != "talking-captions")
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output, format="PNG")
    return output if output.is_file() else None


def _render_scene_clip(
    ffmpeg: str,
    *,
    scene: Scene,
    output: Path,
    width: int,
    height: int,
    fps: int,
    font: str | None,
    use_drawtext: bool,
    template_id: str = "talking-captions",
) -> bool:
    color = scene.bgColor.lstrip("#")
    # 优先 Pillow 字幕卡（兼容无 drawtext 的 ffmpeg）
    card = output.with_suffix(".png")
    if _make_scene_card_png(
        scene,
        width=width,
        height=height,
        output=card,
        template_id=template_id,
    ):
        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(card),
            "-t",
            f"{scene.durationSec}",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        try:
            card.unlink(missing_ok=True)
        except OSError:
            pass
        if proc.returncode == 0 and output.is_file():
            return True
        logger.warning("字幕卡渲染失败，尝试 lavfi: %s", (proc.stderr or "")[-300:])

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x{color}:s={width}x{height}:d={scene.durationSec}:r={fps}",
    ]
    if use_drawtext and font:
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
    speech_rate: float = 1.0,
) -> Path | None:
    """为单镜合成配音并混入；失败返回 None（保留静音片）。"""
    text = scene_narration_text(headline=scene.headline, body=scene.body or "")
    if not text:
        return None
    audio_path = synthesize_to_file(
        text,
        tmp_dir / f"{scene.index:02d}.wav",
        speech_rate=speech_rate,
    )
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
        audio = synthesize_to_file(
            text[:400],
            tmp_path / "full.wav",
            speech_rate=storyboard.speechRate,
        )
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


def _try_mix_bgm(storyboard: Storyboard, video_path: Path) -> Path | None:
    """把轻 BGM 混入成片；关闭或失败返回 None。"""
    settings = get_settings()
    enabled = bool(storyboard.bgmEnabled and settings.video_bgm_enabled)
    if not enabled:
        return None
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg or not video_path.is_file():
        return None

    duration = max(3.0, float(storyboard.total_duration_sec or 12.0))
    vol = float(storyboard.bgmVolume if storyboard.bgmVolume is not None else settings.video_bgm_volume)
    vol = max(0.0, min(1.0, vol))
    bgm_file = (settings.video_bgm_file or "").strip()

    with tempfile.TemporaryDirectory(prefix="ai-video-bgm-") as tmp:
        tmp_path = Path(tmp)
        bgm_wav = tmp_path / "bgm.wav"
        if bgm_file and Path(bgm_file).is_file():
            # 循环裁剪到片长
            cmd = [
                ffmpeg,
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                bgm_file,
                "-t",
                f"{duration:.2f}",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(bgm_wav),
            ]
        else:
            # 粉噪轻氛围（无需外部素材）
            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anoisesrc=color=pink:amplitude=0.35:duration={duration:.2f}",
                "-af",
                f"lowpass=f=1200,volume={vol:.3f},"
                f"afade=t=in:st=0:d=0.8,afade=t=out:st={max(0.5, duration - 1.0):.2f}:d=1.0",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(bgm_wav),
            ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not bgm_wav.is_file():
            logger.warning("生成 BGM 失败: %s", (proc.stderr or "")[-300:])
            return None

        out = video_path.with_name(video_path.stem + "_bgm.mp4")
        # 有无原音轨都兼容：先尝试混音，失败则仅挂 BGM
        mix = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(bgm_wav),
            "-filter_complex",
            f"[1:a]volume={vol:.3f}[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        proc = subprocess.run(mix, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not out.is_file():
            # 原片可能无音轨
            mix2 = [
                ffmpeg,
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(bgm_wav),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                str(out),
            ]
            proc = subprocess.run(mix2, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not out.is_file():
                logger.warning("混入 BGM 失败: %s", (proc.stderr or "")[-300:])
                return None
        try:
            shutil.move(str(out), str(video_path))
            return video_path
        except OSError:
            return out if out.is_file() else None


def _resolution(aspect: str) -> tuple[int, int]:
    if aspect == "16:9":
        return 1280, 720
    if aspect == "1:1":
        return 720, 720
    return 720, 1280  # 9:16
