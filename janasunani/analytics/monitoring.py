"""Publish the aggregate-only CM grievance monitoring release.

The publisher reads only the two declared lake tables plus governed ticket-to-
group CSVs.  It never emits ticket numbers or source rows.  The serving layer
reads the resulting JSON; it does not query the lake at request time.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import duckdb

from janasunani.analytics import journey
from janasunani.config import directories
from janasunani.olap import lake

PERIOD_ID = "fy-2024-25"
PERIOD_LABEL = "FY 2024-25"
PERIOD_START = date(2024, 7, 1)
PERIOD_END = date(2025, 7, 1)
SNAPSHOT_DATE = date(2025, 7, 30)
MIN_CELL = 10
COUNT_UNITS = frozenset({"grievances", "groups", "closures", "citizens"})
# Metrics that stand in for the thing a reader cares about rather than
# measuring it. Wording is not closure quality, a dedup group is not a proven
# problem, an identity key is not a verified person, and the loop, follow-up
# and FIFO measures are the proxies their labels already say they are.
# Everything else counts what the record directly contains.
PROXY_METRICS = frozenset({
    "loop-rate", "followup-proxy", "fifo-exception",
    "problems", "citizens", "duplicate-adjustment", "repeat-groups", "campaigns",
    "bare-ladder", "bare-resolved", "action-recorded", "benefit-recorded",
    "refiling-30", "refiling-90",
})
# Subcategory scopes are built per published department, largest first. Each
# scope re-runs the whole panel suite over the lake, so this is deliberately a
# short list rather than all 196 subcategories department 21 records.
SUBCATEGORY_TOP_N = 5
SUBCATEGORY_MIN_FILINGS = 500
CAMPAIGN_THRESHOLD = 200  # existing large/campaign bucket threshold
MAX_ARTIFACT_BYTES = 10_000_000
SUBTYPE_ROLES = {
    "District Collector",
    "Block Development Officer",
    "Tahasildar",
    "Department Secretary",
    "Police station",
}

_SQL_DIR = Path(__file__).with_name("sql")
_SAFE_ID = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ScopeSpec:
    id: str
    label: str
    kind: str
    definition: str
    predicate: str
    parent_id: str | None = None
    quick_view: bool = False


CORE_SCOPES = (
    ScopeSpec(
        "statewide",
        "Statewide",
        "statewide",
        "All grievances recorded in Janasunani.",
        "TRUE",
    ),
    ScopeSpec(
        "department-21",
        "Panchayati Raj & Drinking Water",
        "department",
        "Grievances recorded against department 21.",
        "dept_id = 21",
        quick_view=True,
    ),
    ScopeSpec(
        "department-40",
        "Social Security & Empowerment of Persons with Disabilities",
        "department",
        "Grievances recorded against department 40.",
        "dept_id = 40",
        quick_view=True,
    ),
    ScopeSpec(
        "handling-cm-grievance-cell",
        "CM Grievance Cell handling",
        "handling_office",
        "Grievances with at least one recorded step at the CM Grievance Cell.",
        "EXISTS (SELECT 1 FROM steps_all s WHERE s.ticket_no = grievance_base.ticket_no AND s.role_name = 'Chief Minister''s Grievance Cell')",
        quick_view=True,
    ),
    ScopeSpec(
        "entry-office-office-of-chief-minister",
        "CM Office entry",
        "entry_office",
        "Grievances whose recorded intake office is Office of Chief Minister.",
        "office = 'Office of Chief Minister'",
        quick_view=True,
    ),
)


def _slug(value: str) -> str:
    return _SAFE_ID.sub("-", value.lower()).strip("-")


def _sql_str(value: str) -> str:
    """Quote a literal for inlining into a scope predicate."""
    return "'" + value.replace("'", "''") + "'"


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pct(numerator: int | float, denominator: int | float) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _metric(
    metric_id: str,
    label: str,
    value: int | float | None,
    *,
    unit: str,
    numerator: int | None = None,
    denominator: int | None = None,
    coverage: float | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if value is None:
        return {
            "id": metric_id,
            "label": label,
            "state": "unavailable",
            "reason": note or "The source does not contain this measure.",
        }
    # The minimum reportable cell covers headline metrics as well as
    # breakdowns: a count, or the numerator or denominator behind a rate or a
    # mean, of 1-9 is withheld. Zero is published, as in suppress_breakdown.
    cells = [numerator, denominator]
    if unit in COUNT_UNITS:
        cells.append(value)
    if any(cell is not None and 0 < cell < MIN_CELL for cell in cells):
        return {
            "id": metric_id,
            "label": label,
            "state": "unavailable",
            "reason": f"Withheld because a cell is below {MIN_CELL}.",
        }
    return {
        "id": metric_id,
        "label": label,
        "state": "recorded",
        "value": round(value, 1) if isinstance(value, float) else value,
        "unit": unit,
        "numerator": numerator,
        "denominator": denominator,
        "coveragePct": coverage,
        "note": note,
        "basis": "proxy" if metric_id in PROXY_METRICS else "direct",
    }


def suppress_breakdown(rows: list[dict[str, Any]], count_key: str = "value") -> list[dict[str, Any]] | None:
    """Withhold a whole breakdown when any positive cell is below ten."""
    if any(0 < int(row[count_key]) < MIN_CELL for row in rows):
        return None
    return rows


def withhold_small_panel(panel: dict[str, Any]) -> dict[str, Any]:
    """Withhold a whole panel whose cohort is 1-9.

    The dashboard always renders a recorded panel's denominator, so a small
    cohort is itself a reportable cell, whatever _metric withheld inside it.
    """
    denominator = panel.get("denominator") or {}
    value = denominator.get("value")
    if panel.get("state") != "recorded" or value is None or not 0 < value < MIN_CELL:
        return panel
    return {
        "id": panel["id"],
        "title": panel["title"],
        "state": "unavailable",
        "reason": f"Withheld because the panel's cohort is below {MIN_CELL}.",
        "caveats": panel["caveats"],
    }


def _one(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
    cur = con.execute(sql, params or [])
    columns = [item[0] for item in cur.description]
    return dict(zip(columns, cur.fetchone(), strict=True))


def _subcategory_scopes(
    con: duckdb.DuckDBPyConnection,
    parents: Sequence[ScopeSpec],
) -> tuple[ScopeSpec, ...]:
    """Build a scope per large subcategory of each published department.

    Subcategory is a cut *within* a department, not a sibling of one, so each
    scope is parented to its department and the selector cascades exactly as
    the handling-office subtypes already do.

    Only the departments that already carry a dashboard are cut this way, and
    only their largest subcategories: every scope re-runs the full panel suite
    over the lake, so this multiplies the build.
    """
    scopes: list[ScopeSpec] = []
    for parent in parents:
        if parent.kind != "department":
            continue
        dept_id = int(parent.id.removeprefix("department-"))
        rows = con.execute(
            """
            SELECT subcategory, COUNT(*) n FROM complaints
            WHERE dept_id = ? AND subcategory IS NOT NULL
              AND created_on >= ? AND created_on < ?
            GROUP BY subcategory
            HAVING COUNT(*) >= ?
            ORDER BY n DESC, subcategory
            LIMIT ?
            """,
            [dept_id, PERIOD_START, PERIOD_END, SUBCATEGORY_MIN_FILINGS, SUBCATEGORY_TOP_N],
        ).fetchall()
        for label, _count in rows:
            scopes.append(ScopeSpec(
                f"subcategory-{dept_id}-{_slug(label)}",
                label,
                "subcategory",
                f"Grievances recorded against department {dept_id}, subcategory {label}.",
                f"dept_id = {dept_id} AND subcategory = {_sql_str(label)}",
                parent_id=parent.id,
            ))
    return tuple(scopes)


def _catalog(
    con: duckdb.DuckDBPyConnection,
    extra_scopes: Sequence[ScopeSpec] = (),
) -> list[dict[str, Any]]:
    # `extra_scopes` are the scopes built dynamically for this release that
    # also get a dashboard, so they must carry an available period like the
    # core ones. Everything else in the catalogue is listed without a period.
    scopes = list(CORE_SCOPES) + list(extra_scopes)
    core_ids = {scope.id for scope in scopes}
    for dept_id, label in con.execute(
        "SELECT DISTINCT dept_id, dept FROM complaints "
        "WHERE dept_id IS NOT NULL "
        "ORDER BY dept_id"
    ).fetchall():
        label = label or (
            "Department not recorded"
            if dept_id == 0
            else f"Recorded department {dept_id} (name unavailable)"
        )
        scope = ScopeSpec(
            f"department-{dept_id}", label, "department",
            f"Grievances recorded against department {dept_id}.",
            f"dept_id = {int(dept_id)}",
        )
        if scope.id not in core_ids:
            scopes.append(scope)
    for (label,) in con.execute(
        "SELECT DISTINCT office FROM complaints WHERE office IS NOT NULL ORDER BY office"
    ).fetchall():
        scope = ScopeSpec(
            f"entry-office-{_slug(label)}", label, "entry_office",
            f"Grievances whose recorded intake office is {label}.", "FALSE",
        )
        if scope.id not in core_ids:
            scopes.append(scope)
    roles = con.execute(
        "SELECT role_name, place, COUNT(DISTINCT ticket_no) n FROM acting_office_named "
        "WHERE role_name IS NOT NULL GROUP BY role_name, place ORDER BY role_name, place"
    ).fetchall()
    for role in sorted({row[0] for row in roles}):
        role_id = (
            "handling-cm-grievance-cell"
            if role == "Chief Minister's Grievance Cell"
            else f"handling-{_slug(role)}"
        )
        scope = ScopeSpec(
            role_id, role, "handling_office",
            f"Grievances with at least one recorded step at {role}.", "FALSE",
        )
        if scope.id not in core_ids:
            scopes.append(scope)
        if role not in SUBTYPE_ROLES:
            continue
        for row_role, place, n in roles:
            if place and n >= MIN_CELL and row_role == role:
                scopes.append(ScopeSpec(
                    f"{role_id}-{_slug(place)}", place, "handling_office_subtype",
                    f"Grievances with at least one recorded step at {role}, {place}.",
                    "FALSE", parent_id=role_id,
                ))
    return [
        {
            "id": scope.id,
            "label": scope.label,
            "kind": scope.kind,
            "parentId": scope.parent_id,
            "definition": scope.definition,
            "quickView": scope.quick_view,
            "availablePeriods": [PERIOD_ID] if scope.id in core_ids else [],
        }
        for scope in scopes
    ]


def _prepare(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET threads = 4")
    con.execute("SET memory_limit = '8GB'")
    con.execute((_SQL_DIR / "grievance_journey.sql").read_text())
    con.execute((_SQL_DIR / "closure.sql").read_text())
    con.execute("CREATE OR REPLACE TABLE g AS SELECT * FROM grievance_base")
    journey.install_steps(con)
    con.execute("ALTER TABLE steps RENAME TO steps_all")


def _scope_tables(con: duckdb.DuckDBPyConnection, scope: ScopeSpec) -> dict[str, int]:
    con.execute(
        f"CREATE OR REPLACE TABLE scope_tickets AS "
        f"SELECT * FROM grievance_base WHERE {scope.predicate}"
    )
    con.execute("CREATE OR REPLACE TABLE g AS SELECT * FROM scope_tickets")
    journey.install_steps(con)
    journey.install_phase_bounds(con)
    phase_coverage = journey.install_phases(
        con, "created_on >= DATE '2024-07-01' AND created_on < DATE '2025-07-01'"
    )
    journey.install_returns(con)
    return phase_coverage


def _aging(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    rows = con.execute("""
        WITH latest AS (
          SELECT ticket_no, MAX(action_taken_date) latest_action
          FROM action_history GROUP BY ticket_no
        ), open AS (
          SELECT g.ticket_no,
            DATE '2025-07-30' - CAST(g.created_on AS DATE) age_days,
            DATE '2025-07-30' - CAST(GREATEST(g.last_updated_on, l.latest_action) AS DATE) inactive_days,
            g.escalation_date
          FROM complaints g JOIN scope_tickets s USING (ticket_no)
          LEFT JOIN latest l USING (ticket_no)
          WHERE g.created_on < DATE '2025-07-31'
            AND (g.resolved_on IS NULL OR CAST(g.resolved_on AS DATE) > DATE '2025-07-30')
            AND g.status NOT IN ('Disposed', 'Discard')
        )
        SELECT CASE WHEN age_days <= 6 THEN '0-6 days'
                    WHEN age_days <= 14 THEN '7-14 days'
                    WHEN age_days <= 29 THEN '15-29 days'
                    WHEN age_days <= 59 THEN '30-59 days' ELSE '60+ days' END bucket,
               COUNT(*) AS count_value
        FROM open GROUP BY bucket
        ORDER BY CASE bucket WHEN '0-6 days' THEN 1 WHEN '7-14 days' THEN 2
                 WHEN '15-29 days' THEN 3 WHEN '30-59 days' THEN 4 ELSE 5 END
    """).fetchall()
    summary = _one(con, """
        WITH latest AS (SELECT ticket_no, MAX(action_taken_date) latest_action FROM action_history GROUP BY ticket_no),
        open AS (
          SELECT DATE '2025-07-30' - CAST(GREATEST(g.last_updated_on, l.latest_action) AS DATE) inactive_days,
                 g.escalation_date
          FROM complaints g JOIN scope_tickets s USING(ticket_no) LEFT JOIN latest l USING(ticket_no)
          WHERE g.created_on < DATE '2025-07-31' AND (g.resolved_on IS NULL OR CAST(g.resolved_on AS DATE)>DATE '2025-07-30')
            AND g.status NOT IN ('Disposed','Discard'))
        SELECT COUNT(*) denominator,
          COUNT(*) FILTER (WHERE inactive_days >= 7) inactive,
          COUNT(*) FILTER (WHERE escalation_date < TIMESTAMP '2025-07-31') escalation_passed
        FROM open
    """)
    buckets = suppress_breakdown([{"label": label, "value": value} for label, value in rows])
    return {
        "id": "aging", "title": "Aging and escalation", "state": "recorded",
        "denominator": {"label": "Open at 30 July 2025", "value": summary["denominator"]},
        "metrics": [
            _metric("inactive-7", "No recorded activity for 7+ days", summary["inactive"], unit="grievances", numerator=summary["inactive"], denominator=summary["denominator"]),
            _metric("escalation-passed", "Escalation date passed", summary["escalation_passed"], unit="grievances", numerator=summary["escalation_passed"], denominator=summary["denominator"]),
        ],
        "breakdown": buckets,
        "breakdownUnavailableReason": None if buckets is not None else "Withheld because a positive age cell is below 10.",
        "caveats": ["Activity is the later of last update and latest recorded action.", "Open status follows the recorded status and resolution date."],
    }


def _transfers(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = _one(con, """
        WITH cohort AS (
          SELECT ticket_no FROM scope_tickets WHERE created_on >= DATE '2024-07-01' AND created_on < DATE '2025-07-01'),
        transfer AS (
          SELECT a.*, LEAD(action_taken_date) OVER(PARTITION BY a.ticket_no ORDER BY action_taken_date,id) later
          FROM action_history a JOIN cohort USING(ticket_no)),
        t AS (SELECT * FROM transfer WHERE action_status='Complaint Transfer'),
        r AS (SELECT r.* FROM returns r JOIN cohort USING(ticket_no))
        SELECT (SELECT COUNT(*) FROM cohort) filings,
          (SELECT COUNT(DISTINCT ticket_no) FROM t) transferred,
          (SELECT COUNT(*) FROM t) transfer_events,
          (SELECT COUNT(*) FROM t WHERE later IS NULL OR later > action_taken_date + INTERVAL 7 DAY) no_followup_7d,
          (SELECT COUNT(*) FROM r) ranked,
          (SELECT COUNT(*) FROM r WHERE arrivals > 1) loops
    """)
    return {
        "id": "transfers", "title": "Transfers and loops", "state": "recorded",
        "denominator": {"label": "Grievances created in FY 2024-25", "value": row["filings"]},
        "metrics": [
            _metric("transfer-rate", "Transfer rate", _pct(row["transferred"], row["filings"]), unit="percent", numerator=row["transferred"], denominator=row["filings"]),
            _metric("loop-rate", "Deepest-office repeat arrival", _pct(row["loops"], row["ranked"]), unit="percent", numerator=row["loops"], denominator=row["ranked"]),
            _metric("followup-proxy", "No later recorded action within 7 days", _pct(row["no_followup_7d"], row["transfer_events"]), unit="percent", numerator=row["no_followup_7d"], denominator=row["transfer_events"]),
        ],
        "breakdown": None,
        "breakdownUnavailableReason": None,
        "caveats": ["Follow-up means a later recorded action, not acknowledgement delivery or proof of avoidable delay."],
    }


def _journey(con: duckdb.DuckDBPyConnection, coverage: dict[str, int]) -> dict[str, Any]:
    row = _one(con, """
        SELECT COUNT(*) denominator, MEDIAN(days_to_close) median_total,
          AVG(days_to_close) mean_total,
          AVG(registration) registration, AVG(first_assignment) first_assignment,
          AVG(field_action) field_action, AVG(review) review, AVG(closure) closure
        FROM phases_clean
    """)
    breakdown = [
        {"label": journey.PHASE_LABEL[name], "value": round(float(row[name] or 0), 1)}
        for name in journey.PHASES
    ]
    # The mean leads, because it is the only total the breakdown below can add
    # up to: the five spans tile per row, so the phase means sum to the mean
    # total. Phase medians do not sum to the median total, so leading with the
    # median invited a reader to reconcile the parts against a whole they can
    # never reach. The median stays alongside it: these distributions are long
    # tailed and the mean alone overstates the typical case.
    return {
        "id": "journey", "title": "End-to-end journey", "state": "recorded",
        "denominator": {"label": "Disposed journeys that tile", "value": row["denominator"]},
        "metrics": [
            _metric("mean-total", "Mean total time", float(row["mean_total"] or 0), unit="days", denominator=row["denominator"]),
            _metric("median-total", "Median total time", float(row["median_total"] or 0), unit="days", denominator=row["denominator"]),
            _metric("tiling-coverage", "Tiling-sample coverage", _pct(coverage["tiling_rows"], coverage["all_rows"]), unit="percent", numerator=coverage["tiling_rows"], denominator=coverage["all_rows"]),
        ],
        "breakdown": breakdown,
        "breakdownUnavailableReason": None,
        "caveats": [
            "Reuses the governed five-phase journey; phase means are over clean tiling journeys.",
            "The five phases below are means and add up to the mean total. They do not add up to the median, because medians do not sum.",
        ],
    }


def _atr(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    if not con.execute(
        "SELECT EXISTS(SELECT 1 FROM action_history WHERE action_status='ATR Received')"
    ).fetchone()[0]:
        return {
            "id": "atr", "title": "ATR queue discipline", "state": "unavailable",
            "reason": "The extract has no recorded 'ATR Received' event. 'Replied' remarks are not silently substituted.",
            "caveats": ["Notification, download, priority, and acknowledgement fields are also absent."],
        }
    con.execute("""
        CREATE OR REPLACE TEMP TABLE atr_latest AS
        WITH eligible AS (
          SELECT a.ticket_no, a.action_taken_date atr_date, s.role_name office,
                 c.resolved_on,
                 ROW_NUMBER() OVER(PARTITION BY a.ticket_no ORDER BY a.action_taken_date DESC,a.id DESC) rn
          FROM action_history a JOIN scope_tickets c USING(ticket_no)
          LEFT JOIN acting_office_named s ON s.id=a.id
          WHERE a.action_status='ATR Received' AND a.action_taken_date < TIMESTAMP '2025-07-31'
            AND (c.resolved_on IS NULL OR a.action_taken_date <= c.resolved_on))
        SELECT *, DATE '2025-07-30'-CAST(atr_date AS DATE) age_days FROM eligible WHERE rn=1
    """)
    summary = _one(con, """
        SELECT COUNT(*) FILTER (WHERE resolved_on IS NULL OR CAST(resolved_on AS DATE)>DATE '2025-07-30') outstanding,
          MAX(age_days) FILTER (WHERE resolved_on IS NULL OR CAST(resolved_on AS DATE)>DATE '2025-07-30') oldest,
          MEDIAN(CAST(resolved_on AS DATE)-CAST(atr_date AS DATE)) FILTER (WHERE resolved_on IS NOT NULL AND resolved_on>=atr_date) median_disposal
        FROM atr_latest
    """)
    rows = con.execute("""
        SELECT CASE WHEN age_days<=6 THEN '0-6 days' WHEN age_days<=14 THEN '7-14 days'
          WHEN age_days<=29 THEN '15-29 days' WHEN age_days<=59 THEN '30-59 days' ELSE '60+ days' END bucket,
          COUNT(*) AS count_value
        FROM atr_latest WHERE resolved_on IS NULL OR CAST(resolved_on AS DATE)>DATE '2025-07-30'
        GROUP BY bucket ORDER BY MIN(age_days)
    """).fetchall()
    fifo = _one(con, """
        SELECT COUNT(*) older_outstanding,
          COUNT(*) FILTER (WHERE EXISTS(
            SELECT 1 FROM atr_latest later WHERE later.office=older.office
              AND later.atr_date>older.atr_date AND later.resolved_on IS NOT NULL
              AND CAST(later.resolved_on AS DATE)<=DATE '2025-07-30')) fifo_exceptions
        FROM atr_latest older
        WHERE older.office IS NOT NULL AND (older.resolved_on IS NULL OR CAST(older.resolved_on AS DATE)>DATE '2025-07-30')
    """)
    buckets = suppress_breakdown([{"label": a, "value": b} for a, b in rows])
    return {
        "id": "atr", "title": "ATR queue discipline", "state": "recorded",
        "denominator": {"label": "Outstanding latest ATRs", "value": summary["outstanding"]},
        "metrics": [
            _metric("oldest-atr", "Oldest outstanding ATR", summary["oldest"], unit="days"),
            _metric("atr-to-disposal", "Median ATR-to-disposal", float(summary["median_disposal"]) if summary["median_disposal"] is not None else None, unit="days"),
            _metric("fifo-exception", "Older ATR bypassed proxy", _pct(fifo["fifo_exceptions"], fifo["older_outstanding"]), unit="percent", numerator=fifo["fifo_exceptions"], denominator=fifo["older_outstanding"]),
        ],
        "breakdown": buckets,
        "breakdownUnavailableReason": None if buckets is not None else "Withheld because a positive ATR-age cell is below 10.",
        "caveats": ["Uses the latest recorded ATR before snapshot or resolution.", "The FIFO proxy does not prove notification, download, priority, or acknowledgement."],
    }


def _load_groups(con: duckdb.DuckDBPyConnection, path: Path, table: str) -> bool:
    if not path.is_file():
        return False
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {table} AS SELECT ticket_no, duplicate_group_id, group_size "
        "FROM read_csv_auto(?, header=true, all_varchar=true)", [str(path)]
    )
    return True


def _demand(
    con: duckdb.DuckDBPyConnection,
    identity_path: Path | None,
    full_path: Path | None,
    citizen_counts: dict[str, int] | None,
) -> dict[str, Any]:
    filings = _one(con, "SELECT COUNT(*) n FROM scope_tickets WHERE created_on>=DATE '2024-07-01' AND created_on<DATE '2025-07-01'")["n"]
    if not identity_path or not _load_groups(con, identity_path, "identity_groups"):
        return {
            "id": "demand", "title": "Demand and duplication", "state": "recorded",
            "denominator": {"label": "Filings in FY 2024-25", "value": filings},
            "metrics": [_metric("filings", "Filings", filings, unit="grievances")] + [
                _metric(key, label, None, unit="grievances", note="No validated grouping artifact exists for this scope.")
                for key, label in (("problems", "Distinct problem groups"), ("citizens", "Distinct citizens"), ("duplicate-adjustment", "Duplicate adjustment"), ("repeat-groups", "Repeat-submission groups"), ("campaigns", "Multi-signatory campaign groups"))
            ],
            "breakdown": None, "breakdownUnavailableReason": None,
            "caveats": ["Dedup-derived submetrics are explicit unavailable states; filings are not silently substituted."],
        }
    has_full = bool(full_path and _load_groups(con, full_path, "full_groups"))
    row = _one(con, """
        WITH cohort AS (SELECT ticket_no FROM scope_tickets WHERE created_on>=DATE '2024-07-01' AND created_on<DATE '2025-07-01'),
        i AS (SELECT DISTINCT i.* FROM identity_groups i JOIN cohort USING(ticket_no))
        SELECT (SELECT COUNT(*) FROM cohort) filings,
          (SELECT COUNT(DISTINCT duplicate_group_id) FROM i) problems,
          (SELECT COUNT(*) FROM cohort)-(SELECT COUNT(DISTINCT duplicate_group_id) FROM i) duplicate_adjustment,
          (SELECT COUNT(DISTINCT duplicate_group_id) FROM i WHERE TRY_CAST(group_size AS INTEGER)>1) repeats
    """)
    campaigns = None
    if has_full:
        campaigns = _one(con, """
            WITH cohort AS (SELECT ticket_no FROM scope_tickets WHERE created_on>=DATE '2024-07-01' AND created_on<DATE '2025-07-01'),
            grouped AS (
              SELECT f.duplicate_group_id, COUNT(*) filings,
                     COUNT(DISTINCT i.duplicate_group_id) signatory_groups
              FROM full_groups f JOIN cohort USING(ticket_no)
              JOIN identity_groups i USING(ticket_no)
              GROUP BY f.duplicate_group_id)
            SELECT COUNT(*) campaigns FROM grouped
            WHERE filings>=200 AND signatory_groups>1
        """)["campaigns"]
    citizen_counts = citizen_counts or {}
    covered = citizen_counts.get("identityCovered")
    citizens = citizen_counts.get("distinctCitizens")
    return {
        "id": "demand", "title": "Demand and duplication", "state": "recorded",
        "denominator": {"label": "Filings in FY 2024-25", "value": row["filings"]},
        "metrics": [
            _metric("filings", "Filings", row["filings"], unit="grievances"),
            _metric("problems", "Distinct problem groups", row["problems"], unit="groups"),
            _metric("citizens", "Distinct citizens with identity", citizens, unit="citizens", numerator=covered, denominator=row["filings"], coverage=_pct(covered or 0, row["filings"]), note=None if citizens is not None else "Secure identity aggregate was not supplied."),
            _metric("duplicate-adjustment", "Duplicate adjustment", row["duplicate_adjustment"], unit="grievances"),
            _metric("repeat-groups", "Repeat-submission groups", row["repeats"], unit="groups"),
            _metric("campaigns", "Multi-signatory campaign groups", campaigns, unit="groups", note=None if campaigns is not None else "The validated text-or-identity campaign mapping is not yet available for this scope."),
        ],
        "breakdown": None, "breakdownUnavailableReason": None,
        "caveats": ["Distinct problems and repeat submissions use the governed same-person and same-text grouping.", f"Campaign groups, when available, use the existing {CAMPAIGN_THRESHOLD}-member large-bucket threshold."],
    }


def refiling_summary(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Right-censored same-group refiling counts for the FY closure cohort."""
    return _one(con, """
        WITH mapped AS (
          SELECT c.ticket_no, CAST(c.created_on AS DATE) created,
                 CAST(c.resolved_on AS DATE) closed, f.duplicate_group_id gid
          FROM scope_tickets c JOIN full_groups f USING(ticket_no)),
        closures AS (
          SELECT * FROM mapped WHERE closed IS NOT NULL
            AND created>=DATE '2024-07-01' AND created<DATE '2025-07-01'),
        filings AS (
          SELECT * FROM mapped WHERE created<=DATE '2025-07-30'),
        y AS (SELECT x.*,
          EXISTS(SELECT 1 FROM filings z WHERE z.gid=x.gid AND z.created>x.closed AND z.created<=x.closed+30) ref30,
          EXISTS(SELECT 1 FROM filings z WHERE z.gid=x.gid AND z.created>x.closed AND z.created<=x.closed+90) ref90
          FROM closures x)
        SELECT COUNT(*) FILTER(WHERE closed<=DATE '2025-06-30') den30,
          COUNT(*) FILTER(WHERE closed<=DATE '2025-06-30' AND ref30) num30,
          COUNT(*) FILTER(WHERE closed<=DATE '2025-05-01') den90,
          COUNT(*) FILTER(WHERE closed<=DATE '2025-05-01' AND ref90) num90 FROM y
    """)


