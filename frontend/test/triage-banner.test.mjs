import assert from "node:assert/strict";
import test from "node:test";

const { classifyDuplicateDisplay,
  wasActuallyScored, SPAM_REASON_MESSAGES } = await import(
  "../lib/types.ts"
);

// -- #180: campaign badge must gate on distinct signatories, not group size --

test("classifyDuplicateDisplay withholds the Sambalpur mega-group (1 signatory, 26,203 filings)", () => {
  const state = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "GOV2024999640",
    related_filings: 26203,
    distinct_signatories: 1,
  });
  assert.deepEqual(state, { kind: "withheld" });
});

test("classifyDuplicateDisplay recognizes a genuine campaign (1,155/1,291 signatories)", () => {
  const state = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "DM2024854026",
    related_filings: 1291,
    distinct_signatories: 1155,
  });
  assert.deepEqual(state, {
    kind: "campaign",
    relatedFilings: 1291,
    distinctSignatories: 1155,
  });
});

test("classifyDuplicateDisplay recognizes the other two backfill campaigns", () => {
  const a = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "DM2024947088",
    related_filings: 1190,
    distinct_signatories: 1079,
  });
  assert.equal(a.kind, "campaign");

  const b = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "DM2024577146",
    related_filings: 617,
    distinct_signatories: 579,
  });
  assert.equal(b.kind, "campaign");
});

test("classifyDuplicateDisplay withholds a campaign-shaped group with no signatory count at all", () => {
  // This is today's real state: no provider populates distinct_signatories
  // yet (#109). A bare duplicate_kind === "campaign" must not render the
  // badge just because a count is missing.
  const state = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "GOV2024999640",
    related_filings: 26203,
  });
  assert.deepEqual(state, { kind: "withheld" });
});

test("classifyDuplicateDisplay withholds a single filer resubmitting twice from a tiny group", () => {
  // 1 signatory / 2 filings clears the 50% ratio but not the minimum
  // signatory count -- must not read as a campaign.
  const state = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "EDGE0000001",
    related_filings: 2,
    distinct_signatories: 1,
  });
  assert.deepEqual(state, { kind: "withheld" });
});

test("classifyDuplicateDisplay withholds a group below the signatory ratio threshold", () => {
  const state = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "EDGE0000002",
    related_filings: 1000,
    distinct_signatories: 10,
  });
  assert.deepEqual(state, { kind: "withheld" });
});

test("classifyDuplicateDisplay renders a resubmission with its ticket", () => {
  const state = classifyDuplicateDisplay({
    duplicate_kind: "resubmission",
    duplicate_group_id: "RESUB001",
    duplicate_ticket_no: "JS0000001ABC123DEF456",
  });
  assert.deepEqual(state, {
    kind: "resubmission",
    ticketNo: "JS0000001ABC123DEF456",
  });
});

test("classifyDuplicateDisplay is 'none' for an absent duplicate signal", () => {
  assert.deepEqual(classifyDuplicateDisplay(null), { kind: "none" });
  assert.deepEqual(classifyDuplicateDisplay(undefined), { kind: "none" });
});

// -- #211: render the scored triage fields, advisory framing only --

test("SPAM_REASON_MESSAGES covers every bounded-scorer reason code", () => {
  const boundedCodes = [
    "low_signal_details_inadequate",
    "low_signal_no_grievance",
    "repetition_collapse",
    "length_too_short",
    "clean",
  ];
  for (const code of boundedCodes) {
    assert.ok(
      typeof SPAM_REASON_MESSAGES[code] === "string" &&
        SPAM_REASON_MESSAGES[code].length > 0,
      `missing message for reason code ${code}`,
    );
  }
});

test("SPAM_REASON_MESSAGES covers the legacy/advisory reason codes too", () => {
  const legacyCodes = [
    "validated_low_signal_evidence",
    "ocr_repetition_collapse_unvalidated",
    "live_review_disabled_pending_redacted_adjudication",
    "mock_low_signal_review_unavailable",
    "advisory_provider_unavailable",
  ];
  for (const code of legacyCodes) {
    assert.ok(typeof SPAM_REASON_MESSAGES[code] === "string");
  }
});

test("the 'clean' message does not assert the grievance is genuine", () => {
  const message = SPAM_REASON_MESSAGES.clean.toLowerCase();
  // A clean/low score is an abstention, not a positive claim of validity.
  assert.equal(message.includes("this is a real grievance"), false);
  assert.equal(message.includes("legitimate"), false);
  assert.equal(message.includes("valid grievance"), false);
  assert.match(message, /not confirmation|screening/);
});

// --- Codex findings on #227 ---------------------------------------------

test("a campaign the API actually emits is still displayed", () => {
  // The mock processor emits bucket-1 campaigns. Before distinct_signatories
  // reached the serving contract, the signatory guard classified every one of
  // them as withheld, removing the campaign badge from the demo flow rather
  // than rejecting an unverified group.
  const display = classifyDuplicateDisplay({
    duplicate_kind: "campaign",
    duplicate_group_id: "GRP-1",
    related_filings: 18,
    distinct_signatories: 16,
  });
  assert.equal(display.kind, "campaign");
  assert.equal(display.distinctSignatories, 16);
});

test("an unavailable screening does not report a clean score", () => {
  // Older persisted unavailable responses carried a zero score and "clean"
  // reason. The current API omits both, but the UI must keep treating the
  // legacy shape as unavailable rather than reassuring an officer.
  assert.equal(
    wasActuallyScored({
      decision: "abstained",
      reason_code: "advisory_provider_unavailable",
      spam_score: 0.0,
      spam_reason: "clean",
      method: "unavailable",
      evidence: [],
    }),
    false,
  );
});

test("a real clean score is still shown", () => {
  assert.equal(
    wasActuallyScored({
      decision: "abstained",
      reason_code: "clean",
      spam_score: 0.07,
      spam_reason: "clean",
      method: "spam-v1-bounded",
      evidence: [],
    }),
    true,
  );
});

test("the legacy disabled-review state is not treated as scored", () => {
  assert.equal(
    wasActuallyScored({
      decision: "abstained",
      reason_code: "live_review_disabled_pending_redacted_adjudication",
      spam_score: 0.0,
      spam_reason: "clean",
      method: "legacy-advisory",
      evidence: [],
    }),
    false,
  );
});
