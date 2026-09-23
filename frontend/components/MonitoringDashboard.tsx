"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchMonitoringCatalog, fetchMonitoringDashboard } from "@/lib/api";
import { childChoice, parentScopeFor, quickScopes, scopesForView, subtypesFor, type MonitoringCatalog, type MonitoringDashboard as Dashboard, type MonitoringMetric, type MonitoringPanel } from "@/lib/monitoring";

const VIEWS = [
  ["statewide", "Statewide"], ["department", "Department"],
  ["entry_office", "Entry office"], ["handling_office", "Handling office"],
] as const;

function display(metric: MonitoringMetric): string {
  if (metric.state === "unavailable") return "Unavailable";
  if (metric.unit === "percent") return `${metric.value.toFixed(1)}%`;
  if (metric.unit === "days") return `${metric.value.toFixed(1)} d`;
  return metric.value.toLocaleString("en-IN");
}

function displayDate(value: string): string {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

function PanelCard({ panel }: { panel: MonitoringPanel }) {
  if (panel.state === "unavailable") {
    return <article className="rounded-lg border border-hair bg-panel p-4"><h3 className="text-lg font-bold text-text-dark">{panel.title}</h3><p className="mt-3 text-sm leading-relaxed text-text-body">{panel.reason}</p><p className="mt-4 border-l-2 border-maroon pl-2 text-xs text-text-secondary"><strong>Source limitation:</strong> {panel.caveats.join(" ")}</p></article>;
  }
  const maximum = Math.max(1, ...(panel.breakdown ?? []).map((row) => row.value));
  return (
    <article className="rounded-lg border border-hair bg-surface p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-lg font-bold text-text-dark">{panel.title}</h3>
        <span className="rounded-full bg-panel px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-maroon">recorded</span>
      </div>
      <p className="mt-1 text-xs text-text-secondary">{panel.denominator.label}: <strong>{panel.denominator.value.toLocaleString("en-IN")}</strong></p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {panel.metrics.map((metric) => (
          <div key={metric.id} className="rounded-md border border-hair bg-panel p-2.5">
            <p className="text-[11px] font-semibold uppercase leading-tight tracking-wide text-text-secondary">{metric.label}</p>
            <p className={`mt-1 text-xl font-bold tabular-nums ${metric.state === "unavailable" ? "text-text-secondary" : "text-text-dark"}`}>{display(metric)}</p>
            {metric.state === "recorded" && metric.denominator !== null ? <p className="mt-0.5 text-[11px] text-text-secondary">{metric.numerator === null ? "Base: " : `n=${metric.numerator.toLocaleString("en-IN")} / `}{metric.denominator.toLocaleString("en-IN")}</p> : null}
            {metric.state === "unavailable" ? <p className="mt-1 text-[11px] leading-snug text-text-secondary">{metric.reason}</p> : null}
          </div>
        ))}
      </div>
      {panel.breakdown ? (
        <div className="mt-4 space-y-2" aria-label={`${panel.title} breakdown`}>
          {panel.breakdown.map((row) => (
            <div key={row.label}>
              <div className="flex justify-between text-xs"><span>{row.label}</span><strong className="tabular-nums">{row.value.toLocaleString("en-IN")}</strong></div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-card"><div className="monitoring-bar h-full rounded-full bg-maroon" style={{ width: `${row.value === 0 ? 0 : Math.max(2, 100 * row.value / maximum)}%` }} /></div>
            </div>
          ))}
        </div>
      ) : panel.breakdownUnavailableReason ? <p className="mt-3 text-xs text-text-secondary">{panel.breakdownUnavailableReason}</p> : null}
      <p className="mt-4 border-l-2 border-maroon pl-2 text-xs leading-relaxed text-text-secondary"><strong>How to read this:</strong> {panel.caveats.join(" ")}</p>
    </article>
  );
}

export function MonitoringDashboard() {
  const [catalog, setCatalog] = useState<MonitoringCatalog | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [view, setView] = useState("department");
  const [scopeId, setScopeId] = useState("department-21");
  const [period, setPeriod] = useState("fy-2024-25");
  const [message, setMessage] = useState("Loading the validated monitoring release…");

  useEffect(() => {
    fetchMonitoringCatalog().then((value) => { setCatalog(value); setMessage(""); }).catch(() => setMessage("Monitoring catalogue unavailable."));
  }, []);
  useEffect(() => {
    if (!catalog) return;
    fetchMonitoringDashboard(scopeId, period).then((value) => { setDashboard(value); setMessage(""); }).catch(() => setMessage("No validated aggregate has been published for this selection."));
  }, [catalog, scopeId, period]);

  const parentScopes = useMemo(() => catalog ? scopesForView(catalog, view) : [], [catalog, view]);
  const parentScope = catalog ? parentScopeFor(catalog, scopeId) : undefined;
  const children = catalog && parentScope ? subtypesFor(catalog, parentScope.id) : [];
  const quick = catalog ? quickScopes(catalog) : [];

  function chooseView(next: string) {
    setDashboard(null); setMessage("Loading selected aggregate view…");
    setView(next);
    const first = catalog?.scopes.find((scope) => scope.kind === next && scope.availablePeriods.length);
    if (first) { setScopeId(first.id); setPeriod(first.availablePeriods[0]); }
  }
  function chooseScope(next: string) {
    setDashboard(null); setMessage("Loading selected aggregate view…");
    const scope = catalog?.scopes.find((item) => item.id === next);
    setScopeId(next);
    if (scope?.availablePeriods[0]) setPeriod(scope.availablePeriods[0]);
  }

  return (
    <div className="flex flex-col gap-5">
      <section className="rounded-xl bg-[linear-gradient(135deg,#f7eee6,#f2f2f2)] p-5 ring-1 ring-hair">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-maroon">CM grievance monitoring</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
          <div><h2 className="text-2xl font-bold text-text-dark">{dashboard?.scopeLabel ?? "Validated management view"}</h2><p className="mt-1 max-w-3xl text-sm text-text-body">{dashboard?.scopeDefinition ?? "Six governed measures. Aggregate only."}</p></div>
          {dashboard ? <div className="rounded-md bg-surface/90 px-3 py-2 text-right"><p className="text-[11px] uppercase tracking-wide text-text-secondary">Snapshot</p><p className="font-bold text-text-dark">{displayDate(dashboard.snapshotDate)}</p></div> : null}
        </div>
          {dashboard ? <div className="mt-4 flex flex-wrap gap-2">{dashboard.panels.filter((panel) => panel.state === "recorded").slice(0, 3).map((panel) => <span key={panel.id} className="rounded-full border border-hair bg-surface px-3 py-1.5 text-xs"><strong>{panel.metrics[0] ? display(panel.metrics[0]) : "—"}</strong> {panel.metrics[0]?.label.toLowerCase()}</span>)}</div> : null}
      </section>

      <section className="rounded-lg border border-hair bg-panel p-4" aria-label="Monitoring filters">
        <div className="grid gap-3 md:grid-cols-4">
          <label className="text-xs font-semibold text-text-secondary">Viewpoint<select value={view} onChange={(event) => chooseView(event.target.value)} className="mt-1 w-full rounded border border-hair bg-surface p-2 text-sm text-text-dark">{VIEWS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
          <label className="text-xs font-semibold text-text-secondary">Office / department<select value={parentScope?.id ?? scopeId} onChange={(event) => chooseScope(event.target.value)} className="mt-1 w-full rounded border border-hair bg-surface p-2 text-sm text-text-dark">{parentScopes.map((scope) => <option key={scope.id} value={scope.id} disabled={!scope.availablePeriods.length}>{scope.label}{scope.availablePeriods.length ? "" : " — not published"}</option>)}</select></label>
          <label className="text-xs font-semibold text-text-secondary">Subtype<select disabled={!children.length} value={children.some((child) => child.id === scopeId) ? scopeId : ""} onChange={(event) => chooseScope(childChoice(event.target.value, parentScope?.id ?? scopeId))} className="mt-1 w-full rounded border border-hair bg-surface p-2 text-sm text-text-dark disabled:text-text-secondary"><option value="">{children.length ? "All recorded steps" : "No subtype"}</option>{children.map((scope) => <option key={scope.id} value={scope.id} disabled={!scope.availablePeriods.length}>{scope.label}</option>)}</select></label>
          <label className="text-xs font-semibold text-text-secondary">Period<select value={period} onChange={(event) => { setDashboard(null); setMessage("Loading selected aggregate view…"); setPeriod(event.target.value); }} className="mt-1 w-full rounded border border-hair bg-surface p-2 text-sm text-text-dark">{catalog?.periods.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        </div>
        <div className="mt-3 flex flex-wrap gap-2"><span className="py-1 text-xs font-semibold text-text-secondary">Quick views</span>{quick.map((scope) => {
          const selected = scope.id === scopeId;
          return <button key={scope.id} type="button" aria-pressed={selected} onClick={() => { setView(scope.kind); chooseScope(scope.id); }} className={`rounded-full border px-3 py-1 text-xs font-semibold focus:outline-2 focus:outline-offset-2 focus:outline-maroon ${selected ? "border-maroon bg-maroon text-white" : "border-hair bg-surface text-maroon hover:border-maroon"}`}>{scope.label}</button>;
        })}</div>
      </section>

      {message ? <p role="status" className="rounded-md border border-hair bg-panel p-4 text-sm text-text-secondary">{message}</p> : null}
      {dashboard ? <div className="grid gap-4 xl:grid-cols-2">{dashboard.panels.map((panel) => <PanelCard key={panel.id} panel={panel} />)}</div> : null}
      {dashboard ? <p className="text-xs text-text-secondary">Source: {dashboard.artifact}. Extract maximum {dashboard.sourceFreshness.extractMaximum}. No grievance text, ticket numbers, officer names, contact details, identity keys, or raw office codes are served.</p> : null}
    </div>
  );
}
