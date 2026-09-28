// The supervisor dashboard contract (janasunani/serving/schemas.py, Dashboard*)
// and the pure helpers the panels share. Kept free of path aliases so the node
// tests can import it directly.

export interface Office {
  id: string;
  label: string;
}

export interface Year {
  /** "2024-25": filed July 2024 to June 2025. */
  id: string;
  label: string;
  fyStart: number;
}

export interface DashboardMeta {
  asOf: string;
  generatedAt: string;
  offices: Office[];
  /** Newest first. */
  years: Year[];
  defaultDisposedYear: string;
}

export type BucketId = "0-30" | "31-60" | "61+";

export interface LiveSummary {
  asOf: string;
  open: number;
  buckets: { id: BucketId; label: string; count: number }[];
}

export interface QueueItem {
  ticketNo: string;
  category: string | null;
  dept: string | null;
  daysOpen: number;
  awaitingAssignment: boolean;
}

export interface FacetCount {
  label: string;
  count: number;
}

export type AwaitingFilter = "all" | "only" | "hide";

export interface QueueFilters {
  awaiting: AwaitingFilter;
  category?: string;
  dept?: string;
}

export interface QueuePage {
  items: QueueItem[];
  total: number;
  limit: number;
  offset: number;
  /** What each filter value would leave, given the other filters chosen. */
  facets: { categories: FacetCount[]; depts: FacetCount[]; awaiting: number; notAwaiting: number };
}

/** A facet's options, keeping the chosen value listed even when the other
 * filters leave it no cases, so the select never loses what it shows. */
export function facetOptions(facets: FacetCount[], chosen?: string): FacetCount[] {
  if (!chosen || facets.some((f) => f.label === chosen)) return facets;
  return [{ label: chosen, count: 0 }, ...facets];
}

export interface TimelineStep {
  date: string;
  status: string | null;
  office: string | null;
  days: number;
  current: boolean;
}

export interface Timeline {
  ticketNo: string;
  createdOn: string;
  asOf: string;
  daysOpen: number;
  steps: TimelineStep[];
}

export type PhaseKey = "registration" | "firstAssignment" | "fieldAction" | "review" | "closure";

export interface DisposalRow {
  label: string;
  n: number;
  meanDays: number;
  phases: Record<PhaseKey, number>;
}

export interface DisposalBreakdown {
  period: string;
  rows: DisposalRow[];
}

export interface RouteBreakdown {
  period: string;
  minRouteN: number;
  /** Every case in scope; "Other routes" holds what the named routes leave. */
  total: number;
  /** The named routes in the requested order, then "Other routes" if any. */
  rows: DisposalRow[];
}

export type Order = "volume" | "slowest" | "fastest";

/** The sort choices, in the order the control shows them. */
export const ORDERS: { id: Order; label: string }[] = [
  { id: "volume", label: "Volume" },
  { id: "slowest", label: "Slowest" },
  { id: "fastest", label: "Fastest" },
];

/** The five phases in journey order, coloured as in the bottleneck notes
 * (`RAMP` in janasunani/analytics/figures.py). */
export const PHASES: { key: PhaseKey; label: string; color: string; ink: string }[] = [
  { key: "registration", label: "Registration", color: "#8B1524", ink: "#fff" },
  { key: "firstAssignment", label: "First assignment", color: "#9E3A47", ink: "#fff" },
  { key: "fieldAction", label: "Field action", color: "#B0606A", ink: "#fff" },
  { key: "review", label: "Review", color: "#C08189", ink: "#261f1c" },
  { key: "closure", label: "Closure", color: "#CE9DA3", ink: "#261f1c" },
];

export interface Segment {
  key: PhaseKey;
  label: string;
  color: string;
  ink: string;
  days: number;
  pct: number;
  /** Wide enough to carry its own label, as in the notes' charts. */
  labelled: boolean;
}

/** A row's phases as bar segments, each a share of the phases' own sum. */
export function stageSegments(phases: Record<PhaseKey, number>): Segment[] {
  const total = PHASES.reduce((sum, p) => sum + Math.max(phases[p.key], 0), 0);
  return PHASES.map((p) => {
    const days = Math.max(phases[p.key], 0);
    const pct = total > 0 ? (100 * days) / total : 0;
    return { ...p, days, pct, labelled: pct > 4 };
  });
}

/** Days as a whole number: the dashboard never shows fractions of a day. */
export function fmtDays(days: number): string {
  const rounded = Math.round(days);
  return `${rounded.toLocaleString("en-IN")} ${rounded === 1 ? "day" : "days"}`;
}

/** Bar widths for a timeline, as percentages of the longest step. A step of
 * zero days still gets a sliver, so every step stays visible. */
export function stepWidths(steps: { days: number }[]): number[] {
  const longest = Math.max(1, ...steps.map((s) => s.days));
  return steps.map((s) => Math.max(2, (100 * s.days) / longest));
}

/** A row's share of the cases in scope, as a whole percent ("<1%" for a
 * sliver, so a non-empty row never reads as 0%). */
export function sharePct(n: number, total: number): string {
  if (total <= 0) return "0%";
  const pct = (100 * n) / total;
  return n > 0 && pct < 1 ? "<1%" : `${Math.round(pct)}%`;
}

/** An ISO date as officers write it: "30 July 2025". */
export function fmtDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const months = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  return y && m && d ? `${d} ${months[m - 1]} ${y}` : iso;
}
