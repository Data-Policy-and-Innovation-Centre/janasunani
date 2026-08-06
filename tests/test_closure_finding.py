"""Tests for the closure finding (#76) and the mart it reads.

Every assertion runs against a hand-built fixture lake, never against the real
one: the point of the finding is a number, and a test that reads live data
cannot tell a correct query from a query that happens to agree with today's
corpus.

The fixture is arranged so each headline figure has an independently countable
answer, and so the two denominators genuinely differ — collapsing them is the
failure mode this finding is most exposed to.
"""

import re
from datetime import datetime

import polars as pl
import pytest

from janasunani.analytics import marts
from janasunani.analytics.findings import closure

# --- the fixture corpus -----------------------------------------------------
#
# 10 complaints. 8 resolved, 2 still pending (so "resolved" is not "all rows").
# Of the 8 resolved:
#   T1  bare        1 step,  1 day    <- two-day bare, single step
#   T2  bare        1 step,  2 days   <- two-day bare, single step
#   T3  bare        4 steps, 40 days
#   T4  with_action 3 steps, 12 days
#   T5  with_action 2 steps, 5 days
#   T6  benefit     2 steps, 9 days   <- benefitted = Yes
#   T7  off_ladder  2 steps, 30 days  (a discard template)
#   T8  off_ladder  0 steps, 3 days   (no action history at all)
#
# So: resolved = 8, ladder = 6, bare = 3, with_action = 2, benefit = 1,
# off_ladder = 2. Bare share of ladder = 3/6 = 50.0%. Bare share of resolved =
# 3/8 = 37.5%. The gap between those two is the thing that must not collapse.

_BARE = "The grievance has been disposed."
_BARE_ALT = "the grievance has been resolved"  # no full stop, already lowercase
_WITH_ACTION = "The grievance has been disposed with appropriate action."
_WITH_ACTION_ALT = "The  grievance  has been resolved with appropriate action."
_BENEFIT = "The grievance has been disposed & beneficiary benefited."
_OFF_LADDER = "Not within the purview of this office."

_COMPLAINTS = [
    ("T1", datetime(2024, 1, 1), datetime(2024, 1, 2), None, "Water", "Puri", "RWSS"),
    ("T2", datetime(2024, 1, 1), datetime(2024, 1, 3), "No", "Water", "Puri", "RWSS"),
    ("T3", datetime(2024, 1, 1), datetime(2024, 2, 10), None, "Roads", "Cuttack", "Works"),
    ("T4", datetime(2024, 1, 1), datetime(2024, 1, 13), None, "Roads", "Cuttack", "Works"),
    ("T5", datetime(2024, 1, 1), datetime(2024, 1, 6), "No", "Water", "Puri", "RWSS"),
    ("T6", datetime(2024, 1, 1), datetime(2024, 1, 10), "Yes", "Welfare", "Puri", "W&CD"),
    ("T7", datetime(2024, 1, 1), datetime(2024, 1, 31), None, "Roads", "Cuttack", "Works"),
    ("T8", datetime(2024, 1, 1), datetime(2024, 1, 4), None, "Water", "Puri", "RWSS"),
    ("T9", datetime(2024, 1, 1), None, None, "Water", "Puri", "RWSS"),
    ("T10", datetime(2024, 1, 1), None, None, "Roads", "Cuttack", "Works"),
]

