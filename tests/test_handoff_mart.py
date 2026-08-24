"""Tests for the handoff mart (elapsed time between recorded handling steps).

Every assertion runs against the real SQL views over a hand-built fixture
lake, never against the live one -- same discipline as test_action_type.py
and test_closure_finding.py. The fixture is arranged so each behaviour the
mart promises (drop undated rows, bucket inverted order as invalid, flag the
trailing open interval, tie-break same-day events deterministically, produce
no gap for a single-event ticket) has an independently countable answer.

This is phase 1: descriptive only. Nothing here computes a per-ticket total
or applies IPCW/RMST -- see handoff.sql's header.
"""

import re
from datetime import datetime

import polars as pl
import pytest

from janasunani.analytics import marts

# ---------------------------------------------------------------------------
# Fixture: five tickets, one behaviour each.
#
# T1: normal multi-step ticket.
#     forwarded_delegated (1/1) -> reported_back (1/5) -> disposed_no_claim (1/8)
#     Two closed intervals: 4 days, then 3 days. The second is trailing-open.
# T2: single-event ticket. No LAG partner -> zero interval rows.
# T3: an undated row (dropped, counted) plus two dated rows -> one interval.
# T4: two events tied on the same date -> gap_days == 0, id breaks the tie.
# T5: inverted order -- id 11 claims an EARLIER date than id 10, so under
#     ORDER BY (action_taken_date, id) row 11 sorts first and row 10 second,
#     even though row 10 was recorded (inserted) before row 11. That is the
#     "nothing enforces ordering on action dates" failure mode: id decreases
#     relative to the date-sorted predecessor -> is_invalid_order.
# ---------------------------------------------------------------------------

_FORWARDED = "Forwarded to concerned officer for necessary action"
_ATR = "ATR received from concerned officer"
_DISPOSED = "The grievance has been disposed."

_ACTIONS = [
    # (id, ticket, date, action_taken_by, action_status, remark)
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


def _write_lake(path, actions=_ACTIONS, complaints=_COMPLAINTS):
    path.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        complaints,
        schema=[
            ("ticket_no", pl.Utf8),
            ("created_on", pl.Datetime),
            ("resolved_on", pl.Datetime),
            ("benefitted", pl.Utf8),
            ("category", pl.Utf8),
            ("district", pl.Utf8),
            ("dept", pl.Utf8),
        ],
        orient="row",
    ).write_parquet(path / "complaints.parquet")
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


def _open(lake_dir):
    return marts.open_lake("action_type", "handoff", lake_dir=lake_dir, tables=("action_history",))


# --- the mart loader ---------------------------------------------------------


def test_mart_sql_is_read_verbatim():
    assert marts.mart_sql("handoff") == marts.mart_path("handoff").read_text()
    assert "CREATE OR REPLACE VIEW handoff_intervals" in marts.mart_sql("handoff")


def test_handoff_depends_on_action_type_and_reads_only_action_history():
    """Declared scope claim: this mart never names `complaints`."""
    sql = marts.mart_sql("handoff")
    # It reads action_history_typed (defined in action_type.sql), not complaints.
    assert "FROM complaints" not in sql
    assert "JOIN complaints" not in sql
    assert "action_history_typed" in sql


def test_open_lake_installs_handoff_after_action_type(lake):
    con = _open(lake)
    try:
        n = con.execute("SELECT count(*) FROM handoff_intervals").fetchone()[0]
        assert n == 5  # T1x2, T3x1, T4x1, T5x1; T2 contributes none
    finally:
        con.close()


# --- interval emission -------------------------------------------------------


def test_normal_multi_step_ticket_emits_two_closed_intervals(lake):
    con = _open(lake)
    try:
        rows = con.execute(
            "SELECT step_index, from_action_type, to_action_type, gap_days, "
            "is_trailing_open, is_invalid_order FROM handoff_intervals "
            "WHERE ticket_no = 'T1' ORDER BY step_index"
        ).fetchall()
    finally:
        con.close()
    assert rows == [
        (2, "forwarded_delegated", "reported_back", 4, False, False),
        (3, "reported_back", "disposed_no_claim", 3, True, False),
    ]


