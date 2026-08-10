"""Tests for janasunani.evaluation.stats — the estimator every scorecard CI depends on.

Real-code-path: no mocking, these call the actual maths. The regressions below
correspond to the two defects fixed when ``_clustered_se`` (formerly private,
copy-pasted into ``scripts/benchmark_pipeline.py``) was lifted out of
``sarvam_scorecard.py`` into this module:

  (a) a hardcoded z-lookup that silently returned 1.96 for any alpha outside
      {0.05, 0.01, 0.10}.
  (b) no small-cluster t correction, even though the working regime here is
      30-40 clusters where t(29) = 2.045 vs z = 1.96.
"""

import math
import statistics

import pytest

from janasunani.evaluation.stats import (
    Estimate,
    agresti_coull,
    clustered_se,
    critical_value,
    paired_difference,
    required_pages_mcnemar,
    stratified_mean,
    t_quantile,
    wilson_interval,
)


# ---------------------------------------------------------------------------
# t_quantile — against a published table
# ---------------------------------------------------------------------------


# (p, df, published two-tailed critical value) — standard Student's t table.
_PUBLISHED_T_TABLE = [
    (0.975, 1, 12.706),
    (0.975, 5, 2.571),
    (0.975, 10, 2.228),
    (0.975, 20, 2.086),
    (0.975, 29, 2.045),
    (0.975, 30, 2.042),
    (0.975, 60, 2.000),
    (0.975, 120, 1.980),
    (0.95, 1, 6.314),
    (0.95, 10, 1.812),
    (0.95, 30, 1.697),
    (0.995, 1, 63.657),
    (0.995, 10, 3.169),
    (0.995, 30, 2.750),
]


@pytest.mark.parametrize("p,df,expected", _PUBLISHED_T_TABLE)
def test_t_quantile_matches_published_table(p, df, expected):
    got = t_quantile(p, df)
    assert got == pytest.approx(expected, abs=0.005)


def test_t_quantile_symmetric_and_converges_to_normal_for_large_df():
    # Symmetric: quantile at 1-p is the negative of the quantile at p.
    assert t_quantile(0.9, 10) == pytest.approx(-t_quantile(0.1, 10), abs=1e-9)
    # As df grows, t -> normal; 1.96 is the familiar 95% two-sided z.
    assert t_quantile(0.975, 100_000) == pytest.approx(1.95996, abs=1e-3)


def test_t_quantile_rejects_bad_input():
    with pytest.raises(ValueError):
        t_quantile(0.0, 10)
    with pytest.raises(ValueError):
        t_quantile(1.0, 10)
    with pytest.raises(ValueError):
        t_quantile(0.5, 0)


# ---------------------------------------------------------------------------
# critical_value — regression for defect (a)
# ---------------------------------------------------------------------------


def test_critical_value_honours_non_standard_alpha():
    """The old lookup only knew {0.05, 0.01, 0.10} and fell back to 1.96 for
    anything else — silently. alpha=0.02 (98% CI) must NOT return 1.96.
    """
    z = critical_value(0.02)
    assert z != pytest.approx(1.96, abs=1e-6)
    # Independently verified via statistics.NormalDist.
    assert z == pytest.approx(statistics.NormalDist().inv_cdf(1 - 0.02 / 2), abs=1e-9)


def test_critical_value_matches_normal_for_standard_alphas_without_df():
    assert critical_value(0.05) == pytest.approx(1.959964, abs=1e-5)
    assert critical_value(0.01) == pytest.approx(2.575829, abs=1e-5)
    assert critical_value(0.10) == pytest.approx(1.644854, abs=1e-5)


def test_critical_value_with_df_uses_t_not_normal():
    # A small-df critical value must be strictly wider than the normal one.
    z = critical_value(0.05)
    t29 = critical_value(0.05, df=29)
    assert t29 > z
    assert t29 == pytest.approx(2.045, abs=0.005)


def test_critical_value_rejects_bad_alpha():
    with pytest.raises(ValueError):
        critical_value(0.0)
    with pytest.raises(ValueError):
        critical_value(1.0)


# ---------------------------------------------------------------------------
# clustered_se + paired_difference — regression for defect (b)
# ---------------------------------------------------------------------------


