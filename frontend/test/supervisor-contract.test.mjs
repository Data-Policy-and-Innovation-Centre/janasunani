import assert from "node:assert/strict";
import test from "node:test";

const { MOCK_SUPERVISOR_RESPONSE } = await import("../lib/supervisor.ts");

test("the mock panels are labelled and carry all three spike counts", () => {
  assert.equal(MOCK_SUPERVISOR_RESPONSE.workload.provenance.state, "mocked");
  assert.equal(MOCK_SUPERVISOR_RESPONSE.spike.provenance.state, "mocked");
  assert.equal(
    MOCK_SUPERVISOR_RESPONSE.workload.totalFilings.value -
      MOCK_SUPERVISOR_RESPONSE.workload.distinctProblems.value,
    MOCK_SUPERVISOR_RESPONSE.workload.duplicateAdjustment.value,
  );
  assert.deepEqual(
    MOCK_SUPERVISOR_RESPONSE.spike.counts.map((count) => count.label),
    ["Filings", "Distinct problems", "Distinct citizens"],
  );
});

test("closure fails closed without a publishable snapshot", () => {
  const closure = MOCK_SUPERVISOR_RESPONSE.closure;
  assert.equal(closure.provenance.state, "unavailable");
  assert.match(closure.numeratorLabel, /bare disposal rung/i);
  assert.match(closure.primaryDenominatorLabel, /six disposal templates/i);
  assert.match(closure.secondaryDenominatorLabel, /all resolved complaints/i);
  assert.match(closure.caveat, /not a failure rate/i);
  assert.equal("value" in closure, false);
});

test("the payload is aggregate-only", () => {
  const serialized = JSON.stringify(MOCK_SUPERVISOR_RESPONSE).toLowerCase();
  for (const forbiddenKey of [
    '"grievance"',
    '"raw_text"',
    '"ticket_no"',
    '"mobile"',
    '"email"',
    '"identity_key"',
  ]) {
    assert.equal(serialized.includes(forbiddenKey), false, forbiddenKey);
  }
});
