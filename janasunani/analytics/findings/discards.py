"""Aggregate-only findings from high-frequency discard templates (#107).

The three findings in this module are deliberately plain exact-match lookups:
the eight discard-reason families, the two officer-confirmed duplicate
families, and the out-of-purview misrouting baseline.  They are **Insights**,
not capabilities.  No complaint text is selected and no free-text tail is
classified.

Matching is exact after one mechanical normalization (lowercase, trim,
collapse whitespace, and remove a trailing full stop).  Adding a template is
a governed lookup-table change; broad ``LIKE``/regex matching is intentionally
not available because it would silently turn arbitrary officer prose into a
label.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal, Optional

import polars as pl
from loguru import logger

from janasunani.config import OUTPUTS_DIR
from janasunani.olap import lake

FindingName = Literal[
    "discard_reason_families", "confirmed_duplicates", "misrouting_baseline"
]

# These are the high-frequency normalized templates recorded in ROADMAP §5.2.
# Variants are enumerated rather than pattern-matched.  The live run must be
# reconciled to the independently measured baseline before publication; if a
# source-system dropdown changes, the lookup is reviewed and changed here.
TEMPLATES: dict[str, tuple[str, ...]] = {
    "details_inadequate": (
        "complaint details inadequate",
        "complaint details are inadequate",
    ),
    "documents_not_attached": (
        "documents not attached",
        "required documents not attached",
        "the required documents are not attached",
    ),
    "case_already_taken_up": (
        "case already taken up",
        "case taken up earlier",
        "the case has already been taken up",
        "the case was taken up earlier",
    ),
    "no_specific_grievance": (
        "no specific grievance",
        "no specific grievance has been mentioned",
    ),
    "duplicate_copy": ("duplicate copy",),
    "policy_decision_required": (
        "needs a policy decision first",
        "can be considered only after a policy decision",
    ),
    "outside_grievance_cell_purview": (
        "not within purview of this grievance cell",
        "not within the purview of this grievance cell",
    ),
    "address_not_given": ("address not given",),
}

FAMILY_LABELS = {
    "details_inadequate": "Details inadequate",
    "documents_not_attached": "Documents not attached",
    "case_already_taken_up": "Case already taken up / taken up earlier",
    "no_specific_grievance": "No specific grievance",
    "duplicate_copy": "Duplicate copy",
    "policy_decision_required": "Needs a policy decision first",
    "outside_grievance_cell_purview": (
        "Not within the purview of this grievance cell"
    ),
    "address_not_given": "Address not given",
}

DUPLICATE_FAMILIES = ("case_already_taken_up", "duplicate_copy")
MISROUTING_FAMILY = "outside_grievance_cell_purview"

_NORMALIZED_REMARK = (
    "regexp_replace(regexp_replace(lower(trim(action_taken_remark)), "
    "'\\s+', ' ', 'g'), '\\.$', '')"
)


class ReconciliationError(RuntimeError):
    """Primary and independently written aggregate queries disagree."""


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _lookup_values() -> str:
    rows = [
        f"({_sql_literal(family)}, {_sql_literal(template)})"
        for family, templates in TEMPLATES.items()
        for template in templates
    ]
    return ",\n        ".join(rows)


def primary_sql() -> str:
    """Lookup-join implementation used for the published aggregates."""
    return f"""
WITH discard_template(family, template) AS (
    VALUES
        {_lookup_values()}
), normalized_action AS (
    SELECT {_NORMALIZED_REMARK} AS normalized_remark
    FROM action_history
    WHERE action_taken_remark IS NOT NULL
), matched AS (
    SELECT d.family
    FROM normalized_action a
    INNER JOIN discard_template d ON d.template = a.normalized_remark
)
SELECT family, count(*)::BIGINT AS rows
FROM matched
GROUP BY family
ORDER BY family
""".strip()


def reconciliation_sql() -> str:
    """Independent CASE-based query; intentionally does not join the lookup."""
    clauses = []
    for family, templates in TEMPLATES.items():
        values = ", ".join(_sql_literal(value) for value in templates)
        clauses.append(
            f"sum(CASE WHEN normalized_remark IN ({values}) THEN 1 ELSE 0 END)"
            f"::BIGINT AS {_sql_literal(family)}"
        )
    measures = ",\n    ".join(clauses)
    return f"""
WITH normalized_action AS (
    SELECT {_NORMALIZED_REMARK} AS normalized_remark
    FROM action_history
    WHERE action_taken_remark IS NOT NULL
)
SELECT
    {measures}
