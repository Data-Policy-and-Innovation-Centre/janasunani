"""Typed, advisory-only serving contract for issues #73 and #109."""

import json

import pytest
from pydantic import ValidationError

from janasunani.serving.schemas import (
    ActionabilityReview,
    ClassificationResult,
    DuplicateReview,
    DuplicateSignal,
    EmpiricalRoutingEvidence,
    ExtractionResult,
    GrievanceResult,
    OcrQualityEvidence,
    RedactionResult,
    RoutingResult,
    SpamReview,
    TriageResult,
)
from janasunani.serving.processor import _mock_triage
from janasunani.serving.triage import low_signal_advisory


def test_resubmission_and_campaign_context_cannot_be_conflated():
    resubmission = DuplicateSignal(
        duplicate_kind="resubmission",
        duplicate_group_id="g-resubmission",
        duplicate_ticket_no="CMO202400042",
    )
    campaign = DuplicateSignal(
        duplicate_kind="campaign",
        duplicate_group_id="g-campaign",
        related_filings=18,
    )

    assert resubmission.duplicate_ticket_no == "CMO202400042"
    assert resubmission.related_filings is None
    assert campaign.duplicate_ticket_no is None
    assert campaign.related_filings == 18

    with pytest.raises(ValidationError, match="campaign requires related_filings"):
        DuplicateSignal(duplicate_kind="campaign", duplicate_group_id="g")
    with pytest.raises(ValidationError, match="campaign must not carry"):
        DuplicateSignal(
            duplicate_kind="campaign",
            duplicate_group_id="g",
            duplicate_ticket_no="CMO1",
            related_filings=2,
        )


def test_duplicate_review_keeps_absence_distinct_from_a_no_match():
    not_indexed = DuplicateReview(
        decision="not_indexed",
        reason="The live submission is outside every completed index slice.",
    )
    abstained = DuplicateReview(
        decision="abstained",
        reason="The redacted text is too short to compare safely.",
    )
    unavailable = DuplicateReview(
        decision="unavailable",
        reason="The duplicate provider could not be reached.",
    )

    assert not_indexed.decision == "not_indexed"
    assert abstained.decision == "abstained"
    assert unavailable.decision == "unavailable"

    with pytest.raises(ValidationError, match="not_indexed duplicate review requires"):
        DuplicateReview(decision="not_indexed")
    with pytest.raises(ValidationError, match="no_match duplicate review must not"):
        DuplicateReview(decision="no_match", reason="not needed")


def test_duplicate_signal_requires_a_matched_review_state():
    signal = DuplicateSignal(
        duplicate_kind="resubmission",
        duplicate_group_id="g-resubmission",
        duplicate_ticket_no="CMO202400042",
    )
    legacy_shape = TriageResult(duplicate=signal)
    assert legacy_shape.duplicate_review.decision == "matched"

    with pytest.raises(ValidationError, match="duplicate signal must be present"):
        TriageResult(
            duplicate=signal,
            duplicate_review=DuplicateReview(decision="no_match"),
        )
    with pytest.raises(ValidationError, match="duplicate signal must be present"):
        TriageResult(duplicate_review=DuplicateReview(decision="matched"))


def test_low_signal_abstention_is_visible_and_has_a_deterministic_reason_code():
    review = SpamReview(
        decision="abstained",
        reason_code="live_review_disabled_pending_redacted_adjudication",
    )
    assert review.decision == "abstained"
    assert review.reason_code
    # Bounded scorer now carries spam_score/spam_reason; unset fields default to None
    assert review.spam_score is None or 0.0 <= review.spam_score <= 1.0

    with pytest.raises(ValidationError, match="validated_low_signal_evidence requires review"):
        SpamReview(
            decision="abstained",
            reason_code="validated_low_signal_evidence",
        )
    with pytest.raises(ValidationError, match="review requires auditable"):
        SpamReview(
            decision="review",
            reason_code="validated_low_signal_evidence",
        )

    legacy = SpamReview.model_validate(
        {
            "decision": "flagged",
            "spam_reason": "Old mock flag",
            "spam_score": 0.81,
        }
    )
    assert legacy.decision == "abstained"
    assert legacy.reason_code == "live_review_disabled_pending_redacted_adjudication"
    assert legacy.spam_score is None

    reserved_review = SpamReview(
        decision="review",
        reason_code="validated_low_signal_evidence",
        evidence=(OcrQualityEvidence(kind="repetition_collapse", observed=True),),
    )
    assert reserved_review.decision == "review"


