"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { fetchMonitoringCatalog, fetchMonitoringDashboard } from "@/lib/api";
import {
  childChoice,
  flowStageGap,
  parentScopeFor,
  publishedScopes,
  quickScopes,
  recordingState,
  scopesForView,
  sortableColumn,
  subtypesFor,
  type MonitoringCatalog,
  type MonitoringDashboard as Dashboard,
  type MonitoringMetric,
  type MonitoringPanel,
  type MonitoringTable,
  type RecordedMonitoringPanel,
  type RecordingState,
} from "@/lib/monitoring";
import {
  denominatorLabel,
  metricLabel,
  panelTitle,
  rowLabel,
} from "@/lib/labels";
import { CountUp, Reveal } from "./motion";
import { Badge, Note } from "./ui";

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
    <div className="border-t border-hair-soft pt-3.5 first:border-t-0 sm:border-t-0 sm:border-l sm:pl-5 sm:odd:border-l-0 sm:odd:pl-0">
      <p className="font-mono text-[9.5px] uppercase leading-tight tracking-[0.13em] text-text-secondary">
        {metricLabel(metric.id, metric.label)}
      </p>
      {metric.state === "recorded" && metric.basis === "proxy" ? (
        <span
          className="mt-1.5 inline-block"
          title="Stands in for what the label names rather than measuring it directly."
        >
          <Badge>Proxy</Badge>
        </span>
      ) : null}
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

const RECORDING_BADGE: Record<RecordingState, { text: string; tone: "positive" | "neutral" | "negative" }> = {
  recorded: { text: "Recorded", tone: "positive" },
  partial: { text: "Partly", tone: "neutral" },
  absent: { text: "Not recorded", tone: "negative" },
  withheld: { text: "Not shown", tone: "neutral" },
};

/** Note §6 as a checklist: each field, whether the source holds it, and how
 * completely. Fields are listed recorded-first so the gaps read as one block. */
function RecordingPanel({ panel }: { panel: RecordedMonitoringPanel }) {
  const order: RecordingState[] = ["recorded", "partial", "withheld", "absent"];
  const rows = panel.metrics
    .map((metric) => ({ metric, state: recordingState(metric) }))
    .sort((a, b) => order.indexOf(a.state) - order.indexOf(b.state));
  const held = rows.filter((row) => row.state === "recorded" || row.state === "partial").length;
  return (
    <Reveal as="article" className="border-t-2 border-maroon bg-surface p-6 xl:col-span-2">
      <h3 className="font-display text-[20px] leading-tight text-text-dark">
        {panelTitle(panel.id, panel.title)}
      </h3>
      <p className="mt-1.5 max-w-[720px] text-[13.5px] leading-relaxed text-text-body">
        The source holds <strong className="text-text-dark">{held}</strong> of the{" "}
        {rows.length} fields the monitoring note asks for, some only in part.
        The fields it does not hold are why several measures on this page are
        unavailable.
      </p>
      <ul className="mt-6 grid gap-x-8 lg:grid-cols-2">
        {rows.map(({ metric, state }, position) => {
          const badge = RECORDING_BADGE[state];
          // The badge already says "Not recorded"; the text says what it costs.
          const note =
            metric.state === "recorded" ? metric.note : metric.reason.replace(/^Not recorded[.:]\s*/, "");
          return (
            <li key={metric.id} className="border-t border-hair-soft py-3.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[13.5px] text-text-dark">
                  {metricLabel(metric.id, metric.label)}
                </span>
                <span className="flex-none whitespace-nowrap">
                  <Badge tone={badge.tone}>{badge.text}</Badge>
                </span>
              </div>
              {metric.state === "recorded" ? (
                <div className="mt-2 flex items-center gap-3">
                  <div className="h-[3px] flex-1 overflow-hidden bg-card">
                    <div
                      className="monitoring-bar h-full bg-maroon"
                      style={{ width: `${Math.min(100, metric.value)}%`, transitionDelay: `${position * 40}ms` }}
                    />
                  </div>
                  <span className="font-mono text-[11px] tabular-nums text-text-dark">
                    {metric.value.toFixed(1)}%
                  </span>
                </div>
              ) : null}
              {note ? (
                <p className="mt-1.5 text-[12px] leading-snug text-text-secondary">{note}</p>
              ) : null}
            </li>
          );
        })}
      </ul>
      <div className="mt-6">
        <Note label="How to read this">{panel.caveats.join(" ")}</Note>
      </div>
    </Reveal>
  );
}

