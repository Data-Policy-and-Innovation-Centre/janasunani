"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchMonitoringCatalog, fetchMonitoringDashboard } from "@/lib/api";
import {
  childChoice,
  parentScopeFor,
  publishedScopes,
  quickScopes,
  scopesForView,
  subtypesFor,
  type MonitoringCatalog,
  type MonitoringDashboard as Dashboard,
  type MonitoringMetric,
  type MonitoringPanel,
} from "@/lib/monitoring";
import {
  denominatorLabel,
  metricLabel,
  panelTitle,
  rowLabel,
} from "@/lib/labels";
import { CountUp, Reveal } from "./motion";
import { Note } from "./ui";

const VIEWS = [
  ["statewide", "Statewide"],
  ["department", "Department"],
  ["entry_office", "Entry office"],
  ["handling_office", "Handling office"],
] as const;

/** Only a recorded metric carries a value and a unit. */
type RecordedMetric = Extract<MonitoringMetric, { state: "recorded" }>;

/**
 * The formatter a counting figure uses for every frame, so an intermediate
 * value is rendered in the same units as the settled one.
 */
function formatterFor(metric: RecordedMetric): (value: number) => string {
  if (metric.unit === "percent") return (value) => `${value.toFixed(1)}%`;
  if (metric.unit === "days") return (value) => `${value.toFixed(1)} d`;
  return (value) => Math.round(value).toLocaleString("en-IN");
}

