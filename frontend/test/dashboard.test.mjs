import assert from "node:assert/strict";
import test from "node:test";

const { ORDERS, PHASES, facetOptions, visibleStages, fmtDate, fmtDays, sharePct, stageSegments, stepWidths } = await import("../lib/dashboard.ts");

const phases = { registration: 1, firstAssignment: 3, fieldAction: 20, review: 0, closure: 5 };

test("stage segments keep journey order and are shares of the phases' sum", () => {
  const segs = stageSegments(phases);
  assert.deepEqual(segs.map((s) => s.key), PHASES.map((p) => p.key));
  assert.equal(Math.round(segs.reduce((sum, s) => sum + s.pct, 0)), 100);
  assert.equal(segs[2].pct, (100 * 20) / 29);
});

test("only segments over 4% of the bar carry a label", () => {
  const segs = stageSegments(phases);
  assert.deepEqual(segs.map((s) => s.labelled), [false, true, true, false, true]);
});

test("an all-zero row draws nothing rather than dividing by zero", () => {
  const segs = stageSegments({ registration: 0, firstAssignment: 0, fieldAction: 0, review: 0, closure: 0 });
  assert.ok(segs.every((s) => s.pct === 0 && !s.labelled));
});

test("the sort control offers volume and both speed directions", () => {
  assert.deepEqual(ORDERS.map((o) => o.id), ["volume", "slowest", "fastest"]);
});

test("days read naturally", () => {
  assert.equal(fmtDays(1), "1 day");
  assert.equal(fmtDays(24.54), "25 days");
  assert.equal(fmtDays(0.4), "0 days");
  assert.equal(fmtDays(1234), "1,234 days");
  assert.equal(fmtDays(20), "20 days");
});

test("timeline bars scale to the longest step and never vanish", () => {
  assert.deepEqual(stepWidths([{ days: 10 }, { days: 0 }, { days: 5 }]), [100, 2, 50]);
});

test("a chosen facet value stays listed even when other filters leave it none", () => {
  const facets = [{ label: "Health", count: 3 }];
  assert.deepEqual(facetOptions(facets, "Land"), [{ label: "Land", count: 0 }, ...facets]);
  assert.equal(facetOptions(facets, "Health"), facets);
  assert.equal(facetOptions(facets), facets);
});

test("shares are whole percents and a sliver never reads as zero", () => {
  assert.equal(sharePct(1, 3), "33%");
  assert.equal(sharePct(1, 500), "<1%");
  assert.equal(sharePct(0, 500), "0%");
  assert.equal(sharePct(5, 0), "0%");
});

test("dates read the way officers write them", () => {
  assert.equal(fmtDate("2025-07-30"), "30 July 2025");
  assert.equal(fmtDate("2025-01-05"), "5 January 2025");
});

test("the legend drops stages that take under a day in every bar", () => {
  const statewide = { registration: 0, firstAssignment: 11.8, fieldAction: 44.7, review: 6.2, closure: 0.4 };
  assert.deepEqual(visibleStages([statewide]).map((p) => p.key), ["firstAssignment", "fieldAction", "review"]);
  // A drill-down row that does spend time registering brings it back.
  const slowIntake = { ...statewide, registration: 3 };
  assert.deepEqual(visibleStages([statewide, slowIntake]).map((p) => p.key)[0], "registration");
});
