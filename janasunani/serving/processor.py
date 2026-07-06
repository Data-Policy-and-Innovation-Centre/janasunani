"""Grievance processors behind the API.

``GrievanceProcessor`` is the seam Phase 8/9 fills: the skeleton ships only
``MockGrievanceProcessor``, which returns the real response *shapes* with
canned-but-deterministic values so the frontend can be built (and the API
tested) before any model loads. The wire-up swaps in the warm inference
service behind the same protocol — no endpoint changes.

⚠ The mock's "redaction" is a toy regex (digit runs + emails), NOT the
production Presidio analyzer, and its category/routing are hash-picked from
fixed lists. Nothing here may ever serve real citizen submissions.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Optional, Protocol

from janasunani.serving.schemas import (
    ClassificationResult,
    ExtractionResult,
    GrievanceResult,
    PIIEntity,
    RedactionResult,
    RoutingResult,
)


class GrievanceProcessor(Protocol):
    """What Phase 8/9 must provide. Exactly one of text/document is set."""

    name: str

    def process(
        self,
        *,
        grievance_id: str,
        ticket_no: str,
        text: Optional[str] = None,
        document_name: Optional[str] = None,
        document_bytes: Optional[bytes] = None,
        district: Optional[str] = None,
    ) -> GrievanceResult: ...


# Real top-level categories from the historical lake, so frontend styling is
# developed against strings of realistic length/wording.
_MOCK_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Drinking Water Supply", "Rural Water Supply & Sanitation"),
    ("Electricity", "Energy"),
    ("Roads & Bridges", "Works"),
    ("Public Health", "Health & Family Welfare"),
    ("Land & Revenue", "Revenue & Disaster Management"),
)

# Toy patterns for the mock only — see module docstring.
_MOCK_PHONE = re.compile(r"\b\d{10}\b")
_MOCK_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


class MockGrievanceProcessor:
    """Deterministic canned results: same input -> same output (frontend
    snapshots and API tests stay stable)."""

    name = "mock"

    def process(
        self,
        *,
        grievance_id: str,
        ticket_no: str,
        text: Optional[str] = None,
        document_name: Optional[str] = None,
        document_bytes: Optional[bytes] = None,
        district: Optional[str] = None,
    ) -> GrievanceResult:
        if text is not None:
            extraction = ExtractionResult(source="text", extracted_text=text)
        else:
            size = len(document_bytes or b"")
            extraction = ExtractionResult(
                source="document",
                extracted_text=(
                    f"[mock OCR of {document_name} ({size} bytes)] "
                    "The hand pump in our village has been broken for two "
                    "months. Contact me at 9876543210."
                ),
                ocr_model="mock-ocr",
                pages=1,
            )

        redaction = _mock_redact(extraction.extracted_text)
        category, dept = _pick(_MOCK_CATEGORIES, extraction.extracted_text)
        district = district or "Khordha"

        return GrievanceResult(
            id=grievance_id,
            ticket_no=ticket_no,
            status="Submitted",
            submitted_on=datetime.now(UTC),
            extraction=extraction,
            redaction=redaction,
            classification=ClassificationResult(category=category, language="en"),
            summary=_mock_summary(redaction.redacted_text),
            routing=RoutingResult(
                dept=dept,
                office=f"Office of the Collector, {district}",
                designation="Block Development Officer",
                escalation_authority=f"District Magistrate, {district}",
                confidence=0.42,
                method="mock",
            ),
        )


def _pick(options, text: str):
    """Stable pseudo-random choice keyed on the input text."""
    digest = hashlib.sha256(text.encode()).digest()
    return options[digest[0] % len(options)]


def _mock_redact(text: str) -> RedactionResult:
    entities = [
        PIIEntity(entity=label, start=m.start(), end=m.end())
        for label, pattern in (("PHONE", _MOCK_PHONE), ("EMAIL", _MOCK_EMAIL))
        for m in pattern.finditer(text)
    ]
    entities.sort(key=lambda e: e.start)
    redacted = []
    cursor = 0
    for ent in entities:
        redacted.append(text[cursor : ent.start])
        redacted.append(f"<{ent.entity}>")
        cursor = ent.end
    redacted.append(text[cursor:])
    return RedactionResult(redacted_text="".join(redacted), entities=entities)


def _mock_summary(redacted_text: str, max_chars: int = 180) -> str:
    flat = " ".join(redacted_text.split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1].rsplit(" ", 1)[0] + "…"
