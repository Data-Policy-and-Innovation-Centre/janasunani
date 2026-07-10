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

# Conservative page cap for the live path: protects the serving worker from
# a huge/accidental multi-hundred-page upload monopolizing it synchronously.
# Callers that need more (or an unbounded document) pass `max_pages`
# explicitly (a larger int, or `None`).
DEFAULT_MAX_PAGES = 50


class OcrQualityError(Exception):
    """Raised when `quality_check_fn` rejected every rendered page.

    Distinct from a genuinely empty/page-less document: this only fires when
    at least one page rendered *and* a quality hook was supplied, so the
    caller can mark the grievance unreadable instead of routing an
    indistinguishable-from-legitimate blank `OcrResult` into redaction,
    classification, and routing.
    """


@dataclass
class OcrResult:
    """Result of OCR-ing one uploaded document."""

    full_text: str
    pages: int
    per_page: list[tuple[str, str | None]]  # (page_text, page_type_label_or_None)
    # True when the page loop stopped because it hit `max_pages` and at
    # least one more page existed beyond the cap — signals that `full_text`
    # / `per_page` are a partial document, not the whole thing. Defaulted
    # for backward compatibility with existing positional/partial callers.
    truncated: bool = False


def ocr_document(
    document_bytes: bytes,
    document_name: str,
    *,
    extract_text_fn: Callable[..., str],
    render_page_fn: Callable[[Path, int], object],
    page_type_predict: Callable[[object], str] | None = None,
    force_lang: str | None = None,
    max_pages: int | None = DEFAULT_MAX_PAGES,
    quality_check_fn: Callable[[str], bool] | None = None,
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
            count (as `render_page` does). If the *first* page can't be
            rendered, that's treated as an unsupported/corrupt upload and
            the error is re-raised rather than swallowed as end-of-document.
        page_type_predict: Optional `(image) -> label` classifier run per
            page before OCR.
        force_lang: Optional tesseract-style forced language, passed through
            to `extract_text_fn`.
        max_pages: Stop after this many pages (default `DEFAULT_MAX_PAGES`),
            protecting the live worker from a huge/accidental multi-hundred
            -page document. Pass a larger int, or `None`, to lift the cap.
            If the document has more pages than the cap, the result comes
            back with `truncated=True` (see `OcrResult`) instead of silently
            looking like a complete document.
        quality_check_fn: Optional `(page_text) -> bool` run on each page's
            extracted text. A page whose text fails the check (returns
            `False`) is skipped — excluded from `full_text` and `per_page`
            — so looped/garbage OCR output can't reach redaction or
            classification downstream. `None` (default) applies no check.
            If every rendered page fails the check, `OcrQualityError` is
            raised rather than returning an `OcrResult` indistinguishable
            from a legitimate blank document.

    Returns:
        OcrResult with the joined full text, page count, per-page
        (text, label) pairs, and a `truncated` flag.

    Raises:
        OcrQualityError: `quality_check_fn` was supplied, at least one page
            rendered, and every rendered page failed the quality check.
    """
    suffix = Path(document_name).suffix or DEFAULT_SUFFIX
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(document_bytes)
        tmp_path = Path(tmp.name)

    try:
        per_page: list[tuple[str, str | None]] = []
        page_number = 1
        rendered_any_page = False
        truncated = False
        while max_pages is None or page_number <= max_pages:
            try:
                image = render_page_fn(tmp_path, page_number)
            except (ValueError, IndexError):
                if not rendered_any_page:
                    # The renderer also raises ValueError for an
                    # unsupported/corrupt file, not just end-of-document —
                    # if page 1 never rendered, propagate rather than
                    # silently returning an empty result that would route
                    # as a blank grievance.
                    raise
                # Renderer signals "no such page" this way (see
                # page_renderer.render_page) — that's the end of the document.
                break
            rendered_any_page = True

            label = page_type_predict(image) if page_type_predict else None
            text = extract_text_fn(image, force_lang)
            if quality_check_fn is not None and not quality_check_fn(text):
                page_number += 1
                continue
            per_page.append((text, label))
            page_number += 1
        else:
            # The `while` condition went false (`page_number > max_pages`),
            # not a `break` — the loop stopped because of the cap, not
            # because we ran out of document. Probe exactly one page past
            # the cap to tell "cap == document length" (not truncated) apart
            # from "more content was dropped" (truncated), without OCR-ing
            # any further pages.
            if max_pages is not None:
                try:
                    render_page_fn(tmp_path, max_pages + 1)
                except (ValueError, IndexError):
                    truncated = False
                else:
                    truncated = True

        if quality_check_fn is not None and rendered_any_page and not per_page:
            # Every rendered page failed the quality check. Returning an
            # empty OcrResult here would be indistinguishable from a
            # legitimately blank document, so a blank grievance would flow
            # into redaction/classification/routing — raise instead so the
            # caller can mark the grievance unreadable.
            raise OcrQualityError(
                f"all {page_number - 1} rendered page(s) of {document_name!r} "
                "failed the quality check"
            )

        full_text = "\n\n".join(text for text, _ in per_page)
        return OcrResult(
            full_text=full_text,
            pages=len(per_page),
            per_page=per_page,
            truncated=truncated,
        )
    finally:
        os.unlink(tmp_path)