/** One drill-down table. Sortable by workload only: ordering offices by an
 * unadjusted rate is the ranking the roadmap rules out before case-mix
 * adjustment. The publisher's order is the default, and the folded "other"
 * row always stays last. */
function DrilldownTable({ table }: { table: MonitoringTable }) {
  const [sort, setSort] = useState<{ column: number; descending: boolean } | null>(null);
  const [body, other] = useMemo(() => {
    const last = table.rows.at(-1);
    const hasOther = last !== undefined && (last.label === "Other districts" || last.label === "Other roles");
    const rows = hasOther ? table.rows.slice(0, -1) : [...table.rows];
    if (sort) {
      const key = (row: MonitoringTable["rows"][number]) => row.values[sort.column] ?? -1;
      rows.sort((a, b) => (sort.descending ? key(b) - key(a) : key(a) - key(b)));
    }
    return [rows, hasOther ? last : undefined];
  }, [table, sort]);

  const cell = (value: number | null, unit: string) =>
    value === null ? (
      <span className="text-text-secondary" title="Not shown: fewer than 10 cases, or nothing to divide by">—</span>
    ) : unit === "percent" ? (
      `${value.toFixed(1)}%`
    ) : (
      value.toLocaleString("en-IN")
    );

  const row = (item: MonitoringTable["rows"][number], muted = false) => (
    <tr key={item.label} className={`border-t border-hair-soft ${muted ? "text-text-secondary" : "text-text-dark"}`}>
      <th scope="row" className="py-2 pr-4 text-left font-normal text-[13px]">{item.label}</th>
      {item.values.map((value, column) => (
        <td key={column} className="py-2 pl-4 text-right font-mono text-[12px] tabular-nums">
          {cell(value, table.columns[column].unit)}
        </td>
      ))}
    </tr>
  );

  return (
    <div className="min-w-0">
      <p className="kicker">{table.title}</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse" style={{ minWidth: 180 + 110 * table.columns.length }}>
          <thead>
            <tr>
              <th scope="col" className="pb-2 text-left font-mono text-[9.5px] font-normal uppercase tracking-[0.13em] text-text-secondary">
                <span className="sr-only">Name</span>
              </th>
              {table.columns.map((column, index) => {
                const active = sort?.column === index;
                const label = (
                  <span className={`font-mono text-[9.5px] uppercase leading-tight tracking-[0.13em] ${active ? "text-maroon" : "text-text-secondary"}`}>
                    {column.label}
                  </span>
                );
                return (
                  <th
                    key={column.label}
                    scope="col"
                    aria-sort={active ? (sort.descending ? "descending" : "ascending") : undefined}
                    className="pb-2 pl-4 text-right align-bottom"
                  >
                    {!sortableColumn(column) ? label : <button
                      type="button"
                      onClick={() => setSort({ column: index, descending: active ? !sort.descending : true })}
                      className={`font-mono text-[9.5px] uppercase leading-tight tracking-[0.13em] hover:text-maroon ${active ? "text-maroon" : "text-text-secondary"}`}
                    >
                      {column.label}
                      <span aria-hidden className="ml-1 inline-block w-2">{active ? (sort.descending ? "↓" : "↑") : ""}</span>
                    </button>}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {body.map((item) => row(item))}
            {other ? row(other, true) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OfficesPanel({ panel }: { panel: RecordedMonitoringPanel }) {
  return (
    <Reveal as="article" className="border-t-2 border-maroon bg-surface p-6 xl:col-span-2">
      <h3 className="font-display text-[20px] leading-tight text-text-dark">
        {panelTitle(panel.id, panel.title)}
      </h3>
      <p className="mt-1.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-text-secondary">
        {denominatorLabel(panel.denominator.label)} &middot;{" "}
        {panel.denominator.value.toLocaleString("en-IN")}
      </p>
      <div className="mt-6 grid gap-x-10 gap-y-8 2xl:grid-cols-2">
        {(panel.tables ?? []).map((table) => (
          <DrilldownTable key={table.title} table={table} />
        ))}
      </div>
      <div className="mt-6">
        <Note label="How to read this">{panel.caveats.join(" ")}</Note>
      </div>
    </Reveal>
  );
}

/** The band's geometry, in a 1000 x 200 box the SVG stretches to fit. The
 * band's top edge is level and it loses height from below, so what leaves at
 * each stage can peel away downwards as its own ribbon. */
const FLOW_W = 1000;
const FLOW_H = 200;
const FLOW_TOP = 12;
const FLOW_BAND = 112;

function flowGeometry(heights: number[]) {
  const n = heights.length;
  const step = FLOW_W / n;
  const left = (i: number) => step * (i + 0.5) - step * 0.2;
  const right = (i: number) => step * (i + 0.5) + step * 0.2;
  const floor = (i: number) => FLOW_TOP + heights[i];
  const k = (i: number) => (left(i) - right(i - 1)) / 2;

  let band = `M 0 ${FLOW_TOP} L ${FLOW_W} ${FLOW_TOP} L ${FLOW_W} ${floor(n - 1)} L ${left(n - 1)} ${floor(n - 1)}`;
  for (let i = n - 1; i > 0; i--) {
    band += ` C ${left(i) - k(i)} ${floor(i)} ${right(i - 1) + k(i)} ${floor(i - 1)} ${right(i - 1)} ${floor(i - 1)} L ${left(i - 1)} ${floor(i - 1)}`;
  }
  band += ` L 0 ${floor(0)} Z`;

  // What left between stage i-1 and i, as a ribbon from under the band down
  // to the bottom of the box beneath stage i, where its label sits.
  const leak = (i: number) => {
    const x0 = right(i - 1);
    const upper = floor(i);
    const lower = floor(i - 1);
    const width = Math.max(2, lower - upper);
    const exit = step * (i + 0.5);
    // Both edges follow the same curve, one ribbon-width apart, so the
    // ribbon keeps the width of what was lost all the way down.
    const bend = (exit - x0) * 0.55;
    return `M ${x0} ${upper} C ${x0 + bend} ${upper} ${exit + width / 2} ${upper + 40} ${exit + width / 2} ${FLOW_H}`
      + ` L ${exit - width / 2} ${FLOW_H} C ${exit - width / 2} ${lower + 40} ${x0 + bend - width} ${lower} ${x0} ${lower} Z`;
  };
  return { band, leak, step };
}

/**
 * The period's grievances as one pipeline. Each stage keeps what passed the
 * one before, so the band narrows by exactly what left the path there. A stage
 * the release cannot publish (repeats before dedup is validated, or a small
 * cell) carries the band through unchanged, hatched, and says why.
 */
function FlowPanel({ panel }: { panel: RecordedMonitoringPanel }) {
  const ids = useId().replace(/:/g, "");
  const stages = panel.metrics;
  const counts: number[] = [];
  stages.forEach((metric, i) => counts.push(metric.state === "recorded" ? metric.value : i ? counts[i - 1] : 0));
  const total = Math.max(1, counts[0]);
  // A floor keeps a small positive stage visible; a recorded zero draws as zero.
  const heights = counts.map((count) => (count > 0 ? Math.max(3, (count / total) * FLOW_BAND) : 0));
  const { band, leak, step } = flowGeometry(heights);
  const drop = (i: number) => {
    const metric = stages[i];
    if (i === 0) return null;
    if (metric.state === "unavailable") return { text: "Not shown", note: metric.reason };
    const lost = counts[i - 1] - counts[i];
    return lost > 0 ? { text: `−${lost.toLocaleString("en-IN")}`, note: metric.note } : null;
  };
  const share = (i: number) => (i === 0 ? "all filings" : `${((100 * counts[i]) / total).toFixed(0)}% of filed`);

  return (
    <Reveal as="article" className="border-t-2 border-maroon bg-surface p-6 xl:col-span-2">
      <h3 className="font-display text-[20px] leading-tight text-text-dark">
        {panelTitle(panel.id, panel.title)}
      </h3>
      <p className="mt-1.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-text-secondary">
        {denominatorLabel(panel.denominator.label)} &middot; {panel.denominator.value.toLocaleString("en-IN")}
      </p>

      {/* Wide screens: one band across the stages. */}
      <div className="mt-8 hidden lg:block">
        <ol className="grid" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }}>
          {stages.map((metric, i) => (
            <li key={metric.id} className="px-2 text-center">
              <p className="font-mono text-[9.5px] uppercase leading-tight tracking-[0.13em] text-text-secondary">
                {metricLabel(metric.id, metric.label)}
              </p>
              {metric.state === "recorded" ? (
                <CountUp value={metric.value} format={(v) => Math.round(v).toLocaleString("en-IN")} duration={900 + i * 110} className="figure mt-2 block text-[26px]" />
              ) : (
                <p className="mt-2 font-display text-[22px] leading-none text-text-secondary">—</p>
              )}
              <p className="mt-1 font-mono text-[9.5px] tracking-[0.06em] text-text-secondary">
                {metric.state === "recorded" ? share(i) : flowStageGap(metric)}
              </p>
              {metric.state === "recorded" && metric.basis === "proxy" ? (
                <div className="mt-1.5"><Badge>Proxy</Badge></div>
              ) : null}
            </li>
          ))}
        </ol>
        <svg viewBox={`0 0 ${FLOW_W} ${FLOW_H}`} preserveAspectRatio="none" className="mt-4 block h-[200px] w-full" aria-hidden>
          <defs>
            <linearGradient id={`${ids}-band`} x1="0" x2="1" y1="0" y2="0">
              <stop offset="0" style={{ stopColor: "var(--color-maroon)", stopOpacity: 0.95 }} />
              <stop offset="1" style={{ stopColor: "var(--color-maroon)", stopOpacity: 0.55 }} />
            </linearGradient>
            <pattern id={`${ids}-hatch`} width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="4" height="10" style={{ fill: "var(--color-surface)" }} opacity="0.7" />
            </pattern>
          </defs>
          {stages.map((metric, i) =>
            i > 0 && metric.state === "recorded" && counts[i] < counts[i - 1] ? (
              <path key={metric.id} d={leak(i)} style={{ fill: "var(--color-maroon-soft)" }} opacity="0.35" />
            ) : null,
          )}
          <path d={band} fill={`url(#${ids}-band)`} />
          {stages.map((metric, i) =>
            metric.state === "unavailable" && i > 0 ? (
              <rect key={metric.id} x={step * i} width={step} y={FLOW_TOP} height={heights[i]} fill={`url(#${ids}-hatch)`} />
            ) : null,
          )}
        </svg>
        <ol className="mt-2 grid" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }}>
          {stages.map((metric, i) => {
            const d = drop(i);
            return (
              <li key={metric.id} className="px-2 text-center">
                {d ? (
                  <>
                    <p className={`font-mono text-[12px] tabular-nums ${d.text === "Not shown" ? "text-text-secondary" : "text-maroon"}`}>{d.text}</p>
                    <p className="mt-1 text-[11.5px] leading-snug text-text-secondary">{d.note}</p>
                  </>
                ) : null}
              </li>
            );
          })}
        </ol>
      </div>

      {/* Narrow screens: the same stages as a column of bars. */}
      <ol className="mt-6 lg:hidden">
        {stages.map((metric, i) => {
          const d = drop(i);
          return (
            <li key={metric.id}>
              {d ? (
                <p className="py-2 pl-3 text-[11.5px] leading-snug text-text-secondary">
                  <span className="font-mono text-maroon">{d.text}</span> {d.note}
                </p>
              ) : null}
              <div className="flex items-baseline justify-between gap-3">
                <span className="flex items-center gap-2 text-[13px] text-text-dark">
                  {metricLabel(metric.id, metric.label)}
                  {metric.state === "recorded" && metric.basis === "proxy" ? <Badge>Proxy</Badge> : null}
                </span>
                <span className="font-mono text-[12px] tabular-nums text-text-dark">
                  {metric.state === "recorded" ? metric.value.toLocaleString("en-IN") : "—"}
                </span>
              </div>
              <div className="mt-1.5 h-[6px] overflow-hidden bg-card">
                <div
                  className={`monitoring-bar h-full ${metric.state === "recorded" ? "bg-maroon" : "bg-hair"}`}
                  style={{ width: `${counts[i] > 0 ? Math.max(1, (100 * counts[i]) / total) : 0}%` }}
                />
              </div>
            </li>
          );
        })}
      </ol>

      <div className="mt-7">
        <Note label="How to read this">{panel.caveats.join(" ")}</Note>
      </div>
    </Reveal>
  );
}

function PanelCard({ panel, index }: { panel: MonitoringPanel; index: number }) {
  if (panel.id === "flow" && panel.state === "recorded") {
    return <FlowPanel panel={panel} />;
  }
  if (panel.id === "recording" && panel.state === "recorded") {
    return <RecordingPanel panel={panel} />;
  }
  if (panel.id === "offices" && panel.state === "recorded") {
    return <OfficesPanel panel={panel} />;
  }
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

      {panel.tables?.map((table) => (
        <div key={table.title} className="mt-7 border-t border-hair-soft pt-5">
          <DrilldownTable table={table} />
        </div>
      ))}

      <div className="mt-6">
        <Note label="How to read this">{panel.caveats.join(" ")}</Note>
      </div>
    </Reveal>
  );
}

/** Where every figure on the page comes from, stated once above the panels. */
function AboutTheData({ dashboard }: { dashboard: Dashboard }) {
  const facts: [string, string][] = [
    ["Period", dashboard.periodLabel],
    ["Snapshot", displayDate(dashboard.snapshotDate)],
    ["Published", displayDate(dashboard.generatedAt.slice(0, 10))],
  ];
  const extract = dashboard.sourceFreshness.extractMaximum;
  if (extract) facts.push(["Extract ends", displayDate(extract)]);
  return (
    <div
      className="mt-6 border-l-2 border-hair py-1 pl-4"
      aria-label="About this data"
    >
      <dl className="flex flex-wrap gap-x-6 gap-y-1.5">
        {facts.map(([term, value]) => (
          <div key={term} className="flex items-baseline gap-2">
            <dt className="font-mono text-[9.5px] uppercase tracking-[0.13em] text-text-secondary">
              {term}
            </dt>
            <dd className="text-[12.5px] text-text-dark">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 max-w-[720px] text-[12px] leading-relaxed text-text-secondary">
        Counts are filings unless a figure says groups or citizens. Every rate
        shows its numerator and base. A figure marked <em>Proxy</em> stands in
        for what it names: closure wording for closure quality, a duplicate
        group for a problem.
      </p>
    </div>
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

        {dashboard ? <AboutTheData dashboard={dashboard} /> : null}
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