@pytest.mark.parametrize("bad_probability", [float("nan"), float("inf"), True])
def test_actionability_probabilities_must_be_finite_numeric_values(bad_probability):
    probabilities = {
        "actionable": 0.2,
        "underspecified": 0.2,
        "irrelevant": 0.2,
        "out_of_scope": 0.2,
        "policy_blocked": 0.2,
    }
    probabilities["irrelevant"] = bad_probability

    with pytest.raises(ValidationError, match="finite"):
        ActionabilityReview(
            decision="review",
            predicted_label="irrelevant",
            confidence=0.2,
            probabilities=probabilities,
            method="local-test",
        )


def test_actionability_confidence_rejects_boolean_values():
    with pytest.raises(ValidationError, match="confidence"):
        ActionabilityReview(
            decision="abstained",
            predicted_label="actionable",
            confidence=True,
            probabilities={
                "actionable": 1.0,
                "underspecified": 0.0,
                "irrelevant": 0.0,
                "out_of_scope": 0.0,
                "policy_blocked": 0.0,
            },
            method="local-test",
        )


def test_binary_actionability_contract_requests_review_without_a_reason_label():
    result = ActionabilityReview(
        decision="review",
        predicted_label="review_required",
        confidence=0.8,
        probabilities={"actionable": 0.2, "review_required": 0.8},
        method="tfidf-review-v1",
        objective="actionable_vs_officer_review",
    )

    assert result.predicted_label == "review_required"
    assert "underspecified" not in result.probabilities


def test_binary_actionability_contract_rejects_fabricated_reason_probabilities():
    with pytest.raises(ValidationError, match="selected objective"):
        ActionabilityReview(
            decision="review",
            predicted_label="underspecified",
            confidence=0.8,
            probabilities={"actionable": 0.2, "review_required": 0.8},
            method="tfidf-review-v1",
            objective="actionable_vs_officer_review",
        )


def test_low_signal_advisory_records_ocr_quality_evidence_but_still_abstains():
    collapsed = low_signal_advisory("repeat this phrase " * 30)
    ordinary = low_signal_advisory(
        "The village road has been damaged for several months and residents "
        "request a safe repair before the monsoon makes travel more difficult."
    )

    assert collapsed.decision == "abstained"
    assert collapsed.reason_code == "ocr_repetition_collapse_unvalidated"
    assert collapsed.evidence[0].observed is True
    assert ordinary.decision == "abstained"
    assert ordinary.reason_code == (
        "live_review_disabled_pending_redacted_adjudication"
    )
    assert ordinary.evidence[0].observed is False
    # Bounded scorer now populates spam_score/spam_reason on the advisory path as well
    assert collapsed.spam_score is not None
    assert 0.0 <= collapsed.spam_score <= 1.0


def test_bounded_spam_review_rejects_boolean_score_and_conflicting_reason():
    base = {
        "decision": "review",
        "reason_code": "low_signal_no_grievance",
        "spam_score": 0.78,
        "spam_reason": "low_signal_no_grievance",
        "evidence": ({"kind": "repetition_collapse", "observed": False},),
        "method": "test",
    }
    with pytest.raises(ValidationError, match="numeric"):
        SpamReview(**{**base, "spam_score": True})
    with pytest.raises(ValidationError, match="must match"):
        SpamReview(**{**base, "spam_reason": "length_too_short"})


def test_unwired_live_triage_is_explicitly_abstained_pending_validation():
    triage = TriageResult()
    dumped = triage.model_dump(mode="json")
    assert dumped["duplicate"] is None
    assert dumped["duplicate_review"]["decision"] == "not_indexed"
    assert dumped["spam"]["decision"] == "abstained"
    assert dumped["spam"]["reason_code"] == "live_review_disabled_pending_redacted_adjudication"
    # Bounded scorer now adds spam_score/spam_reason/method; allow None or bounded value
    assert dumped["spam"]["evidence"] == []
    assert dumped["spam"]["spam_score"] is None or 0.0 <= dumped["spam"]["spam_score"] <= 1.0


def test_older_persisted_result_gets_the_explicit_low_signal_abstention_default():
    result = GrievanceResult(
        id="old-result",
        ticket_no="JSOLD",
        status="Submitted",
        submitted_on="2026-08-07T12:00:00Z",
        extraction=ExtractionResult(source="text", extracted_text="Synthetic text"),
        redaction=RedactionResult(redacted_text="Synthetic text", entities=[]),
        classification=ClassificationResult(category="Roads", language="en"),
        summary="Synthetic text",
        routing=RoutingResult(
            dept="Works",
            office="Works Department",
            confidence=0.8,
            method="rules",
        ),
    )
    assert result.triage.spam.decision == "abstained"
    assert result.triage.duplicate_review.decision == "not_indexed"