FROM normalized_action
""".strip()


def _complete(frame: pl.DataFrame) -> pl.DataFrame:
    observed = {
        row["family"]: int(row["rows"])
        for row in frame.iter_rows(named=True)
    }
    return pl.DataFrame(
        {
            "family": list(TEMPLATES),
            "label": [FAMILY_LABELS[family] for family in TEMPLATES],
            "rows": [observed.get(family, 0) for family in TEMPLATES],
        }
    )


def compute(lake_dir: Optional[Path] = None) -> pl.DataFrame:
    """Compute all eight families and fail closed if reconciliation differs."""
    primary = _complete(lake.query(primary_sql(), lake_dir))
    check = lake.query(reconciliation_sql(), lake_dir).row(0, named=True)
    expected = {family: int(check[family]) for family in TEMPLATES}
    actual = {
        row["family"]: int(row["rows"])
        for row in primary.iter_rows(named=True)
    }
    if actual != expected:
        raise ReconciliationError(
            "discard-reason lookup disagrees with independent CASE reconciliation"
        )
    return primary


def select_finding(families: pl.DataFrame, finding: FindingName) -> pl.DataFrame:
    """Return only the aggregate rows belonging to one #107 finding."""
    if finding == "discard_reason_families":
        return families
    if finding == "confirmed_duplicates":
        count = int(
            families.filter(pl.col("family").is_in(DUPLICATE_FAMILIES))["rows"].sum()
        )
        return pl.DataFrame(
            {
                "measure": ["officer_confirmed_duplicate_rows"],
                "rows": [count],
            }
        )
    if finding == "misrouting_baseline":
        count = int(
            families.filter(pl.col("family") == MISROUTING_FAMILY)["rows"].sum()
        )
        return pl.DataFrame(
            {
                "measure": ["outside_grievance_cell_purview_rows"],
                "rows": [count],
            }
        )
    raise ValueError(f"unknown finding {finding!r}")


def _n(value: int) -> str:
    return f"{value:,}"


def render_markdown(families: pl.DataFrame, finding: FindingName) -> str:
    """Render a self-labelling, aggregate-only Markdown fragment."""
    if finding == "discard_reason_families":
        lines = [
            "## Why complaints are discarded",
            "",
            "**Insight.** Exact matches over eight high-frequency officer "
            "templates; no model and no complaint text.",
            "",
            "| Reason family | Action rows |",
            "|---|---:|",
        ]
        for row in families.iter_rows(named=True):
            lines.append(f"| {row['label']} | {_n(row['rows'])} |")
        lines += [
            "",
            "These are administrative reason labels, not ground truth about the "
            "citizen or the merits of a grievance. Office-level variation must be "
            "audited before using them as training labels.",
            "",
        ]
        return "\n".join(lines)

    if finding == "confirmed_duplicates":
        rows = select_finding(families, finding).item(0, "rows")
        return "\n".join(
            [
                "## Duplicates already caught by the manual process",
                "",
                "**Insight.** The two exact-match discard families record "
                f"**{_n(rows)} officer-confirmed duplicate action rows**.",
                "",
                "This is the baseline, not the dedup capability claim. The "
                "capability is the separately reported increment found by MinHash. "
                "The baseline inherits office-level discard practices and is not "
                "complete ground truth.",
                "",
            ]
        )

    rows = select_finding(families, finding).item(0, "rows")
    return "\n".join(
        [
            "## Complaints discarded as outside the office's remit",
            "",
            "**Insight.** The exact out-of-purview template records "
            f"**{_n(rows)} action rows**.",
            "",
            "This is a routing baseline, not spam and not evidence that the "
            "grievance lacked merit. It records where a case was judged not to "
            "belong; it does not identify the destination that resolves it well.",
            "",
        ]
    )


def write(
    families: pl.DataFrame,
    finding: FindingName,
    out_dir: Optional[Path] = None,
) -> dict[str, Path]:
    out = Path(out_dir) if out_dir else OUTPUTS_DIR / "findings"
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{finding}.csv"
    md_path = out / f"{finding}.md"
    select_finding(families, finding).write_csv(csv_path)
    md_path.write_text(render_markdown(families, finding))
    return {"csv": csv_path, "markdown": md_path}


def _main(finding: FindingName) -> None:
    parser = argparse.ArgumentParser(description=f"Build the {finding} finding.")
    parser.add_argument(
        "--lake-dir", type=Path, default=None, help="Lake dir (default: data/interim)."
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="Output dir (default: outputs/findings)."
    )
    args = parser.parse_args()
    families = compute(args.lake_dir)
    for kind, path in write(families, finding, args.out_dir).items():
        logger.success(f"{kind} -> {path}")
    print(render_markdown(families, finding))


def discard_reasons_main() -> None:
    _main("discard_reason_families")


def confirmed_duplicates_main() -> None:
    _main("confirmed_duplicates")


def misrouting_main() -> None:
    _main("misrouting_baseline")
