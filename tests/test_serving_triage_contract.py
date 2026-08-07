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
    RedactionResult,
    RoutingResult,
    SpamReview,
    TriageResult,
)
from janasunani.serving.processor import _mock_triage


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


def test_spam_abstention_is_visible_and_explained():
    review = SpamReview(
        decision="abstained",
        spam_reason="Insufficient evidence for a reliable low-signal flag.",
    )
    assert review.decision == "abstained"
    assert review.spam_reason

    with pytest.raises(ValidationError, match="abstained spam review requires a reason"):
        SpamReview(decision="abstained")
    with pytest.raises(ValidationError, match="not_scored spam review must not imply"):
        SpamReview(decision="not_scored", spam_score=0.0)


def test_unwired_live_triage_is_explicitly_not_scored():
    triage = TriageResult()
    assert triage.model_dump() == {
        "duplicate": None,
        "duplicate_review": {
            "decision": "not_indexed",
            "reason": (
                "Duplicate matching has not been run for this submission because "
                "the live submission path is not connected to a completed index."
            ),
        },
        "spam": {
            "decision": "not_scored",
            "spam_reason": None,
            "spam_score": None,
        },
    }


def test_older_persisted_result_gets_the_explicit_not_scored_default():
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
    assert result.triage.spam.decision == "not_scored"
    assert result.triage.duplicate_review.decision == "not_indexed"


def test_legacy_persisted_duplicate_signal_gets_the_matched_review_state():
    triage = TriageResult.model_validate(
        {
            "duplicate": {
                "duplicate_kind": "campaign",
                "duplicate_group_id": "g-campaign",
                "related_filings": 18,
            },
            "spam": {"decision": "not_scored"},
        }
    )

    assert triage.duplicate is not None
    assert triage.duplicate_review.decision == "matched"


def test_mock_contract_exercises_every_advisory_state():
    states: set[str] = set()
    for i in range(100):
        triage = _mock_triage(f"synthetic grievance {i}")
        if triage.duplicate is not None:
            states.add(triage.duplicate.duplicate_kind)
        else:
            states.add(triage.spam.decision)

    assert states == {"resubmission", "campaign", "flagged", "abstained"}


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