# (id, ticket, date, remark). Deliberately not in date order, so the
# closing-remark pick is exercised rather than accidentally satisfied.
_ACTIONS = [
    (1, "T1", datetime(2024, 1, 2), _BARE),
    (2, "T2", datetime(2024, 1, 3), _BARE_ALT),
    (3, "T3", datetime(2024, 1, 5), "Forwarded to the executive engineer."),
    (4, "T3", datetime(2024, 1, 20), "ATR submitted."),
    (5, "T3", datetime(2024, 2, 1), "Reminder issued."),
    (6, "T3", datetime(2024, 2, 10), _BARE),
    (7, "T4", datetime(2024, 1, 13), _WITH_ACTION),
    (8, "T4", datetime(2024, 1, 4), "Forwarded to the executive engineer."),
    (9, "T4", datetime(2024, 1, 9), "ATR submitted."),
    (10, "T5", datetime(2024, 1, 2), "Forwarded to the executive engineer."),
    (11, "T5", datetime(2024, 1, 6), _WITH_ACTION_ALT),
    (12, "T6", datetime(2024, 1, 3), "Forwarded to the block officer."),
    (13, "T6", datetime(2024, 1, 10), _BENEFIT),
    (14, "T7", datetime(2024, 1, 8), "Forwarded to the collector."),
    (15, "T7", datetime(2024, 1, 31), _OFF_LADDER),
    # T8 has no action history at all.
    (16, "T9", datetime(2024, 1, 5), "Pending with the block officer."),
    # A stray action row whose ticket is not in complaints at all.
    (17, "T404", datetime(2024, 1, 5), _BARE),
]


def _write_lake(path, complaints=_COMPLAINTS, actions=_ACTIONS, grievance=None):
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
        # The real lake carries the citizen's own text. The mart must not read
        # it even when it is right there.
        complaints = [(*row, grievance) for row in complaints]
        schema = [*schema, ("grievance", pl.Utf8)]
    pl.DataFrame(complaints, schema=schema, orient="row").write_parquet(
        path / "complaints.parquet"
    )

    pl.DataFrame(
        [(i, t, d, r, "Disposed") for i, t, d, r in actions],
        schema=[
            ("id", pl.Int64),
            ("ticket_no", pl.Utf8),
            ("action_taken_date", pl.Datetime),
            ("action_taken_remark", pl.Utf8),
            ("action_status", pl.Utf8),
        ],
        orient="row",
    ).write_parquet(path / "action_history.parquet")
    return path


@pytest.fixture
def lake(tmp_path):
    return _write_lake(tmp_path)


# --- the mart loader --------------------------------------------------------


def test_mart_sql_is_read_verbatim_from_the_sql_directory():
    """The SQL is the deliverable, so nothing may rewrite it on the way out."""
    assert closure.sql_text() == marts.mart_path("closure").read_text()
    assert "CREATE OR REPLACE VIEW closure_finding_summary" in closure.sql_text()


def test_unknown_mart_names_the_ones_that_exist():
    with pytest.raises(FileNotFoundError, match="closure"):
        marts.mart_sql("no_such_mart")


def test_open_lake_installs_the_mart_over_the_lake_tables(lake):
    con = marts.open_lake("closure", lake_dir=lake)
    try:
        assert con.execute("SELECT count(*) FROM closure_rung").fetchone()[0] == 8
    finally:
        con.close()


def test_the_ladder_lives_only_in_the_sql(lake):
    """Six templates, one source. They are not duplicated in Python."""
    sql = closure.sql_text()
    for template in (
        "the grievance has been disposed",
        "the grievance has been resolved",
        "the grievance has been disposed with appropriate action",
        "the grievance has been resolved with appropriate action",
        "the grievance has been disposed & beneficiary benefited",
        "the grievance has been resolved & beneficiary benefited",
    ):
        assert f"'{template}'" in sql

    con = marts.open_lake("closure", lake_dir=lake)
    try:
        rungs = con.execute(
            "SELECT rung, count(*) FROM closure_disposal_ladder GROUP BY rung"
        ).fetchall()
    finally:
        con.close()
    assert dict(rungs) == {"bare": 2, "with_action": 2, "benefit": 2}


