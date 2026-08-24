"""Eligible joint-action sets and the greedy policy delta(x) = argmin mu_a(x).

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
    """Per-cell joint actions, ranked by training frequency."""

    by_cell: dict[str, tuple[str, ...]]
    top_k: int = TOP_K
    min_support: int = MIN_SUPPORT

    @classmethod
    def fit(
        cls,
        df: pd.DataFrame,
        *,
        cell_col: str = "cell",
        action_col: str = "action_template",
        top_k: int = TOP_K,
        min_support: int = MIN_SUPPORT,
    ) -> "EligibleSets":
        by_cell: dict[str, tuple[str, ...]] = {}
        usable = df[df[action_col].notna()]
        for cell, group in usable.groupby(cell_col):
            counts = group[action_col].value_counts()
            supported = counts[counts >= min_support]
            if supported.empty:
                continue
            by_cell[cell] = tuple(supported.head(top_k).index)
        return cls(by_cell=by_cell, top_k=top_k, min_support=min_support)

    def candidates(self, cell: object) -> tuple[str, ...]:
        return self.by_cell.get(cell, ())

    @property
    def supported_cells(self) -> frozenset[str]:
        """Cells where the support-restricted action set is nonempty."""
        return frozenset(self.by_cell)

    @property
    def universe(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for actions in self.by_cell.values():
            for action in actions:
                seen[action] = None
        return tuple(seen)


@dataclass(frozen=True)
class PolicyScores:
    """Row-wise policy choice and the predicted outcomes behind it."""

    action: pd.Series  # chosen department-chain pair
    mu: pd.Series  # predicted days under the chosen action
    pi: pd.Series | None  # predicted P(correct) under the chosen action
    n_eligible: pd.Series  # candidates meeting the floor
    fallback: pd.Series  # no candidate met the floor; highest-pi flow used


def require_finite_predictions(
    values: np.ndarray, *, quantity: str, action: str
) -> np.ndarray:
    """Fail closed instead of letting ``nanmean`` change the target population."""
    array = np.asarray(values, dtype=float)
    bad = ~np.isfinite(array)
    if bad.any():
        raise ValueError(
            f"non-finite {quantity} predictions for action {action!r}: "
            f"{int(bad.sum())} of {len(array)} rows"
        )
    return array


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
    smearing=None,
    strata: pd.Series | None = None,
) -> PolicyScores:
    """Evaluate mu (and pi) at every eligible action; return the row-wise argmin.

    Every row must have a nonempty candidate set; callers restrict the target
    population to supported cells before scoring. If no candidate meets the
    correctness floor, the total rule chooses the candidate with the highest
    predicted correctness (ties follow the candidate order). Its predicted
    duration and correctness are retained. The fallback depends only on the
    row's pre-treatment inputs and fitted nuisance functions, never its observed
    treatment. `tau` is the regulator's floor: speed may not be bought with
    `bare` disposals.

    `smearing` retransforms the log-scale prediction to days. Without it the
    bare `expm1` returns something closer to a conditional median (Def. 5.3),
    which matters twice over here: the direct term lands on the wrong scale, and
    the argmin then ranks candidates by median rather than by mean duration.
    Those are different orderings whenever the residual spread varies by flow.
    """
    universe = eligible.universe
    if not universe:
        raise ValueError("no eligible actions; check MIN_SUPPORT and the training cells")
    if not 0 <= tau <= 1:
        raise ValueError("correctness floor tau must lie in [0, 1]")
    if pi_model is None and tau > 0:
        raise ValueError("a positive correctness floor requires pi_model")

    allowed = pd.Series([eligible.candidates(c) for c in df[cell_col]], index=df.index)
    positions_by_action: dict[str, list[int]] = {action: [] for action in universe}
    for position, (index, candidates) in enumerate(allowed.items()):
        if not candidates:
            raise ValueError(
                f"row {index!r} has no supported policy candidates; "
                "restrict evaluation to EligibleSets.supported_cells"
            )
        for action in candidates:
            positions_by_action[action].append(position)

    # Score a flow only on rows whose cell admits it. A flow may be supported
    # in only a handful of cells; evaluating every flow on the whole frame makes
    # the unrestricted arm needlessly quadratic in rows x global templates.
    mu_by_action: dict[str, np.ndarray] = {}
    pi_by_action: dict[str, np.ndarray] = {}
    for action in universe:
        positions = np.asarray(positions_by_action[action], dtype=int)
        if not len(positions):
            continue
        rows = df.iloc[positions]
        X = encoder.transform(rows, action=action)[features]
        predicted = require_finite_predictions(
            mu_model.predict(X), quantity="log-duration", action=action
        )
        row_strata = strata.iloc[positions] if strata is not None else None
        mu_days = (
            np.expm1(predicted)
            if smearing is None
            else smearing.apply(predicted, strata=row_strata)
        )
        action_mu = np.full(len(df), np.nan)
        action_mu[positions] = require_finite_predictions(
            mu_days, quantity="duration", action=action
        )
        mu_by_action[action] = action_mu
        if pi_model is not None:
            pi = require_finite_predictions(
                pi_model.predict_proba(X)[:, 1], quantity="correctness", action=action
            )
            if ((pi < 0) | (pi > 1)).any():
                raise ValueError(
                    f"correctness predictions outside [0, 1] for action {action!r}"
                )
            action_pi = np.full(len(df), np.nan)
            action_pi[positions] = pi
            pi_by_action[action] = action_pi

    best_action: list[object] = []
    best_mu: list[float] = []
    best_pi: list[float] = []
    n_eligible: list[int] = []
    used_fallback: list[bool] = []

    for position, (index, candidates) in enumerate(allowed.items()):
        chosen, chosen_mu, chosen_pi = None, np.inf, np.nan
        evaluated = 0
        for action in candidates:
            if pi_model is not None and pi_by_action[action][position] < tau:
                continue
            evaluated += 1
            value = mu_by_action[action][position]
            if value < chosen_mu:
                chosen, chosen_mu = action, float(value)
                chosen_pi = (
                    float(pi_by_action[action][position])
                    if pi_model is not None
                    else np.nan
                )
        if chosen is None:
            # Total pre-treatment fallback from the same admissible set. The
            # fixed candidate order resolves ties reproducibly.
            chosen = max(candidates, key=lambda action: pi_by_action[action][position])
            chosen_mu = float(mu_by_action[chosen][position])
            chosen_pi = float(pi_by_action[chosen][position])
            used_fallback.append(True)
        else:
            used_fallback.append(False)
        best_action.append(chosen)
        best_mu.append(chosen_mu)
        best_pi.append(chosen_pi)
        n_eligible.append(evaluated)

    return PolicyScores(
        action=pd.Series(best_action, index=df.index),
        mu=pd.Series(best_mu, index=df.index),
        pi=pd.Series(best_pi, index=df.index) if pi_model is not None else None,
        n_eligible=pd.Series(n_eligible, index=df.index),
        fallback=pd.Series(used_fallback, index=df.index, dtype=bool),
    )