def test_single_event_ticket_has_no_gap(lake):
    """T2 has exactly one dated row: no predecessor, so no interval row."""
    con = _open(lake)
    try:
        n = con.execute(
            "SELECT count(*) FROM handoff_intervals WHERE ticket_no = 'T2'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 0


def test_null_date_is_dropped_and_counted_not_given_insertion_order(lake):
    con = _open(lake)
    try:
        dropped = con.execute(
            "SELECT dropped_undated_rows FROM handoff_dropped_undated"
        ).fetchone()[0]
        t3_intervals = con.execute(
            "SELECT count(*) FROM handoff_intervals WHERE ticket_no = 'T3'"
        ).fetchone()[0]
        t3_gap = con.execute(
            "SELECT gap_days FROM handoff_intervals WHERE ticket_no = 'T3'"
        ).fetchone()[0]
    finally:
        con.close()
    assert dropped == 1
    # Only the two dated T3 rows participate: one interval, 6 days apart.
    assert t3_intervals == 1
    assert t3_gap == 6


def test_tie_on_same_date_breaks_deterministically_by_id(lake):
    con = _open(lake)
    try:
        row = con.execute(
            "SELECT gap_days, from_action_type, to_action_type FROM handoff_intervals "
            "WHERE ticket_no = 'T4'"
        ).fetchone()
    finally:
        con.close()
    gap_days, from_type, to_type = row
    assert gap_days == 0
    # id 8 < id 9, both dated 2024-02-01: id 8 (forwarded) precedes id 9 (ATR).
    assert from_type == "forwarded_delegated"
    assert to_type == "reported_back"


def test_inverted_order_is_bucketed_invalid_not_clamped(lake):
    """T5: id 11 claims an earlier date than id 10.

    Under ORDER BY (action_taken_date, id) row 11 sorts first (it has the
    earlier claimed date), so gap_days itself is still non-negative (9 days,
    computed from the sorted pair) -- but id decreased across that pair,
    which is the actual "nothing enforces ordering on action dates" failure:
    the row that is chronologically later per its own claimed date was
    logged (lower id) before the row the dates say it followed.
    """
    con = _open(lake)
    try:
        row = con.execute(
            "SELECT gap_days, is_invalid_order FROM handoff_intervals "
            "WHERE ticket_no = 'T5'"
        ).fetchone()
    finally:
        con.close()
    gap_days, is_invalid = row
    assert is_invalid is True
    assert gap_days == 9  # not clamped to 0 or dropped -- reported, then excluded


def test_trailing_open_interval_is_flagged_on_the_final_recorded_event(lake):
    con = _open(lake)
    try:
        rows = con.execute(
            "SELECT ticket_no, is_trailing_open FROM handoff_intervals ORDER BY ticket_no, step_index"
        ).fetchall()
    finally:
        con.close()
    by_ticket = {}
    for ticket_no, is_open in rows:
        by_ticket.setdefault(ticket_no, []).append(is_open)
    # Every ticket's LAST emitted interval is flagged trailing-open; none of
    # the earlier ones are (T1 has two intervals: only the second is).
    assert by_ticket["T1"] == [False, True]
    assert by_ticket["T3"] == [True]
    assert by_ticket["T4"] == [True]
    assert by_ticket["T5"] == [True]


def test_coverage_summary_reconciles_counts(lake):
    con = _open(lake)
    try:
        row = con.execute(
            "SELECT action_rows_total, dropped_undated_rows, emitted_intervals, "
            "invalid_order_intervals, trailing_open_intervals, tickets_with_intervals "
            "FROM handoff_coverage_summary"
        ).fetchone()
    finally:
        con.close()
    total, dropped, emitted, invalid, trailing, tickets = row
    assert total == len(_ACTIONS)
    assert dropped == 1
    assert emitted == 5
    assert invalid == 1  # T5
    assert trailing == 4  # T1, T3, T4, T5's final interval each
    assert tickets == 4  # T2 contributes no interval row


# --- per-ticket reducer -------------------------------------------------------


def test_per_ticket_reducer_scalars(lake):
    con = _open(lake)
    try:
        rows = {
            r[0]: r[1:]
            for r in con.execute(
                "SELECT ticket_no, n_closed_intervals, total_gap_days, max_gap_days, "
                "forwarded_delegated_open_days, largest_gap_share_pct, "
                "has_trailing_open_interval FROM handoff_ticket_summary"
            ).fetchall()
        }
    finally:
        con.close()

    # T1: two valid closed intervals, 4 + 3 days. The larger (4) is opened by
    # forwarded_delegated. Largest share = 4/7.
    n, total, mx, fwd, share, trailing = rows["T1"]
    assert (n, total, mx, fwd, trailing) == (2, 7, 4, 4, True)
    assert share == pytest.approx(400.0 / 7.0)

    # T4: one valid closed interval, 0 days. No positive span to share.
    n, total, mx, fwd, share, trailing = rows["T4"]
    assert (n, total, mx, fwd, trailing) == (1, 0, 0, 0, True)
    assert share is None

    # T5's only interval is invalid-order, so it is excluded from the "valid"
    # population the reducer aggregates over -- T5 does not appear at all.
    assert "T5" not in rows
    # T2 never opened an interval either.
    assert "T2" not in rows


# --- dedup-sensitivity bound --------------------------------------------------


def test_dedup_sensitivity_compares_templated_and_non_templated_populations(lake):
    """Every fixture remark is a known template, so excluding templated `to`
    events should empty the second population -- demonstrating the bound
    actually removes what it claims to."""
    con = _open(lake)
    try:
        rows = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT population, intervals FROM handoff_dedup_sensitivity"
            ).fetchall()
        }
    finally:
        con.close()
    assert rows["all_intervals"] == 4  # excludes T5's invalid-order interval
    assert rows["excluding_templated_to_events"] == 0


