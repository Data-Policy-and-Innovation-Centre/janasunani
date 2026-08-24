"""Duan's smearing estimator: getting from the log scale back to days.

Definition 5.3 of `docs/experiments/routing-outcome-model.tex`.

The duration model is fitted on `log(1 + T)` but the estimand is in days. The
obvious retransformation is wrong, and wrong in a fixed direction. By Jensen,

    E[1 + T | x] = exp(mu(x)) * E[exp(eps) | x] >= exp(mu(x))

so `expm1(mu_hat(x))` estimates something closer to a conditional *median* than
a conditional mean. In this corpus the gap is roughly 25-30 days: the fitted
historical direct-method value came out at 67.3 days against a realised mean of
93.1 on the same rows.

That gap is not merely a level shift that cancels in a contrast. It is why
`Delta_DM` and `Delta_DR` disagreed by 11 days for the ridge in the superseded
run: an uncorrected direct term forces the augmentation to absorb the whole
retransformation error, which inflates its variance for no reason and makes the
two estimators look like they disagree about the treatment effect when they are
actually disagreeing about the scale.

The correction needs no distributional assumption beyond homoskedasticity on
the log scale. That assumption is not credible across a corpus spanning
two-day screen-outs and year-long land disputes, so the factor is computed
within strata by default and pooled only as a fallback for thin cells.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: A stratum needs at least this many training residuals to get its own factor.
#: Below it, the pooled factor is used: a smearing factor estimated off a
#: handful of residuals is noisier than the bias it removes.
MIN_STRATUM = 50


@dataclass(frozen=True)
class SmearingFactor:
    """Duan factors fitted on training residuals, applied at prediction time."""

    pooled: float
    by_stratum: dict[str, float] = field(default_factory=dict)
    min_stratum: int = MIN_STRATUM

    @classmethod
    def fit(
        cls,
        log_actual: np.ndarray,
        log_predicted: np.ndarray,
        *,
        strata: pd.Series | None = None,
        min_stratum: int = MIN_STRATUM,
    ) -> "SmearingFactor":
        residual = np.asarray(log_actual, dtype=float) - np.asarray(log_predicted, dtype=float)
        pooled = float(np.mean(np.exp(residual)))

        by_stratum: dict[str, float] = {}
        if strata is not None:
            keys = strata.astype(str).to_numpy()
            for value in np.unique(keys):
                rows = keys == value
                if rows.sum() >= min_stratum:
                    by_stratum[value] = float(np.mean(np.exp(residual[rows])))
        return cls(pooled=pooled, by_stratum=by_stratum, min_stratum=min_stratum)

    def apply(
        self, log_predicted: np.ndarray, *, strata: pd.Series | None = None
    ) -> np.ndarray:
        """`s * exp(mu_hat) - 1`, the estimate of `E[T | x]` in days."""
        log_predicted = np.asarray(log_predicted, dtype=float)
        if strata is None or not self.by_stratum:
            factor = np.full(log_predicted.shape, self.pooled)
        else:
            keys = strata.astype(str).to_numpy()
            factor = np.array([self.by_stratum.get(k, self.pooled) for k in keys])
        return factor * np.exp(log_predicted) - 1.0

    def summary(self) -> dict:
        values = list(self.by_stratum.values())
        return {
            "pooled": self.pooled,
            "n_strata_fitted": len(self.by_stratum),
            "stratum_min": min(values) if values else None,
            "stratum_max": max(values) if values else None,
            # A pooled factor near 1 means the correction is doing nothing and
            # should be interrogated rather than trusted.
            "implied_pct_uplift": self.pooled - 1.0,
        }


def naive_days(log_predicted: np.ndarray) -> np.ndarray:
    """`expm1(mu_hat)`, the uncorrected retransformation. Kept for the contrast."""
    return np.expm1(np.asarray(log_predicted, dtype=float))
