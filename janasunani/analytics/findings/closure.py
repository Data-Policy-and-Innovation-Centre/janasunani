"""The closure finding (#76): share of closures recording no action.

Runs the ``closure`` mart over the Parquet lake and writes its tables plus a
presentable Markdown fragment to ``outputs/findings/``. This is an **Insight**,
not a capability: no model, no new processing, and the thing the department
actually receives is the view definition itself, to run against their own
database. The Python here is the reporting wrapper, not the logic.

Two rules are enforced in code rather than left to whoever quotes the number:

* **Both denominators, always.** :func:`render_markdown` cannot emit the
  headline share without the templated-closure base beside it and the
  all-resolved share under it — they come out of the same row of the same view.
* **Aggregates only.** Every output is a count or a share. The one view that
  emits strings (``closure_off_ladder_templates``) is bounded to remarks used
  1,000+ times, which are dropdown templates rather than citizen writing. This
  never reads ``complaints.grievance``.
"""

import argparse
from pathlib import Path
from typing import Optional

import polars as pl
from loguru import logger

from janasunani.analytics.marts import mart_sql, open_lake
from janasunani.config import OUTPUTS_DIR

MART = "closure"

# The shareable finding. `closure_closing_action` and `closure_rung` are
# intermediate and stay unwritten: they are complaint-level, so they are the two
# that must not become an output file.
FINDING_VIEWS = (
    "closure_finding_summary",
    "closure_by_trajectory",
    "closure_two_day_bare",
    "closure_benefitted_overlap",
)

# Engineer-facing, and deliberately not part of the handover. This is the only
# view that emits remark text. A 1,000-use floor is dropdown scale rather than
# free text, but frequency is evidence and not proof: a remark repeated ten
# thousand times could still carry a name or a number somebody pasted into a
# form. So it goes to its own directory, never into the shareable set, and it
# exists for one purpose — telling you the ladder stopped matching.
DIAGNOSTIC_VIEWS = ("closure_off_ladder_templates",)

REPORT_VIEWS = FINDING_VIEWS + DIAGNOSTIC_VIEWS

# Deterministic ordering per view, so reruns diff cleanly.
_ORDER_BY = {
    "closure_by_trajectory": " ORDER BY steps_bucket, elapsed_bucket",
    "closure_benefitted_overlap": " ORDER BY rung, benefitted_value",
    "closure_off_ladder_templates": " ORDER BY resolved_complaints DESC",
}

# Carried into every rendering of this finding. Not a footnote.
DESCRIPTIVE_CAVEAT = (
    "This is descriptive and is **not** a failure rate. Sometimes no action is "
    "the correct outcome: an information request answered, an ineligible claim "
    "properly refused, a matter already settled elsewhere. A correct closure "
    "and a premature one are identical in this record. Turning the figure into "
    "a claim needs 300-500 closures adjudicated by hand, which has not been "
    "done. Report it at state level as an observation about the closure "
    "workflow, never as an office league table."
)

# Below this, the ladder strings have drifted and the headline has silently
# collapsed into `off_ladder` rather than failing. Expect roughly two thirds.
MIN_LADDER_COVERAGE_PCT = 50.0


def sql_text() -> str:
    """The view definitions, as handed over."""
    return mart_sql(MART)


def compute(lake_dir: Optional[Path] = None) -> dict[str, pl.DataFrame]:
    """Install the mart over the lake and return every reportable table."""
    con = open_lake(MART, lake_dir=lake_dir)
    try:
        return {
            view: con.execute(f"SELECT * FROM {view}{_ORDER_BY.get(view, '')}").pl()  # noqa: S608
            for view in REPORT_VIEWS
        }
    finally:
        con.close()


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _n(value: Optional[int]) -> str:
    return "n/a" if value is None else f"{int(value):,}"


