"""Elapsed time between recorded handling steps (phase 1: descriptive only).

Runs the ``handoff`` mart over the Parquet lake and writes its aggregate
tables plus a presentable Markdown fragment to ``outputs/findings/``. Like
``closure.py`` this is an **Insight**, not a capability: the deliverable is
the view definition itself, handed over for the department to run against
their own database. The Python here is the reporting wrapper.

**THIS IS NOT THE WITHDRAWN ROUTING-SAVINGS CLAIM.** "Better routing saves
11-23 days per case" was withdrawn on 23 Aug (commits ``879c24c``,
``365e3b4``) after failing temporal replication; its archived artifacts sit
under ``docs/experiments/superseded/`` and must never be quoted. That work
was causal, counterfactual, and read the de jure route (``dept_id`` +
``vchAllEscUser``). This module reads ``action_history`` only -- the record
of what actually happened -- and computes no counterfactual. Every rendered
fragment below says "elapsed time between recorded steps", never "delay",
"time lost" or "saving".

Phase 1 scope: a descriptive distribution over completed gaps (both endpoints
observed). No IPCW, no RMST, no survival correction -- see ``handoff.sql``'s
header for why a completed gap needs none, and why the per-ticket total
(which would) is out of scope here.

Caveats every render carries, spelled out in full in ``DESCRIPTIVE_CAVEAT``:

1. De facto handling, not the routing decision.
2. Not causal, not a saving. No counterfactual is computed.
3. Dedup collapse can move counts and duration quantiles in either direction.
   ``handoff_dedup_sensitivity`` compares subpopulations; it is not a bound or
   correction because the collapsed rows are gone (see the mart header).
4. Rows missing a ticket identifier or date are dropped and counted separately.
5. Inverted timestamps bucketed ``invalid``, count reported
   (``invalid_order_intervals``).
6. Hops cannot be labelled by role: ``action_taken_by`` is free text, never
   joined to a role table.
7. Chain labels are unusable as strata: this mart never reads ``complaints``
   and does not stratify by department.
8. A gap is not idle time. It can include field enquiry, statutory waiting
   periods, and citizen response.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import polars as pl
from loguru import logger

from janasunani.analytics.marts import mart_sql, open_lake
from janasunani.config import OUTPUTS_DIR

MART = "handoff"

# Depends on action_type.sql's action_history_typed view -- install both, in
# order, on every connection (see handoff.sql's header).
DEPENDS_ON = ("action_type",)

# The shareable finding. `handoff_ordered`, `handoff_intervals`,
# `handoff_ticket_year` and `handoff_ticket_summary` stay unwritten: they are
# per-ticket, so they are the ones that must not become an output file --
# same discipline as `closure_closing_action` / `closure_rung` in closure.py.
FINDING_VIEWS = (
    "handoff_coverage_summary",
    "handoff_gap_by_from_type",
    "handoff_forwarded_delegated_by_year",
    "handoff_dedup_sensitivity",
)

# Deterministic ordering per view, so reruns diff cleanly.
_ORDER_BY = {
    "handoff_gap_by_from_type": " ORDER BY from_action_type",
    "handoff_forwarded_delegated_by_year": " ORDER BY ticket_creation_year_proxy",
    "handoff_dedup_sensitivity": " ORDER BY population",
}

# Carried into every rendering of this finding. Not a footnote.
DESCRIPTIVE_CAVEAT = (
    "This is **elapsed time between recorded steps**, not a delay, not time "
    "lost, and not a saving. It measures the realised event stream (de facto "
    "handling), not the assigned route, and computes no counterfactual -- it "
    "is not the withdrawn routing-savings claim. A gap can include field "
    "enquiry, statutory waiting periods, and citizen response, so it is not "
    "idle time. `action_taken_by` is free text with no link to a role table, "
    "so hops cannot be labelled by who handled them, and this mart does not "
    "stratify by department. `action_history_uniq` can collapse genuinely distinct "
    "hand-offs sharing the same officer, status and templated remark into one "
    "recorded row. The dropped row is gone, so its effect can have either "
    "direction; `handoff_dedup_sensitivity` compares subpopulations and is "
    "not a bound or correction."
)

# Below this, the ladder of coverage checks should be treated as evidence the
# mart's assumptions have drifted for this corpus, not proof they are wrong.
MIN_INVALID_ORDER_SHARE_PCT = 5.0


def sql_text() -> str:
    """The view definitions, as handed over. Run action_type.sql first."""
    return mart_sql(MART)


def compute(lake_dir: Optional[Path] = None) -> dict[str, pl.DataFrame]:
    """Install the mart over the lake and return every reportable table."""
    con = open_lake(
        *DEPENDS_ON,
        MART,
        lake_dir=lake_dir,
        tables=("action_history",),
    )
    try:
        return {
            view: con.execute(f"SELECT * FROM {view}{_ORDER_BY.get(view, '')}").pl()  # noqa: S608
            for view in FINDING_VIEWS
        }
    finally:
        con.close()


def _n(value: Optional[int]) -> str:
    return "n/a" if value is None else f"{int(value):,}"


def _days(value) -> str:
    return "n/a" if value is None else f"{float(value):.1f}"


def invalid_order_share_pct(tables: dict[str, pl.DataFrame]) -> Optional[float]:
    """Share of emitted intervals whose ordering could not be trusted."""
    row = tables["handoff_coverage_summary"].row(0, named=True)
    emitted = row["emitted_intervals"]
    if not emitted:
        return None
    return 100.0 * row["invalid_order_intervals"] / emitted


def check_data_quality(tables: dict[str, pl.DataFrame]) -> Optional[str]:
    """Warn (does not fail the build) when invalid-order intervals dominate.

    Unlike closure.py's ladder-coverage guard this never refuses to publish:
    a high invalid-order share is itself part of what this descriptive mart
    is meant to surface, not a sign the query is broken. It is surfaced so a
    reader checks the coverage table before trusting the headline.
    """
    share = invalid_order_share_pct(tables)
    if share is not None and share > MIN_INVALID_ORDER_SHARE_PCT:
        return (
            f"{share:.1f}% of emitted intervals have an invalid event order "
            "(a later-claimed action_taken_date recorded before an earlier "
            "one). Read handoff_coverage_summary before quoting the headline."
        )
    return None


def render_markdown(tables: dict[str, pl.DataFrame]) -> str:
    """The presentable fragment. The caveat always appears."""
    cov = tables["handoff_coverage_summary"].row(0, named=True)

    lines = [
        "## Elapsed time between recorded handling steps",
        "",
        "**Insight, phase 1.** Descriptive only: the distribution of time "
        "between one recorded action_history row and the next on the same "
        "ticket. No model, no counterfactual, no routing decision read.",
        "",
        f"⚠️ {DESCRIPTIVE_CAVEAT}",
        "",
        "### Coverage",
        "",
        "| Action rows | Dropped (missing ticket) | Dropped (undated) | Emitted intervals | Invalid order | Trailing open |",
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| {_n(cov['action_rows_total'])} | "
            f"{_n(cov['dropped_missing_ticket_rows'])} | "
            f"{_n(cov['dropped_undated_rows'])} | "
            f"{_n(cov['emitted_intervals'])} | {_n(cov['invalid_order_intervals'])} | "
            f"{_n(cov['trailing_open_intervals'])} |"
        ),
        "",
        "### Median and IQR of the gap, by the action that opened it",
        "",
        "| Opened by | Intervals | Median (days) | Q1 | Q3 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in tables["handoff_gap_by_from_type"].iter_rows(named=True):
        lines.append(
            f"| {row['from_action_type']} | {_n(row['intervals'])} | "
            f"{_days(row['median_gap_days'])} | {_days(row['q1_gap_days'])} | "
            f"{_days(row['q3_gap_days'])} |"
        )

    lines += [
        "",
        "### Forwarded/delegated-opened intervals, by ticket-creation-year proxy",
        "",
        (
            "The year of the ticket's first RECORDED action_history row "
            "stands in for `complaints.created_on`: this mart reads "
            "`action_history` only."
        ),
        "",
        "| Year (proxy) | Intervals | Median (days) | Q1 | Q3 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in tables["handoff_forwarded_delegated_by_year"].iter_rows(named=True):
        lines.append(
            f"| {_n(row['ticket_creation_year_proxy'])} | {_n(row['intervals'])} | "
            f"{_days(row['median_gap_days'])} | {_days(row['q1_gap_days'])} | "
            f"{_days(row['q3_gap_days'])} |"
        )

    lines += [
        "",
        "### Dedup-sensitivity comparison",
        "",
        (
            "`action_history_uniq` excludes `action_taken_date` from its key, "
            "so two genuinely distinct hand-offs sharing the same officer, "
            "status and templated remark collapse into one recorded row at "
            "insert time -- the dropped row is gone, not hidden, so this "
            "cannot be corrected. As a sensitivity check, how does the median move "
            "if every interval closed by a known-template action were removed "
            "outright? This compares subpopulations; it is not a bound."
        ),
        "",
        "| Population | Intervals | Median (days) | Q1 | Q3 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in tables["handoff_dedup_sensitivity"].iter_rows(named=True):
        lines.append(
            f"| {row['population']} | {_n(row['intervals'])} | "
            f"{_days(row['median_gap_days'])} | {_days(row['q1_gap_days'])} | "
            f"{_days(row['q3_gap_days'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _out_dir(out_dir: Optional[Path]) -> Path:
    return Path(out_dir) if out_dir else OUTPUTS_DIR / "findings"


def write(
    tables: dict[str, pl.DataFrame], out_dir: Optional[Path] = None
) -> dict[str, Path]:
    """Write the shareable finding: its CSVs, the fragment, and the SQL."""
    out = _out_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name in FINDING_VIEWS:
        path = out / f"{name}.csv"
        tables[name].write_csv(path)
        written[name] = path
    md = out / "handoff_finding.md"
    md.write_text(render_markdown(tables))
    written["markdown"] = md
    handover = out / "handoff_finding.sql"
    handover.write_text(
        "-- Run action_type.sql first; handoff.sql depends on "
        "action_history_typed.\n\n" + sql_text()
    )
    written["sql"] = handover
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Elapsed time between recorded handling steps: median/IQR gap "
            "by opening action, forwarded/delegated-opened gaps by ticket "
            "year, and the dedup-sensitivity comparison. Descriptive only, phase 1."
        )
    )
    parser.add_argument(
        "--lake-dir", type=Path, default=None, help="Lake dir (default: data/interim)."
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output dir (default: outputs/findings).",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print the view definitions and exit, without touching the lake.",
    )
    args = parser.parse_args()

    if args.print_sql:
        print(sql_text())
        return

    tables = compute(args.lake_dir)

    warning = check_data_quality(tables)
    if warning:
        logger.warning(warning)

    for name, path in write(tables, args.out_dir).items():
        logger.success(f"{name} -> {path}")
    print(render_markdown(tables))
