/** Aggregate-only DTO for the Phase 15 supervisor surface.
 *
 * The browser receives either a validated published aggregate artifact or an
 * explicit unavailable state. It never receives a UI fixture standing in for
 * the dedup backfill or a real worked spike.
 */

export interface SliceLabel {
  district: string;
  category: string;
  period: string;
}

export interface AggregateCount {
  label: string;
  value: number;
  explanation: string;
}

export interface RecordedArtifactProvenance {
  state: "recorded";
  label: string;
  artifact: string;
  artifactWrittenAt: string;
}

export interface UnavailableProvenance {
  state: "unavailable";
  label: string;
  reason: string;
}

export interface RecordedWorkloadPanel {
  kind: "Capability";
  title: string;
  slice: SliceLabel;
  provenance: RecordedArtifactProvenance;
  totalFilings: AggregateCount;
  distinctProblems: AggregateCount;
  duplicateAdjustment: AggregateCount;
}

export interface UnavailableWorkloadPanel {
  kind: "Capability";
  title: string;
  provenance: UnavailableProvenance;
  requirement: string;
}

export type WorkloadPanel = RecordedWorkloadPanel | UnavailableWorkloadPanel;

export interface RecordedSpikePanel {
  kind: "Capability";
  title: string;
  slice: SliceLabel;
  provenance: RecordedArtifactProvenance;
  interpretation: string;
  counts: readonly [AggregateCount, AggregateCount, AggregateCount];
}

export interface UnavailableSpikePanel {
  kind: "Capability";
  title: string;
  provenance: UnavailableProvenance;
  requirement: string;
}

export type SpikePanel = RecordedSpikePanel | UnavailableSpikePanel;

export interface UnavailableClosurePanel {
  kind: "Insight";
  title: string;
  provenance: UnavailableProvenance;
  numeratorLabel: string;
  primaryDenominatorLabel: string;
  secondaryDenominatorLabel: string;
  caveat: string;
}

export interface RecordedClosurePanel {
  kind: "Insight";
  title: string;
  provenance: RecordedArtifactProvenance;
  numeratorLabel: string;
  numerator: number;
  primaryDenominatorLabel: string;
  primaryDenominator: number;
  primarySharePct: number;
  secondaryDenominatorLabel: string;
  secondaryDenominator: number;
  secondarySharePct: number;
  caveat: string;
}

export type ClosurePanel = UnavailableClosurePanel | RecordedClosurePanel;

export interface SupervisorDashboard {
  generatedLabel: string;
  safetyNote: string;
  workload: WorkloadPanel;
  spike: SpikePanel;
  closure: ClosurePanel;
}

const WORKLOAD_REQUIREMENT =
  "Requires a validated aggregate release with total filings and deduplicated distinct-problem counts for the same selected slice.";
const SPIKE_REQUIREMENT =
  "Requires one validated worked spike with total filings, distinct problems, and distinct citizens or signatories for the same slice, compared with the same period last year.";
const CLOSURE_CAVEAT =
  "This is descriptive, not a failure rate. A bare disposal does not prove that no work occurred or that a closure was wrong; making that claim requires human adjudication.";

export function unavailableSupervisorDashboard(reason: string): SupervisorDashboard {
  return {
    generatedLabel: "Supervisor aggregate response unavailable",
    safetyNote:
      "Aggregate counts only. No value is rendered until a validated aggregate artifact is available; this surface never accepts grievance text, contact details, or citizen identifiers.",
    workload: {
      kind: "Capability",
      title: "Duplicate-adjusted workload",
      provenance: {
        state: "unavailable",
        label: "Metric output unavailable",
        reason,
      },
      requirement: WORKLOAD_REQUIREMENT,
    },
    spike: {
      kind: "Capability",
      title: "Worked spike decomposition",
      provenance: {
        state: "unavailable",
        label: "Metric output unavailable",
        reason,
      },
      requirement: SPIKE_REQUIREMENT,
    },
    closure: {
      kind: "Insight",
      title: "How cases are closed",
      provenance: {
        state: "unavailable",
        label: "Recorded aggregate unavailable",
        reason,
      },
      numeratorLabel: "Closures on the bare disposal rung",
      primaryDenominatorLabel:
        "Closures matching one of the six disposal templates",
      secondaryDenominatorLabel: "All resolved complaints",
      caveat: CLOSURE_CAVEAT,
    },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 2_000;
}

function isCount(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0
  );
}

