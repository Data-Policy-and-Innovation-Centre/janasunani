"""Render a specific page from a source file (PDF or image) to a PIL image.

This module handles the file-to-image step the OCR backends need. PDFs go
through pdf2image (poppler); image files load directly with Pillow.
"""
from __future__ import annotations

import os
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"}
PDF_EXTENSIONS = {".pdf"}

# DPI for PDF rasterization. 300 is the sweet spot for OCR quality vs. memory.
# The format classifier uses 200; OCR benefits from higher resolution.
PDF_RENDER_DPI = 300
PDF_RENDER_TIMEOUT_SECONDS = 60
_USER_POPPLER_PATH = Path.home() / ".local/poppler/usr/bin"
if (_USER_POPPLER_PATH / "pdfinfo").exists():
    POPPLER_PATH = str(_USER_POPPLER_PATH)
    _USER_POPPLER_LIB = Path.home() / ".local/poppler/usr/lib/x86_64-linux-gnu"
    if _USER_POPPLER_LIB.exists():
        os.environ["LD_LIBRARY_PATH"] = (
            f"{_USER_POPPLER_LIB}:{os.environ.get('LD_LIBRARY_PATH', '')}"
        ).rstrip(":")
elif Path("/usr/bin/pdfinfo").exists():
    POPPLER_PATH = "/usr/bin"
else:
    POPPLER_PATH = None


def render_page(file_path: Path, page_number: int) -> Image.Image:
    """Render a single page of a source file to a PIL Image.

    Args:
        file_path: Path to a PDF or image file.
        page_number: 1-indexed page number. For non-PDF inputs this must be 1.

    Returns:
        PIL Image in RGB mode.

    Raises:
        FileNotFoundError: If file_path doesn't exist.
        ValueError: If page_number is invalid or extension is unsupported.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"source file not found: {file_path}")

    ext = file_path.suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        if page_number != 1:
            raise ValueError(
                f"image file {file_path.name} has only page 1, got page {page_number}"
            )
        return Image.open(file_path).convert("RGB")

    if ext in PDF_EXTENSIONS:
        images = convert_from_path(
            str(file_path),
            dpi=PDF_RENDER_DPI,
            first_page=page_number,
            last_page=page_number,
            fmt="png",
            timeout=PDF_RENDER_TIMEOUT_SECONDS,
            poppler_path=POPPLER_PATH,
        )
        if not images:
            raise ValueError(
                f"could not render page {page_number} of {file_path.name}"
            )
        return images[0].convert("RGB")

    raise ValueError(f"unsupported file type: {ext} ({file_path.name})")
