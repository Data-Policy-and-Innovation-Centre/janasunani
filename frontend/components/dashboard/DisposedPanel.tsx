"use client";

import { useEffect, useState } from "react";
import { fetchDisposed, fetchRoutes } from "@/lib/api";
import {
  fmtDays,
  sharePct,
  ORDERS,
  type DisposalRow,
  type Order,
  type RouteBreakdown,
} from "@/lib/dashboard";
import { StageBar, StageLegend } from "./StageBar";

/** Where the drill-down stands: all cases, then a department's, then one of
 * its categories' routes. */
type Drill = { level: "overall" } | { level: "dept" } | { level: "category"; dept: string } | { level: "routes"; dept: string; category: string };

function Row({ row, onOpen, total }: { row: DisposalRow; onOpen?: () => void; total?: number }) {
  const other = row.label.startsWith("Other routes");
  const days = (
    <>
      <span className="figure text-[28px]">{fmtDays(row.meanDays)}</span>
    </>
  );
  return (
    <li className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] sm:items-center sm:gap-6">
      <div>
        <p className={`text-[18px] ${other ? "italic text-text-secondary" : "text-text-dark"}`}>{row.label}</p>
        <p className="text-[14px] text-text-secondary">
          {row.n.toLocaleString("en-IN")} cases{total ? ` · ${sharePct(row.n, total)} of cases` : ""}
        </p>
        {onOpen ? (
          <button type="button" onClick={onOpen} className="mt-1 text-left hover:underline" title="Break down">
            {days} <span className="text-maroon">›</span>
          </button>
        ) : (
          <p className="mt-1">{days}</p>
        )}
      </div>
      <StageBar phases={row.phases} compact />
    </li>
  );
}

/** Disposal times for one scope. The page remounts it when the entry office
 * or year changes, so the drill-down starts again from the top. */
export function DisposedPanel({ office, year }: { office: string; year: string }) {
  const [period, setPeriod] = useState("");
  const [drill, setDrill] = useState<Drill>({ level: "overall" });
  const [order, setOrder] = useState<Order>("slowest");
  const [overall, setOverall] = useState<DisposalRow | null>(null);
  const [rows, setRows] = useState<DisposalRow[]>([]);
  const [routes, setRoutes] = useState<RouteBreakdown | null>(null);
  const [error, setError] = useState<string | null>(null);

  const officeParam = office || undefined;
  const yearParam = year || undefined;

  useEffect(() => {
    let cancelled = false;
    fetchDisposed({ level: "overall", office: officeParam, year: yearParam })
      .then((b) => {
        if (cancelled) return;
        setOverall(b.rows[0] ?? null);
        setPeriod(b.period);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [officeParam, yearParam]);

  useEffect(() => {
    let cancelled = false;
    const fail = (e: Error) => !cancelled && setError(e.message);
    if (drill.level === "dept" || drill.level === "category") {
      fetchDisposed({ level: drill.level, order, office: officeParam, year: yearParam, dept: drill.level === "category" ? drill.dept : undefined })
        .then((b) => !cancelled && setRows(b.rows))
        .catch(fail);
    } else if (drill.level === "routes") {
      fetchRoutes({ office: officeParam, year: yearParam, dept: drill.dept, category: drill.category, order })
        .then((r) => !cancelled && setRoutes(r))
        .catch(fail);
    }
    return () => {
      cancelled = true;
    };
  }, [drill, order, officeParam, yearParam]);

  // A new level or office replaces the rows below, so the old ones are
  // cleared here rather than left on screen while the new ones load.
  const go = (to: Drill) => {
    setRows([]);
    setRoutes(null);
    setDrill(to);
  };
  const crumbs: { label: string; to: Drill }[] = [{ label: "All disposed", to: { level: "overall" } }];
  if (drill.level !== "overall") crumbs.push({ label: "Departments", to: { level: "dept" } });
  if (drill.level === "category" || drill.level === "routes") crumbs.push({ label: drill.dept, to: { level: "category", dept: drill.dept } });
  if (drill.level === "routes") crumbs.push({ label: drill.category, to: drill });

  return (
    <div className="space-y-8">
      {error && <p className="text-[16px] text-negative">{error}</p>}

      {overall && (
        <section className="space-y-4">
          <p className="text-[15px] text-text-secondary">
            Average time to dispose · {period} · {overall.n.toLocaleString("en-IN")} cases
          </p>
          <button
            type="button"
            onClick={() => go(drill.level === "overall" ? { level: "dept" } : { level: "overall" })}
            className="text-left"
            title="Break down by department"
          >
            <span className="figure text-[54px]">{fmtDays(overall.meanDays)}</span>
            <span className="ml-3 text-[15px] text-text-secondary">
              <span className="text-maroon">by department ›</span>
            </span>
          </button>
          <StageBar phases={overall.phases} />
          <StageLegend bars={[overall, ...rows, ...(routes?.rows ?? [])].map((r) => r.phases)} />
          <p className="text-[15px] text-text-secondary">
            Stages add up to the average.
          </p>
        </section>
      )}

      {drill.level !== "overall" && (
        <section>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <nav aria-label="Breakdown" className="flex flex-wrap items-center gap-1 text-[16px]">
              {crumbs.map((c, i) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span className="text-text-secondary">›</span>}
                  <button
                    type="button"
                    onClick={() => go(c.to)}
                    className={i === crumbs.length - 1 ? "text-text-dark" : "text-maroon hover:underline"}
                  >
                    {c.label}
                  </button>
                </span>
              ))}
            </nav>
            {/* One sort for every level: by how many cases, or by how long. */}
            <div className="flex items-center gap-2">
              <span className="text-[15px] text-text-secondary">Sort by</span>
              <div className="inline-flex overflow-hidden rounded-full border border-hair text-[15px]" role="group" aria-label="Sort by">
                {ORDERS.map((o) => (
                  <button
                    key={o.id}
                    type="button"
                    aria-pressed={order === o.id}
                    onClick={() => setOrder(o.id)}
                    className={`px-3 py-1 ${order === o.id ? "bg-maroon text-white" : "text-text-secondary hover:bg-maroon-wash"}`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {drill.level !== "routes" && (
            <ul className="divide-y divide-hair-soft border-y border-hair">
              {rows.map((row) => (
                <Row
                  key={row.label}
                  row={row}
                  onOpen={() =>
                    go(
                      drill.level === "dept"
                        ? { level: "category", dept: row.label }
                        : { level: "routes", dept: (drill as { dept: string }).dept, category: row.label },
                    )
                  }
                />
              ))}
            </ul>
          )}

          {routes && (
            <>
              <p className="mb-2 text-[15px] text-text-secondary">
                Top routes, up to 10, covering 95% of cases. Routes under {routes.minRouteN} cases are grouped as Other.
              </p>
              <ul className="divide-y divide-hair-soft border-y border-hair">
                {routes.rows.map((row) => <Row key={row.label} row={row} total={routes.total} />)}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}
