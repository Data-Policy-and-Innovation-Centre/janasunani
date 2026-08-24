"""Governed historical scorecards over structured lake fields.

This module deliberately exposes aggregate-only loaders.  Routing evaluation
needs category, subcategory, district, department, and time; it never needs
complaint text.  The SQL therefore names every selected column and turns the
1.37M-row lake into weighted feature cells before Python sees it.

The resulting benchmark measures agreement with the department snapshot
recorded in ``complaints.dept``. The source-system owner is unavailable, so
that field's lifecycle semantics remain unconfirmed: it must not be presented
as the initial joint department-and-chain assignment or as the route traversed
in action history. Years are held out chronologically: 2021--2023 train, 2024
validation, and 2025 test by default. Because the current full lake has no
full-corpus dedup-group join, the report says so explicitly rather than
claiming duplicate-group isolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

from janasunani.evaluation.routing import (
    RouteRecord,
    RoutingBenchmark,
    benchmark_incidence_router,
    department_snapshot_provenance,
)


UNINFORMATIVE_CATEGORIES = frozenset({"general", "miscellaneous"})


@dataclass(frozen=True)
class HistoricalRouteData:
    records: tuple[RouteRecord, ...]
    diagnostics: dict[str, object]


def _split_for_year(
    year: int,
    *,
    train_from: int,
    train_through: int,
    validation_year: int,
    test_year: int,
) -> str | None:
    if train_from <= year <= train_through:
        return "train"
    if year == validation_year:
        return "validation"
    if year == test_year:
        return "test"
    return None


def _cell_id(values: Sequence[str]) -> str:
    payload = "\0".join(values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _route_cell_sql(*, use_subcategory: bool) -> str:
    subcategory = (
        "coalesce(nullif(trim(subcategory), ''), '') AS subcategory,"
        if use_subcategory
        else "'' AS subcategory,"
    )
    return f"""
WITH normalized AS (
    SELECT
        created_year,
        trim(category) AS category,
        {subcategory}
        coalesce(nullif(trim(district), ''), '') AS district,
        trim(dept) AS department
    FROM read_parquet(?)
    WHERE created_year IS NOT NULL
      AND category IS NOT NULL AND trim(category) <> ''
      AND dept IS NOT NULL AND trim(dept) <> ''
)
SELECT
    created_year,
    category,
    subcategory,
    district,
    department,
    count(*)::BIGINT AS cases
FROM normalized
GROUP BY created_year, category, subcategory, district, department
ORDER BY created_year, category, subcategory, district, department
""".strip()


def load_route_cells(
    complaints_path: Path,
    *,
    train_from: int = 2021,
    train_through: int = 2023,
    validation_year: int = 2024,
    test_year: int = 2025,
    use_subcategory: bool = False,
    informative_categories_only: bool = False,
) -> HistoricalRouteData:
    """Load weighted route cells without selecting grievance text."""

    if not complaints_path.is_file():
        raise FileNotFoundError(complaints_path)
    if not train_from <= train_through < validation_year < test_year:
        raise ValueError(
            "years must satisfy train_from <= train_through < validation < test"
        )

    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            _route_cell_sql(use_subcategory=use_subcategory),
            [str(complaints_path)],
        ).fetchall()
    finally:
        connection.close()

    records: list[RouteRecord] = []
    excluded = {
        "outside_split_years": 0,
        "uninformative_category": 0,
    }
    split_cases = {"train": 0, "validation": 0, "test": 0}
    split_cells = {"train": 0, "validation": 0, "test": 0}
    for year, category, subcategory, district, department, cases in rows:
        split = _split_for_year(
            int(year),
            train_from=train_from,
            train_through=train_through,
            validation_year=validation_year,
            test_year=test_year,
        )
        if split is None:
            excluded["outside_split_years"] += int(cases)
            continue
        if (
            informative_categories_only
            and category.strip().lower() in UNINFORMATIVE_CATEGORIES
        ):
            excluded["uninformative_category"] += int(cases)
            continue
        values = (
            split,
            str(year),
            category,
            subcategory,
            district,
            department,
        )
        identifier = _cell_id(values)
        records.append(
            RouteRecord(
                item_id=identifier,
                group_id=identifier,
                observed_on=date(int(year), 1, 1),
                category=category,
                subcategory=subcategory or None,
                district=district or None,
                department=department,
                split=split,
                language="unknown_not_available_without_text",
                weight=int(cases),
            )
        )
        split_cases[split] += int(cases)
        split_cells[split] += 1

    diagnostics: dict[str, object] = {
        "source": str(complaints_path),
        "selected_columns": [
            "created_year",
            "category",
            "subcategory" if use_subcategory else None,
            "district",
            "dept",
        ],
        "label_provenance": department_snapshot_provenance(),
        "split_years": {
            "train_from": train_from,
            "train_through": train_through,
            "validation": validation_year,
            "test": test_year,
        },
        "split_cases": split_cases,
        "split_feature_cells": split_cells,
        "excluded_cases": excluded,
        "informative_categories_only": informative_categories_only,
        "dedup_group_isolation": "unavailable_full_corpus",
        "language_slices": "unavailable_without_governed_redacted_text",
    }
    return HistoricalRouteData(records=tuple(records), diagnostics=diagnostics)


def benchmark_historical_routing(
    complaints_path: Path,
    *,
    use_subcategory: bool = False,
    informative_categories_only: bool = False,
    alpha_values: Sequence[float] = (3.0, 10.0, 30.0, 100.0),
    artifact_dir: Path | None = None,
) -> RoutingBenchmark:
    data = load_route_cells(
        complaints_path,
        use_subcategory=use_subcategory,
        informative_categories_only=informative_categories_only,
    )
    benchmark = benchmark_incidence_router(
        data.records,
        alpha_values=alpha_values,
        history_year_values=(None, 1, 2),
        use_subcategory=use_subcategory,
        artifact_dir=artifact_dir,
    )
    benchmark.report["historical_data"] = data.diagnostics
    benchmark.report["limitations"].extend(
        [
            "complaints.dept lifecycle semantics are unconfirmed because the source-system owner is unavailable",
            "the recorded department snapshot is neither the joint department-and-chain assignment intent nor the action-history route traversal",
            "full-corpus dedup groups are unavailable, so leakage control is chronological but not campaign-grouped",
            "language is not present in structured complaints and is not inferred from raw grievance text",
        ]
    )
    return benchmark


def _parse_alpha_values(value: str) -> tuple[float, ...]:
    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--alpha-values must be comma-separated numbers") from exc
    if not values:
        raise argparse.ArgumentTypeError("--alpha-values cannot be empty")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate local historical-incidence routing on structured fields"
    )
    parser.add_argument("--complaints", type=Path, required=True)
    parser.add_argument("--use-subcategory", action="store_true")
    parser.add_argument("--informative-categories-only", action="store_true")
    parser.add_argument(
        "--alpha-values", type=_parse_alpha_values, default=(3.0, 10.0, 30.0, 100.0)
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="Explicitly publish an immutable, aggregate-only serving artifact",
    )
    args = parser.parse_args()

    result = benchmark_historical_routing(
        args.complaints,
        use_subcategory=args.use_subcategory,
        informative_categories_only=args.informative_categories_only,
        alpha_values=args.alpha_values,
        artifact_dir=args.artifact_dir,
    ).report
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
