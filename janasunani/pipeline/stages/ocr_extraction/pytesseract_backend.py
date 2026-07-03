"""Pytesseract OCR backend.

CPU-only. Uses Sauvola adaptive thresholding for preprocessing, then
runs tesseract over a list of candidate languages (or one forced
language) and returns the result with the highest mean confidence.

Lifted from the original extract_pytesseract.py with the line-by-line
code removed — the pipeline stores one text blob per page, so the
simpler whole-image OCR is what we need.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytesseract
from loguru import logger
from PIL import Image
from pytesseract import Output
from skimage.filters import threshold_sauvola

# Languages tried when no force_lang is given. The first two are single-
# language modes; the third runs tesseract with both dictionaries enabled.
# Whichever returns the highest mean confidence wins.
CANDIDATE_LANGS = ["eng", "ori", "eng+ori"]

# Default tesseract config: legacy+LSTM engine, assume uniform block of text.
DEFAULT_CONFIG = "--oem 1 --psm 6"
TESSERACT_TIMEOUT_SECONDS = 60


def _configure_tesseract() -> None:
    """Locate the tesseract binary if not already configured.

    Idempotent — safe to call multiple times.
    """
    from shutil import which

    candidates = [
        os.environ.get("TESSERACT_CMD"),
        which("tesseract"),
        str(Path.home() / ".local/tesseract/usr/bin/tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            libdirs = [
                Path.home() / ".local/tesseract/usr/lib/x86_64-linux-gnu",
                Path.home() / ".local/poppler/usr/lib/x86_64-linux-gnu",
            ]
            local_libs = ":".join(str(path) for path in libdirs if path.exists())
            if local_libs:
                os.environ["LD_LIBRARY_PATH"] = (
                    f"{local_libs}:{os.environ.get('LD_LIBRARY_PATH', '')}"
                ).rstrip(":")
            tessdata = Path.home() / ".local/tesseract/usr/share/tesseract-ocr/4.00/tessdata"
            if "TESSDATA_PREFIX" not in os.environ and tessdata.exists():
                os.environ["TESSDATA_PREFIX"] = str(tessdata)
            return


def _available_languages() -> set[str]:
    _configure_tesseract()
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return set()


def _filter_available_langs(langs: list[str]) -> list[str]:
    available = _available_languages()
    if not available:
        return langs
    return [
        lang
        for lang in langs
        if all(part in available for part in lang.split("+"))
    ]


def _apply_sauvola(gray_image: np.ndarray, window: int = 35, k: float = 0.2) -> np.ndarray:
    """Sauvola adaptive thresholding. Good for documents with uneven lighting."""
    threshold = threshold_sauvola(gray_image, window_size=window, k=k)
    return (gray_image > threshold).astype(np.uint8) * 255


def _ocr_one_lang(image: np.ndarray, config: str, lang: str) -> tuple[str, float]:
    """Run tesseract once with a specific language. Returns (text, mean_conf)."""
    data = pytesseract.image_to_data(
        image,
        lang=lang,
        output_type=Output.DICT,
        config=config,
        timeout=TESSERACT_TIMEOUT_SECONDS,
    )
    # conf values arrive as ints OR strings, integral OR float ("96.3")
    # depending on tesseract/pytesseract version; -1 marks non-text blocks.
    # int() on a float string raises, which the caller treats as OCR failure
    # -> silently blank pages, so parse as float.
    confs = [float(c) for c in data["conf"] if float(c) >= 0]
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    text = pytesseract.image_to_string(
        image,
        lang=lang,
        config=config,
        timeout=TESSERACT_TIMEOUT_SECONDS,
    )
    return text, avg_conf


def extract_text(
    image: Image.Image,
    force_lang: str | None = None,
    config: str = DEFAULT_CONFIG,
) -> str:
    """Extract text from a PIL image using pytesseract.

    Args:
        image: PIL Image in any mode (will be converted to grayscale).
        force_lang: If given, use this tesseract language code exclusively
            (e.g. "eng", "ori", "eng+ori"). Otherwise, try all candidates
            and return the highest-confidence result.
        config: Tesseract config string.

    Returns:
        Extracted text. Returns empty string if all candidate languages fail.
    """
    _configure_tesseract()

    # Convert to grayscale numpy array and apply Sauvola
    gray = np.asarray(image.convert("L"))
    filtered = _apply_sauvola(gray)

    if force_lang is not None:
        if force_lang not in _filter_available_langs([force_lang]):
            # The requested traineddata isn't installed (e.g. `ori` missing).
            # Degrade to the available candidates rather than silently
            # producing an empty page that gets marked ok.
            logger.warning(
                f"tesseract language {force_lang!r} not installed; "
                "falling back to available candidate languages"
            )
        else:
            try:
                text, _ = _ocr_one_lang(filtered, config, force_lang)
                return text
            except Exception as e:
                logger.error(f"tesseract failed with lang={force_lang}: {e}")
                return ""

    best_text = ""
    best_conf = -1.0
    for lang in _filter_available_langs(CANDIDATE_LANGS):
        try:
            text, conf = _ocr_one_lang(filtered, config, lang)
        except Exception as e:
            logger.error(f"tesseract failed with lang={lang}: {e}")
            continue
        if conf > best_conf:
            best_text = text
            best_conf = conf

    return best_text


# ---------------------------------------------------------------------------
# Map pipeline language values to tesseract language codes
# ---------------------------------------------------------------------------

_LANG_TO_TESSERACT = {
    "English": "eng",
    "Odia": "ori",
    "English, Odia": "eng+ori",
}


def pipeline_lang_to_tesseract(pipeline_lang: str | None) -> str | None:
    """Translate a pipeline `language` column value to a tesseract lang code.

    Returns None if the value doesn't map to a known tesseract code (caller
    should fall back to trying all candidates).
    """
    if pipeline_lang is None:
        return None
    return _LANG_TO_TESSERACT.get(pipeline_lang)
