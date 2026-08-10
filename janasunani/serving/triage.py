"""Advisory live triage with bounded spam scoring.

Live submissions are scored over redacted text only (never raw
``complaints.grievance``).  The spam signal is bounded
(pipeline/spam.py, spam-v1.1-bounded) — repetition collapse, length, and
low-signal patterns — and is advisory only (never blocks submission).
Duplicate matching remains slice-scoped and unavailable live.

The default spam guard remains a deterministic cascade.  ``model`` adds the
separate five-class actionability scorer over the same redacted text; it does
not replace the bounded spam evidence and cannot block a submission.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional, Protocol

from loguru import logger

from janasunani.pipeline.ocr_quality import is_repetition_collapsed
from janasunani.serving.schemas import (
    ActionabilityReview,
    DuplicateReview,
    OcrQualityEvidence,
    SpamReview,
    TriageResult,
)

try:
    from janasunani.pipeline.spam import SPAM_VERSION, SpamScorer, score_spam
except Exception:  # pragma: no cover — scorer absent in minimal env
    SPAM_VERSION = "unavailable"  # type: ignore
    SpamScorer = object  # type: ignore
    score_spam = None  # type: ignore

#: Environment variable selecting the triage implementation.
TRIAGE_ENV_VAR = "JANASUNANI_TRIAGE"

#: The shipped heuristic scorer.
TRIAGE_BOUNDED = "bounded"

#: Advisory triage disabled; every submission reports unavailable.
TRIAGE_OFF = "off"

#: A trained scorer, when one exists (#74).
TRIAGE_MODEL = "model"

SUPPORTED_TRIAGE_PROVIDERS = (TRIAGE_BOUNDED, TRIAGE_OFF, TRIAGE_MODEL)


class TriageUnavailableError(RuntimeError):
    """An advisory provider could not be used without blocking submission."""


class LearnedScorerUnresolved(RuntimeError):
    """Base for the reasons ``_resolve_learned_scorer`` has no scorer to give.

    Both subclasses degrade identically at request time -- neither may block
    a citizen's submission -- but they are different states for an operator:
    one says "produce and publish an artifact", the other says "the loader
    hasn't been written yet, no artifact will fix it". Preflight needs to
    tell them apart even though the request path does not.
    """


class ScorerArtifactAbsent(LearnedScorerUnresolved):
    """No ``actionability`` artifact resolves yet."""


class ScorerLoaderUnimplemented(LearnedScorerUnresolved):
    """A retained compatibility diagnostic for an unusable scorer loader.

    A programming gap, not an operational one: supplying an artifact cannot
    fix this by itself.
    """


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


class OffTriageProvider:
    """Report advisory triage as unavailable without scoring anything.

    Distinct from a broken provider on purpose: this is an operator saying
    "do not run triage", and the response says exactly that rather than
    implying the text was assessed and found clean.
    """

    def assess(
        self,
        *,
        redacted_text: str,
        district: Optional[str],
        submitted_on: datetime,
    ) -> TriageResult:
        del redacted_text, district, submitted_on
        return unavailable_triage()


class ScoredTriageProvider:
    """Run an injected scorer, degrading exactly as the default provider does.

    This is the seam a trained model plugs into. The scorer returns a
    ``SpamScore``, never a ``SpamReview``, so the taxonomy is enforced by
    ``SpamScore.to_review_fields`` rather than by the model's good behaviour:
    a reason code outside the closed set cannot reach the wire.

    The two ``except`` clauses are copied from ``UnwiredTriageProvider`` and
    are the whole safety story. A learned scorer that fails to load, times
    out, or returns nonsense degrades to advisory-unavailable. It never
    blocks a citizen's submission and never surfaces a trace.
    """

    def __init__(self, scorer: "SpamScorer") -> None:
        self._scorer = scorer

    def assess(
        self,
        *,
        redacted_text: str,
        district: Optional[str],
        submitted_on: datetime,
    ) -> TriageResult:
        del district, submitted_on
        try:
            scored = self._scorer.score(redacted_text)
            spam = SpamReview(**scored.to_review_fields())
        except TriageUnavailableError:
            return unavailable_triage()
        except Exception:
            return unavailable_triage()
        return TriageResult(spam=spam)


class ActionabilityTriageProvider:
    """Run bounded spam plus a local five-class actionability artifact."""

    def __init__(self, scorer) -> None:
        self._scorer = scorer

    def assess(
        self,
        *,
        redacted_text: str,
        district: Optional[str],
        submitted_on: datetime,
    ) -> TriageResult:
        del district, submitted_on
        try:
            spam = score_spam_review(redacted_text)
        except Exception:
            return unavailable_triage()
        try:
            assessment = self._scorer.score(redacted_text)
            actionability = ActionabilityReview(
                decision=assessment.decision,
                predicted_label=assessment.predicted_label,
                confidence=assessment.confidence,
                probabilities=dict(assessment.probabilities),
                method=assessment.method,
                taxonomy_version=assessment.taxonomy_version,
            )
        except Exception:
            # Preserve the bounded scorer if the learned artifact fails on an
            # individual request; absence of actionability is explicit None.
            return TriageResult(spam=spam)
        return TriageResult(spam=spam, actionability=actionability)


def triage_provider_from_env(value: str | None = None) -> TriageProvider:
    """Select a triage provider by environment, never raising.

    Unset keeps the shipped bounded scorer. ``off`` disables advisory triage.
    ``model`` selects a learned scorer and falls back to the bounded one when
    no artifact resolves, so setting it early costs nothing.
    """
    configured = (value if value is not None else os.environ.get(TRIAGE_ENV_VAR, "")).strip().lower()
    if not configured or configured == TRIAGE_BOUNDED:
        return UnwiredTriageProvider()
    if configured == TRIAGE_OFF:
        return OffTriageProvider()
    if configured == TRIAGE_MODEL:
        try:
            scorer = _resolve_learned_scorer()
        except ScorerArtifactAbsent:
            logger.warning(
                "{}={} but no learned scorer artifact resolved; using the bounded scorer",
                TRIAGE_ENV_VAR,
                TRIAGE_MODEL,
            )
            return UnwiredTriageProvider()
        except ScorerLoaderUnimplemented:
            logger.warning(
                "{}={} but the actionability artifact could not be loaded; "
                "using the bounded scorer",
                TRIAGE_ENV_VAR,
                TRIAGE_MODEL,
            )
            return UnwiredTriageProvider()
        return ActionabilityTriageProvider(scorer)
    logger.warning(
        "{}={!r} is not one of {}; using the bounded scorer",
        TRIAGE_ENV_VAR,
        configured,
        ", ".join(SUPPORTED_TRIAGE_PROVIDERS),
    )
    return UnwiredTriageProvider()


def _resolve_learned_scorer():
    """Resolve a trained scorer artifact.

    Absence and structural/load failure remain distinct operator states, while
    both degrade to the bounded advisory scorer on the request path.
    """
    from janasunani.tracking.artifacts import resolve_artifact

    artifact = resolve_artifact("actionability")
    if artifact is None:
        raise ScorerArtifactAbsent("no actionability artifact resolved")
    try:
        from janasunani.inference.actionability import load_actionability_scorer

        return load_actionability_scorer(artifact)
    except Exception as exc:
        raise ScorerLoaderUnimplemented(
            "the actionability artifact is present but could not be loaded"
        ) from exc


def triage_status() -> tuple[str, bool, str]:
    """Report ``(name, ok, detail)`` for preflight without scoring anything."""
    configured = os.environ.get(TRIAGE_ENV_VAR, "").strip().lower() or TRIAGE_BOUNDED
    if configured == TRIAGE_OFF:
        return (TRIAGE_OFF, True, "advisory triage disabled by configuration")
    if configured == TRIAGE_MODEL:
        try:
            _resolve_learned_scorer()
        except ScorerArtifactAbsent:
            return (
                TRIAGE_BOUNDED,
                False,
                f"{TRIAGE_ENV_VAR}=model but no actionability artifact resolved; "
                f"serving the bounded heuristic scorer ({SPAM_VERSION})",
            )
        except ScorerLoaderUnimplemented:
            return (
                TRIAGE_BOUNDED,
                False,
                f"{TRIAGE_ENV_VAR}=model and an actionability artifact resolved, but "
                "it could not be loaded or validated; serving the bounded "
                f"heuristic scorer ({SPAM_VERSION})",
            )
        return (TRIAGE_MODEL, True, "checksummed actionability artifact loaded")
    if configured not in SUPPORTED_TRIAGE_PROVIDERS:
        return (
            TRIAGE_BOUNDED,
            False,
            f"{TRIAGE_ENV_VAR}={configured!r} is unknown; serving the bounded scorer",
        )
    if score_spam is None:
        return (TRIAGE_BOUNDED, False, "spam scorer import failed; triage reports unavailable")
    return (TRIAGE_BOUNDED, True, f"bounded heuristic scorer ({SPAM_VERSION}); not a learned model")
