"""Audit administrative discard families before weak-label training.

Officer remarks are matched only to an enumerated exact-template registry.
The free-text tail is neither returned nor classified.  Duplicate families
remain outside the actionability taxonomy, and out-of-purview cases retain an
``out_of_scope`` label instead of being called spam.

The office field in ``complaints`` describes the complaint's recorded office,
not necessarily the actor who entered a historical action.  Office variation
is therefore a confounding alarm, not evidence about an office or an officer.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from janasunani.analytics.findings.discards import TEMPLATES
from janasunani.evaluation.actionability import WEAK_LABELS_BY_DISCARD_FAMILY


_NORMALIZED_REMARK = (
    "regexp_replace(regexp_replace(lower(trim(action_taken_remark)), "
    "'\\s+', ' ', 'g'), '\\.$', '')"
)


def _template_rows() -> list[tuple[str, str]]:
    return [
        (family, template)
        for family, templates in TEMPLATES.items()
        for template in templates
    ]


def _eligible_label_rows() -> list[tuple[str, str]]:
    return [
        (family, weak.label)
        for family, weak in WEAK_LABELS_BY_DISCARD_FAMILY.items()
        if weak.eligible_for_training and weak.label is not None
    ]


def _total_variation(
    observed: dict[str, float], reference: dict[str, float]
) -> float:
    labels = set(observed).union(reference)
    return 0.5 * sum(
        abs(observed.get(label, 0.0) - reference.get(label, 0.0))
        for label in labels
    )


def _distribution(counts: Counter[str]) -> dict[str, float]:
    total = sum(counts.values())
    return {
        label: count / total
        for label, count in sorted(counts.items())
    }


def _percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def audit_weak_labels(
    complaints_path: Path,
    action_history_path: Path,
    *,
    min_office_support: int = 100,
) -> dict[str, object]:
    """Return aggregate label counts and office-variation diagnostics."""

    if not complaints_path.is_file():
        raise FileNotFoundError(complaints_path)
    if not action_history_path.is_file():
        raise FileNotFoundError(action_history_path)
    if min_office_support < 1:
        raise ValueError("min_office_support must be positive")

    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TEMP TABLE discard_template(family VARCHAR, template VARCHAR)"
        )
        connection.executemany(
            "INSERT INTO discard_template VALUES (?, ?)", _template_rows()
        )
        connection.execute(
            "CREATE TEMP TABLE eligible_label(family VARCHAR, label VARCHAR)"
        )
        connection.executemany(
            "INSERT INTO eligible_label VALUES (?, ?)", _eligible_label_rows()
        )
        connection.from_parquet(str(complaints_path)).create_view(
            "complaints_parquet"
        )
        connection.from_parquet(str(action_history_path)).create_view(
            "action_history_parquet"
        )
        connection.execute(
            "CREATE TEMP VIEW complaints AS SELECT ticket_no, office, created_year "
            "FROM complaints_parquet"
        )
        connection.execute(
            "CREATE TEMP VIEW action_history AS SELECT ticket_no, action_taken_remark "
            "FROM action_history_parquet"
        )
        connection.execute(
            f"""
            CREATE TEMP VIEW matched_action AS
            SELECT a.ticket_no, d.family
            FROM (
                SELECT ticket_no, {_NORMALIZED_REMARK} AS normalized_remark
                FROM action_history
                WHERE action_taken_remark IS NOT NULL
            ) a
            INNER JOIN discard_template d ON d.template = a.normalized_remark
            """
        )
        family_rows = connection.execute(
            """
            SELECT family, count(*)::BIGINT action_rows,
                   count(DISTINCT ticket_no)::BIGINT tickets
            FROM matched_action
            GROUP BY family
            ORDER BY family
            """
        ).fetchall()
        label_rows = connection.execute(
            """
            WITH ticket_label AS (
                SELECT DISTINCT m.ticket_no, e.label AS label_name
                FROM matched_action m
                INNER JOIN eligible_label e USING (family)
            ), ticket_summary AS (
                SELECT ticket_no, count(*) AS label_count,
                       min(label_name) AS label_name
                FROM ticket_label
                GROUP BY ticket_no
            )
            SELECT
                t.ticket_no,
                t.label_count,
                t.label_name,
                c.ticket_no complaint_ticket_no,
                nullif(trim(c.office), '') office,
                c.created_year
            FROM ticket_summary t
            LEFT JOIN complaints c USING (ticket_no)
            """
        ).fetchall()
    finally:
        connection.close()

    valid = [row for row in label_rows if int(row[1]) == 1]
    conflicts = [row for row in label_rows if int(row[1]) > 1]
    missing_complaint = sum(row[3] is None for row in label_rows)
    missing_office = sum(
        row[3] is not None and row[4] is None for row in label_rows
    )

    global_counts: Counter[str] = Counter(str(row[2]) for row in valid)
    office_eligible = [
        row for row in valid if row[3] is not None and row[4] is not None
    ]
    office_global_counts: Counter[str] = Counter(
        str(row[2]) for row in office_eligible
    )
    office_global_distribution = _distribution(office_global_counts)
    office_counts: dict[str, Counter[str]] = defaultdict(Counter)
    year_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for _, _, label, _, office, year in valid:
        if office is not None:
            office_counts[str(office)][str(label)] += 1
        year_counts[str(year) if year is not None else "(missing)"][str(label)] += 1

    office_rows = []
    for office, counts in office_counts.items():
        support = sum(counts.values())
        if support < min_office_support:
            continue
        distribution = _distribution(counts)
        office_rows.append(
            {
                "office": office,
                "n": support,
                "total_variation": _total_variation(
                    distribution, office_global_distribution
                ),
                "distribution": distribution,
            }
        )
    office_rows.sort(
        key=lambda row: (-float(row["total_variation"]), -int(row["n"]), str(row["office"]))
    )
    variations = [float(row["total_variation"]) for row in office_rows]

    family_lookup = {
        family: {"action_rows": int(action_rows), "tickets": int(tickets)}
        for family, action_rows, tickets in family_rows
    }
    return {
        "source": {
            "complaints": str(complaints_path),
            "action_history": str(action_history_path),
        },
        "matching": "exact_enumerated_templates_after_mechanical_normalization",
        "family_counts": {
            family: family_lookup.get(family, {"action_rows": 0, "tickets": 0})
            for family in TEMPLATES
        },
        "eligible_ticket_labels": {
            "valid_single_label": len(valid),
            "conflicting_labels_excluded": len(conflicts),
            "missing_complaint_join": missing_complaint,
            "missing_office": missing_office,
            "distribution": dict(sorted(global_counts.items())),
            "by_created_year": {
                year: dict(sorted(counts.items()))
                for year, counts in sorted(year_counts.items())
            },
        },
        "office_variation": {
            "office_field": "complaints.office_current_not_action_actor",
            "min_support": min_office_support,
            "eligible_offices": len(office_rows),
            "global_distribution": office_global_distribution,
            "max_total_variation": max(variations) if variations else None,
            "median_total_variation": _percentile(variations, 0.5),
            "p90_total_variation": _percentile(variations, 0.9),
            "worst_supported_offices": office_rows[:10],
            "interpretation": "descriptive confounding alarm, not proof of office bias",
        },
        "training_gate": {
            "weak_labels_train_only": True,
            "adjudicated_validation_and_test_required": True,
            "duplicates_excluded": True,
            "out_of_scope_never_spam": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit exact discard-family weak labels before model training"
    )
    parser.add_argument("--complaints", type=Path, required=True)
    parser.add_argument("--action-history", type=Path, required=True)
    parser.add_argument("--min-office-support", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = audit_weak_labels(
        args.complaints,
        args.action_history,
        min_office_support=args.min_office_support,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
