"""Tests for the handoff finding (elapsed time between recorded handling
steps) -- the Python wrapper around handoff.sql.

Every assertion runs against a hand-built fixture lake, never against the
live one -- see test_handoff_mart.py for the SQL-level behaviour this module
reports on. This file exercises compute() / render_markdown() / write() /
the CLI, and the two things a demo audience could misuse if they slipped:
naming ("elapsed time", never "delay"/"time lost"/"saving") and the
withdrawn-claim boundary (no counterfactual, no `complaints` read).
"""

import sys
from datetime import datetime

import polars as pl
import pytest

from janasunani.analytics import marts
from janasunani.analytics.findings import handoff

_FORWARDED = "Forwarded to concerned officer for necessary action"
_ATR = "ATR received from concerned officer"
_DISPOSED = "The grievance has been disposed."

_ACTIONS = [
    (1, "T1", datetime(2024, 1, 1), "Officer A", "Forwarded", _FORWARDED),
    (2, "T1", datetime(2024, 1, 5), "Officer B", "ATR Received", _ATR),
    (3, "T1", datetime(2024, 1, 8), "Officer A", "Disposed", _DISPOSED),
    (4, "T2", datetime(2024, 1, 2), "Officer A", "Forwarded", _FORWARDED),
    (5, "T3", None, "Officer A", "Pending", _FORWARDED),
    (6, "T3", datetime(2024, 1, 3), "Officer A", "Forwarded", _FORWARDED),
    (7, "T3", datetime(2024, 1, 9), "Officer B", "Disposed", _DISPOSED),
    (8, "T4", datetime(2024, 2, 1), "Officer A", "Forwarded", _FORWARDED),
    (9, "T4", datetime(2024, 2, 1), "Officer B", "ATR Received", _ATR),
    (10, "T5", datetime(2024, 3, 10), "Officer A", "Forwarded", _FORWARDED),
    (11, "T5", datetime(2024, 3, 1), "Officer B", "ATR Received", _ATR),
]
_COMPLAINTS = [
    (f"T{i}", datetime(2024, 1, 1), None, None, "Water", "Puri", "RWSS") for i in range(1, 6)
]


def _write_lake(path, actions=_ACTIONS, complaints=_COMPLAINTS, grievance=None):
    path.mkdir(parents=True, exist_ok=True)
    schema = [
        ("ticket_no", pl.Utf8),
        ("created_on", pl.Datetime),
        ("resolved_on", pl.Datetime),
        ("benefitted", pl.Utf8),
        ("category", pl.Utf8),
        ("district", pl.Utf8),
        ("dept", pl.Utf8),
    ]
    if grievance is not None:
        complaints = [(*row, grievance) for row in complaints]
        schema = [*schema, ("grievance", pl.Utf8)]
    pl.DataFrame(complaints, schema=schema, orient="row").write_parquet(
        path / "complaints.parquet"
    )
    pl.DataFrame(
        actions,
        schema=[
            ("id", pl.Int64),
            ("ticket_no", pl.Utf8),
            ("action_taken_date", pl.Datetime),
            ("action_taken_by", pl.Utf8),
            ("action_status", pl.Utf8),
            ("action_taken_remark", pl.Utf8),
        ],
        orient="row",
    ).write_parquet(path / "action_history.parquet")
    return path


@pytest.fixture
def lake(tmp_path):
    return _write_lake(tmp_path)


# --- the mart loader ----------------------------------------------------------


def test_mart_sql_is_read_verbatim_from_the_sql_directory():
    assert handoff.sql_text() == marts.mart_path("handoff").read_text()
    assert "CREATE OR REPLACE VIEW handoff_coverage_summary" in handoff.sql_text()


def test_compute_only_opens_action_history(lake):
    """This mart reads action_history only -- `complaints` is not a declared
    table dependency, so a lake missing it must still work."""
    (lake / "complaints.parquet").unlink()
    tables = handoff.compute(lake)
    assert set(tables) == set(handoff.FINDING_VIEWS)


def test_compute_never_surfaces_citizen_text_even_when_the_lake_carries_it(tmp_path):
    tables = handoff.compute(_write_lake(tmp_path, grievance="My hand pump is broken"))
    for frame in tables.values():
        assert "grievance" not in frame.columns
    assert "hand pump" not in handoff.render_markdown(tables)


# --- coverage and the invalid/dropped counts -----------------------------------


def test_coverage_reports_dropped_and_invalid_counts(lake):
    row = handoff.compute(lake)["handoff_coverage_summary"].row(0, named=True)
    assert row["dropped_undated_rows"] == 1
    assert row["invalid_order_intervals"] == 1
    assert row["emitted_intervals"] == 5
    assert row["trailing_open_intervals"] == 4


def test_data_quality_check_warns_above_threshold(lake):
    warning = handoff.check_data_quality(handoff.compute(lake))
    # 1 invalid / 5 emitted = 20%, above the 5% default threshold.
    assert warning is not None
    assert "invalid event order" in warning