def test_clustered_se_uses_t_when_clusters_are_few():
    """With few clusters, the CI half-width must come from the t quantile at
    df = n_clusters - 1, not the normal quantile — this is what makes the
    30-40 cluster regime ~4% wider than the old code reported.
    """
    # 3 tickets (clusters), several pages each — deliberately few clusters.
    a = [1, 1, 1, 0, 0, 0, 1, 0, 1]
    b = [0, 0, 0, 0, 0, 0, 0, 0, 0]
    clusters = ["T1"] * 3 + ["T2"] * 3 + ["T3"] * 3
    res = paired_difference(a, b, clusters)
    assert res["n_clusters"] == 3
    se = clustered_se([float(x - y) for x, y in zip(a, b)], clusters)
    expected_t_crit = t_quantile(0.975, df=2)
    assert res["z"] == pytest.approx(expected_t_crit, abs=1e-9)
    assert res["z"] > 1.96  # the widening defect (b) fixes
    assert res["ci_high"] - res["ci_low"] == pytest.approx(2 * expected_t_crit * se, abs=1e-9)


def test_clustered_se_converges_to_normal_with_many_clusters():
    # 62 clusters -> df=61 -> t(61) is within a whisker of z.
    a = [1, 0] * 31
    b = [0, 0] * 31
    clusters = [f"T{i}" for i in range(62)]
    res = paired_difference(a, b, clusters)
    assert res["n_clusters"] == 62
    assert res["z"] == pytest.approx(1.96, abs=0.05)


def test_clustered_se_matches_naive_variance_for_single_cluster():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    se_one_cluster = clustered_se(values, ["T1"] * 5)
    naive = math.sqrt(statistics.variance(values) / 5)
    assert se_one_cluster == pytest.approx(naive, abs=1e-9)


def test_clustered_se_short_input_returns_zero():
    assert clustered_se([1.0], ["T1"]) == 0.0
    assert clustered_se([], []) == 0.0


# ---------------------------------------------------------------------------
# Proportion CIs — Wilson and Agresti-Coull
# ---------------------------------------------------------------------------


def test_wilson_bounded_and_nonzero_width_at_zero_successes():
    """The plain normal CI on a 0/89 cell (a shape the PII scorecard
    produces) collapses to zero width and can leave [0, 1]. Wilson must not.
    """
    est = wilson_interval(0, 89)
    assert 0.0 <= est.ci_low <= est.ci_high <= 1.0
    assert est.ci_high > 0.0  # non-zero width even though point estimate is 0
    assert est.point == 0.0


def test_wilson_bounded_at_n_successes():
    est = wilson_interval(89, 89)
    assert 0.0 <= est.ci_low <= est.ci_high <= 1.0
    assert est.ci_low < 1.0  # non-zero width at the top too


def test_wilson_interval_reasonable_for_mid_proportion():
    est = wilson_interval(45, 100)
    assert est.point == pytest.approx(0.45)
    assert est.ci_low < 0.45 < est.ci_high
    assert est.method == "wilson"
    assert est.estimand  # required, non-empty


def test_wilson_rejects_bad_input():
    with pytest.raises(ValueError):
        wilson_interval(-1, 10)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)
    with pytest.raises(ValueError):
        wilson_interval(0, 0)


def test_agresti_coull_bounded_and_nonzero_width_at_zero_successes():
    est = agresti_coull(0, 89)
    assert 0.0 <= est.ci_low <= est.ci_high <= 1.0
    assert est.ci_high > 0.0
    assert est.method == "agresti-coull"


# ---------------------------------------------------------------------------
# stratified_mean — Horvitz-Thompson
# ---------------------------------------------------------------------------


def test_stratified_mean_recovers_known_population_mean():
    """Two strata with known population sizes and known selection
    probabilities; every sampled value equals its stratum's true mean, so
    the Horvitz-Thompson estimate must recover the exact population mean.
    """
    # Stratum A: population 100, sample 2 units (selection prob 2/100),
    # HT weight = 1/prob = 50; every sampled value is the true stratum mean.
    # Stratum B: population 300, sample 3 units, HT weight = 100.
    values = [10.0, 10.0, 20.0, 20.0, 20.0]
    strata = ["A", "A", "B", "B", "B"]
    weights = [50.0, 50.0, 100.0, 100.0, 100.0]

    est = stratified_mean(values, strata, weights, estimand="test population mean")
    true_population_mean = (100 * 10.0 + 300 * 20.0) / 400
    assert est.point == pytest.approx(true_population_mean)
    assert est.se == pytest.approx(0.0, abs=1e-9)  # no within-stratum variance
    assert est.weighted is True
    assert est.n == 5
    assert est.n_clusters == 2
    assert est.estimand == "test population mean"


def test_stratified_mean_requires_estimand():
    with pytest.raises(TypeError):
        stratified_mean([1.0, 2.0], ["A", "A"])  # missing required estimand kwarg


