"""Tests for the scoped journey tables in `janasunani.analytics.journey`.

The mart's views are covered by test_grievance_journey_mart.py. These are the
tables that carry a window over a grievance's whole action history and so must
be materialised per scope, plus the three lifted out of the notebooks:
`route`, `office_wait` and `transferred`.

Each fixture grievance is built so one behaviour has an independently countable
answer.
"""

from datetime import datetime

import polars as pl
import pytest

from janasunani.analytics import journey, marts

# ---------------------------------------------------------------------------
# R1 walks CM cell -> Collector -> Collector -> BDO -> Collector.
#     Rungs 1, 3, 3, 5, 3. Collapsed: 1 > 3 > 5 > 3, four steps, three levels.
#     It also comes back UP to rung 3 after reaching rung 5, so it has a review
#     phase and one arrival at its deepest rung.
# R2 is handled by one office throughout: no hand-off, no level change.
# R3 touches an office the rung ladder does not recognise, so it must be
#     excluded from route/phases rather than given a wrong deepest rung.
# R4 carries a Complaint Transfer event.
# R5 walks Collector -> DSSO -> Director: three offices, but the first two are
#     both rung 3, so it separates the level route from the office route. The
#     Director has a rung and no name, which is the "Other state office" case.
# ---------------------------------------------------------------------------
_ACTIONS = [
    (1, "R1", datetime(2024, 7, 1), "Complaint Register",
     "Complaint Register - CM Grievance Cell"),
    (2, "R1", datetime(2024, 7, 3), "Forwarded", "Forwarded - Collector, Puri"),
    (3, "R1", datetime(2024, 7, 5), "Forwarded", "Forwarded - Collector, Puri"),
    (4, "R1", datetime(2024, 7, 9), "Forwarded", "Forwarded - BDO, Sadar"),
    (5, "R1", datetime(2024, 7, 20), "ATR Received", "ATR Received - Collector, Puri"),

    (6, "R2", datetime(2024, 7, 2), "Forwarded", "Forwarded - Collector, Ganjam"),
    (7, "R2", datetime(2024, 7, 8), "Disposed", "Disposed - Collector, Ganjam"),

    (8, "R3", datetime(2024, 7, 2), "Forwarded", "Forwarded - Mystery Office, Nowhere"),
    (9, "R3", datetime(2024, 7, 6), "Forwarded", "Forwarded - BDO, Sadar"),

    (10, "R4", datetime(2024, 7, 2), "Forwarded", "Forwarded - Collector, Puri"),
    (11, "R4", datetime(2024, 7, 4), "Complaint Transfer",
     "Complaint Transfer - Collector, Puri"),

    (12, "R5", datetime(2024, 7, 2), "Forwarded", "Forwarded - Collector, Puri"),
    (13, "R5", datetime(2024, 7, 4), "Forwarded", "Forwarded - DSSO ,Puri"),
    (14, "R5", datetime(2024, 7, 6), "Forwarded", "Forwarded - Director, Pension"),
]

_COMPLAINTS = [
    ("R1", datetime(2024, 7, 1), datetime(2024, 7, 25), "Disposed"),
    ("R2", datetime(2024, 7, 2), datetime(2024, 7, 8), "Disposed"),
    ("R3", datetime(2024, 7, 2), datetime(2024, 7, 10), "Disposed"),
    ("R4", datetime(2024, 7, 2), datetime(2024, 7, 12), "Disposed"),
    ("R5", datetime(2024, 7, 2), datetime(2024, 7, 14), "Disposed"),
]


@pytest.fixture
def con(tmp_path):
    pl.DataFrame(
        _COMPLAINTS,
        schema=[("ticket_no", pl.Utf8), ("created_on", pl.Datetime),
                ("resolved_on", pl.Datetime), ("status", pl.Utf8)],
        orient="row",
    ).with_columns(
        benefitted=pl.lit("No"), mode=pl.lit("m"), mode_id=pl.lit(1, pl.Int64),
        dept=pl.lit("d"), dept_id=pl.lit(40, pl.Int64), category=pl.lit("c"),
        subcategory=pl.lit("s"), district=pl.lit("Puri"),
        transfer_status=pl.lit("No"), office=pl.lit("Departments"),
    ).write_parquet(tmp_path / "complaints.parquet")
    pl.DataFrame(
        _ACTIONS,
        schema=[("id", pl.Int64), ("ticket_no", pl.Utf8),
                ("action_taken_date", pl.Datetime), ("action_status", pl.Utf8),
                ("complaint_status_with_authority", pl.Utf8)],
        orient="row",
    ).write_parquet(tmp_path / "action_history.parquet")

    connection = marts.open_lake(
        "grievance_journey", lake_dir=tmp_path,
        tables=("complaints", "action_history"),
    )
    # `g` is the caller's own base view; journey reads it for scope.
    connection.execute("CREATE OR REPLACE VIEW g AS SELECT * FROM grievance_base")
    try:
        yield connection
    finally:
        connection.close()