def _closure(con: duckdb.DuckDBPyConnection, full_path: Path | None) -> dict[str, Any]:
    row = _one(con, """
        WITH cohort AS (SELECT ticket_no FROM scope_tickets WHERE created_on>=DATE '2024-07-01' AND created_on<DATE '2025-07-01'),
        r AS (SELECT c.* FROM closure_rung c JOIN cohort USING(ticket_no)),
        reopened AS (SELECT COUNT(DISTINCT a.ticket_no) n FROM action_history a JOIN cohort USING(ticket_no)
                     WHERE LOWER(a.action_status) LIKE '%reopen%')
        SELECT COUNT(*) resolved, SUM(on_ladder) ladder,
          COUNT(*) FILTER(WHERE rung='bare') bare,
          COUNT(*) FILTER(WHERE rung='with_action') action_recorded,
          COUNT(*) FILTER(WHERE rung='benefit') benefit_recorded,
          (SELECT n FROM reopened) reopened FROM r
    """)
    refiling_metrics = [
        _metric("refiling-30", "Same-problem refiling within 30 days", None, unit="percent", note="A validated problem-group mapping is required."),
        _metric("refiling-90", "Same-problem refiling within 90 days", None, unit="percent", note="A validated problem-group mapping is required."),
    ]
    if full_path and _load_groups(con, full_path, "full_groups"):
        rf = refiling_summary(con)
        refiling_metrics = [
            _metric("refiling-30", "Same-problem refiling within 30 days", _pct(rf["num30"], rf["den30"]), unit="percent", numerator=rf["num30"], denominator=rf["den30"]),
            _metric("refiling-90", "Same-problem refiling within 90 days", _pct(rf["num90"], rf["den90"]), unit="percent", numerator=rf["num90"], denominator=rf["den90"]),
        ]
    return {
        "id": "closure", "title": "Closure and return", "state": "recorded",
        "denominator": {"label": "All resolved in FY 2024-25 cohort", "value": row["resolved"]},
        "metrics": [
            _metric("bare-ladder", "Bare disposal / templated closures", _pct(row["bare"], row["ladder"]), unit="percent", numerator=row["bare"], denominator=row["ladder"]),
            _metric("bare-resolved", "Bare disposal / all resolved", _pct(row["bare"], row["resolved"]), unit="percent", numerator=row["bare"], denominator=row["resolved"]),
            _metric("action-recorded", "Action recorded", row["action_recorded"], unit="closures", denominator=row["ladder"]),
            _metric("benefit-recorded", "Benefit recorded", row["benefit_recorded"], unit="closures", denominator=row["ladder"]),
            _metric("reopened", "Recorded reopen events", row["reopened"], unit="grievances", denominator=row["resolved"]),
            *refiling_metrics,
        ],
        "breakdown": None, "breakdownUnavailableReason": None,
        "caveats": ["Closure wording is descriptive, not a failure rate.", "Returns are review signals, not proof that an earlier decision was wrong.", "Refiling denominators include only closures with the full follow-up window."],
    }


