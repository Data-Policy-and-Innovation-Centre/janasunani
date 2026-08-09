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
    DuplicateReview,
    DuplicateSignal,
    ExtractionResult,
    GrievanceResult,
    PIIEntity,
    RedactionResult,
    RoutingResult,
    SpamReview,
    TriageResult,
)

# Real processor is intentionally NOT imported at top level — it pulls
# torch/transformers/presidio via janasunani.inference.service and would
# break the import-light contract for the mock skeleton. Use
# get_processor() below for a lazy, env-aware seam.


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
            # Triage receives only the mock-redacted representation too.  The
            # mock is never a source of live low-signal evidence.
            triage=_mock_triage(redaction.redacted_text),
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


def _mock_triage(text: str) -> TriageResult:
    """Deterministic illustrative triage states for frontend contract work only."""
    bucket = hashlib.sha256(text.encode()).digest()[1] % 4
    group_id = hashlib.sha256(text.encode()).hexdigest()[:10]
    mock_low_signal = SpamReview(
        decision="abstained",
        reason_code="mock_low_signal_review_unavailable",
    )
    if bucket == 0:
        return TriageResult(
            duplicate=DuplicateSignal(
                duplicate_kind="resubmission",
                duplicate_group_id=group_id,
                duplicate_ticket_no="CMO202400042",
            ),
            duplicate_review=DuplicateReview(decision="matched"),
            spam=mock_low_signal,
        )
    if bucket == 1:
        return TriageResult(
            duplicate=DuplicateSignal(
                duplicate_kind="campaign",
                duplicate_group_id=group_id,
                related_filings=18,
                # 16 people behind 18 filings: the shape of a real collective
                # grievance, and what the badge is allowed to claim. A mock
                # that omitted this would be modelling a state the contract no
                # longer permits.
                distinct_signatories=16,
            ),
            duplicate_review=DuplicateReview(decision="matched"),
            spam=mock_low_signal,
        )
    return TriageResult(spam=mock_low_signal)

def get_processor(prefer_real: bool = True) -> GrievanceProcessor:
    """Return a GrievanceProcessor for the current environment.

    Lazy, import-light seam for Unit 3 rehearsal: when heavy ML extras
    (pipeline-core, pii, categorizer) and the DVC-mirrored models are
    present, this returns the warm PipelineGrievanceProcessor (real code
    path, routing via RuleRouter -> method "fallback" until Unit 7 crosswalk).
    Otherwise it falls back to MockGrievanceProcessor so the API contract
    and frontend can still be exercised in a light CI env.

    The inference stack is imported inside the function — never at module
    top level — preserving the per-extra lazy-import contract.
    """
    if not prefer_real:
        return MockGrievanceProcessor()
    try:
        from janasunani.inference.service import build_processor

        return build_processor()  # type: ignore[return-value]
    except Exception:
        return MockGrievanceProcessor()
