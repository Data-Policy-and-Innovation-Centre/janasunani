import assert from "node:assert/strict";
import test from "node:test";

const { childChoice, parseMonitoringCatalog, parseMonitoringDashboard, parentScopeFor, quickScopes, scopesForView, subtypesFor } = await import("../lib/monitoring.ts");

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

const panels = ["aging", "transfers", "journey", "atr", "demand", "closure"].map((id) => ({
  id, title: id, state: "recorded",
  denominator: { label: "Synthetic denominator", value: 20 },
  metrics: [{ id: `${id}-metric`, label: "Synthetic", state: "recorded", value: 50, unit: "percent", numerator: 10, denominator: 20, coveragePct: null, note: null }],
  breakdown: null, breakdownUnavailableReason: null, caveats: ["Synthetic fixture."],
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

test("dashboard requires all six governed panels", () => {
  assert.equal(parseMonitoringDashboard(dashboard).panels.length, 6);
  assert.throws(() => parseMonitoringDashboard({ ...dashboard, panels: panels.slice(0, 5) }), /malformed/i);
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
