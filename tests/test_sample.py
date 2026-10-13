"""Tests for `janasunani.analytics.sample` and `janasunani.analytics.exhibits`.

`Sample` is what lets a new note be a list of steps rather than a new class,
so the invariants it holds on every note's behalf are pinned here: the funnel
only narrows, the dedup collapse is optional, and two samples on one
connection do not overwrite each other's journey tables. The exhibits are
checked for the one property every chart depends on: stage spans add back to
the mean they decompose, however they are folded.

Runs over the journey-tables fixture lake (R1 to R5, all dept 40, 2024-25),
never the live one.
"""

import polars as pl
import pytest

from janasunani.analytics import exhibits
from janasunani.analytics.journey import PHASES, STAGES_FIVE, STAGES_THREE
from janasunani.analytics.sample import Sample
from tests.test_journey_tables import con  # noqa: F401  (pytest fixture)

STEPS = [
    ("F0", "Filed in 2024-25", "year = '2024-25'"),
    ("F1", "Disposed, usable closing date",
     "year = '2024-25' AND is_disposed AND days_to_close IS NOT NULL"),
]


def _sample(con, ns, base="TRUE"):  # noqa: F811
    s = Sample(con, ns=ns, base=base, steps=STEPS)
    s.install_route("F1", "F1")
    s.install_phases("F1", "F1")
    return s


def test_funnel_narrows_and_opens_p3_p4(con):  # noqa: F811
    s = _sample(con, "a_")
    assert s.FUNNEL["F0"] == s.FUNNEL["F1"] == 5
    # R3 passes an office the ladder cannot place, so it has no route.
    assert s.FUNNEL["P3"] == 4
    assert s.FUNNEL["P4"] <= s.FUNNEL["P3"]
    assert s.funnel_table(["F0", "F1", "P3", "P4"])["Grievances"].to_list() == [
        s.FUNNEL[k] for k in ("F0", "F1", "P3", "P4")]


def test_a_step_that_widens_is_refused(con):  # noqa: F811
    with pytest.raises(AssertionError, match="F1"):
        Sample(con, ns="w_", base="TRUE", steps=[
            ("F0", "One", "ticket_no = 'R1'"), ("F1", "All", "TRUE")])


def test_without_a_dedup_file_every_filing_is_primary(con):  # noqa: F811
    s = Sample(con, ns="d_", base="TRUE", steps=STEPS)
    assert not s.deduped
    assert s.count("SELECT COUNT(*) FROM d_g WHERE NOT is_primary") == 0


def test_flags_become_columns(con):  # noqa: F811
    s = Sample(con, ns="f_", base="TRUE", flags={"is_r1": "ticket_no = 'R1'"},
               steps=[("A", "All", "TRUE"), ("B", "R1", "is_r1")])
    assert s.FUNNEL["B"] == 1


def test_two_samples_keep_their_own_journey_tables(con):  # noqa: F811
    """The reason for the namespace: building one must not rewrite the other."""
    a = _sample(con, "a_")
    a_p4 = a.count("SELECT COUNT(*) FROM a_phases_clean")
    b = _sample(con, "b_", base="ticket_no = 'R1'")
    assert b.FUNNEL["P4"] == 1
    assert a.count("SELECT COUNT(*) FROM a_phases_clean") == a_p4 == a.FUNNEL["P4"]
    assert exhibits.stage_means(a)[1] != exhibits.stage_means(b)[1]


@pytest.mark.parametrize("stages", [STAGES_FIVE, STAGES_THREE])
def test_stages_add_to_the_mean_however_folded(con, stages):  # noqa: F811
    s = _sample(con, "a_")
    means, total = exhibits.stage_means(s, stages)
    mean = s.count("SELECT AVG(days_to_close) FROM a_phases_clean")
    assert total == pytest.approx(mean, abs=0.15)
    table = exhibits.stage_table(s, total, stages)
    assert table["Phase"].to_list() == [exhibits.stage_label(k) for k in stages]
    assert sum(table["Mean days"]) == pytest.approx(total, abs=0.15)


def test_three_stages_fold_every_phase_exactly_once():
    folded = [p for cols in STAGES_THREE.values() for p in cols]
    assert sorted(folded) == sorted(PHASES)


def test_route_groups_decompose_to_their_own_means(con):  # noqa: F811
    s = _sample(con, "a_")
    groups = exhibits.top_route_groups(s, top=3, floor=1)
    assert 0 < len(groups) <= 3
    rows = exhibits.stage_rows(s, groups, STAGES_THREE, floor=1)
    assert sum(r["n"] for _, r in rows) <= s.FUNNEL["P4"]
    for _, r in rows:
        assert sum(float(r[k]) for k in STAGES_THREE) == pytest.approx(
            float(r["total"]), abs=0.15)


