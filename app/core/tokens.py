"""多 Token 解析与校验（P3-D13）。

来源（并集）：
- API_TOKEN（兼容单 Token）
- API_TOKENS（逗号 / 分号 / 空白分隔）

作废：
- API_TOKENS_REVOKED（同上分隔）
- 可选文件 data/api_tokens_revoked.local（每行一个，改文件即可热生效）
"""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger("docs_agent.auth")

_SPLIT = re.compile(r"[,;\s]+")
_revoked_file_cache: tuple[float, frozenset[str]] | None = None


def _split_tokens(raw: str) -> set[str]:
    text = (raw or "").strip()
    if not text:
        return set()
    return {p for p in _SPLIT.split(text) if p}


def _revoked_file_path() -> Path:
    settings = get_settings()
    root = Path(settings.api_tokens_revoked_file)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


def _load_revoked_from_file() -> frozenset[str]:
    """读取本地作废列表；按 mtime 缓存。"""
    global _revoked_file_cache
    path = _revoked_file_path()
    if not path.is_file():
        _revoked_file_cache = None
        return frozenset()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return frozenset()

    if _revoked_file_cache and _revoked_file_cache[0] == mtime:
        return _revoked_file_cache[1]

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("read revoked tokens file failed: %s", exc)
        return frozenset()

    tokens: set[str] = set()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        tokens.add(s)
    frozen = frozenset(tokens)
    _revoked_file_cache = (mtime, frozen)
    return frozen


def get_valid_api_tokens() -> frozenset[str]:
    """
    当前有效 Token 集合。

    空集合表示未开启鉴权（与仅空 API_TOKEN 行为一致）。
    """
    settings = get_settings()
    active = set()
    active |= _split_tokens(settings.api_token)
    active |= _split_tokens(settings.api_tokens)
    revoked = _split_tokens(settings.api_tokens_revoked) | set(_load_revoked_from_file())
    valid = frozenset(t for t in active if t not in revoked)
    return valid


def is_api_token_valid(provided: str) -> bool:
    """常量时间倾向的多 Token 比对。"""
    token = (provided or "").strip()
    if not token:
        return False
    valid = get_valid_api_tokens()
    if not valid:
        return False
    matched = False
    for expected in valid:
        # 遍历全部，降低「靠前 Token 更快命中」的侧信道
        if secrets.compare_digest(token, expected):
            matched = True
    return matched


def auth_enabled() -> bool:
    """是否启用鉴权（存在至少一个有效 Token）。"""
    return bool(get_valid_api_tokens())
