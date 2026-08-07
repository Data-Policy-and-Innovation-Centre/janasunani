"""Tests for the closure finding (#76) and the mart it reads.

Every assertion runs against a hand-built fixture lake, never against the real
one: the point of the finding is a number, and a test that reads live data
cannot tell a correct query from a query that happens to agree with today's
corpus.

The fixture is arranged so each headline figure has an independently countable
answer, and so the two denominators genuinely differ. Collapsing them is the
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
    literals first: the disposal templates themselves contain the word
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
    assert row["bare"] == 3  # T1, T2, T3; T2 only if normalization works
    assert row["with_action"] == 2  # T4, T5; T5 only if whitespace collapses


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


def test_actions_after_resolution_are_not_treated_as_the_closing_action(tmp_path):
    """A reopen or audit row filed after `resolved_on` did not close the case.

    Taking the latest row unconditionally would pick it as the closing remark,
    knocking a complaint off the ladder whose actual disposal matched, and
    would count post-closure activity as work done before closure.
    """
    complaints = [
        ("R1", datetime(2024, 1, 1), datetime(2024, 1, 10), None, "Water", "Puri", "RWSS")
    ]
    actions = [
        (1, "R1", datetime(2024, 1, 2), "Forwarded to the executive engineer."),
        (2, "R1", datetime(2024, 1, 10), _BARE),
        # Six weeks later: an audit note. Not a closing action.
        (3, "R1", datetime(2024, 2, 20), "Audit observation recorded."),
    ]
    tables = closure.compute(_write_lake(tmp_path, complaints, actions))

    row = tables["closure_finding_summary"].row(0, named=True)
    assert row["bare"] == 1  # still on the ladder
    assert row["off_ladder"] == 0

    # And the audit row is not counted as a step of work before closure.
    traj = tables["closure_by_trajectory"].row(0, named=True)
    assert traj["steps_bucket"] == "2 steps"


def test_undated_actions_are_quarantined_from_the_closing_trajectory(tmp_path):
    """Unknown timing cannot establish either a closure or its work count."""
    complaints = [
        ("U1", datetime(2024, 1, 1), datetime(2024, 1, 10), None, "Water", "Puri", "RWSS")
    ]
    actions = [
        (1, "U1", datetime(2024, 1, 10), _BARE),
        (2, "U1", None, "Undated free-text follow-up."),
    ]
    tables = closure.compute(_write_lake(tmp_path, complaints, actions))

    assert tables["closure_finding_summary"].row(0, named=True)["bare"] == 1
    assert tables["closure_by_trajectory"].row(0, named=True)["steps_bucket"] == "1 step"


def test_negative_durations_are_quarantined_rather_than_read_as_fast_closures(tmp_path):
    """`resolved_on` before `created_on` happens: the two timestamps are parsed
    independently at ingest and nothing enforces an order. Such a row is bad
    data, not a two-day closure."""
    complaints = [
        # Resolved four days *before* it was created.
        ("N1", datetime(2024, 1, 10), datetime(2024, 1, 6), None, "Water", "Puri", "RWSS"),
    ]
    actions = [(1, "N1", datetime(2024, 1, 6), _BARE)]
    tables = closure.compute(_write_lake(tmp_path, complaints, actions))

    assert tables["closure_two_day_bare"].row(0, named=True)["two_day_bare"] == 0
    buckets = tables["closure_by_trajectory"]["elapsed_bucket"].to_list()
    assert buckets == ["invalid"]


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

    # A complaint with no action history gets its own bucket rather than being
    # dropped, and rather than being folded in with genuinely one-step cases.
    none = traj.filter(
        (pl.col("steps_bucket") == "0 steps") & (pl.col("elapsed_bucket") == "3-7 days")
    ).row(0, named=True)
    assert none["resolved_complaints"] == 1  # T8, zero actions
    # T1 and T2 genuinely have one action row each, and stay separate from it.
    assert traj.filter(pl.col("steps_bucket") == "1 step")["resolved_complaints"].sum() == 2


def test_two_day_bare_subfinding(lake):
    row = closure.compute(lake)["closure_two_day_bare"].row(0, named=True)

    assert row["two_day_bare"] == 2  # T1 (1 day), T2 (2 days)
    # One-step histories are not the portal's three-row floor trajectory.
    assert row["two_day_bare_min_trajectory"] == 0
    assert row["bare"] == 3
    assert row["share_of_bare_pct"] == pytest.approx(200.0 / 3)
    assert row["share_of_ladder_pct"] == pytest.approx(200.0 / 6)
    assert row["share_of_resolved_pct"] == pytest.approx(25.0)

    # T3 is a bare disposal too, but it took forty days over four steps. If the
    # boundary leaked it would show up here.
    assert row["two_day_bare"] < row["bare"]


def test_min_trajectory_separates_the_floor_case_from_a_fast_worked_case(tmp_path):
    """Two days is fast. Two days *after four steps of work* is a different
    case, and the floor column has to tell them apart.

    The threshold is three action rows, not one: the portal writes a create and
    an assign row before an officer can dispose, so a one-step disposal does not
    exist in this record.
    """
    complaints = [
        ("F1", datetime(2024, 1, 1), datetime(2024, 1, 2), None, "Water", "Puri", "RWSS"),
        ("F2", datetime(2024, 1, 1), datetime(2024, 1, 3), None, "Water", "Puri", "RWSS"),
    ]
    actions = [
        # F1: the floor -- created, assigned, disposed.
        (1, "F1", datetime(2024, 1, 1), "Complaint registered."),
        (2, "F1", datetime(2024, 1, 1), "Assigned to the block officer."),
        (3, "F1", datetime(2024, 1, 2), _BARE),
        # F2: same two days, but five steps of real movement first.
        (4, "F2", datetime(2024, 1, 1), "Complaint registered."),
        (5, "F2", datetime(2024, 1, 1), "Assigned to the block officer."),
        (6, "F2", datetime(2024, 1, 2), "Forwarded to the executive engineer."),
        (7, "F2", datetime(2024, 1, 2), "ATR submitted."),
        (8, "F2", datetime(2024, 1, 3), _BARE),
    ]
    row = closure.compute(_write_lake(tmp_path, complaints, actions))[
        "closure_two_day_bare"
    ].row(0, named=True)

    assert row["two_day_bare"] == 2  # both are two-day bare disposals
    assert row["two_day_bare_min_trajectory"] == 1  # only F1 is the floor case


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
    """An unmatched ladder string does not error. It moves complaints into
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

    # The trajectory table carries its own base too. Without it, a cell reading
    # "4 templated closures" looks like thin data when it is actually tens of
    # thousands of complaints that closed on a discard template instead.
    assert "| Action steps | Elapsed | Resolved | Templated closures |" in md
    assert "| 0 steps | 3-7 days | 1 | 0 | 0 | n/a |" in md  # T8, zero actions


