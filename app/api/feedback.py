"""消息反馈：点赞 / 点踩落库，down 可导出评测候选（P3-D8）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import raise_api_error
from app.core.ids import new_id
from app.db.models import Message, MessageFeedback
from app.db.session import get_db

router = APIRouter(prefix="/v1", tags=["feedback"])

_ALLOWED = frozenset({"up", "down"})
_CANDIDATES_PATH = Path("eval/feedback_candidates.jsonl")


class FeedbackRequest(BaseModel):
    rating: str = Field(..., description="up | down")
    comment: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    message_id: str = Field(serialization_alias="messageId")
    session_id: str = Field(serialization_alias="sessionId")
    rating: str
    comment: str | None = None
    exported: bool = False
    created_at: object | None = Field(default=None, serialization_alias="createdAt")
    updated_at: object | None = Field(default=None, serialization_alias="updatedAt")


def _to_out(row: MessageFeedback) -> FeedbackOut:
    return FeedbackOut(
        id=row.id,
        message_id=row.message_id,
        session_id=row.session_id,
        rating=row.rating,
        comment=row.comment,
        exported=bool(row.exported),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def feedback_map_for_messages(
    db: Session, message_ids: list[str]
) -> dict[str, MessageFeedback]:
    """批量取消息反馈，供 list_messages 附带。"""
    if not message_ids:
        return {}
    rows = db.scalars(
        select(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids))
    ).all()
    return {r.message_id: r for r in rows}


@router.post(
    "/messages/{message_id}/feedback",
    response_model=FeedbackOut,
    response_model_by_alias=True,
)
def upsert_feedback(
    message_id: str,
    body: FeedbackRequest,
    db: Session = Depends(get_db),
) -> FeedbackOut:
    """对助手消息点赞 / 点踩（可改评）。"""
    rating = (body.rating or "").strip().lower()
    if rating not in _ALLOWED:
        raise_api_error(400, "FEEDBACK_BAD_RATING", "rating 须为 up 或 down")

    msg = db.get(Message, message_id)
    if msg is None:
        raise_api_error(404, "MSG_NOT_FOUND", "消息不存在")
    if msg.role != "assistant":
        raise_api_error(400, "FEEDBACK_NOT_ASSISTANT", "只能对助手消息反馈")

    comment = (body.comment or "").strip() or None
    if comment and len(comment) > 2000:
        raise_api_error(400, "FEEDBACK_COMMENT_TOO_LONG", "评论过长（≤2000）")

    row = db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = MessageFeedback(
            id=new_id("fb"),
            message_id=message_id,
            session_id=msg.session_id,
            rating=rating,
            comment=comment,
            exported=0,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.rating = rating
        row.comment = comment
        row.updated_at = now
        # 改评后允许再次导出
        if rating == "down":
            row.exported = 0
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/messages/{message_id}/feedback")
def clear_feedback(message_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """清除反馈。"""
    row = db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    if row is None:
        raise_api_error(404, "FEEDBACK_NOT_FOUND", "尚无反馈")
    db.delete(row)
    db.commit()
    return {"status": "deleted", "messageId": message_id}


@router.get("/feedbacks")
def list_feedbacks(
    rating: str | None = Query(default=None, description="up | down"),
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    """反馈列表（人工审阅 / 导出前预览）。"""
    limit = max(1, min(limit, 200))
    stmt = select(MessageFeedback).order_by(MessageFeedback.updated_at.desc())
    if rating:
        r = rating.strip().lower()
        if r not in _ALLOWED:
            raise_api_error(400, "FEEDBACK_BAD_RATING", "rating 须为 up 或 down")
        stmt = stmt.where(MessageFeedback.rating == r)
    rows = db.scalars(stmt.limit(limit)).all()

    items = []
    for fb in rows:
        msg = db.get(Message, fb.message_id)
        items.append(
            {
                **_to_out(fb).model_dump(by_alias=True, mode="json"),
                "messagePreview": (msg.content[:200] if msg else None),
                "requestId": msg.request_id if msg else None,
            }
        )
    return {"items": items}


@router.post("/feedbacks/export-down")
def export_down_feedbacks(db: Session = Depends(get_db)) -> dict:
    """
    将未导出的点踩写入 eval/feedback_candidates.jsonl，
    并追加 FAILURE_CASES 候选段落，供人工审入评测。
    """
    rows = db.scalars(
        select(MessageFeedback)
        .where(MessageFeedback.rating == "down", MessageFeedback.exported == 0)
        .order_by(MessageFeedback.created_at.asc())
    ).all()
    if not rows:
        return {"exported": 0, "path": str(_CANDIDATES_PATH), "message": "无待导出点踩"}

    _CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    failure_path = Path("eval/FAILURE_CASES.md")
    appended_md: list[str] = []
    count = 0

    with _CANDIDATES_PATH.open("a", encoding="utf-8") as fp:
        for fb in rows:
            msg = db.get(Message, fb.message_id)
            # 尽量找同会话上一条用户问题
            user_q = None
            if msg is not None:
                prior = db.scalars(
                    select(Message)
                    .where(
                        Message.session_id == msg.session_id,
                        Message.created_at < msg.created_at,
                        Message.role == "user",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                ).first()
                if prior:
                    user_q = prior.content

            record = {
                "feedbackId": fb.id,
                "messageId": fb.message_id,
                "sessionId": fb.session_id,
                "requestId": msg.request_id if msg else None,
                "rating": fb.rating,
                "comment": fb.comment,
                "userQuestion": user_q,
                "assistantAnswer": msg.content if msg else None,
                "exportedAt": datetime.now(timezone.utc).isoformat(),
            }
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            q_preview = (user_q or "(未知问题)")[:60]
            appended_md.append(
                f"\n### {day} · feedback · {q_preview}\n"
                f"- 来源：用户点踩（feedbackId=`{fb.id}`）\n"
                f"- requestId：`{msg.request_id if msg else ''}`\n"
                f"- 评论：{fb.comment or '(无)'}\n"
                f"- 归因：待人工审（检索/切分/Prompt/幻觉/其他）\n"
                f"- 状态：open\n"
            )
            fb.exported = 1
            fb.updated_at = datetime.now(timezone.utc)
            count += 1

    db.commit()

    if appended_md and failure_path.is_file():
        with failure_path.open("a", encoding="utf-8") as md:
            md.write("\n## 用户点踩候选（P3-D8 自动导出）\n")
            md.write("".join(appended_md))

    return {
        "exported": count,
        "path": str(_CANDIDATES_PATH),
        "failureCasesUpdated": bool(appended_md and failure_path.is_file()),
    }
