"""Tests for the grievance_journey mart.

Every assertion runs against the real SQL views over a hand-built fixture
lake, never the live one -- same discipline as test_handoff_mart.py.

The fixture is arranged so each rule the mart promises has an independently
countable answer: the July-June year boundary on both sides, the note's
online/offline mode grouping, its four-way outcome rule, the routed flag that
discards cannot carry, disposed-only timing with a backwards-dated record
excluded, the office decode and its rung ladder, and step compression.
"""

from datetime import datetime

import polars as pl
import pytest

from janasunani.analytics import marts

# ---------------------------------------------------------------------------
# Fixture complaints. Columns: ticket, created, resolved, status, benefitted,
# mode_id, dept_id, subcategory.
#
# G1  30 Jun 2024, so fy_start 2023 -- the day before the year turns.
# G2   1 Jul 2024, so fy_start 2024 -- the day it turns.
# G3  online (mode_id 5), disposed with benefit, 10 days.
# G4  offline (mode_id 2), discarded, and carries no department: the shape
#     that makes a department slice look like it discards nothing.
# G5  open, never resolved.
# G6  disposed but resolved BEFORE it was created -- days_to_close must be
#     NULL rather than negative.
# G7  disposed, benefitted spelled 'YES ' -- the rule is case- and
#     whitespace-insensitive.
# ---------------------------------------------------------------------------
_COMPLAINTS = [
    # ticket, created, resolved, status, benefitted, mode_id, dept_id, subcat, office
    ("G1", datetime(2024, 6, 30), datetime(2024, 7, 10), "Disposed", "No", 1, 40, "OAP",
     "Departments"),
    ("G2", datetime(2024, 7, 1), datetime(2024, 7, 21), "Disposed", "No", 2, 40, "OAP",
     "Office of Chief Minister"),
    ("G3", datetime(2024, 8, 1), datetime(2024, 8, 11), "Disposed", "Yes", 5, 12, "Other",
     "Collector"),
    ("G4", datetime(2024, 8, 2), None, "Discard", None, 2, 0, "Other",
     "Chief Secretary"),
    ("G5", datetime(2024, 8, 3), None, "Pending", None, 8, 12, "Other",
     "Governor"),
    ("G6", datetime(2024, 8, 4), datetime(2024, 8, 1), "Disposed", "No", 3, 12, "Other",
     "DG & IG Police"),
    # office NULL: a quarter of the real corpus, and its own category.
    ("G7", datetime(2024, 8, 5), datetime(2024, 8, 9), "Disposed", "YES ", 9, 12, "Other",
     None),
]

# Action history. Columns: id, ticket, date, status, complaint_status_with_authority.
#
# G2 walks CM cell -> Collector -> Collector -> BDO: four rows, but the repeat
#    Collector row collapses, so three compressed steps.
# G3's authority prefix does not match its own status, so `code` must be NULL
#    rather than sliced at a guessed offset.
_ACTIONS = [
    (1, "G2", datetime(2024, 7, 1), "Complaint Register",
     "Complaint Register - CM Grievance Cell"),
    (2, "G2", datetime(2024, 7, 3), "Forwarded", "Forwarded - Collector, Puri"),
    (3, "G2", datetime(2024, 7, 5), "Forwarded", "Forwarded - Collector, Puri"),
    (4, "G2", datetime(2024, 7, 9), "Forwarded", "Forwarded - BDO, Sadar"),
    (5, "G3", datetime(2024, 8, 1), "Forwarded", "Disposed - Collector, Puri"),
    (6, "G3", datetime(2024, 8, 2), "Forwarded", "Forwarded - Tehsildar, Sadar"),
    # A space before the comma. 95,957 DSSO rows and 7,323 BDO rows are
    # spelled this way in the corpus, and a `code LIKE 'DSSO,%'` test misses
    # every one of them.
    (7, "G5", datetime(2024, 8, 3), "Forwarded", "Forwarded - DSSO ,Puri"),
    (8, "G5", datetime(2024, 8, 4), "Forwarded", "Forwarded - PDDRDA,Puri"),
]


