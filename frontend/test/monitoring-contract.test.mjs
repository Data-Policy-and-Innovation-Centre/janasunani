import assert from "node:assert/strict";
import test from "node:test";

const { PANEL_IDS, childChoice, sortableColumn, recordingState, parseMonitoringCatalog, parseMonitoringDashboard, parentScopeFor, publishedScopes, quickScopes, scopesForView, subtypesFor } = await import("../lib/monitoring.ts");

const catalog = {
  schemaVersion: 1,
  sourceFreshness: { extractMaximum: "2025-07-30" },
  periods: [{ id: "fy-2024-25", label: "FY 2024-25", start: "2024-07-01", endExclusive: "2025-07-01" }],
  scopes: [
    { id: "department-21", label: "Panchayati Raj", kind: "department", parentId: null, definition: "Department 21.", quickView: true, availablePeriods: ["fy-2024-25"] },
    { id: "department-22", label: "Another department", kind: "department", parentId: null, definition: "Not yet published.", quickView: false, availablePeriods: [] },
    { id: "handling-collector", label: "Collector", kind: "handling_office", parentId: null, definition: "Any Collector step.", quickView: true, availablePeriods: ["fy-2024-25"] },
    { id: "handling-collector-puri", label: "Puri", kind: "handling_office_subtype", parentId: "handling-collector", definition: "Collector, Puri.", quickView: false, availablePeriods: ["fy-2024-25"] },
  ],
};

const panels = PANEL_IDS.map((id) => ({
  id, title: id, state: "recorded",
  denominator: { label: "Synthetic denominator", value: 20 },
  metrics: [{ id: `${id}-metric`, label: "Synthetic", state: "recorded", value: 50, unit: "percent", numerator: 10, denominator: 20, coveragePct: null, note: null, basis: "direct" }],
  breakdown: null, breakdownUnavailableReason: null, tables: null, caveats: ["Synthetic fixture."],
}));

const dashboard = {
  schemaVersion: 1, generatedAt: "2025-07-30T00:00:00Z",
  sourceFreshness: { extractMaximum: "2025-07-30" }, artifact: "monitoring_dashboard_v1.json",
  scopeId: "department-21", scopeLabel: "Panchayati Raj", scopeKind: "department",
  scopeDefinition: "Department 21.", periodId: "fy-2024-25", periodLabel: "FY 2024-25",
  snapshotDate: "2025-07-30", panels,
};

test("catalog retains published and explicit unpublished selector states", () => {
  const parsed = parseMonitoringCatalog(catalog);
  assert.deepEqual(parsed.scopes[0].availablePeriods, ["fy-2024-25"]);
  assert.deepEqual(parsed.scopes[1].availablePeriods, []);
});

test("cascading selectors retain the parent, subtype, and quick-view relationships", () => {
  const parsed = parseMonitoringCatalog(catalog);
  assert.deepEqual(scopesForView(parsed, "department").map((scope) => scope.id), ["department-21", "department-22"]);
  assert.equal(parentScopeFor(parsed, "handling-collector-puri").id, "handling-collector");
  assert.deepEqual(subtypesFor(parsed, "handling-collector").map((scope) => scope.id), ["handling-collector-puri"]);
  assert.deepEqual(quickScopes(parsed).map((scope) => scope.id), ["department-21", "handling-collector"]);
});

test("the selectors offer only scopes with a published aggregate", () => {
  const parsed = parseMonitoringCatalog(catalog);
  // department-22 carries no period, so it must not reach the picker.
  assert.deepEqual(
    publishedScopes(scopesForView(parsed, "department")).map((scope) => scope.id),
    ["department-21"],
  );
  assert.deepEqual(
    publishedScopes(subtypesFor(parsed, "handling-collector")).map((scope) => scope.id),
    ["handling-collector-puri"],
  );
  assert.deepEqual(publishedScopes([]), []);
});

test("filtering the pickers does not narrow scopesForView itself", () => {
  // scopesForView answers "every scope of this kind" and other callers rely on
  // that; the published-only filter belongs at the call site, not inside it.
  const parsed = parseMonitoringCatalog(catalog);
  assert.deepEqual(scopesForView(parsed, "department").map((scope) => scope.id), [
    "department-21",
    "department-22",
  ]);
});

test("dashboard requires every governed panel", () => {
  assert.deepEqual(parseMonitoringDashboard(dashboard).panels.map((panel) => panel.id), [...PANEL_IDS]);
  assert.throws(() => parseMonitoringDashboard({ ...dashboard, panels: panels.slice(0, -1) }), /malformed/i);
  // Right count, but one governed panel replaced by a repeat.
  assert.throws(() => parseMonitoringDashboard({ ...dashboard, panels: [...panels.slice(0, -1), panels[0]] }), /malformed/i);
});

