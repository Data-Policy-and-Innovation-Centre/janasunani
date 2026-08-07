import Link from "next/link";
import type { TriageResult } from "@/lib/types";
import { Badge } from "./ui";

/**
 * Review context only. These states deliberately have no dismiss/accept/reject
 * action: a triage signal must never change whether a grievance is received.
 */
export function TriageBanner({ triage }: { triage: TriageResult }) {
  const { duplicate, spam } = triage;
  const hasSpamReview = spam.decision !== "not_scored";

  if (!duplicate && !hasSpamReview) return null;

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
        {duplicate?.duplicate_kind === "resubmission" &&
          duplicate.duplicate_ticket_no && (
            <article className="rounded-sm border border-hair bg-surface px-3 py-2">
              <Badge tone="neutral">possible duplicate</Badge>
              <p className="mt-1 text-sm text-text-body">
                Possible duplicate of ticket{" "}
                <Link
                  href={`/history?q=${encodeURIComponent(duplicate.duplicate_ticket_no)}`}
                  className="font-mono font-semibold text-maroon underline underline-offset-2"
                >
                  {duplicate.duplicate_ticket_no}
                </Link>
                . Review both filings before taking any action.
              </p>
            </article>
          )}

        {duplicate?.duplicate_kind === "campaign" &&
          duplicate.related_filings && (
          <article className="rounded-sm border border-positive bg-positive/10 px-3 py-2">
            <Badge tone="positive">collective grievance</Badge>
            <h3 className="mt-1 text-sm font-semibold text-text-dark">
              Part of a campaign
            </h3>
            <p className="mt-1 text-sm text-text-body">
              {duplicate.related_filings} related filings were found. This is
              a collective grievance, not spam; each filing remains visible
              for review.
            </p>
          </article>
        )}

        {spam.decision === "flagged" && (
          <article className="rounded-sm border border-negative bg-negative/10 px-3 py-2">
            <Badge tone="negative">low-signal review</Badge>
            <h3 className="mt-1 text-sm font-semibold text-text-dark">
              Flagged low-signal, review
            </h3>
            <p className="mt-1 text-sm text-text-body">{spam.spam_reason}</p>
            <p className="mt-1 text-xs text-text-secondary">
              This flag does not reject the grievance.
            </p>
          </article>
        )}

        {spam.decision === "abstained" && (
          <article className="rounded-sm border border-hair bg-surface px-3 py-2">
            <Badge tone="neutral">low-signal review abstained</Badge>
            <p className="mt-1 text-sm text-text-body">{spam.spam_reason}</p>
            <p className="mt-1 text-xs text-text-secondary">
              No low-signal flag was assigned.
            </p>
          </article>
        )}
      </div>
    </section>
  );
}