def test_dedup_sensitivity_retains_free_text_intervals(tmp_path):
    """A free-text closing remark is not a known template, so it survives
    the exclusion and the sensitivity populations genuinely diverge."""
    actions = [
        (1, "F1", datetime(2024, 1, 1), "Officer A", "Forwarded", _FORWARDED),
        (
            2,
            "F1",
            datetime(2024, 1, 4),
            "Officer A",
            "Pending",
            "Site visit conducted, awaiting the panchayat's confirmation letter",
        ),
    ]
    complaints = [("F1", datetime(2024, 1, 1), None, None, "Water", "Puri", "RWSS")]
    con = _open(_write_lake(tmp_path, actions=actions, complaints=complaints))
    try:
        rows = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT population, intervals FROM handoff_dedup_sensitivity"
            ).fetchall()
        }
    finally:
        con.close()
    assert rows["all_intervals"] == 1
    assert rows["excluding_templated_to_events"] == 1


# --- headline aggregates -------------------------------------------------------


def test_gap_by_from_type_excludes_invalid_order(lake):
    con = _open(lake)
    try:
        rows = {
            r[0]: r[1] for r in con.execute(
                "SELECT from_action_type, intervals FROM handoff_gap_by_from_type"
            ).fetchall()
        }
    finally:
        con.close()
    # T5's reported_back -> forwarded_delegated interval is invalid-order and
    # must not inflate either bucket.
    assert rows == {"forwarded_delegated": 3, "reported_back": 1}


def test_forwarded_delegated_by_year_uses_first_recorded_action_as_proxy(lake):
    con = _open(lake)
    try:
        rows = con.execute(
            "SELECT ticket_creation_year_proxy, intervals FROM handoff_forwarded_delegated_by_year"
        ).fetchall()
    finally:
        con.close()
    assert rows == [(2024, 3)]  # T1, T3, T4 each open one forwarded_delegated interval


def test_no_complaints_columns_read_and_no_free_text_in_reportable_views(tmp_path):
    """Static + dynamic: the mart never selects a remark column, and no free
    text survives into an aggregate view even when the lake carries it."""
    sql = marts.mart_sql("handoff")
    assert "action_taken_remark" not in sql
    # Strip comments first: the header prose legitimately mentions
    # `complaints.grievance` (to say it is never read) and "grievance cell".
    code = re.sub(r"--[^\n]*", "", sql)
    assert "grievance" not in code

    con = _open(_write_lake(tmp_path))
    try:
        for view in (
            "handoff_gap_by_from_type",
            "handoff_forwarded_delegated_by_year",
            "handoff_dedup_sensitivity",
            "handoff_coverage_summary",
        ):
            cols = [c.lower() for c in con.execute(f"SELECT * FROM {view}").pl().columns]
            assert "action_taken_remark" not in cols
            assert "remark" not in cols
    finally:
        con.close()