def _write_lake(path):
    path.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        _COMPLAINTS,
        schema=[
            ("ticket_no", pl.Utf8),
            ("created_on", pl.Datetime),
            ("resolved_on", pl.Datetime),
            ("status", pl.Utf8),
            ("benefitted", pl.Utf8),
            ("mode_id", pl.Int64),
            ("dept_id", pl.Int64),
            ("subcategory", pl.Utf8),
            ("office", pl.Utf8),
        ],
        orient="row",
    ).with_columns(
        mode=pl.lit("m"),
        dept=pl.lit("d"),
        category=pl.lit("c"),
        district=pl.lit("Puri"),
        transfer_status=pl.lit("No"),
    ).write_parquet(path / "complaints.parquet")
    pl.DataFrame(
        _ACTIONS,
        schema=[
            ("id", pl.Int64),
            ("ticket_no", pl.Utf8),
            ("action_taken_date", pl.Datetime),
            ("action_status", pl.Utf8),
            ("complaint_status_with_authority", pl.Utf8),
        ],
        orient="row",
    ).write_parquet(path / "action_history.parquet")
    return path


@pytest.fixture
def lake(tmp_path):
    return _write_lake(tmp_path)


def _open(lake_dir):
    return marts.open_lake(
        "grievance_journey",
        lake_dir=lake_dir,
        tables=("complaints", "action_history"),
    )


def _rows(lake_dir, sql):
    con = _open(lake_dir)
    try:
        return con.execute(sql).pl()
    finally:
        con.close()


# --- the mart loader ---------------------------------------------------------


def test_mart_sql_is_read_verbatim():
    assert marts.mart_sql("grievance_journey") == marts.mart_path(
        "grievance_journey"
    ).read_text()
    assert "CREATE OR REPLACE VIEW grievance_base" in marts.mart_sql(
        "grievance_journey"
    )


def _executable_sql(name: str) -> str:
    """The mart with its `--` comments removed.

    The scope claims below are about what the SQL *does*, so they have to be
    asserted against the statements alone: the header comment names the very
    columns the mart promises never to read, and matching it would pass a
    mart that read them while failing one that only mentioned them.
    """
    return "\n".join(
        line.split("--", 1)[0] for line in marts.mart_sql(name).splitlines()
    )


def test_mart_never_reads_citizen_prose_or_officer_names():
    """Declared scope claim, enforced rather than promised in a comment."""
    sql = _executable_sql("grievance_journey")
    assert "action_taken_by" not in sql
    # `complaints.grievance` is citizen prose. `grievance_base`/`grievance_year`
    # are this mart's own view names, so match the column reference only.
    assert ".grievance" not in sql
    assert "SELECT grievance" not in sql


def test_mart_uses_no_duckdb_only_macro():
    """It is handed to the department for PostgreSQL; MACRO is DuckDB-only."""
    assert "CREATE MACRO" not in _executable_sql("grievance_journey")
    assert "CREATE OR REPLACE MACRO" not in _executable_sql("grievance_journey")


# --- the July-June year ------------------------------------------------------


def test_fiscal_year_turns_on_1_july(lake):
    got = _rows(lake, "SELECT ticket_no, fy_start, year FROM grievance_base "
                      "WHERE ticket_no IN ('G1', 'G2') ORDER BY ticket_no")
    assert got["fy_start"].to_list() == [2023, 2024]
    assert got["year"].to_list() == ["2023-24", "2024-25"]


# --- the note's classification rules -----------------------------------------


def test_channel_follows_the_notes_mode_grouping(lake):
    got = _rows(lake, "SELECT ticket_no, channel FROM grievance_base ORDER BY ticket_no")
    assert dict(zip(got["ticket_no"], got["channel"])) == {
        "G1": "Online",    # 1  Email
        "G2": "Offline",   # 2  Physical
        "G3": "Online",    # 5  Website
        "G4": "Offline",   # 2  Physical
        "G5": "Online",    # 8  Twitter
        "G6": "Offline",   # 3  Letter
        "G7": "Online",    # 9  Mobile app
    }


def test_outcome_is_the_notes_four_way_rule(lake):
    got = _rows(lake, "SELECT ticket_no, outcome FROM grievance_base ORDER BY ticket_no")
    assert dict(zip(got["ticket_no"], got["outcome"])) == {
        "G1": "Disposed",
        "G2": "Disposed",
        "G3": "Disposed with benefit",
        "G4": "Discarded",
        "G5": "Open",
        "G6": "Disposed",
        "G7": "Disposed with benefit",   # 'YES ' -- case and space insensitive
    }


def test_discard_without_a_department_is_not_routed(lake):
    """The trap the mart exists to make visible: dept_id 0 is not a department."""
    got = _rows(lake, "SELECT ticket_no, is_routed FROM grievance_base ORDER BY ticket_no")
    assert dict(zip(got["ticket_no"], got["is_routed"]))["G4"] is False
    assert dict(zip(got["ticket_no"], got["is_routed"]))["G1"] is True


