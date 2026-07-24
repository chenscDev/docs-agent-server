"""文本切分：标题 → 段落 → 定长窗口（P3-D3 结构化）。"""

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
    heading: str | None = None


def estimate_tokens(text: str) -> int:
    """粗估 token：中文约按字符，英文按空白分词近似。"""
    if not text:
        return 0
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
    结构化切分：

    1. 按 Markdown # / ## 标题分段（保留 heading）
    2. 段内按空行（段落）切开
    3. 过长段落再按窗口二次切
    """
    cleaned = text.strip()
    if not cleaned:
        return []

    sections = _split_by_markdown_headings(cleaned)
    raw: list[tuple[str | None, str]] = []
    for heading, body in sections:
        for para in _split_paragraphs(body):
            for piece in _window_split(para, chunk_size=chunk_size, overlap=overlap):
                raw.append((heading, piece))

    pieces: list[ChunkPiece] = []
    for heading, chunk in raw:
        body = chunk.strip()
        if not body:
            continue
        # 若正文未带标题行，将 heading 前缀进内容，利于检索与展示
        content = body
        if heading and not body.lstrip().startswith("#"):
            content = f"{heading}\n{body}"
        pieces.append(
            ChunkPiece(
                index=len(pieces),
                content=content,
                token_estimate=estimate_tokens(content),
                content_hash=content_hash(content),
                heading=heading,
            )
        )
    return pieces


_HEADING_RE = re.compile(r"(?m)^(#{1,3}\s+.+)$")


def _split_by_markdown_headings(text: str) -> list[tuple[str | None, str]]:
    """按 #～### 标题切开；返回 (heading|None, body)。"""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(None, text)]

    parts: list[tuple[str | None, str]] = []
    if matches[0].start() > 0:
        preface = text[: matches[0].start()].strip()
        if preface:
            parts.append((None, preface))

    for i, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # body 为空时仍保留标题本身，避免丢章节名
        section_body = f"{heading}\n{body}".strip() if body else heading
        parts.append((heading, section_body))
    return parts or [(None, text)]


def _split_paragraphs(text: str) -> list[str]:
    """按空行分段；无空行则整段返回。"""
    text = text.strip()
    if not text:
        return []
    parts = re.split(r"\n\s*\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _window_split(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """定长窗口切分，带 overlap；尽量在句号/换行处收尾。"""
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
        if end < len(text):
            # 向后找近邻断句点，避免拦腰切断
            window = text[start:end]
            soft = max(
                window.rfind("。"),
                window.rfind("！"),
                window.rfind("？"),
                window.rfind("\n"),
                window.rfind(". "),
            )
            if soft >= chunk_size // 2:
                end = start + soft + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start += step if end == start + chunk_size else max(1, end - start - overlap)
        if start >= len(text):
            break
    return chunks