def test_stratified_mean_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        stratified_mean([1.0, 2.0], ["A"], estimand="x")
    with pytest.raises(ValueError):
        stratified_mean([1.0, 2.0], ["A", "A"], weights=[1.0], estimand="x")


def test_design_effect_is_one_for_simple_random_sample():
    """Unweighted, single-stratum stratified_mean must reduce exactly to the
    ordinary SRS variance of the mean — design effect (deff) 1.
    """
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    strata = ["A"] * len(values)
    est = stratified_mean(values, strata, estimand="srs mean")
    srs_variance = statistics.variance(values) / len(values)
    deff = (est.se**2) / srs_variance
    assert deff == pytest.approx(1.0, abs=1e-9)
    assert est.point == pytest.approx(statistics.mean(values))
    assert est.weighted is False


# ---------------------------------------------------------------------------
# Estimate — estimand is mandatory
# ---------------------------------------------------------------------------


def test_estimate_estimand_is_required():
    with pytest.raises(TypeError):
        Estimate(  # type: ignore[call-arg]
            point=0.5,
            se=0.1,
            ci_low=0.3,
            ci_high=0.7,
            n=10,
            n_clusters=5,
            alpha=0.05,
            method="test",
            weighted=False,
        )


def test_estimate_holds_all_fields():
    est = Estimate(
        point=0.5,
        se=0.1,
        ci_low=0.3,
        ci_high=0.7,
        n=10,
        n_clusters=5,
        alpha=0.05,
        method="test",
        weighted=False,
        estimand="share of pages that diverge, Sambalpur/2024 sample",
    )
    assert est.estimand == "share of pages that diverge, Sambalpur/2024 sample"


# ---------------------------------------------------------------------------
# required_pages_mcnemar — moved unchanged; smoke that the move didn't break it
# ---------------------------------------------------------------------------


def test_required_pages_mcnemar_smoke():
    assert 140 <= required_pages_mcnemar(0.10, 0.20) <= 180
    with pytest.raises(ValueError):
        required_pages_mcnemar(0.10, 0.05)  # gap must be < discordance


# --- the weighted variance path, which the unweighted tests could not see ---


def test_stratified_variance_does_not_double_count_the_weight():
    """Codex finding on #237, reproduced exactly before fixing.

    The within-stratum sample variance was computed with the weights inside
    it and still divided by (n_h - 1), while the stratum share below already
    carried the population size. The weight was counted twice, inflating the
    interval by the weight itself.

    This bit ONLY the weighted path, which is why every unweighted test
    passed -- and the weighted path is the one an inverse-probability sample
    uses, so it was wrong exactly where it matters.
    """
    estimate = stratified_mean(
        [0, 2, 10, 12],
        ["A", "A", "B", "B"],
        [50, 50, 100, 100],
        estimand="Codex #237 reproduction",
    )
    # Hand-derived: W_A=1/3, W_B=2/3; s2_h=2 in both; fpc 98/99 and 198/199.
    assert estimate.se == pytest.approx(0.743, abs=0.005)
    # The defect reported 7.05 here, a 9.5x overwide interval.
    assert estimate.se < 1.0


def test_stratified_variance_matches_an_analytic_population():
    """Draw with unequal probabilities from a population whose stratified
    variance is known in closed form, and check the estimator recovers it.

    A test that only checks the point estimate cannot catch a variance bug,
    which is how the double-counted weight survived the first round.
    """
    # Two strata, N_A=100 N_B=400, sampled n_h=5 each -> weights 20 and 80.
    values_a = [10.0, 12.0, 14.0, 16.0, 18.0]  # s2 = 10
    values_b = [20.0, 24.0, 28.0, 32.0, 36.0]  # s2 = 40
    values = values_a + values_b
    strata = ["A"] * 5 + ["B"] * 5
    weights = [20.0] * 5 + [80.0] * 5

    estimate = stratified_mean(values, strata, weights, estimand="analytic check")

    total = 100.0 + 400.0
    expected_var = 0.0
    for s2, n_h, n_pop in ((10.0, 5, 100.0), (40.0, 5, 400.0)):
        share = n_pop / total
        fpc = (n_pop - n_h) / (n_pop - 1)
        expected_var += share**2 * fpc * s2 / n_h
    assert estimate.se == pytest.approx(expected_var**0.5, rel=1e-9)


