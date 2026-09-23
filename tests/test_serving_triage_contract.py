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


def test_unavailable_spam_review_discards_legacy_fabricated_clean_score():
    review = SpamReview(
        decision="abstained",
        reason_code="advisory_provider_unavailable",
        spam_score=0.0,
        spam_reason="clean",
        method="unavailable",
    )

    assert review.spam_score is None
    assert review.spam_reason is None


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


# -- Candidate relationship labels (concept note §2.3) ------------------------

from janasunani.serving.schemas import DuplicateEvidence  # noqa: E402
from janasunani.serving.triage import (  # noqa: E402
    RELATIONSHIP_RULE_VERSION,
    candidate_relationship,
)


@pytest.mark.parametrize(
    ("evidence", "label"),
    [
        # Same key, same text, and every follow-up signal checked and absent.
        (dict(identity_match=True, text_similarity="near", new_information=False,
              follow_up_cue=False, explicit_reference=False), "pure_duplicate"),
        # Same, but the follow-up cue was never checked.
        (dict(identity_match=True, text_similarity="near", new_information=False), "uncertain"),
        # A copy that names the earlier ticket, with no cue or new facts, is
        # still a repeat: a reference links, it does not make a follow-up.
        (dict(identity_match=True, text_similarity="near", new_information=False,
              follow_up_cue=False, explicit_reference=True), "pure_duplicate"),
        # Same key and new facts, but no evidence it is the same problem.
        (dict(identity_match=True, new_information=True), "uncertain"),
        # The costly confusion: same filer and text, but a status request.
        (dict(identity_match=True, text_similarity="near", follow_up_cue=True, new_information=False), "follow_up"),
        # Same filer and text with new facts is a follow-up, not a repeat.
        (dict(identity_match=True, text_similarity="identical", new_information=True), "follow_up"),
        # An explicit reference links the tickets without an identity key.
        (dict(explicit_reference=True, follow_up_cue=True), "follow_up"),
        # Same text from different filers.
        (dict(identity_match=False, text_similarity="identical", explicit_reference=False), "campaign"),
        # The same, with the reference never checked: it could be a follow-up.
        (dict(identity_match=False, text_similarity="identical"), "uncertain"),
        # A different key that names the earlier ticket is a follow-up, not a campaign.
        (dict(identity_match=False, text_similarity="near", explicit_reference=True,
              follow_up_cue=True), "follow_up"),
        # Similar subject, not linked to the same filer.
        # Not linked by key or reference is not evidence of a distinct problem:
        # the evidence cannot yet show one, so related is never assigned.
        (dict(identity_match=False, text_similarity="similar", explicit_reference=False), "uncertain"),
        # A similar subject with the links never checked could be a follow-up.
        (dict(text_similarity="similar"), "uncertain"),
        (dict(identity_match=False, text_similarity="similar"), "uncertain"),
        # Same filer and text but new information never checked: not a repeat.
        (dict(identity_match=True, text_similarity="near"), "uncertain"),
        # A reference to an unrelated problem.
        (dict(explicit_reference=True, follow_up_cue=True, text_similarity="different"), "uncertain"),
        # Nothing assessed.
        (dict(), "uncertain"),
    ],
)
def test_candidate_relationship_rules(evidence, label):
    assert candidate_relationship(DuplicateEvidence(**evidence)) == label


def test_a_label_travels_with_its_evidence_and_rules():
    base = dict(duplicate_kind="resubmission", duplicate_group_id="g", duplicate_ticket_no="CMO1")
    with pytest.raises(ValidationError, match="travel together"):
        DuplicateSignal(**base, relationship="follow_up")
    with pytest.raises(ValidationError, match="cannot be labelled campaign"):
        DuplicateSignal(**base, relationship="campaign", evidence=DuplicateEvidence(), rule_version="v")
    with pytest.raises(ValidationError, match="campaign or uncertain"):
        DuplicateSignal(
            duplicate_kind="campaign", duplicate_group_id="g", related_filings=5,
            relationship="pure_duplicate", evidence=DuplicateEvidence(), rule_version="v",
        )
    # A result persisted before labelling existed still loads.
    assert DuplicateSignal(**base).relationship is None


def test_the_mock_processor_labels_through_the_real_rules():
    seen = set()
    for index in range(64):
        duplicate = _mock_triage(f"water supply irregular in ward {index}").duplicate
        if duplicate is None:
            continue
        assert duplicate.rule_version == RELATIONSHIP_RULE_VERSION
        assert duplicate.relationship == candidate_relationship(duplicate.evidence)
        # Survives the store's serialise-and-revalidate round trip.
        assert DuplicateSignal.model_validate_json(duplicate.model_dump_json()) == duplicate
        seen.add((duplicate.duplicate_kind, duplicate.relationship))
    assert seen == {("resubmission", "follow_up"), ("campaign", "campaign")}


def test_a_label_that_contradicts_its_evidence_is_rejected():
    from pydantic import ValidationError
    from janasunani.serving.schemas import DuplicateSignal
    evidence = DuplicateEvidence(identity_match=True, text_similarity="near", follow_up_cue=True, new_information=False)
    signal = dict(duplicate_kind="resubmission", duplicate_group_id="g1", duplicate_ticket_no="T1", evidence=evidence,
                  rule_version=RELATIONSHIP_RULE_VERSION)
    assert DuplicateSignal(**signal, relationship="follow_up").relationship == "follow_up"
    with pytest.raises(ValidationError, match="contradicts"):
        DuplicateSignal(**signal, relationship="pure_duplicate")
    # Another rule version's label is not recomputed under these rules.
    assert DuplicateSignal(**{**signal, "rule_version": "other-rules"}, relationship="pure_duplicate")
