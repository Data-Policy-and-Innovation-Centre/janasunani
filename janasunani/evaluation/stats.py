"""Statistics primitives shared across evaluation scorecards.

Lifted out of ``sarvam_scorecard.py`` (see #127, #84) where the only
estimator in this repo — a cluster-robust sandwich SE for a mean — lived
private and had already been copy-pasted verbatim into
``scripts/benchmark_pipeline.py``. Every confidence interval this repo
publishes traces back to that one function, so it is public, tested here in
isolation, and fixed on the way out:

* **Defect (a) — wrong critical value for non-standard alpha.** The old code
  (``sarvam_scorecard.paired_difference``, formerly lines 238-248) was a
  lookup table for ``{0.05, 0.01, 0.10}`` that silently fell back to
  ``z=1.96`` for *any other* alpha — a 90%-CI-shaped bug that never raised.
  ``required_pages_mcnemar`` in the same module already computed critical
  values correctly via ``NormalDist().inv_cdf``; :func:`critical_value` below
  is that fix, generalised and made the one path every caller uses.
* **Defect (b) — no small-cluster correction.** A clustered SE is a sandwich
  estimator with ``C - 1`` effective degrees of freedom, not infinite. This
  repo's benchmark designs run 30-40 clusters (tickets/documents), where
  ``t(29) = 2.045`` vs ``z = 1.96`` — about 4% wider. Every clustered SE in
  this module now defaults to the ``t`` quantile at ``df = n_clusters - 1``
  instead of the normal quantile. This widens every published CI that used
  the old code; that widening is correct, not a regression.

Pure stdlib — no scipy, no numpy (numpy sits in a conflicting extra; see
``[tool.uv].conflicts``). :func:`t_quantile` inverts the Student's t CDF via
the regularized incomplete beta function (continued-fraction evaluation,
Numerical Recipes ``betacf``) and bisection. This is mathematically the same
quantile Hill's (1970) Algorithm 396 approximates directly; bisection on the
incomplete-beta relation was chosen over reconstructing Hill's rational
approximation from memory because it is easy to verify against a published
table by hand and cannot silently drift from the true quantile the way a
mistyped polynomial coefficient would.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

# ---------------------------------------------------------------------------
# Estimate — the container every estimator below returns
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    """A point estimate with its interval and the population it describes.

    Every field is required — in particular ``estimand``, which is not a
    label of convenience. A confidence interval with no stated estimand is
    how a 300-page stratified sample's category-accuracy difference gets
    read, three slides later, as a claim about all 1.37M complaints in the
    lake. Making the field mandatory pushes that question onto the type
    system instead of relying on whoever writes the report remembering to
    say it: every ``Estimate`` must say, in one line, what population this
    number is about (e.g. "category accuracy difference on the 312-page
    Sambalpur/2024 stratified sample", not "category accuracy").
    """

    point: float
    se: float
    ci_low: float
    ci_high: float
    n: int
    n_clusters: int
    alpha: float
    method: str
    weighted: bool
    estimand: str


# ---------------------------------------------------------------------------
# Cluster-robust SE for a mean
# ---------------------------------------------------------------------------


def clustered_se(values: Sequence[float], clusters: Sequence[str]) -> float:
    """Cluster-robust SE for the mean of ``values`` clustered by ``clusters``.

    Sandwich estimator for a mean:

        Var = sum_c (sum_{i in c} (x_i - mean))^2 / n^2 * C/(C-1)

    where ``n = len(values)``, ``C`` = number of distinct clusters. When
    ``C <= 1`` (no cluster structure, or everything in one cluster), falls
    back to the ordinary variance of the mean. Returns 0.0 for ``n < 2``.

    Maths unchanged from the original private
    ``sarvam_scorecard._clustered_se`` (#127) — this is that function, made
    public. Pairs with :func:`critical_value` (``df=n_clusters - 1``) to turn
    this SE into a confidence interval; this function does not pick the
    critical value itself.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    cluster_sums: dict[str, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        cluster_sums[c] += v - mean
    num_clusters = len(cluster_sums)
    summed_sq = sum(s * s for s in cluster_sums.values())
    if num_clusters <= 1:
        # simple SE: sqrt( sum (x - mean)^2 / (n*(n-1)) )
        s2 = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
        return math.sqrt(s2 / n) if n else 0.0
    correction = num_clusters / (num_clusters - 1)
    var = summed_sq / (n * n) * correction
    if var < 0:  # numerical guard
        var = 0.0
    return math.sqrt(var)


# ---------------------------------------------------------------------------
# Student's t quantile — pure stdlib, no scipy/numpy
# ---------------------------------------------------------------------------


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(x: float, a: float, b: float) -> float:
    """Continued-fraction evaluation of the incomplete beta function.

    Standard Numerical Recipes ``betacf`` (Lentz's algorithm). Used only by
    :func:`_betainc` below; not part of the public API.
    """
    max_iter = 200
    eps = 3e-14
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betainc(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a, b) for 0 <= x <= 1."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(-_log_beta(a, b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(x, a, b) / a
    return 1.0 - bt * _betacf(1.0 - x, b, a) / b


def _t_cdf(t: float, df: float) -> float:
    """CDF of Student's t with ``df`` degrees of freedom, via I_x(df/2, 1/2)."""
    x = df / (df + t * t)
    prob = _betainc(x, df / 2.0, 0.5)
    return 1.0 - 0.5 * prob if t >= 0 else 0.5 * prob


def t_quantile(p: float, df: float) -> float:
    """Inverse CDF (quantile) of Student's t with ``df`` degrees of freedom.

    Returns ``x`` such that ``P(T <= x) = p``. ``df`` need not be an integer
    (a clustered SE's effective ``df`` is ``n_clusters - 1``, always an
    integer in this repo, but the algorithm does not require it). Monotone
    bisection on :func:`_t_cdf`, which is itself exact via the incomplete
    beta relation — see the module docstring for why this replaces a direct
    port of Hill's (1970) rational approximation.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if df <= 0:
        raise ValueError("df must be positive")
    if p == 0.5:
        return 0.0
    lo, hi = -1.0, 1.0
    while _t_cdf(lo, df) > p:
        lo *= 2.0
    while _t_cdf(hi, df) < p:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if hi - lo < 1e-12:
            break
        if _t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def critical_value(alpha: float, *, df: float | None = None) -> float:
    """Two-sided critical value for a ``1 - alpha`` confidence interval.

    ``df=None`` uses the normal quantile (``NormalDist().inv_cdf``) — the
    asymptotic case, or a caller with no cluster structure. ``df`` given uses
    the Student's t quantile at that many degrees of freedom, which is wider
    for small ``df`` and converges to the normal quantile as ``df`` grows.

    This is **defect (a)**'s fix: the code this replaced
    (``sarvam_scorecard.paired_difference``, formerly lines 238-248) was a
    lookup table for ``alpha in {0.05, 0.01, 0.10}`` that silently returned
    ``z=1.96`` for any other alpha — an unannounced 90%-CI substitution.
    ``required_pages_mcnemar`` in the same module already did this correctly;
    every caller of a clustered SE in this module now goes through here.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    p = 1.0 - alpha / 2.0
    if df is None:
        return NormalDist().inv_cdf(p)
    if df <= 0:
        raise ValueError("df must be positive")
    return t_quantile(p, df)


def _df_for_clusters(n_clusters: int) -> float | None:
    """``n_clusters - 1`` when there is cluster structure to correct for.

    Shared by every clustered-SE call site so "default df = C - 1 wherever a
    clustered SE is used" is one decision, not one per caller. Falls back to
    ``None`` (normal quantile) when there are 0 or 1 clusters, where a t
    quantile is undefined.
    """
    return n_clusters - 1 if n_clusters > 1 else None


# ---------------------------------------------------------------------------
# Proportion confidence intervals
# ---------------------------------------------------------------------------


def wilson_interval(successes: int, n: int, alpha: float = 0.05) -> Estimate:
    """Wilson score interval for a binomial proportion.

    No proportion CI existed anywhere in this repo before this module; the
    scorecards computed proportions (divergence rates, PII detection rates)
    and reported a plain normal interval, which can leave ``[0, 1]`` and
    collapses to a zero-width interval at ``successes=0`` or
    ``successes=n`` — exactly the shape a 0/89 PII-detection cell produces.
    Wilson's interval is bounded in ``[0, 1]`` by construction and does not
    collapse at the extremes.

    ``se`` on the returned :class:`Estimate` is the naive Wald SE
    (``sqrt(p_hat * (1 - p_hat) / n)``), reported for reference only — it is
    *not* what determines ``ci_low``/``ci_high``, which come from inverting
    the score test directly. ``estimand`` describes the proportion measured
    (e.g. "PII detection rate on the redaction gold set"), not restated here
    since it is caller-supplied.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes must be in [0, n]")
    z = critical_value(alpha)
    p_hat = successes / n
    denom = 1.0 + z * z / n
    center = p_hat + z * z / (2 * n)
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    ci_low = max(0.0, (center - margin) / denom)
    ci_high = min(1.0, (center + margin) / denom)
    wald_se = math.sqrt(p_hat * (1 - p_hat) / n)
    return Estimate(
        point=p_hat,
        se=wald_se,
        ci_low=ci_low,
        ci_high=ci_high,
        n=n,
        n_clusters=n,
        alpha=alpha,
        method="wilson",
        weighted=False,
        estimand=f"proportion ({successes}/{n})",
    )


def agresti_coull(successes: int, n: int, alpha: float = 0.05) -> Estimate:
    """Agresti-Coull interval for a binomial proportion.

    Simpler-to-explain cousin of :func:`wilson_interval`: add ``z^2 / 2``
    "successes" and ``z^2`` "trials" before computing a plain Wald interval
    on the adjusted proportion. Bounded in ``[0, 1]`` for the same reason
    Wilson is; offered alongside Wilson because different consumers of a
    scorecard expect different textbook conventions for a small-sample
    proportion CI.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes must be in [0, n]")
    z = critical_value(alpha)
    n_tilde = n + z * z
    p_tilde = (successes + z * z / 2.0) / n_tilde
    margin = z * math.sqrt(p_tilde * (1 - p_tilde) / n_tilde)
    ci_low = max(0.0, p_tilde - margin)
    ci_high = min(1.0, p_tilde + margin)
    return Estimate(
        point=p_tilde,
        se=math.sqrt(p_tilde * (1 - p_tilde) / n_tilde),
        ci_low=ci_low,
        ci_high=ci_high,
        n=n,
        n_clusters=n,
        alpha=alpha,
        method="agresti-coull",
        weighted=False,
        estimand=f"proportion ({successes}/{n}, Agresti-Coull adjusted)",
    )


# ---------------------------------------------------------------------------
# Stratified (Horvitz-Thompson) mean
# ---------------------------------------------------------------------------


def stratified_mean(
    values: Sequence[float],
    strata: Sequence[str],
    weights: Sequence[float] | None = None,
    *,
    alpha: float = 0.05,
    estimand: str,
) -> Estimate:
    """Horvitz-Thompson stratified mean with per-stratum variance and FPC.

    ``weights`` are Horvitz-Thompson (inverse inclusion-probability) weights,
    one per observation; omit for an unweighted stratified mean, in which
    case every unit gets weight 1 and, with a single stratum, this reduces
    exactly to the ordinary sample mean and its SRS variance (design effect
    1 — see the test of the same name).

    Within each stratum the estimated population size is
    ``N_h_hat = sum(weights in stratum)`` and the sample size is
    ``n_h = count in stratum``. The finite-population correction
    ``(N_h_hat - n_h) / (N_h_hat - 1)`` is applied to that stratum's
    contribution to the variance whenever ``N_h_hat > n_h`` (real
    finite-population information was supplied via the weights); otherwise
    the correction is 1 (no FPC), which is what an unweighted call gets.

    The critical value uses ``df = n_strata - 1`` via :func:`critical_value`
    — the same small-sample t-correction as every other estimator here.

    Every stratum must supply at least 2 sampled units and a constant weight
    across its members, or this raises ``ValueError`` — see the comments in
    the loop below for why both are hard requirements, not warnings.

    ``estimand`` is required, keyword-only, no default — see
    :class:`Estimate`.
    """
    n = len(values)
    if len(strata) != n:
        raise ValueError("values and strata must have equal length")
    if weights is not None and len(weights) != n:
        raise ValueError("weights must have the same length as values")
    if n == 0:
        raise ValueError("stratified_mean needs at least one observation")
    w = list(weights) if weights is not None else [1.0] * n
    if weights is not None:
        # A Horvitz-Thompson weight is an inverse inclusion probability; it
        # cannot be zero or negative. Rejecting this here means w_h (the
        # per-stratum weight sum) can never be <= 0 below, so that no longer
        # needs its own silent-skip branch.
        for wt in w:
            if wt <= 0:
                raise ValueError("weights must be positive")

    by_stratum: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for v, s, wt in zip(values, strata, w):
        by_stratum[s].append((v, wt))

    total_weight = sum(w)
    if total_weight <= 0:
        raise ValueError("total weight must be positive")
    point = sum(v * wt for v, wt in zip(values, w)) / total_weight

    var = 0.0
    df_units = 0
    for stratum, members in by_stratum.items():
        n_h = len(members)
        # A singleton stratum's within-stratum variance is not estimable --
        # there is no deviation to measure it from. The previous code
        # `continue`d past it, which drops its ENTIRE variance contribution
        # while its weight stays in the point estimate above: values [0, 10]
        # in strata ['A', 'B'] returned se=0 and the zero-width interval
        # [5, 5], a silently downward-biased interval, not a conservative
        # one. That is the dangerous direction, so this raises instead of
        # guessing.
        #
        # A real survey design sometimes has a deliberate certainty
        # stratum (a self-representing unit sampled with probability 1,
        # contributing zero variance by design) -- but that is a design
        # decision the caller must make explicitly, not something this
        # function can infer from n_h == 1 alone. If that is the intent,
        # exclude the stratum's variance contribution explicitly at the call
        # site with a comment saying so; otherwise collapse the singleton
        # into a neighboring stratum, or draw n_h >= 2, before calling this.
        if n_h < 2:
            raise ValueError(
                f"stratum {stratum!r} has only {n_h} sampled unit(s); its "
                "variance is not estimable. Collapse it into a neighboring "
                "stratum, or draw n_h >= 2, before calling stratified_mean. "
                "(If it is a deliberate certainty stratum, exclude its "
                "variance contribution explicitly at the call site instead "
                "of relying on this function to guess that.)"
            )
        stratum_weights = [wt for _, wt in members]
        # The within-stratum sample variance below is UNWEIGHTED, which is
        # only a valid estimator when every unit in the stratum shares the
        # same inclusion probability (the constant-weight assumption
        # stratified sampling relies on). The public API accepts arbitrary
        # per-observation weights, so nothing stopped a caller from passing
        # a PPS / unequal-probability design here and getting a plausible
        # but invalid SE back with no warning. Enforce the assumption the
        # estimator actually relies on instead of leaving it as a comment
        # nobody reading only the call site would see.
        first_wt = stratum_weights[0]
        if any(not math.isclose(wt, first_wt, rel_tol=1e-9, abs_tol=1e-12) for wt in stratum_weights[1:]):
            raise ValueError(
                f"stratum {stratum!r} has varying weights within it "
                f"({sorted(set(stratum_weights))}); stratified_mean's "
                "variance estimator assumes a constant weight (constant "
                "inclusion probability) within each stratum. Split the "
                "stratum so weights are constant within each part, or use a "
                "different estimator for unequal-probability (PPS) sampling "
                "-- this function does not implement one."
            )
        w_h = sum(stratum_weights)
        mean_h = sum(v * wt for v, wt in members) / w_h
        # Weighting the squared deviations and still dividing by (n_h - 1)
        # inflates s2_h by the weight itself, and the stratum share below
        # already carries the population size, so the weight was being counted
        # twice. On values [0,2,10,12] with weights [50,50,100,100] that
        # reported SE 7.05 against a true 0.743, a 9.5x overwide interval.
        #
        # It bit only the weighted path, which is why the unweighted tests
        # never saw it -- and the weighted path is the one an inverse-
        # probability sample uses, so it was wrong exactly where it matters.
        s2_h = sum((v - mean_h) ** 2 for v, _wt in members) / (n_h - 1)
        fpc = 1.0
        if w_h > n_h:
            fpc = (w_h - n_h) / (w_h - 1)
        stratum_weight_share = w_h / total_weight
        var += (stratum_weight_share**2) * fpc * s2_h / n_h
        df_units += n_h - 1

    se = math.sqrt(var) if var > 0 else 0.0
    n_strata = len(by_stratum)
    # Degrees of freedom come from the SAMPLED UNITS, sum_h (n_h - 1), not
    # from the stratum count.
    #
    # Strata are not the independent clusters the clustered-SE correction
    # counts: the variance estimate rests on the within-stratum variances, so
    # it is those that supply the degrees of freedom. Using n_strata - 1 gave
    # a two-stratum design df=1 and a critical value of 12.706 no matter how
    # many units were drawn, so a 300-unit sample got an interval more than
    # six times too wide. Two- and three-stratum designs are the common case,
    # which is where it did the most damage.
    df = df_units if df_units >= 1 else 1
    z = critical_value(alpha, df=df)
    return Estimate(
        point=point,
        se=se,
        ci_low=point - z * se,
        ci_high=point + z * se,
        n=n,
        n_clusters=n_strata,
        alpha=alpha,
        method="stratified-mean-horvitz-thompson",
        weighted=weights is not None,
        estimand=estimand,
    )


# ---------------------------------------------------------------------------
# Paired difference with ticket-clustered CI
# ---------------------------------------------------------------------------


def paired_difference(
    a_correct: Sequence[int],
    b_correct: Sequence[int],
    clusters: Sequence[str],
    alpha: float = 0.05,
) -> dict[str, float]:
    """Paired difference in accuracy with ticket-clustered CI.

    Args:
      a_correct: 1/0 per page for system A (e.g. Sarvam)
      b_correct: 1/0 per page for system B (e.g. pytesseract/pipeline)
      clusters: ticket id per page
      alpha: two-sided level (0.05 -> 95% CI)

    Returns dict with diff, se, z, ci_low, ci_high, n, n_clusters.
    The difference is mean(a) - mean(b). Positive favours A.

    Moved from ``sarvam_scorecard.paired_difference`` (formerly lines
    211-252) with both defects fixed in place: the critical value now comes
    from :func:`critical_value` for arbitrary alpha (defect a), at
    ``df = n_clusters - 1`` (defect b) instead of a hardcoded ``z=1.96``.
    Both fixes widen every previously published CI from this function; see
    the module docstring.
    """
    if not (len(a_correct) == len(b_correct) == len(clusters)):
        raise ValueError("a_correct, b_correct, clusters must have equal length")
    n = len(a_correct)
    if n == 0:
        return {
            "diff": 0.0,
            "se": 0.0,
            "z": critical_value(alpha),
            "ci_low": 0.0,
            "ci_high": 0.0,
            "n": 0,
            "n_clusters": 0,
        }
    diffs = [float(a - b) for a, b in zip(a_correct, b_correct)]
    diff = sum(diffs) / n
    se = clustered_se(diffs, clusters)
    n_clusters = len(set(clusters))
    crit = critical_value(alpha, df=_df_for_clusters(n_clusters))
    ci_low = diff - crit * se
    ci_high = diff + crit * se
    return {
        "diff": diff,
        "se": se,
        "z": crit,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n": n,
        "n_clusters": n_clusters,
    }


# ---------------------------------------------------------------------------
# Power (McNemar, paired) — already correct; moved unchanged
# ---------------------------------------------------------------------------


def required_pages_mcnemar(gap: float, discordance: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Approximate required pages for a paired (McNemar) comparison.

    ``gap`` is the accuracy difference to detect (e.g. 0.10 for 10 points),
    ``discordance`` is the expected share where the two systems disagree.
    Uses the normal approximation for McNemar's test. Returns ceiling.

    The table in #127 was computed with the same approximation; values are
    indicative — the recommmended 200-250 stratified sample is powered for
    ~10 points at 20-30% discordance.

    Moved unchanged from ``sarvam_scorecard.required_pages_mcnemar`` (formerly
    lines 314-342) — this one already used real quantiles
    (``NormalDist().inv_cdf``) rather than the hardcoded-lookup pattern that
    was defect (a) elsewhere in the module.
    """
    if not (0 < gap < 1 and 0 < gap < discordance <= 1):
        raise ValueError("need 0 < gap < discordance <= 1")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    if not 0 < power < 1:
        raise ValueError("power must be in (0, 1)")
    # Actual quantiles, not the 0.05/0.80 constants. Both ternaries here
    # returned the same value on either branch, so asking for 90% power or a
    # 1% level silently returned the 80%/5% sample size — a deliberately
    # under-powered study reporting itself as powered, which is the failure
    # #127 exists to prevent, and every page submitted costs money.
    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_beta = NormalDist().inv_cdf(power)
    # McNemar: n = (z_alpha*sqrt(p_d) + z_beta*sqrt(p_d - gap^2))^2 / gap^2
    p_d = discordance
    numerator = (z_alpha * math.sqrt(p_d) + z_beta * math.sqrt(max(p_d - gap * gap, 1e-9))) ** 2
    n = numerator / (gap * gap)
    return int(math.ceil(n))


__all__ = [
    "Estimate",
    "agresti_coull",
    "clustered_se",
    "critical_value",
    "paired_difference",
    "required_pages_mcnemar",
    "stratified_mean",
    "t_quantile",
    "wilson_interval",
]
