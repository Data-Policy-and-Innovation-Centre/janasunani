"use client";

import { useEffect, useState } from "react";
import { fetchLive, fetchQueue } from "@/lib/api";
import {
  facetOptions,
  fmtDays,
  type AwaitingFilter,
  type BucketId,
  type LiveSummary,
  type DashboardMeta,
  type QueueFilters,
  type QueueItem,
  type QueuePage,
} from "@/lib/dashboard";
import { Picker } from "./Picker";
import { Timeline } from "./Timeline";

const PAGE = 50;

// Each label kind has its own colour, keyed by the legend above the queue.
const CHIP = {
  category: "border-gold/40 bg-gold/10 text-gold",
  dept: "border-sky/40 bg-sky/10 text-sky",
} as const;

/** A label on a ticket. Clicking it filters the queue to that value. */
function Chip({ kind, onPick, children }: { kind: keyof typeof CHIP; onPick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onPick}
      title={`Show only this ${kind === "dept" ? "department" : "category"}`}
      className={`inline-flex max-w-[320px] items-center truncate rounded-full border px-2.5 py-[3px] text-[15px] hover:brightness-95 ${CHIP[kind]}`}
    >
      {children}
    </button>
  );
}

/** A facet as a select in its label's colour; the first option clears it. */
function FacetSelect({ kind, label, options, value, onChange }: {
  kind: keyof typeof CHIP;
  label: string;
  options: { label: string; count: number }[];
  value?: string;
  onChange: (v: string | undefined) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || undefined)}
      className={`max-w-[300px] rounded-full border px-3 py-1 text-[15px] ${CHIP[kind]}`}
    >
      <option value="">{`${label}: all`}</option>
      {options.map((o) => (
        <option key={o.label} value={o.label}>
          {`${o.label} (${o.count.toLocaleString("en-IN")})`}
        </option>
      ))}
    </select>
  );
}

const AWAITING: { id: AwaitingFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "only", label: "Only awaiting" },
  { id: "hide", label: "Hide awaiting" },
];

