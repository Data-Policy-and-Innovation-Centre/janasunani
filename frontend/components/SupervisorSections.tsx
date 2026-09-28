"use client";

import { useEffect, useRef, useState } from "react";
import { fetchDashboardMeta } from "@/lib/api";
import { fmtDate, type DashboardMeta } from "@/lib/dashboard";
import { DisposedPanel } from "./dashboard/DisposedPanel";
import { LivePanel } from "./dashboard/LivePanel";
import { Picker } from "./dashboard/Picker";
import { StatusTree } from "./dashboard/StatusTree";

type TabId = "live" | "disposed";

/**
 * The scope first (entry office and year filed), then every case in it by
 * status, then the two tabs. The scope carries through all three: the tree,
 * the open queue and the disposal times always describe the same cases.
 */
export function SupervisorSections() {
  const [tab, setTab] = useState<TabId>("live");
  const [office, setOffice] = useState("");
  const [year, setYear] = useState("");
  const tabsRef = useRef<HTMLDivElement>(null);

  // Open cases are the Live tab; disposed cases are the Disposed tab.
  const openBranch = (branch: "open" | "disposed") => {
    setTab(branch === "open" ? "live" : "disposed");
    tabsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  // Keyed on the scope, so a new office or year starts each tab afresh.
  const scopeKey = `${office}|${year}`;
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
        <div className="mb-10 space-y-8">
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
            <p className="text-[17px] text-text-secondary">
              Snapshot of <span className="font-medium text-text-dark">{fmtDate(meta.asOf)}</span>
            </p>
            <Picker label="Entry office" allLabel="Statewide" options={meta.offices} value={office} onChange={setOffice} />
            <Picker label="Year filed" allLabel="All years" options={meta.years} value={year} onChange={setYear} />
          </div>
          <StatusTree key={scopeKey} office={office} year={year} onOpen={openBranch} />
        </div>
      )}
      <div ref={tabsRef} className="grid scroll-mt-24 grid-cols-2 border-y border-hair" role="tablist" aria-label="Supervisor sections">
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
        {meta && (tab === "live" ? <LivePanel key={scopeKey} office={office} year={year} /> : <DisposedPanel key={scopeKey} office={office} year={year} />)}
      </div>
    </div>
  );
}
