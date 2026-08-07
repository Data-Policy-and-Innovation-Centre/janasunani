"""The closure finding (#76): share of closures recording no action.

Runs the ``closure`` mart over the Parquet lake and writes its tables plus a
presentable Markdown fragment to ``outputs/findings/``. This is an **Insight**,
not a capability: no model, no new processing, and the thing the department
actually receives is the view definition itself, to run against their own
database. The Python here is the reporting wrapper, not the logic.

Two rules are enforced in code rather than left to whoever quotes the number:

* **Both denominators, always.** :func:`render_markdown` cannot emit the
  headline share without the templated-closure base beside it and the
  all-resolved share under it. They come out of the same row of the same view.
* **Aggregates only.** Every output is a count or a share. Drift diagnostics
  retain only aggregate frequencies, never the source remark or a linkable
  fingerprint.
  This never reads ``complaints.grievance``.
"""

import argparse
from pathlib import Path
from typing import Optional

import polars as pl
from loguru import logger

from janasunani.analytics.findings.discards import TEMPLATES as DISCARD_TEMPLATES
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

# Engineer-facing, and deliberately not part of the handover. High frequency
# never proves arbitrary text is an approved template, so the deliverable
# diagnostic carries only aggregate counts; identifying a drifted phrase
# requires the private source system.
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


class ClosureReconciliationError(RuntimeError):
    """The mart and an independently structured aggregate query disagree."""


# Independent of closure.sql's window-ranked `closure_closing_action`: this
# uses DuckDB's grouped `arg_max` to pick the latest eligible action. It is a
# runtime reconciliation query, not the portable handover SQL.
RECONCILIATION_SQL = r"""
WITH resolved AS (
    SELECT ticket_no, created_on, resolved_on
    FROM complaints
    WHERE resolved_on IS NOT NULL
), independent_closing AS (
    SELECT
        a.ticket_no,
        arg_max(
            a.action_taken_remark,
            struct_pack(action_date := a.action_taken_date, action_id := a.id)
        ) AS action_taken_remark,
        count(*) AS action_steps
    FROM action_history a
    INNER JOIN resolved r ON r.ticket_no = a.ticket_no
    WHERE a.action_taken_date IS NOT NULL
      AND CAST(a.action_taken_date AS DATE) <= CAST(r.resolved_on AS DATE)
    GROUP BY a.ticket_no
), classified AS (
    SELECT
        r.created_on,
        r.resolved_on,
        coalesce(c.action_steps, 0) AS action_steps,
        CASE regexp_replace(
            trim(regexp_replace(lower(c.action_taken_remark), '\s+', ' ', 'g')),
            '\.+$', '', 'g'
        )
            WHEN 'the grievance has been disposed' THEN 'bare'
            WHEN 'the grievance has been resolved' THEN 'bare'
            WHEN 'the grievance has been disposed with appropriate action' THEN 'with_action'
            WHEN 'the grievance has been resolved with appropriate action' THEN 'with_action'
            WHEN 'the grievance has been disposed & beneficiary benefited' THEN 'benefit'
            WHEN 'the grievance has been resolved & beneficiary benefited' THEN 'benefit'
            ELSE 'off_ladder'
        END AS rung
    FROM resolved r
    LEFT JOIN independent_closing c ON c.ticket_no = r.ticket_no
)
SELECT
    count(*)::BIGINT AS resolved_complaints,
    count(*) FILTER (WHERE rung <> 'off_ladder')::BIGINT AS ladder_closures,
    count(*) FILTER (WHERE rung = 'bare')::BIGINT AS bare,
    count(*) FILTER (WHERE rung = 'with_action')::BIGINT AS with_action,
    count(*) FILTER (WHERE rung = 'benefit')::BIGINT AS benefit,
    count(*) FILTER (WHERE rung IN ('with_action', 'benefit'))::BIGINT AS claims_action,
    count(*) FILTER (WHERE rung = 'off_ladder')::BIGINT AS off_ladder,
    count(*) FILTER (
        WHERE rung = 'bare'
          AND CAST(resolved_on AS DATE) - CAST(created_on AS DATE) BETWEEN 0 AND 2
    )::BIGINT AS two_day_bare,
    count(*) FILTER (
        WHERE rung = 'bare'
          AND CAST(resolved_on AS DATE) - CAST(created_on AS DATE) BETWEEN 0 AND 2
          AND action_steps = 3
    )::BIGINT AS two_day_bare_min_trajectory
FROM classified
""".strip()

