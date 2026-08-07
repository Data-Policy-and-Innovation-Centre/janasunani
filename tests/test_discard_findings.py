"""Fixture tests for findings 3-5 in the Sprint 3 findings pack (#107)."""

from datetime import datetime

import polars as pl
import pytest

from janasunani.analytics.findings import discards


@pytest.fixture
def lake(tmp_path):
    remarks = [
        "Complaint details inadequate.",
        "  Complaint   details inadequate. May file a detail grievance.  ",
        "Relevant/requisite document/s not attached.",
        "Case already taken up for examination.",
        "Case taken up earlier hence closed.",
        "No specific grievance.",
        "Duplicate copy.",
        "Can be considered only after a policy decision is made by the Government.",
        "This is not within the purview of this Grievance Cell.",
        "Detail address of the complainant not given.",
        # Arbitrary prose must not be pulled into a family by fuzzy matching.
        "The address was checked and found correct.",
        "A duplicate document was attached, but this is a different case.",
        None,
    ]
    pl.DataFrame(
        {
            "id": range(1, len(remarks) + 1),
            "ticket_no": [f"T{i}" for i in range(1, len(remarks) + 1)],
            "action_taken_date": [datetime(2024, 1, 1)] * len(remarks),
            "action_taken_remark": remarks,
            "action_status": ["Disposed"] * len(remarks),
        }
    ).write_parquet(tmp_path / "action_history.parquet")
    return tmp_path


def test_queries_are_exact_match_aggregate_only():
    primary = discards.primary_sql().lower()
    check = discards.reconciliation_sql().lower()

    assert "action_taken_remark" in primary
    assert "action_taken_remark" in check
    assert "complaints" not in primary
    assert "complaints.grievance" not in primary
    assert "ticket_no" not in primary
    assert " like " not in primary
    assert "regexp_matches" not in primary
    assert " join discard_template " in primary
    assert "case when" in check
    assert "join discard_template" not in check


def test_eight_families_reconcile_against_independent_query(lake):
    result = discards.compute(lake)
    counts = dict(zip(result["family"], result["rows"], strict=True))

    assert result.height == 8
    assert counts["details_inadequate"] == 2
    assert counts["case_already_taken_up"] == 2
    assert counts["duplicate_copy"] == 1
    assert counts["outside_grievance_cell_purview"] == 1
    assert sum(counts.values()) == 10
    assert result.filter(pl.col("family") == "duplicate_copy").item(
        0, "roadmap_reference_rows"
    ) == 14_767


def test_confirmed_duplicate_baseline_is_two_families_only(lake):
    families = discards.compute(lake)
    result = discards.select_finding(families, "confirmed_duplicates")

    assert result.item(0, "rows") == 3
    markdown = discards.render_markdown(families, "confirmed_duplicates")
    assert "**Insight.**" in markdown
    assert "**3 officer-confirmed duplicate action rows**" in markdown
    assert "ROADMAP reference of 34,671" in markdown
    assert "baseline, not the dedup capability claim" in markdown


def test_misrouting_is_not_described_as_spam(lake):
    families = discards.compute(lake)
    result = discards.select_finding(families, "misrouting_baseline")

    assert result.item(0, "rows") == 1
    markdown = discards.render_markdown(families, "misrouting_baseline")
    assert "**Insight.**" in markdown
    assert "not spam" in markdown
    assert "ROADMAP reference of 8,455" in markdown
    assert "does not identify the destination that resolves it well" in markdown


@pytest.mark.parametrize(
    "finding",
    ["discard_reason_families", "confirmed_duplicates", "misrouting_baseline"],
)
def test_each_finding_writes_only_aggregate_csv_and_markdown(lake, tmp_path, finding):
    families = discards.compute(lake)
    out = tmp_path / "out"
    written = discards.write(families, finding, out)

    assert set(written) == {"csv", "markdown"}
    assert written["csv"].is_file()
    assert written["markdown"].is_file()
    csv_text = written["csv"].read_text()
    markdown = written["markdown"].read_text()
    assert "T1" not in csv_text
    assert "action_taken_remark" not in csv_text
    assert "**Insight.**" in markdown


def test_reconciliation_disagreement_fails_closed(lake, monkeypatch):
    real_query = discards.lake.query
    calls = 0

    def disagreeing_query(sql, lake_dir, *, tables=None):
        nonlocal calls
        calls += 1
        frame = real_query(sql, lake_dir, tables=tables)
        if calls == 2:
            frame = frame.with_columns(
                (pl.col("duplicate_copy") + 1).alias("duplicate_copy")
            )
        return frame

    monkeypatch.setattr(discards.lake, "query", disagreeing_query)
    with pytest.raises(discards.ReconciliationError):
        discards.compute(lake)
