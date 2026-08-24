"""Eligible flow sets and the greedy policy delta(x) = argmin mu_a(x).

What the Aug 11 run actually did, despite the name: for each
`category x district` cell it picked the eligible template with the smallest
*training cell mean* days, identically for every case in the cell. That is not
a policy of `x` -- it ignores the covariates entirely -- and it is not the
`argmin_a mu_a(x)` the write-up claimed. The reason is visible in the original
`ope.py`, where `best_mu_for_row` is an abandoned stub whose comment reads
"this is messy because codes are per-dataset": with per-dataframe categorical
codes there was no way to re-score a row under a different flow.

With `FeatureEncoder` there is. `score_policy` evaluates the fitted mu at every
eligible flow for every row and takes the row-wise minimum, subject to the
correctness floor tau.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import FeatureEncoder

#: A template must appear this often in a training cell to be eligible.
MIN_SUPPORT = 10

#: How many templates per cell the deployable policy may choose among.
TOP_K = 3


@dataclass(frozen=True)
class EligibleSets:
    """Per-cell candidate templates, ranked by training frequency."""

    by_cell: dict[str, tuple[str, ...]]
    top_k: int = TOP_K
    min_support: int = MIN_SUPPORT

    @classmethod
    def fit(
        cls,
        df: pd.DataFrame,
        *,
        cell_col: str = "cell",
        flow_col: str = "flow_template",
        top_k: int = TOP_K,
        min_support: int = MIN_SUPPORT,
    ) -> "EligibleSets":
        by_cell: dict[str, tuple[str, ...]] = {}
        usable = df[df[flow_col].notna()]
        for cell, group in usable.groupby(cell_col):
            counts = group[flow_col].value_counts()
            supported = counts[counts >= min_support]
            if supported.empty:
                continue
            by_cell[cell] = tuple(supported.head(top_k).index)
        return cls(by_cell=by_cell, top_k=top_k, min_support=min_support)

    def candidates(self, cell: object) -> tuple[str, ...]:
        return self.by_cell.get(cell, ())

    @property
    def universe(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for flows in self.by_cell.values():
            for flow in flows:
                seen[flow] = None
        return tuple(seen)


@dataclass(frozen=True)
class PolicyScores:
    """Row-wise policy choice and the predicted outcomes behind it."""

    flow: pd.Series  # chosen template
    mu: pd.Series  # predicted days under the chosen template
    pi: pd.Series | None  # predicted P(correct) under the chosen template
    n_eligible: pd.Series  # candidates actually evaluated


def score_policy(
    df: pd.DataFrame,
    *,
    encoder: FeatureEncoder,
    mu_model,
    eligible: EligibleSets,
    features: list[str],
    pi_model=None,
    tau: float = 0.0,
    cell_col: str = "cell",
    observed_flow_col: str = "flow_template",
    observed_mu: pd.Series | None = None,
    smearing=None,
    strata: pd.Series | None = None,
) -> PolicyScores:
    """Evaluate mu (and pi) at every eligible flow; return the row-wise argmin.

    Rows whose cell has no eligible template, or whose every candidate fails the
    correctness floor, fall back to the observed flow and `observed_mu`. `tau`
    is the regulator's floor: speed may not be bought with `bare` disposals.

    `smearing` retransforms the log-scale prediction to days. Without it the
    bare `expm1` returns something closer to a conditional median (Def. 5.3),
    which matters twice over here: the direct term lands on the wrong scale, and
    the argmin then ranks candidates by median rather than by mean duration.
    Those are different orderings whenever the residual spread varies by flow.
    """
    universe = eligible.universe
    if not universe:
        raise ValueError("no eligible templates; check MIN_SUPPORT and the training cells")

    # One prediction pass per candidate template over the whole frame. Cheaper
    # and far less error-prone than per-row feature surgery.
    mu_by_flow: dict[str, np.ndarray] = {}
    pi_by_flow: dict[str, np.ndarray] = {}
    for flow in universe:
        X = encoder.transform(df, flow=flow)[features]
        predicted = mu_model.predict(X)
        mu_by_flow[flow] = (
            np.expm1(predicted)
            if smearing is None
            else smearing.apply(predicted, strata=strata)
        )
        if pi_model is not None:
            pi_by_flow[flow] = pi_model.predict_proba(X)[:, 1]

    allowed = pd.Series([eligible.candidates(c) for c in df[cell_col]], index=df.index)

    best_flow: list[object] = []
    best_mu: list[float] = []
    best_pi: list[float] = []
    n_eligible: list[int] = []

    observed_flow = df[observed_flow_col]
    fallback_mu = (
        observed_mu if observed_mu is not None else pd.Series(np.nan, index=df.index)
    )

    for position, (index, candidates) in enumerate(allowed.items()):
        chosen, chosen_mu, chosen_pi = None, np.inf, np.nan
        evaluated = 0
        for flow in candidates:
            if pi_model is not None and pi_by_flow[flow][position] < tau:
                continue
            evaluated += 1
            value = mu_by_flow[flow][position]
            if value < chosen_mu:
                chosen, chosen_mu = flow, float(value)
                chosen_pi = float(pi_by_flow[flow][position]) if pi_model is not None else np.nan
        if chosen is None:
            chosen = observed_flow.iat[position]
            chosen_mu = float(fallback_mu.iat[position])
        best_flow.append(chosen)
        best_mu.append(chosen_mu)
        best_pi.append(chosen_pi)
        n_eligible.append(evaluated)

    return PolicyScores(
        flow=pd.Series(best_flow, index=df.index),
        mu=pd.Series(best_mu, index=df.index),
        pi=pd.Series(best_pi, index=df.index) if pi_model is not None else None,
        n_eligible=pd.Series(n_eligible, index=df.index),
    )
