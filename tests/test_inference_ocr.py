"""Tests for janasunani.inference.ocr — real-code-path with fake injected
render/extract functions (no tesseract/poppler/torch required).

`ocr_document` is exercised end-to-end: real temp-file writing/cleanup,
real page-loop / end-of-document detection, real text joining. Only the
heavy OCR/render primitives are fakes, matching how the real
`render_page`/`extract_text` behave (out-of-range page -> ValueError).
"""
from __future__ import annotations

from pathlib import Path

from janasunani.inference.ocr import OcrResult, ocr_document


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
