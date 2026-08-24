"""Driver: load the fitted artefacts, build the policy arms, run OPE on one split.

Kept apart from `ope.py` so the estimator there stays importable and testable
without sklearn, a pickle, or any access to `data/`.

WHAT THIS CARRIES THAT THE 11 AUG VERSION DID NOT
--------------------------------------------------
The earlier driver evaluated on `{split}_correct.parquet` with an unweighted
`days_capped` outcome, raw `expm1` retransformation and the uncalibrated
classifier. Every one of those is a different estimand from the one the design
document specifies, and four of them were flagged in review of #264. All four
are now wired:

* the population uses closure proxy `S_tilde`, not the binary-correct
  completers; it remains descriptive for the latent `S*` target;
* the outcome is the restricted duration with IPCW weights, so censored rows are
  reweighted rather than dropped;
* both the observed and the counterfactual predictions are smeared back to the
  day scale;
* the correctness floor is applied to the isotonically calibrated `pi`.

ON CROSS-FITTING
----------------
Cross-fitting exists to stop a nuisance being evaluated at the observations it
was fitted on (§6.1). This design already has that, and by a stronger route than
fold rotation: every nuisance -- `mu`, `pi`, the propensity and the eligible
sets -- is estimated on 2021-23 and evaluated on 2024 or 2025. The evaluation
rows are not merely out of fold, they are out of period, so the empirical-process
term Proposition 5.4 worries about does not arise.

What the chronological split does *not* fix is the winner's curse. The policy is
`argmin_a mu_hat_a(x)`, and the argmin selects whichever candidate's estimate is
most negatively biased regardless of which rows the value is measured on, because
the bias lives in `mu_hat` rather than in the evaluation sample (Lemma F.3). So
`--policy-split` fits a second `mu` on a disjoint half of the training clusters,
chooses the policy with one and takes the direct term from the other. The gap
between the split and unsplit values is the curse, priced.
"""

from __future__ import annotations

import json
import pickle

import numpy as np
import pandas as pd

from . import censoring, crossfit, paths
from . import tau as tau_module
from .features import ACTION_DEFINITION, cell_key, decode_flow_columns
from .ope import dr_scores, evaluate_arm, historical_value, summarise
from .policy import EligibleSets, require_finite_predictions, score_policy
from .propensity import EmpiricalSharePropensity

#: Wide enough to act as "any supported template in the cell".
UNRESTRICTED_TOP_K = 50


def _load_split(name: str, user_role: dict[str, str], *, subset: str) -> pd.DataFrame:
    df = pd.read_parquet(paths.out(f"{name}_{subset}.parquet"))
    decode_flow_columns(df, user_role)
    df["cell"] = cell_key(df)
    created = pd.to_datetime(df["created_on"], errors="coerce")
    df["cluster"] = df["district"].fillna("MISSING").astype(str) + "|" + created.dt.year.astype(
        "Int64"
    ).astype(str)
    return df


