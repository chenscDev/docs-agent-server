"""从本地文件提取纯文本。"""

from __future__ import annotations

from pathlib import Path

# 允许的扩展名（小写）
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}

# 演示/练手期 PDF 页数上限（过大易拖垮本机 embedding）
MAX_PDF_PAGES = 100


class ExtractError(Exception):
    """文本提取失败。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def normalize_extension(filename: str) -> str:
    """返回小写扩展名，含点号。"""
    return Path(filename).suffix.lower()


def guess_mime(filename: str) -> str:
    """根据扩展名推断 MIME。"""
    ext = normalize_extension(filename)
    return MIME_BY_EXT.get(ext, "application/octet-stream")


def extract_text(file_path: Path, filename: str) -> str:
    """
    提取文本。

    成功返回非空字符串；失败抛 ExtractError（带稳定 code）。
    """
    ext = normalize_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ExtractError("UNSUPPORTED_TYPE", f"不支持的文件类型: {ext or '(无扩展名)'}")

    if not file_path.is_file():
        raise ExtractError("INTERNAL", f"文件不存在: {file_path}")

    try:
        if ext == ".pdf":
            text = _extract_pdf(file_path)
        else:
            text = _extract_plain_text(file_path)
    except ExtractError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractError("INTERNAL", f"提取文本失败: {exc}") from exc

    cleaned = text.strip()
    if not cleaned:
        raise ExtractError(
            "PARSE_EMPTY",
            "未能提取到可用文本（可能是扫描版 PDF、空文件或仅图片页）",
        )
    return cleaned


def _extract_plain_text(file_path: Path) -> str:
    """读取 txt/md；拒绝明显二进制。"""
    raw = file_path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ExtractError("PARSE_BINARY", "文本文件疑似二进制，无法按 UTF-8 解析")
    return raw.decode("utf-8", errors="ignore")


def _extract_pdf(file_path: Path) -> str:
    """使用 PyMuPDF 提取 PDF 文本，处理加密 / 空页 / 页数上限。"""
    try:
        import fitz  # pymupdf
    except ModuleNotFoundError as exc:
        raise ExtractError(
            "PARSE_DEPENDENCY",
            "缺少 PyMuPDF（import fitz 失败）。请用仓库 .venv 启动服务："
            ".venv/bin/pip install -r requirements.txt",
        ) from exc

    with fitz.open(file_path) as doc:
        if doc.is_encrypted:
            # 尝试空密码（部分 PDF 仅权限加密）
            auth_ok = False
            try:
                auth_ok = bool(doc.authenticate(""))
            except Exception:  # noqa: BLE001
                auth_ok = False
            if not auth_ok:
                raise ExtractError("PDF_ENCRYPTED", "PDF 已加密，请提供未加密文件")

        page_count = int(doc.page_count or 0)
        if page_count <= 0:
            raise ExtractError("PARSE_EMPTY", "PDF 无页面")

        if page_count > MAX_PDF_PAGES:
            raise ExtractError(
                "PDF_TOO_MANY_PAGES",
                f"PDF 页数 {page_count} 超过上限 {MAX_PDF_PAGES}，请拆分后再上传",
            )

        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text("text") or "")

    return "\n".join(parts)
