"""客户端偏好与公开元信息（P2-D3）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import raise_api_error
from app.db.models import KnowledgeBase
from app.db.session import DEFAULT_KB_ID, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["meta"])


def _prefs_path() -> Path:
    settings = get_settings()
    # 与 DB 同目录旁路存放，避免再加表
    db_url = settings.database_url
    if db_url.startswith("sqlite:///"):
        db_file = Path(db_url.replace("sqlite:///", "", 1))
        if not db_file.is_absolute():
            db_file = Path.cwd() / db_file
        root = db_file.parent
    else:
        root = Path.cwd() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / "client_prefs.json"


def _load_prefs() -> dict:
    path = _prefs_path()
    if not path.is_file():
        return {"currentKnowledgeBaseId": DEFAULT_KB_ID}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"currentKnowledgeBaseId": DEFAULT_KB_ID}
        data.setdefault("currentKnowledgeBaseId", DEFAULT_KB_ID)
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 prefs 失败: %s", exc)
        return {"currentKnowledgeBaseId": DEFAULT_KB_ID}


def _save_prefs(data: dict) -> None:
    path = _prefs_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class PrefsUpdate(BaseModel):
    current_knowledge_base_id: str | None = Field(
        default=None,
        alias="currentKnowledgeBaseId",
    )

    model_config = ConfigDict(populate_by_name=True)


@router.get("/meta")
def get_meta() -> dict:
    """公开元信息（不含 API Key）。"""
    settings = get_settings()
    return {
        "llmModel": settings.llm_model,
        "embeddingModel": settings.embedding_model,
        "defaultKnowledgeBaseId": DEFAULT_KB_ID,
        "chunkSize": settings.chunk_size,
        "chunkOverlap": settings.chunk_overlap,
    }


@router.get("/prefs")
def get_prefs() -> dict:
    """读取客户端偏好（当前知识库等）。"""
    return _load_prefs()


@router.put("/prefs")
def put_prefs(body: PrefsUpdate, db: Session = Depends(get_db)) -> dict:
    """更新客户端偏好。"""
    prefs = _load_prefs()
    if body.current_knowledge_base_id is not None:
        kb_id = body.current_knowledge_base_id.strip() or DEFAULT_KB_ID
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise_api_error(404, "KB_NOT_FOUND", f"知识库不存在: {kb_id}")
        prefs["currentKnowledgeBaseId"] = kb_id
    _save_prefs(prefs)
    return prefs