def run(
    *,
    split: str = "val",
    tau: float = 0.0,
    top_k: int = 3,
    mu: str = "gbm",
    policy_split: bool = False,
) -> dict:
    with open(paths.out("models.pkl"), "rb") as handle:
        artefacts = pickle.load(handle)
    if artefacts.get("action_definition") != ACTION_DEFINITION:
        raise ValueError(
            "models.pkl predates the joint department-chain action; rebuild the mart and models"
        )

    encoder = artefacts["encoder"]
    features = artefacts["features"]
    # The GBM overfits: it wins on train and loses to the ridge on validation.
    # Which mu backs the policy is a real choice, so it is a flag rather than a
    # constant, and the chosen model is recorded in the summary.
    mu_model = artefacts[f"mu_{mu}"]
    # The calibrated classifier, not the raw one. `tau` is a floor on a
    # probability, so an uncalibrated `pi` makes the floor a statement about the
    # classifier rather than about correctness (Table 8).
    pi_model = artefacts.get("pi_calibrated") or artefacts.get("pi_gbm")
    smearing = (artefacts.get("smearing") or {}).get(mu)
    user_role = artefacts["user_role"]

    # Routing happens at intake, before correctness is known, so the propensity
    # and the eligible sets are estimated on every training case. The first run
    # fitted both on the correct-only subset, conditioning the treatment model
    # on a post-treatment outcome.
    train = _load_split("train", user_role, subset="all")
    # The proxy-selected population, not the binary-correct completers. Legacy
    # `S` is closure-derived S_tilde, so this does not identify the S*=1 target.
    evaluation = _load_split(split, user_role, subset="actionable")
    cohort = _load_split(split, user_role, subset="all")

    propensity = EmpiricalSharePropensity.fit(train)
    arms_config = {
        "eligible": EligibleSets.fit(train, top_k=top_k),
        "unrestricted": EligibleSets.fit(train, top_k=UNRESTRICTED_TOP_K),
    }
    supported_cells = set.intersection(
        *(set(config.supported_cells) for config in arms_config.values())
    )
    n_before_support = len(evaluation)
    evaluation = evaluation[evaluation["cell"].isin(supported_cells)].copy()
    if evaluation.empty:
        raise ValueError("no evaluation rows have a nonempty supported action set")
    n_support_excluded = n_before_support - len(evaluation)

    # Restricted duration with censoring weights. `G` is fitted on the whole
    # arrival cohort: every proxy-selected row is resolved by construction, so a
    # curve fitted in place would be the constant 1 and the weights all exactly
    # 1 -- a correction that silently does nothing.
    restricted = censoring.restricted_outcome(
        evaluation, fit_frame=cohort, stratum_col="cluster"
    )
    evaluation["outcome"] = restricted.y
    evaluation["ipcw"] = restricted.weight

    X_observed = encoder.transform(evaluation)[features]
    predicted = require_finite_predictions(
        mu_model.predict(X_observed), quantity="log-duration", action="<observed>"
    )
    observed_days = (
        np.expm1(predicted)
        if smearing is None
        else smearing.apply(predicted, strata=evaluation["cluster"])
    )
    evaluation["mu_observed"] = require_finite_predictions(
        observed_days, quantity="duration", action="<observed>"
    )

    policy_mu_model = mu_model
    curse = None
    if policy_split:
        policy_mu_model, curse = _split_policy_model(
            train, encoder, features, mu, artefacts
        )

    historical = historical_value(
        evaluation,
        outcome_col="outcome",
        mu_observed_col="mu_observed",
        censoring_weight_col="ipcw",
    )

    # pi under the observed flow, for the correctness arm's residual.
    evaluation["pi_observed"] = require_finite_predictions(
        pi_model.predict_proba(X_observed)[:, 1],
        quantity="correctness",
        action="<observed>",
    )

    arms = []
    correctness: dict[str, dict] = {}
    for name, eligible in arms_config.items():
        scored = score_policy(
            evaluation,
            encoder=encoder,
            mu_model=policy_mu_model,
            eligible=eligible,
            features=features,
            # Always passed, not only when tau > 0: the chosen flow's predicted
            # correctness is the direct term of the constraint arm. At tau = 0
            # the floor admits every candidate, so this does not change the
            # policy, only what is recorded about it.
            pi_model=pi_model,
            tau=tau,
            smearing=smearing,
            strata=evaluation["cluster"],
        )
        evaluation[f"{name}_action"] = scored.action
        evaluation[f"{name}_mu"] = scored.mu
        evaluation[f"{name}_pi"] = scored.pi
        arm, _ = evaluate_arm(
            evaluation,
            name=name,
            outcome_col="outcome",
            mu_observed_col="mu_observed",
            mu_policy_col=f"{name}_mu",
            policy_action_col=f"{name}_action",
            observed_action_col="action_template",
            propensity=propensity,
            censoring_weight_col="ipcw",
        )
        arms.append(arm)
        correctness[name] = _correctness_value(
            evaluation,
            name=name,
            propensity=propensity,
            n_eligible=scored.n_eligible,
            fallback=scored.fallback,
        )

    censoring_rate = float(artefacts.get("censoring_rate", {}).get(split, float("nan")))
    summary = summarise(historical, arms, censoring_rate=censoring_rate, n=len(evaluation))
    summary["split"] = split
    summary["action_definition"] = ACTION_DEFINITION
    summary["tau"] = tau
    summary["top_k"] = top_k
    summary["mu_model"] = mu
    summary["mu_val_rmse"] = artefacts["metrics"][mu]["val"]
    summary["horizon"] = censoring.HORIZON
    summary["ipcw"] = restricted.summary()
    summary["smearing"] = smearing.summary() if smearing else None
    summary["pi_calibrated"] = "pi_calibrated" in artefacts
    summary["policy_split"] = curse
    summary["correctness"] = correctness
    summary["historical_correct"] = _historical_correct(evaluation)
    summary["support"] = {
        "n_before": n_before_support,
        "n_evaluated": len(evaluation),
        "n_excluded": n_support_excluded,
        "n_supported_cells": len(supported_cells),
    }

    with open(paths.out(f"ope_{split}_{mu}.json"), "w") as handle:
        json.dump(summary, handle, indent=2, default=float)
    return summary