function isPercentage(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 100
  );
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const actual = Object.keys(value);
  return (
    actual.length === keys.length &&
    keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
}

const FORBIDDEN_KEYS = new Set([
  "grievance",
  "rawText",
  "raw_text",
  "ticketNo",
  "ticket_no",
  "mobile",
  "email",
  "identityKey",
  "identity_key",
]);

function containsForbiddenKey(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some(containsForbiddenKey);
  }
  if (!isRecord(value)) {
    return false;
  }
  return Object.entries(value).some(
    ([key, nested]) =>
      FORBIDDEN_KEYS.has(key) || containsForbiddenKey(nested),
  );
}

function parseProvenance(
  value: unknown,
): RecordedArtifactProvenance | UnavailableProvenance | null {
  if (!isRecord(value) || !isText(value.state) || !isText(value.label)) {
    return null;
  }
  if (
    value.state === "recorded" &&
    hasExactKeys(value, [
      "state",
      "label",
      "artifact",
      "artifactWrittenAt",
    ]) &&
    isText(value.artifact) &&
    isText(value.artifactWrittenAt) &&
    !value.artifact.includes("/") &&
    !value.artifact.includes("\\") &&
    !Number.isNaN(Date.parse(value.artifactWrittenAt))
  ) {
    return {
      state: "recorded",
      label: value.label,
      artifact: value.artifact,
      artifactWrittenAt: value.artifactWrittenAt,
    };
  }
  if (
    value.state === "unavailable" &&
    hasExactKeys(value, ["state", "label", "reason"]) &&
    isText(value.reason)
  ) {
    return {
      state: "unavailable",
      label: value.label,
      reason: value.reason,
    };
  }
  return null;
}

function parseSlice(value: unknown): SliceLabel | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["district", "category", "period"]) ||
    !isText(value.district) ||
    !isText(value.category) ||
    !isText(value.period)
  ) {
    return null;
  }
  return {
    district: value.district,
    category: value.category,
    period: value.period,
  };
}

function parseCount(value: unknown): AggregateCount | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["label", "value", "explanation"]) ||
    !isText(value.label) ||
    !isCount(value.value) ||
    !isText(value.explanation)
  ) {
    return null;
  }
  return {
    label: value.label,
    value: value.value,
    explanation: value.explanation,
  };
}

function parseWorkload(value: unknown): WorkloadPanel | null {
  if (!isRecord(value) || value.kind !== "Capability" || !isText(value.title)) {
    return null;
  }
  const provenance = parseProvenance(value.provenance);
  if (!provenance) {
    return null;
  }
  if (
    provenance.state === "recorded" &&
    hasExactKeys(value, [
      "kind",
      "title",
      "slice",
      "provenance",
      "totalFilings",
      "distinctProblems",
      "duplicateAdjustment",
    ])
  ) {
    const slice = parseSlice(value.slice);
    const totalFilings = parseCount(value.totalFilings);
    const distinctProblems = parseCount(value.distinctProblems);
    const duplicateAdjustment = parseCount(value.duplicateAdjustment);
    if (!slice || !totalFilings || !distinctProblems || !duplicateAdjustment) {
      return null;
    }
    if (
      totalFilings.value - distinctProblems.value !==
      duplicateAdjustment.value
    ) {
      return null;
    }
    return {
      kind: "Capability",
      title: value.title,
      slice,
      provenance,
      totalFilings,
      distinctProblems,
      duplicateAdjustment,
    };
  }
  if (
    provenance.state === "unavailable" &&
    hasExactKeys(value, ["kind", "title", "provenance", "requirement"]) &&
    isText(value.requirement)
  ) {
    return {
      kind: "Capability",
      title: value.title,
      provenance,
      requirement: value.requirement,
    };
  }
  return null;
}

