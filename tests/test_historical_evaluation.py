from pathlib import Path

import polars as pl
import pytest

from janasunani.evaluation.historical import (
    _route_cell_sql,
    benchmark_historical_routing,
    load_route_cells,
)
from janasunani.evaluation.routing import load_incidence_router


def complaints(path: Path) -> Path:
    rows = []
    for year, split_count in ((2023, 30), (2024, 12), (2025, 12)):
        for index in range(split_count):
            housing = index % 3 != 0
            rows.append(
                {
                    "created_year": year,
                    "category": "Housing" if housing else "General",
                    "subcategory": "PMAY" if housing else None,
                    "district": "Sambalpur",
                    "dept": "Housing Department" if housing else "General Department",
                    "grievance": "raw text must never be selected",
                }
            )
    rows.append(
        {
            "created_year": 2020,
            "category": "Legacy",
            "subcategory": None,
            "district": "Sambalpur",
            "dept": "Legacy Department",
            "grievance": "must remain outside the declared training window",
        }
    )
    target = path / "complaints.parquet"
    pl.DataFrame(rows).write_parquet(target)
    return target


def test_sql_names_only_structured_fields():
    sql = _route_cell_sql(use_subcategory=True).lower()

    assert "grievance" not in sql
    assert "select *" not in sql
    assert "subcategory" in sql


def test_loader_aggregates_cases_and_holds_out_years(tmp_path):
    data = load_route_cells(complaints(tmp_path), use_subcategory=True)

    assert sum(record.weight for record in data.records) == 54
    assert data.diagnostics["split_cases"] == {
        "train": 30,
        "validation": 12,
        "test": 12,
    }
    assert len(data.records) == 6
    assert {record.observed_on.year for record in data.records} == {2023, 2024, 2025}
    assert data.diagnostics["dedup_group_isolation"] == "unavailable_full_corpus"
    assert data.diagnostics["excluded_cases"]["outside_split_years"] == 1
    assert data.diagnostics["label_provenance"] == {
        "source_table": "complaints",
        "source_field": "dept",
        "semantics": "unconfirmed_recorded_department_snapshot",
        "source_owner_confirmation": "unavailable",
        "not_equivalent_to": [
            "joint_department_chain_assignment_intent",
            "action_history_route_traversal",
            "correct_authority",
        ],
    }


def test_informative_scope_excludes_general_but_reports_it(tmp_path):
    data = load_route_cells(
        complaints(tmp_path), informative_categories_only=True
    )

    assert sum(record.weight for record in data.records) == 36
    assert data.diagnostics["excluded_cases"]["uninformative_category"] == 18


def test_historical_benchmark_is_weighted_and_not_outcome_optimized(tmp_path):
    result = benchmark_historical_routing(
        complaints(tmp_path), alpha_values=(3.0,)
    ).report

    assert result["test"]["n"] == 12
    assert result["test"]["accuracy"] == pytest.approx(1.0)
    assert result["test"]["accuracy_interval"] is None
    assert result["test"]["interval_status"] == (
        "suppressed_not_cluster_robust_for_weighted_route_cells"
    )
    assert result["outcome_optimized"] is False
    assert result["historical_data"]["split_feature_cells"]["train"] == 2


def test_historical_benchmark_publishes_artifact_only_when_explicit(tmp_path):
    artifact_dir = tmp_path / "release"
    result = benchmark_historical_routing(
        complaints(tmp_path), alpha_values=(3.0,), artifact_dir=artifact_dir
    ).report

    assert result["serving_artifact"]["outcome_optimized"] is False
    assert load_incidence_router(artifact_dir) is not None


def test_loader_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_route_cells(tmp_path / "missing.parquet")
