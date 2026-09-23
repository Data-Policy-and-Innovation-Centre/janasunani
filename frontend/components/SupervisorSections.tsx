"use client";

import { useState } from "react";
import { MonitoringDashboard } from "./MonitoringDashboard";
import { SupervisorDashboard } from "./SupervisorDashboard";

export function SupervisorSections() {
  const [tab, setTab] = useState<"monitoring" | "intelligence">("monitoring");
  return (
    <div className="flex flex-col gap-5">
      <div className="flex w-fit rounded-md border border-hair bg-panel p-1" role="tablist" aria-label="Supervisor sections">
        {(["monitoring", "intelligence"] as const).map((id) => <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)} className={`rounded px-4 py-2 text-sm font-semibold capitalize focus:outline-2 focus:outline-offset-2 focus:outline-maroon ${tab === id ? "bg-maroon text-white" : "text-text-body"}`}>{id}</button>)}
      </div>
      {tab === "monitoring" ? <MonitoringDashboard /> : <SupervisorDashboard />}
    </div>
  );
}
