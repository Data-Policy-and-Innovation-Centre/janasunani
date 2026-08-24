"""Cross-fitting and sample splitting for the learned policy.

Equation (5.4) and §6.1 of `docs/experiments/routing-outcome-model.tex`.

TWO DISTINCT BIASES, TWO DISTINCT SPLITS
-----------------------------------------
They are easy to conflate and the fix for one does not fix the other.

*Cross-fitting* addresses the nuisance estimates. Corollary 5.2 tolerates
slow-converging `mu` and `e`, but the argument needs the score to be evaluated
at observations independent of the ones the nuisance was fitted on. Otherwise an
empirical-process term need not vanish, and the classical repair -- requiring
the estimator to live in a Donsker class -- excludes exactly the gradient
boosting we want to use. Fitting outside each fold and scoring inside it removes
the requirement entirely, at a constant factor in compute and nothing in rate.

*Sample splitting* addresses the policy. The rule is `argmin_a mu_hat_a(x)`,
and an argmin over noisy estimates selects whichever candidate is most
negatively biased: `E[min_a mu_hat_a] <= min_a E[mu_hat_a]` (Lemma F.3). The
gap grows with the noise and with the number of candidates, so a policy free to
pick among many thin templates flatters itself for purely statistical reasons.
Learning the rule on one partition and valuing it on another makes the rule a
fixed function from the evaluation fold's point of view, and the curse does not
bite.

FOLDS FOLLOW CLUSTERS, NOT ROWS
--------------------------------
Grievances in a district-year share administrative shocks -- a vacancy, an
election, a flood -- which is why §5.7 clusters the variance there. Splitting
i.i.d. across rows would put two grievances from the same shock on opposite
sides of a fold boundary and leak precisely the correlation the clustering
exists to price, making the out-of-fold score look more independent than it is.
Whole clusters move together.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Folds for the nuisance cross-fit.
N_FOLDS = 5

#: Reproducibility for the fold assignment.
SEED = 20260813


@dataclass(frozen=True)
class ClusterFolds:
    """Fold index per row, assigned so no cluster spans two folds."""

    fold: np.ndarray
    n_folds: int
    cluster_sizes: dict[str, int]

    def train_rows(self, k: int) -> np.ndarray:
        """Rows outside fold `k`: where nuisances for fold `k` are fitted."""
        return np.flatnonzero(self.fold != k)

    def score_rows(self, k: int) -> np.ndarray:
        """Rows inside fold `k`: where the score for fold `k` is evaluated."""
        return np.flatnonzero(self.fold == k)

    def balance(self) -> list[int]:
        return [int((self.fold == k).sum()) for k in range(self.n_folds)]


def assign_folds(
    clusters: pd.Series, *, n_folds: int = N_FOLDS, seed: int = SEED
) -> ClusterFolds:
    """Partition whole clusters into `n_folds`, greedily balancing row counts.

    Greedy largest-first rather than a random draw: district-year clusters are
    very unequal in size, and a random assignment of a few hundred clusters
    routinely produces folds differing by tens of percent, which shows up as
    fold-to-fold variance that looks like instability in the estimate.
    """
    keys = clusters.astype(str).to_numpy()
    unique, counts = np.unique(keys, return_counts=True)

    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(len(unique))
    order = shuffled[np.argsort(-counts[shuffled], kind="stable")]

    loads = np.zeros(n_folds, dtype=int)
    cluster_fold: dict[str, int] = {}
    for index in order:
        target = int(np.argmin(loads))
        cluster_fold[unique[index]] = target
        loads[target] += counts[index]

    fold = np.array([cluster_fold[k] for k in keys], dtype=int)
    return ClusterFolds(
        fold=fold,
        n_folds=n_folds,
        cluster_sizes={str(u): int(c) for u, c in zip(unique, counts)},
    )


def split_for_policy(
    folds: ClusterFolds, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """Policy-learning rows and evaluation rows for fold `k`.

    Nested against the nuisance split: the rule for fold `k` is learned on the
    same out-of-fold rows the nuisances were fitted on, and both are then
    applied to fold `k`. So the evaluation rows saw neither the nuisance fit nor
    the argmin, which is what Corollary 4.4 needs to apply unmodified.
    """
    return folds.train_rows(k), folds.score_rows(k)


def crossfit_mean(
    scores_by_fold: dict[int, np.ndarray], *, n_folds: int = N_FOLDS
) -> float:
    """The fold-averaged estimate of equation (5.4).

    Averaging fold *means* rather than pooling all scores: the folds are the
    independent replicates here, and pooling would weight a fold by its size in a
    way the theory does not license.
    """
    means = [
        float(np.mean(scores_by_fold[k]))
        for k in range(n_folds)
        if k in scores_by_fold and len(scores_by_fold[k])
    ]
    if not means:
        return float("nan")
    return float(np.mean(means))