test("malformed arithmetic and row-level fields fail closed", () => {
  const malformed = structuredClone(dashboard);
  malformed.panels[0].metrics[0].value = Number.NaN;
  assert.throws(() => parseMonitoringDashboard(malformed), /malformed/i);

  const malformedNumerator = structuredClone(dashboard);
  malformedNumerator.panels[0].metrics[0].numerator = "10";
  assert.throws(() => parseMonitoringDashboard(malformedNumerator), /malformed/i);

  const malformedBreakdown = structuredClone(dashboard);
  malformedBreakdown.panels[0].breakdown = [{ label: "0-6 days", value: "10" }];
  assert.throws(() => parseMonitoringDashboard(malformedBreakdown), /malformed/i);

  assert.throws(
    () => parseMonitoringDashboard({ ...dashboard, snapshotDate: "30 July 2025" }),
    /malformed/i,
  );

  const sensitive = structuredClone(dashboard);
  sensitive.panels[0].ticketNo = "synthetic-row-id";
  assert.throws(() => parseMonitoringDashboard(sensitive), /aggregate-only/i);
});

test("unavailable metric is explicit and carries no substitute value", () => {
  const unavailable = structuredClone(dashboard);
  unavailable.panels[4].metrics = [{ id: "citizens", label: "Distinct citizens", state: "unavailable", reason: "Secure aggregate was not supplied." }];
  const parsed = parseMonitoringDashboard(unavailable);
  assert.equal(parsed.panels[4].metrics[0].state, "unavailable");
  assert.equal("value" in parsed.panels[4].metrics[0], false);
});

test("the empty child option selects the parent scope", () => {
  assert.equal(childChoice("", "department-21"), "department-21");
  assert.equal(childChoice("subcategory-21-x", "department-21"), "subcategory-21-x");
});

test("a recorded metric must say whether it is direct or a proxy", () => {
  const withoutBasis = structuredClone(dashboard);
  delete withoutBasis.panels[0].metrics[0].basis;
  assert.throws(() => parseMonitoringDashboard(withoutBasis), /malformed/);
  const unknownBasis = structuredClone(dashboard);
  unknownBasis.panels[0].metrics[0].basis = "estimated";
  assert.throws(() => parseMonitoringDashboard(unknownBasis), /malformed/);
});

test("recording states keep a withheld figure apart from a field that is not recorded", () => {
  const recorded = { id: "r", label: "R", state: "recorded", unit: "percent", numerator: 100, denominator: 100, coveragePct: null, basis: "direct" };
  assert.equal(recordingState({ ...recorded, value: 100, note: null }), "recorded");
  assert.equal(recordingState({ ...recorded, value: 99.9, note: null }), "recorded");
  assert.equal(recordingState({ ...recorded, value: 62.0, note: null }), "partial");
  // Complete coverage of only part of the field.
  assert.equal(recordingState({ ...recorded, value: 100, note: "Only the current category." }), "partial");
  assert.equal(recordingState({ id: "a", label: "A", state: "unavailable", reason: "Not recorded. Would make possible: x." }), "absent");
  assert.equal(recordingState({ id: "w", label: "W", state: "unavailable", reason: "Withheld because a cell is below 10." }), "withheld");
});

test("drill-down tables must have one cell per column", () => {
  const table = { title: "By district", columns: [{ label: "Open", unit: "grievances" }, { label: "Open 30+ days", unit: "percent" }], rows: [{ label: "Puri", values: [120, 41.5] }, { label: "Khordha", values: [15, null] }] };
  const good = structuredClone(dashboard);
  good.panels[0].tables = [table];
  assert.equal(parseMonitoringDashboard(good).panels[0].tables[0].rows[1].values[1], null);
  const ragged = structuredClone(good);
  ragged.panels[0].tables[0].rows[0].values = [120];
  assert.throws(() => parseMonitoringDashboard(ragged), /malformed/i);
  const badUnit = structuredClone(good);
  badUnit.panels[0].tables[0].columns[0].unit = "days";
  assert.throws(() => parseMonitoringDashboard(badUnit), /malformed/i);
  const overHundred = structuredClone(good);
  overHundred.panels[0].tables[0].rows[0].values = [120, 150];
  assert.throws(() => parseMonitoringDashboard(overHundred), /malformed/i);
  const fractionalCount = structuredClone(good);
  fractionalCount.panels[0].tables[0].rows[0].values = [1.5, 41.5];
  assert.throws(() => parseMonitoringDashboard(fractionalCount), /malformed/i);
  const negative = structuredClone(good);
  negative.panels[0].tables[0].rows[0].values = [-1, 2];
  assert.throws(() => parseMonitoringDashboard(negative), /malformed/i);
});

test("drill-down tables sort by workload, never by a raw rate", () => {
  assert.equal(sortableColumn({ label: "Open now", unit: "grievances" }), true);
  assert.equal(sortableColumn({ label: "Open 30+ days", unit: "percent" }), false);
});