# High-volume closing templates verified on the 7 August snapshot as distinct
# from the six disposal-ladder strings. This is only a drift allowlist: it does
# not assign an action class or make an outcome claim. The governed discard
# catalog is included separately below. Any new >=1,000-use off-ladder string
# still fails closed until reviewed.
KNOWN_HIGH_VOLUME_NON_LADDER_TEMPLATES = {
    "as reported",
    (
        "advised to place the grievance for house before the collector in joint "
        "hearing of grievances on monday"
    ),
    "thanks for the suggestions",
    "resolved",
    "will be considered as per rule in due course of time",
    (
        "ପ୍ରଧାନମନ୍ତ୍ରୀ ଆବାସ ଯୋଜନା (ଗ୍ରାମୀଣ) ରେ ନୂତନ ହିତାଧିକାରୀ ଚୟନ ନିମନ୍ତେ "
        "ବର୍ତ୍ତମାନ ସର୍ଭେ ଚାଲୁଅଛି i ଆଶାୟୀ ପରିବାର awaasplus2024 ମୋବାଇଲ ଆପ୍ "
        "ଜରିଆରେ ନିଜେ କିମ୍ବା ବ୍ଲକ ଅଧିକାରୀଙ୍କ ସହାୟତାରେ ନିଜ ନାମ ସର୍ଭେ "
        "ତାଲିକାଭୁକ୍ତ କରିପାରିବେ i ବିସ୍ତୃତ ସୂଚନା https://pmayg.nic.in / "
        "https://www.rhodisha.gov.in ରେ ଉପଲବ୍ଧ i ତାଲିକାଭୁକ୍ତ ପରିବାରଙ୍କୁ "
        "ଯୋଗ୍ୟତା ମାନଦଣ୍ଡ ଅନୁଯାୟୀ ପକ୍କା ଘର ମଞ୍ଜୁର ହେବ i"
    ),
    "other",
    "complaint details not legible",
    "advised to go through the due recruitment process",
    (
        "you are requested to send your grievance/petition directly to vigilance "
        "organisation for redressal of your grievance"
    ),
    (
        "the grievance has been kept in priority category and shall be taken up "
        "after due government approval"
    ),
}
KNOWN_HIGH_VOLUME_NON_LADDER_TEMPLATES.update(
    template for templates in DISCARD_TEMPLATES.values() for template in templates
)


