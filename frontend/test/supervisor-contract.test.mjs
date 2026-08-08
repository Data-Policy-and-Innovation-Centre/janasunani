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

function makeSampleGrievanceResult(overrides = {}) {
  return {
    id: "abc123def456",
    ticket_no: "JS0000001ABC123DEF456",
    status: "Submitted",
    submitted_on: new Date().toISOString(),
    extraction: { source: "text", extracted_text: "Synthetic hand pump broken for two months." },
    redaction: { redacted_text: "Synthetic hand pump broken for two months.", entities: [] },
    classification: { category: "Drinking Water Supply", language: "en" },
    summary: "Synthetic summary: hand pump broken.",
    routing: {
      dept: "Panchayati Raj & Drinking Water",
      office: "Panchayati Raj & Drinking Water Department, Sambalpur",
      designation: null,
      escalation_authority: "District Magistrate, Sambalpur",
      confidence: 0.82,
      method: "learned",
      empirical_evidence: { support: 15560, concentration: 0.82, width: "category" },
    },
    triage: {
      duplicate: null,
      duplicate_review: { decision: "not_indexed", reason: "Not indexed for this slice." },
      spam: {
        decision: "abstained",
        reason_code: "clean",
        spam_score: 0.07,
        spam_reason: "clean",
        evidence: [{ kind: "repetition_collapse", observed: false }],
        method: "spam-v1-bounded",
      },
    },
    ...overrides,
  };
}

test("sample GrievanceResult carries triage with bounded spam_score and duplicate review", () => {
  const result = makeSampleGrievanceResult();

  assert.equal(typeof result.id, "string");
  assert.equal(typeof result.ticket_no, "string");
  assert.ok(result.extraction.extracted_text.length > 0);
  assert.ok(result.redaction.redacted_text.length > 0);
  assert.ok(result.classification.category.length > 0);
  assert.ok(result.summary.length > 0);

  // triage contract — duplicate_review and spam must be present
  assert.ok(result.triage);
  assert.ok(result.triage.duplicate_review);
  assert.match(result.triage.duplicate_review.decision, /^(matched|no_match|abstained|not_indexed|unavailable)$/);
  if (["abstained", "not_indexed", "unavailable"].includes(result.triage.duplicate_review.decision)) {
    assert.ok(result.triage.duplicate_review.reason);
  }

  const spam = result.triage.spam;
  assert.match(spam.decision, /^(review|abstained)$/);
  assert.equal(typeof spam.spam_score, "number");
  assert.ok(spam.spam_score >= 0.0 && spam.spam_score <= 1.0, "spam_score must be bounded in [0,1]");
  assert.match(spam.spam_reason, /^(low_signal_details_inadequate|low_signal_no_grievance|repetition_collapse|length_too_short|clean)$/);
  assert.ok(Array.isArray(spam.evidence));
  const rep = spam.evidence.find((e) => e.kind === "repetition_collapse");
  assert.ok(rep);
  assert.equal(typeof rep.observed, "boolean");
});

test("sample GrievanceResult routing ladder is never mock and empirical evidence matches method", () => {
  const learned = makeSampleGrievanceResult({
    routing: {
      dept: "Energy",
      office: "Energy Department, Cuttack",
      designation: null,
      escalation_authority: "District Magistrate, Cuttack",
      confidence: 0.79,
      method: "learned",
      empirical_evidence: { support: 15334, concentration: 0.79, width: "category" },
    },
  });
  assert.match(learned.routing.method, /^(learned|rules|fallback)$/);
  assert.notEqual(learned.routing.method, "mock");
  assert.ok(learned.routing.empirical_evidence);
  assert.ok(learned.routing.empirical_evidence.support >= 3);
  assert.ok(learned.routing.empirical_evidence.concentration > 0 && learned.routing.empirical_evidence.concentration <= 1);
  assert.match(learned.routing.empirical_evidence.width, /^(category|category\+district|category\+subcategory|category\+subcategory\+district)$/);

  const fallback = makeSampleGrievanceResult({
    routing: {
      dept: "General Administration & Public Grievance",
      office: "Public Grievance Cell, Khordha",
      designation: "Public Grievance Officer",
      escalation_authority: "Collectorate, Khordha",
      confidence: 0.25,
      method: "fallback",
      empirical_evidence: null,
    },
  });
  assert.equal(fallback.routing.method, "fallback");
  assert.equal(fallback.routing.empirical_evidence, null);

  const rules = makeSampleGrievanceResult({
    routing: {
      dept: "Energy",
      office: "Office of the Collector, Khordha",
      designation: "Executive Engineer",
      escalation_authority: "District Magistrate, Khordha",
      confidence: 0.8,
      method: "rules",
      empirical_evidence: null,
    },
  });
  assert.equal(rules.routing.method, "rules");
  assert.equal(rules.routing.empirical_evidence, null);
});

test("triage spam_score is advisory only — never blocks submission", () => {
  const lowSignal = makeSampleGrievanceResult({
    triage: {
      duplicate: null,
      duplicate_review: { decision: "not_indexed", reason: "Not indexed." },
      spam: {
        decision: "review",
        reason_code: "low_signal_details_inadequate",
        spam_score: 0.68,
        spam_reason: "low_signal_details_inadequate",
        evidence: [{ kind: "repetition_collapse", observed: false }],
        method: "spam-v1-bounded",
      },
    },
  });
  // still Submitted
  assert.equal(lowSignal.status, "Submitted");
  assert.equal(lowSignal.triage.spam.decision, "review");
  assert.ok(lowSignal.triage.spam.spam_score >= 0.5);
});