def test_route_table_is_capped_and_ordered(con):  # noqa: F811
    s = _sample(con, "a_")
    got = exhibits.route_table(s, "F1", limit=2)
    assert got.height <= 2
    assert got["Claims"].to_list() == sorted(got["Claims"].to_list(), reverse=True)


def test_outcomes_account_for_the_whole_step(con):  # noqa: F811
    s = Sample(con, ns="o_", base="TRUE", steps=STEPS)
    assert exhibits.outcomes(s, "F0")["n"].sum() == s.FUNNEL["F0"]


def test_durations_stand_on_p4(con):  # noqa: F811
    s = _sample(con, "a_")
    assert exhibits.durations(s)["n"][0] == s.FUNNEL["P4"]


def test_pie_folds_benefit_into_disposed(con, tmp_path):  # noqa: F811
    import matplotlib
    matplotlib.use("Agg")
    from janasunani.analytics.figures import Report

    s = Sample(con, ns="o_", base="TRUE", steps=STEPS)
    rep = Report(con, [("All", "TRUE")], tmp_path, prefix="t_")
    d = pl.DataFrame({"outcome": ["Disposed", "Disposed with benefit", "Open"],
                      "n": [6, 2, 2]})
    exhibits.draw_outcomes_pie(rep, d, "Title", s.note("F0"), "pie")
    assert (tmp_path / "t_pie.png").exists()
    # The shares the labels quote: benefit is inside disposed, not a slice.
    assert exhibits.outcomes(s, "F0")["n"].sum() == s.FUNNEL["F0"]


def test_route_splits_into_the_offices_of_each_stage(con, tmp_path):  # noqa: F811
    """R1: CM Cell, then Collector > BDO, then back up to the Collector.

    The first hand-off (CM Cell to Collector) ends processing; the first step
    above the deepest office (back to the Collector after the BDO) starts
    review.
    """
    import matplotlib
    matplotlib.use("Agg")
    from janasunani.analytics.figures import Report

    s = _sample(con, "a_", base="ticket_no = 'R1'")
    groups = exhibits.top_route_groups(s, top=3, floor=1)
    got = exhibits.route_stage_roles(s, groups, STAGES_THREE).row(0, named=True)
    assert got["Route"] == "CM Cell > Collector > BDO > Collector"
    assert (got["Processing"], got["Field action"], got["Review"]) == (
        "CM Cell", "Collector > BDO", "Collector")
    assert got["% of route's claims"] == 100.0

    rows = exhibits.stage_rows(s, groups, STAGES_THREE, floor=1)
    rep = Report(con, [("All", "TRUE")], tmp_path, prefix="t_")
    exhibits.draw_stage_stack(rep, rows, "Route", "T", "S", "stack", STAGES_THREE,
                              braces=[{k: got[k] for k in STAGES_THREE}])
    assert (tmp_path / "t_stack.png").exists()


def test_step_stages_tag_each_step_with_its_phase(con):  # noqa: F811
    """R1: CM Cell (assignment), Collector x2 > BDO (field), Collector (review)."""
    _sample(con, "a_", base="ticket_no = 'R1'")
    got = con.execute("SELECT role, phase, gap FROM a_step_stages "
                      "ORDER BY step_index").pl()
    assert got["phase"].to_list() == ["first_assignment", "field_action",
                                      "field_action", "field_action", "review"]
    assert got["role"][0] == "Chief Minister's Grievance Cell"
    assert got["gap"].to_list()[:4] == [2, 2, 4, 11]   # 1, 3, 5, 9, 20 July


def test_field_frame_names_sender_and_field_office(con):  # noqa: F811
    s = _sample(con, "a_")
    f = exhibits.field_frame(s)
    r1 = f.filter(pl.col("ticket_no") == "R1").row(0, named=True)
    assert r1["sender_office"] == "Chief Minister's Grievance Cell"
    assert r1["field_office"] == "Block Development Officer, Sadar"
    # R2 never leaves its first office, so it never reaches field action.
    assert "R2" not in f["ticket_no"].to_list()
    levels = exhibits.field_levels(s, f)
    assert sum(levels["Mean days per claim"]) == pytest.approx(
        f["field_action"].mean(), abs=0.15)


def test_shapley_gives_a_determining_layer_the_whole_share():
    import numpy as np

    from janasunani.analytics.variance import shapley_shares
    rng = np.random.default_rng(1)
    n = 4000
    office = rng.integers(0, 40, n)
    df = pl.DataFrame({"office": office.astype(str),
                       "noise": rng.integers(0, 40, n).astype(str),
                       "y": (office * 3.0) + rng.normal(0, 0.1, n)})
    shares, full = shapley_shares(df, "y", {"Office": "office", "Noise": "noise"})
    got = dict(zip(shares["Layer"], shares["% of what the record explains"]))
    assert full > 0.99
    assert got["Office"] > 99 and abs(got["Noise"]) < 1
