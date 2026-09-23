// Mirrors the serving contract's MonitoringScope.kind
// (janasunani/serving/schemas.py). Both subtype kinds are children reached
// through the same parent cascade rather than viewpoints of their own.
export type ScopeKind =
  | "statewide"
  | "department"
  | "entry_office"
  | "handling_office"
  | "handling_office_subtype"
  | "subcategory";

export interface MonitoringScope {
  id: string;
  label: string;
  kind: ScopeKind;
  parentId: string | null;
  definition: string;
  quickView: boolean;
  availablePeriods: string[];
}

export interface MonitoringPeriod {
  id: string;
  label: string;
  start: string;
  endExclusive: string;
}

export interface MonitoringCatalog {
  schemaVersion: 1;
  sourceFreshness: Record<string, string>;
  periods: MonitoringPeriod[];
  scopes: MonitoringScope[];
}

export type MonitoringMetric =
  | {
      id: string;
      label: string;
      state: "recorded";
      value: number;
      unit: string;
      numerator: number | null;
      denominator: number | null;
      coveragePct: number | null;
      note: string | null;
      basis: "direct" | "proxy";
    }
  | { id: string; label: string; state: "unavailable"; reason: string };

/** The governed panels, in display order. Mirrors MonitoringPanelId in
 * janasunani/serving/schemas.py; a dashboard carries each exactly once. */
export const PANEL_IDS = ["aging", "transfers", "journey", "atr", "demand", "closure", "discards", "recording"] as const;
export type PanelId = (typeof PANEL_IDS)[number];

export type MonitoringPanel = RecordedMonitoringPanel | UnavailableMonitoringPanel;

export interface RecordedMonitoringPanel {
  id: PanelId;
  title: string;
  state: "recorded";
  denominator: { label: string; value: number };
  metrics: MonitoringMetric[];
  breakdown: { label: string; value: number }[] | null;
  breakdownUnavailableReason: string | null;
  caveats: string[];
}

export interface UnavailableMonitoringPanel {
  id: PanelId;
  title: string;
  state: "unavailable";
  reason: string;
  caveats: string[];
}

export interface MonitoringDashboard {
  schemaVersion: 1;
  generatedAt: string;
  sourceFreshness: Record<string, string>;
  artifact: string;
  scopeId: string;
  scopeLabel: string;
  scopeKind: string;
  scopeDefinition: string;
  periodId: string;
  periodLabel: string;
  snapshotDate: string;
  panels: MonitoringPanel[];
}

/**
 * Whether a recording-panel field is in the source (concept note §6).
 *
 * "absent" only when the publisher says the field is not recorded; any other
 * unavailable metric was withheld (a small cell, or nothing to count) and
 * must not be shown as a gap in the record. "partial" when coverage is short
 * of complete or the publisher notes which part is missing.
 */
export type RecordingState = "recorded" | "partial" | "absent" | "withheld";

export function recordingState(metric: MonitoringMetric): RecordingState {
  if (metric.state === "unavailable") {
    return metric.reason.startsWith("Not recorded") ? "absent" : "withheld";
  }
  return metric.value >= 99.5 && metric.note === null ? "recorded" : "partial";
}

export function scopesForView(catalog: MonitoringCatalog, kind: string): MonitoringScope[] {
  return catalog.scopes.filter((scope) => scope.kind === kind);
}

export function parentScopeFor(catalog: MonitoringCatalog, scopeId: string): MonitoringScope | undefined {
  const selected = catalog.scopes.find((scope) => scope.id === scopeId);
  return selected?.parentId
    ? catalog.scopes.find((scope) => scope.id === selected.parentId)
    : selected;
}

export function subtypesFor(catalog: MonitoringCatalog, parentId: string): MonitoringScope[] {
  return catalog.scopes.filter((scope) => scope.parentId === parentId);
}

/** The scope a child selector choice means. Its empty "all" option stands
 * for the parent itself; passing "" on would request a scope that does not
 * exist and leave both selectors with nothing selected. */
export function childChoice(value: string, parentId: string): string {
  return value || parentId;
}

export function quickScopes(catalog: MonitoringCatalog): MonitoringScope[] {
  return catalog.scopes.filter((scope) => scope.quickView);
}

/**
 * Keeps only the scopes a validated aggregate has actually been published for.
 *
 * The catalogue lists every scope the release knows about, and only a handful
 * carry a period — 5 of 1,175 in the current release. Offering the rest as
 * dead entries makes the picker look broken, so the selectors filter through
 * this. It is deliberately separate from `scopesForView`/`subtypesFor`, which
 * answer "every scope of this kind" and are relied on to keep doing so.
 */
export function publishedScopes(scopes: MonitoringScope[]): MonitoringScope[] {
  return scopes.filter((scope) => scope.availablePeriods.length > 0);
}