def test_legacy_persisted_duplicate_signal_gets_the_matched_review_state():
    triage = TriageResult.model_validate(
        {
            "duplicate": {
                "duplicate_kind": "campaign",
                "duplicate_group_id": "g-campaign",
                "related_filings": 18,
            },
            "spam": {"decision": "not_scored", "spam_score": 0.81},
        }
    )

    assert triage.duplicate is not None
    assert triage.duplicate_review.decision == "matched"
    assert triage.spam.decision == "abstained"
    assert triage.spam.spam_score is None or 0.0 <= triage.spam.spam_score <= 1.0


def test_mock_contract_never_claims_a_low_signal_review():
    states: set[str] = set()
    for i in range(100):
        triage = _mock_triage(f"synthetic grievance {i}")
        if triage.duplicate is not None:
            states.add(triage.duplicate.duplicate_kind)
        else:
            states.add(triage.spam.decision)

    assert states == {"resubmission", "campaign", "abstained"}


def test_learned_routing_requires_aggregate_evidence():
    evidence = EmpiricalRoutingEvidence(
        support=4000,
        concentration=0.9,
        width="category+subcategory+district",
    )
    route = RoutingResult(
        dept="PHED",
        office="PHED Sambalpur Division",
        escalation_authority="District Magistrate, Sambalpur",
        confidence=0.9,
        method="learned",
        empirical_evidence=evidence,
    )
    assert route.empirical_evidence == evidence

    with pytest.raises(ValidationError, match="learned routing requires"):
        RoutingResult(
            dept="PHED",
            office="PHED Sambalpur Division",
            confidence=0.9,
            method="learned",
        )
    with pytest.raises(ValidationError, match="only learned routing"):
        RoutingResult(
            dept="PHED",
            office="PHED Sambalpur Division",
            confidence=0.8,
            method="rules",
            empirical_evidence=evidence,
        )


def test_a_campaign_survives_a_serialized_round_trip_with_signatories():
    """Codex asked for a serialized provider response, not a hand-built object.

    `store.py` persists `model_dump()` and re-validates on read, so a field
    that only works in memory is not actually in the contract.
    """
    from janasunani.serving.schemas import DuplicateSignal

    signal = DuplicateSignal(
        duplicate_kind="campaign",
        duplicate_group_id="GRP-1",
        related_filings=18,
        distinct_signatories=16,
    )
    restored = DuplicateSignal.model_validate(json.loads(signal.model_dump_json()))
    assert restored.distinct_signatories == 16
    assert restored.related_filings == 18


def test_a_legacy_campaign_without_signatories_still_loads():
    """Requiring the field would make every campaign recorded before it exists
    unreadable, because the result store re-validates on read."""
    from janasunani.serving.schemas import DuplicateSignal

    restored = DuplicateSignal.model_validate(
        {
            "duplicate_kind": "campaign",
            "duplicate_group_id": "GRP-legacy",
            "related_filings": 18,
        }
    )
    assert restored.distinct_signatories is None


def test_more_signatories_than_filings_is_rejected():
    """A counting bug that would inflate the ratio the badge is gated on."""
    import pytest as _pytest

    from janasunani.serving.schemas import DuplicateSignal

    with _pytest.raises(ValueError, match="cannot exceed related_filings"):
        DuplicateSignal(
            duplicate_kind="campaign",
            duplicate_group_id="GRP-2",
            related_filings=3,
            distinct_signatories=9,
        )


def test_the_mock_processor_emits_a_displayable_campaign():
    """The regression Codex found: the guard must not blank the demo flow.

    Every bucket-1 campaign the mock emits must carry enough evidence for the
    frontend to render the badge, or the signatory gate removed a working
    demo surface instead of rejecting an unverified group.
    """
    from janasunani.serving.processor import _mock_triage

    # The bucket is a hash of the text, so vary the text rather than the ids.
    seen_campaign = False
    for index in range(64):
        triage = _mock_triage(f"water supply irregular in ward {index}")
        duplicate = triage.duplicate
        if duplicate is not None and duplicate.duplicate_kind == "campaign":
            seen_campaign = True
            assert duplicate.distinct_signatories is not None, (
                "a campaign the API emits carries no signatory evidence, so the "
                "frontend guard will withhold the badge and the demo loses it"
            )
            assert duplicate.distinct_signatories >= 2
            assert duplicate.distinct_signatories <= duplicate.related_filings
    assert seen_campaign, "no campaign was produced; the assertion above never ran"
