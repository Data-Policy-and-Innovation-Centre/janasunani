"""Restricted mean survival time and inverse-probability-of-censoring weights.

Definition 2.7 and Theorem C.2 of `docs/experiments/routing-outcome-model.tex`.

WHY NOT JUST DROP THE CENSORED ROWS
------------------------------------
Because they are not missing at random with respect to the outcome. A grievance
is censored exactly when it has *not closed yet*, so a completers-only analysis
admits a case only if it was fast enough to finish before the extract. The
selection is on the outcome being estimated, and it gets worse the more recent
the cohort:

    2021  2.1%      2023  3.7%      2025  34.4%
    2022  2.3%      2024  9.2%

On the 2025 cohort the realised mean duration among completers is 40.6 days
against 93.1 on 2024. That gap is not the system getting faster.

WHY THE STRUCTURE HERE IS FAVOURABLE
-------------------------------------
Censoring is *administrative*: the censoring time `D_i` is a deterministic
function of the arrival date and the extract date, both recorded. So the
independent-censoring assumption is not a hope about unobserved dropout, it is
a statement about a calendar. Stratifying the Kaplan-Meier fit on the arrival
cohort makes it near-exact rather than approximate.

THE HORIZON DOES REAL WORK
---------------------------
Restricting at `L = 365` days means the estimand is identified from data on
`[0, L]` with no parametric tail assumption, is measured in days so "the policy
saves d days per grievance" is literally true, and equals the area under the
survival curve to `L`. A case still open at the horizon contributes exactly `L`
and needs no extrapolation: `Y = min(T, L)` is fully observed whenever `D >= L`,
whether or not the case has closed.

Fully observed is not the same as unweighted, and conflating the two is an easy
and costly mistake. A case reaching the horizon has `Y = L` known exactly, but
it only appears in the sample at all when `D >= L`, which happens with
probability `G(L-)`. In this corpus `G(L-)` is small -- most arrivals are not
old enough to have been observable for a full year -- so those rows each stand
for roughly ten others and must carry weight `1 / G(L-)`. Giving them weight 1
because their outcome is certain drops the entire slow tail and reproduces most
of the completers-only bias the module exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Restriction horizon in days.
HORIZON = 365.0

#: Weights above this are treated as unidentified. A case whose censoring
#: survival probability is near zero carries almost no information and enormous
#: variance; bounding `G` is an explicit, reportable restriction of the target
#: population, unlike truncating the score (§5.3).
MIN_G = 0.05


@dataclass(frozen=True)
class RestrictedOutcome:
    """`Y = min(T, L)` with IPCW weights, plus what had to be assumed."""

    y: pd.Series
    weight: pd.Series
    uncensored: pd.Series
    horizon: float
    n_clipped: int
    stratum_count: int

    def summary(self) -> dict:
        contributing = self.weight > 0
        return {
            "horizon": self.horizon,
            "n": int(len(self.y)),
            "n_contributing": int(contributing.sum()),
            "n_weight_clipped": self.n_clipped,
            "strata": self.stratum_count,
            "rmst": float(np.average(self.y[contributing], weights=self.weight[contributing]))
            if contributing.any()
            else float("nan"),
            "naive_completer_mean": float(self.y[self.uncensored].mean())
            if self.uncensored.any()
            else float("nan"),
            "mean_weight": float(self.weight[contributing].mean())
            if contributing.any()
            else float("nan"),
        }


def kaplan_meier_censoring(
    observed_days: np.ndarray, event: np.ndarray, *, horizon: float = HORIZON
) -> tuple[np.ndarray, np.ndarray]:
    """Kaplan-Meier estimate of the *censoring* survival curve `G(t) = P(D > t)`.

    Note the flipped indicator. The survival function being estimated is the one
    for the censoring process, so a censoring event (`event == 0`) is what counts
    as a "death" here. Getting this backwards estimates the outcome curve and
    silently inverts every weight.

    Returns the sorted distinct times and `G` evaluated just after each, so a
    caller can look up `G(t-)` by searching for the last time strictly below `t`.
    """
    times = np.asarray(observed_days, dtype=float)
    censored = np.asarray(event, dtype=int) == 0

    grid = np.unique(times[times <= horizon])
    if grid.size == 0:
        return np.array([0.0]), np.array([1.0])

    # Vectorised rather than a loop over the grid. Durations are effectively
    # continuous, so the grid is nearly as long as the data and the obvious
    # `for t in grid: (times >= t).sum()` is quadratic -- fine on a test frame,
    # hours on 1.3 million rows.
    at_risk = len(times) - np.searchsorted(np.sort(times), grid, side="left")
    failures = np.bincount(
        np.searchsorted(grid, times[censored], side="left"),
        minlength=grid.size,
    )[: grid.size]

    with np.errstate(divide="ignore", invalid="ignore"):
        hazard = np.where(at_risk > 0, failures / np.maximum(at_risk, 1), 0.0)
    survival = np.cumprod(1.0 - hazard)
    return grid, survival


def _lookup_g(grid: np.ndarray, survival: np.ndarray, query: np.ndarray) -> np.ndarray:
    """`G(t-)`: the curve strictly before each query time."""
    position = np.searchsorted(grid, query, side="left") - 1
    out = np.ones(query.shape, dtype=float)
    valid = position >= 0
    out[valid] = survival[position[valid]]
    return out


def restricted_outcome(
    df: pd.DataFrame,
    *,
    fit_frame: pd.DataFrame | None = None,
    observed_col: str = "observed_days",
    event_col: str = "event",
    stratum_col: str | None = "cluster",
    horizon: float = HORIZON,
    min_g: float = MIN_G,
) -> RestrictedOutcome:
    """`Y = min(T, L)` and IPCW weights, fitting `G` within each stratum.

    A case observed past the horizon has `Y = L` known exactly, but is weighted
    by `1 / G(L-)` like any other: certainty about its outcome says nothing
    about the probability it was observable at all. Only cases censored
    *before* the horizon are uninformative, and they receive weight 0 while the
    rest are upweighted to stand for them.

    `fit_frame` estimates `G` somewhere other than `df`, and passing it is
    mandatory whenever `df` has been filtered on anything downstream of closure.
    The motivating case is the actionable population: `S` is read off the
    closing remark, so every `S == 1` row is resolved by construction, and a `G`
    fitted there sees no censoring events at all, returns the constant 1, and
    hands back weights that are all exactly 1. The correction silently becomes a
    no-op and every downstream number looks unremarkable. Fit `G` on the whole
    arrival cohort instead, where the censoring actually is.
    """
    source = fit_frame if fit_frame is not None else df
    if fit_frame is not None and stratum_col and stratum_col not in source.columns:
        raise ValueError(f"fit_frame lacks the stratum column {stratum_col!r}")

    observed = df[observed_col].to_numpy(dtype=float)
    event = df[event_col].to_numpy(dtype=int)
    fit_observed = source[observed_col].to_numpy(dtype=float)
    fit_event = source[event_col].to_numpy(dtype=int)

    y = np.minimum(observed, horizon)
    # Fully observed: closed before the horizon, or still around at the horizon.
    known = (event == 1) | (observed >= horizon)

    weight = np.zeros(len(df), dtype=float)
    strata = (
        df[stratum_col].astype(str).to_numpy()
        if stratum_col and stratum_col in df.columns
        else np.zeros(len(df), dtype=str)
    )

    fit_strata = (
        source[stratum_col].astype(str).to_numpy()
        if stratum_col and stratum_col in source.columns
        else np.zeros(len(source), dtype=str)
    )

    clipped = 0
    for value in np.unique(strata):
        rows = np.flatnonzero(strata == value)
        fit_rows = np.flatnonzero(fit_strata == value)
        if fit_rows.size == 0:
            # No cohort to estimate G from. Weight 1 is the only defensible
            # answer and it is stated, not silently assumed.
            weight[rows] = np.where(known[rows], 1.0, 0.0)
            continue
        grid, survival = kaplan_meier_censoring(
            fit_observed[fit_rows], fit_event[fit_rows], horizon=horizon
        )
        # `y` is already `min(observed, horizon)`, so a row past the horizon
        # queries `G(L-)` here by construction. That is the right weight for it
        # -- see the module docstring on why it is not 1.
        g = _lookup_g(grid, survival, y[rows])
        clipped += int((g < min_g).sum())
        g = np.clip(g, min_g, 1.0)
        weight[rows] = np.where(known[rows], 1.0 / g, 0.0)

    return RestrictedOutcome(
        y=pd.Series(y, index=df.index),
        weight=pd.Series(weight, index=df.index),
        uncensored=pd.Series(event == 1, index=df.index),
        horizon=horizon,
        n_clipped=clipped,
        stratum_count=int(len(np.unique(strata))),
    )
