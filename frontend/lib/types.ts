// TypeScript mirror of janasunani/serving/schemas.py — field names 1:1 with the
// frozen serving contract. Do not rename; wire-up must not change these shapes.

export interface PIIEntity {
  entity: string; // normalized label: NAME / PHONE / EMAIL / AADHAAR / ...
  start: number;
  end: number;
}

export interface ExtractionResult {
  source: "text" | "document";
  extracted_text: string;
  ocr_model?: string | null; // null for direct-text submissions (no OCR ran)
  pages?: number | null;
}

export interface RedactionResult {
  redacted_text: string;
  entities: PIIEntity[];
}

export interface ClassificationResult {
  category: string;
  subcategory?: string | null;
  language: string; // ISO-ish code the categorizer gate produced ("en", "or")
}

export interface RoutingResult {
  dept: string;
  office: string;
  designation?: string | null;
  escalation_authority?: string | null;
  confidence: number; // 0..1
  method: "rules" | "learned" | "fallback" | "mock";
  empirical_evidence?: EmpiricalRoutingEvidence | null;
}

export interface EmpiricalRoutingEvidence {
  support: number;
  concentration: number; // 0..1: historic destination share
  width:
    | "category+subcategory+district"
    | "category+subcategory"
    | "category+district"
    | "category";
}

export interface DuplicateSignal {
  duplicate_kind: "resubmission" | "campaign";
  duplicate_group_id: string;
  duplicate_ticket_no?: string | null;
  related_filings?: number | null;
}

export interface DuplicateReview {
  decision:
    | "matched"
    | "no_match"
    | "abstained"
    | "not_indexed"
    | "unavailable";
  reason?: string | null;
}

export interface OcrQualityEvidence {
  kind: "repetition_collapse";
  observed: boolean;
}

export interface SpamReview {
  decision: "review" | "abstained";
  reason_code:
    | "validated_low_signal_evidence"
    | "ocr_repetition_collapse_unvalidated"
    | "live_review_disabled_pending_redacted_adjudication"
    | "mock_low_signal_review_unavailable"
    | "advisory_provider_unavailable";
  evidence: OcrQualityEvidence[];
}

export interface TriageResult {
  duplicate?: DuplicateSignal | null;
  duplicate_review: DuplicateReview;
  spam: SpamReview;
}

export interface GrievanceResult {
  id: string;
  ticket_no: string;
  status: string;
  submitted_on: string; // ISO datetime
  extraction: ExtractionResult;
  redaction: RedactionResult;
  classification: ClassificationResult;
  summary: string;
  routing: RoutingResult;
  triage: TriageResult;
}

export interface HistoryItem {
  // Pydantic emits None fields, so every key is present (value-or-null), not optional.
  ticket_no: string;
  created_on: string | null;
  district: string | null;
  category: string | null;
  subcategory: string | null;
  dept: string | null;
  status: string | null;
  office: string | null;
  grievance: string | null;
}

export interface HistoryPage {
  items: HistoryItem[];
  total: number; // rows matching the filters, before pagination
  limit: number;
  offset: number;
}

export interface HealthResponse {
  status: "ok";
  processor: string; // "mock" until Phase 8/9 wire-up
}
