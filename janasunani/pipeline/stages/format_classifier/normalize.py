"""Normalize raw model output strings to canonical pipeline values.

The trained classifier emits values like "yes ", "Yes", "n/a", "Mostly English",
etc. — artifacts of the manual labeling process. This module maps them to
canonical values: "yes" / "no" / "partial" for booleans, "English" / "Odia" /
"Hindi" / "picture" for languages, etc.

NULL (Python None) is used for "we don't know" — these get written to SQLite
as NULL rather than empty strings so downstream queries can use IS NULL.
"""
from __future__ import annotations

import re

import pandas as pd


def normalize_value(value: object, column: str) -> str | None:
    """Map a raw model output to a canonical value (or None for unknown)."""
    if pd.isna(value) or str(value).strip() == "":
        return None

    value = re.sub(r"\s+", " ", str(value).strip().lower())

    if column in ("scanned", "handwritten", "printed"):
        if value in ("yes", "y"):
            return "yes"
        if value in ("no", "n"):
            return "no"
        if value in ("partial", "some"):
            return "partial"
        if value in ("blank", "n/a", "n./a", "unknown"):
            return None
        if "no" in value or "photo" in value or "picture" in value:
            return "no"
        if "yes" in value:
            return "yes"
        return None

    if column == "image_quality":
        if value in ("high", "h"):
            return "high"
        if value in ("medium", "med", "m"):
            return "medium"
        if value in ("low", "l"):
            return "low"
        return None

    if column == "color_scale":
        clean = value.replace(" ", "").replace("-", "").replace("&", "")
        if clean in ("bnw", "bw", "blackandwhite"):
            return "bnw"
        if clean in ("rgb", "rbg", "color", "colour"):
            return "rgb"
        return None

    if column == "language":
        # Multi-label: a page may be tagged with several languages
        parts = [
            p.strip()
            for p in value.replace(" and ", ", ").replace(" & ", ", ").split(",")
        ]
        languages: list[str] = []
        for part in parts:
            pl = part.lower()
            if ("english" in pl or "eng" in pl) and "English" not in languages:
                languages.append("English")
            if ("odia" in pl or "odi" in pl) and "Odia" not in languages:
                languages.append("Odia")
            if "hindi" in pl and "Hindi" not in languages:
                languages.append("Hindi")
            if "picture" in pl and "picture" not in languages:
                languages.append("picture")
        return ", ".join(sorted(languages)) if languages else None

    return value
