"""视频渲染：优先 Remotion CLI，可选 Lambda 渲染跳板，其次 FFmpeg 字幕条；可选 TTS 配音与缩略图。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.video.schema import Scene, Storyboard
from app.video.tts import (
    fit_narration_to_duration,
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
    reused_scene_ids: list[str] = field(default_factory=list)


def scene_content_hash(
    scene: Scene,
    *,
    template_id: str,
    speech_rate: float,
    tts_voice: str = "",
    caption_position: str = "bottom",
) -> str:
    """镜头内容指纹：文案/配图/时长/配音/字幕位置变化则需重渲。"""
    payload = {
        "templateId": template_id,
        "speechRate": round(float(speech_rate), 3),
        "ttsVoice": (tts_voice or "").strip(),
        "captionPosition": (caption_position or "bottom").strip(),
        "id": scene.id,
        # 不含 index：仅调整镜头顺序时可复用成片片段
        "durationSec": scene.durationSec,
        "headline": scene.headline,
        "body": scene.body or "",
        "visualHint": scene.visualHint or "",
        "bgColor": scene.bgColor,
        "accentColor": scene.accentColor,
        "imageUrl": scene.imageUrl or "",
        "videoUrl": getattr(scene, "videoUrl", "") or "",
        "videoTrimStartSec": round(
            float(getattr(scene, "videoTrimStartSec", 0) or 0), 3
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def clips_dir_for_job(job_id: str) -> Path:
    settings = get_settings()
    root = Path(settings.video_output_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    path = root / "clips" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_reusable_clip(
    *,
    parent_job_id: str | None,
    scene_id: str,
    content_hash: str,
) -> Path | None:
    if not parent_job_id:
        return None
    parent_dir = clips_dir_for_job(parent_job_id)
    candidate = parent_dir / f"{scene_id}_{content_hash}.mp4"
    if candidate.is_file() and candidate.stat().st_size > 0:
        return candidate
    return None


def render_storyboard_to_mp4(
    storyboard: Storyboard,
    *,
    output_path: Path,
    job_id: str,
    parent_job_id: str | None = None,
    cancel_check=None,
    on_progress: ProgressCallback | None = None,
) -> RenderResult:
    """
    渲染分镜为 MP4，并尽量生成封面/分镜缩略图与配音。

    顺序（auto）：本机 Remotion →（可选）Lambda 渲染跳板 → FFmpeg → 纯色兜底。
    TTS / BGM / Logo / 缩略图仍在本机后处理；Job/SSE 不变。
    Remix 子任务可通过 parent_job_id 复用未改镜 clip。
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

    # 有父任务且可复用 clip 时，优先走 FFmpeg 局部重渲（整片 Remotion/Lambda 无法复用 clip）
    prefer_partial = bool(parent_job_id) and mode in ("auto", "ffmpeg")

    lambda_ready = _lambda_configured(settings)
    prefer_lambda = bool(settings.remotion_prefer_lambda) or mode == "lambda"
    try_local = mode in ("remotion", "auto") and not prefer_partial
    try_lambda = (
        (mode in ("lambda", "auto") or (mode == "remotion" and lambda_ready))
        and lambda_ready
        and not prefer_partial
    )

    # 1) 可选：优先 Lambda（演示机内存紧）
    if try_lambda and prefer_lambda:
        report("尝试 Remotion Lambda 云端渲染…", 0.52)
        result = _try_remotion_lambda(storyboard, output_path, job_id=job_id)
        if result is None and mode == "lambda":
            raise RuntimeError("Remotion Lambda 渲染失败且 VIDEO_RENDERER=lambda")
        if result is not None:
            report("Lambda 成片完成，后处理中…", 0.88)
            result = _apply_logo_overlay(storyboard, result)

    # 2) 本机 Remotion CLI（默认先走这里，稳住观感主路径）
    if result is None and try_local and not (prefer_lambda and mode == "lambda"):
        report("尝试本机 Remotion 高质量渲染…", 0.53)
        result = _try_remotion(storyboard, output_path, job_id=job_id)
        if result is None and mode == "remotion" and not try_lambda:
            raise RuntimeError("Remotion 渲染失败且 VIDEO_RENDERER=remotion")
        if result is not None:
            report("Remotion 成片完成，后处理中…", 0.88)
            result = _apply_logo_overlay(storyboard, result)

    # 3) 本机失败后再试 Lambda（未优先时的卸载跳板）
    if result is None and try_lambda and not prefer_lambda:
        report("本机 Remotion 未成功，尝试 Lambda 渲染跳板…", 0.54)
        result = _try_remotion_lambda(storyboard, output_path, job_id=job_id)
        if result is not None:
            report("Lambda 成片完成，后处理中…", 0.88)
            result = _apply_logo_overlay(storyboard, result)

    if result is None and mode in ("ffmpeg", "auto"):
        report(
            "使用 FFmpeg 逐镜合成（支持局部复用）…"
            if prefer_partial
            else "使用 FFmpeg 逐镜合成画面与配音…",
            0.55,
        )
        result = _try_ffmpeg(
            storyboard,
            output_path,
            job_id=job_id,
            parent_job_id=parent_job_id,
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
            "无法渲染视频：请安装 ffmpeg，或配置 Remotion / Remotion Lambda"
        )

    if not result.has_audio and bool(getattr(storyboard, "ttsEnabled", True)):
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

    report("混入背景音乐…", 0.97)
    mixed = _try_mix_bgm(storyboard, result.output_path)
    if mixed is not None:
        result.output_path = mixed
        result.has_audio = True

    result = _apply_logo_overlay(storyboard, result)

    report("渲染收尾…", 0.99)
    return result


