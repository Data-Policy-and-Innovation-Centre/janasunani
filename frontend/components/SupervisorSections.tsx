"use client";

import { useState } from "react";
import { MonitoringDashboard } from "./MonitoringDashboard";
import { SupervisorDashboard } from "./SupervisorDashboard";

const TABS = [
  ["monitoring", "Queue & Routing"],
  ["intelligence", "Intelligence Briefing"],
] as const;

type TabId = (typeof TABS)[number][0];

/**
 * Chapter strip: numbered cells divided by hairlines, the active one marked by
 * a maroon rule underneath. Mirrors the section nav used across the rest of
 * the interface so a supervisor reads it as the same kind of control.
 */
export function SupervisorSections() {
  const [tab, setTab] = useState<TabId>("monitoring");

  return (
    <div>
      <div
        className="grid border-y border-hair sm:grid-cols-2"
        role="tablist"
        aria-label="Supervisor sections"
      >
        {TABS.map(([id, label], index) => {
          const active = tab === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(id)}
              className={`group relative border-hair px-5 py-4 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-maroon sm:border-l sm:first:border-l-0 ${
                active ? "bg-surface" : "hover:bg-maroon-wash/40"
              }`}
            >
              <span
                className={`block font-mono text-[9.5px] tracking-[0.14em] ${
                  active ? "text-maroon-soft" : "text-text-secondary"
                }`}
              >
                {String(index + 1).padStart(2, "0")}
              </span>
              <span
                className={`mt-1 block text-[14px] font-medium ${
                  active ? "text-maroon" : "text-text-secondary"
                }`}
              >
                {label}
              </span>
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

      <div className="pt-10">
        {tab === "monitoring" ? (
          <MonitoringDashboard />
        ) : (
          <SupervisorDashboard />
        )}
      </div>
    </div>
  );
}
