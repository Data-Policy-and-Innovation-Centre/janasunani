"""Single-document OCR: raw bytes -> text, for the live warm processor.

The batch pipeline (`pipeline/stages/ocr_extraction/`) only wires OCR
primitives together through its artifact DB (see `stage.py`'s
`_process_page`), one page row at a time. Serving one uploaded grievance
document needs a standalone orchestration wrapper with the same per-page
shape but no DB in the middle.

Everything heavy — rendering (poppler/pdf2image), text extraction
(tesseract or a future out-of-process DeepSeek backend), page-type
classification — is dependency-injected, so this module imports cleanly
with no OCR/ML libraries installed and is unit-testable with fakes.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_SUFFIX = ".pdf"


@dataclass
class OcrResult:
    """Result of OCR-ing one uploaded document."""

    full_text: str
    pages: int
    per_page: list[tuple[str, str | None]]  # (page_text, page_type_label_or_None)


def ocr_document(
    document_bytes: bytes,
    document_name: str,
    *,
    extract_text_fn: Callable[..., str],
    render_page_fn: Callable[[Path, int], object],
    page_type_predict: Callable[[object], str] | None = None,
    force_lang: str | None = None,
) -> OcrResult:
    """OCR one uploaded document (bytes) into text, page by page.

    Args:
        document_bytes: Raw bytes of the uploaded file (PDF or image).
        document_name: Original filename, used only to derive the file
            extension (`render_page_fn` needs a real file path, not bytes).
        extract_text_fn: `(image, force_lang) -> str`, e.g.
            `pytesseract_backend.extract_text`.
        render_page_fn: `(file_path, page_number) -> PIL.Image`, e.g.
            `page_renderer.render_page`. Must raise `ValueError` or
            `IndexError` once `page_number` exceeds the document's page
            count (as `render_page` does).
        page_type_predict: Optional `(image) -> label` classifier run per
            page before OCR.
        force_lang: Optional tesseract-style forced language, passed through
            to `extract_text_fn`.

    Returns:
        OcrResult with the joined full text, page count, and per-page
        (text, label) pairs.
    """
    suffix = Path(document_name).suffix or DEFAULT_SUFFIX
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(document_bytes)
        tmp_path = Path(tmp.name)

    try:
        per_page: list[tuple[str, str | None]] = []
        page_number = 1
        while True:
            try:
                image = render_page_fn(tmp_path, page_number)
            except (ValueError, IndexError):
                # Renderer signals "no such page" this way (see
                # page_renderer.render_page) — that's the end of the document.
                break

            label = page_type_predict(image) if page_type_predict else None
            text = extract_text_fn(image, force_lang)
            per_page.append((text, label))
            page_number += 1

        full_text = "\n\n".join(text for text, _ in per_page)
        return OcrResult(full_text=full_text, pages=len(per_page), per_page=per_page)
    finally:
        os.unlink(tmp_path)
