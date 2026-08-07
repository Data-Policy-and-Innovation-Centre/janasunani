/** Aggregate-only contract for the Phase 15 supervisor surface.
 *
 * The frozen grievance API has no analytics endpoint. This module therefore
 * exposes an explicit typed mock/unavailable response; a future analytics
 * adapter can produce the same shape without changing the view.
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

export interface MockProvenance {
  state: "mocked";
  label: string;
  note: string;
}

export interface UnavailableProvenance {
  state: "unavailable";
  label: string;
  reason: string;
}

export interface RecordedProvenance {
  state: "recorded";
  label: string;
  asOf: string;
  source: string;
}

export interface WorkloadPanel {
  kind: "Capability";
  title: string;
  slice: SliceLabel;
  provenance: MockProvenance;
  totalFilings: AggregateCount;
  distinctProblems: AggregateCount;
  duplicateAdjustment: AggregateCount;
}

export interface SpikePanel {
  kind: "Capability";
  title: string;
  slice: SliceLabel;
  provenance: MockProvenance;
  interpretation: string;
  counts: readonly [AggregateCount, AggregateCount, AggregateCount];
}

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
  provenance: RecordedProvenance;
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

/**
 * Contract-first fixture for UI authoring. Every value is illustrative, not
 * measured. Provenance is repeated on each numbered panel so a card cannot be
 * screenshotted without its source state.
 */
export const MOCK_SUPERVISOR_RESPONSE: SupervisorDashboard = {
  generatedLabel: "Illustrative supervisor response",
  safetyNote:
    "Aggregate counts only. This surface contains no grievance text, contact details, or citizen identifiers.",
  workload: {
    kind: "Capability",
    title: "Duplicate-adjusted workload",
    slice: {
      district: "Illustrative district",
      category: "Illustrative category",
      period: "Illustrative week",
    },
    provenance: {
      state: "mocked",
      label: "Mocked response",
      note: "The dedup backfill has not been connected to a serving endpoint. These values demonstrate the contract only.",
    },
    totalFilings: {
      label: "Total filings",
      value: 120,
      explanation: "Every filing received in the selected slice.",
    },
    distinctProblems: {
      label: "Distinct problems",
      value: 80,
      explanation: "Unique grievance clusters after duplicate grouping.",
    },
    duplicateAdjustment: {
      label: "Repeat workload",
      value: 40,
      explanation: "Filings above the distinct-problem count; not discarded cases.",
    },
  },
  spike: {
    kind: "Capability",
    title: "Worked spike decomposition",
    slice: {
      district: "Illustrative district",
      category: "Illustrative category",
      period: "Illustrative spike week",
    },
    provenance: {
      state: "mocked",
      label: "Mocked response",
      note: "This worked example is not a detected real-world spike. It shows the three-count response shape.",
    },
    interpretation:
      "Many citizens are represented across a small number of distinct problems. Review as a possible campaign-driven surge, not as duplicate noise.",
    counts: [
      {
        label: "Filings",
        value: 60,
        explanation: "How much incoming work arrived.",
      },
      {
        label: "Distinct problems",
        value: 3,
        explanation: "How many unique grievance clusters were represented.",
      },
      {
        label: "Distinct citizens",
        value: 58,
        explanation: "How many salted identity keys or campaign signatories were represented.",
      },
    ],
  },
  closure: {
    kind: "Insight",
    title: "How cases are closed",
    provenance: {
      state: "unavailable",
      label: "Recorded snapshot unavailable",
      reason:
        "The closure mart exists, but no trustworthy committed result artifact or analytics endpoint is available to this screen.",
    },
    numeratorLabel: "Closures on the bare disposal rung",
    primaryDenominatorLabel: "Closures matching one of the six disposal templates",
    secondaryDenominatorLabel: "All resolved complaints",
    caveat:
      "This is descriptive, not a failure rate. A bare disposal does not prove that no work occurred or that the closure was wrong; that claim requires human adjudication.",
  },
};