const FORBIDDEN = new Set([
  "grievance", "ticket_no", "ticketNo", "mobile", "email", "petitioner_name",
  "action_taken_by", "identity_key", "identityKey", "signature", "hash",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function rejectSensitive(value: unknown): void {
  if (Array.isArray(value)) return value.forEach(rejectSensitive);
  if (!isRecord(value)) return;
  for (const [key, nested] of Object.entries(value)) {
    if (FORBIDDEN.has(key)) throw new Error("Monitoring response violated the aggregate-only contract.");
    rejectSensitive(nested);
  }
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 2_000;
}

function count(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function wholeCount(value: unknown): value is number {
  return count(value) && Number.isInteger(value);
}

function dateText(value: unknown): value is string {
  return text(value) && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function stringRecord(value: unknown): boolean {
  return isRecord(value) && Object.values(value).every(text);
}

function keys(value: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === expected.length && expected.every((key) => key in value);
}

export function parseMonitoringCatalog(value: unknown): MonitoringCatalog {
  rejectSensitive(value);
  if (!isRecord(value) || !keys(value, ["schemaVersion", "sourceFreshness", "periods", "scopes"]) || value.schemaVersion !== 1 || !stringRecord(value.sourceFreshness) || !Array.isArray(value.periods) || !Array.isArray(value.scopes)) {
    throw new Error("Monitoring catalogue response is malformed.");
  }
  for (const period of value.periods) {
    if (!isRecord(period) || !keys(period, ["id", "label", "start", "endExclusive"]) || !text(period.id) || !text(period.label) || !dateText(period.start) || !dateText(period.endExclusive)) throw new Error("Monitoring catalogue response is malformed.");
  }
  for (const scope of value.scopes) {
    if (!isRecord(scope) || !keys(scope, ["id", "label", "kind", "parentId", "definition", "quickView", "availablePeriods"]) || !text(scope.id) || !text(scope.label) || !text(scope.kind) || (scope.parentId !== null && !text(scope.parentId)) || !text(scope.definition) || typeof scope.quickView !== "boolean" || !Array.isArray(scope.availablePeriods) || !scope.availablePeriods.every(text)) throw new Error("Monitoring catalogue response is malformed.");
  }
  return value as unknown as MonitoringCatalog;
}

function validMetric(value: unknown): boolean {
  if (!isRecord(value) || !text(value.id) || !text(value.label) || !text(value.state)) return false;
  if (value.state === "unavailable") return keys(value, ["id", "label", "state", "reason"]) && text(value.reason);
  return value.state === "recorded" && keys(value, ["id", "label", "state", "value", "unit", "numerator", "denominator", "coveragePct", "note", "basis"]) && (value.basis === "direct" || value.basis === "proxy") && count(value.value) && text(value.unit) && (value.numerator === null || wholeCount(value.numerator)) && (value.denominator === null || wholeCount(value.denominator)) && (value.coveragePct === null || (count(value.coveragePct) && value.coveragePct <= 100)) && (value.note === null || text(value.note));
}

export function parseMonitoringDashboard(value: unknown): MonitoringDashboard {
  rejectSensitive(value);
  const top = ["schemaVersion", "generatedAt", "sourceFreshness", "artifact", "scopeId", "scopeLabel", "scopeKind", "scopeDefinition", "periodId", "periodLabel", "snapshotDate", "panels"];
  if (!isRecord(value) || !keys(value, top) || value.schemaVersion !== 1 || !text(value.generatedAt) || !stringRecord(value.sourceFreshness) || !text(value.artifact) || value.artifact.includes("/") || !text(value.scopeId) || !text(value.scopeLabel) || !text(value.scopeKind) || !text(value.scopeDefinition) || !text(value.periodId) || !text(value.periodLabel) || !dateText(value.snapshotDate) || !Array.isArray(value.panels) || value.panels.length !== PANEL_IDS.length) throw new Error("Monitoring dashboard response is malformed.");
  const ids = new Set<string>();
  for (const panel of value.panels) {
    if (!isRecord(panel) || !text(panel.id) || !text(panel.title) || !text(panel.state) || !Array.isArray(panel.caveats) || !panel.caveats.every(text)) throw new Error("Monitoring dashboard response is malformed.");
    if (panel.state === "unavailable") {
      if (!keys(panel, ["id", "title", "state", "reason", "caveats"]) || !text(panel.reason)) throw new Error("Monitoring dashboard response is malformed.");
    } else if (panel.state !== "recorded" || !keys(panel, ["id", "title", "state", "denominator", "metrics", "breakdown", "breakdownUnavailableReason", "caveats"]) || !isRecord(panel.denominator) || !keys(panel.denominator, ["label", "value"]) || !text(panel.denominator.label) || !wholeCount(panel.denominator.value) || !Array.isArray(panel.metrics) || !panel.metrics.every(validMetric) || (panel.breakdown !== null && (!Array.isArray(panel.breakdown) || !panel.breakdown.every((row) => isRecord(row) && keys(row, ["label", "value"]) && text(row.label) && count(row.value)))) || (panel.breakdownUnavailableReason !== null && !text(panel.breakdownUnavailableReason))) throw new Error("Monitoring dashboard response is malformed.");
    ids.add(panel.id);
  }
  if (PANEL_IDS.some((id) => !ids.has(id))) throw new Error("Monitoring dashboard response is malformed.");
  return value as unknown as MonitoringDashboard;
}
