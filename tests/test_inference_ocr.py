"""Tests for janasunani.inference.ocr — real-code-path with fake injected
render/extract functions (no tesseract/poppler/torch required).

`ocr_document` is exercised end-to-end: real temp-file writing/cleanup,
real page-loop / end-of-document detection, real text joining. Only the
heavy OCR/render primitives are fakes, matching how the real
`render_page`/`extract_text` behave (out-of-range page -> ValueError).
"""
from __future__ import annotations

from pathlib import Path

from janasunani.inference.ocr import OcrQualityError, OcrResult, ocr_document


class FakeImage:
    """Sentinel standing in for a PIL.Image, tagged with its page number."""

    def __init__(self, page_number: int):
        self.page_number = page_number


def make_render_page_fn(num_pages: int, tmp_paths: list[Path]):
    """Fake render_page_fn: returns a FakeImage per page, raises ValueError
    past `num_pages` (mirrors page_renderer.render_page's out-of-range
    behavior)."""

    def render_page_fn(file_path: Path, page_number: int) -> FakeImage:
        tmp_paths.append(file_path)
        assert file_path.exists(), "temp file must exist while rendering"
        if page_number > num_pages:
            raise ValueError(
                f"could not render page {page_number} of {file_path.name}"
            )
        return FakeImage(page_number)

    return render_page_fn


def fake_extract_text_fn(image: FakeImage, force_lang: str | None) -> str:
    lang_tag = force_lang or "auto"
    return f"page {image.page_number} text ({lang_tag})"


def fake_page_type_predict(image: FakeImage) -> str:
    return "letter" if image.page_number == 1 else "attachment"


def test_single_page_image_document():
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"fake-image-bytes",
        document_name="scan.jpg",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(1, tmp_paths),
    )

    assert isinstance(result, OcrResult)
    assert result.pages == 1
    assert result.full_text == "page 1 text (auto)"
    assert result.per_page == [("page 1 text (auto)", None)]

    # Temp file must be cleaned up afterward.
    assert len(tmp_paths) == 2  # called once for page 1, once for out-of-range page 2
    assert not tmp_paths[0].exists()
    assert tmp_paths[0].suffix == ".jpg"


def test_multi_page_pdf_document_with_labels_and_lang():
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="complaint.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(3, tmp_paths),
        page_type_predict=fake_page_type_predict,
        force_lang="eng",
    )

    assert result.pages == 3
    assert result.full_text == (
        "page 1 text (eng)\n\npage 2 text (eng)\n\npage 3 text (eng)"
    )
    assert result.per_page == [
        ("page 1 text (eng)", "letter"),
        ("page 2 text (eng)", "attachment"),
        ("page 3 text (eng)", "attachment"),
    ]

    assert not tmp_paths[0].exists()
    assert tmp_paths[0].suffix == ".pdf"


def test_default_suffix_when_document_name_has_no_extension():
    tmp_paths: list[Path] = []
    ocr_document(
        document_bytes=b"bytes",
        document_name="no_extension_here",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(1, tmp_paths),
    )

    assert tmp_paths[0].suffix == ".pdf"


def test_temp_file_removed_even_on_extract_error():
    tmp_paths: list[Path] = []

    def render_page_fn(file_path: Path, page_number: int) -> FakeImage:
        tmp_paths.append(file_path)
        if page_number > 1:
            raise ValueError("no more pages")
        return FakeImage(page_number)

    def failing_extract_text_fn(image: FakeImage, force_lang: str | None) -> str:
        raise RuntimeError("ocr backend exploded")

    try:
        ocr_document(
            document_bytes=b"bytes",
            document_name="doc.pdf",
            extract_text_fn=failing_extract_text_fn,
            render_page_fn=render_page_fn,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError to propagate")

    assert not tmp_paths[0].exists()


def test_first_page_render_failure_propagates():
    """`render_page` raises ValueError for both end-of-document AND an
    unsupported/corrupt upload. If page 1 itself fails to render, that's
    never "end of document" (there's no document 0) — it must propagate so
    an unsupported file isn't silently routed as a blank grievance."""
    tmp_paths: list[Path] = []

    def render_page_fn(file_path: Path, page_number: int) -> FakeImage:
        tmp_paths.append(file_path)
        raise ValueError("could not render page 1: unsupported file type")

    try:
        ocr_document(
            document_bytes=b"this is not a pdf or image",
            document_name="notes.txt",
            extract_text_fn=fake_extract_text_fn,
            render_page_fn=render_page_fn,
        )
    except ValueError as exc:
        assert "could not render page 1" in str(exc)
    else:
        raise AssertionError("expected ValueError from page 1 to propagate")

    # Temp file must still be cleaned up even though the error propagated.
    assert not tmp_paths[0].exists()


def test_page_n_render_failure_terminates_cleanly_with_earlier_pages():
    """Once at least one page has rendered, a later render failure is the
    normal end-of-document signal, not a propagated error — the pages seen
    so far are kept."""
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="complaint.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(2, tmp_paths),
    )

    assert result.pages == 2
    assert result.full_text == "page 1 text (auto)\n\npage 2 text (auto)"
    assert result.per_page == [
        ("page 1 text (auto)", None),
        ("page 2 text (auto)", None),
    ]


