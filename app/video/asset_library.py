"""素材库：历史上传索引、按项目分组、从成片/分镜快照入库。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_id
from app.db.models import VideoAsset, VideoJob
from app.video.assets import assets_dir
from app.video.materials import normalize_materials

logger = logging.getLogger(__name__)


def asset_to_dict(row: VideoAsset) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "url": row.url,
        "filename": row.filename,
        "caption": row.caption,
        "projectId": row.project_id,
        "projectTitle": row.project_title,
        "sourceType": row.source_type,
        "sourceJobId": row.source_job_id,
        "ownerId": row.owner_id,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _default_owner(owner_id: str | None) -> str | None:
    settings = get_settings()
    return (owner_id or settings.video_default_owner_id or "").strip() or None


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path or url
    return Path(path).name[:255]


def find_asset_by_url(
    db: Session,
    url: str,
    *,
    owner_id: str | None = None,
) -> VideoAsset | None:
    u = (url or "").strip()
    if not u:
        return None
    stmt = select(VideoAsset).where(VideoAsset.url == u)
    owner = _default_owner(owner_id)
    if owner:
        stmt = stmt.where(VideoAsset.owner_id == owner)
    return db.scalar(stmt.limit(1))


def register_asset(
    db: Session,
    *,
    url: str,
    kind: str = "image",
    filename: str | None = None,
    caption: str | None = None,
    project_id: str = "inbox",
    project_title: str | None = None,
    source_type: str = "upload",
    source_job_id: str | None = None,
    owner_id: str | None = None,
    asset_id: str | None = None,
) -> VideoAsset:
    """写入或更新素材库索引（同 URL 去重）。"""
    u = (url or "").strip()
    if not u:
        raise ValueError("url 不能为空")
    k = (kind or "image").strip().lower()
    if k not in ("image", "video"):
        k = "image"
    owner = _default_owner(owner_id)
    existing = find_asset_by_url(db, u, owner_id=owner)
    if existing is not None:
        # 补全分组信息（例如从成片入库时归属到项目）
        if project_id and project_id != "inbox":
            existing.project_id = project_id[:128]
            if project_title:
                existing.project_title = project_title[:255]
        if caption and not existing.caption:
            existing.caption = caption[:500]
        if source_job_id and not existing.source_job_id:
            existing.source_job_id = source_job_id
        if source_type and existing.source_type == "upload":
            existing.source_type = source_type[:32]
        db.commit()
        db.refresh(existing)
        return existing

    row = VideoAsset(
        id=(asset_id or new_id("vass"))[:64],
        kind=k,
        url=u[:1024],
        filename=(filename or _filename_from_url(u) or None),
        caption=(caption or "").strip()[:500] or None,
        project_id=(project_id or "inbox")[:128],
        project_title=(project_title or "").strip()[:255] or None,
        source_type=(source_type or "upload")[:32],
        source_job_id=source_job_id,
        owner_id=owner,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_assets(
    db: Session,
    *,
    limit: int = 60,
    project_id: str | None = None,
    owner_id: str | None = None,
    kind: str | None = None,
) -> list[VideoAsset]:
    stmt = select(VideoAsset).order_by(VideoAsset.created_at.desc())
    owner = _default_owner(owner_id)
    if owner:
        stmt = stmt.where(VideoAsset.owner_id == owner)
    pid = (project_id or "").strip()
    if pid and pid != "all":
        stmt = stmt.where(VideoAsset.project_id == pid)
    k = (kind or "").strip().lower()
    if k in ("image", "video"):
        stmt = stmt.where(VideoAsset.kind == k)
    stmt = stmt.limit(max(1, min(limit, 200)))
    return list(db.scalars(stmt).all())


def list_asset_projects(
    db: Session,
    *,
    owner_id: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """按项目汇总素材数量（用于端上筛选）。"""
    rows = list_assets(db, limit=200, owner_id=owner_id)
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = row.project_id or "inbox"
        if pid not in buckets:
            buckets[pid] = {
                "id": pid,
                "title": row.project_title
                or ("上传箱" if pid == "inbox" else pid),
                "count": 0,
            }
        buckets[pid]["count"] += 1
    items = list(buckets.values())
    items.sort(key=lambda x: (0 if x["id"] == "inbox" else 1, -x["count"]))
    return items[: max(1, min(limit, 100))]


def delete_asset(db: Session, asset_id: str, *, remove_file: bool = False) -> bool:
    row = db.get(VideoAsset, asset_id)
    if row is None:
        return False
    if remove_file and row.filename:
        try:
            path = assets_dir() / Path(row.filename).name
            if path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("删除素材文件失败: %s", exc)
    db.delete(row)
    db.commit()
    return True


def import_job_to_library(
    db: Session,
    job: VideoJob,
    *,
    include_materials: bool = True,
    include_scenes: bool = True,
    include_cover: bool = True,
    include_output: bool = False,
    owner_id: str | None = None,
) -> list[VideoAsset]:
    """把任务素材 / 分镜图 / 封面快照进素材库，按任务 id 分组。"""
    title = (job.title or job.prompt or job.id)[:40]
    project_id = job.id
    owner = owner_id or getattr(job, "owner_id", None)
    collected: list[tuple[str, str, str | None, str]] = []

    if include_materials and job.materials_json:
        try:
            raw = json.loads(job.materials_json)
        except json.JSONDecodeError:
            raw = []
        for m in normalize_materials(raw if isinstance(raw, list) else []):
            collected.append(
                (
                    m["url"],
                    m["kind"],
                    m.get("caption"),
                    "job_material",
                )
            )

    if include_scenes and job.storyboard_json:
        try:
            board = json.loads(job.storyboard_json) or {}
        except json.JSONDecodeError:
            board = {}
        for scene in board.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            vurl = str(scene.get("videoUrl") or "").strip()
            iurl = str(scene.get("imageUrl") or "").strip()
            if vurl:
                collected.append((vurl, "video", None, "scene"))
            elif iurl:
                collected.append((iurl, "image", None, "scene"))

    if include_cover and job.cover_url:
        collected.append((job.cover_url, "image", None, "cover"))
    if include_output and job.output_url:
        collected.append((job.output_url, "video", None, "output"))

    # 去重保序
    seen: set[str] = set()
    out: list[VideoAsset] = []
    for url, kind, caption, source_type in collected:
        if not url or url in seen:
            continue
        # 跳过非本站可复用的临时本地路径
        if not (
            url.startswith("/cdn/video/")
            or url.startswith("http://")
            or url.startswith("https://")
        ):
            continue
        seen.add(url)
        row = register_asset(
            db,
            url=url,
            kind=kind,
            caption=caption,
            project_id=project_id,
            project_title=title,
            source_type=source_type,
            source_job_id=job.id,
            owner_id=owner,
        )
        out.append(row)
    return out