def test_write_emits_the_tables_the_markdown_and_the_handed_over_sql(lake, tmp_path):
    out = tmp_path / "findings"
    written = closure.write(closure.compute(lake), out)

    for view in closure.FINDING_VIEWS:
        assert written[view].exists()
    assert written["markdown"].read_text().startswith("## How cases are closed")
    # The deliverable is the view definition, so it ships beside the numbers.
    assert written["sql"].read_text() == closure.sql_text()

    # The two complaint-level views stay intermediate: no per-complaint file.
    names = {p.name for p in out.iterdir()}
    assert not {n for n in names if n.startswith(("closure_rung", "closure_closing"))}


def test_the_remark_diagnostic_is_aggregate_only_before_it_lands_in_outputs(lake, tmp_path):
    """`closure_off_ladder_templates` is the only output carrying remark text.

    A 1,000-use floor is not proof, so even `diagnostics/`, which the recursive
    deliver target can copy, receives aggregate counts only.
    """
    out = tmp_path / "findings"
    tables = closure.compute(lake)
    closure.write(tables, out)
    assert not (out / "closure_off_ladder_templates.csv").exists()

    written = closure.write_diagnostics(tables, out)
    assert written["closure_off_ladder_templates"].parent.name == "diagnostics"
    diagnostic = pl.read_csv(out / "diagnostics" / "closure_off_ladder_templates.csv")
    assert diagnostic.columns == [
        "high_volume_off_ladder_remarks",
        "affected_resolved_complaints",
    ]
    assert _OFF_LADDER not in diagnostic.write_csv()