def test_max_pages_caps_the_page_loop():
    """A document with more pages than `max_pages` stops at the cap instead
    of OCR-ing every page synchronously."""
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="huge.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(10, tmp_paths),
        max_pages=3,
    )

    assert result.pages == 3
    assert result.per_page == [
        ("page 1 text (auto)", None),
        ("page 2 text (auto)", None),
        ("page 3 text (auto)", None),
    ]
    assert result.truncated is True
    # The loop must stop at the cap and OCR nothing past it — the only
    # extra render is the page-4 truncation probe (no extract/classify).
    assert len(tmp_paths) == 4


def test_default_max_pages_protects_a_huge_document():
    """The out-of-the-box default caps the live path even when the caller
    doesn't pass `max_pages` explicitly."""
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="huge.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(500, tmp_paths),
    )

    assert result.pages == 50
    assert result.truncated is True
    assert len(tmp_paths) == 51  # 50 rendered pages + 1 truncation probe


def test_max_pages_not_truncated_when_document_ends_exactly_at_the_cap():
    """A document with exactly `max_pages` pages is not truncated — the
    probe past the cap hits the renderer's normal end-of-document error."""
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="exact.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(3, tmp_paths),
        max_pages=3,
    )

    assert result.pages == 3
    assert result.truncated is False
    assert len(tmp_paths) == 4  # 3 rendered pages + 1 truncation probe


def test_unbounded_max_pages_is_never_truncated():
    """`max_pages=None` (unbounded) always runs to real end-of-document via
    `break`, so the truncation probe never fires and `truncated` stays
    False."""
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="complaint.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(5, tmp_paths),
        max_pages=None,
    )

    assert result.pages == 5
    assert result.truncated is False


def test_quality_check_fn_skips_pages_that_fail_the_check():
    """An injected quality_check_fn that rejects a page's text excludes that
    page from both `full_text` and `per_page`, so looped/garbage OCR output
    can't reach redaction/classification downstream."""
    tmp_paths: list[Path] = []

    def quality_check_fn(text: str) -> bool:
        return "page 2" not in text

    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="complaint.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(3, tmp_paths),
        quality_check_fn=quality_check_fn,
    )

    assert result.pages == 2
    assert result.full_text == "page 1 text (auto)\n\npage 3 text (auto)"
    assert result.per_page == [
        ("page 1 text (auto)", None),
        ("page 3 text (auto)", None),
    ]


def test_quality_check_fn_rejecting_every_page_raises_ocr_quality_error():
    """If `quality_check_fn` was supplied and every rendered page fails it,
    an empty OcrResult would be indistinguishable from a legitimately blank
    document — so this must raise instead of returning a blank result that
    would silently route into redaction/classification."""
    tmp_paths: list[Path] = []

    def quality_check_fn(text: str) -> bool:
        return False

    try:
        ocr_document(
            document_bytes=b"%PDF-fake-bytes",
            document_name="garbage.pdf",
            extract_text_fn=fake_extract_text_fn,
            render_page_fn=make_render_page_fn(3, tmp_paths),
            quality_check_fn=quality_check_fn,
        )
    except OcrQualityError as exc:
        assert "garbage.pdf" in str(exc)
    else:
        raise AssertionError("expected OcrQualityError")

    # Temp file must still be cleaned up even though the error propagated.
    assert not tmp_paths[0].exists()


def test_quality_check_fn_rejecting_single_page_document_raises():
    """A single-page document whose one page fails the quality check is the
    same "every page rejected" case, not a special-cased empty document."""
    tmp_paths: list[Path] = []

    def quality_check_fn(text: str) -> bool:
        return False

    try:
        ocr_document(
            document_bytes=b"fake-image-bytes",
            document_name="scan.jpg",
            extract_text_fn=fake_extract_text_fn,
            render_page_fn=make_render_page_fn(1, tmp_paths),
            quality_check_fn=quality_check_fn,
        )
    except OcrQualityError:
        pass
    else:
        raise AssertionError("expected OcrQualityError")


def test_no_quality_check_fn_does_not_raise_ocr_quality_error():
    """`OcrQualityError` is scoped to "a quality hook was supplied and
    rejected everything." A page-less/empty document with *no* hook is a
    separate, legitimately-empty case: page 1's render failure still
    propagates as the existing `ValueError` (unrelated, pre-existing
    behavior) — not the new `OcrQualityError` — because the quality gate
    never engages when `quality_check_fn` is None."""

    def render_page_fn(file_path: Path, page_number: int):
        raise ValueError("could not render page 1: unsupported file type")

    try:
        ocr_document(
            document_bytes=b"bytes",
            document_name="empty.pdf",
            extract_text_fn=fake_extract_text_fn,
            render_page_fn=render_page_fn,
        )
    except OcrQualityError:
        raise AssertionError("must not raise OcrQualityError without a hook") from None
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError from page 1 to propagate")


def test_quality_check_fn_default_none_is_a_no_op():
    """Without a quality_check_fn (the default), the pytesseract path is
    unchanged — no pages are filtered."""
    tmp_paths: list[Path] = []
    result = ocr_document(
        document_bytes=b"%PDF-fake-bytes",
        document_name="complaint.pdf",
        extract_text_fn=fake_extract_text_fn,
        render_page_fn=make_render_page_fn(2, tmp_paths),
    )

    assert result.pages == 2
    assert result.per_page == [
        ("page 1 text (auto)", None),
        ("page 2 text (auto)", None),
    ]
