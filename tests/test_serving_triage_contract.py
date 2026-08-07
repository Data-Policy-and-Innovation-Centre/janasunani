"""Typed, advisory-only serving contract for issues #73 and #109."""

import pytest
from pydantic import ValidationError

from janasunani.serving.schemas import (
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
    assert "spam_score" not in review.model_dump()

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
    assert "spam_score" not in legacy.model_dump()

    reserved_review = SpamReview(
        decision="review",
        reason_code="validated_low_signal_evidence",
        evidence=(OcrQualityEvidence(kind="repetition_collapse", observed=True),),
    )
    assert reserved_review.decision == "review"


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
    assert "spam_score" not in collapsed.model_dump()


def test_unwired_live_triage_is_explicitly_abstained_pending_validation():
    triage = TriageResult()
    assert triage.model_dump(mode="json") == {
        "duplicate": None,
        "duplicate_review": {
            "decision": "not_indexed",
            "reason": (
                "Duplicate matching has not been run for this submission because "
                "the live submission path is not connected to a completed index."
            ),
        },
        "spam": {
            "decision": "abstained",
            "reason_code": "live_review_disabled_pending_redacted_adjudication",
            "evidence": [],
        },
    }


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
    assert "spam_score" not in triage.spam.model_dump()


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