def build_release(
    *,
    lake_dir: Path,
    dedup_dir: Path,
    citizen_aggregates: Path | None = None,
) -> dict[str, Any]:
    con = lake.connect(lake_dir, tables=("complaints", "action_history"))
    try:
        _prepare(con)
        subcategories = _subcategory_scopes(con, CORE_SCOPES)
        catalog = _catalog(con, subcategories)
        citizens = json.loads(citizen_aggregates.read_text()) if citizen_aggregates else {}
        dashboards: dict[str, Any] = {}
        dedup_names = {
            "department-21": "panchayati_raj",
            "department-40": "ssepd",
            "handling-cm-grievance-cell": "cm_cell",
        }
        for scope in (*CORE_SCOPES, *subcategories):
            coverage = _scope_tables(con, scope)
            # A subcategory inherits its department's ticket-to-group index.
            # That is correct rather than approximate: the demand and closure
            # queries join the index to `scope_tickets`, which is already cut
            # to the subcategory, so the department-wide file is filtered to
            # this scope before anything is counted.
            base = dedup_names.get(scope.id)
            if base is None and scope.parent_id:
                base = dedup_names.get(scope.parent_id)
            identity = dedup_dir / f"{base}_dedup_groups.csv" if base else None
            full = dedup_dir / f"{base}_dedup_full_groups.csv" if base else None
            dashboards[f"{scope.id}:{PERIOD_ID}"] = {
                "scopeId": scope.id,
                "scopeLabel": scope.label,
                "scopeKind": scope.kind,
                "scopeDefinition": scope.definition,
                "periodId": PERIOD_ID,
                "periodLabel": PERIOD_LABEL,
                "snapshotDate": SNAPSHOT_DATE.isoformat(),
                "panels": [
                    _aging(con), _transfers(con), _journey(con, coverage), _atr(con),
                    _demand(con, identity, full, citizens.get(scope.id)),
                    _closure(con, identity),
                ],
            }
        for dashboard in dashboards.values():
            dashboard["panels"] = [withhold_small_panel(p) for p in dashboard["panels"]]
        return {
            "schemaVersion": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sourceFreshness": {
                "extractMaximum": SNAPSHOT_DATE.isoformat(),
                "complaintsFileModifiedAt": datetime.fromtimestamp((lake_dir / "complaints.parquet").stat().st_mtime, timezone.utc).isoformat(),
                "actionHistoryFileModifiedAt": datetime.fromtimestamp((lake_dir / "action_history.parquet").stat().st_mtime, timezone.utc).isoformat(),
            },
            "inputDigests": {
                "complaints": _digest(lake_dir / "complaints.parquet"),
                "actionHistory": _digest(lake_dir / "action_history.parquet"),
            },
            "periods": [{"id": PERIOD_ID, "label": PERIOD_LABEL, "start": PERIOD_START.isoformat(), "endExclusive": PERIOD_END.isoformat()}],
            "scopes": catalog,
            "dashboards": dashboards,
        }
    finally:
        con.close()


