"""Advisory live triage with bounded spam scoring.

Live submissions are scored over redacted text only (never raw
``complaints.grievance``).  The spam signal is bounded
(pipeline/spam.py, spam-v1-bounded) — repetition collapse, length, and
low-signal patterns — and is advisory only (never blocks submission).
Duplicate matching remains slice-scoped and unavailable live.
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

try:
    from janasunani.pipeline.spam import SPAM_VERSION, score_spam
except Exception:  # pragma: no cover — scorer absent in minimal env
    SPAM_VERSION = "unavailable"  # type: ignore
    score_spam = None  # type: ignore


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
            spam_score=0.0,
            spam_reason="clean",
            method="unavailable",
        ),
    )


def score_spam_review(redacted_text: str) -> SpamReview:
    """Score one redacted text with the bounded scorer; advisory only.

    Never reads raw grievance, never mutates status, never scores duplicate
    or not-within-purview families as spam (those are dedup/routing, not
    low-signal, and their prose scores ``clean`` here).
    """
    if score_spam is None:
        raise TriageUnavailableError("spam scorer unavailable")
    scored = score_spam(redacted_text)
    fields = scored.to_review_fields()
    return SpamReview(**fields)


def low_signal_advisory(redacted_text: str) -> SpamReview:
    """Legacy fail-closed advisory (retained for tests and fallback).

    Prefer :func:`score_spam_review` for live scoring; this remains as the
    fallback when the scorer is unavailable and as the pre-validation
    contract that always abstains.
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
            spam_score=0.0,
            spam_reason="clean" if not collapsed else "repetition_collapse",
            method="legacy-advisory",
        )
    return SpamReview(
        decision="abstained",
        reason_code="live_review_disabled_pending_redacted_adjudication",
        evidence=evidence,
        spam_score=0.0,
        spam_reason="clean",
        method="legacy-advisory",
    )


class UnwiredTriageProvider:
    """Current production default — now wired to the bounded scorer."""

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
        try:
            spam = score_spam_review(redacted_text)
        except TriageUnavailableError:
            return unavailable_triage()
        except Exception:
            # Scoring errors must not block submission — fall back to
            # unavailable rather than surfacing an internal trace.
            return unavailable_triage()
        return TriageResult(spam=spam)