# --- disposed-only timing ----------------------------------------------------


def test_days_to_close_is_disposed_only_and_never_negative(lake):
    got = _rows(lake, "SELECT ticket_no, days_to_close FROM grievance_base "
                      "ORDER BY ticket_no")
    by_ticket = dict(zip(got["ticket_no"], got["days_to_close"]))
    assert by_ticket["G1"] == 10
    assert by_ticket["G2"] == 20
    assert by_ticket["G4"] is None    # discarded
    assert by_ticket["G5"] is None    # open
    assert by_ticket["G6"] is None    # resolved before created


# --- office decoding ---------------------------------------------------------


def test_authority_is_recovered_only_when_the_prefix_matches_the_status(lake):
    got = _rows(lake, "SELECT id, code FROM acting_office ORDER BY id")
    by_id = dict(zip(got["id"], got["code"]))
    assert by_id[2] == "Collector, Puri"
    # id 5 says 'Disposed - ...' while its action_status is 'Forwarded'.
    assert by_id[5] is None


def test_rung_ladder_runs_apex_to_field(lake):
    got = _rows(lake, "SELECT id, rung FROM acting_rung ORDER BY id")
    by_id = dict(zip(got["id"], got["rung"]))
    assert by_id[1] == 1     # CM Grievance Cell
    assert by_id[2] == 3     # Collector
    assert by_id[4] == 5     # BDO
    assert by_id[5] is None  # no code, so no rung


def test_office_is_split_into_a_readable_role_and_place(lake):
    got = _rows(lake, "SELECT id, role_name, place FROM acting_office_named "
                      "WHERE role_name IS NOT NULL ORDER BY id")
    assert got["role_name"].to_list() == [
        "Chief Minister's Grievance Cell",
        "District Collector", "District Collector",
        "Block Development Officer", "Tahasildar",
        "District social security officer", "DRDA Project Director",
    ]
    assert got["place"].to_list() == [
        "", "Puri", "Puri", "Sadar", "Sadar", "Puri", "Puri"]


def test_a_space_before_the_comma_still_names_the_role(lake):
    """The bug this view was rewritten for.

    `role_name` used to be matched against the raw code with a comma glued on
    (`code LIKE 'DSSO,%'`), which no `DSSO ,Puri` row satisfies. Everything
    that filters on `role_name IS NOT NULL` -- `journey.install_office_wait`
    among them -- then dropped the busiest acting role in the SSEPD pension
    caseload without saying so.
    """
    got = _rows(lake, "SELECT id, role_code, role_name, place "
                      "FROM acting_office_named WHERE id IN (7, 8) ORDER BY id")
    assert got["role_code"].to_list() == ["DSSO", "PDDRDA"]
    assert got["role_name"].to_list() == [
        "District social security officer", "DRDA Project Director"]
    assert got["place"].to_list() == ["Puri", "Puri"]


# --- step compression --------------------------------------------------------


def test_repeat_actions_by_one_office_collapse_to_a_single_step(lake):
    """G2 has four action rows but only three office spells."""
    got = _rows(lake, "SELECT code FROM compressed_steps WHERE ticket_no = 'G2' "
                      "ORDER BY action_taken_date, id")
    assert got["code"].to_list() == [
        "CM Grievance Cell", "Collector, Puri", "BDO, Sadar"
    ]


# --- entry route -------------------------------------------------------------


def test_entry_route_names_every_office_and_falls_back_on_null(lake):
    got = _rows(lake, "SELECT ticket_no, entry_route FROM grievance_base "
                      "ORDER BY ticket_no")
    assert dict(zip(got["ticket_no"], got["entry_route"])) == {
        "G1": "Received directly by the department",
        "G2": "Forwarded from the Chief Minister's Cell",
        "G3": "Forwarded from a District Collector",
        "G4": "Forwarded from the Chief Secretary",
        "G5": "Forwarded from the Governor's office",
        "G6": "Forwarded from the police",
        "G7": "Entry point not recorded",
    }


def test_received_directly_is_true_only_for_the_department(lake):
    got = _rows(lake, "SELECT ticket_no, received_directly FROM grievance_base "
                      "ORDER BY ticket_no")
    by_ticket = dict(zip(got["ticket_no"], got["received_directly"]))
    assert by_ticket["G1"] is True
    assert by_ticket["G3"] is False
    # NULL office must not produce NULL here -- it is "not directly received".
    assert by_ticket["G7"] is False
