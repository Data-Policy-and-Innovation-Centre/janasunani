"""Shared text processing utilities for PII redaction."""

import logging
import re

import nltk
from nltk.tokenize import word_tokenize

logger = logging.getLogger(__name__)

try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)


def normalize_text(text: str) -> str:
    """Ensure punctuation spacing matches training format.

    Args:
        text: Input text string

    Returns:
        Text with normalized punctuation spacing
    """
    if not isinstance(text, str):
        return ""

    if not text.strip():
        return ""

    text = re.sub(r"\(", "( ", text)
    text = re.sub(r"\)", " )", text)
    text = re.sub(r"([A-Za-z])([\)\(\.,;:])", r"\1 \2", text)
    text = re.sub(r"([\)\(\.,;:])([A-Za-z])", r"\1 \2", text)
    return text


def preprocess_words(text: str) -> list[str]:
    """Tokenize text to match training format.

    Args:
        text: Input text string

    Returns:
        List of word tokens
    """
    if not text or not text.strip():
        return []
    return word_tokenize(text)


def redact_words(words_tags: list[tuple[str, str]]) -> str:
    """Turn B-PII / I-PII spans into [PII].

    Args:
        words_tags: List of (word, tag) tuples

    Returns:
        String with PII replaced by [PII] tokens
    """
    if not words_tags:
        return ""

    out, inside = [], False

    for w, t in words_tags:
        if t.startswith("B-") or t.startswith("I-"):
            if not inside:
                out.append("[PII]")
                inside = True
        else:
            inside = False
            out.append(w)

    return " ".join(out)