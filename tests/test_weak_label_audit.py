from pathlib import Path

import polars as pl
import pytest

from janasunani.evaluation.weak_labels import audit_weak_labels


def fixtures(path: Path) -> tuple[Path, Path]:
    complaints = path / "complaints.parquet"
    actions = path / "action_history.parquet"
    pl.DataFrame(
        {
            "ticket_no": ["T1", "T2", "T3", "T4", "T5", "T6"],
            "office": ["A", "A", "B", "B", "B", "B"],
            "created_year": [2023, 2023, 2024, 2024, 2025, 2025],
            "grievance": ["must", "never", "be", "selected", "or", "reported"],
        }
    ).write_parquet(complaints)
    pl.DataFrame(
        {
            "ticket_no": ["T1", "T2", "T3", "T4", "T5", "T5", "T6"],
            "action_taken_remark": [
                "Complaint details inadequate.",
                "No specific grievance",
                "This is not within the purview of this grievance cell",
                "Duplicate copy",
                "No specific grievance",
                "Complaint details inadequate",
                "citizen prose outside the governed templates",
            ],
        }
    ).write_parquet(actions)
    return complaints, actions


def test_audit_counts_exact_families_and_excludes_conflicts(tmp_path):
    complaints, actions = fixtures(tmp_path)
    result = audit_weak_labels(complaints, actions, min_office_support=1)

    assert result["family_counts"]["details_inadequate"] == {
        "action_rows": 2,
        "tickets": 2,
    }
    assert result["family_counts"]["duplicate_copy"]["tickets"] == 1
    assert result["eligible_ticket_labels"]["valid_single_label"] == 3
    assert result["eligible_ticket_labels"]["conflicting_labels_excluded"] == 1
    assert result["eligible_ticket_labels"]["distribution"] == {
        "irrelevant": 1,
        "out_of_scope": 1,
        "underspecified": 1,
    }
    assert result["training_gate"]["out_of_scope_never_spam"] is True


def test_office_variation_is_aggregate_and_support_gated(tmp_path):
    complaints, actions = fixtures(tmp_path)
    result = audit_weak_labels(complaints, actions, min_office_support=2)

    office = result["office_variation"]
    assert office["eligible_offices"] == 1
    assert office["worst_supported_offices"][0]["office"] == "A"
    assert office["interpretation"].startswith("descriptive")


def test_office_audit_separates_missing_join_office_and_year(tmp_path):
    complaints = tmp_path / "complaints.parquet"
    actions = tmp_path / "action_history.parquet"
    pl.DataFrame(
        {
            "ticket_no": ["T1", "T2"],
            "office": ["A", None],
            "created_year": [None, 2024],
        }
    ).write_parquet(complaints)
    pl.DataFrame(
        {
            "ticket_no": ["T1", "T2", "T3"],
            "action_taken_remark": [
                "Complaint details inadequate.",
                "No specific grievance",
                "This is not within the purview of this grievance cell",
            ],
        }
    ).write_parquet(actions)

    result = audit_weak_labels(complaints, actions, min_office_support=1)

    labels = result["eligible_ticket_labels"]
    assert labels["missing_complaint_join"] == 1
    assert labels["missing_office"] == 1
    office = result["office_variation"]
    assert office["eligible_offices"] == 1
    assert [row["office"] for row in office["worst_supported_offices"]] == ["A"]


def test_office_variation_median_interpolates_even_sample(tmp_path):
    complaints, actions = fixtures(tmp_path)
    result = audit_weak_labels(complaints, actions, min_office_support=1)

    variations = sorted(
        row["total_variation"]
        for row in result["office_variation"]["worst_supported_offices"]
    )
    assert len(variations) == 2
    assert result["office_variation"]["median_total_variation"] == pytest.approx(
        sum(variations) / 2
    )


def test_audit_validates_inputs(tmp_path):
    with pytest.raises(FileNotFoundError):
        audit_weak_labels(tmp_path / "missing", tmp_path / "also-missing")

    complaints, actions = fixtures(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        audit_weak_labels(complaints, actions, min_office_support=0)