def _rows(con, sql):
    return con.execute(sql).pl()


# --- route -------------------------------------------------------------------


def test_route_collapses_repeats_of_the_same_level(con):
    journey.install_steps(con)
    journey.install_route(con)
    got = _rows(con, "SELECT rungs, levels_touched FROM route WHERE ticket_no = 'R1'")
    # 1, 3, 3, 5, 3 -> 1 > 3 > 5 > 3: the repeat collapses, the return does not.
    assert got["rungs"].to_list()[0] == [1, 3, 5, 3]
    assert got["levels_touched"][0] == 3


def test_office_route_separates_two_offices_at_the_same_level(con):
    """The reason the route is not the rung ladder.

    R5 is Collector -> district social security officer -> a directorate. The
    first two both sit at rung 3, so a level route says the claim went to "the
    district" once and stops. The office route says which two desks held it.
    """
    journey.install_steps(con)
    journey.install_route(con)
    got = _rows(con, "SELECT rungs, levels_touched, roles, offices_touched "
                     "FROM route WHERE ticket_no = 'R5'")
    assert got["rungs"].to_list()[0] == [3, 2]
    assert got["levels_touched"][0] == 2
    assert got["roles"].to_list()[0] == [
        "District Collector", "District social security officer",
        "Other state office"]
    assert got["offices_touched"][0] == 3


def test_office_route_collapses_a_repeated_spell_in_one_role(con):
    """R1 is worked twice by the same Collector: one step, not two."""
    journey.install_steps(con)
    journey.install_route(con)
    got = _rows(con, "SELECT roles FROM route WHERE ticket_no = 'R1'")
    assert got["roles"].to_list()[0] == [
        "Chief Minister's Grievance Cell", "District Collector",
        "Block Development Officer", "District Collector"]


def test_route_excludes_grievances_with_an_unrecognised_office(con):
    journey.install_steps(con)
    journey.install_route(con)
    assert _rows(con, "SELECT * FROM route WHERE ticket_no = 'R3'").is_empty()


def test_route_keeps_a_single_office_grievance_as_one_level(con):
    journey.install_steps(con)
    journey.install_route(con)
    got = _rows(con, "SELECT rungs, levels_touched FROM route WHERE ticket_no = 'R2'")
    assert got["rungs"].to_list()[0] == [3]
    assert got["levels_touched"][0] == 1


# --- office_wait -------------------------------------------------------------


def test_office_wait_is_one_row_per_handoff_with_the_gap_to_the_next_action(con):
    journey.install_office_wait(con)
    got = _rows(con, """SELECT role_name, place, wait_days FROM office_wait
                        WHERE ticket_no = 'R1' ORDER BY wait_days""")
    # Four dated actions have a successor; the CM cell row has no role_name and
    # is dropped, leaving Collector(3->5), Collector(5->9), BDO(9->20).
    assert got["role_name"].to_list() == [
        "District Collector", "District Collector", "Block Development Officer"]
    assert got["wait_days"].to_list() == [2, 4, 11]
    assert set(got["place"]) == {"Puri", "Sadar"}


def test_office_wait_drops_the_last_action_which_has_no_successor(con):
    journey.install_office_wait(con)
    n = con.execute(
        "SELECT COUNT(*) FROM office_wait WHERE ticket_no = 'R2'").fetchone()[0]
    # Two actions, so exactly one gap.
    assert n == 1


# --- transferred -------------------------------------------------------------


def test_transferred_finds_the_action_event_not_the_complaints_flag(con):
    journey.install_transferred(con)
    got = _rows(con, "SELECT ticket_no FROM transferred ORDER BY ticket_no")
    # Every fixture complaint has transfer_status 'No'; only R4 has the event.
    assert got["ticket_no"].to_list() == ["R4"]


# --- phases still tile over the scoped tables --------------------------------


def test_phases_tile_and_review_is_zero_when_never_sent_back_up(con):
    journey.install_steps(con)
    journey.install_phase_bounds(con)
    chk = journey.install_phases(con)
    assert chk["tiling_rows"] >= 1
    got = _rows(con, """SELECT ticket_no, registration, first_assignment,
                          field_action, review, closure, days_to_close
                        FROM phases_clean ORDER BY ticket_no""")
    for row in got.iter_rows(named=True):
        parts = sum(row[p] for p in journey.PHASES)
        assert parts == row["days_to_close"]
    # R2 never leaves one office, so it is never sent back up.
    r2 = got.filter(pl.col("ticket_no") == "R2")
    if not r2.is_empty():
        assert r2["review"][0] == 0
