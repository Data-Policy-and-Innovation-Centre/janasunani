import assert from "node:assert/strict";
import test from "node:test";

const {
  parseSupervisorDashboard,
  unavailableSupervisorDashboard,
} = await import("../lib/supervisor.ts");

test("the initial supervisor response fails closed for every unpublished panel", () => {
  const dashboard = unavailableSupervisorDashboard(
    "No validated aggregate artifact is available.",
  );

  assert.equal(dashboard.workload.provenance.state, "unavailable");
  assert.equal(dashboard.spike.provenance.state, "unavailable");
  assert.equal(dashboard.closure.provenance.state, "unavailable");
  assert.match(dashboard.workload.requirement, /total filings/i);
  assert.match(dashboard.spike.requirement, /same period last year/i);
  assert.match(dashboard.closure.caveat, /not a failure rate/i);

  const serialized = JSON.stringify(dashboard).toLowerCase();
  assert.equal(serialized.includes('"value"'), false);
});

test("the browser accepts only the aggregate-only unavailable DTO shape", () => {
  const dashboard = unavailableSupervisorDashboard(
    "No validated aggregate artifact is available.",
  );

  const parsed = parseSupervisorDashboard(JSON.parse(JSON.stringify(dashboard)));

  assert.deepEqual(parsed, dashboard);
});

test("the browser rejects a response carrying a row-level field", () => {
  const dashboard = JSON.parse(
    JSON.stringify(
      unavailableSupervisorDashboard(
        "No validated aggregate artifact is available.",
      ),
    ),
  );
  dashboard.closure.grievance = "synthetic-only-content";

  assert.throws(
    () => parseSupervisorDashboard(dashboard),
    /aggregate-only contract/i,
  );
});
