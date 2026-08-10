import Link from "next/link";
import {
  SPAM_REASON_MESSAGES,
  classifyDuplicateDisplay,
  wasActuallyScored,
  type TriageResult,
} from "@/lib/types";
import { Badge } from "./ui";

/**
 * Review context only. These states deliberately have no dismiss/accept/reject
 * action: a triage signal must never change whether a grievance is received.
 */
export function TriageBanner({ triage }: { triage: TriageResult }) {
  const { duplicate, duplicate_review, spam } = triage;
  const lowSignalMessage = SPAM_REASON_MESSAGES[spam.reason_code];
  const repetitionEvidence = spam.evidence.find(
    (evidence) => evidence.kind === "repetition_collapse",
  );
  const duplicateDisplay = classifyDuplicateDisplay(duplicate);
  // A failed screening is not a clean result. `unavailable_triage()` returns
  // spam_score 0.0 and spam_reason "clean" alongside
  // reason_code "advisory_provider_unavailable", so rendering on a non-null
  // score alone would report "clean (spam_score 0.00)" for a screening that
  // never ran. Show the number only when something actually scored it.
  //
  // Advisory either way: a numeric score never determines the review outcome,
  // so the caveat travels with the value.
  const spamScoreLine = wasActuallyScored(spam) ? (
    <p className="mt-1 text-xs text-text-secondary">
      low-signal: <code>{spam.spam_reason ?? spam.reason_code}</code>{" "}
      (spam_score {spam.spam_score!.toFixed(2)}). Advisory only — it does
      not determine the review outcome.
    </p>
  ) : null;

  return (
    <section
      aria-label="Advisory triage signals"
      className="rounded-md border border-hair bg-card p-4"
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-bold text-text-dark">
          Advisory triage signals
        </h2>
        <Badge tone="neutral">review only</Badge>
      </div>
      <p className="mb-3 text-xs leading-relaxed text-text-secondary">
        These signals assist officer review. They do not block, reject, or
        remove this grievance.
      </p>

      <div className="flex flex-col gap-2">
        {duplicate_review.decision === "not_indexed" && (
          <article className="rounded-sm border border-hair bg-surface px-3 py-2">
            <Badge tone="neutral">duplicate check not indexed</Badge>
            <p className="mt-1 text-sm text-text-body">
              {duplicate_review.reason}
            </p>
            <p className="mt-1 text-xs text-text-secondary">
              This is not a finding that there are no related filings.
            </p>
          </article>
        )}

        {duplicate_review.decision === "unavailable" && (
          <article className="rounded-sm border border-hair bg-surface px-3 py-2">
            <Badge tone="neutral">duplicate check unavailable</Badge>
            <p className="mt-1 text-sm text-text-body">
              {duplicate_review.reason}
            </p>
          </article>
        )}

        {duplicate_review.decision === "abstained" && (
          <article className="rounded-sm border border-hair bg-surface px-3 py-2">
            <Badge tone="neutral">duplicate check abstained</Badge>
            <p className="mt-1 text-sm text-text-body">
              {duplicate_review.reason}
            </p>
            <p className="mt-1 text-xs text-text-secondary">
              No duplicate finding was made.
            </p>
          </article>
        )}

        {duplicate_review.decision === "no_match" && (
          <article className="rounded-sm border border-hair bg-surface px-3 py-2">
            <Badge tone="neutral">duplicate check complete</Badge>
            <p className="mt-1 text-sm text-text-body">
              No verified related filing was found in the checked index.
            </p>
          </article>
        )}

        {duplicateDisplay.kind === "resubmission" && (
          <article className="rounded-sm border border-hair bg-surface px-3 py-2">
            <Badge tone="neutral">possible duplicate</Badge>
            <p className="mt-1 text-sm text-text-body">
              Possible duplicate of ticket{" "}
              <Link
                href={`/history?q=${encodeURIComponent(duplicateDisplay.ticketNo)}`}
                className="font-mono font-semibold text-maroon underline underline-offset-2"
              >
                {duplicateDisplay.ticketNo}
              </Link>
              . Review both filings before taking any action.
            </p>
          </article>
        )}

        {duplicateDisplay.kind === "campaign" && (
          <article className="rounded-sm border border-positive bg-positive/10 px-3 py-2">
            <Badge tone="positive">collective grievance</Badge>
            <h3 className="mt-1 text-sm font-semibold text-text-dark">
              Part of a campaign
            </h3>
            <p className="mt-1 text-sm text-text-body">
              {duplicateDisplay.relatedFilings} related filings across{" "}
              {duplicateDisplay.distinctSignatories} distinct signatories
              were found. This is a collective grievance, not spam; each
              filing remains visible for review.
            </p>
          </article>
        )}

        {/* duplicateDisplay.kind === "withheld": a campaign-sized group
            without signatory evidence that clears the threshold above. Not
            rendered as campaign (would launder one filer as a movement) and
            not rendered as spam (the scorer abstains by design). What, if
            anything, this state should show is an open question — #180. */}

        {spam.decision === "review" && (
          <article className="rounded-sm border border-negative bg-negative/10 px-3 py-2">
            <Badge tone="negative">low-signal review</Badge>
            <h3 className="mt-1 text-sm font-semibold text-text-dark">
              Officer review requested
            </h3>
            <p className="mt-1 text-sm text-text-body">{lowSignalMessage}</p>
            <p className="mt-1 text-xs text-text-secondary">
              Reason code: <code>{spam.reason_code}</code>. This advisory does
              not reject the grievance.
            </p>
            {spamScoreLine}
          </article>
        )}

        {spam.decision === "abstained" && (
          <article className="rounded-sm border border-hair bg-surface px-3 py-2">
            <Badge tone="neutral">low-signal review abstained</Badge>
            <p className="mt-1 text-sm text-text-body">{lowSignalMessage}</p>
            <p className="mt-1 text-xs text-text-secondary">
              Reason code: <code>{spam.reason_code}</code>. No low-signal
              review was assigned.
            </p>
            {spamScoreLine}
            {repetitionEvidence && (
              <p className="mt-1 text-xs text-text-secondary">
                OCR repetition-collapse guard: {repetitionEvidence.observed ? "observed" : "not observed"}. No source text is shown here.
              </p>
            )}
          </article>
        )}
      </div>
    </section>
  );
}