function displayDate(value: string): string {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

const selectClass =
  "mt-2 w-full rounded-none border-0 border-b border-hair bg-transparent pb-1.5 text-[13.5px] text-text-dark outline-none transition-colors focus:border-maroon disabled:text-text-secondary";

const labelClass =
  "block font-mono text-[9.5px] uppercase tracking-[0.14em] text-text-secondary";

function MetricCell({
  metric,
  index,
}: {
  metric: MonitoringMetric;
  index: number;
}) {
  return (
    <div className="border-t border-hair-soft pt-3.5 first:border-t-0 sm:border-t-0 sm:border-l sm:pl-5 sm:first:border-l-0 sm:first:pl-0">
      <p className="font-mono text-[9.5px] uppercase leading-tight tracking-[0.13em] text-text-secondary">
        {metricLabel(metric.id, metric.label)}
      </p>
      {metric.state === "unavailable" ? (
        <p className="mt-2 font-display text-[22px] leading-none text-text-secondary">
          Unavailable
        </p>
      ) : (
        <CountUp
          value={metric.value}
          format={formatterFor(metric)}
          duration={900 + index * 120}
          className="figure mt-2 block text-[34px]"
        />
      )}
      {metric.state === "recorded" && metric.denominator !== null ? (
        <p className="mt-2 font-mono text-[9.5px] tracking-[0.06em] text-text-secondary">
          {metric.numerator === null
            ? "base "
            : `n=${metric.numerator.toLocaleString("en-IN")} / `}
          {metric.denominator.toLocaleString("en-IN")}
        </p>
      ) : null}
      {metric.state === "unavailable" ? (
        <p className="mt-2 text-[11.5px] leading-snug text-text-secondary">
          {metric.reason}
        </p>
      ) : null}
    </div>
  );
}

function PanelCard({ panel, index }: { panel: MonitoringPanel; index: number }) {
  if (panel.state === "unavailable") {
    return (
      <Reveal as="article" className="border-t-2 border-hair bg-panel/60 p-6">
        <h3 className="font-display text-[20px] leading-tight text-text-dark">
          {panelTitle(panel.id, panel.title)}
        </h3>
        <p className="mt-3 text-[13.5px] leading-relaxed text-text-body">
          {panel.reason}
        </p>
        <div className="mt-5">
          <Note label="Source limitation">{panel.caveats.join(" ")}</Note>
        </div>
      </Reveal>
    );
  }

  const maximum = Math.max(1, ...(panel.breakdown ?? []).map((row) => row.value));

  return (
    <Reveal as="article" className="border-t-2 border-maroon bg-surface p-6">
      <div className="flex items-start justify-between gap-4">
        <h3 className="font-display text-[20px] leading-tight text-text-dark">
          {panelTitle(panel.id, panel.title)}
        </h3>
        <span className="flex-none font-mono text-[9px] uppercase tracking-[0.14em] text-maroon-soft">
          {String(index + 1).padStart(2, "0")}
        </span>
      </div>
      <p className="mt-1.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-text-secondary">
        {denominatorLabel(panel.denominator.label)} &middot;{" "}
        {panel.denominator.value.toLocaleString("en-IN")}
      </p>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        {panel.metrics.map((metric, position) => (
          <MetricCell key={metric.id} metric={metric} index={position} />
        ))}
      </div>

      {panel.breakdown ? (
        <div
          className="mt-7 border-t border-hair-soft pt-5"
          aria-label={`${panel.title} breakdown`}
        >
          {panel.breakdown.map((row, position) => (
            <div key={row.label} className="mt-3 first:mt-0">
              <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
                <span className="text-text-body">{rowLabel(row.label)}</span>
                <strong className="font-mono text-[12px] font-medium tabular-nums text-text-dark">
                  {row.value.toLocaleString("en-IN")}
                </strong>
              </div>
              <div className="mt-1.5 h-[3px] overflow-hidden bg-card">
                <div
                  className="monitoring-bar h-full bg-maroon"
                  style={{
                    width: `${row.value === 0 ? 0 : Math.max(2, (100 * row.value) / maximum)}%`,
                    transitionDelay: `${position * 70}ms`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : panel.breakdownUnavailableReason ? (
        <p className="mt-6 border-t border-hair-soft pt-4 text-[12.5px] text-text-secondary">
          {panel.breakdownUnavailableReason}
        </p>
      ) : null}

      <div className="mt-6">
        <Note label="How to read this">{panel.caveats.join(" ")}</Note>
      </div>
    </Reveal>
  );
}

export function MonitoringDashboard() {
  const [catalog, setCatalog] = useState<MonitoringCatalog | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [view, setView] = useState("department");
  const [scopeId, setScopeId] = useState("department-21");
  const [period, setPeriod] = useState("fy-2024-25");
  const [message, setMessage] = useState(
    "Loading the validated monitoring release…",
  );

  useEffect(() => {
    fetchMonitoringCatalog()
      .then((value) => {
        setCatalog(value);
        setMessage("");
      })
      .catch(() => setMessage("Monitoring catalogue unavailable."));
  }, []);

  useEffect(() => {
    if (!catalog) return;
    fetchMonitoringDashboard(scopeId, period)
      .then((value) => {
        setDashboard(value);
        setMessage("");
      })
      .catch(() =>
        setMessage(
          "No validated aggregate has been published for this selection.",
        ),
      );
  }, [catalog, scopeId, period]);

  // Both selectors offer only scopes with a published aggregate; the rest
  // would be dead entries. See `publishedScopes`.
  const parentScopes = useMemo(
    () => (catalog ? publishedScopes(scopesForView(catalog, view)) : []),
    [catalog, view],
  );
  const parentScope = catalog ? parentScopeFor(catalog, scopeId) : undefined;
  const children =
    catalog && parentScope
      ? publishedScopes(subtypesFor(catalog, parentScope.id))
      : [];
  // A department's children are its subcategories; a handling office's are its
  // places. Same cascade, so the control is named after what it currently holds
  // rather than carrying one label that is wrong half the time.
  const childrenAreSubcategories = children[0]?.kind === "subcategory";
  const childFieldLabel = childrenAreSubcategories ? "Subcategory" : "Subtype";
  const childAllLabel = childrenAreSubcategories
    ? "All subcategories"
    : "All recorded steps";
  const quick = catalog ? quickScopes(catalog) : [];

  function chooseView(next: string) {
    setDashboard(null);
    setMessage("Loading selected aggregate view…");
    setView(next);
    const first = catalog?.scopes.find(
      (scope) => scope.kind === next && scope.availablePeriods.length,
    );
    if (first) {
      setScopeId(first.id);
      setPeriod(first.availablePeriods[0]);
    }
  }

  function chooseScope(next: string) {
    setDashboard(null);
    setMessage("Loading selected aggregate view…");
    const scope = catalog?.scopes.find((item) => item.id === next);
    setScopeId(next);
    if (scope?.availablePeriods[0]) setPeriod(scope.availablePeriods[0]);
  }

  // The first metric of each recorded panel, used as the headline figure row.
  const headline =
    dashboard?.panels
      .filter((panel) => panel.state === "recorded")
      .map((panel) => panel.metrics[0])
      .filter((metric): metric is RecordedMetric =>
        Boolean(metric && metric.state === "recorded"),
      )
      .slice(0, 4) ?? [];

  return (
    <div>
      {/* Masthead for the selected aggregate. */}
      <section className="pb-10">
        <Reveal>
          <p className="kicker">CM grievance monitoring</p>
        </Reveal>
        <div className="mt-4 flex flex-wrap items-end justify-between gap-6">
          <Reveal delay={80} className="min-w-0 flex-1">
            <h2 className="font-display text-[30px] leading-[1.1] text-text-dark">
              {dashboard?.scopeLabel ?? "Validated management view"}
            </h2>
            <p className="mt-3 max-w-[640px] text-[14px] leading-relaxed text-text-secondary">
              {dashboard?.scopeDefinition ??
                "Six governed measures. Aggregate only."}
            </p>
          </Reveal>
          {dashboard ? (
            <Reveal delay={160} className="flex-none text-right">
              <p className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-text-secondary">
                Snapshot
              </p>
              <p className="mt-1.5 font-display text-[20px] text-maroon">
                {displayDate(dashboard.snapshotDate)}
              </p>
            </Reveal>
          ) : null}
        </div>

        {headline.length ? (
          <Reveal delay={240}>
            <div className="mt-9 grid gap-6 border-y border-hair py-7 sm:grid-cols-2 lg:grid-cols-4">
              {headline.map((metric, position) => (
                <div key={metric.id}>
                  <CountUp
                    value={metric.value}
                    format={formatterFor(metric)}
                    duration={1000 + position * 140}
                    className="figure block text-[42px]"
                  />
                  <p className="mt-2 font-mono text-[9.5px] uppercase leading-tight tracking-[0.13em] text-text-secondary">
                    {metricLabel(metric.id, metric.label)}
                  </p>
                </div>
              ))}
            </div>
          </Reveal>
        ) : null}
      </section>

      {/* Filters. Sticks below the appbar as the panels scroll past. */}
      <section
        className="sticky top-16 z-[700] -mx-5 border-y border-hair-soft bg-ground/95 px-5 py-4 backdrop-blur-md sm:-mx-7 sm:px-7"
        aria-label="Monitoring filters"
      >
        <div className="grid gap-x-7 gap-y-4 md:grid-cols-4">
          <label className={labelClass}>
            Viewpoint
            <select
              value={view}
              onChange={(event) => chooseView(event.target.value)}
              className={selectClass}
            >
              {VIEWS.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className={labelClass}>
            Office / department
            <select
              value={parentScope?.id ?? scopeId}
              onChange={(event) => chooseScope(event.target.value)}
              className={selectClass}
            >
              {parentScopes.length ? (
                parentScopes.map((scope) => (
                  <option key={scope.id} value={scope.id}>
                    {scope.label}
                  </option>
                ))
              ) : (
                <option value="">Nothing published for this viewpoint</option>
              )}
            </select>
          </label>
          <label className={labelClass}>
            {childFieldLabel}
            <select
              disabled={!children.length}
              value={children.some((child) => child.id === scopeId) ? scopeId : ""}
              onChange={(event) =>
                chooseScope(childChoice(event.target.value, parentScope?.id ?? scopeId))
              }
              className={selectClass}
            >
              <option value="">
                {children.length ? childAllLabel : `No ${childFieldLabel.toLowerCase()}`}
              </option>
              {children.map((scope) => (
                <option key={scope.id} value={scope.id}>
                  {scope.label}
                </option>
              ))}
            </select>
          </label>
          <label className={labelClass}>
            Period
            <select
              value={period}
              onChange={(event) => {
                setDashboard(null);
                setMessage("Loading selected aggregate view…");
                setPeriod(event.target.value);
              }}
              className={selectClass}
            >
              {catalog?.periods.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-text-secondary">
            Quick views
          </span>
          {quick.map((scope) => {
            const selected = scope.id === scopeId;
            return (
              <button
                key={scope.id}
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  setView(scope.kind);
                  chooseScope(scope.id);
                }}
                className={`rounded-full border px-3 py-1 text-[11.5px] transition-colors duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maroon ${
                  selected
                    ? "border-maroon bg-maroon text-white"
                    : "border-hair text-text-secondary hover:border-maroon hover:text-maroon"
                }`}
              >
                {scope.label}
              </button>
            );
          })}
        </div>
      </section>

      {message ? (
        <p
          role="status"
          className="mt-10 border-l-2 border-maroon/30 py-2 pl-4 text-[13.5px] text-text-secondary"
        >
          {message}
        </p>
      ) : null}

      {dashboard ? (
        <div className="mt-10 grid gap-x-7 gap-y-9 xl:grid-cols-2">
          {dashboard.panels.map((panel, index) => (
            <PanelCard key={panel.id} panel={panel} index={index} />
          ))}
        </div>
      ) : null}

      {dashboard ? (
        <p className="mt-12 border-t border-hair pt-5 text-[11.5px] leading-relaxed text-text-secondary">
          Source: {dashboard.artifact}. Extract maximum{" "}
          {dashboard.sourceFreshness.extractMaximum}. No grievance text, ticket
          numbers, officer names, contact details, identity keys, or raw office
          codes are served.
        </p>
      ) : null}
    </div>
  );
}
