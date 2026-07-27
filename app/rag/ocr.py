"""扫描版 PDF 云 OCR：PyMuPDF 渲页 + 通义 qwen-vl-ocr。"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 默认提示：只要正文，不要模型额外描述
_OCR_PROMPT = (
    "Please extract all text content from the image "
    "without any additional descriptions or formatting."
)

ProgressCallback = Callable[[str, float | None], None]


class OcrError(Exception):
    """OCR 失败（由 extract 转为 ExtractError）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def ocr_pdf_file(
    file_path: Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> str:
    """
    对整本 PDF 逐页 OCR，返回拼接后的纯文本。

    失败抛 OcrError（OCR_TOO_MANY_PAGES / OCR_FAILED）。
    """
    settings = get_settings()
    api_key = (settings.llm_api_key or "").strip()
    if not api_key:
        raise OcrError(
            "OCR_FAILED",
            "未配置 LLM_API_KEY，无法对扫描 PDF 做 OCR",
        )

    try:
        import fitz  # pymupdf
    except ModuleNotFoundError as exc:
        raise OcrError(
            "PARSE_DEPENDENCY",
            "缺少 PyMuPDF（import fitz 失败），无法 OCR",
        ) from exc

    max_pages = max(1, int(settings.ocr_max_pages))
    timeout = max(5.0, float(settings.ocr_timeout_sec))
    model = (settings.ocr_model or "qwen-vl-ocr-latest").strip()

    client = OpenAI(api_key=api_key, base_url=settings.llm_base_url)

    with fitz.open(file_path) as doc:
        page_count = int(doc.page_count or 0)
        if page_count <= 0:
            raise OcrError("PARSE_EMPTY", "PDF 无页面，无法 OCR")

        if page_count > max_pages:
            raise OcrError(
                "OCR_TOO_MANY_PAGES",
                f"扫描 PDF 共 {page_count} 页，超过 OCR 上限 {max_pages} 页，请拆分后再上传",
            )

        parts: list[str] = []
        for index, page in enumerate(doc):
            page_no = index + 1
            if on_progress is not None:
                # 提取阶段进度落在 0.15～0.45，留给后续切分/索引
                ratio = 0.15 + 0.3 * (page_no / page_count)
                on_progress(f"OCR 识别中 {page_no}/{page_count}…", ratio)

            try:
                page_text = _ocr_one_page(
                    client,
                    model,
                    page,
                    matrix=fitz.Matrix(2, 2),
                    timeout=timeout,
                )
            except OcrError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "ocr page failed path=%s page=%s", file_path, page_no
                )
                raise OcrError(
                    "OCR_FAILED",
                    f"第 {page_no} 页 OCR 失败: {exc}",
                ) from exc

            cleaned = (page_text or "").strip()
            if cleaned:
                parts.append(cleaned)

    joined = "\n\n".join(parts).strip()
    if not joined:
        raise OcrError("OCR_FAILED", "OCR 完成但未识别到可用文本")

    logger.info(
        "ocr_pdf ok path=%s pages=%s chars=%s model=%s",
        file_path.name,
        page_count,
        len(joined),
        model,
    )
    return joined


def _ocr_one_page(
    client: OpenAI,
    model: str,
    page: Any,
    *,
    matrix: Any,
    timeout: float,
) -> str:
    """渲染单页为 JPEG 并调用多模态 OCR。"""
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    jpeg_bytes = pix.tobytes("jpeg")
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": _OCR_PROMPT},
                    ],
                }
            ],
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise OcrError("OCR_FAILED", f"调用 OCR 模型失败: {exc}") from exc

    if not response.choices:
        raise OcrError("OCR_FAILED", "OCR 模型返回空 choices")

    content = response.choices[0].message.content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(t for t in texts if t).strip()

    return (content or "").strip()
