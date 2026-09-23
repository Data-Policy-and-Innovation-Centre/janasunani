"""Tests for the shared bottleneck-note machinery.

`janasunani/analytics/bottlenecks.py` is what stops the SSEPD and Panchayati
Raj notes from being two copies of one analysis, so the invariants it enforces
on their behalf -- the funnel only narrows, the entry-office partition loses
nobody, the by-office time table is one row per office -- are pinned here
rather than only inside a notebook that nothing runs in CI.

Runs against the same hand-built fixture lake as the mart tests, never the
live one. `check_note` is not exercised here: it asserts statewide published
counts, which a seven-row fixture cannot reproduce, and it is a free function
precisely so the class can be tested without it.
"""

import matplotlib
import polars as pl
import pytest

matplotlib.use("Agg")

from janasunani.analytics import marts  # noqa: E402
from janasunani.analytics.bottlenecks import (  # noqa: E402
    ENTRY_OFFICES, ROLE_CASE, Bottlenecks,
)
from janasunani.analytics.journey import SHORT_ROLE  # noqa: E402
from tests.test_grievance_journey_mart import _write_lake  # noqa: E402

# G1 and G2 are the two dept_id 40 rows in the fixture, both subcategory OAP;
# G2 is the later filing and is marked a repeat of G1 here so the dedup join
# has something to collapse.
_DEDUP = pl.DataFrame(
    [("G1", "grp1", 2), ("G2", "grp1", 2)],
    schema=[("ticket_no", pl.Utf8), ("duplicate_group_id", pl.Utf8),
            ("group_size", pl.Int64)],
    orient="row",
)


@pytest.fixture
def b(tmp_path):
    lake = _write_lake(tmp_path / "lake")
    csv = tmp_path / "dedup.csv"
    _DEDUP.write_csv(csv)
    con = marts.open_lake("grievance_journey", lake_dir=lake,
                          tables=("complaints", "action_history"))
    try:
        yield Bottlenecks(
            con, dept_id=40, dept="Fixture dept", subcategory="OAP",
            claim="pension claim", dedup_csv=csv, figdir=tmp_path / "figs",
            prefix="t_", first_fy=2023, last_fy=2024)
    finally:
        con.close()


def test_repeat_filings_collapse_to_the_earliest(b):
    got = b.q("SELECT ticket_no, is_primary FROM g ORDER BY ticket_no")
    # G1 (30 Jun 2024) is the first filing of the group, G2 (1 Jul 2024) a repeat.
    assert dict(zip(got["ticket_no"], got["is_primary"])) == {
        "G1": True, "G2": False}


def test_funnel_only_ever_narrows(b):
    for a, c in [("S0", "S1"), ("S1", "S2"), ("S2", "S3"), ("P0", "P1"),
                 ("P1", "P2")]:
        assert b.FUNNEL[a] >= b.FUNNEL[c]
    assert b.FUNNEL["P0"] <= b.FUNNEL["S1"]
    assert b.FUNNEL["S0"] == 2          # G1 and G2
    assert b.FUNNEL["S1"] == 1          # G2 collapses into G1


def test_the_entry_office_partition_loses_nobody(b):
    """The whole point of the partition asserts, restated as a test.

    A NULL `office` must land in "Other or not recorded", not vanish: the
    fixture's G7 is exactly that case in the mart tests, and a bare
    `office = 'Departments'` comparison would drop it.
    """
    for key in ("S2", "P1", "P2"):
        parts = sum(b.q(f"SELECT COUNT(*) AS n FROM g "
                        f"WHERE {b.WHERE[key]} AND ({p})")["n"][0]
                    for _, p in ENTRY_OFFICES)
        assert parts == b.FUNNEL[key]


def test_every_grievance_falls_in_exactly_one_entry_group(b):
    """Not just complete but disjoint -- a claim counted twice also sums right."""
    overlaps = b.q(f"""SELECT COUNT(*) AS n FROM g WHERE (
        {' + '.join(f'CASE WHEN {p} THEN 1 ELSE 0 END' for _, p in ENTRY_OFFICES)}
    ) <> 1""")["n"][0]
    assert overlaps == 0


def test_by_office_time_table_is_long_by_office(b):
    """One row per entry point, statistics across -- not the transpose."""
    got = b.entry_time_table()
    assert got.columns == ["Entry point", "Disposed", "Median days",
                           "Mean days", "Slowest tenth"]
    assert got["Entry point"].to_list() == [lab for lab, _ in ENTRY_OFFICES]


def test_step_note_names_its_sample_and_its_size(b):
    note = b.note("S1", "Extra.")
    assert note.startswith(f"S1 · {b.FUNNEL['S1']:,} — ")
    assert note.endswith("Extra.")


def test_funnel_table_shows_each_step_against_the_one_above(b):
    got = b.funnel_table(["S0", "S1"])
    assert got["% of the step above"].to_list() == [
        None, round(100 * b.FUNNEL["S1"] / b.FUNNEL["S0"], 1)]


def test_the_route_role_case_is_valid_sql_for_every_role(b):
    """One role carries an apostrophe, and a raw f-string ends the literal.

    The generated CASE is not exercised by any table this fixture builds, so
    it is run here against the role names themselves: an unescaped quote is a
    parser error, and a wrong branch is a wrong short name.
    """
    values = ", ".join("('" + k.replace("'", "''") + "')" for k in SHORT_ROLE)
    got = b.q(f"SELECT x, {ROLE_CASE} AS short FROM (VALUES {values}) t(x)")
    assert dict(zip(got["x"], got["short"])) == SHORT_ROLE
    # A role the map does not name passes through rather than becoming NULL.
    assert b.q(f"SELECT {ROLE_CASE} AS s FROM (VALUES "
               "('Other district office')) t(x)")["s"][0] == "Other district office"
