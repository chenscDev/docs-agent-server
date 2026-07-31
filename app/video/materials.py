"""多素材（图/短视频）规范化与分镜绑定。"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any, Literal

from app.video.schema import Storyboard, validate_storyboard

MaterialKind = Literal["image", "video"]

_MAX_MATERIALS = 9
# 有用户视频时，成片目标总时长（秒）
_TARGET_VIDEO_TOTAL_SEC = 10.0

logger = logging.getLogger(__name__)


def normalize_materials(raw: list[Any] | None) -> list[dict[str, str]]:
    """
    规范化客户端 materials。

    每项：{ url, kind: image|video, caption? }
    最多 9 条；非法项丢弃。
    """
    if not raw:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in ("image", "video"):
            # 按后缀猜测
            lower = url.lower().split("?")[0]
            if any(lower.endswith(ext) for ext in (".mp4", ".webm", ".mov")):
                kind = "video"
            else:
                kind = "image"
        # 轻量白名单：本站 assets 或 http(s)
        if not (
            url.startswith("/cdn/video/")
            or url.startswith("http://")
            or url.startswith("https://")
        ):
            continue
        seen.add(url)
        row: dict[str, str] = {"url": url[:500], "kind": kind}
        caption = str(item.get("caption") or "").strip()
        if caption:
            row["caption"] = caption[:200]
        out.append(row)
        if len(out) >= _MAX_MATERIALS:
            break
    return out


def attach_materials_to_scenes(
    board: Storyboard,
    materials: list[dict[str, str]] | None,
) -> Storyboard:
    """
    按顺序把素材写入各镜：video → videoUrl，image → imageUrl。

    - 素材多于镜头：只绑定前 N 镜
    - 镜头多于素材：循环复用素材
    - 有视频时：按镜连续错开 trim 起点，整片约取素材中最优约 10 秒窗口
    """
    mats = normalize_materials(materials)
    if not mats:
        return board
    data = board.model_dump()
    scenes = list(data.get("scenes") or [])
    if not scenes:
        return board
    n = len(mats)
    for i, sc in enumerate(scenes):
        mat = mats[i % n]
        url = mat["url"]
        if mat["kind"] == "video":
            sc["videoUrl"] = url
            # 有视频时清空配图，避免渲染歧义
            sc["imageUrl"] = ""
        else:
            sc["imageUrl"] = url
            sc["videoUrl"] = ""
            sc["videoTrimStartSec"] = 0.0
        # 补充画面说明，便于 Remix / 生图
        if not (sc.get("visualHint") or "").strip():
            cap = (mat.get("caption") or "").strip()
            sc["visualHint"] = (
                cap[:120]
                if cap
                else (
                    "用户短视频素材"
                    if mat["kind"] == "video"
                    else "用户图片素材"
                )
            )[:120]
    data["scenes"] = scenes
    board = validate_storyboard(data)
    return apply_video_trim_timeline(board)


def apply_video_trim_timeline(board: Storyboard) -> Storyboard:
    """
    同一 videoUrl 多镜时连续推进时间轴，避免每镜都从 0 秒重播首帧。

    源片长于成片目标时，取中间一段（略跳开头黑场）作为最优窗口。
    """
    data = board.model_dump()
    scenes = list(data.get("scenes") or [])
    if not scenes:
        return board

    # url → 该片被绑到的镜下标（按出场顺序）
    groups: dict[str, list[int]] = {}
    for i, sc in enumerate(scenes):
        url = str(sc.get("videoUrl") or "").strip()
        if not url:
            continue
        groups.setdefault(url, []).append(i)

    for url, indices in groups.items():
        source_dur = probe_media_duration_sec(url)
        if source_dur is None or source_dur <= 0.5:
            source_dur = 18.0  # 探测失败时按常见短视频时长兜底
        need = float(sum(float(scenes[i].get("durationSec") or 3.0) for i in indices))
        # 成片窗口：贴近目标 10 秒，且不超过源片
        window = min(source_dur, max(need, min(_TARGET_VIDEO_TOTAL_SEC, source_dur)))
        if need > window + 0.05:
            # 总镜长超出窗口：等比压缩各镜时长，保证连续不重叠且不越界
            scale = window / need
            for i in indices:
                d = float(scenes[i].get("durationSec") or 3.0) * scale
                scenes[i]["durationSec"] = max(1.0, round(d, 2))
            need = float(
                sum(float(scenes[i].get("durationSec") or 3.0) for i in indices)
            )
        # 略跳过片头 0.3s，再尽量居中取窗
        pad = 0.3 if source_dur > window + 0.6 else 0.0
        usable = max(0.0, source_dur - pad)
        start0 = pad + max(0.0, (usable - need) / 2.0)
        if start0 + need > source_dur:
            start0 = max(0.0, source_dur - need)

        cursor = start0
        for i in indices:
            scenes[i]["videoTrimStartSec"] = round(max(0.0, cursor), 3)
            cursor += float(scenes[i].get("durationSec") or 3.0)

    data["scenes"] = scenes
    try:
        return validate_storyboard(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_video_trim_timeline 校验失败: %s", exc)
        return board


def probe_media_duration_sec(url_or_path: str) -> float | None:
    """ffprobe 探测音视频时长；失败返回 None。"""
    path = _resolve_for_probe(url_or_path)
    if not path:
        return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        try:
            import imageio_ffmpeg

            # imageio 通常只带 ffmpeg；用 ffmpeg -i 解析
            return _probe_via_ffmpeg(path)
        except Exception:  # noqa: BLE001
            return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip())
    except Exception as exc:  # noqa: BLE001
        logger.debug("ffprobe failed: %s", exc)
        return None


def _probe_via_ffmpeg(path: str) -> float | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", path],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        # Duration: 00:00:18.40
        import re

        m = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
            proc.stderr or "",
        )
        if not m:
            return None
        h, mi, s = m.groups()
        return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:  # noqa: BLE001
        return None


def _resolve_for_probe(url_or_path: str) -> str | None:
    raw = (url_or_path or "").strip()
    if not raw:
        return None
    try:
        from app.video.renderer import _resolve_media_file

        p = _resolve_media_file(raw)
        if p is not None and p.is_file():
            return str(p)
    except Exception:  # noqa: BLE001
        pass
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return None


def target_scene_count(materials: list[dict[str, str]] | None, default: int = 4) -> int:
    """有素材时镜头数贴近素材数；单视频约 3 镜凑满约 10 秒。"""
    mats = normalize_materials(materials)
    if not mats:
        return max(3, min(6, default))
    videos = [m for m in mats if m.get("kind") == "video"]
    if len(videos) == 1 and len(mats) == 1:
        return 3
    if videos and len(mats) <= 2:
        return max(2, min(4, len(mats) + 1))
    return max(3, min(_MAX_MATERIALS, len(mats)))


def target_total_duration_sec(materials: list[dict[str, str]] | None) -> float:
    """有视频素材时目标成片约 10 秒。"""
    mats = normalize_materials(materials)
    if any(m.get("kind") == "video" for m in mats):
        return _TARGET_VIDEO_TOTAL_SEC
    return 16.0