def _flat_rows(release: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for dashboard in release["dashboards"].values():
        for panel in dashboard["panels"]:
            if panel["state"] == "unavailable":
                rows.append({
                    "scope_id": dashboard["scopeId"], "scope_label": dashboard["scopeLabel"],
                    "period": dashboard["periodLabel"], "snapshot_date": dashboard["snapshotDate"],
                    "panel": panel["title"], "metric": panel["title"], "state": "unavailable",
                    "value": None, "unit": None, "numerator": None, "denominator": None,
                    "coverage_pct": None, "basis": None, "caveat": panel["reason"],
                })
                continue
            for metric in panel["metrics"]:
                rows.append({
                    "scope_id": dashboard["scopeId"], "scope_label": dashboard["scopeLabel"],
                    "period": dashboard["periodLabel"], "snapshot_date": dashboard["snapshotDate"],
                    "panel": panel["title"], "metric": metric["label"], "state": metric["state"],
                    "value": metric.get("value"), "unit": metric.get("unit"),
                    "numerator": metric.get("numerator"), "denominator": metric.get("denominator"),
                    "coverage_pct": metric.get("coveragePct"), "basis": metric.get("basis"),
                    "caveat": metric.get("note") or metric.get("reason"),
                })
    return rows


def publish(release: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "monitoring_dashboard_v1.json"
    csv_path = output_dir / "monitoring_results_v1.csv"
    payload = json.dumps(release, indent=2, ensure_ascii=False)
    if len(payload.encode()) > MAX_ARTIFACT_BYTES:
        raise ValueError("monitoring release exceeds the 10 MB serving limit")
    json_path.write_text(payload + "\n")
    rows = _flat_rows(release)
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake-dir", type=Path, default=directories.INTERIM)
    parser.add_argument("--dedup-dir", type=Path, default=Path("outputs/dedup"))
    parser.add_argument("--citizen-aggregates", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/monitoring"))
    args = parser.parse_args()
    paths = publish(build_release(lake_dir=args.lake_dir, dedup_dir=args.dedup_dir, citizen_aggregates=args.citizen_aggregates), args.output_dir)
    print("\n".join(str(path) for path in paths))


if __name__ == "__main__":
    main()
