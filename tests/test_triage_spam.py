"""Triage wiring for bounded spam scorer (Unit 2b)."""

from datetime import UTC, datetime

from janasunani.pipeline.spam import SPAM_VERSION
from janasunani.serving.schemas import SpamReview, TriageResult
from janasunani.serving.triage import (
    UnwiredTriageProvider,
    score_spam_review,
    unavailable_triage,
)


def test_assess_returns_spam_with_score():
    provider = UnwiredTriageProvider()
    result = provider.assess(
        redacted_text="The village road has been broken for months and needs repair.",
        district="Sambalpur",
        submitted_on=datetime.now(UTC),
    )
    assert isinstance(result, TriageResult)
    spam = result.spam
    assert isinstance(spam, SpamReview)
    assert 0.0 <= spam.spam_score <= 1.0
    assert spam.spam_reason in {
        "low_signal_details_inadequate",
        "low_signal_no_grievance",
        "repetition_collapse",
        "length_too_short",
        "clean",
    }
    assert spam.method == SPAM_VERSION
    assert spam.evidence


def test_assess_short_text_flagged_but_advisory():
    provider = UnwiredTriageProvider()
    result = provider.assess(
        redacted_text="hi",
        district="Sambalpur",
        submitted_on=datetime.now(UTC),
    )
    # Flagged as length_too_short, decision review, but still advisory — not blocking
    assert result.spam.spam_score >= 0.5
    assert result.spam.decision == "review"
    assert result.spam.reason_code == "length_too_short"


def test_assess_clean_text_abstained():
    provider = UnwiredTriageProvider()
    long_text = (
        "The drinking water hand pump in our village has been broken for two months. "
        "We have complained to the block office but no action was taken. Kindly repair urgently."
    )
    result = provider.assess(
        redacted_text=long_text,
        district="Sambalpur",
        submitted_on=datetime.now(UTC),
    )
    assert result.spam.spam_score < 0.5
    assert result.spam.spam_reason == "clean"
    assert result.spam.decision == "abstained"


def test_unavailable_fallback():
    result = unavailable_triage()
    assert result.spam.decision == "abstained"
    assert result.spam.reason_code == "advisory_provider_unavailable"
    assert result.spam.spam_score is None
    assert result.spam.spam_reason is None
    assert result.spam.method == "unavailable"
    assert result.duplicate_review.decision == "unavailable"


def test_method_reflects_scorer_version():
    review = score_spam_review("The village road needs repair urgently with sufficient length.")
    assert review.method == SPAM_VERSION


def test_score_spam_review_evidence():
    review = score_spam_review("repeat this phrase " * 40)
    assert review.evidence
    assert review.evidence[0].kind == "repetition_collapse"
    assert review.spam_score is not None


def test_triage_never_blocks_on_exception(monkeypatch):
    # Simulate scorer raising; should fall back to unavailable rather than raise
    import janasunani.serving.triage as triage_mod

    def failing_score(_text):
        raise RuntimeError("scorer blew up")

    monkeypatch.setattr(triage_mod, "score_spam", failing_score)
    provider = UnwiredTriageProvider()
    result = provider.assess(
        redacted_text="anything",
        district="Sambalpur",
        submitted_on=datetime.now(UTC),
    )
    assert result.spam.reason_code == "advisory_provider_unavailable"
    assert result.spam.decision == "abstained"


def test_duplicate_prose_never_flagged_via_triage():
    provider = UnwiredTriageProvider()
    legit = (
        "Respected sir, the road to our village has been damaged for months and "
        "children cannot reach school. Please repair it on priority."
    )
    result = provider.assess(redacted_text=legit, district="Sambalpur", submitted_on=datetime.now(UTC))
    assert result.spam.spam_reason == "clean"
    assert result.spam.spam_score < 0.5


def test_not_within_purview_prose_never_flagged_via_triage():
    provider = UnwiredTriageProvider()
    legit = (
        "My application for a central scheme is pending at the central ministry. "
        "The state cell may not have jurisdiction but the filing is detailed and legitimate."
    )
    result = provider.assess(redacted_text=legit, district="Sambalpur", submitted_on=datetime.now(UTC))
    assert result.spam.spam_reason == "clean"
    assert result.spam.spam_score < 0.5


def test_spam_review_model_valid():
    review = score_spam_review("hi")
    # Should be valid pydantic model
    assert review.model_dump()
    assert 0.0 <= review.spam_score <= 1.0


def test_ppv_reported_over_synthetic_lake(tmp_path):
    # PPV harness should run without error even on missing lake (reported not gated)
    from janasunani.evaluation.spam_scorecard import compute_ppv

    result = compute_ppv(tmp_path, "Sambalpur", 2024)
    # Missing lake -> total 0 but keys present, ppv None is allowed
    assert "ppv_holdout" in result
    assert "false_positive_rate_holdout" in result
    assert "slice" in result