function parseSpike(value: unknown): SpikePanel | null {
  if (!isRecord(value) || value.kind !== "Capability" || !isText(value.title)) {
    return null;
  }
  const provenance = parseProvenance(value.provenance);
  if (!provenance) {
    return null;
  }
  if (
    provenance.state === "recorded" &&
    hasExactKeys(value, [
      "kind",
      "title",
      "slice",
      "provenance",
      "interpretation",
      "counts",
    ]) &&
    isText(value.interpretation) &&
    Array.isArray(value.counts) &&
    value.counts.length === 3
  ) {
    const slice = parseSlice(value.slice);
    const counts = value.counts.map(parseCount);
    if (!slice || counts.some((count) => count === null)) {
      return null;
    }
    return {
      kind: "Capability",
      title: value.title,
      slice,
      provenance,
      interpretation: value.interpretation,
      counts: counts as [
        AggregateCount,
        AggregateCount,
        AggregateCount,
      ],
    };
  }
  if (
    provenance.state === "unavailable" &&
    hasExactKeys(value, ["kind", "title", "provenance", "requirement"]) &&
    isText(value.requirement)
  ) {
    return {
      kind: "Capability",
      title: value.title,
      provenance,
      requirement: value.requirement,
    };
  }
  return null;
}

function parseClosure(value: unknown): ClosurePanel | null {
  if (!isRecord(value) || value.kind !== "Insight" || !isText(value.title)) {
    return null;
  }
  const provenance = parseProvenance(value.provenance);
  if (!provenance) {
    return null;
  }
  if (
    provenance.state === "recorded" &&
    hasExactKeys(value, [
      "kind",
      "title",
      "provenance",
      "numeratorLabel",
      "numerator",
      "primaryDenominatorLabel",
      "primaryDenominator",
      "primarySharePct",
      "secondaryDenominatorLabel",
      "secondaryDenominator",
      "secondarySharePct",
      "caveat",
    ]) &&
    isText(value.numeratorLabel) &&
    isCount(value.numerator) &&
    isText(value.primaryDenominatorLabel) &&
    isCount(value.primaryDenominator) &&
    isPercentage(value.primarySharePct) &&
    isText(value.secondaryDenominatorLabel) &&
    isCount(value.secondaryDenominator) &&
    isPercentage(value.secondarySharePct) &&
    isText(value.caveat)
  ) {
    return {
      kind: "Insight",
      title: value.title,
      provenance,
      numeratorLabel: value.numeratorLabel,
      numerator: value.numerator,
      primaryDenominatorLabel: value.primaryDenominatorLabel,
      primaryDenominator: value.primaryDenominator,
      primarySharePct: value.primarySharePct,
      secondaryDenominatorLabel: value.secondaryDenominatorLabel,
      secondaryDenominator: value.secondaryDenominator,
      secondarySharePct: value.secondarySharePct,
      caveat: value.caveat,
    };
  }
  if (
    provenance.state === "unavailable" &&
    hasExactKeys(value, [
      "kind",
      "title",
      "provenance",
      "numeratorLabel",
      "primaryDenominatorLabel",
      "secondaryDenominatorLabel",
      "caveat",
    ]) &&
    isText(value.numeratorLabel) &&
    isText(value.primaryDenominatorLabel) &&
    isText(value.secondaryDenominatorLabel) &&
    isText(value.caveat)
  ) {
    return {
      kind: "Insight",
      title: value.title,
      provenance,
      numeratorLabel: value.numeratorLabel,
      primaryDenominatorLabel: value.primaryDenominatorLabel,
      secondaryDenominatorLabel: value.secondaryDenominatorLabel,
      caveat: value.caveat,
    };
  }
  return null;
}

export function parseSupervisorDashboard(value: unknown): SupervisorDashboard {
  if (
    !isRecord(value) ||
    containsForbiddenKey(value) ||
    !hasExactKeys(value, [
      "generatedLabel",
      "safetyNote",
      "workload",
      "spike",
      "closure",
    ]) ||
    !isText(value.generatedLabel) ||
    !isText(value.safetyNote)
  ) {
    throw new Error(
      "Supervisor aggregate response did not meet the aggregate-only contract.",
    );
  }

  const workload = parseWorkload(value.workload);
  const spike = parseSpike(value.spike);
  const closure = parseClosure(value.closure);
  if (!workload || !spike || !closure) {
    throw new Error(
      "Supervisor aggregate response did not meet the aggregate-only contract.",
    );
  }

  return {
    generatedLabel: value.generatedLabel,
    safetyNote: value.safetyNote,
    workload,
    spike,
    closure,
  };
}
