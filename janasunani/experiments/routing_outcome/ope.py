"""Developmental OPE for joint assignment under a correctness constraint.

The treatment is the intention to route: the jointly selected department and
complete assigned chain. The current lake snapshot does not prove that those
fields preserve their immutable initial values, so outputs remain observational
diagnostics rather than causal effects or savings.

Replaces both the original `ope.py` (test 2025) and `val_ope.py` (val 2024),
which were near-duplicate scripts that disagreed with each other about what the
historical baseline even was. Four defects in the as-run estimator are fixed
here, in descending order of how much they moved the answer.

1. **The two arms used different estimators.** `V_hist` was the mean of the GBM
   prediction; `V_policy` was the mean of a *training cell mean* in days. Their
   difference (reported as `delta_policy`, 9.5 days on val) is not a treatment
   contrast -- it is mostly the gap between a fitted model and a raw group
   average. Both arms now come from the same fitted mu.

2. **The "oracle" minimised over noise.** `V_oracle` took, for each
   `category x district` cell, the *smallest realised* mu_hat among the rows in
   that cell, and averaged it. Minimising over sampling noise within a stratum
   is mechanically far below the stratum mean, and gets further below it the
   more rows the cell has, so the reported 46.9-day "upper bound" measured
   sample size as much as routing. The unrestricted arm here instead evaluates
   mu at every supported template *for each row* and takes the row-wise
   minimum, which is a real (if optimistic) bound.

3. **The DR residual did not centre.** The score was
   `policy_mu + (T - mu_hat_observed)/e`, mixing a training cell mean in the
   direct term with a GBM prediction in the residual, so the correction carried
   the difference between the two estimators rather than the model's error.
   Both terms now use the same mu.

4. **The score was clipped to [0, 365].** Clipping an augmented score is not a
   robustness measure; it is a bias whose size depends on the propensity draw.
   The propensity is clipped instead (a stated assumption), the correction is
   self-normalised, and ESS is reported so a correction resting on a handful of
   matched rows is visible rather than silent.

Uncertainty is a cluster bootstrap by `district x creation-year`, which the
as-run version reported not at all -- every headline number was a point
estimate with no standard error.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .propensity import EmpiricalSharePropensity, overlap_report

#: Bootstrap replicates for the cluster bootstrap.
N_BOOTSTRAP = 200

#: Reproducibility for the bootstrap draw.
SEED = 20260811


@dataclass
class ArmValue:
    """Value by direct method and augmented score (legacy field name ``v_dr``).

    With censored outcomes, the augmented score is doubly robust in the outcome
    and propensity models only conditional on a correct censoring model.
    """

    name: str
    v_direct: float
    v_dr: float
    v_dr_se: float | None = None
    overlap: dict | None = None


def dr_scores(
    outcome: np.ndarray,
    mu_observed: np.ndarray,
    mu_policy: np.ndarray,
    matched: np.ndarray,
    propensity: np.ndarray,
    *,
    censoring_weight: np.ndarray | None = None,
    self_normalise: bool = True,
) -> np.ndarray:
    """Per-row augmented IPCW scores for a deterministic policy.

    Gamma_i = mu_delta(x_i)
              + 1{A_i = delta(x_i)} * R_i / (e_delta(x_i) G(Y_i-)) * (Y_i - mu_{A_i}(x_i))

    Both mu terms must come from the same fitted model, otherwise the residual
    absorbs the difference between two estimators instead of the model's error.
    With `self_normalise`, the correction is divided by the mean weight rather
    than by n, which bounds the influence of a single low-propensity match.

    `censoring_weight` is the IPCW factor R/G from `censoring.py`. It multiplies
    into the same weight as the propensity because the two corrections compose:
    a row must be both routed the way the policy would route it *and* observed
    long enough to have its restricted duration known. Omitting it leaves the
    residual correction on the completers, which is the selection the restricted
    mean exists to undo.

    The score is doubly robust in ``mu`` and ``propensity`` only when ``G`` is
    correct. An exact outcome regression does not protect against a wrong
    censoring weight because ``R/G(Y)`` changes the conditional residual mean.
    Without censoring-martingale augmentation this is not the censored-data
    efficient influence function.
    """
    factor = np.ones_like(propensity, dtype=float) if censoring_weight is None else np.asarray(
        censoring_weight, dtype=float
    )
    weights = np.where(matched, factor / propensity, 0.0)
    residual = outcome - mu_observed
    correction = weights * residual
    if self_normalise:
        total = weights.sum()
        if total > 0:
            correction = correction * (len(weights) / total)
    return mu_policy + correction


def cluster_bootstrap_se(
    scores: np.ndarray, clusters: np.ndarray, *, n_boot: int = N_BOOTSTRAP, seed: int = SEED
) -> float:
    """Standard error of `mean(scores)` resampling whole clusters with replacement."""
    rng = np.random.default_rng(seed)
    unique, inverse = np.unique(clusters, return_inverse=True)
    by_cluster = [np.flatnonzero(inverse == i) for i in range(len(unique))]
    if len(by_cluster) < 2:
        return float("nan")
    means = np.empty(n_boot)
    for b in range(n_boot):
        drawn = rng.integers(0, len(by_cluster), size=len(by_cluster))
        idx = np.concatenate([by_cluster[d] for d in drawn])
        means[b] = scores[idx].mean()
    return float(means.std(ddof=1))


def evaluate_arm(
    df: pd.DataFrame,
    *,
    name: str,
    outcome_col: str,
    mu_observed_col: str,
    mu_policy_col: str,
    policy_action_col: str,
    observed_action_col: str,
    propensity: EmpiricalSharePropensity,
    cell_col: str = "cell",
    cluster_col: str = "cluster",
    censoring_weight_col: str | None = None,
) -> tuple[ArmValue, np.ndarray]:
    """Direct-method and DR value for one policy arm, with overlap diagnostics."""
    matched = (df[policy_action_col] == df[observed_action_col]) & df[
        observed_action_col
    ].notna()
    e_policy = propensity.score(df[cell_col], df[policy_action_col])
    censoring_weight = (
        df[censoring_weight_col].to_numpy(dtype=float) if censoring_weight_col else None
    )

    scores = dr_scores(
        outcome=df[outcome_col].to_numpy(dtype=float),
        mu_observed=df[mu_observed_col].to_numpy(dtype=float),
        mu_policy=df[mu_policy_col].to_numpy(dtype=float),
        matched=matched.to_numpy(dtype=bool),
        propensity=e_policy.to_numpy(dtype=float),
        censoring_weight=censoring_weight,
    )
    arm = ArmValue(
        name=name,
        v_direct=float(df[mu_policy_col].mean()),
        v_dr=float(scores.mean()),
        v_dr_se=cluster_bootstrap_se(scores, df[cluster_col].to_numpy()),
        overlap=overlap_report(e_policy, matched),
    )
    return arm, scores


def historical_value(
    df: pd.DataFrame,
    *,
    outcome_col: str,
    mu_observed_col: str,
    censoring_weight_col: str | None = None,
) -> ArmValue:
    """Value of the stochastic logging regime, not a deterministic rule.

    Each observed action is one draw from ``g_0(a | x)``. Averaging the fitted
    value at those realised draws is the Monte Carlo direct estimate of
    ``E[sum_a g_0(a | X) m_a(X)]``; the observed outcome mean estimates the same
    regime by the outcome route. Under the logging policy ``v_dr`` retains its
    legacy name but is computed as
    the Hájek IPCW mean of Y, not a plain mean. Weighting only the policy arms
    and not the baseline
    would difference a censoring-corrected value against an uncorrected one and
    report the correction as a treatment effect.

    The direct-method figure is the mean fitted value, kept separate so that a
    contrast is never taken across the two.
    """
    outcome = df[outcome_col].to_numpy(dtype=float)
    if censoring_weight_col:
        weight = df[censoring_weight_col].to_numpy(dtype=float)
        v_dr = float(np.average(outcome, weights=weight)) if weight.sum() > 0 else float("nan")
    else:
        v_dr = float(outcome.mean())
    return ArmValue(
        name="historical",
        v_direct=float(df[mu_observed_col].mean()),
        v_dr=v_dr,
    )


def summarise(
    historical: ArmValue, arms: list[ArmValue], *, censoring_rate: float, n: int
) -> dict:
    """Δ per arm, differenced like-with-like, plus the caveats that qualify it."""
    summary: dict = {
        "n": n,
        "censoring_rate_of_split": censoring_rate,
        "historical_regime": "stochastic_logging_policy",
        "historical": asdict(historical),
        "arms": {},
        "caveats": [
            "The legacy S column is the closure-derived proxy S_tilde, so the "
            "selected population is conditioned on resolution and does not "
            "identify the intake-time S*=1 target. IPCW corrects for differential speed "
            "among cases that closed; it does not restore cases still open at "
            "the snapshot, whose restricted duration is known but whose S_tilde is "
            "not. Constructing S* from pre-treatment inputs is unbuilt.",
            "The available X does not distinguish the responsible assigning "
            "office from the citizen-selected intake office. Category timing "
            "and the office/time-specific workflow menu are also unverified.",
            "The current complaint snapshot does not establish that dept_id "
            "and vchAllEscUser preserve the initial de jure assignment. The "
            "available action history records de facto handling but contains "
            "no route snapshots from which to reconstruct an overwritten "
            "intention to route. Until source-system immutability is verified, "
            "the treatment provenance is unresolved.",
            "The department-role-template action coarsens named-authority and "
            "resolution-time choices made in the same assignment transaction, "
            "so a causal interpretation requires a fixed treatment-version "
            "mechanism that is not yet established. The semantics of the portal's "
            "Assign Another ATA control are also unresolved.",
            "Congestion Q_r(t) and trailing destination performance are absent "
            "from X. They are proposed point-in-time system-state adjustments, "
            "not fields shown on the captured assignment form. Direction unknown.",
            "Propensity is an empirical category x district share, not the "
            "hierarchical penalized logit in the plan.",
            "The augmented duration score requires a correct censoring model "
            "in both outcome/propensity robustness branches and is not the "
            "censored-data efficient influence function without a censoring-"
            "martingale augmentation.",
            "Selection on officer observables is assumed, not tested.",
        ],
    }
    for arm in arms:
        summary["arms"][arm.name] = {
            **asdict(arm),
            "delta_direct": historical.v_direct - arm.v_direct,
            "delta_dr": historical.v_dr - arm.v_dr,
        }
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--split",
        choices=("val", "test"),
        default="val",
        help="val 2024 is the reporting slice (9.2%% censored); test 2025 is 34.4%%.",
    )
    parser.add_argument("--tau", type=float, default=0.0, help="correctness floor")
    parser.add_argument("--top-k", type=int, default=3, help="eligible templates per cell")
    parser.add_argument(
        "--mu",
        choices=("gbm", "ridge"),
        default="ridge",
        help="which fitted mu backs the policy; the ridge generalises better on val.",
    )
    parser.add_argument(
        "--policy-split",
        action="store_true",
        help="choose the policy with a mu fitted on disjoint training clusters, "
        "so the argmin cannot select on the same noise the direct term carries.",
    )
    parser.add_argument(
        "--sweep-tau",
        action="store_true",
        help="trace the speed-correctness frontier instead of reporting one tau.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # Import here so that `--help` does not require sklearn or touch the lake.
    from .run_ope import run, sweep_frontier

    if args.sweep_tau:
        summary = sweep_frontier(
            split=args.split, top_k=args.top_k, mu=args.mu, policy_split=args.policy_split
        )
    else:
        summary = run(
            split=args.split,
            tau=args.tau,
            top_k=args.top_k,
            mu=args.mu,
            policy_split=args.policy_split,
        )
    print(json.dumps(summary, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