def test_the_cli_refuses_to_publish_when_the_ladder_guard_fails(tmp_path, monkeypatch):
    """A warning beside the artifacts is not a guard.

    A batch caller keeping stdout and dropping stderr would publish exactly the
    number `check_ladder_coverage` says must not be quoted.
    """
    drifted = [(i, t, d, r.replace("grievance", "petition")) for i, t, d, r in _ACTIONS]
    lake = _write_lake(tmp_path, actions=drifted)
    out = tmp_path / "findings"
    monkeypatch.setattr(
        "sys.argv", ["janasunani-closure-finding", "--lake-dir", str(lake), "--out-dir", str(out)]
    )

    with pytest.raises(SystemExit) as exit_info:
        closure.main()
    assert exit_info.value.code == 1

    assert not (out / "closure_finding.md").exists()
    assert not (out / "closure_finding_summary.csv").exists()
    # The diagnostic is still written: it is what tells you why.
    assert (out / "diagnostics" / "closure_off_ladder_templates.csv").exists()


def test_high_volume_off_ladder_text_refuses_publication(tmp_path, monkeypatch):
    """Frequency does not make raw text a safe or approved template."""
    actions = [
        (i, f"O{i}", datetime(2024, 1, 2), "Unapproved free-text template")
        for i in range(1, 1001)
    ]
    complaints = [
        (f"O{i}", datetime(2024, 1, 1), datetime(2024, 1, 2), None, "Water", "Puri", "RWSS")
        for i in range(1, 1001)
    ]
    lake = _write_lake(tmp_path / "lake", complaints, actions)
    out = tmp_path / "findings"
    monkeypatch.setattr(
        "sys.argv", ["janasunani-closure-finding", "--lake-dir", str(lake), "--out-dir", str(out)]
    )

    with pytest.raises(SystemExit, match="1"):
        closure.main()

    diagnostic = (out / "diagnostics" / "closure_off_ladder_templates.csv").read_text()
    assert "free-text" not in diagnostic
    assert "Unapproved" not in diagnostic


def test_failed_guard_removes_stale_shareable_artifacts(tmp_path, monkeypatch):
    """A failed rerun must not leave an earlier valid report for delivery."""
    healthy = tmp_path / "healthy"
    healthy.mkdir()
    out = tmp_path / "findings"
    closure.write(closure.compute(_write_lake(healthy)), out)
    drifted = [(i, t, d, r.replace("grievance", "petition")) for i, t, d, r in _ACTIONS]
    failed = tmp_path / "failed"
    failed.mkdir()
    lake = _write_lake(failed, actions=drifted)
    monkeypatch.setattr(
        "sys.argv", ["janasunani-closure-finding", "--lake-dir", str(lake), "--out-dir", str(out)]
    )

    with pytest.raises(SystemExit, match="1"):
        closure.main()

    assert not (out / "closure_finding.md").exists()
    assert not (out / "closure_finding_summary.csv").exists()
    assert not (out / "closure_finding.sql").exists()


@pytest.mark.parametrize(
    ("finding", "view", "renderer"),
    [
        (
            "closure_recording_no_action",
            "closure_finding_summary",
            closure.render_headline_markdown,
        ),
        (
            "two_day_bare_closures",
            "closure_two_day_bare",
            closure.render_two_day_markdown,
        ),
    ],
)
def test_each_closure_finding_has_its_own_aggregate_artifacts(
    lake, tmp_path, finding, view, renderer
):
    tables = closure.compute(lake)
    out = tmp_path / "single"
    written = closure.write_single_finding(tables, finding, out)

    assert set(written) == {"csv", "markdown"}
    assert pl.read_csv(written["csv"]).equals(tables[view])
    markdown = written["markdown"].read_text()
    assert markdown == renderer(tables)
    assert "**Insight.**" in markdown
    assert "complaint text" in markdown


def test_single_closure_headline_always_carries_both_denominators(lake):
    markdown = closure.render_headline_markdown(closure.compute(lake))

    assert "Closed on one of the six disposal templates" in markdown
    assert "All resolved complaints" in markdown
    assert "**50.0%**" in markdown
    assert "**37.5%**" in markdown
    assert closure.DESCRIPTIVE_CAVEAT in markdown


def test_single_two_day_finding_carries_both_relevant_shares(lake):
    markdown = closure.render_two_day_markdown(closure.compute(lake))

    assert "**2 complaints**" in markdown
    assert "66.7% of all bare disposals" in markdown
    assert "25.0% of all resolved complaints" in markdown
    assert "not proof that the closure was wrong" in markdown
