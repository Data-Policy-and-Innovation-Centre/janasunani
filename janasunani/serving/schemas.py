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
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


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

    @model_validator(mode="after")
    def _kind_has_the_right_context(self) -> "DuplicateSignal":
        if self.duplicate_kind == "resubmission":
            if not self.duplicate_ticket_no:
                raise ValueError("resubmission requires duplicate_ticket_no")
            if self.related_filings is not None:
                raise ValueError("resubmission must not carry related_filings")
        elif self.duplicate_ticket_no is not None:
            raise ValueError("campaign must not carry duplicate_ticket_no")
        elif self.related_filings is None:
            raise ValueError("campaign requires related_filings")
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


class SpamReview(BaseModel):
    """Officer-review state for the optional low-signal scorer.

    ``abstained`` is intentionally observable: absence of a flag is not
    evidence that a scorer ran and found the filing actionable.
    """

    decision: Literal["flagged", "abstained", "not_scored"]
    spam_reason: Optional[str] = None
    spam_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _decision_has_an_explanation(self) -> "SpamReview":
        if self.decision in {"flagged", "abstained"} and not self.spam_reason:
            raise ValueError(f"{self.decision} spam review requires a reason")
        if self.decision == "not_scored" and (
            self.spam_reason is not None or self.spam_score is not None
        ):
            raise ValueError("not_scored spam review must not imply a result")
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
    spam: SpamReview = Field(default_factory=lambda: SpamReview(decision="not_scored"))

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
