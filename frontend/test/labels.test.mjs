import assert from "node:assert/strict";
import test from "node:test";

const {
  availableLanguages,
  denominatorLabel,
  metricLabel,
  panelTitle,
  rowLabel,
} = await import("../lib/labels.ts");

test("the analyst's jargon is replaced with wording an officer can read", () => {
  assert.equal(
    metricLabel("tiling-coverage", "Tiling-sample coverage"),
    "Cases with usable dates",
  );
  assert.equal(
    metricLabel("loop-rate", "Deepest-office repeat arrival"),
    "Came back to the same office",
  );
  assert.equal(
    metricLabel("bare-ladder", "Bare disposal / templated closures"),
    "Closed with no action recorded",
  );
  assert.equal(panelTitle("journey", "End-to-end journey"), "How long a case takes");
  assert.equal(panelTitle("atr", "ATR queue discipline"), "Action taken reports");
});

// The artifact is generated upstream. A metric added there must still be
// readable here, so an unmapped id falls through to its own label rather than
// rendering blank.
test("an unmapped id falls back to the label the artifact supplied", () => {
  assert.equal(metricLabel("brand-new-metric", "Some Upstream Label"), "Some Upstream Label");
  assert.equal(panelTitle("brand-new-panel", "Upstream Panel"), "Upstream Panel");
  assert.equal(rowLabel("90+ days"), "90+ days");
});

test("denominator captions are rewritten without losing their date", () => {
  assert.equal(denominatorLabel("Open at 30 July 2025"), "Open cases at 30 July 2025");
  assert.equal(denominatorLabel("Grievances created in FY 2024-25"), "Cases filed in FY 2024-25");
  assert.equal(
    denominatorLabel("Disposed journeys that tile"),
    "Closed cases with usable dates",
  );
  // "cohort" is dropped, and the date it carried survives.
  assert.equal(
    denominatorLabel("All resolved in FY 2024-25 cohort"),
    "All cases closed in FY 2024-25",
  );
});

test("the journey phases read as steps, and the aging buckets are left alone", () => {
  assert.equal(rowLabel("First assignment"), "Sent to the first officer");
  assert.equal(rowLabel("Field action"), "Work in the field");
  assert.equal(rowLabel("0-6 days"), "0-6 days");
});

// A language with no wording yet must not be offered: a toggle that silently
// fell back to English would be worse than no toggle.
test("only languages with actual wording are offered", () => {
  const langs = availableLanguages();
  assert.deepEqual(langs, ["en"]);
  assert.ok(!langs.includes("or"));
});

test("an unpopulated language still renders English rather than nothing", () => {
  assert.equal(
    metricLabel("tiling-coverage", "Tiling-sample coverage", "or"),
    "Cases with usable dates",
  );
  assert.equal(denominatorLabel("Open at 30 July 2025", "or"), "Open cases at 30 July 2025");
});
