"""Response shapes for the serving API — THE contract (Phases 8/9/11 build
against these; the frontend is developed against exactly these models).

Field names deliberately mirror what already exists elsewhere in the repo so
the wire-up phase is a plumbing job, not a renaming exercise:

- extraction/redaction fields match the pipeline artifact DB
  (``pages.extracted_text`` / ``pages.redacted_text`` / ``pages.ocr_model``);
- PII spans match ``pii_tagger.PIISpan`` (entity/start/end over the ORIGINAL
  text, exactly what ``detect_pii_spans`` returns);
- classification matches ``documents.grievance_category`` plus the
  category/subcategory split the lake uses;
- routing matches the Phase 9 contract (category + district -> dept ->
  office/designation + escalation, with a confidence and the router that
  produced it);
- history rows are a browse-friendly subset of the lake's ``complaints``
  columns, names unchanged (``ticket_no``, ``dept``, ``created_on``, ...).

Changing a field here is an API break — coordinate with the frontend.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _camel_case(name: str) -> str:
    """Serialize new frontend-facing aggregate models in the existing TS style."""

    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class PIIEntity(BaseModel):
    """One detected PII span over the *extracted* (unredacted) text."""

    entity: str  # normalized label: NAME / PHONE / EMAIL / AADHAAR / ...
    start: int
    end: int


class ExtractionResult(BaseModel):
    source: Literal["text", "document"]
    extracted_text: str
    # None for direct-text submissions (no OCR ran)
    ocr_model: Optional[str] = None
    pages: Optional[int] = None


class RedactionResult(BaseModel):
    redacted_text: str
    entities: list[PIIEntity]


class ClassificationResult(BaseModel):
    category: str
    # the lake splits category/subcategory; the current categorizer predicts
    # only the top level, so subcategory may stay None even after wire-up
    subcategory: Optional[str] = None
    language: str  # ISO-ish code the categorizer gate produced ("en", "or")


class EmpiricalRoutingEvidence(BaseModel):
    """Aggregate evidence behind an empirically observed destination.

    The crosswalk describes where comparable cases were historically sent. It
    does not assert that those destinations produced the best outcome.
    """

    support: int = Field(ge=1)
    concentration: float = Field(ge=0.0, le=1.0)
    width: Literal[
        "category+subcategory+district",
        "category+subcategory",
        "category+district",
        "category",
    ]


class RoutingResult(BaseModel):
    dept: str
    office: str
    designation: Optional[str] = None
    escalation_authority: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    method: Literal["rules", "learned", "fallback", "mock"]
    empirical_evidence: Optional[EmpiricalRoutingEvidence] = None

    @model_validator(mode="after")
    def _empirical_evidence_matches_method(self) -> "RoutingResult":
        if self.method == "learned" and self.empirical_evidence is None:
            raise ValueError("learned routing requires empirical_evidence")
        if self.method != "learned" and self.empirical_evidence is not None:
            raise ValueError("only learned routing may carry empirical_evidence")
        return self


class DuplicateSignal(BaseModel):
    """A possible resubmission or a collective campaign, never a disposition."""

    duplicate_kind: Literal["resubmission", "campaign"]
    duplicate_group_id: str = Field(min_length=1)
    duplicate_ticket_no: Optional[str] = None
    related_filings: Optional[int] = Field(default=None, ge=2)
    #: Distinct filers behind a campaign, never the group size.
    #:
    #: A campaign badge says "this is a collective grievance, not spam", and it
    #: must never be attachable to one actor. The Sambalpur 2024 index holds a
    #: group of 26,203 filings resolving to a single identity key, against
    #: genuine campaigns at 1,155 signatories over 1,291 filings. On group size
    #: alone those are indistinguishable; on signatories they are not.
    #:
    #: Optional rather than required, deliberately. ``store.py`` re-validates
    #: persisted results on read, so making it mandatory would make every
    #: campaign recorded before this field existed unreadable. A provider that
    #: omits it is saying "I cannot evidence this", and the display guard
    #: withholds the badge for exactly that case. The contract enforces
    #: consistency when the number is present; the UI enforces what may be
    #: claimed when it is absent.
    distinct_signatories: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _kind_has_the_right_context(self) -> "DuplicateSignal":
        if self.duplicate_kind == "resubmission":
            if not self.duplicate_ticket_no:
                raise ValueError("resubmission requires duplicate_ticket_no")
            if self.related_filings is not None:
                raise ValueError("resubmission must not carry related_filings")
            if self.distinct_signatories is not None:
                raise ValueError("resubmission must not carry distinct_signatories")
        elif self.duplicate_ticket_no is not None:
            raise ValueError("campaign must not carry duplicate_ticket_no")
        elif self.related_filings is None:
            raise ValueError("campaign requires related_filings")
        elif (
            self.distinct_signatories is not None
            and self.distinct_signatories > self.related_filings
        ):
            # More signatories than filings is not a thin campaign, it is a
            # counting bug, and it would inflate the ratio the badge is gated
            # on in exactly the wrong direction.
            raise ValueError("distinct_signatories cannot exceed related_filings")
        return self


class DuplicateReview(BaseModel):
    """Availability and outcome of the duplicate/campaign lookup.

    An absent ``DuplicateSignal`` alone is ambiguous: it could mean a verified
    no-match, a short submission the matcher declined to compare, or an index
    that is unavailable.  Keep those states visible so neither an officer nor
    the frontend turns missing evidence into a negative finding.
    """

    decision: Literal[
        "matched", "no_match", "abstained", "not_indexed", "unavailable"
    ]
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _unavailable_states_explain_themselves(self) -> "DuplicateReview":
        if self.decision in {"abstained", "not_indexed", "unavailable"} and not self.reason:
            raise ValueError(f"{self.decision} duplicate review requires a reason")
        if self.decision in {"matched", "no_match"} and self.reason is not None:
            raise ValueError(f"{self.decision} duplicate review must not imply uncertainty")
        return self


class OcrQualityEvidence(BaseModel):
    """A non-content observation from the established OCR quality guard.

    This deliberately records only whether the existing repetition-collapse
    guard fired.  It never serializes source text, a feature vector, or a
    purported probability.
    """

    kind: Literal["repetition_collapse"]
    observed: bool


class SpamReview(BaseModel):
    """Officer-review state for the bounded low-signal scorer.

    The bounded scorer (pipeline/spam.py, spam-v1-bounded) emits
    ``spam_score`` in [0,1] + ``spam_reason`` in the 5-value set with
    evidence, advisory only (never blocks submission).  The provider
    remains unavailable outside a scored path: ``advisory_provider_unavailable``
    carries no score.  Legacy ``flagged``/``not_scored`` records are still
    read as an explicit abstention.
    """

    decision: Literal["review", "abstained"]
    reason_code: Literal[
        "validated_low_signal_evidence",
        "ocr_repetition_collapse_unvalidated",
        "live_review_disabled_pending_redacted_adjudication",
        "mock_low_signal_review_unavailable",
        "advisory_provider_unavailable",
        "low_signal_details_inadequate",
        "low_signal_no_grievance",
        "repetition_collapse",
        "length_too_short",
        "clean",
    ]
    spam_score: float | None = Field(default=None, ge=0.0, le=1.0)
    spam_reason: Literal[
        "low_signal_details_inadequate",
        "low_signal_no_grievance",
        "repetition_collapse",
        "length_too_short",
        "clean",
    ] | None = None
    evidence: tuple[OcrQualityEvidence, ...] = ()
    method: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _replace_legacy_score_contract(cls, value):
        """Read pre-validation persisted results without retaining their score.

        Prior demo responses could contain a mock ``flagged`` result or a
        ``not_scored`` status plus a numeric ``spam_score``.  Neither is
        defensible as a live decision, so old records become an explicit
        abstention instead of preserving a pseudo-score on the new wire shape.
        Legacy records that are not on the new bounded contract lose their
        score; new bounded records (with a new reason_code) keep theirs.
        """

        if not isinstance(value, dict):
            return value
        legacy_decision = value.get("decision")
        if legacy_decision not in {"flagged", "not_scored", "abstained"}:
            return value
        if legacy_decision == "abstained" and "reason_code" in value:
            # New contract: abstained with any valid reason_code keeps its score
            return value
        normalized = dict(value)
        normalized.update(
            decision="abstained",
            reason_code="live_review_disabled_pending_redacted_adjudication",
            evidence=(),
        )
        normalized.pop("spam_reason", None)
        normalized.pop("spam_score", None)
        normalized.pop("method", None)
        return normalized

    @model_validator(mode="after")
    def _decision_matches_the_reason_code(self) -> "SpamReview":
        # New bounded scorer: review when flagged (score >= 0.5), with any
        # of the 5 spam reasons (including repetition/length).  Clean is
        # abstained.  Keep legacy validated path as before.
        bounded_review_reasons = {
            "low_signal_details_inadequate",
            "low_signal_no_grievance",
            "repetition_collapse",
            "length_too_short",
        }
        if self.decision == "review":
            if self.reason_code == "validated_low_signal_evidence":
                if not self.evidence:
                    raise ValueError("review requires auditable low-signal evidence")
                return self
            if self.reason_code in bounded_review_reasons:
                if not self.evidence:
                    raise ValueError("review requires auditable low-signal evidence")
                # spam_score/reason coherence when present
                if self.spam_reason is not None and self.spam_reason != self.reason_code:
                    # allow reason_code to mirror spam_reason for bounded path
                    pass
                if self.spam_score is not None and not (0.0 <= self.spam_score <= 1.0):
                    raise ValueError("spam_score must be in [0,1]")
                return self
            raise ValueError("review requires validated_low_signal_evidence or a bounded spam reason")
        # abstained
        if self.reason_code == "validated_low_signal_evidence":
            raise ValueError("validated_low_signal_evidence requires review")
        if self.reason_code in bounded_review_reasons:
            raise ValueError(f"{self.reason_code} requires review (score >= 0.5)")
        # clean and advisory abstentions are allowed with or without score
        if self.spam_score is not None and not (0.0 <= self.spam_score <= 1.0):
            raise ValueError("spam_score must be in [0,1]")
        return self


class ActionabilityReview(BaseModel):
    """Additive, advisory five-class assessment over redacted text.

    It is deliberately separate from ``SpamReview``: an underspecified case,
    an irrelevant message, an out-of-scope grievance, and a policy-blocked
    grievance need different officer actions.  No value in this object changes
    whether the citizen's submission is accepted.
    """

    decision: Literal["review", "abstained"]
    predicted_label: Literal[
        "actionable", "underspecified", "irrelevant", "out_of_scope", "policy_blocked"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[
        Literal[
            "actionable",
            "underspecified",
            "irrelevant",
            "out_of_scope",
            "policy_blocked",
        ],
        float,
    ]
    method: str = Field(min_length=1)
    taxonomy_version: Literal["actionability-v1"] = "actionability-v1"

    @model_validator(mode="before")
    @classmethod
    def _reject_boolean_probabilities(cls, value):
        if isinstance(value, dict):
            if isinstance(value.get("confidence"), bool):
                raise ValueError("actionability confidence must be a finite numeric value")
            probabilities = value.get("probabilities")
            if isinstance(probabilities, dict) and any(
                isinstance(probability, bool) for probability in probabilities.values()
            ):
                raise ValueError("actionability probabilities must be finite numeric values")
        return value

    @model_validator(mode="after")
    def _validate_advisory_contract(self) -> "ActionabilityReview":
        expected = {
            "actionable", "underspecified", "irrelevant", "out_of_scope",
            "policy_blocked",
        }
        if set(self.probabilities) != expected:
            raise ValueError("probabilities must cover the actionability taxonomy")
        if any(
            isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0.0
            or value > 1.0
            for value in self.probabilities.values()
        ):
            raise ValueError("actionability probabilities must be finite and in [0,1]")
        if abs(sum(self.probabilities.values()) - 1.0) > 1e-6:
            raise ValueError("actionability probabilities must sum to one")
        if abs(self.probabilities[self.predicted_label] - self.confidence) > 1e-9:
            raise ValueError("confidence must match predicted_label probability")
        if self.decision == "review" and self.predicted_label == "actionable":
            raise ValueError("actionable predictions cannot request non-actionability review")
        return self


class TriageResult(BaseModel):
    """Advisory signals only; none changes whether a grievance is accepted."""

    duplicate: Optional[DuplicateSignal] = None
    duplicate_review: DuplicateReview = Field(
        default_factory=lambda: DuplicateReview(
            decision="not_indexed",
            reason=(
                "Duplicate matching has not been run for this submission because "
                "the live submission path is not connected to a completed index."
            ),
        )
    )
    spam: SpamReview = Field(
        default_factory=lambda: SpamReview(
            decision="abstained",
            reason_code="live_review_disabled_pending_redacted_adjudication",
        )
    )
    actionability: Optional[ActionabilityReview] = None

    @model_validator(mode="before")
    @classmethod
    def _derive_duplicate_review_for_persisted_contracts(cls, value):
        """Read pre-#163 result JSON without mistaking a prior signal for absent.

        Older persisted results carried ``duplicate`` directly, before the
        lookup state existed.  A legacy signal is still a match; legacy
        ``null`` stays explicitly not-indexed through the default above.
        """
        if not isinstance(value, dict) or "duplicate_review" in value:
            return value
        if value.get("duplicate") is None:
            return value
        derived = dict(value)
        derived["duplicate_review"] = {"decision": "matched"}
        return derived

    @model_validator(mode="after")
    def _duplicate_signal_matches_review_state(self) -> "TriageResult":
        has_signal = self.duplicate is not None
        is_match = self.duplicate_review.decision == "matched"
        if has_signal != is_match:
            raise ValueError(
                "duplicate signal must be present exactly when duplicate review is matched"
            )
        return self


class GrievanceResult(BaseModel):
    """Everything the demo shows for one submitted grievance."""

    id: str
    ticket_no: str
    status: str
    submitted_on: datetime
    extraction: ExtractionResult
    redaction: RedactionResult
    classification: ClassificationResult
    summary: str
    routing: RoutingResult
    triage: TriageResult = Field(default_factory=TriageResult)


class HistoryItem(BaseModel):
    """One historical complaint — lake column names, unchanged."""

    ticket_no: str
    created_on: Optional[datetime] = None
    district: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    dept: Optional[str] = None
    status: Optional[str] = None
    office: Optional[str] = None
    grievance: Optional[str] = None


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    total: int  # rows matching the filters, before pagination
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    processor: str  # "mock" until Phase 8/9 wire-up


# The individual-grievance models above are a frozen Phase 8-11 contract and
# retain their established field names.  The supervisor endpoint is a new,
# aggregate-only contract, so it uses camel-case aliases matching the frontend
# data-transfer object rather than making the client translate Python names.
class SupervisorResponseModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel_case, populate_by_name=True)


class SupervisorSlice(SupervisorResponseModel):
    district: str
    category: str
    period: str


class SupervisorAggregateCount(SupervisorResponseModel):
    label: str
    value: int = Field(ge=0)
    explanation: str


class RecordedArtifactProvenance(SupervisorResponseModel):
    """A validated aggregate artifact, not a claim about source-data freshness."""

    state: Literal["recorded"] = "recorded"
    label: str
    artifact: str
    artifact_written_at: datetime


class UnavailableArtifactProvenance(SupervisorResponseModel):
    state: Literal["unavailable"] = "unavailable"
    label: str
    reason: str


class RecordedWorkloadPanel(SupervisorResponseModel):
    kind: Literal["Capability"] = "Capability"
    title: str
    slice: SupervisorSlice
    provenance: RecordedArtifactProvenance
    total_filings: SupervisorAggregateCount
    distinct_problems: SupervisorAggregateCount
    duplicate_adjustment: SupervisorAggregateCount


class UnavailableWorkloadPanel(SupervisorResponseModel):
    kind: Literal["Capability"] = "Capability"
    title: str
    provenance: UnavailableArtifactProvenance
    requirement: str


class RecordedSpikePanel(SupervisorResponseModel):
    kind: Literal["Capability"] = "Capability"
    title: str
    slice: SupervisorSlice
    provenance: RecordedArtifactProvenance
    interpretation: str
    counts: tuple[
        SupervisorAggregateCount,
        SupervisorAggregateCount,
        SupervisorAggregateCount,
    ]


class UnavailableSpikePanel(SupervisorResponseModel):
    kind: Literal["Capability"] = "Capability"
    title: str
    provenance: UnavailableArtifactProvenance
    requirement: str


class RecordedClosurePanel(SupervisorResponseModel):
    kind: Literal["Insight"] = "Insight"
    title: str
    provenance: RecordedArtifactProvenance
    numerator_label: str
    numerator: int = Field(ge=0)
    primary_denominator_label: str
    primary_denominator: int = Field(ge=0)
    primary_share_pct: float = Field(ge=0, le=100)
    secondary_denominator_label: str
    secondary_denominator: int = Field(ge=0)
    secondary_share_pct: float = Field(ge=0, le=100)
    caveat: str


class UnavailableClosurePanel(SupervisorResponseModel):
    kind: Literal["Insight"] = "Insight"
    title: str
    provenance: UnavailableArtifactProvenance
    numerator_label: str
    primary_denominator_label: str
    secondary_denominator_label: str
    caveat: str


class SupervisorDashboard(SupervisorResponseModel):
    """The aggregate-only response for the supervisor briefing surface."""

    generated_label: str
    safety_note: str
    workload: RecordedWorkloadPanel | UnavailableWorkloadPanel
    spike: RecordedSpikePanel | UnavailableSpikePanel
    closure: RecordedClosurePanel | UnavailableClosurePanel
