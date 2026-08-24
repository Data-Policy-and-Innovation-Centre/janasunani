"""Fit mu (restricted duration) and pi (P(action taken)) on the training split.

Corresponds to E2/E3 in the plan. M3 (Cox), M5 (hierarchical Bayes) and M6
(queue-augmented) from the plan's six-model suite are **not** implemented, and
nothing downstream may report an envelope over a suite that does not exist.

WHAT CHANGED FROM THE SUPERSEDED FIT
-------------------------------------
The 11 Aug fit trained `mu` on `log1p(days_capped)` over rows with `correct == 1`
-- completers only, on a population selected by a post-treatment outcome. Three
corrections, each from a module beside this one:

* **Population.** Rows are now the actionable ones (`S == 1`) from `outcome.py`,
  not the binary-correct ones. `S` does not vary with the flow, so conditioning
  on it is harmless; `correct` does, and conditioning on it was the
  principal-stratum error of Example 2.9.
* **Censoring.** The target is the restricted duration `Y = min(T, 365)` and
  rows carry IPCW weights from `censoring.py`, so censored cases are reweighted
  rather than dropped. This is what makes the 2025 cohort (34.4% censored)
  usable at all.
* **Retransformation.** A smearing factor is fitted here, on training residuals,
  and stored with the model. Without it every prediction in days is a
  conditional median wearing a mean's label.

`pi` is additionally isotonically calibrated on a held-out slice, because it
enters the policy only through the threshold `pi >= tau` and an uncalibrated
classifier makes `tau` uninterpretable.
"""

from __future__ import annotations

import json
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import brier_score_loss, mean_squared_error, roc_auc_score
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from . import censoring, paths, smear, tau
from .features import FeatureEncoder, decode_flow_columns
from .flow import load_tables

GBM_PARAMS = {"n_estimators": 200, "max_depth": 6, "learning_rate": 0.1, "random_state": 0}
CLF_PARAMS = {"n_estimators": 150, "max_depth": 4, "random_state": 0}


def _rmse(y_true, y_pred) -> float:
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))


def _weight_kwarg(model, weights) -> dict:
    """`sample_weight` reaches a bare estimator directly and a Pipeline by
    step name. Passing it unqualified to a Pipeline raises; omitting it fits an
    unweighted model that looks fine and silently ignores the censoring."""
    if isinstance(model, Pipeline):
        final = model.steps[-1][0]
        return {f"{final}__sample_weight": weights}
    return {"sample_weight": weights}


def _cluster(df: pd.DataFrame) -> pd.Series:
    created = pd.to_datetime(df["created_on"], errors="coerce")
    return (
        df["district"].fillna("MISSING").astype(str)
        + "|"
        + created.dt.year.astype("Int64").astype(str)
    )


