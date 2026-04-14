from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import fitz
import pytesseract


def _clean_text(text: str) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    replacements = {
        "\u00a0": " ",   # no-break space
        "\u200b": "",    # zero width space
        "\ufeff": "",    # byte order mark
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _configure_hf_runtime() -> None:
    # Keep logs cleaner on Windows when symlinks are unavailable.
    if not os.getenv("HF_HUB_DISABLE_SYMLINKS_WARNING"):
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    # Allow users to provide HF_TOKEN once and reuse it for hub auth.
    hf_token = os.getenv("HF_TOKEN", "").strip()
    if hf_token and not os.getenv("HUGGINGFACEHUB_API_TOKEN"):
        os.environ["HUGGINGFACEHUB_API_TOKEN"] = hf_token


def _docling_markdown(path: str) -> tuple[str | None, str]:
    _configure_hf_runtime()
    try:
        from docling.document_converter import DocumentConverter
    except Exception:
        return None, "docling_unavailable"

    try:
        converter = DocumentConverter()
        result = converter.convert(path)
        markdown = result.document.export_to_markdown()
    except Exception:
        return None, "docling_failed"

    text = _clean_text(markdown or "")
    if not text:
        return None, "docling_empty"
    return text, "docling"


def _pymupdf_text(path: str) -> tuple[str | None, str]:
    try:
        doc = fitz.open(path)
    except Exception:
        return None, "pymupdf_open_failed"

    parts: list[str] = []
    try:
        for idx, page in enumerate(doc, start=1):
            page_text = (page.get_text("text") or "").strip()
            if page_text:
                parts.append(f"# Page {idx}\n{page_text}")
    finally:
        doc.close()

    text = _clean_text("\n\n".join(parts))
    if not text:
        return None, "pymupdf_empty"
    return text, "pymupdf"


def _tesseract_ocr(path: str) -> tuple[str | None, str]:
    tesseract_bin = os.getenv("TESSERACT_BIN", "").strip()
    if tesseract_bin:
        pytesseract.pytesseract.tesseract_cmd = tesseract_bin

    try:
        doc = fitz.open(path)
    except Exception:
        return None, "ocr_open_failed"

    pages: list[str] = []
    try:
        for idx, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=220)
            ocr_text = pytesseract.image_to_string(pix.pil_image(), lang="eng").strip()
            if ocr_text:
                pages.append(f"# Page {idx}\n{ocr_text}")
    except Exception:
        return None, "ocr_failed"
    finally:
        doc.close()

    text = _clean_text("\n\n".join(pages))
    if not text:
        return None, "ocr_empty"
    return text, "tesseract_ocr"


def _split_by_headings(markdown_text: str) -> str:
    sections: list[dict[str, Any]] = []
    heading_re = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(markdown_text))
    if not matches:
        return json.dumps([{"heading": "Document", "content": _clean_text(markdown_text)}], ensure_ascii=True)

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        chunk = _clean_text(markdown_text[start:end])
        title = m.group(1).strip()
        sections.append({"heading": title, "content": chunk})
    return json.dumps(sections, ensure_ascii=True)


def extract_pdf_content(local_path: str) -> tuple[str | None, str]:
    file_path = Path(local_path)
    if not file_path.exists():
        return None, "pdf_missing"

    markdown, method = _docling_markdown(local_path)
    if not markdown:
        markdown, method = _pymupdf_text(local_path)
    if not markdown:
        markdown, method = _tesseract_ocr(local_path)
    if not markdown:
        return None, method

    try:
        doc = fitz.open(local_path)
        page_count = doc.page_count
        doc.close()
    except Exception:
        page_count = 0

    if page_count > 20:
        return _split_by_headings(markdown), f"{method}:long_pdf_json"
    return markdown, f"{method}:short_pdf_markdown"
