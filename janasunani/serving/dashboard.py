"""The supervisor dashboard provider: the open queue and disposal times.

Serves the release ``janasunani-publish-dashboard`` writes
(``janasunani/analytics/dashboard.py``). It never queries the lake: the four
published files are loaded into an in-memory DuckDB once, and each request is
a parameterised query over them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb

from janasunani.analytics.journey import PHASES
from janasunani.serving.schemas import (
    DashboardMeta,
    DisposalBreakdown,
    DisposalRow,
    FacetCount,
    LiveBucket,
    LiveSummary,
    Phases,
    QueueFacets,
    QueueItem,
    QueuePage,
    RouteBreakdown,
    StatusSummary,
    Timeline,
    TimelineStep,
)

_FILES = ("status_counts", "open_cases", "open_actions", "disposed_phases")

# Days open, inclusive at both ends.
BUCKETS = {
    "0-30": ("Open up to 30 days", 0, 30),
    "31-60": ("Open 31 to 60 days", 31, 60),
    "61+": ("Open more than 60 days", 61, None),
}

# Which routes get a row of their own. Routes are listed largest first until
# they cover ROUTES_COVER of the cases, up to ROUTES_MAX rows, and only while a
# route has MIN_ROUTE_N cases; the rest fold into one "Other routes" row. Tried
# on the FY 2024-25 release: routes fragment so much within a category that
# a 5% tail alone would list dozens of one-case routes, and the case floor is
# what binds (typically 5 rows, a fifth of cases in "Other").
ROUTES_COVER = 0.95
ROUTES_MAX = 10
# A route's mean over a handful of cases is noise, so no route with fewer
# cases than this gets a row of its own.
MIN_ROUTE_N = 10


# What a missing category or department is shown as, and filtered back by.
NOT_RECORDED = "Not recorded"


def _label_filters(where: str, params: list, values: dict[str, str | None]) -> tuple[str, list]:
    """Narrow to chosen category/department values; the not-recorded label
    selects the missing ones."""
    for column, value in values.items():
        if value is not None:
            where += f" AND {column} IS NOT DISTINCT FROM ?"
            params = params + [None if value == NOT_RECORDED else value]
    return where, params


class DashboardUnavailable(RuntimeError):
    """No valid release is published; the endpoints answer 503."""


def _bucket_sql(bucket: str) -> tuple[str, list]:
    _label, low, high = BUCKETS[bucket]
    if high is None:
        return "days_open >= ?", [low]
    return "days_open BETWEEN ? AND ?", [low, high]


class DashboardProvider:
    def __init__(self, release_dir: Path) -> None:
        self.release_dir = Path(release_dir)
        self._con: duckdb.DuckDBPyConnection | None = None
        self._meta: DashboardMeta | None = None
        self._stamp: int | None = None

    def _load(self) -> duckdb.DuckDBPyConnection:
        # Loaded on first use, not at startup, so the API still starts (and
        # says why) when nothing has been published yet.
        paths = [self.release_dir / f"{name}.parquet" for name in _FILES]
        meta_path = self.release_dir / "meta.json"
        missing = [p.name for p in [meta_path, *paths] if not p.is_file()]
        if missing:
            raise DashboardUnavailable(
                "No dashboard release is published (missing "
                + ", ".join(missing) + "). Run janasunani-publish-dashboard.")
        # The publisher writes meta.json last, so a new stamp on it means a
        # new release: reload it without restarting the API.
        stamp = meta_path.stat().st_mtime_ns
        if self._con is not None and stamp == self._stamp:
            return self._con
        con = duckdb.connect()
        try:
            meta = DashboardMeta.model_validate(json.loads(meta_path.read_text()))
            for name, path in zip(_FILES, paths):
                con.execute(
                    f"CREATE TABLE {name} AS SELECT * FROM read_parquet(?)",
                    [path.as_posix()])
        except Exception as exc:
            con.close()
            raise DashboardUnavailable(
                f"The dashboard release is unreadable ({exc}). "
                "Run janasunani-publish-dashboard.") from exc
        self._con, self._meta, self._stamp = con, meta, stamp
        return con

    def _cursor(self) -> duckdb.DuckDBPyConnection:
        # One cursor per request: FastAPI runs sync handlers on a thread pool,
        # and a DuckDB connection is not safe to share across threads.
        return self._load().cursor()

    def meta(self) -> DashboardMeta:
        self._load()
        assert self._meta is not None
        return self._meta

    def _scope(self, office: str | None, year: str | None) -> tuple[str, list]:
        """The entry office and filing year every view is cut by. None is
        statewide, or all years."""
        where, params = ["TRUE"], []
        if office is not None:
            if office not in {o.id for o in self.meta().offices}:
                raise KeyError(f"unknown office: {office}")
            where.append("entry_office = ?")
            params.append(office)
        if year is not None:
            fy = {y.id: y.fy_start for y in self.meta().years}.get(year)
            if fy is None:
                raise KeyError(f"unknown year: {year}")
            where.append("fy = ?")
            params.append(fy)
        return " AND ".join(where), params

    def _period(self, year: str | None) -> str:
        return "All years" if year is None else next(
            f"Filed {y.label}" for y in self.meta().years if y.id == year)

    # -- status ---------------------------------------------------------------

    def status(self, office: str | None, year: str | None = None) -> StatusSummary:
        """Every filing in scope, split by outcome: the tree the page opens on."""
        where, params = self._scope(office, year)
        counts = dict(self._cursor().execute(
            f"SELECT outcome, SUM(n) FROM status_counts WHERE {where} GROUP BY 1",
            params).fetchall())
        get = lambda k: int(counts.get(k) or 0)  # noqa: E731
        return StatusSummary(
            period=self._period(year), total=sum(int(v) for v in counts.values()),
            open=get("Open"), disposed=get("Disposed") + get("Disposed with benefit"),
            disposed_with_benefit=get("Disposed with benefit"), discarded=get("Discarded"))

    # -- live ---------------------------------------------------------------

    def live(self, office: str | None, year: str | None = None) -> LiveSummary:
        where, params = self._scope(office, year)
        cur = self._cursor()
        counts = []
        for bucket, (label, _low, _high) in BUCKETS.items():
            cond, extra = _bucket_sql(bucket)
            n = cur.execute(
                f"SELECT COUNT(*) FROM open_cases WHERE {where} AND {cond}",
                params + extra).fetchone()[0]
            counts.append(LiveBucket(id=bucket, label=label, count=n))
        return LiveSummary(as_of=self.meta().as_of,
                           open=sum(b.count for b in counts), buckets=counts)

    def queue(self, office: str | None, bucket: str, limit: int, offset: int,
              awaiting: str = "all", category: str | None = None,
              dept: str | None = None, year: str | None = None) -> QueuePage:
        where, params = self._scope(office, year)
        cond, extra = _bucket_sql(bucket)
        where, params = f"{where} AND {cond}", params + extra
        chosen = {"category": category, "dept": dept}
        awaiting_sql = {"all": "TRUE", "only": "awaiting_assignment",
                        "hide": "NOT awaiting_assignment"}[awaiting]

        def narrowed(skip: str | None = None) -> tuple[str, list]:
            # Every filter but `skip`, so a facet's counts say what choosing
            # each of its values would leave, given the other filters.
            w, p = _label_filters(where, list(params),
                                  {k: v for k, v in chosen.items() if k != skip})
            return (w if skip == "awaiting" else f"{w} AND {awaiting_sql}"), p

        cur = self._cursor()
        w, p = narrowed()
        total = cur.execute(f"SELECT COUNT(*) FROM open_cases WHERE {w}", p).fetchone()[0]
        rows = cur.execute(
            f"""SELECT ticket_no, category, dept, days_open, awaiting_assignment
                FROM open_cases WHERE {w}
                ORDER BY awaiting_assignment DESC, days_open DESC, ticket_no
                LIMIT ? OFFSET ?""",
            p + [limit, offset]).fetchall()
        items = [QueueItem(ticket_no=t, category=c, dept=d, days_open=n,
                           awaiting_assignment=a) for t, c, d, n, a in rows]

        def counts(column: str) -> list[FacetCount]:
            w, p = narrowed(column)
            return [FacetCount(label=label, count=n) for label, n in cur.execute(
                f"""SELECT COALESCE({column}, '{NOT_RECORDED}') AS label, COUNT(*) AS n
                    FROM open_cases WHERE {w} GROUP BY 1 ORDER BY n DESC, label""",
                p).fetchall()]

        w, p = narrowed("awaiting")
        split = cur.execute(
            f"""SELECT COUNT(*) FILTER (WHERE awaiting_assignment),
                       COUNT(*) FILTER (WHERE NOT awaiting_assignment)
                FROM open_cases WHERE {w}""", p).fetchone()
        return QueuePage(
            items=items, total=total, limit=limit, offset=offset,
            facets=QueueFacets(categories=counts("category"), depts=counts("dept"),
                               awaiting=split[0] or 0, not_awaiting=split[1] or 0))

    def timeline(self, ticket_no: str) -> Timeline:
        cur = self._cursor()
        case = cur.execute(
            "SELECT created_on, days_open FROM open_cases WHERE ticket_no = ?",
            [ticket_no]).fetchone()
        if case is None:
            raise KeyError(f"no open case {ticket_no}")
        as_of = self.meta().as_of
        rows = cur.execute(
            """SELECT action_date, status, office,
                      LEAD(action_date) OVER (ORDER BY seq) AS next_date
               FROM open_actions WHERE ticket_no = ? ORDER BY seq""",
            [ticket_no]).fetchall()
        steps = [
            TimelineStep(date=d, status=s, office=o,
                         # The record's own dates can run backwards; a step
                         # never shows negative time.
                         days=max(((nxt or as_of) - d).days, 0),
                         current=nxt is None)
            for d, s, o, nxt in rows
        ]
        return Timeline(ticket_no=ticket_no, created_on=case[0], as_of=as_of,
                        days_open=case[1], steps=steps)

    # -- disposed -----------------------------------------------------------

    def _rows(self, where: str, params: list, group: str | None,
              order: str = "slowest", having: str = "TRUE",
              limit: int | None = None, order_by: str | None = None) -> list[DisposalRow]:
        label = group or "'All disposed cases'"
        sort = {"slowest": "mean_days DESC", "fastest": "mean_days ASC",
                "volume": "n DESC"}[order]
        rows = self._cursor().execute(
            f"""SELECT COALESCE(CAST({label} AS VARCHAR), '{NOT_RECORDED}') AS label,
                       COUNT(*) AS n, AVG(days_to_close) AS mean_days,
                       {', '.join(f'AVG({p})' for p in PHASES)}
                FROM disposed_phases WHERE {where}
                GROUP BY 1 HAVING {having}
                ORDER BY {order_by or sort}, label
                {'' if limit is None else f'LIMIT {int(limit)}'}""",
            params).fetchall()
        out = []
        for lab, n, mean, *spans in rows:
            # The five spans tile each case's total, so their means must add
            # up to the mean total. A mismatch is a broken release.
            if abs(sum(spans) - mean) > 0.01:
                raise DashboardUnavailable(
                    f"phases for {lab!r} sum to {sum(spans):.2f}, total is {mean:.2f}")
            out.append(DisposalRow(
                label=lab, n=n, mean_days=round(mean, 1),
                phases=Phases(**{p: round(v, 1) for p, v in zip(PHASES, spans)})))
        return out

    def _disposed_where(self, office: str | None, year: str | None, dept: str | None,
                        category: str | None) -> tuple[str, list]:
        where, params = self._scope(office, year)
        return _label_filters(where, params, {"dept": dept, "category": category})

    def disposed(self, office: str | None, level: str, order: str,
                 dept: str | None = None, year: str | None = None) -> DisposalBreakdown:
        group = {"overall": None, "dept": "dept", "category": "category"}[level]
        where, params = self._disposed_where(
            office, year, dept if level == "category" else None, None)
        return DisposalBreakdown(period=self._period(year),
                                 rows=self._rows(where, params, group, order))

    def routes(self, office: str | None, dept: str | None,
               category: str | None, order: str = "volume",
               year: str | None = None) -> RouteBreakdown:
        where, params = self._disposed_where(office, year, dept, category)
        counts = self._cursor().execute(
            f"""SELECT route, COUNT(*) AS n FROM disposed_phases WHERE {where}
                GROUP BY route ORDER BY n DESC, route""", params).fetchall()
        total = sum(n for _, n in counts)
        named, covered = [], 0
        for route, n in counts:
            if covered >= ROUTES_COVER * total or len(named) >= ROUTES_MAX or n < MIN_ROUTE_N:
                break
            named.append(route)
            covered += n
        rest = len(counts) - len(named)
        named_sql = "route IN (SELECT UNNEST(?::VARCHAR[]))"
        rows = self._rows(f"{where} AND {named_sql}", params + [named], "route",
                          order) if named else []
        # "Other" mixes many routes, so it is never ranked: it always comes last.
        if rest:
            label = f"Other routes ({rest:,} {'route' if rest == 1 else 'routes'})"
            rows += self._rows(f"{where} AND NOT {named_sql}", params + [named],
                               f"'{label}'")
        return RouteBreakdown(period=self._period(year),
                              min_route_n=MIN_ROUTE_N, total=total, rows=rows)

def dashboard_provider_from_env() -> DashboardProvider:
    return DashboardProvider(Path(os.environ.get(
        "JANASUNANI_DASHBOARD_DIR", "outputs/dashboard")))