def test_data_quality_check_silent_when_clean(tmp_path):
    clean = [
        (1, "C1", datetime(2024, 1, 1), "Officer A", "Forwarded", _FORWARDED),
        (2, "C1", datetime(2024, 1, 5), "Officer B", "ATR Received", _ATR),
    ]
    tables = handoff.compute(
        _write_lake(tmp_path, actions=clean, complaints=[("C1", datetime(2024, 1, 1), None, None, "Water", "Puri", "RWSS")])
    )
    assert handoff.check_data_quality(tables) is None


def test_invalid_order_share_pct_handles_empty_corpus(tmp_path):
    tables = handoff.compute(
        _write_lake(
            tmp_path,
            actions=[(1, "Z1", datetime(2024, 1, 1), "Officer A", "Forwarded", _FORWARDED)],
            complaints=[("Z1", datetime(2024, 1, 1), None, None, "Water", "Puri", "RWSS")],
        )
    )
    assert handoff.invalid_order_share_pct(tables) is None
    assert handoff.check_data_quality(tables) is None


# --- headline aggregates --------------------------------------------------------


def test_gap_by_from_type_excludes_invalid_order_and_matches_the_mart(lake):
    rows = {
        r["from_action_type"]: r["intervals"]
        for r in handoff.compute(lake)["handoff_gap_by_from_type"].iter_rows(named=True)
    }
    assert rows == {"forwarded_delegated": 3, "reported_back": 1}


def test_forwarded_delegated_by_year_proxy(lake):
    # Three forwarded_delegated-opened intervals in 2024: T1 (4 days), T3 (6
    # days), T4 (0 days). PERCENTILE_CONT over [0, 4, 6]: median 4.0,
    # Q1 (pos 0.5 of the way from 0 to 4) = 2.0, Q3 (pos 1.5 from 4 to 6) = 5.0.
    rows = handoff.compute(lake)["handoff_forwarded_delegated_by_year"].to_dicts()
    assert rows == [
        {
            "ticket_creation_year_proxy": 2024,
            "intervals": 3,
            "median_gap_days": pytest.approx(4.0),
            "q1_gap_days": pytest.approx(2.0),
            "q3_gap_days": pytest.approx(5.0),
        }
    ]


def test_dedup_sensitivity_bounds_the_templated_population(lake):
    rows = {
        r["population"]: r["intervals"]
        for r in handoff.compute(lake)["handoff_dedup_sensitivity"].iter_rows(named=True)
    }
    assert rows["all_intervals"] == 4
    assert rows["excluding_templated_to_events"] == 0


# --- markdown: naming discipline and required caveats ---------------------------


def test_markdown_never_uses_delay_time_lost_or_saving_as_a_positive_description(lake):
    """The caveat is allowed to name "delay"/"time lost"/"saving" -- it exists
    to say these numbers are none of those things. What must never happen is
    those words being used to describe the numbers OUTSIDE that disclaimer
    (a table header, a headline sentence, a column label)."""
    md = handoff.render_markdown(handoff.compute(lake))
    remainder = md.replace(handoff.DESCRIPTIVE_CAVEAT, "").lower()
    for banned in ("time lost", "saving", "days saved", "delay"):
        assert banned not in remainder
    assert "elapsed time between recorded steps" in md.lower()


def test_markdown_states_every_required_caveat(lake):
    md = handoff.render_markdown(handoff.compute(lake))
    assert "not a delay, not time lost, and not a saving" in md
    assert "not the withdrawn routing-savings claim" in md
    assert "not idle time" in md
    assert "free text with no link to a role table" in md
    assert "does not stratify by department" in md
    assert "lower bound" in md and "upper bound" in md
    assert "Insight, phase 1" in md


def test_markdown_reports_coverage_and_dedup_tables(lake):
    md = handoff.render_markdown(handoff.compute(lake))
    assert "### Coverage" in md
    assert "### Dedup-sensitivity bound" in md
    assert "excluding_templated_to_events" in md
    assert "ticket-creation-year proxy" in md.lower()


# --- write() -------------------------------------------------------------------


def test_write_emits_tables_markdown_and_the_handed_over_sql(lake, tmp_path):
    out = tmp_path / "findings"
    written = handoff.write(handoff.compute(lake), out)

    for view in handoff.FINDING_VIEWS:
        assert written[view].exists()
    assert written["markdown"].read_text().startswith(
        "## Elapsed time between recorded handling steps"
    )
    assert "action_type.sql first" in written["sql"].read_text()
    assert handoff.sql_text() in written["sql"].read_text()

    # Per-ticket intermediates never become an output file.
    names = {p.name for p in out.iterdir()}
    assert not {n for n in names if n.startswith(("handoff_intervals", "handoff_ordered", "handoff_ticket"))}


# --- the CLI ---------------------------------------------------------------------


def test_cli_print_sql_never_touches_the_lake(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["janasunani-handoff-finding", "--print-sql"])
    monkeypatch.chdir(tmp_path)  # no data/interim here -- must not be read
    handoff.main()
    assert "CREATE OR REPLACE VIEW handoff_coverage_summary" in capsys.readouterr().out


def test_cli_writes_findings(lake, tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setattr(
        sys, "argv", ["janasunani-handoff-finding", "--lake-dir", str(lake), "--out-dir", str(out)]
    )
    handoff.main()
    assert (out / "handoff_finding.md").exists()
    assert (out / "handoff_coverage_summary.csv").exists()
