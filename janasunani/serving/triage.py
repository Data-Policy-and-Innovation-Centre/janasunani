"""Safe seam for advisory live triage.

The historical duplicate index is slice-scoped and does not yet provide a
matcher for a newly submitted grievance.  This module makes that absence
explicit while reserving a narrow future integration point: providers receive
only redacted text, never the raw grievance or direct identity fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from janasunani.pipeline.ocr_quality import is_repetition_collapsed
from janasunani.serving.schemas import (
    DuplicateReview,
    OcrQualityEvidence,
    SpamReview,
    TriageResult,
)


class TriageUnavailableError(RuntimeError):
    """An advisory provider could not be used without blocking submission."""


class TriageProvider(Protocol):
    """Assess one submission after PII redaction and before classification."""

    def assess(
        self,
        *,
        redacted_text: str,
        district: Optional[str],
        submitted_on: datetime,
    ) -> TriageResult: ...


def unavailable_triage() -> TriageResult:
    """Return the safe non-blocking state after a provider availability error."""
    return TriageResult(
        duplicate_review=DuplicateReview(
            decision="unavailable",
            reason=(
                "Duplicate matching is temporarily unavailable. This is not a "
                "finding that no related filing exists."
            ),
        ),
        spam=SpamReview(
            decision="abstained",
            reason_code="advisory_provider_unavailable",
        ),
    )


def low_signal_advisory(redacted_text: str) -> SpamReview:
    """Return the current fail-closed low-signal advisory.

    The sole observation is the existing OCR repetition-collapse guard.  It
    runs over already-redacted text and is recorded for audit, but it cannot
    create a review flag until a redacted, human-adjudicated validation release
    authorizes a rule.  This is not a score, spam model, classifier, or a
    pipeline gate.
    """

    collapsed = is_repetition_collapsed(redacted_text)
    evidence = (
        OcrQualityEvidence(kind="repetition_collapse", observed=collapsed),
    )
    if collapsed:
        return SpamReview(
            decision="abstained",
            reason_code="ocr_repetition_collapse_unvalidated",
            evidence=evidence,
        )
    return SpamReview(
        decision="abstained",
        reason_code="live_review_disabled_pending_redacted_adjudication",
        evidence=evidence,
    )


class UnwiredTriageProvider:
    """Current production default before a validated low-signal release."""

    def assess(
        self,
        *,
        redacted_text: str,
        district: Optional[str],
        submitted_on: datetime,
    ) -> TriageResult:
        # Duplicate matching remains unwired.  The low-signal check uses only
        # redacted text; district and submission time cannot become proxies
        # for identity, routing, or filing history.
        del district, submitted_on
        return TriageResult(spam=low_signal_advisory(redacted_text))