def unexpected_off_ladder_templates(tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """High-volume closing strings not yet reviewed as non-ladder templates."""
    table = tables["closure_off_ladder_templates"]
    return table.filter(
        ~pl.col("closing_remark").is_in(KNOWN_HIGH_VOLUME_NON_LADDER_TEMPLATES)
    )


def sql_text() -> str:
    """The view definitions, as handed over."""
    return mart_sql(MART)


def compute(lake_dir: Optional[Path] = None) -> dict[str, pl.DataFrame]:
    """Install the mart over the lake and return every reportable table."""
    con = open_lake(
        MART,
        lake_dir=lake_dir,
        tables=("complaints", "action_history"),
    )
    try:
        tables = {
            view: con.execute(f"SELECT * FROM {view}{_ORDER_BY.get(view, '')}").pl()  # noqa: S608
            for view in REPORT_VIEWS
        }
        _assert_reconciled(tables, con.execute(RECONCILIATION_SQL).pl())
        return tables
    finally:
        con.close()


def _assert_reconciled(
    tables: dict[str, pl.DataFrame], reconciliation: pl.DataFrame
) -> None:
    summary = tables["closure_finding_summary"].row(0, named=True)
    two_day = tables["closure_two_day_bare"].row(0, named=True)
    expected = reconciliation.row(0, named=True)
    observed = {
        key: int(summary[key])
        for key in (
            "resolved_complaints",
            "ladder_closures",
            "bare",
            "with_action",
            "benefit",
            "claims_action",
            "off_ladder",
        )
    }
    observed.update(
        {
            "two_day_bare": int(two_day["two_day_bare"]),
            "two_day_bare_min_trajectory": int(
                two_day["two_day_bare_min_trajectory"]
            ),
        }
    )
    expected = {key: int(value) for key, value in expected.items()}
    if observed != expected:
        raise ClosureReconciliationError(
            "closure mart disagrees with independent arg_max reconciliation"
        )


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
            "rather than the system as a whole. Two days is fast, not wrong. "
            "An information request answered on the spot belongs here too."
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


def render_headline_markdown(tables: dict[str, pl.DataFrame]) -> str:
    """Finding 1 only: the closure headline with both required denominators."""
    s = tables["closure_finding_summary"].row(0, named=True)
    return "\n".join(
        [
            "## Share of closures recording no action",
            "",
            "**Insight.** Exact matches over the six disposal templates; no "
            "model and no complaint text.",
            "",
            "| Base | Complaints | Bare disposal rung | Share |",
            "|---|---:|---:|---:|",
            (
                "| Closed on one of the six disposal templates | "
                f"{_n(s['ladder_closures'])} | {_n(s['bare'])} | "
                f"**{_pct(s['bare_share_of_ladder_pct'])}** |"
            ),
            (
                f"| All resolved complaints | {_n(s['resolved_complaints'])} | "
                f"{_n(s['bare'])} | **{_pct(s['bare_share_of_resolved_pct'])}** |"
            ),
            "",
            (
                f"Quote the {_n(s['ladder_closures'])} templated-closure base "
                f"whenever the {_pct(s['bare_share_of_ladder_pct'])} figure is used."
            ),
            "",
            f"⚠️ {DESCRIPTIVE_CAVEAT}",
            "",
        ]
    )


def render_two_day_markdown(tables: dict[str, pl.DataFrame]) -> str:
    """Finding 2 only: fast bare closures on both relevant denominators."""
    t = tables["closure_two_day_bare"].row(0, named=True)
    return "\n".join(
        [
            "## Cases created and closed within two days on a bare disposal",
            "",
            "**Insight.** An exact, aggregate subset of the governed closure "
            "finding; no model and no complaint text.",
            "",
            (
                f"**{_n(t['two_day_bare'])} complaints**: "
                f"{_pct(t['share_of_bare_pct'])} of all bare disposals and "
                f"{_pct(t['share_of_resolved_pct'])} of all resolved complaints."
            ),
            "",
            (
                f"{_n(t['two_day_bare_min_trajectory'])} closed on the shortest "
                "trajectory that reaches a disposal at all. Two days is fast, "
                "not proof that the closure was wrong."
            ),
            "",
            f"⚠️ {DESCRIPTIVE_CAVEAT}",
            "",
        ]
    )


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


def write_single_finding(
    tables: dict[str, pl.DataFrame],
    finding: str,
    out_dir: Optional[Path] = None,
) -> dict[str, Path]:
    """Write one #107 closure finding as one aggregate CSV + Markdown pair."""
    definitions = {
        "closure_recording_no_action": (
            "closure_finding_summary",
            render_headline_markdown,
        ),
        "two_day_bare_closures": (
            "closure_two_day_bare",
            render_two_day_markdown,
        ),
    }
    try:
        view, renderer = definitions[finding]
    except KeyError:
        raise ValueError(f"unknown closure finding {finding!r}") from None

    out = _out_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{finding}.csv"
    md_path = out / f"{finding}.md"
    tables[view].write_csv(csv_path)
    md_path.write_text(renderer(tables))
    return {"csv": csv_path, "markdown": md_path}


def _single_finding_main(finding: str) -> None:
    parser = argparse.ArgumentParser(description=f"Build the {finding} finding.")
    parser.add_argument(
        "--lake-dir", type=Path, default=None, help="Lake dir (default: data/interim)."
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="Output dir (default: outputs/findings)."
    )
    args = parser.parse_args()
    tables = compute(args.lake_dir)
    drift = check_ladder_coverage(tables)
    if drift:
        out = _out_dir(args.out_dir)
        for suffix in ("csv", "md"):
            path = out / f"{finding}.{suffix}"
            if path.is_file():
                path.unlink()
        logger.error(drift)
        logger.error("Refusing to write the finding. Nothing was published.")
        raise SystemExit(1)
    for kind, path in write_single_finding(tables, finding, args.out_dir).items():
        logger.success(f"{kind} -> {path}")


def closure_headline_main() -> None:
    _single_finding_main("closure_recording_no_action")


def two_day_bare_main() -> None:
    _single_finding_main("two_day_bare_closures")


def write_diagnostics(
    tables: dict[str, pl.DataFrame], out_dir: Optional[Path] = None
) -> dict[str, Path]:
    """Write safe drift diagnostics under ``diagnostics/``.

    Diagnostics remain below ``outputs/`` and can be delivered recursively,
    so raw remarks must not be serialized there.
    """
    out = _out_dir(out_dir) / "diagnostics"
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name in DIAGNOSTIC_VIEWS:
        path = out / f"{name}.csv"
        table = tables[name]
        unexpected = unexpected_off_ladder_templates(tables)
        pl.DataFrame(
            {
                "known_high_volume_non_ladder_templates": [
                    table.height - unexpected.height
                ],
                "known_affected_resolved_complaints": [
                    int(table["resolved_complaints"].sum())
                    - (
                        int(unexpected["resolved_complaints"].sum())
                        if unexpected.height
                        else 0
                    )
                ],
                "unexpected_high_volume_off_ladder_templates": [unexpected.height],
                "unexpected_affected_resolved_complaints": [
                    int(unexpected["resolved_complaints"].sum())
                    if unexpected.height
                    else 0
                ],
            }
        ).write_csv(path)
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
    if coverage is None or coverage < MIN_LADDER_COVERAGE_PCT:
        return (
            f"The disposal ladder matched only {_pct(coverage)} of resolved "
            "complaints. Expected roughly two thirds. The template strings have "
            "probably drifted. Check the private source system before quoting anything."
        )
    unexpected = unexpected_off_ladder_templates(tables)
    if unexpected.height:
        return (
            "Previously unseen high-volume off-ladder closing remarks were detected. "
            "The shareable diagnostic contains aggregate counts only; validate the "
            "templates against the private source system before quoting the headline."
        )
    return None


def remove_shareable_artifacts(out_dir: Optional[Path] = None) -> None:
    """Remove only this finding's prior deliverables after a failed guard."""
    out = _out_dir(out_dir)
    paths = [out / f"{name}.csv" for name in FINDING_VIEWS]
    paths.extend((out / "closure_finding.md", out / "closure_finding.sql"))
    for path in paths:
        if path.is_file():
            path.unlink()


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
        remove_shareable_artifacts(args.out_dir)
        logger.error(drift)
        logger.error("Refusing to write the finding. Nothing was published.")
        raise SystemExit(1)

    for name, path in write(tables, args.out_dir).items():
        logger.success(f"{name} -> {path}")
    print(render_markdown(tables))