def test_the_mart_never_reads_the_citizen_text_column(tmp_path):
    """Not reading `complaints.grievance` is why this finding needs no
    redaction pass, no slice decision and no gold set. It is a scope claim the
    whole issue rests on, so it is asserted rather than asserted-in-a-comment.

    Checked twice: the SQL never names the column, and a lake that *has* the
    column never surfaces it. The static half strips comments and string
    literals first — the disposal templates themselves contain the word
    "grievance", so a plain substring search would pass for the wrong reason.
    """
    sql = re.sub(r"--[^\n]*", "", closure.sql_text())
    sql = re.sub(r"'[^']*'", "''", sql)
    assert "grievance" not in sql.replace("closure_", "")

    tables = closure.compute(_write_lake(tmp_path, grievance="My hand pump is broken"))
    for frame in tables.values():
        assert "grievance" not in frame.columns
    assert "hand pump" not in closure.render_markdown(tables)


# --- the headline -----------------------------------------------------------


def test_headline_reports_both_denominators_and_they_differ(lake):
    row = closure.compute(lake)["closure_finding_summary"].row(0, named=True)

    assert row["resolved_complaints"] == 8  # the two pending rows are excluded
    assert row["ladder_closures"] == 6
    assert row["bare"] == 3
    assert row["with_action"] == 2
    assert row["benefit"] == 1
    assert row["claims_action"] == 3
    assert row["off_ladder"] == 2

    # The whole point of the finding: the same 3 complaints, two denominators.
    assert row["bare_share_of_ladder_pct"] == pytest.approx(50.0)
    assert row["bare_share_of_resolved_pct"] == pytest.approx(37.5)
    assert row["ladder_coverage_pct"] == pytest.approx(75.0)
    assert row["off_ladder_share_pct"] == pytest.approx(25.0)

    # Reconciliation, written independently of the SQL: the rungs partition the
    # resolved complaints, and the ladder is exactly the non-off_ladder part.
    assert row["bare"] + row["claims_action"] == row["ladder_closures"]
    assert row["ladder_closures"] + row["off_ladder"] == row["resolved_complaints"]
    assert row["with_action"] + row["benefit"] == row["claims_action"]


def test_rung_assignment_normalizes_case_whitespace_and_trailing_stops(lake):
    """T2's remark has no trailing stop; T5's has a doubled internal space."""
    row = closure.compute(lake)["closure_finding_summary"].row(0, named=True)
    assert row["bare"] == 3  # T1, T2, T3 — T2 only if normalization works
    assert row["with_action"] == 2  # T4, T5 — T5 only if whitespace collapses


def test_closing_remark_is_the_latest_action_not_the_first_row(lake):
    """T4's disposal is stored ahead of its earlier steps in row order.

    If the closing remark were taken by row order it would be the *forwarded*
    remark and T4 would fall off the ladder entirely.
    """
    cell = (
        closure.compute(lake)["closure_by_trajectory"]
        .filter(
            (pl.col("steps_bucket") == "3-5 steps")
            & (pl.col("elapsed_bucket") == "8-30 days")
        )
        .row(0, named=True)
    )
    assert cell["ladder_closures"] == 1
    assert cell["bare"] == 0


def test_actions_for_unknown_tickets_do_not_invent_complaints(lake):
    """T404's action row has no complaint. It must not reach any count."""
    row = closure.compute(lake)["closure_finding_summary"].row(0, named=True)
    assert row["resolved_complaints"] == 8


# --- the required controls --------------------------------------------------


def test_trajectory_conditioning_separates_two_day_closures_from_worked_cases(lake):
    traj = closure.compute(lake)["closure_by_trajectory"]

    fast = traj.filter(
        (pl.col("steps_bucket") == "1 step") & (pl.col("elapsed_bucket") == "0-2 days")
    ).row(0, named=True)
    assert fast["resolved_complaints"] == 2  # T1, T2
    assert fast["bare"] == 2
    assert fast["bare_share_of_ladder_pct"] == pytest.approx(100.0)

    worked = traj.filter(
        (pl.col("steps_bucket") == "3-5 steps") & (pl.col("elapsed_bucket") == "31+ days")
    ).row(0, named=True)
    assert worked["resolved_complaints"] == 1  # T3: four steps, forty days
    assert worked["bare"] == 1

    # The buckets partition the resolved complaints, exactly once each.
    assert traj["resolved_complaints"].sum() == 8

    # A complaint with no action history lands in the zero-step bucket rather
    # than being silently dropped.
    none = traj.filter(
        (pl.col("steps_bucket") == "1 step") & (pl.col("elapsed_bucket") == "3-7 days")
    ).row(0, named=True)
    assert none["resolved_complaints"] == 1  # T8, zero actions


