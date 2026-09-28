"use client";

import { useEffect, useState } from "react";
import { fetchTimeline } from "@/lib/api";
import { fmtDate, fmtDays, stepWidths, type Timeline as TimelineData } from "@/lib/dashboard";

/** A ticket's recorded actions, oldest first, each bar as long as the time the
 * case sat there. The last step is where it is waiting now. */
export function Timeline({ ticketNo }: { ticketNo: string }) {
  const [data, setData] = useState<TimelineData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchTimeline(ticketNo)
      .then((t) => !cancelled && setData(t))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [ticketNo]);

  if (error) return <p className="text-[16px] text-negative">{error}</p>;
  if (!data) return <p className="text-[16px] text-text-secondary">Loading history…</p>;
  if (data.steps.length === 0) {
    return (
      <p className="text-[16px] text-text-secondary">
        No action recorded since it was filed on {fmtDate(data.createdOn)}: {fmtDays(data.daysOpen)} waiting.
      </p>
    );
  }
  const widths = stepWidths(data.steps);
  return (
    <ol className="space-y-2.5">
      {data.steps.map((step, i) => (
        <li key={i} className="grid grid-cols-[132px_1fr] gap-3">
          <span className="pt-0.5 text-[15px] tabular-nums text-text-secondary">{fmtDate(step.date)}</span>
          <div>
            <p className="text-[16px] text-text-dark">
              {step.office ?? "Office not recorded"}
              <span className="text-text-secondary"> · {step.status ?? "status not recorded"}</span>
            </p>
            <div className="mt-1 flex items-center gap-2">
              <span
                className={`h-3 rounded-full ${step.current ? "bg-maroon animate-pulse" : "bg-greystone-light"}`}
                style={{ width: `${widths[i]}%` }}
              />
              <span className={`flex-none text-[15px] tabular-nums ${step.current ? "text-maroon" : "text-text-secondary"}`}>
                {step.current ? `waiting here · ${fmtDays(step.days)}` : fmtDays(step.days)}
              </span>
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}
