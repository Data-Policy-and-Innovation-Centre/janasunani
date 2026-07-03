"""Image feature extraction for the format classifier.

These functions are lifted directly from the original training/inference
code with no logic changes. They compute the feature vector the trained
model expects at inference time.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image
from loguru import logger

# Color/grayscale threshold from the original code
COLOR_THRESHOLD = 10
# Minimum tesseract confidence to count a word as "real"
CONFIDENCE_THRESHOLD = 60
OCR_LANGUAGES = ("eng", "ori")
TESSERACT_TIMEOUT_SECONDS = 30

# Set at import time; flipped to True if scikit-image is installed
SKIMAGE_AVAILABLE = False
try:
    from skimage import img_as_float  # noqa: F401

    SKIMAGE_AVAILABLE = True
except ImportError:
    pass


_AVAILABLE_TESSERACT_LANGUAGES: set[str] | None = None


def configure_tesseract() -> None:
    """Locate the tesseract binary and configure pytesseract to use it.

    Idempotent — safe to call once per process at worker startup.
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


def _available_tesseract_languages() -> set[str]:
    """Return installed Tesseract languages, or an empty set if unavailable."""
    global _AVAILABLE_TESSERACT_LANGUAGES
    if _AVAILABLE_TESSERACT_LANGUAGES is not None:
        return _AVAILABLE_TESSERACT_LANGUAGES
    try:
        _AVAILABLE_TESSERACT_LANGUAGES = set(pytesseract.get_languages(config=""))
    except Exception:
        _AVAILABLE_TESSERACT_LANGUAGES = set()
    return _AVAILABLE_TESSERACT_LANGUAGES


def calculate_brisque_features(gray_image: np.ndarray) -> dict[str, float]:
    default = {"brisque_mean": 0.0, "brisque_std": 0.0, "brisque_entropy": 0.0}
    if not SKIMAGE_AVAILABLE:
        return default
    try:
        from skimage import img_as_float

        img_float = img_as_float(gray_image)
        kernel = np.ones((7, 7)) / 49
        mu = cv2.filter2D(img_float, -1, kernel)
        sigma = np.sqrt(
            np.maximum(cv2.filter2D(img_float**2, -1, kernel) - mu**2, 0)
        )
        return {
            "brisque_mean": float(np.mean(mu)),
            "brisque_std": float(np.std(sigma)),
            "brisque_entropy": float(
                -np.sum(sigma * np.log2(sigma + 1e-10)) / sigma.size
            ),
        }
    except Exception:
        return default


def calculate_glcm_features(gray_image: np.ndarray) -> dict[str, float]:
    default = {
        "glcm_contrast": 0.0,
        "glcm_dissimilarity": 0.0,
        "glcm_homogeneity": 0.0,
        "glcm_energy": 0.0,
        "glcm_correlation": 0.0,
    }
    try:
        from skimage.feature import graycomatrix, graycoprops

        small = cv2.resize(gray_image, (256, 256))
        small = (
            (small - small.min()) / (small.max() - small.min() + 1e-10) * 255
        ).astype(np.uint8)
        glcm = graycomatrix(
            small,
            [1],
            [0, np.pi / 4, np.pi / 2, 3 * np.pi / 4],
            levels=256,
            symmetric=True,
            normed=True,
        )
        return {
            "glcm_contrast": float(graycoprops(glcm, "contrast").mean()),
            "glcm_dissimilarity": float(graycoprops(glcm, "dissimilarity").mean()),
            "glcm_homogeneity": float(graycoprops(glcm, "homogeneity").mean()),
            "glcm_energy": float(graycoprops(glcm, "energy").mean()),
            "glcm_correlation": float(graycoprops(glcm, "correlation").mean()),
        }
    except Exception:
        return default


def calculate_layout_features(gray_image: np.ndarray) -> dict[str, float]:
    try:
        binary = cv2.adaptiveThreshold(
            gray_image,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            11,
            2,
        )
        text_density = np.sum(binary > 0) / binary.size
        edges = cv2.Canny(gray_image, 50, 150)
        edge_density = np.sum(edges > 0) / edges.size
        hist = np.bincount(gray_image.ravel(), minlength=256)[:256].astype(np.float64)
        hist = hist / (hist.sum() + 1e-10)
        hist_entropy = -np.sum(hist * np.log2(hist + 1e-10))
        return {
            "text_density": float(text_density),
            "edge_density": float(edge_density),
            "hist_entropy": float(hist_entropy),
            "hist_mean": float(gray_image.mean()),
            "hist_std": float(gray_image.std()),
        }
    except Exception:
        return {
            "text_density": 0.0,
            "edge_density": 0.0,
            "hist_entropy": 0.0,
            "hist_mean": 0.0,
            "hist_std": 0.0,
        }