def test_degrees_of_freedom_come_from_sampled_units_not_strata():
    """Codex finding on #237: two strata gave df=1 and t=12.706 regardless
    of how many units were drawn, so a large sample got an interval more
    than six times too wide."""
    values = [float(i) for i in range(300)]
    strata = ["A"] * 150 + ["B"] * 150
    estimate = stratified_mean(values, strata, estimand="300 units, 2 strata")

    # df = sum(n_h - 1) = 298, so the critical value is essentially normal.
    assert estimate.ci_high - estimate.ci_low == pytest.approx(
        2 * critical_value(0.05, df=298) * estimate.se, rel=1e-9
    )
    # The defect used df = n_strata - 1 = 1 -> t = 12.706.
    naive_width = 2 * critical_value(0.05, df=1) * estimate.se
    assert (estimate.ci_high - estimate.ci_low) < naive_width / 5


def test_a_single_stratum_still_has_usable_degrees_of_freedom():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    estimate = stratified_mean(values, ["A"] * 5, estimand="one stratum")
    assert estimate.se > 0
    assert estimate.ci_low < estimate.point < estimate.ci_high


def test_singleton_stratum_is_rejected_not_silently_zero_variance():
    """Codex finding on #237: a stratum with a single sampled unit has no
    estimable within-stratum variance. The old code ``continue``d past it,
    dropping its ENTIRE variance contribution while its weight stayed in the
    point estimate -- values [0, 10] in strata ['A', 'B'] returned se=0 and
    the zero-width interval [5, 5]. That is a silently downward-biased
    interval (dangerous), not a conservative one, so this must raise instead
    of guessing.
    """
    with pytest.raises(ValueError, match="only 1 sampled unit"):
        stratified_mean([0.0, 10.0], ["A", "B"], estimand="singleton strata")


def test_singleton_stratum_rejected_even_when_mixed_with_a_real_stratum():
    # Stratum A has 5 real observations; stratum B is a lone extreme value.
    # Before the fix this silently dropped B's uncertainty and reported a
    # tighter interval than was justified.
    values = [0.0, 2.0, 4.0, 6.0, 8.0, 100.0]
    strata = ["A", "A", "A", "A", "A", "B"]
    with pytest.raises(ValueError, match="only 1 sampled unit"):
        stratified_mean(values, strata, estimand="mixed singleton")


def test_two_units_per_stratum_is_the_minimum_that_works():
    # n_h=2 in every stratum is exactly the boundary the singleton rejection
    # sits above -- must not raise, and must report a nonzero SE derivable
    # from an actual within-stratum spread.
    values = [0.0, 4.0, 10.0, 14.0]
    strata = ["A", "A", "B", "B"]
    estimate = stratified_mean(values, strata, estimand="boundary n_h=2")
    assert estimate.se > 0


def test_varying_weight_within_a_stratum_is_rejected():
    """Codex finding on #237: the within-stratum sample variance is only a
    valid estimator when every unit in a stratum shares the same inclusion
    probability. The public API accepted arbitrary per-observation weights
    with no check, so a PPS / unequal-probability design silently got back a
    plausible-looking but statistically invalid SE. Demonstrate the defect
    directly: holding stratum B's weights constant and stratum A's total
    weight fixed, swapping an even weight split for a wildly skewed one
    changes nothing about how the "variance" is computed (it is derived only
    from raw values and the weighted mean, never from the weights'
    dispersion) even though the two designs have very different reliability.
    Enforcing the assumption is safer than reporting that invalid number.
    """
    values = [0.0, 10.0, 20.0, 22.0]
    strata = ["A", "A", "B", "B"]
    with pytest.raises(ValueError, match="varying weights"):
        stratified_mean(values, strata, [1.0, 99.0, 50.0, 50.0], estimand="varying weight")


def test_constant_weight_within_stratum_is_accepted():
    # The common, valid design -- weights differ ACROSS strata but are
    # constant WITHIN each -- must not raise.
    values = [0.0, 2.0, 10.0, 12.0]
    strata = ["A", "A", "B", "B"]
    estimate = stratified_mean(values, strata, [50.0, 50.0, 100.0, 100.0], estimand="valid design")
    assert estimate.se > 0


def test_stratified_mean_rejects_nonpositive_weights():
    # A Horvitz-Thompson weight is an inverse inclusion probability; it
    # cannot be zero or negative. Rejecting this upfront is what makes the
    # per-stratum weight sum provably positive, removing the need for a
    # separate silent-skip branch for a degenerate weight sum.
    with pytest.raises(ValueError, match="positive"):
        stratified_mean([1.0, 2.0], ["A", "A"], [0.0, 5.0], estimand="zero weight")
    with pytest.raises(ValueError, match="positive"):
        stratified_mean([1.0, 2.0], ["A", "A"], [-1.0, 5.0], estimand="negative weight")
