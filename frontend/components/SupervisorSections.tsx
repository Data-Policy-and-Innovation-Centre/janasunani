"use client";

import { useEffect, useState } from "react";
import { fetchDashboardMeta } from "@/lib/api";
import { fmtDate, type DashboardMeta } from "@/lib/dashboard";
import { DisposedPanel } from "./dashboard/DisposedPanel";
import { LivePanel } from "./dashboard/LivePanel";

type TabId = "live" | "disposed";

/**
 * Two tabs, divided by a hairline, the active one marked by a maroon rule
 * underneath. Live carries a blinking dot and the snapshot date it is live
 * as of: the lake is a snapshot, not a feed.
 */
export function SupervisorSections() {
  const [tab, setTab] = useState<TabId>("live");
  const [meta, setMeta] = useState<DashboardMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDashboardMeta().then(setMeta).catch((e: Error) => setError(e.message));
  }, []);

  const tabs: { id: TabId; label: React.ReactNode }[] = [
    {
      id: "live",
      label: (
        <span className="flex items-center gap-2">
          <span className="relative flex h-3.5 w-3.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-60" />
            <span className="relative inline-flex h-3.5 w-3.5 rounded-full bg-red-600" />
          </span>
          Live cases
        </span>
      ),
    },
    { id: "disposed", label: "Disposed cases" },
  ];

  return (
    <div>
      {/* The lake is a snapshot, not a feed, so the date every figure below is
          counted to leads the page rather than hiding in a tab label. */}
      {meta && (
        <p className="mb-6 text-[17px] text-text-secondary">
          Snapshot of <span className="font-medium text-text-dark">{fmtDate(meta.asOf)}</span>
        </p>
      )}
      <div className="grid grid-cols-2 border-y border-hair" role="tablist" aria-label="Supervisor sections">
        {tabs.map(({ id, label }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(id)}
              className={`relative border-l border-hair px-6 py-5 text-left font-display text-[26px] font-normal transition-colors first:border-l-0 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-maroon ${
                active ? "bg-surface text-maroon" : "text-text-secondary hover:bg-maroon-wash/40"
              }`}
            >
              {label}
              <span
                aria-hidden="true"
                className={`absolute inset-x-0 -bottom-px h-[2px] origin-center bg-maroon transition-transform duration-300 ease-[cubic-bezier(.22,1,.36,1)] ${
                  active ? "scale-x-100" : "scale-x-0"
                }`}
              />
            </button>
          );
        })}
      </div>

      <div className="pt-10" role="tabpanel">
        {error && <p className="text-[18px] text-negative">{error}</p>}
        {meta && (tab === "live" ? <LivePanel meta={meta} /> : <DisposedPanel meta={meta} />)}
      </div>
    </div>
  );
}
