#!/usr/bin/env python3
"""P2-D5～D6：验证启动 recover 会把 parsing/indexing 重置为 pending 并入队。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.ids import new_id  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.models import Document  # noqa: E402
from app.db.session import DEFAULT_KB_ID, init_db  # noqa: E402
from app.rag import parse_queue  # noqa: E402


def main() -> int:
    init_db()
    factory = db_session.SessionLocal
    if factory is None:
        print("FAIL: SessionLocal 未初始化", file=sys.stderr)
        return 1

    parse_queue.stop_parse_queue(wait=False)
    # 不启动真实 worker，避免本用例触发 Embedding
    parse_queue._executor = None  # type: ignore[attr-defined]
    parse_queue._started = False  # type: ignore[attr-defined]
    parse_queue._queued_or_running.clear()  # type: ignore[attr-defined]

    doc_id = new_id("doc")
    with factory() as db:
        db.add(
            Document(
                id=doc_id,
                knowledge_base_id=DEFAULT_KB_ID,
                title="recover-test.md",
                mime_type="text/markdown",
                byte_size=12,
                storage_key="data/uploads/recover-test-missing.md",
                status="parsing",
                progress=0.3,
                stage_message="假装杀进程卡在 parsing",
                chunk_count=0,
            )
        )
        db.commit()

    enqueued: list[str] = []
    original = parse_queue.enqueue_parse

    def _capture(did: str) -> bool:
        enqueued.append(did)
        with parse_queue._lock:  # type: ignore[attr-defined]
            if did in parse_queue._queued_or_running:  # type: ignore[attr-defined]
                return False
            parse_queue._queued_or_running.add(did)  # type: ignore[attr-defined]
        return True

    parse_queue.enqueue_parse = _capture  # type: ignore[assignment]
    try:
        n = parse_queue.recover_incomplete_parses()
    finally:
        parse_queue.enqueue_parse = original  # type: ignore[assignment]

    with factory() as db:
        doc = db.get(Document, doc_id)
        assert doc is not None
        status = doc.status
        stage = doc.stage_message
        db.delete(doc)
        db.commit()

    ok = n >= 1 and doc_id in enqueued and status == "pending"
    print(
        f"recovered={n} enqueued={enqueued} status={status} stage={stage!r} ok={ok}"
    )
    if not ok:
        print("FAIL: recover 未把 parsing 重置为 pending 或未入队", file=sys.stderr)
        return 1
    print("PASS: parsing → pending + enqueue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