def test_two_day_bare_subfinding(lake):
    row = closure.compute(lake)["closure_two_day_bare"].row(0, named=True)

    assert row["two_day_bare"] == 2  # T1 (1 day), T2 (2 days)
    assert row["two_day_bare_single_step"] == 2
    assert row["bare"] == 3
    assert row["share_of_bare_pct"] == pytest.approx(200.0 / 3)
    assert row["share_of_ladder_pct"] == pytest.approx(200.0 / 6)
    assert row["share_of_resolved_pct"] == pytest.approx(25.0)

    # T3 is a bare disposal too, but it took forty days over four steps. If the
    # boundary leaked it would show up here.
    assert row["two_day_bare"] < row["bare"]


def test_benefitted_overlap_is_reported_so_the_third_rung_is_not_claimed_novel(lake):
    pairs = {
        (r["rung"], r["benefitted_value"]): r["resolved_complaints"]
        for r in closure.compute(lake)["closure_benefitted_overlap"].iter_rows(named=True)
    }
    assert pairs[("benefit", "yes")] == 1  # T6 carries both signals
    assert pairs[("bare", "(null)")] == 2  # T1, T3
    assert pairs[("bare", "no")] == 1  # T2
    assert sum(pairs.values()) == 8


# --- the guards -------------------------------------------------------------


def test_off_ladder_templates_never_leak_low_frequency_text(lake):
    """The coverage check is bounded to templates used 1,000+ times.

    The fixture's off-ladder remark appears twice, so the view must be empty: a
    string used twice is not a dropdown template, it is somebody's writing.
    """
    assert closure.compute(lake)["closure_off_ladder_templates"].height == 0


def test_template_drift_is_reported_rather_than_silently_shrinking_the_base(tmp_path):
    """An unmatched ladder string does not error — it moves complaints into
    `off_ladder` and quietly shrinks the denominator the headline divides by."""
    drifted = [(i, t, d, r.replace("grievance", "petition")) for i, t, d, r in _ACTIONS]
    tables = closure.compute(_write_lake(tmp_path, actions=drifted))
    assert tables["closure_finding_summary"].row(0, named=True)["ladder_closures"] == 0
    warning = closure.check_ladder_coverage(tables)
    assert warning is not None
    assert "drifted" in warning


def test_healthy_coverage_raises_no_drift_warning(lake):
    assert closure.check_ladder_coverage(closure.compute(lake)) is None


def test_markdown_never_states_the_headline_without_its_base(lake):
    md = closure.render_markdown(closure.compute(lake))

    assert "50.0%" in md  # the ladder share
    assert "37.5%" in md  # and the all-resolved figure, not optional
    assert "**not** a failure rate" in md
    assert "300-500 closures adjudicated by hand" in md
    assert "Insight" in md
    # No citizen text, and no per-office breakdown, ever.
    assert _OFF_LADDER not in md


def test_write_emits_the_tables_the_markdown_and_the_handed_over_sql(lake, tmp_path):
    written = closure.write(closure.compute(lake), tmp_path / "findings")

    for view in closure.REPORT_VIEWS:
        assert written[view].exists()
    assert written["markdown"].read_text().startswith("## How cases are closed")
    # The deliverable is the view definition, so it ships beside the numbers.
    assert written["sql"].read_text() == closure.sql_text()

    # The two complaint-level views stay intermediate: no per-complaint file.
    names = {p.name for p in (tmp_path / "findings").iterdir()}
    assert not {n for n in names if n.startswith(("closure_rung", "closure_closing"))}