def main() -> int:
    user_role = load_tables().user_role

    splits = {}
    cohorts = {}
    for name in ("train", "val", "test"):
        df = pd.read_parquet(paths.out(f"{name}_actionable.parquet"))
        decode_flow_columns(df, user_role)
        df["cluster"] = _cluster(df)
        splits[name] = df

        # The full arrival cohort, censored rows included. `S` is read off the
        # closing remark, so every actionable row is resolved and a censoring
        # curve fitted there would be the constant 1. `G` is estimated here.
        cohort = pd.read_parquet(paths.out(f"{name}_all.parquet"))
        cohort["cluster"] = _cluster(cohort)
        cohorts[name] = cohort

    # Levels are fitted once, on train, and reused for val and test. Fitting
    # them per split is the bug that invalidated the first run.
    encoder = FeatureEncoder.fit(splits["train"])
    features = encoder.feature_names()
    features_noflow = encoder.feature_names(include_flow=False)

    # Restricted duration with censoring weights, per split.
    restricted = {
        name: censoring.restricted_outcome(
            df, fit_frame=cohorts[name], stratum_col="cluster"
        )
        for name, df in splits.items()
    }
    target = {name: np.log1p(restricted[name].y.clip(0, censoring.HORIZON)) for name in splits}
    weight = {name: restricted[name].weight for name in splits}

    # Rows censored before the horizon carry zero weight and cannot train a
    # least-squares fit; sklearn accepts them but they contribute nothing and
    # distort the reported RMSE, so they are dropped from the fit only.
    fit_rows = {name: weight[name].to_numpy() > 0 for name in splits}

    metrics: dict[str, dict[str, float]] = {}

    def _fit(model, name: str, columns: list[str], transform_kwargs: dict | None = None):
        kwargs = transform_kwargs or {}
        rows = fit_rows["train"]
        x = encoder.transform(splits["train"], **kwargs)[columns][rows]
        model.fit(x, target["train"][rows], **_weight_kwarg(model, weight["train"][rows]))
        metrics[name] = {}
        for split in splits:
            keep = fit_rows[split]
            predicted = model.predict(encoder.transform(splits[split], **kwargs)[columns][keep])
            metrics[name][split] = _rmse(target[split][keep], predicted)
        return model

    ridge = _fit(make_pipeline(StandardScaler(), Ridge(alpha=1.0)), "ridge", features)
    gbm = _fit(GradientBoostingRegressor(**GBM_PARAMS), "gbm", features)

    gbm_noflow = _fit(
        GradientBoostingRegressor(**{**GBM_PARAMS, "random_state": 1}),
        "gbm_noflow",
        features_noflow,
        {"include_flow": False},
    )

    # Smearing factors, fitted on training residuals and stored with the models.
    # Strata are the duration clusters: homoskedasticity on the log scale is not
    # credible across two-day screen-outs and year-long land disputes.
    smearing = {}
    for name, model in (("ridge", ridge), ("gbm", gbm)):
        rows = fit_rows["train"]
        predicted = model.predict(encoder.transform(splits["train"])[features][rows])
        smearing[name] = smear.SmearingFactor.fit(
            target["train"][rows], predicted, strata=splits["train"]["cluster"][rows]
        )
        metrics[f"smearing_{name}"] = smearing[name].summary()

    # pi is fitted on every actionable case with C determined -- `C` is NULL for
    # the `as reported` bucket, and imputing zero there would invent 90,061
    # no-action closures.
    labelled = {name: splits[name]["C"].notna().to_numpy() for name in splits}
    design_all = {name: encoder.transform(df)[features] for name, df in splits.items()}
    label = {name: splits[name]["C"].fillna(0).astype(int) for name in splits}

    clf = GradientBoostingClassifier(**CLF_PARAMS)
    clf.fit(design_all["train"][labelled["train"]], label["train"][labelled["train"]])

    # Calibrated on validation, which the classifier did not train on.
    # Calibrating on train would reproduce the overfitting it exists to remove.
    calibrated = tau.calibrate(
        clf, design_all["val"][labelled["val"]], label["val"][labelled["val"]]
    )

    for tag, model in (("raw", clf), ("calibrated", calibrated)):
        metrics[f"pi_auc_{tag}"] = {}
        metrics[f"pi_brier_{tag}"] = {}
        metrics[f"pi_ece_{tag}"] = {}
        for split in splits:
            keep = labelled[split]
            probability = model.predict_proba(design_all[split][keep])[:, 1]
            truth = label[split][keep]
            metrics[f"pi_auc_{tag}"][split] = float(roc_auc_score(truth, probability))
            metrics[f"pi_brier_{tag}"][split] = float(brier_score_loss(truth, probability))
            metrics[f"pi_ece_{tag}"][split] = tau.calibration_report(
                probability, truth.to_numpy()
            )["expected_calibration_error"]

    metrics["n_rows"] = {name: int(len(df)) for name, df in splits.items()}
    metrics["n_weighted"] = {name: int(fit_rows[name].sum()) for name in splits}
    metrics["n_c_labelled"] = {name: int(labelled[name].sum()) for name in splits}
    metrics["ipcw"] = {name: restricted[name].summary() for name in splits}

    with open(paths.out("censoring.json")) as handle:
        censoring_rate = json.load(handle)

    with open(paths.out("models.pkl"), "wb") as handle:
        pickle.dump(
            {
                "encoder": encoder,
                "features": features,
                "features_noflow": features_noflow,
                "mu_ridge": ridge,
                "mu_gbm": gbm,
                "mu_gbm_noflow": gbm_noflow,
                "pi_gbm": clf,
                "pi_calibrated": calibrated,
                "smearing": smearing,
                "user_role": user_role,
                "censoring_rate": censoring_rate,
                "metrics": metrics,
            },
            handle,
        )

    with open(paths.out("fit_metrics.json"), "w") as handle:
        json.dump(metrics, handle, indent=2)
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
