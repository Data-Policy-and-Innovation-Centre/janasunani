"""Safe seam for advisory live triage.

The historical duplicate index is slice-scoped and does not yet provide a
matcher for a newly submitted grievance.  This module makes that absence
explicit while reserving a narrow future integration point: providers receive
only redacted text, never the raw grievance or direct identity fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from janasunani.serving.schemas import DuplicateReview, SpamReview, TriageResult


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
        spam=SpamReview(decision="not_scored"),
    )


class UnwiredTriageProvider:
    """Current production default until a live duplicate/spam matcher exists."""

    def assess(
        self,
        *,
        redacted_text: str,
        district: Optional[str],
        submitted_on: datetime,
    ) -> TriageResult:
        # Parameters are deliberately accepted but not inspected: this is not
        # a heuristic fallback, and must never fabricate a live triage signal.
        del redacted_text, district, submitted_on
        return TriageResult()
