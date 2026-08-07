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

from pydantic import BaseModel, ConfigDict, Field


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


class RoutingResult(BaseModel):
    dept: str
    office: str
    designation: Optional[str] = None
    escalation_authority: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    method: Literal["rules", "learned", "fallback", "mock"]


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