def _composition_id(template_id: str) -> str:
    return {
        "talking-captions": "TalkingCaptions",
        "kinetic-text": "KineticText",
        "brand-intro": "BrandIntro",
    }.get(template_id, "TalkingCaptions")


def _write_props(storyboard: Storyboard, output_path: Path) -> Path:
    props_path = output_path.with_suffix(".props.json")
    props_path.write_text(
        json.dumps(storyboard.to_public_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    return props_path


def _lambda_configured(settings) -> bool:
    if not bool(getattr(settings, "remotion_lambda_enabled", False)):
        return False
    return bool(
        (settings.remotion_lambda_region or "").strip()
        and (settings.remotion_lambda_function_name or "").strip()
        and (settings.remotion_lambda_serve_url or "").strip()
    )


def _try_remotion(
    storyboard: Storyboard, output_path: Path, *, job_id: str
) -> RenderResult | None:
    settings = get_settings()
    root = Path(settings.remotion_project_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    if not root.is_dir():
        logger.info("Remotion 项目不存在: %s", root)
        return None
    # 缺 tsconfig 时 Remotion CLI 会直接失败并刷屏，提前跳过走 FFmpeg
    if not (root / "tsconfig.json").is_file():
        logger.info("Remotion 缺少 tsconfig.json，跳过: %s", root)
        return None
    if not (root / "node_modules" / "remotion").is_dir() and not (
        root / "node_modules" / "@remotion" / "cli"
    ).is_dir():
        logger.info("Remotion 依赖未安装（请在 video-renderer 执行 npm i），跳过")
        return None
    npx = shutil.which("npx")
    if not npx:
        logger.info("未找到 npx，跳过 Remotion")
        return None

    props_path = _write_props(storyboard, output_path)
    composition = _composition_id(storyboard.templateId)
    concurrency = max(1, int(getattr(settings, "remotion_concurrency", 1) or 1))

    cmd = [
        npx,
        "remotion",
        "render",
        composition,
        str(output_path),
        f"--props={props_path}",
        f"--concurrency={concurrency}",
        "--log=verbose",
    ]
    env = os.environ.copy()
    # 限制 Node 堆，避免 2G 机被本机 Chromium + Node 一起打爆
    mem_mb = int(getattr(settings, "remotion_node_max_old_space_mb", 768) or 768)
    if mem_mb > 0:
        prev = env.get("NODE_OPTIONS", "")
        flag = f"--max-old-space-size={mem_mb}"
        env["NODE_OPTIONS"] = f"{prev} {flag}".strip() if prev else flag

    logger.info(
        "Remotion render job=%s composition=%s concurrency=%s",
        job_id,
        composition,
        concurrency,
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=int(settings.video_render_timeout_sec),
            check=False,
            env=env,
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


def _try_remotion_lambda(
    storyboard: Storyboard, output_path: Path, *, job_id: str
) -> RenderResult | None:
    """仅替换渲染执行器；TTS/BGM/缩略图仍走后续本机后处理。"""
    settings = get_settings()
    if not _lambda_configured(settings):
        return None

    root = Path(settings.remotion_project_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    script = root / "scripts" / "render-on-lambda.mjs"
    if not script.is_file():
        logger.info("缺少 Lambda 脚本: %s", script)
        return None
    node = shutil.which("node")
    if not node:
        logger.info("未找到 node，跳过 Remotion Lambda")
        return None

    props_path = _write_props(storyboard, output_path)
    composition = _composition_id(storyboard.templateId)
    env = os.environ.copy()
    env.update(
        {
            "REMOTION_LAMBDA_REGION": settings.remotion_lambda_region.strip(),
            "REMOTION_LAMBDA_FUNCTION_NAME": settings.remotion_lambda_function_name.strip(),
            "REMOTION_LAMBDA_SERVE_URL": settings.remotion_lambda_serve_url.strip(),
            "REMOTION_LAMBDA_TIMEOUT_MS": str(
                max(30_000, int(settings.video_render_timeout_sec) * 1000 - 5_000)
            ),
        }
    )
    cmd = [
        node,
        str(script),
        "--composition",
        composition,
        "--props",
        str(props_path),
        "--out",
        str(output_path),
    ]
    logger.info("Remotion Lambda render job=%s composition=%s", job_id, composition)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=int(settings.video_render_timeout_sec) + 30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Remotion Lambda 执行异常: %s", exc)
        return None

    if proc.returncode != 0 or not output_path.is_file():
        logger.warning(
            "Remotion Lambda 失败 code=%s stderr=%s stdout=%s",
            proc.returncode,
            (proc.stderr or "")[-600:],
            (proc.stdout or "")[-400:],
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
    job_id: str | None = None,
    parent_job_id: str | None = None,
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
    reused: list[str] = []
    job_stem = output_path.stem
    total = max(1, len(storyboard.scenes))
    persist_dir = clips_dir_for_job(job_id) if job_id else None
    cover = None

    with tempfile.TemporaryDirectory(prefix="ai-video-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        for ord_i, scene in enumerate(storyboard.scenes):
            if cancel_check and cancel_check():
                raise RuntimeError("CANCELLED")
            # 用列表位次展示，避免 scene.index 不连续时出现 6/5
            display_n = ord_i + 1
            base = 0.55 + 0.30 * (ord_i / total)
            sc_hash = scene_content_hash(
                scene,
                template_id=storyboard.templateId,
                speech_rate=storyboard.speechRate,
                tts_voice=storyboard.ttsVoice or "",
                caption_position=getattr(storyboard, "captionPosition", "bottom")
                or "bottom",
            )
            reused_clip = find_reusable_clip(
                parent_job_id=parent_job_id,
                scene_id=scene.id,
                content_hash=sc_hash,
            )
            part = tmp_path / f"{ord_i:02d}.mp4"
            if reused_clip is not None:
                report(
                    f"镜头 {display_n}/{total} · 复用未改内容",
                    base,
                )
                shutil.copyfile(reused_clip, part)
                reused.append(scene.id)
                has_audio = True
            else:
                report(
                    f"镜头 {display_n}/{total} · 绘制画面：{scene.headline[:24]}",
                    base,
                )
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
                    caption_position=getattr(storyboard, "captionPosition", "bottom")
                    or "bottom",
                ):
                    return None
                report(
                    f"镜头 {display_n}/{total} · 生成配音…",
                    base + 0.30 / total * 0.5,
                )
                if bool(getattr(storyboard, "ttsEnabled", True)):
                    voiced = _mux_scene_tts(
                        ffmpeg,
                        scene=scene,
                        video_path=part,
                        tmp_dir=tmp_path,
                        speech_rate=storyboard.speechRate,
                        voice_id=storyboard.ttsVoice,
                    )
                    if voiced is not None:
                        part = voiced
                        has_audio = True

            if persist_dir is not None and part.is_file():
                dest = persist_dir / f"{scene.id}_{sc_hash}.mp4"
                try:
                    if not dest.is_file():
                        shutil.copyfile(part, dest)
                except OSError as exc:
                    logger.warning("保存 clip 失败: %s", exc)

            thumb = output_path.parent / f"{job_stem}_s{ord_i}.jpg"
            if _extract_single_thumb(part, thumb):
                thumbs[scene.id] = thumb
            parts.append(part)

        if not parts:
            return None

        report(
            f"拼接成片（复用 {len(reused)} 镜）…" if reused else "拼接全部分镜成片…",
            0.88,
        )
        list_file = tmp_path / "list.txt"
        list_file.write_text(
            "\n".join(f"file '{p.name}'" for p in parts) + "\n",
            encoding="utf-8",
        )
        concat_copy = [
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(output_path),
        ]
        if not has_audio:
            concat_copy = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c:v", "copy", "-an", str(output_path),
            ]
        proc = subprocess.run(
            concat_copy, cwd=str(tmp_path), capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0 or not output_path.is_file():
            logger.warning(
                "ffmpeg concat copy 失败，回退重编码: %s",
                (proc.stderr or "")[-400:],
            )
            if output_path.is_file():
                output_path.unlink(missing_ok=True)
            concat_cmd = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            ]
            if has_audio:
                concat_cmd.extend(["-c:a", "aac", "-shortest"])
            else:
                concat_cmd.append("-an")
            concat_cmd.append(str(output_path))
            proc = subprocess.run(
                concat_cmd, cwd=str(tmp_path), capture_output=True, text=True, check=False,
            )
            if proc.returncode != 0 or not output_path.is_file():
                logger.warning(
                    "ffmpeg concat failed: %s", (proc.stderr or "")[-800:]
                )
                return None

        report("拼接完成，生成封面…", 0.92)
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
            reused_scene_ids=reused,
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


def _accent_hex(scene: Scene) -> str:
    """返回 0xRRGGBB 形式的强调色，供 ffmpeg drawbox 使用。"""
    raw = (scene.accentColor or "#38BDF8").lstrip("#")
    if len(raw) != 6:
        return "0x38BDF8"
    return f"0x{raw}"


def _build_scene_drawtext_vf(
    *,
    template_id: str,
    scene: Scene,
    font: str,
    width: int,
    height: int,
    caption_position: str = "bottom",
) -> str:
    """
    按模板生成 lavfi drawtext/drawbox 滤镜链（Pillow 失败时的主视觉差异）。

    - talking-captions：字幕条（上/中/下）+ 左侧强调条
    - kinetic-text：顶部色带 + 大号居中标题 + 镜号
    - brand-intro：居中描边框 + 标题/副文
    """
    tid = (template_id or "talking-captions").strip()
    headline = _escape_drawtext(scene.headline[:40])
    body = _escape_drawtext((scene.body or "")[:60])
    accent = _accent_hex(scene)
    font_q = font.replace(":", "\\:")
    parts: list[str] = []

    if tid == "kinetic-text":
        top_h = max(48, int(height * 0.20))
        parts.append(
            f"drawbox=x=0:y=0:w={width}:h={top_h}:color={accent}@0.95:t=fill"
        )
        parts.append(
            f"drawbox=x=0:y={height - 16}:w={width}:h=16:color={accent}@0.95:t=fill"
        )
        parts.append(
            f"drawtext=fontfile='{font_q}':text='{scene.index + 1:02d}':"
            f"fontsize=28:fontcolor=white:x=28:y=28"
        )
        parts.append(
            f"drawtext=fontfile='{font_q}':text='{headline}':fontsize=52:"
            f"fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-20:"
            f"box=1:boxcolor=black@0.55:boxborderw=18"
        )
        if body:
            parts.append(
                f"drawtext=fontfile='{font_q}':text='{body}':fontsize=24:"
                f"fontcolor=white@0.9:x=(w-text_w)/2:y=(h-text_h)/2+60:"
                f"box=1:boxcolor=black@0.4:boxborderw=12"
            )
    elif tid == "brand-intro":
        mx = int(width * 0.1)
        my = int(height * 0.30)
        bw = width - 2 * mx
        bh = int(height * 0.38)
        parts.append(
            f"drawbox=x={mx}:y={my}:w={bw}:h={bh}:color={accent}@0.95:t=6"
        )
        # 顶部圆点用小色块近似
        cx = width // 2 - 24
        cy = int(height * 0.22) - 24
        parts.append(
            f"drawbox=x={cx}:y={cy}:w=48:h=48:color={accent}@0.95:t=fill"
        )
        parts.append(
            f"drawtext=fontfile='{font_q}':text='{headline}':fontsize=40:"
            f"fontcolor=white:x=(w-text_w)/2:y={my + int(bh * 0.28)}:"
            f"box=0"
        )
        if body:
            parts.append(
                f"drawtext=fontfile='{font_q}':text='{body}':fontsize=24:"
                f"fontcolor=white@0.85:x=(w-text_w)/2:y={my + int(bh * 0.58)}"
            )
    else:
        # talking-captions：加高字幕条 + 略缩小字号，减少裁切
        bar_h = max(140, int(height * 0.36))
        cap = (caption_position or "bottom").strip()
        if cap == "top":
            bar_y = 0
        elif cap == "center":
            bar_y = int(height * 0.34)
        else:
            bar_y = height - bar_h
        parts.append(
            f"drawbox=x=0:y={bar_y}:w={width}:h={bar_h}:color=black@0.88:t=fill"
        )
        parts.append(
            f"drawbox=x=0:y={bar_y}:w=14:h={bar_h}:color={accent}@1:t=fill"
        )
        # 按字数硬换行，避免单行画出字幕条
        hl_lines = _wrap_cjk_lines(scene.headline or "", 14)[:2]
        body_lines = _wrap_cjk_lines(scene.body or "", 18)[:3]
        y = bar_y + 22
        for i, line in enumerate(hl_lines):
            esc = _escape_drawtext(line)
            parts.append(
                f"drawtext=fontfile='{font_q}':text='{esc}':fontsize=34:"
                f"fontcolor=white:x=36:y={y + i * 40}"
            )
        y = bar_y + 22 + len(hl_lines) * 40 + 8
        for i, line in enumerate(body_lines):
            esc = _escape_drawtext(line)
            parts.append(
                f"drawtext=fontfile='{font_q}':text='{esc}':fontsize=22:"
                f"fontcolor=white@0.88:x=36:y={y + i * 30}"
            )

    return ",".join(parts)


def _wrap_cjk_lines(text: str, max_chars: int) -> list[str]:
    """按字数折行（中文无空格）。"""
    s = re.sub(r"\s+", "", (text or "").strip())
    if not s:
        return []
    max_chars = max(4, int(max_chars))
    return [s[i : i + max_chars] for i in range(0, len(s), max_chars)]


def _make_scene_card_png(
    scene: Scene,
    *,
    width: int,
    height: int,
    output: Path,
    template_id: str = "talking-captions",
    transparent_bg: bool = False,
    caption_position: str = "bottom",
) -> Path | None:
    """用 Pillow 画字幕卡；transparent_bg=True 时仅画字幕叠层（RGBA），用于视频底图。"""
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

    if transparent_bg:
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    else:
        img = Image.new("RGB", (width, height), rgb)
        # 有配图时铺满作为背景
        bg_path = _resolve_image_file(getattr(scene, "imageUrl", "") or "")
        if bg_path is not None:
            try:
                bg_img = Image.open(bg_path).convert("RGB")
                bg_img = bg_img.resize((width, height), Image.Resampling.LANCZOS)
                img.paste(bg_img, (0, 0))
            except Exception as exc:  # noqa: BLE001
                logger.warning("加载分镜配图失败: %s", exc)
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
                font_large = ImageFont.truetype(fp, 36)
                font_small = ImageFont.truetype(fp, 24)
                break
            except OSError:
                continue

    headline = (scene.headline or "")[:48]
    body = (scene.body or "")[:90]
    tid = (template_id or "talking-captions").strip()

    # 模板装饰差异
    if tid == "kinetic-text":
        # 与 Remotion 对齐：矩形顶带 + 底条 + 左上镜号
        top_h = int(height * 0.20)
        draw.rectangle(
            [0, 0, width, top_h],
            fill=(*accent_rgb, 240) if transparent_bg else accent_rgb,
        )
        draw.rectangle(
            [0, height - 16, width, height],
            fill=(*accent_rgb, 240) if transparent_bg else accent_rgb,
        )
        draw.text(
            (32, 36),
            f"{scene.index + 1:02d}",
            fill=(255, 255, 255, 255) if transparent_bg else (255, 255, 255),
            font=font_small,
        )
        title_y = int(height * 0.42)
        body_y = int(height * 0.58)
    elif tid == "brand-intro":
        # 居中品牌框；首镜略加大色点
        is_first = int(getattr(scene, "index", 0) or 0) == 0
        margin = int(width * 0.1)
        box = [margin, int(height * 0.30), width - margin, int(height * 0.70)]
        outline = (*accent_rgb, 255) if transparent_bg else accent_rgb
        try:
            draw.rounded_rectangle(box, radius=28, outline=outline, width=6)
        except AttributeError:
            draw.rectangle(box, outline=outline, width=6)
        dot_r = 36 if is_first else 28
        draw.ellipse(
            [
                width // 2 - dot_r,
                int(height * 0.20) - dot_r,
                width // 2 + dot_r,
                int(height * 0.20) + dot_r,
            ],
            fill=(*accent_rgb, 255) if transparent_bg else accent_rgb,
        )
        title_y = int(height * 0.42)
        body_y = int(height * 0.55)
    else:
        # talking-captions：加高字幕条 + 自动折行，避免字幕被裁切
        bar_h = int(height * 0.36)
        bar_fill = (0, 0, 0, 220) if transparent_bg else (0, 0, 0)
        accent_fill = (*accent_rgb, 255) if transparent_bg else accent_rgb
        cap = (caption_position or "bottom").strip()
        if cap == "top":
            bar_y0, bar_y1 = 0, bar_h
        elif cap == "center":
            bar_y0 = int(height * 0.34)
            bar_y1 = bar_y0 + bar_h
        else:
            bar_y0, bar_y1 = height - bar_h, height
        draw.rectangle([0, bar_y0, width, bar_y1], fill=bar_fill)
        draw.rectangle([0, bar_y0, 14, bar_y1], fill=accent_fill)
        title_y = bar_y0 + 28
        body_y = bar_y0 + 100

    text_fill = (255, 255, 255, 255) if transparent_bg else (255, 255, 255)
    box_fill = (0, 0, 0, 180) if transparent_bg else (0, 0, 0)
    max_text_w = width - 72

    def _measure(text: str, font) -> tuple[int, int]:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def _wrap_to_width(text: str, font, max_w: int, max_lines: int) -> list[str]:
        raw = (text or "").strip()
        if not raw:
            return []
        lines: list[str] = []
        cur = ""
        for ch in raw:
            trial = cur + ch
            tw, _ = _measure(trial, font)
            if tw <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
                if len(lines) >= max_lines:
                    break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        elif cur and lines:
            # 末行加省略
            last = lines[-1]
            ell = "…"
            while last and _measure(last + ell, font)[0] > max_w:
                last = last[:-1]
            lines[-1] = (last + ell) if last else ell
        return lines

    def _draw_text(text: str, font, y: int, *, boxed: bool = True) -> None:
        if not text:
            return
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        # 口播模板左对齐；其余居中
        if tid == "talking-captions":
            x = 36
        else:
            x = max(16, (width - tw) // 2)
        if boxed and tid != "talking-captions":
            pad = 16
            draw.rectangle(
                [x - pad, y - 6, x + tw + pad, y + (bbox[3] - bbox[1]) + 10],
                fill=box_fill,
            )
        draw.text((x, y), text, fill=text_fill, font=font)

    if tid == "talking-captions":
        hl_lines = _wrap_to_width(headline, font_large, max_text_w, 2)
        body_lines = _wrap_to_width(body, font_small, max_text_w, 3)
        y = title_y
        for line in hl_lines:
            _draw_text(line, font_large, y, boxed=False)
            y += 42
        y += 6
        for line in body_lines:
            _draw_text(line, font_small, y, boxed=False)
            y += 32
    else:
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
    caption_position: str = "bottom",
) -> bool:
    color = scene.bgColor.lstrip("#")
    video_src = _resolve_media_file(getattr(scene, "videoUrl", "") or "")

    # 有短视频底图：视频铺满 + 透明字幕叠层（按 trim 起点裁一段，避免永远播首帧）
    if video_src is not None:
        overlay = output.with_suffix(".overlay.png")
        trim_start = max(0.0, float(getattr(scene, "videoTrimStartSec", 0) or 0))
        trim_dur = max(0.5, float(scene.durationSec))
        if _make_scene_card_png(
            scene,
            width=width,
            height=height,
            output=overlay,
            template_id=template_id,
            transparent_bg=True,
            caption_position=caption_position,
        ):
            vf = (
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"trim=start={trim_start}:duration={trim_dur},"
                f"setpts=PTS-STARTPTS,fps={fps}[bg];"
                f"[1:v]format=rgba,fps={fps}[ov];"
                f"[bg][ov]overlay=0:0:shortest=1[vout]"
            )
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                f"{trim_start:.3f}",
                "-i",
                str(video_src),
                "-loop",
                "1",
                "-i",
                str(overlay),
                "-filter_complex",
                # 输入已 -ss，滤镜内再 trim 从 0 起裁时长即可
                (
                    f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},trim=duration={trim_dur},"
                    f"setpts=PTS-STARTPTS,fps={fps}[bg];"
                    f"[1:v]format=rgba,fps={fps}[ov];"
                    f"[bg][ov]overlay=0:0:shortest=1[vout]"
                ),
                "-map",
                "[vout]",
                "-t",
                f"{trim_dur}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(output),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            try:
                overlay.unlink(missing_ok=True)
            except OSError:
                pass
            if proc.returncode == 0 and output.is_file():
                return True
            logger.warning(
                "分镜视频底图渲染失败，回退静图: %s",
                (proc.stderr or "")[-300:],
            )

    # 优先 Pillow 字幕卡（兼容无 drawtext 的 ffmpeg）
    card = output.with_suffix(".png")
    if _make_scene_card_png(
        scene,
        width=width,
        height=height,
        output=card,
        template_id=template_id,
        caption_position=caption_position,
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
        vf = _build_scene_drawtext_vf(
            template_id=template_id,
            scene=scene,
            font=font,
            width=width,
            height=height,
            caption_position=caption_position,
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


def _atempo_chain(speed: float) -> str:
    """生成 ffmpeg atempo 链（单段仅支持 0.5～2.0）。"""
    speed = max(0.5, min(3.0, float(speed)))
    factors: list[float] = []
    remaining = speed
    while remaining > 2.0 + 1e-6:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-6:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(round(remaining, 4))
    return ",".join(f"atempo={f}" for f in factors)


def _mux_scene_tts(
    ffmpeg: str,
    *,
    scene: Scene,
    video_path: Path,
    tmp_dir: Path,
    speech_rate: float = 1.0,
    voice_id: str | None = None,
) -> Path | None:
    """为单镜合成配音并混入；失败返回 None（保留静音片）。

    画面时长严格按 scene.durationSec。
    先按时长精简口播再 TTS；仅在仍略长时轻微加速（≤1.15x），避免听起来过快。
    """
    scene_dur = max(1.0, float(scene.durationSec))
    raw = scene_narration_text(headline=scene.headline, body=scene.body or "")
    text = fit_narration_to_duration(
        raw, scene_dur, speech_rate=speech_rate
    )
    if not text:
        return None
    if text != raw:
        logger.info(
            "口播按时长精简 scene=%s %s→%s字 dur=%.1fs",
            scene.index,
            len(raw),
            len(text),
            scene_dur,
        )
    audio_path = synthesize_to_file(
        text,
        tmp_dir / f"{scene.index:02d}.wav",
        speech_rate=speech_rate,
        voice_id=voice_id,
    )
    if audio_path is None:
        return None

    audio_dur = probe_wav_duration_sec(audio_path) or scene_dur

    out = tmp_dir / f"{scene.index:02d}_voiced.mp4"
    af_parts: list[str] = []
    if audio_dur > scene_dur + 0.12:
        # 仅允许轻微加速；仍超长交给 atrim 截尾，不再 2x 赶读
        speed = min(1.15, audio_dur / scene_dur)
        if speed > 1.02:
            af_parts.append(_atempo_chain(speed))
    # 偏短补静音，偏长截断，保证与画面同长
    af_parts.append(f"apad=whole_dur={scene_dur:.3f}")
    af_parts.append(f"atrim=0:{scene_dur:.3f}")
    af = ",".join(af_parts)

    mux_cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-filter_complex",
        f"[1:a]{af}[a]",
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-t",
        f"{scene_dur:.3f}",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
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
            voice_id=storyboard.ttsVoice,
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


def _extract_single_thumb(
    video_path: Path,
    thumb_path: Path,
    *,
    ss: float = 0.25,
) -> Path | None:
    """从成片抽取一帧作为封面/缩略图。"""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg or not video_path.is_file():
        return None
    thumb_path = Path(thumb_path)
    seek = max(0.0, float(ss))
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{seek:.2f}",
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


def extract_video_frame(
    video_path: Path,
    thumb_path: Path,
    *,
    ss: float = 0.25,
) -> Path | None:
    """对外：按秒数从成片抽帧。"""
    return _extract_single_thumb(video_path, thumb_path, ss=ss)


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
    from app.video.bgm_catalog import resolve_bgm_track

    settings = get_settings()
    track = resolve_bgm_track(storyboard.bgmTrackId)
    if not track or track.get("id") == "off":
        return None
    enabled = bool(storyboard.bgmEnabled and settings.video_bgm_enabled)
    if not enabled:
        return None
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg or not video_path.is_file():
        return None

    duration = max(3.0, float(storyboard.total_duration_sec or 12.0))
    track_vol = float(track.get("volume") or 0.16)
    vol = float(
        storyboard.bgmVolume if storyboard.bgmVolume is not None else track_vol
    )
    vol = max(0.0, min(1.0, vol))
    # 优先：曲目自带文件 → 全局 video_bgm_file → lavfi 兜底
    from app.video.bgm_catalog import resolve_track_file

    track_path = resolve_track_file(track)
    global_file = (settings.video_bgm_file or "").strip()
    bgm_file = ""
    if track_path is not None:
        bgm_file = str(track_path)
    elif global_file and Path(global_file).is_file():
        bgm_file = global_file
    lavfi = str(track.get("lavfi") or "").strip()
    af_extra = str(track.get("afExtra") or "lowpass=f=1200").strip()

    with tempfile.TemporaryDirectory(prefix="ai-video-bgm-") as tmp:
        tmp_path = Path(tmp)
        bgm_wav = tmp_path / "bgm.wav"
        if bgm_file:
            fade_out_st = max(0.5, duration - 1.0)
            cmd = [
                ffmpeg,
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                bgm_file,
                "-t",
                f"{duration:.2f}",
                "-af",
                f"volume={vol:.3f},"
                f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_st:.2f}:d=1.0",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(bgm_wav),
            ]
        elif lavfi:
            src = lavfi
            if "duration=" not in src:
                if src.startswith("anoisesrc") or src.startswith("sine="):
                    sep = ":" if "=" in src else ":"
                    src = f"{src}{sep}duration={duration:.2f}"
            fade_out_st = max(0.5, duration - 1.0)
            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                src,
                "-af",
                f"{af_extra},volume={vol:.3f},"
                f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_st:.2f}:d=1.0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-t",
                f"{duration:.2f}",
                str(bgm_wav),
            ]
        else:
            return None
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not bgm_wav.is_file():
            logger.warning("生成 BGM 失败: %s", (proc.stderr or "")[-300:])
            return None

        out = video_path.with_name(video_path.stem + "_bgm.mp4")
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

def _resolve_image_file(url_or_path: str) -> Path | None:
    """把 imageUrl/logoUrl 解析为本地图片文件。"""
    return _resolve_media_file(
        url_or_path,
        allowed_suffixes={".png", ".jpg", ".jpeg", ".webp", ".gif"},
        default_suffix=".jpg",
    )


def _resolve_media_file(
    url_or_path: str,
    *,
    allowed_suffixes: set[str] | None = None,
    default_suffix: str = ".mp4",
) -> Path | None:
    """把 imageUrl/videoUrl/logoUrl 解析为本地文件（支持 /cdn/video 与 http）。"""
    raw = (url_or_path or "").strip()
    if not raw:
        return None
    suffixes = allowed_suffixes or {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".mp4",
        ".webm",
        ".mov",
    }
    settings = get_settings()
    root = Path(settings.video_output_dir)
    if not root.is_absolute():
        root = Path.cwd() / root

    if raw.startswith("/cdn/video/"):
        rel = raw[len("/cdn/video/") :]
        local = root / rel
        if local.is_file():
            return local
    if raw.startswith("cdn/video/"):
        local = root / raw[len("cdn/video/") :]
        if local.is_file():
            return local

    local2 = Path(raw)
    if local2.is_file():
        return local2
    maybe = root / Path(raw).name
    if maybe.is_file():
        return maybe
    maybe_assets = root / "assets" / Path(raw).name
    if maybe_assets.is_file():
        return maybe_assets

    if raw.startswith("http://") or raw.startswith("https://"):
        try:
            suffix = Path(raw.split("?", 1)[0]).suffix.lower() or default_suffix
            if suffix not in suffixes:
                suffix = default_suffix
            dest = (
                root
                / "assets"
                / f"fetch_{hashlib.sha1(raw.encode()).hexdigest()[:12]}{suffix}"
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file():
                urllib.request.urlretrieve(raw, str(dest))  # noqa: S310
            if dest.is_file() and dest.stat().st_size > 0:
                return dest
        except Exception as exc:  # noqa: BLE001
            logger.warning("下载媒体失败 url=%s: %s", raw[:120], exc)
            return None
    return None


def _logo_overlay_xy(
    position: str, *, width: int, height: int, logo_w: int, logo_h: int, margin: int = 24
) -> tuple[int, int]:
    pos = (position or "top-right").strip()
    if pos == "top-left":
        return margin, margin
    if pos == "bottom-left":
        return margin, max(margin, height - logo_h - margin)
    if pos == "bottom-right":
        return max(margin, width - logo_w - margin), max(margin, height - logo_h - margin)
    return max(margin, width - logo_w - margin), margin


def _apply_logo_overlay(storyboard: Storyboard, result: RenderResult) -> RenderResult:
    """成片角标 Logo；无 logoUrl 时原样返回。"""
    logo_url = (getattr(storyboard, "logoUrl", "") or "").strip()
    if not logo_url or result.output_path is None or not result.output_path.is_file():
        return result
    logo_path = _resolve_image_file(logo_url)
    if logo_path is None:
        logger.warning("Logo 文件不可用: %s", logo_url[:120])
        return result
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return result

    w, h = _resolution(storyboard.aspectRatio)
    target_w = max(64, int(w * 0.18))
    try:
        from PIL import Image

        with Image.open(logo_path) as im:
            iw, ih = im.size
        if iw <= 0 or ih <= 0:
            return result
        target_h = max(32, int(target_w * ih / iw))
    except Exception:
        target_h = int(target_w * 0.45)

    x, y = _logo_overlay_xy(
        getattr(storyboard, "logoPosition", "top-right") or "top-right",
        width=w,
        height=h,
        logo_w=target_w,
        logo_h=target_h,
    )
    out = result.output_path.with_name(result.output_path.stem + "_logo.mp4")
    filt = (
        f"[1:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[lg];"
        f"[0:v][lg]overlay={x}:{y}:format=auto"
    )
    cmd = [
        ffmpeg, "-y",
        "-i", str(result.output_path),
        "-i", str(logo_path),
        "-filter_complex", filt,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-shortest",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.is_file():
        cmd2 = [
            ffmpeg, "-y",
            "-i", str(result.output_path),
            "-i", str(logo_path),
            "-filter_complex", filt,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-an",
            str(out),
        ]
        proc2 = subprocess.run(cmd2, capture_output=True, text=True, check=False)
        if proc2.returncode != 0 or not out.is_file():
            logger.warning("Logo overlay 失败: %s", (proc.stderr or proc2.stderr or "")[-400:])
            return result
    try:
        shutil.move(str(out), str(result.output_path))
    except OSError:
        result.output_path = out
    return result

