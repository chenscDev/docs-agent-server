"""文本切分：优先按 Markdown 标题，否则按长度滑动窗口。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass
class ChunkPiece:
    """切分后的一段文本。"""

    index: int
    content: str
    token_estimate: int
    content_hash: str


def estimate_tokens(text: str) -> int:
    """粗估 token：中文约按字符，英文按空白分词近似。"""
    if not text:
        return 0
    # 对中英混合足够用于展示，不追求精确
    return max(1, len(text))


def content_hash(text: str) -> str:
    """内容哈希，供后续 embedding 去重。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def split_text(
    text: str,
    *,
    chunk_size: int = 500,
    overlap: int = 80,
) -> list[ChunkPiece]:
    """
    切分纯文本。

    - 若存在 Markdown 一级/二级标题，先按标题分段，再对过长段做窗口切分
    - 否则整篇按窗口切分
    """
    cleaned = text.strip()
    if not cleaned:
        return []

    sections = _split_by_markdown_headings(cleaned)
    raw_chunks: list[str] = []
    for section in sections:
        raw_chunks.extend(_window_split(section, chunk_size=chunk_size, overlap=overlap))

    pieces: list[ChunkPiece] = []
    for i, chunk in enumerate(raw_chunks):
        body = chunk.strip()
        if not body:
            continue
        pieces.append(
            ChunkPiece(
                index=len(pieces),
                content=body,
                token_estimate=estimate_tokens(body),
                content_hash=content_hash(body),
            )
        )
    return pieces


_HEADING_RE = re.compile(r"(?m)^(#{1,2}\s+.+)$")


def _split_by_markdown_headings(text: str) -> list[str]:
    """按 # / ## 标题切开；没有标题则返回整篇。"""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [text]

    parts: list[str] = []
    # 标题前的前言
    if matches[0].start() > 0:
        preface = text[: matches[0].start()].strip()
        if preface:
            parts.append(preface)

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end].strip()
        if section:
            parts.append(section)
    return parts or [text]


def _window_split(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """定长窗口切分，带 overlap。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须 > 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 须满足 0 <= overlap < chunk_size")

    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    step = chunk_size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start += step
    return chunks