def _historical_correct(evaluation: pd.DataFrame) -> dict:
    """Realised correctness rate under the logging policy, the constraint's floor.

    Rows where `C` is undetermined are excluded rather than counted as failures.
    `C` is NULL for the `as reported` bucket -- 7.45% of resolved grievances --
    and imputing zero there would invent that many no-action closures, deflate
    the historical floor, and make every candidate policy look feasible.
    """
    labelled = evaluation["C"].notna()
    return {
        "rate": float(evaluation.loc[labelled, "C"].mean()) if labelled.any() else float("nan"),
        "n_labelled": int(labelled.sum()),
        "n_undetermined": int((~labelled).sum()),
    }


def _correctness_value(
    evaluation: pd.DataFrame,
    *,
    name: str,
    propensity: EmpiricalSharePropensity,
    n_eligible: pd.Series,
    fallback: pd.Series,
) -> dict:
    """V_C(delta): the augmented score with C in place of Y.

    Same estimator as the duration arm so the two axes of the frontier are
    like-for-like. No censoring weight here: `C` is observed exactly when the
    case closed, which is every row in this population, so there is nothing to
    reweight -- and applying the duration IPCW would upweight slow cases on an
    axis where speed is not the outcome.
    """
    labelled = evaluation["C"].notna().to_numpy()
    matched = (
        (evaluation[f"{name}_action"] == evaluation["action_template"])
        & evaluation["action_template"].notna()
    ).to_numpy()
    e_policy = propensity.score(evaluation["cell"], evaluation[f"{name}_action"]).to_numpy()

    scores = dr_scores(
        outcome=evaluation["C"].fillna(0).to_numpy(dtype=float),
        mu_observed=evaluation["pi_observed"].to_numpy(dtype=float),
        mu_policy=evaluation[f"{name}_pi"].to_numpy(dtype=float),
        matched=(matched & labelled),
        propensity=e_policy,
    )
    direct = evaluation[f"{name}_pi"].to_numpy(dtype=float)
    evaluated_scores = scores[labelled]
    if not np.isfinite(direct).all():
        raise ValueError(f"{name} policy has non-finite predicted correctness")
    if not np.isfinite(evaluated_scores).all():
        raise ValueError(f"{name} policy has non-finite augmented correctness scores")
    return {
        "v_direct": float(direct.mean()),
        "v_dr": float(evaluated_scores.mean()),
        "n_labelled": int(labelled.sum()),
        "mean_eligible": float(n_eligible.mean()),
        "n_fallback": int(fallback.sum()),
    }


def _split_policy_model(train, encoder, features, mu, artefacts):
    """Refit `mu` on half the training clusters, for choosing the policy only.

    Lemma F.3: the argmin selects the candidate whose estimate is most
    negatively biased, so a policy chosen with the same `mu` that supplies the
    direct term flatters itself. Choosing with an independently fitted `mu`
    removes the shared noise, and the difference between the two values is the
    curse rather than a treatment effect.
    """
    from .models import boosted_duration_model, ridge_duration_model
    from .train import _weight_kwarg

    folds = crossfit.assign_folds(train["cluster"], n_folds=2)
    rows = folds.train_rows(0)
    subset = train.iloc[rows]
    subset = subset[subset["S"] == 1]
    if subset.empty:
        return artefacts[f"mu_{mu}"], {"fitted": False, "reason": "no proxy-selected rows in fold"}

    restricted = censoring.restricted_outcome(
        subset, fit_frame=train.iloc[rows], stratum_col="cluster"
    )
    keep = restricted.weight.to_numpy() > 0
    y = np.log1p(restricted.y.clip(0, censoring.HORIZON))[keep]
    x = encoder.transform(subset)[features][keep]

    model = (
        ridge_duration_model(features)
        if mu == "ridge"
        else boosted_duration_model(features)
    )
    model.fit(
        x,
        y,
        **_weight_kwarg(model, restricted.weight[keep]),
    )
    return model, {"fitted": True, "n": int(keep.sum()), "n_folds": 2}


