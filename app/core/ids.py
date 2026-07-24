"""生成带前缀的业务 ID。"""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """生成形如 doc_xxxxxxxx 的 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