def calculate_quality_features(gray_image: np.ndarray) -> dict[str, float]:
    try:
        blur_scores = []
        for scale in [1.0, 0.5, 0.25]:
            scaled = (
                cv2.resize(
                    gray_image,
                    (int(gray_image.shape[1] * scale), int(gray_image.shape[0] * scale)),
                )
                if scale < 1.0
                else gray_image
            )
            blur_scores.append(cv2.Laplacian(scaled, cv2.CV_64F).var())
        grad_x = cv2.Sobel(gray_image, cv2.CV_64F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray_image, cv2.CV_64F, 0, 1, ksize=3)
        sharpness = np.sqrt(grad_x**2 + grad_y**2).mean()
        dft = cv2.dft(np.float32(gray_image), flags=cv2.DFT_COMPLEX_OUTPUT)
        dft_shift = np.fft.fftshift(dft)
        magnitude = 20 * np.log(
            cv2.magnitude(dft_shift[:, :, 0], dft_shift[:, :, 1]) + 1
        )
        return {
            "blur_multi_scale_max": float(max(blur_scores)),
            "blur_multi_scale_min": float(min(blur_scores)),
            "blur_multi_scale_mean": float(np.mean(blur_scores)),
            "sharpness": float(sharpness),
            "noise_estimate": float(magnitude.std()),
        }
    except Exception:
        return {
            "blur_multi_scale_max": 0.0,
            "blur_multi_scale_min": 0.0,
            "blur_multi_scale_mean": 0.0,
            "sharpness": 0.0,
            "noise_estimate": 0.0,
        }


def perform_ocr(img_pil: Image.Image) -> dict[str, Any]:
    """Run tesseract in English and Odia, return language confidence scores."""
    lang_scores = {"eng": 0, "ori": 0}
    word_counts = {"eng": 0, "ori": 0}
    available_languages = _available_tesseract_languages()
    for lang in OCR_LANGUAGES:
        if lang not in available_languages:
            continue
        try:
            data = pytesseract.image_to_data(
                img_pil,
                lang=lang,
                output_type=pytesseract.Output.DICT,
                config="--psm 3 --oem 1",
                timeout=TESSERACT_TIMEOUT_SECONDS,
            )
            confs = [float(c) for c in data["conf"] if c != "-1" and float(c) > 0]
            word_counts[lang] = sum(1 for c in confs if c > CONFIDENCE_THRESHOLD)
            lang_scores[lang] = np.mean(confs) if confs else 0
        except Exception as e:
            logger.warning(f"ocr {lang} failed or timed out: {e}")

    if word_counts["eng"] > 0 or word_counts["ori"] > 0:
        eng_score = lang_scores["eng"] * (word_counts["eng"] + 1)
        ori_score = lang_scores["ori"] * (word_counts["ori"] + 1)
        predominant = "ori" if ori_score > eng_score else "eng"
    else:
        predominant = "eng"

    return {
        "lang_conf_eng": lang_scores["eng"],
        "lang_conf_ori": lang_scores["ori"],
        "word_count_eng": word_counts["eng"],
        "word_count_ori": word_counts["ori"],
        "predominant_lang": predominant,
    }


def extract_features(img_cv: np.ndarray) -> dict[str, Any] | None:
    """Compute the full feature dict the classifier expects.

    Returns None on any failure. The caller should treat None as
    "this page could not be processed."
    """
    try:
        img_pil = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB))
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        small_gray = cv2.resize(gray, (int(w * 0.75), int(h * 0.75)))
        small_cv = cv2.resize(img_cv, (int(w * 0.75), int(h * 0.75)))
        std_r, std_g, std_b = (np.std(small_cv[:, :, i]) for i in [2, 1, 0])
        channel_diff = max(std_r, std_g, std_b) - min(std_r, std_g, std_b)
        return {
            "blur_score": float(cv2.Laplacian(small_gray, cv2.CV_64F).var()),
            "contrast": float(small_gray.std()),
            "brightness": float(small_gray.mean()),
            "is_color": 1 if channel_diff > COLOR_THRESHOLD else 0,
            **perform_ocr(img_pil),
            **calculate_glcm_features(small_gray),
            **calculate_layout_features(small_gray),
            **calculate_quality_features(small_gray),
            **calculate_brisque_features(small_gray),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# stderr suppression helper — pdf2image and friends are noisy on bad PDFs
# ---------------------------------------------------------------------------

def suppress_stderr() -> int:
    """Redirect fd 2 to /dev/null. Returns the saved fd to pass to restore."""
    devnull = open(os.devnull, "w")
    saved = os.dup(2)
    os.dup2(devnull.fileno(), 2)
    devnull.close()
    return saved


def restore_stderr(saved: int) -> None:
    os.dup2(saved, 2)
    os.close(saved)