export function LivePanel({ meta }: { meta: DashboardMeta }) {
  const [office, setOffice] = useState("");
  // Open cases from every year by default: a case filed in 2023 and still
  // open is the one a supervisor most needs to see.
  const [year, setYear] = useState("");
  const [summary, setSummary] = useState<LiveSummary | null>(null);
  const [bucket, setBucket] = useState<BucketId | null>(null);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<QueuePage["facets"] | null>(null);
  const [filters, setFilters] = useState<QueueFilters>({ awaiting: "all" });
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchLive(office || undefined, year || undefined)
      .then((s) => !cancelled && setSummary(s))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [office, year]);

  useEffect(() => {
    let cancelled = false;
    if (!bucket) return;
    fetchQueue(bucket, office || undefined, year || undefined, filters, 0, PAGE)
      .then((p) => {
        if (cancelled) return;
        setItems(p.items);
        setTotal(p.total);
        setFacets(p.facets);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [bucket, office, year, filters]);

  // Changing either selector starts a fresh queue, so it is cleared here, in
  // the handler, rather than in the effect that refetches it.
  const reset = () => {
    setItems([]);
    setTotal(0);
    setOpen(null);
  };
  // A category or department chosen for one office or band may not exist in
  // the next, so those clear; the awaiting choice carries over.
  const chooseOffice = (value: string) => {
    reset();
    setFilters((f) => ({ awaiting: f.awaiting }));
    setOffice(value);
  };
  const chooseYear = (value: string) => {
    reset();
    setFilters((f) => ({ awaiting: f.awaiting }));
    setYear(value);
  };
  const chooseBucket = (value: BucketId | null) => {
    reset();
    setFilters((f) => ({ awaiting: f.awaiting }));
    setBucket(value);
  };
  const filter = (change: Partial<QueueFilters>) => {
    reset();
    setFilters((f) => ({ ...f, ...change }));
  };
  const filtered = filters.awaiting !== "all" || filters.category !== undefined || filters.dept !== undefined;

  const loadMore = () => {
    if (!bucket) return;
    fetchQueue(bucket, office || undefined, year || undefined, filters, items.length, PAGE)
      .then((p) => setItems((prev) => [...prev, ...p.items]))
      .catch((e: Error) => setError(e.message));
  };

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap gap-x-8 gap-y-3">
        <Picker label="Entry office" allLabel="Statewide" options={meta.offices} value={office} onChange={chooseOffice} />
        <Picker label="Year filed" allLabel="All years" options={meta.years} value={year} onChange={chooseYear} />
      </div>
      {error && <p className="text-[16px] text-negative">{error}</p>}

      {summary && (
        <>
          <div>
            <p className="text-[15px] text-text-secondary">Open cases</p>
            <p className="figure mt-1 text-[54px]">{summary.open.toLocaleString("en-IN")}</p>
          </div>
          {/* One row per age band, as the old aging breakdown drew it: the
              count over a bar scaled to the largest band. */}
          <div className="border-t border-hair-soft pt-2" aria-label="Open cases by days open">
            {summary.buckets.map((b) => {
              const active = bucket === b.id;
              const largest = Math.max(1, ...summary.buckets.map((x) => x.count));
              return (
                <button
                  key={b.id}
                  type="button"
                  onClick={() => chooseBucket(active ? null : b.id)}
                  aria-pressed={active}
                  className={`block w-full rounded px-3 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-maroon ${
                    active ? "bg-maroon-wash" : "hover:bg-maroon-wash/40"
                  }`}
                >
                  <span className="flex items-baseline justify-between gap-3 text-[17px]">
                    <span className={active ? "text-maroon" : "text-text-body"}>
                      {b.label} <span className="text-maroon-soft">{active ? "▴" : "▾"}</span>
                    </span>
                    <strong className="text-[17px] font-medium tabular-nums text-text-dark">
                      {b.count.toLocaleString("en-IN")}
                    </strong>
                  </span>
                  <span className="mt-2 block h-[10px] overflow-hidden rounded-full bg-card">
                    <span
                      className="block h-full rounded-full bg-maroon transition-[width] duration-500"
                      style={{ width: `${b.count === 0 ? 0 : Math.max(2, (100 * b.count) / largest)}%` }}
                    />
                  </span>
                </button>
              );
            })}
          </div>
        </>
      )}

      {bucket && (
        <section>
          <p className="mb-3 text-[16px] text-text-secondary">
            {total.toLocaleString("en-IN")} {filtered ? "cases match these filters" : "cases"}. Cases still with the office
            they entered at come first, then the longest open. Click a label on a case to filter by it.
          </p>
          {/* The legend is the filter bar: each control is in the colour of
              the label it filters. Counts come from the server, so they cover
              the whole band, not only the tickets loaded so far. */}
          <div className="mb-3 flex flex-wrap items-center gap-2" aria-label="Filter the queue">
            <div className="inline-flex overflow-hidden rounded-full border border-maroon/40 text-[15px]" role="group" aria-label="Awaiting assignment">
              {AWAITING.map((o) => {
                const active = filters.awaiting === o.id;
                const n = o.id === "only" ? facets?.awaiting : o.id === "hide" ? facets?.notAwaiting : undefined;
                return (
                  <button
                    key={o.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => filter({ awaiting: o.id })}
                    className={`px-3 py-1 ${active ? "bg-maroon text-white" : "text-maroon hover:bg-maroon-wash"}`}
                  >
                    {o.label}
                    {n !== undefined && <span className="ml-1 opacity-70">{n.toLocaleString("en-IN")}</span>}
                  </button>
                );
              })}
            </div>
            <FacetSelect kind="category" label="Category" value={filters.category}
              options={facetOptions(facets?.categories ?? [], filters.category)}
              onChange={(category) => filter({ category })} />
            <FacetSelect kind="dept" label="Department" value={filters.dept}
              options={facetOptions(facets?.depts ?? [], filters.dept)}
              onChange={(dept) => filter({ dept })} />
            {filtered && (
              <button type="button" onClick={() => filter({ awaiting: "all", category: undefined, dept: undefined })}
                className="text-[15px] text-text-secondary underline">
                Clear filters
              </button>
            )}
          </div>
          <ul className="divide-y divide-hair-soft border-y border-hair">
            {items.map((item) => (
              <li key={item.ticketNo} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="mr-2 font-mono text-[16px] text-text-dark">{item.ticketNo}</span>
                  {item.awaitingAssignment && (
                    <span className="rounded-full bg-maroon px-3 py-[3px] text-[14px] text-white">
                      Awaiting assignment
                    </span>
                  )}
                  <Chip kind="category" onPick={() => filter({ category: item.category ?? "Not recorded" })}>
                    {item.category ?? "Category not recorded"}
                  </Chip>
                  <Chip kind="dept" onPick={() => filter({ dept: item.dept ?? "Not recorded" })}>
                    {item.dept ?? "Department not recorded"}
                  </Chip>
                  <button
                    type="button"
                    onClick={() => setOpen(open === item.ticketNo ? null : item.ticketNo)}
                    aria-expanded={open === item.ticketNo}
                    className="ml-auto rounded-full border border-maroon/40 px-3 py-[3px] text-[15px] tabular-nums text-maroon hover:bg-maroon-wash"
                  >
                    {fmtDays(item.daysOpen)} {open === item.ticketNo ? "▴" : "▾"}
                  </button>
                </div>
                {open === item.ticketNo && (
                  <div className="mt-4 rounded bg-panel px-4 py-4">
                    <Timeline ticketNo={item.ticketNo} />
                  </div>
                )}
              </li>
            ))}
          </ul>
          {items.length < total && (
            <button type="button" onClick={loadMore} className="mt-4 text-[16px] text-maroon underline">
              Show {Math.min(PAGE, total - items.length)} more
            </button>
          )}
        </section>
      )}
    </div>
  );
}