def sweep_frontier(
    *,
    split: str = "val",
    top_k: int = 3,
    mu: str = "ridge",
    policy_split: bool = False,
    arm: str = "eligible",
) -> dict:
    """Trace tau -> (duration, correctness) and diagnose the feasible floor.

    Corollary 4.6. `tau*` is the *smallest* floor meeting the constraint, since
    any larger one buys correctness the constraint did not ask for and pays for
    it in days. The whole curve is reported because it, and not the selected
    point, is what answers the question an administrator actually has: what does
    a day of delay buy.

    Both direct-method and augmented values are retained. The fitted direct
    scores inherit the pointwise monotonicity result; finite-sample augmented
    estimates need not, because their residual corrections and policy matches
    change with the floor. A single `tau_star` is reported only when the two
    estimators agree. `C` is NULL for the `as reported` bucket, and those rows
    are excluded from the correctness axis rather than counted as failures.
    """
    # The constraint's floor is the realised correctness rate under the logging
    # policy, read once at tau = 0 rather than assumed.
    baseline = run(split=split, tau=0.0, top_k=top_k, mu=mu, policy_split=policy_split)
    floor = baseline["historical_correct"]["rate"]

    points = []
    for tau_value in tau_module.DEFAULT_GRID:
        summary = run(
            split=split, tau=tau_value, top_k=top_k, mu=mu, policy_split=policy_split
        )
        arm_summary = summary["arms"][arm]
        correct = summary["correctness"][arm]
        points.append(
            tau_module.FrontierPoint(
                tau=tau_value,
                v_duration_dm=arm_summary["v_direct"],
                v_duration_aipw=arm_summary["v_dr"],
                v_correct_dm=correct["v_direct"],
                v_correct_aipw=correct["v_dr"],
                feasible_dm=correct["v_direct"] >= floor,
                feasible_aipw=correct["v_dr"] >= floor,
                n_fallback=correct["n_fallback"],
                mean_eligible=correct["mean_eligible"],
            )
        )
    frame = tau_module.frontier_frame(points)
    best_dm = tau_module.smallest_feasible(points, estimator="dm")
    best_aipw = tau_module.smallest_feasible(points, estimator="aipw")
    estimators_agree = (
        best_dm is not None
        and best_aipw is not None
        and best_dm.tau == best_aipw.tau
    )
    if estimators_agree:
        status = "estimators_agree"
        note = None
    elif best_dm is None and best_aipw is None:
        status = "no_feasible_floor"
        note = (
            "Neither estimator finds a floor on the grid meeting historical "
            "correctness. Do not substitute the largest tau."
        )
    else:
        status = "unresolved_estimator_disagreement"
        note = (
            "Direct and AIPW correctness estimates select different floors. "
            "The finite-sample AIPW curve need not be monotone; report both "
            "candidates and do not present either as the resolved tau star."
        )
    out = {
        "split": split,
        "mu_model": mu,
        "arm": arm,
        "top_k": top_k,
        "policy_split": policy_split,
        "historical_correct": baseline["historical_correct"],
        "tau_star": best_dm.as_dict() if estimators_agree else None,
        "tau_star_dm": best_dm.as_dict() if best_dm else None,
        "tau_star_aipw": best_aipw.as_dict() if best_aipw else None,
        "tau_star_status": status,
        "tau_star_note": note,
        "monotonicity": tau_module.monotonicity_report(points),
        "frontier": frame.to_dict(orient="records"),
    }
    with open(paths.out(f"frontier_{split}_{mu}.json"), "w") as handle:
        json.dump(out, handle, indent=2, default=float)
    return out
