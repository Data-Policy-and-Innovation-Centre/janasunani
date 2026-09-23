"use client";

import { useEffect, useState } from "react";
import { fetchHealth, fetchHistory } from "@/lib/api";
import type { HistoryItem, HistoryPage } from "@/lib/types";
import { Badge } from "./ui";

const LIMIT = 20;

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function HistoryView({ initialQuery = "" }: { initialQuery?: string }) {
  // Applied filters (drive the query); form fields are separate so typing
  // doesn't fire a request per keystroke.
  const [q, setQ] = useState(initialQuery);
  const [district, setDistrict] = useState("");
  const [category, setCategory] = useState("");
  const [applied, setApplied] = useState({
    q: initialQuery,
    district: "",
    category: "",
  });
  const [offset, setOffset] = useState(0);

  const [page, setPage] = useState<HistoryPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Which backend is answering. `null` = not yet known.
  //
  // Against the mock API, /history serves MockHistory's fabricated rows, and
  // none of the columns below render `grievance` — the only field carrying its
  // "[fake row N]" marker. Unmarked, they read as genuine historical records.
  //
  // So rows are marked unless the pipeline is *confirmed* live. Labelling real
  // history "mock" for a moment is a cosmetic error; showing invented
  // grievances to a government audience as real is not.
  const [liveProcessor, setLiveProcessor] = useState<boolean | null>(null);
  const showMockMark = liveProcessor !== true;

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((h) => {
        if (!cancelled) setLiveProcessor(h.processor !== "mock");
      })
      .catch(() => {
        if (!cancelled) setLiveProcessor(false); // unreachable is not "live"
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch whenever the applied filters or pagination offset change.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchHistory({ ...applied, limit: LIMIT, offset });
        if (!cancelled) setPage(res);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load history",
          );
          setPage(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applied, offset]);

  function onSearch(e: React.FormEvent) {
    e.preventDefault();
    setOffset(0);
    setApplied({ q, district, category });
  }

  const items: HistoryItem[] = page?.items ?? [];
  const total = page?.total ?? 0;
  const shownFrom = total === 0 ? 0 : offset + 1;
  const shownTo = Math.min(offset + LIMIT, total);
  const canPrev = offset > 0;
  const canNext = offset + LIMIT < total;

  return (
    <div className="flex flex-col gap-7">
      <form
        onSubmit={onSearch}
        className="grid grid-cols-1 items-end gap-x-6 gap-y-4 border-y border-hair py-5 sm:grid-cols-4"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search text (q)"
          className="rounded-none border-0 border-b border-hair bg-transparent px-0 py-2 text-[14.5px] text-text-body outline-none transition-colors focus:border-maroon sm:col-span-2"
        />
        <input
          value={district}
          onChange={(e) => setDistrict(e.target.value)}
          placeholder="District"
          className="rounded-none border-0 border-b border-hair bg-transparent px-0 py-2 text-[14.5px] text-text-body outline-none transition-colors focus:border-maroon"
        />
        <input
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder="Category"
          className="rounded-none border-0 border-b border-hair bg-transparent px-0 py-2 text-[14.5px] text-text-body outline-none transition-colors focus:border-maroon"
        />
        <button
          type="submit"
          disabled={loading}
          className="justify-self-start rounded-full bg-maroon px-6 py-2.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-white transition-colors duration-200 hover:bg-maroon-full disabled:opacity-40"
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && (
        <p className="border-l-2 border-negative bg-negative/5 py-2 pl-3 text-[13.5px] text-negative">
          {error}
        </p>
      )}

      {showMockMark && items.length > 0 && (
        <p className="border-l-2 border-maroon py-2 pl-3 text-[13.5px] text-text-body">
          <Badge tone="maroon">mock data</Badge>{" "}
          These rows are illustrative, not real grievance history. Run{" "}
          <code className="font-mono text-xs">janasunani-api-live</code> against
          the materialized lake to browse actual records.
        </p>
      )}

      <div className="overflow-x-auto border-t border-hair">
        <table className="w-full border-collapse text-[13.5px]">
          <thead>
            <tr className="border-b border-hair text-left">
              <th className="px-3 py-3 font-mono text-[9.5px] font-normal uppercase tracking-[0.13em] text-maroon-soft">Ticket</th>
              <th className="px-3 py-3 font-mono text-[9.5px] font-normal uppercase tracking-[0.13em] text-maroon-soft">Date</th>
              <th className="px-3 py-3 font-mono text-[9.5px] font-normal uppercase tracking-[0.13em] text-maroon-soft">District</th>
              <th className="px-3 py-3 font-mono text-[9.5px] font-normal uppercase tracking-[0.13em] text-maroon-soft">Category</th>
              <th className="px-3 py-3 font-mono text-[9.5px] font-normal uppercase tracking-[0.13em] text-maroon-soft">Department</th>
              <th className="px-3 py-3 font-mono text-[9.5px] font-normal uppercase tracking-[0.13em] text-maroon-soft">Status</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && (
              <tr>
                <td
                  colSpan={6}
                  className="px-3 py-6 text-center text-text-secondary"
                >
                  No matching grievances.
                </td>
              </tr>
            )}
            {items.map((it) => (
              <tr
                key={it.ticket_no}
                className="border-b border-hair-soft transition-colors duration-150 hover:bg-maroon-wash/50"
              >
                <td className="px-3 py-2 font-mono text-text-dark">
                  {it.ticket_no}
                  {showMockMark && (
                    <span className="ml-2 align-middle">
                      <Badge tone="maroon">mock</Badge>
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-text-body">
                  {fmtDate(it.created_on)}
                </td>
                <td className="px-3 py-2 text-text-body">
                  {it.district ?? "—"}
                </td>
                <td className="px-3 py-2 text-text-body">
                  {it.category ?? "—"}
                  {it.subcategory ? (
                    <span className="block text-xs text-text-secondary">
                      {it.subcategory}
                    </span>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-text-body">{it.dept ?? "—"}</td>
                <td className="px-3 py-2">
                  {it.status ? (
                    <Badge tone="neutral">{it.status}</Badge>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-[12.5px] text-text-secondary">
        <span>
          {total > 0
            ? `Showing ${shownFrom}–${shownTo} of ${total}`
            : "0 results"}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            disabled={!canPrev || loading}
            className="rounded-full border border-hair px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-text-secondary transition-colors duration-200 hover:border-maroon hover:text-maroon disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setOffset(offset + LIMIT)}
            disabled={!canNext || loading}
            className="rounded-full border border-hair px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-text-secondary transition-colors duration-200 hover:border-maroon hover:text-maroon disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
