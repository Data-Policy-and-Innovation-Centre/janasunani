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
  // Not yet emitted by any wired provider (#109/#180 — nothing populates
  // duplicate_kind at all today). Anticipated ahead of the backend contract
  // so the campaign badge can gate on distinct signatories rather than raw
  // group size once a provider is wired: see classifyDuplicateDisplay below.
  // Without it, "campaign" cannot be trusted (Sambalpur GOV2024999640 is
  // 26,203 filings behind a single identity key) and display is withheld.
  distinct_signatories?: number | null;
}

/** Officer-facing, UI-computed classification of a DuplicateSignal. Not a
 * wire shape — the point is that this is the only path TriageBanner uses to
 * decide what to render, so "campaign" is unrepresentable unless the
 * signatory evidence actually supports it. */
export type DuplicateDisplayState =
  | { kind: "none" }
  | { kind: "resubmission"; ticketNo: string }
  | { kind: "campaign"; relatedFilings: number; distinctSignatories: number }
  // Group size alone cannot support the campaign claim: no signatory count
  // was supplied, or the one supplied fails the threshold below. This is
  // the safe default, not a considered UI state — what (if anything) should
  // be shown instead for a verified single-signatory mega-group is an open
  // design question (#180) that this change deliberately does not decide.
  | { kind: "withheld" };

// The Sambalpur 2024 backfill separates the two populations by orders of
// magnitude: 1 signatory / 26,203 filings vs. 1,155 / 1,291 (and similarly
// 1,079/1,190, 579/617). A 50% signatory ratio is a wide, conservative
// margin drawn well inside that gap, not a tuned boundary — and a minimum
// of two signatories rules out a single filer resubmitting twice from
// slipping through on a small group.
const CAMPAIGN_MIN_SIGNATORIES = 2;
const CAMPAIGN_SIGNATORY_RATIO = 0.5;

/** Reason codes that mean the screening did not run, whatever score came back. */
const UNSCORED_REASON_CODES = new Set<string>([
  "advisory_provider_unavailable",
  "live_review_disabled_pending_redacted_adjudication",
]);

/**
 * Whether a spam score reflects an actual assessment.
 *
 * `unavailable_triage()` returns `spam_score: 0.0` and `spam_reason: "clean"`
 * next to `reason_code: "advisory_provider_unavailable"`, so a null check
 * alone cannot tell "screened and found clean" from "screening failed". The
 * two must never render the same way: reporting a failed screening as a clean
 * result is the kind of false reassurance an officer would act on.
 */
export function wasActuallyScored(spam: SpamReview): boolean {
  if (spam.spam_score == null) return false;
  if (spam.method === "unavailable") return false;
  return !UNSCORED_REASON_CODES.has(spam.reason_code);
}

export function classifyDuplicateDisplay(
  duplicate: DuplicateSignal | null | undefined,
): DuplicateDisplayState {
  if (!duplicate) return { kind: "none" };

  if (duplicate.duplicate_kind === "resubmission") {
    return duplicate.duplicate_ticket_no
      ? { kind: "resubmission", ticketNo: duplicate.duplicate_ticket_no }
      : { kind: "none" };
  }

  const relatedFilings = duplicate.related_filings;
  if (!relatedFilings) return { kind: "none" };

  const distinctSignatories = duplicate.distinct_signatories;
  if (
    distinctSignatories != null &&
    distinctSignatories >= CAMPAIGN_MIN_SIGNATORIES &&
    distinctSignatories / relatedFilings >= CAMPAIGN_SIGNATORY_RATIO
  ) {
    return { kind: "campaign", relatedFilings, distinctSignatories };
  }

  return { kind: "withheld" };
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

// The bounded scorer's 5-value reason set (janasunani/pipeline/spam.py,
// spam-v1-bounded) is authoritative for spam_reason. reason_code is a
// superset: it also carries the legacy/advisory states that never had a
// score attached.
export type SpamReason =
  | "low_signal_details_inadequate"
  | "low_signal_no_grievance"
  | "repetition_collapse"
  | "length_too_short"
  | "clean";

export type SpamReasonCode =
  | "validated_low_signal_evidence"
  | "ocr_repetition_collapse_unvalidated"
  | "live_review_disabled_pending_redacted_adjudication"
  | "mock_low_signal_review_unavailable"
  | "advisory_provider_unavailable"
  | SpamReason;

export interface SpamReview {
  decision: "review" | "abstained";
  reason_code: SpamReasonCode;
  // Present only on a bounded-scorer result; null/absent everywhere else
  // (legacy records, advisory-unavailable). Advisory only — never a verdict.
  spam_score?: number | null;
  spam_reason?: SpamReason | null;
  // Which scorer produced this, mirroring SpamReview.method on the wire.
  // "unavailable" means no screening ran, whatever spam_score says.
  method?: string | null;
  evidence: OcrQualityEvidence[];
}

/** Officer-facing gloss for each reason_code. Advisory framing throughout:
 * the scorer abstains by design, and a "clean" result is a screening
 * outcome, not a claim that the grievance is genuine or complete. See
 * janasunani/pipeline/spam.py for the authoritative reason set this mirrors. */
export const SPAM_REASON_MESSAGES: Record<SpamReasonCode, string> = {
  validated_low_signal_evidence:
    "Validated low-signal evidence requests an officer review.",
  ocr_repetition_collapse_unvalidated:
    "The established OCR repetition-collapse guard observed a problem, but it is not an approved low-signal review rule.",
  live_review_disabled_pending_redacted_adjudication:
    "Low-signal review is disabled pending redacted human-adjudicated validation.",
  mock_low_signal_review_unavailable:
    "The mock demo does not run low-signal review.",
  advisory_provider_unavailable:
    "The advisory provider was unavailable, so no low-signal review was assigned.",
  low_signal_details_inadequate:
    "The bounded scorer rates this submission low-signal: the details look inadequate to act on.",
  low_signal_no_grievance:
    "The bounded scorer rates this submission low-signal: it reads as generic or test-like rather than a specific grievance.",
  repetition_collapse:
    "The OCR text shows a repetition-collapse (looping/garbled) pattern, which the bounded scorer treats as low-signal.",
  length_too_short:
    "The submission is very short — too few characters or words for the bounded scorer to assess as actionable.",
  clean:
    "The bounded scorer did not flag this submission as low-signal. That is a screening result, not confirmation that the grievance is genuine or complete.",
};

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