def render_markdown(tables: dict[str, pl.DataFrame]) -> str:
    """The presentable fragment. Both denominators appear or nothing does."""
    s = tables["closure_finding_summary"].row(0, named=True)
    t = tables["closure_two_day_bare"].row(0, named=True)

    lines = [
        "## How cases are closed",
        "",
        "**Insight.** No model and no new processing: an exact string match "
        "over the disposal templates officers already pick from.",
        "",
        "### The headline, on both denominators",
        "",
        "| Base | Complaints | Closed on the rung claiming no action | Share |",
        "|---|---|---|---|",
        (
            f"| Closed on one of the six disposal templates | "
            f"{_n(s['ladder_closures'])} | {_n(s['bare'])} | "
            f"**{_pct(s['bare_share_of_ladder_pct'])}** |"
        ),
        (
            f"| All resolved complaints | {_n(s['resolved_complaints'])} | "
            f"{_n(s['bare'])} | **{_pct(s['bare_share_of_resolved_pct'])}** |"
        ),
        "",
        (
            f"The two differ because {_pct(s['off_ladder_share_pct'])} of resolved "
            f"complaints ({_n(s['off_ladder'])}) close on neither template. "
            f"Quote the {_n(s['ladder_closures'])} base whenever the "
            f"{_pct(s['bare_share_of_ladder_pct'])} figure is used."
        ),
        "",
        (
            f"The more specific rung was available and chosen "
            f"{_n(s['claims_action'])} times ({_n(s['with_action'])} with "
            f"appropriate action, {_n(s['benefit'])} with the beneficiary "
            f"benefited)."
        ),
        "",
        f"⚠️ {DESCRIPTIVE_CAVEAT}",
        "",
        "### Created and closed within two days, on a bare disposal",
        "",
        (
            f"{_n(t['two_day_bare'])} complaints, "
            f"{_pct(t['share_of_bare_pct'])} of all bare disposals and "
            f"{_pct(t['share_of_resolved_pct'])} of all resolved complaints. "
            f"{_n(t['two_day_bare_min_trajectory'])} of them closed on the "
            f"shortest trajectory that reaches a disposal at all."
        ),
        "",
        (
            "This subset is the useful half: it names a specific set of cases "
            "rather than the system as a whole. Two days is fast, not wrong — "
            "an information request answered on the spot belongs here too."
        ),
        "",
        "### Conditioned on trajectory",
        "",
        (
            "A case going created → forwarded → ATR → disposed had work done "
            "whatever the closing phrase says. The bare share by trajectory:"
        ),
        "",
        "| Action steps | Elapsed | Resolved | Templated closures | Bare | Share |",
        "|---|---|---|---|---|---|",
    ]
    for row in tables["closure_by_trajectory"].iter_rows(named=True):
        lines.append(
            f"| {row['steps_bucket']} | {row['elapsed_bucket']} | "
            f"{_n(row['resolved_complaints'])} | {_n(row['ladder_closures'])} | "
            f"{_n(row['bare'])} | {_pct(row['bare_share_of_ladder_pct'])} |"
        )
    lines += [
        "",
        (
            "Read the resolved column beside the templated one. Cells where the "
            "two diverge sharply are not thin data, they are complaints that "
            "closed on a **discard** template rather than a disposal one "
            "(`complaint details inadequate`, `duplicate copy`, `not within the "
            "purview of this grievance cell`). Those are off the ladder by "
            "construction and are a separate finding, not part of this share."
        ),
    ]

    lines += [
        "",
        "### Overlap with the existing `benefitted` column",
        "",
        (
            "Checked so the third rung is not presented as novel if a column "
            "the dashboards already have marks the same complaints."
        ),
        "",
        "| Rung | `benefitted` | Complaints |",
        "|---|---|---|",
    ]
    for row in tables["closure_benefitted_overlap"].iter_rows(named=True):
        lines.append(
            f"| {row['rung']} | {row['benefitted_value']} | "
            f"{_n(row['resolved_complaints'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _out_dir(out_dir: Optional[Path]) -> Path:
    return Path(out_dir) if out_dir else OUTPUTS_DIR / "findings"


def write(
    tables: dict[str, pl.DataFrame], out_dir: Optional[Path] = None
) -> dict[str, Path]:
    """Write the shareable finding: its CSVs, the fragment, and the SQL.

    Diagnostics are **not** written here. See :func:`write_diagnostics`.
    """
    out = _out_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name in FINDING_VIEWS:
        path = out / f"{name}.csv"
        tables[name].write_csv(path)
        written[name] = path
    md = out / "closure_finding.md"
    md.write_text(render_markdown(tables))
    written["markdown"] = md
    handover = out / "closure_finding.sql"
    handover.write_text(sql_text())
    written["sql"] = handover
    return written


def write_diagnostics(
    tables: dict[str, pl.DataFrame], out_dir: Optional[Path] = None
) -> dict[str, Path]:
    """Write the engineer-facing drift diagnostic, under ``diagnostics/``.

    Kept out of :func:`write` on purpose: this is the only output carrying
    remark text, and the directory the finding is shared out of should contain
    aggregates only.
    """
    out = _out_dir(out_dir) / "diagnostics"
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name in DIAGNOSTIC_VIEWS:
        path = out / f"{name}.csv"
        tables[name].write_csv(path)
        written[name] = path
    return written


def check_ladder_coverage(tables: dict[str, pl.DataFrame]) -> Optional[str]:
    """Warn if the disposal ladder stopped matching. ``None`` when it is fine.

    Template drift is the failure mode this finding cannot survive and would
    not otherwise announce: an unmatched string does not error, it quietly
    moves complaints into ``off_ladder`` and shrinks the denominator the
    headline is computed on.
    """
    coverage = tables["closure_finding_summary"].row(0, named=True)["ladder_coverage_pct"]
    if coverage is not None and coverage >= MIN_LADDER_COVERAGE_PCT:
        return None
    return (
        f"The disposal ladder matched only {_pct(coverage)} of resolved "
        "complaints. Expected roughly two thirds — the template strings have "
        "probably drifted. Check closure_off_ladder_templates.csv before "
        "quoting anything."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "The closure finding: share of closures recording no action, on "
            "both denominators, conditioned on trajectory."
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

    # The diagnostic is written either way: when the guard fails it is the
    # thing you need to read to find out why.
    for name, path in write_diagnostics(tables, args.out_dir).items():
        logger.info(f"{name} -> {path}")

    drift = check_ladder_coverage(tables)
    if drift:
        # Refuse to produce the artifacts rather than warning beside them. A
        # batch caller that keeps stdout and drops stderr would otherwise
        # publish exactly the number this check says must not be quoted.
        logger.error(drift)
        logger.error("Refusing to write the finding. Nothing was published.")
        raise SystemExit(1)

    for name, path in write(tables, args.out_dir).items():
        logger.success(f"{name} -> {path}")
    print(render_markdown(tables))
