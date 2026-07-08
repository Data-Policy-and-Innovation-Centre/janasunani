"use client";

import { useEffect, useState } from "react";
import { fetchHistory } from "@/lib/api";
import type { HistoryItem, HistoryPage } from "@/lib/types";
import { Badge } from "./ui";

const LIMIT = 20;

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function HistoryView() {
  // Applied filters (drive the query); form fields are separate so typing
  // doesn't fire a request per keystroke.
  const [q, setQ] = useState("");
  const [district, setDistrict] = useState("");
  const [category, setCategory] = useState("");
  const [applied, setApplied] = useState({ q: "", district: "", category: "" });
  const [offset, setOffset] = useState(0);

  const [page, setPage] = useState<HistoryPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    <div className="flex flex-col gap-4">
      <form
        onSubmit={onSearch}
        className="grid grid-cols-1 gap-3 rounded-md border border-hair bg-surface p-4 sm:grid-cols-4"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search text (q)"
          className="rounded-sm border border-hair bg-surface px-3 py-2 text-sm text-text-body outline-none focus:border-maroon sm:col-span-2"
        />
        <input
          value={district}
          onChange={(e) => setDistrict(e.target.value)}
          placeholder="District"
          className="rounded-sm border border-hair bg-surface px-3 py-2 text-sm text-text-body outline-none focus:border-maroon"
        />
        <input
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder="Category"
          className="rounded-sm border border-hair bg-surface px-3 py-2 text-sm text-text-body outline-none focus:border-maroon"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-sm bg-maroon px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-maroon-full disabled:opacity-50 sm:col-span-1"
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && (
        <p className="rounded-sm border border-negative/40 bg-negative/10 px-3 py-2 text-sm text-negative">
          {error}
        </p>
      )}

      <div className="overflow-x-auto rounded-md border border-hair">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-maroon text-left text-white">
              <th className="px-3 py-2 font-semibold">Ticket</th>
              <th className="px-3 py-2 font-semibold">Date</th>
              <th className="px-3 py-2 font-semibold">District</th>
              <th className="px-3 py-2 font-semibold">Category</th>
              <th className="px-3 py-2 font-semibold">Department</th>
              <th className="px-3 py-2 font-semibold">Status</th>
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
            {items.map((it, i) => (
              <tr
                key={it.ticket_no}
                className={i % 2 === 1 ? "bg-card" : "bg-surface"}
              >
                <td className="px-3 py-2 font-mono text-text-dark">
                  {it.ticket_no}
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

      <div className="flex items-center justify-between text-sm text-text-secondary">
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
            className="rounded-sm border border-hair px-3 py-1.5 font-medium text-text-body transition-colors hover:border-maroon hover:text-maroon disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setOffset(offset + LIMIT)}
            disabled={!canNext || loading}
            className="rounded-sm border border-hair px-3 py-1.5 font-medium text-text-body transition-colors hover:border-maroon hover:text-maroon disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
