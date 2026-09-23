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
from janasunani.analytics.findings.discards import (
    FAMILY_LABELS as DISCARD_FAMILY_LABELS,
    TEMPLATES as DISCARD_TEMPLATES,
    _NORMALIZED_REMARK,
    _lookup_values as _discard_lookup_values,
)
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
# problem, an identity key is not a verified person, and the loop and follow-up
# measures are the proxies their labels already say they are.
# Everything else counts what the record directly contains.
PROXY_METRICS = frozenset({
    "loop-rate", "followup-proxy",
    "problems", "citizens", "duplicate-adjustment", "repeat-groups", "campaigns",
    "bare-ladder", "bare-resolved", "action-recorded", "benefit-recorded",
    "refiling-30", "refiling-90",
    # Inferred from the order of recorded events, not recorded as a review.
    "review-done", "closed-without-review",
    # Coverage of a stand-in field: the assigned workflow for whether review
    # is required, subcategory for scheme or service.
    "rec-review-required", "rec-scheme",
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
          FROM action_history WHERE action_taken_date < TIMESTAMP '2025-07-31' GROUP BY ticket_no
        ), open AS (
          SELECT g.ticket_no,
            DATE '2025-07-30' - CAST(g.created_on AS DATE) age_days,
            DATE '2025-07-30' - CAST(COALESCE(GREATEST(CASE WHEN g.last_updated_on < TIMESTAMP '2025-07-31' THEN g.last_updated_on END, l.latest_action), g.created_on) AS DATE) inactive_days,
            g.escalation_date
          FROM complaints g JOIN scope_tickets s USING (ticket_no)
          LEFT JOIN latest l USING (ticket_no)
          WHERE g.created_on < DATE '2025-07-31'
            -- Open on the snapshot: resolved after it, or not closed at all. A
            -- later status is not the status on the snapshot.
            AND ((g.resolved_on IS NULL AND COALESCE(g.status, '') NOT IN ('Disposed', 'Discard'))
                 OR CAST(g.resolved_on AS DATE) > DATE '2025-07-30')
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
        WITH latest AS (SELECT ticket_no, MAX(action_taken_date) latest_action FROM action_history WHERE action_taken_date < TIMESTAMP '2025-07-31' GROUP BY ticket_no),
        open AS (
          SELECT DATE '2025-07-30' - CAST(COALESCE(GREATEST(CASE WHEN g.last_updated_on < TIMESTAMP '2025-07-31' THEN g.last_updated_on END, l.latest_action), g.created_on) AS DATE) inactive_days,
                 g.escalation_date
          FROM complaints g JOIN scope_tickets s USING(ticket_no) LEFT JOIN latest l USING(ticket_no)
          WHERE g.created_on < DATE '2025-07-31' AND ((g.resolved_on IS NULL AND COALESCE(g.status, '') NOT IN ('Disposed', 'Discard'))
                 OR CAST(g.resolved_on AS DATE) > DATE '2025-07-30'))
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


# The portal's own "Take action" list for a reviewer returning an ATR, as the
# CM Grievance Cell screen shows it (photo 20, 11 Aug 2026). Matched exactly
# after the same normalisation as the discard templates; other wording is
# counted as "Other wording", never guessed at.
REVERT_TEMPLATES = {
    "required more clarification": "More clarification required",
    "please furnish the final atr": "Final ATR requested",
    "further action need to be taken": "Further action needed",
    "please enclose a legible copy of the atr": "Legible ATR copy requested",
    "please submit the atr on the grievance petition": "ATR on the petition requested",
    "no action has been taken in the meantime": "No action taken meanwhile",
    "please re-enquire and take appropriate action early": "Re-enquiry requested",
    "please cause a factual enquiry to the issue raised": "Factual enquiry requested",
}


def _atr(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """ATR submission and the review the assigned workflow requires (note §1.1-1.3).

    The workflow is the case's ``all_esc_user`` chain, stored field office
    first, as the portal's "Define Workflow" list shows it (``BDO --> Collector
    --> CMO``). The first node acts and "Replies"; each later node receives the
    ATR in turn and the last is the office that defined the workflow. A chain
    of three or more nodes therefore puts at least one office between them,
    which reviews before the ATR goes on: that is de jure review. ``Replied``
    is the ATR moving up; ``Reopen`` after it is a reviewer sending it back.
    """
    revert_values = ", ".join(f"('{k}', {_sql_str(v)})" for k, v in REVERT_TEMPLATES.items())
    con.execute("""
        CREATE OR REPLACE TEMP TABLE atr_cases AS
        WITH cohort AS (
          SELECT s.ticket_no, c.status, c.resolved_on,
            len(list_filter(string_split(COALESCE(c.all_esc_user, ''), ','), x -> trim(x) <> '')) nodes
          FROM scope_tickets s JOIN complaints c USING(ticket_no)
          WHERE s.created_on>=DATE '2024-07-01' AND s.created_on<DATE '2025-07-01'),
        acts AS (
          SELECT o.ticket_no, o.id, o.action_taken_date d, o.action_status st, o.code office
          FROM acting_office o JOIN cohort c USING(ticket_no)
          WHERE o.action_taken_date < TIMESTAMP '2025-07-31'
            -- Review happens before closure: a closed case's later actions
            -- are not evidence that its ATR was reviewed.
            AND (c.resolved_on IS NULL OR o.action_taken_date <= c.resolved_on)),
        -- Same-day rows order by id, as everywhere events are sequenced.
        first_reply AS (SELECT ticket_no, MIN(d) fr, arg_min(id, (d, id)) fr_id FROM acts WHERE st='Replied' GROUP BY 1),
        per_case AS (
          SELECT a.ticket_no,
            COUNT(DISTINCT a.office) FILTER(WHERE a.st='Replied') repliers,
            BOOL_OR(a.st='Reopen' AND (a.d, a.id) > (f.fr, f.fr_id)) sent_back,
            arg_max(a.st, (a.d, a.id)) last_status,
            MAX(a.d) last_action
          FROM acts a LEFT JOIN first_reply f USING(ticket_no)
          GROUP BY a.ticket_no)
        SELECT c.ticket_no, c.resolved_on, c.nodes, c.nodes >= 3 required,
          f.fr IS NOT NULL replied, f.fr, f.fr_id,
          COALESCE(p.sent_back, FALSE) sent_back,
          f.fr IS NOT NULL AND (p.repliers >= 2 OR COALESCE(p.sent_back, FALSE)) reviewed,
          c.status='Disposed' AND CAST(c.resolved_on AS DATE)<=DATE '2025-07-30' closed,
          -- Open on the snapshot, as in _aging: a later status is not the
          -- status on the snapshot.
          ((c.resolved_on IS NULL AND COALESCE(c.status, '') NOT IN ('Disposed','Discard'))
            OR CAST(c.resolved_on AS DATE)>DATE '2025-07-30') AND p.last_status='Replied' atr_waiting,
          DATE '2025-07-30'-CAST(p.last_action AS DATE) wait_days
        FROM cohort c LEFT JOIN first_reply f USING(ticket_no) LEFT JOIN per_case p USING(ticket_no)
    """)
    row = _one(con, """
        SELECT COUNT(*) filings,
          COUNT(*) FILTER(WHERE nodes>0) with_workflow,
          COUNT(*) FILTER(WHERE required) required,
          COUNT(*) FILTER(WHERE replied) replied,
          COUNT(*) FILTER(WHERE required AND closed) required_closed,
          COUNT(*) FILTER(WHERE required AND closed AND reviewed) required_closed_reviewed,
          COUNT(*) FILTER(WHERE required AND closed AND NOT reviewed) closed_without_review,
          COUNT(*) FILTER(WHERE replied AND sent_back) sent_back,
          COUNT(*) FILTER(WHERE atr_waiting) waiting,
          MEDIAN(wait_days) FILTER(WHERE atr_waiting) median_wait
        FROM atr_cases
    """)
    ages = con.execute("""
        SELECT CASE WHEN wait_days<=6 THEN '0-6 days' WHEN wait_days<=14 THEN '7-14 days'
          WHEN wait_days<=29 THEN '15-29 days' WHEN wait_days<=59 THEN '30-59 days' ELSE '60+ days' END bucket,
          COUNT(*) AS count_value
        FROM atr_cases WHERE atr_waiting GROUP BY bucket ORDER BY MIN(wait_days)
    """).fetchall()
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE atr_backs AS
        WITH revert(template, label) AS (VALUES {revert_values})
        -- One reason per grievance, the first send-back's, so the rows add
        -- up to the grievances sent back.
        SELECT a.ticket_no, arg_min(COALESCE(r.label, 'Other wording'), (a.action_taken_date, a.id)) AS reason
        FROM action_history a JOIN atr_cases c USING(ticket_no)
        LEFT JOIN revert r ON r.template = {_NORMALIZED_REMARK}
        WHERE c.replied AND a.action_status='Reopen' AND (a.action_taken_date, a.id) > (c.fr, c.fr_id)
          AND a.action_taken_date < TIMESTAMP '2025-07-31'
          AND (c.resolved_on IS NULL OR a.action_taken_date <= c.resolved_on)
        GROUP BY a.ticket_no
    """)
    reasons = con.execute(
        "SELECT reason, COUNT(*) n FROM atr_backs GROUP BY reason "
        "ORDER BY reason='Other wording', n DESC, reason"
    ).fetchall()
    standard = _one(con, """
        SELECT COUNT(DISTINCT ticket_no) sent_back,
          COUNT(DISTINCT ticket_no) FILTER(WHERE reason<>'Other wording') standard
        FROM atr_backs
    """)
    buckets = suppress_breakdown([{"label": a, "value": b} for a, b in ages])
    tables = None
    # Any small reason row is withheld with the whole table: the others and
    # the send-back total would give it away.
    reasons_withheld = any(0 < n < MIN_CELL for _label, n in reasons)
    if reasons and not reasons_withheld:
        tables = [{
            "title": "Why ATRs were sent back",
            "columns": [{"label": "Grievances", "unit": "grievances"}],
            "rows": [{"label": label, "values": [n]} for label, n in reasons],
        }]
    return {
        "id": "atr", "title": "ATRs and review", "state": "recorded",
        "denominator": {"label": "Grievances created in FY 2024-25", "value": row["filings"]},
        "metrics": [
            _metric("review-required", "Workflow requires review", _pct(row["required"], row["with_workflow"]), unit="percent", numerator=row["required"], denominator=row["with_workflow"], note="Three or more offices in the assigned workflow."),
            _metric("atr-replied", "ATR submitted", _pct(row["replied"], row["filings"]), unit="percent", numerator=row["replied"], denominator=row["filings"]),
            _metric("review-done", "Required review happened", _pct(row["required_closed_reviewed"], row["required_closed"]), unit="percent", numerator=row["required_closed_reviewed"], denominator=row["required_closed"]),
            _metric("closed-without-review", "Closed without the required review", _pct(row["closed_without_review"], row["required_closed"]), unit="percent", numerator=row["closed_without_review"], denominator=row["required_closed"]),
            _metric("atr-sent-back", "ATR sent back by a reviewer", _pct(row["sent_back"], row["replied"]), unit="percent", numerator=row["sent_back"], denominator=row["replied"]),
            _metric("atr-standard-reason", "Send-backs with a standard reason", _pct(standard["standard"], standard["sent_back"]), unit="percent", numerator=standard["standard"], denominator=standard["sent_back"]),
            _metric("atr-waiting", "ATRs waiting for the next office", row["waiting"], unit="grievances", denominator=row["filings"]),
            _metric("atr-wait", "Median wait of those ATRs", float(row["median_wait"]) if row["median_wait"] is not None else None, unit="days", denominator=row["waiting"], note=None if row["median_wait"] is not None else "No ATR is waiting."),
        ],
        "breakdown": buckets,
        "breakdownUnavailableReason": None if buckets is not None else "Withheld because a positive waiting-ATR cell is below 10.",
        "tables": tables,
        "caveats": [
            "Review is required when the assigned workflow has three or more offices: the ones between the field office and the office that assigned it review the ATR. The workflow is the current one; an earlier workflow is not kept.",
            "An ATR is 'submitted' when the case records Replied. Review happened when a second office replied or a reviewer sent it back before closure.",
            "'Reopen' after an ATR is a reviewer sending it back, not a citizen reopening the case.",
            "The breakdown is how long waiting ATRs have waited since the last action.",
            *(["The reasons ATRs were sent back are withheld: at least one reason covers fewer than 10 grievances."] if reasons_withheld else []),
        ],
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
            _metric("reopened", "Recorded Reopen events", row["reopened"], unit="grievances", denominator=row["resolved"], note="Most Reopen events follow an ATR and are a reviewer sending it back (see the ATR panel); some are citizen reopenings. The record does not separate them."),
            *refiling_metrics,
        ],
        "breakdown": None, "breakdownUnavailableReason": None,
        "caveats": ["Closure wording is descriptive, not a failure rate.", "Returns are review signals, not proof that an earlier decision was wrong.", "Refiling denominators include only closures with the full follow-up window."],
    }


def _discards(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Discard reasons and their timing, reported together (note §2.2).

    A reason is one of the eight governed officer templates in
    ``analytics/findings/discards.py``; nothing else is read as a reason. Each
    grievance counts once, at its first recognised reason before the snapshot.
    Timing is whether a recorded transfer came before that reason: the extract
    has no event for "officer action started" or "earlier ticket located".
    """
    row = _one(con, """
        WITH cohort AS (
          SELECT s.ticket_no, c.status FROM scope_tickets s JOIN complaints c USING(ticket_no)
          WHERE s.created_on>=DATE '2024-07-01' AND s.created_on<DATE '2025-07-01')
        SELECT (SELECT COUNT(*) FROM cohort) filings,
          (SELECT COUNT(*) FROM cohort WHERE status='Discard') discarded
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE discard_events AS
        WITH cohort AS (
          SELECT s.ticket_no, c.status FROM scope_tickets s JOIN complaints c USING(ticket_no)
          WHERE s.created_on>=DATE '2024-07-01' AND s.created_on<DATE '2025-07-01'),
        discard_template(family, template) AS (VALUES {_discard_lookup_values()}),
        acts AS (
          SELECT a.id, a.ticket_no, a.action_taken_date, a.action_status, d.family
          FROM action_history a JOIN cohort USING(ticket_no)
          LEFT JOIN discard_template d ON d.template = {_NORMALIZED_REMARK}
          WHERE a.action_taken_date < TIMESTAMP '2025-07-31'),
        first_reason AS (
          SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY ticket_no ORDER BY action_taken_date, id) rn
            FROM acts WHERE family IS NOT NULL) WHERE rn=1)
        SELECT e.ticket_no, e.family, c.status,
          EXISTS(SELECT 1 FROM acts t WHERE t.ticket_no=e.ticket_no
            AND t.action_status='Complaint Transfer'
            AND (t.action_taken_date<e.action_taken_date
                 OR (t.action_taken_date=e.action_taken_date AND t.id<e.id))) after_transfer
        FROM first_reason e JOIN cohort c USING(ticket_no)
    """)
    events = _one(con, """
        SELECT COUNT(*) with_reason,
          COUNT(*) FILTER(WHERE status='Discard') discarded_with_reason,
          COUNT(*) FILTER(WHERE after_transfer) after_transfer
        FROM discard_events
    """)
    counts = dict(
        ((family, after), n) for family, after, n in con.execute(
            "SELECT family, after_transfer, COUNT(*) FROM discard_events GROUP BY ALL"
        ).fetchall()
    )
    breakdown = suppress_breakdown([
        {"label": f"{DISCARD_FAMILY_LABELS[family]} · {timing}", "value": counts[(family, after)]}
        for family in DISCARD_TEMPLATES
        for after, timing in ((False, "before any transfer"), (True, "after a transfer"))
        if (family, after) in counts
    ])
    return {
        "id": "discards", "title": "Discard reasons and timing", "state": "recorded",
        "denominator": {"label": "Grievances created in FY 2024-25", "value": row["filings"]},
        "metrics": [
            _metric("discard-rate", "Discard status", _pct(row["discarded"], row["filings"]), unit="percent", numerator=row["discarded"], denominator=row["filings"]),
            _metric("discard-reason-recognised", "Discards with a recognised reason", _pct(events["discarded_with_reason"], row["discarded"]), unit="percent", numerator=events["discarded_with_reason"], denominator=row["discarded"]),
            _metric("discard-after-transfer", "Reason recorded after a transfer", _pct(events["after_transfer"], events["with_reason"]), unit="percent", numerator=events["after_transfer"], denominator=events["with_reason"]),
        ],
        "breakdown": breakdown,
        "breakdownUnavailableReason": None if breakdown is not None else "Withheld because a positive reason-and-timing cell is below 10.",
        "caveats": [
            "Reasons are the eight governed officer templates; other wording is not read as a reason.",
            "Rows count grievances at their first recognised reason, whatever their final status.",
            "Timing is only before or after a recorded transfer. The extract does not record when officer action started or when an earlier ticket was found.",
            "Each reason is a different situation, not one operational failure.",
        ],
    }


OFFICE_TABLE_TOP_N = 12


def _cell(value: int) -> int | None:
    """A count cell, withheld at 1-9 like every other published count."""
    return None if 0 < value < MIN_CELL else value


def _rate_cell(numerator: int, denominator: int) -> float | None:
    if any(0 < cell < MIN_CELL for cell in (numerator, denominator)):
        return None
    return _pct(numerator, denominator)


def _drilldown_rows(
    rows: list[tuple[Any, ...]],
    columns: Sequence[tuple[int, int | None]],
    other_label: str,
) -> list[dict[str, Any]]:
    """Top rows by the first count, the rest folded into one "other" row.

    ``rows`` are ``(label, count, count, ...)`` ordered by workload; each
    column is ``(count index)`` for a count or ``(numerator, denominator)``
    for a rate. A row whose first count is under the minimum cell folds into
    "other" rather than printing a wall of withheld cells.
    """
    # ponytail: per-cell suppression only; a department total elsewhere on the
    # page can difference out a withheld "other" cell. Add complementary
    # suppression if these tables leave the internal dashboard.
    kept = [r for r in rows[:OFFICE_TABLE_TOP_N] if r[1] >= MIN_CELL]
    folded = [r for r in rows if r not in kept]
    if folded:
        width = len(rows[0])
        kept.append((other_label, *(sum(r[i] for r in folded) for i in range(1, width))))
    out = []
    for label, *counts in kept:
        values: list[int | float | None] = []
        for numerator, denominator in columns:
            values.append(
                _cell(counts[numerator]) if denominator is None
                else _rate_cell(counts[numerator], counts[denominator])
            )
        out.append({"label": label, "values": values})
    return out


def _offices(con: duckdb.DuckDBPyConnection, scope: ScopeSpec) -> dict[str, Any]:
    """A department's workload by district and by the office holding it (§3.3).

    Rates sit beside the counts they are rates of, so a small district's
    high share is read against its size. The tables are not a ranking: they
    are ordered by workload, and an office's rate reflects its caseload mix
    as much as its conduct.
    """
    if scope.kind != "department":
        return {
            "id": "offices", "title": "By district and office", "state": "unavailable",
            "reason": "Published for department views only.",
            "caveats": ["A department is where district and office comparisons are like for like."],
        }
    con.execute("""
        CREATE OR REPLACE TEMP TABLE office_base AS
        WITH latest AS (
          SELECT ticket_no, MAX(action_taken_date) latest_action,
                 arg_max(id, (action_taken_date, id)) last_id
          FROM action_history WHERE action_taken_date < TIMESTAMP '2025-07-31' GROUP BY ticket_no),
        transferred AS (
          SELECT DISTINCT ticket_no FROM action_history
          WHERE action_status='Complaint Transfer' AND action_taken_date < TIMESTAMP '2025-07-31')
        SELECT COALESCE(NULLIF(trim(g.district), ''), 'District not recorded') district,
          COALESCE(n.role_name, 'Other or unnamed office') office,
          (g.created_on>=DATE '2024-07-01' AND g.created_on<DATE '2025-07-01') in_fy,
          t.ticket_no IS NOT NULL is_transferred,
          (g.created_on<DATE '2025-07-31'
            AND ((g.resolved_on IS NULL AND COALESCE(g.status, '') NOT IN ('Disposed', 'Discard'))
                 OR CAST(g.resolved_on AS DATE) > DATE '2025-07-30')) is_open,
          DATE '2025-07-30'-CAST(g.created_on AS DATE) age_days,
          DATE '2025-07-30'-CAST(COALESCE(GREATEST(CASE WHEN g.last_updated_on < TIMESTAMP '2025-07-31' THEN g.last_updated_on END, l.latest_action), g.created_on) AS DATE) inactive_days
        FROM complaints g JOIN scope_tickets s USING(ticket_no)
        LEFT JOIN latest l USING(ticket_no)
        LEFT JOIN acting_office_named n ON n.id=l.last_id
        LEFT JOIN transferred t USING(ticket_no)
    """)
    district = con.execute("""
        SELECT district,
          COUNT(*) FILTER(WHERE is_open) open,
          COUNT(*) FILTER(WHERE is_open AND age_days>=30) open30,
          COUNT(*) FILTER(WHERE is_open AND inactive_days>=7) inactive7,
          COUNT(*) FILTER(WHERE in_fy) filed,
          COUNT(*) FILTER(WHERE in_fy AND is_transferred) transferred
        FROM office_base GROUP BY district
        ORDER BY open DESC, filed DESC, district
    """).fetchall()
    office = con.execute("""
        SELECT office,
          COUNT(*) open,
          COUNT(*) FILTER(WHERE age_days>=30) open30,
          COUNT(*) FILTER(WHERE inactive_days>=7) inactive7
        FROM office_base WHERE is_open GROUP BY office
        ORDER BY open DESC, office
    """).fetchall()
    open_columns = [
        {"label": "Open now", "unit": "grievances"},
        {"label": "Open 30+ days", "unit": "percent"},
        {"label": "No action 7+ days", "unit": "percent"},
    ]
    tables = []
    if district:
        tables.append({
            "title": "By district",
            "columns": [*open_columns,
                        {"label": "Filed in FY", "unit": "grievances"},
                        {"label": "Transferred", "unit": "percent"}],
            "rows": _drilldown_rows(district, [(0, None), (1, 0), (2, 0), (3, None), (4, 3)], "Other districts"),
        })
    if office:
        tables.append({
            "title": "Open cases by the role that acted last",
            "columns": open_columns,
            "rows": _drilldown_rows(office, [(0, None), (1, 0), (2, 0)], "Other roles"),
        })
    total_open = sum(r[1] for r in district)
    return {
        "id": "offices", "title": "By district and office", "state": "recorded",
        "denominator": {"label": "Open at 30 July 2025", "value": total_open},
        "metrics": [],
        "breakdown": None, "breakdownUnavailableReason": None,
        "tables": tables,
        "caveats": [
            "Ordered by workload, not ranked. A rate reflects the caseload an office receives as well as how it handles it.",
            "The role shown is the one on the latest recorded action, which may be the office that forwarded the case rather than the one now holding it. Offices are grouped by role across the department: every Block Development Officer is one row.",
            f"The {OFFICE_TABLE_TOP_N} largest rows are shown; the rest, and any under {MIN_CELL} open cases, are folded into the last row. Cells under {MIN_CELL} are withheld.",
        ],
    }


# Concept note §6 fields the extract has no column or event for, each with the
# measure recording it would make possible. Order follows the note.
UNRECORDED_FIELDS = (
    ("rec-earlier-reference", "Reference to an earlier ticket", "separating follow-ups from repeats"),
    ("rec-candidate-relationship", "Candidate relationship to an earlier ticket", "duplicate and follow-up counts by label"),
    ("rec-detection-evidence", "Detection evidence", "checking why a filing was linked"),
    ("rec-confidence", "Confidence score and model or rule version", "label error rates by version"),
    ("rec-reviewed-relationship", "Reviewed relationship label and reason", "precision of the candidate labels"),
    ("rec-operational-action", "Operational action chosen, apart from the label", "what happens to each label"),
    ("rec-closure-reason", "Fixed closure reason", "closure quality without reading wording"),
    ("rec-closure-evidence", "Supporting evidence at closure", "which closures are evidenced"),
    ("rec-route-change-reason", "Reason for changing a category or route", "routing override analysis"),
    ("rec-citizen-contact", "Citizen contact before closure", "whether citizens were reached"),
    ("rec-citizen-feedback", "Citizen feedback after closure", "satisfaction by office and category"),
)


def _recording(con: duckdb.DuckDBPyConnection, discards: dict[str, Any], atr: dict[str, Any]) -> dict[str, Any]:
    """Which note §6 fields the source records, and how completely (§6).

    A field the extract holds is published as the share of FY filings that
    carry it. A field it cannot hold is an explicit unavailable metric naming
    what recording it would make measurable. Fields that exist only in part
    say which part is missing in their note.
    """
    row = _one(con, """
        WITH cohort AS (
          SELECT c.* FROM complaints c JOIN scope_tickets s USING(ticket_no)
          WHERE s.created_on>=DATE '2024-07-01' AND s.created_on<DATE '2025-07-01'),
        acted AS (
          SELECT a.ticket_no, a.action_status FROM action_history a JOIN cohort USING(ticket_no)
          WHERE a.action_taken_date < TIMESTAMP '2025-07-31')
        SELECT COUNT(*) filings,
          -- Each field counts if its label or its code is usable. A blank
          -- label and a zero code are missing values, as elsewhere in analytics.
          COUNT(*) FILTER(WHERE (NULLIF(trim(mode), '') IS NOT NULL OR NULLIF(mode_id, 0) IS NOT NULL)
                                AND created_on IS NOT NULL) entry,
          COUNT(*) FILTER(WHERE NULLIF(category_id, 0) IS NOT NULL OR NULLIF(trim(category), '') IS NOT NULL) category,
          COUNT(*) FILTER(WHERE NULLIF(trim(subcategory), '') IS NOT NULL OR NULLIF(subcategory_id, 0) IS NOT NULL) subcategory,
          COUNT(*) FILTER(WHERE trim(COALESCE(all_esc_user, '')) <> '') workflow,
          -- Assignment is dated on the complaint as well as in the history.
          (SELECT COUNT(*) FROM cohort c WHERE
             c.assigned_on < TIMESTAMP '2025-07-31' OR c.tagged_date < TIMESTAMP '2025-07-31'
             OR c.ticket_no IN (SELECT ticket_no FROM acted WHERE action_status IN
               ('Forwarded To Subordinate', 'Forward', 'Forwarded', 'Complaint Transfer'))) dated_action
        FROM cohort
    """)
    n = row["filings"]

    def share(metric_id: str, label: str, count: int, note: str | None = None, denominator: int = n) -> dict[str, Any]:
        return _metric(metric_id, label, _pct(count, denominator), unit="percent", numerator=count, denominator=denominator, note=note)

    def reuse(panel: dict[str, Any], source_id: str, metric_id: str, label: str, note: str, missing: str) -> dict[str, Any]:
        """A figure another panel already publishes, relabelled for this list."""
        metric = next((m for m in panel.get("metrics", []) if m["id"] == source_id), None)
        if metric and metric["state"] == "recorded":
            return {**metric, "id": metric_id, "label": label, "note": note}
        return _metric(metric_id, label, None, unit="percent", note=(metric or {}).get("reason") or missing)

    return {
        "id": "recording", "title": "What the source records", "state": "recorded",
        "denominator": {"label": "Grievances created in FY 2024-25", "value": n},
        "metrics": [
            share("rec-entry", "Entry channel and date", row["entry"]),
            share("rec-classification", "Classification", row["category"], "Only the current category; later changes are not recorded as events."),
            share("rec-events", "Dated assignment and transfer events", row["dated_action"], "Assignment dates on the complaint or in the action history. Returns are inferred from the office sequence, not recorded as events."),
            reuse(discards, "discard-reason-recognised", "rec-discard-reason", "Discard reason",
                  "Share of discards with one of the eight standard reasons. Timing is known only relative to transfers.",
                  "No discards in this scope."),
            reuse(atr, "atr-replied", "rec-atr", "ATR request, receipt and closure events",
                  "Submission is recorded as Replied; request, receipt and acceptance are not separate dated events.",
                  "No ATR was submitted in this scope."),
            share("rec-review-required", "Whether review is required", row["workflow"],
                  "Read from the assigned workflow (three or more offices); the portal holds no review flag."),
            reuse(atr, "atr-standard-reason", "rec-review-event", "Review date, reviewer role, decision and reason",
                  "Of ATRs sent back (not of all filings): the share whose reason is a standard one. A send-back is dated and by an office; acceptance is not recorded as a decision.",
                  "No ATR was sent back in this scope."),
            share("rec-scheme", "Scheme or service", row["subcategory"], "Subcategory often names the scheme; there is no scheme or service field."),
            *(
                _metric(metric_id, label, None, unit="percent", note=f"Not recorded. Would make possible: {unlocks}.")
                for metric_id, label, unlocks in UNRECORDED_FIELDS
            ),
        ],
        "breakdown": None, "breakdownUnavailableReason": None,
        "caveats": [
            "Shares are of FY 2024-25 filings in this scope, except discard reason (of discards) and the review decision (of ATRs sent back).",
            "A field that is present can still be recorded inconsistently; presence is not quality.",
        ],
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
            discards = _discards(con)
            atr = _atr(con)
            dashboards[f"{scope.id}:{PERIOD_ID}"] = {
                "scopeId": scope.id,
                "scopeLabel": scope.label,
                "scopeKind": scope.kind,
                "scopeDefinition": scope.definition,
                "periodId": PERIOD_ID,
                "periodLabel": PERIOD_LABEL,
                "snapshotDate": SNAPSHOT_DATE.isoformat(),
                "panels": [
                    _aging(con), _transfers(con), _journey(con, coverage), atr,
                    _demand(con, identity, full, citizens.get(scope.id)),
                    _closure(con, identity),
                    discards,
                    _recording(con, discards, atr),
                    _offices(con, scope),
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
            # Drill-down cells, one row per cell, so the review CSV carries
            # every published figure and not only the headline metrics.
            for table in panel.get("tables") or []:
                for table_row in table["rows"]:
                    for column, value in zip(table["columns"], table_row["values"], strict=True):
                        rows.append({
                            "scope_id": dashboard["scopeId"], "scope_label": dashboard["scopeLabel"],
                            "period": dashboard["periodLabel"], "snapshot_date": dashboard["snapshotDate"],
                            "panel": panel["title"],
                            "metric": f"{table['title']} · {table_row['label']} · {column['label']}",
                            "state": "recorded" if value is not None else "unavailable",
                            "value": value, "unit": column["unit"],
                            "numerator": None, "denominator": None, "coverage_pct": None,
                            "basis": "direct",
                            "caveat": None if value is not None else f"Withheld (under {MIN_CELL}) or nothing to divide by.",
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
