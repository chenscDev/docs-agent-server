"""数据库引擎与会话工厂。"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import Base, KnowledgeBase

DEFAULT_KB_ID = "kb_default"
DEFAULT_KB_NAME = "默认知识库"

_engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(database_url: str) -> None:
    """若为本地 sqlite 文件，确保父目录存在。"""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw = database_url[len(prefix) :]
    # 相对路径：./data/xxx.db
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> Engine:
    """获取全局 Engine（懒加载）。"""
    global _engine, SessionLocal
    if _engine is not None:
        return _engine

    settings = get_settings()
    _ensure_sqlite_dir(settings.database_url)
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_engine(
        settings.database_url,
        future=True,
        connect_args=connect_args,
    )

    # SQLite 外键
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级 Session。"""
    get_engine()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表并写入默认知识库。"""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    assert SessionLocal is not None
    with SessionLocal() as db:
        exists = db.scalar(
            select(KnowledgeBase).where(KnowledgeBase.id == DEFAULT_KB_ID)
        )
        if exists is None:
            db.add(
                KnowledgeBase(
                    id=DEFAULT_KB_ID,
                    name=DEFAULT_KB_NAME,
                )
            )
            db.commit()
