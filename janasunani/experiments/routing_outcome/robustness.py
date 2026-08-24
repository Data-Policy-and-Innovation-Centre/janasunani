"""Robustness ladder for the flow ablation.

The 11 Aug fit reported that dropping the flow features cost 0.038 RMSE on
validation ("real, modest", §7.2). The corrected fit reports 0.0003. Three
things changed at once between them -- the population, the target and the
weights -- so the movement is not attributable without decomposing it.

THE LADDER
----------
Each rung changes exactly one thing, and each is two fits (with and without the
flow columns) reporting `delta = RMSE_noflow - RMSE_flow`.

    R0  correct==1   log1p(days_capped), completers   unweighted   <- 11 Aug
    R1  S_tilde==1   log1p(days_capped), completers   unweighted
    R2  S_tilde==1   log1p(min(T,365))                unweighted
    R3  S_tilde==1   log1p(min(T,365))                IPCW         <- current

THERE IS NO SEED NOISE TO MEASURE
----------------------------------
The obvious noise check -- refit over several `random_state` values -- measures
nothing here. `GBM_PARAMS` sets neither `subsample` nor `max_features`, so
`GradientBoostingRegressor` is deterministic and the seed is inert; the
`random_state=1` on the no-flow arm in `train.py` is decorative. Verified by
refitting across seeds and comparing predictions exactly.

So the uncertainty on `delta` has to come from resampling the data, and there
are two distinct sources. **Evaluation noise** is how much `delta` moves when
the validation set is resampled, holding the fitted models fixed; it is cheap,
needs no refit, and answers "is this difference visible above sampling error in
the evaluation set". **Fit noise** is how much `delta` moves when the models are
refitted on resampled training data; it is the larger and more honest one, and
costs a refit per draw. Both resample whole district-year clusters, for the
reason §5.7 clusters the variance there.

A `delta` inside either band is not evidence of anything, in either direction.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from . import censoring, paths
from .features import ACTION_DEFINITION, FeatureEncoder, decode_flow_columns
from .flow import load_tables
from .models import boosted_duration_model, ridge_duration_model
from .train import _cluster, _weight_kwarg

#: Cluster-bootstrap replicates for the evaluation band.
N_EVAL_BOOT = 200

#: Refits per arm for the fit band. Each is a full GBM on ~300k rows, so this
#: trades precision for wall time; 8 gives a usable SD without an hour of fits.
N_FIT_DRAWS = 8

SEED = 20260813

SCHEMA_VERSION = "routing-outcome-robustness-v1"

LADDER: dict[str, dict] = {
    "R0_binary_completers": {
        "population": "correct", "target": "completers", "weights": None,
        "note": "reproduces the 11 Aug configuration",
    },
    "R1_actionable_completers": {
        "population": "S1", "target": "completers", "weights": None,
        "note": "population only: binary correct -> three-state actionable",
    },
    "R2_actionable_restricted": {
        "population": "S1", "target": "restricted", "weights": None,
        "note": "adds the restricted target, still unweighted",
    },
    "R3_actionable_restricted_ipcw": {
        "population": "S1", "target": "restricted", "weights": "ipcw",
        "note": "adds censoring weights; the current configuration",
    },
}


@dataclass
class AblationResult:
    name: str
    rmse_flow: float
    rmse_noflow: float
    delta: float
    delta_eval_se: float
    n_train: int
    n_val: int
    dropped_covariates: tuple[str, ...] = ()
    model: str = "gbm"
    note: str = ""
    delta_fit_se: float | None = None
    delta_fit_draws: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {k: v for k, v in self.__dict__.items()}
        out["delta_over_eval_se"] = (
            self.delta / self.delta_eval_se if self.delta_eval_se else float("nan")
        )
        if self.delta_fit_se:
            out["delta_over_fit_se"] = self.delta / self.delta_fit_se
        return out


def _load_splits() -> dict[str, pd.DataFrame]:
    user_role = load_tables().user_role
    splits = {}
    for name in ("train", "val"):
        df = pd.read_parquet(paths.out(f"{name}_all.parquet"))
        decode_flow_columns(df, user_role)
        df["cluster"] = _cluster(df)
        splits[name] = df
    return splits


def _population(df: pd.DataFrame, population: str) -> pd.DataFrame:
    if population == "correct":
        return df[(df["correct"] == 1) & (df["event"] == 1)]
    if population == "S1":
        return df[df["S"] == 1]
    if population == "S1_C1":
        # Actionable *and* acted upon. `C` is post-treatment, so this
        # deliberately reintroduces the principal-stratum conditioning that the
        # three-state design exists to avoid -- see `R8` for why.
        return df[(df["S"] == 1) & (df["C"] == 1)]
    raise ValueError(f"unknown population {population!r}")


def _target_and_weight(
    df: pd.DataFrame, cohort: pd.DataFrame, target: str, weights: str | None
) -> tuple[pd.Series, np.ndarray]:
    if target == "completers":
        rows = df["event"] == 1
        y = np.log1p(df["days_capped"].clip(0, censoring.HORIZON))
        w = rows.to_numpy(dtype=float)
    elif target == "restricted":
        restricted = censoring.restricted_outcome(
            df, fit_frame=cohort, stratum_col="cluster"
        )
        y = np.log1p(restricted.y.clip(0, censoring.HORIZON))
        w = (
            restricted.weight.to_numpy()
            if weights == "ipcw"
            else (restricted.weight.to_numpy() > 0).astype(float)
        )
    else:
        raise ValueError(f"unknown target {target!r}")
    return y, w


def _rmse(y, yhat, w=None) -> float:
    error = (np.asarray(y, dtype=float) - np.asarray(yhat, dtype=float)) ** 2
    if w is None:
        return float(np.sqrt(error.mean()))
    return float(np.sqrt(np.average(error, weights=w)))


def _build_model(model: str, seed: int, features: list[str]):
    if model == "gbm":
        return boosted_duration_model(features, random_state=seed)
    if model == "ridge":
        return ridge_duration_model(features)
    raise ValueError(f"unknown model {model!r}")


def _eval_bootstrap_se(
    y: np.ndarray,
    pred_flow: np.ndarray,
    pred_noflow: np.ndarray,
    clusters: np.ndarray,
    *,
    n_boot: int = N_EVAL_BOOT,
    seed: int = SEED,
) -> float:
    """SD of `delta` when whole validation clusters are resampled, models fixed."""
    rng = np.random.default_rng(seed)
    unique, inverse = np.unique(clusters, return_inverse=True)
    by_cluster = [np.flatnonzero(inverse == i) for i in range(len(unique))]
    if len(by_cluster) < 2:
        return float("nan")
    se_flow = (y - pred_flow) ** 2
    se_noflow = (y - pred_noflow) ** 2
    deltas = np.empty(n_boot)
    for b in range(n_boot):
        drawn = rng.integers(0, len(by_cluster), size=len(by_cluster))
        idx = np.concatenate([by_cluster[d] for d in drawn])
        deltas[b] = np.sqrt(se_noflow[idx].mean()) - np.sqrt(se_flow[idx].mean())
    return float(deltas.std(ddof=1))


def run_ablation(
    splits: dict[str, pd.DataFrame],
    *,
    name: str,
    population: str,
    target: str,
    weights: str | None,
    drop_covariates: tuple[str, ...] = (),
    model: str = "gbm",
    note: str = "",
    fit_draws: int = 0,
) -> AblationResult:
    train = _population(splits["train"], population)
    val = _population(splits["val"], population)

    encoder = FeatureEncoder.fit(train)
    keep = [c for c in encoder.feature_names() if c not in drop_covariates]
    keep_noflow = [
        c for c in encoder.feature_names(include_action=False) if c not in drop_covariates
    ]

    y_train, w_train = _target_and_weight(train, splits["train"], target, weights)
    y_val, w_val = _target_and_weight(val, splits["val"], target, weights)
    fit_rows = w_train > 0
    val_rows = w_val > 0

    x_train = encoder.transform(train)[keep][fit_rows]
    x_train_nf = encoder.transform(train, include_action=False)[keep_noflow][fit_rows]
    x_val = encoder.transform(val)[keep][val_rows]
    x_val_nf = encoder.transform(val, include_action=False)[keep_noflow][val_rows]
    yt, wt = y_train[fit_rows], w_train[fit_rows]
    yv = y_val[val_rows].to_numpy()
    clusters = val["cluster"].to_numpy()[val_rows]

    flow_model = _build_model(model, 0, keep)
    flow_model.fit(x_train, yt, **_weight_kwarg(flow_model, wt))
    noflow_model = _build_model(model, 0, keep_noflow)
    noflow_model.fit(x_train_nf, yt, **_weight_kwarg(noflow_model, wt))

    pred_flow = flow_model.predict(x_val)
    pred_noflow = noflow_model.predict(x_val_nf)
    rmse_flow = _rmse(yv, pred_flow)
    rmse_noflow = _rmse(yv, pred_noflow)

    result = AblationResult(
        name=name,
        rmse_flow=rmse_flow,
        rmse_noflow=rmse_noflow,
        delta=rmse_noflow - rmse_flow,
        delta_eval_se=_eval_bootstrap_se(yv, pred_flow, pred_noflow, clusters),
        n_train=int(fit_rows.sum()),
        n_val=int(val_rows.sum()),
        dropped_covariates=drop_covariates,
        model=model,
        note=note,
    )

    if fit_draws:
        rng = np.random.default_rng(SEED)
        unique = np.unique(train["cluster"].to_numpy()[fit_rows])
        train_clusters = train["cluster"].to_numpy()[fit_rows]
        draws = []
        for _ in range(fit_draws):
            chosen = rng.choice(unique, size=len(unique), replace=True)
            idx = np.concatenate(
                [np.flatnonzero(train_clusters == c) for c in chosen]
            )
            a = _build_model(model, 0, keep)
            a.fit(x_train.iloc[idx], yt.iloc[idx], **_weight_kwarg(a, wt[idx]))
            b = _build_model(model, 0, keep_noflow)
            b.fit(x_train_nf.iloc[idx], yt.iloc[idx], **_weight_kwarg(b, wt[idx]))
            draws.append(_rmse(yv, b.predict(x_val_nf)) - _rmse(yv, a.predict(x_val)))
        result.delta_fit_draws = [float(d) for d in draws]
        result.delta_fit_se = float(np.std(draws, ddof=1))

    return result


def seed_is_inert(model: str = "gbm") -> dict:
    """Confirm `random_state` does not perturb the fit before reporting a band."""
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(2000, 6)))
    y = pd.Series(x[0] + rng.normal(size=2000))
    features = list(x.columns)
    fitted = [_build_model(model, s, features).fit(x, y) for s in (0, 1, 2)]
    predictions = [pipeline.predict(x.head(20)) for pipeline in fitted]
    identical = all(np.allclose(predictions[0], p) for p in predictions[1:])
    estimator = fitted[0][-1]
    return {
        "model": model,
        "identical_across_seeds": bool(identical),
        "subsample": getattr(estimator, "subsample", None),
        "max_features": getattr(estimator, "max_features", None),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--exercise",
        choices=("ladder", "mechanism", "all"),
        default="all",
        help="ladder = R0-R3; mechanism = drop-office, drop-pending, ridge",
    )
    parser.add_argument("--fit-draws", type=int, default=N_FIT_DRAWS)
    parser.add_argument(
        "--out",
        type=Path,
        help="Output JSON (default: ROUTING_OUTCOME_OUT/robustness.json)",
    )
    args = parser.parse_args(argv)

    # Exercise the cheap provenance diagnostic before the full-data fits. A
    # broken check must fail fast, not discard an otherwise completed ladder.
    seed_check = seed_is_inert()
    splits = _load_splits()
    results: list[AblationResult] = []

    if args.exercise in ("ladder", "all"):
        for name, config in LADDER.items():
            kwargs = dict(config)
            note = kwargs.pop("note", "")
            # The fit band is only informative on the endpoints of the ladder.
            draws = args.fit_draws if name in ("R0_binary_completers", "R3_actionable_restricted_ipcw") else 0
            results.append(
                run_ablation(splits, name=name, note=note, fit_draws=draws, **kwargs)
            )
            print(f"  {results[-1].name:<32} delta={results[-1].delta:+.4f}")

    if args.exercise in ("mechanism", "all"):
        base = dict(population="S1", target="restricted", weights="ipcw")

        # R8 is the decisive test of the collider explanation for R0 -> R1.
        # `C` is post-treatment, so conditioning on it inside the proxy-selected
        # population reintroduces the selection without changing anything else.
        # If selection is what made the flow look predictive under the binary
        # label, the ablation gap should reappear here; if it stays at zero,
        # the collider story is wrong and the R0 gap needs another explanation.
        results.append(
            run_ablation(
                splits,
                name="R8_actionable_and_acted",
                population="S1_C1",
                target="restricted",
                weights="ipcw",
                note="reintroduces post-treatment conditioning on C, nothing else",
                fit_draws=args.fit_draws,
            )
        )
        print(f"  {results[-1].name:<32} delta={results[-1].delta:+.4f}")

        for name, kwargs in (
            (
                "R5_drop_office",
                dict(drop_covariates=("office_code",),
                     note="tests whether office-flow collinearity explains the collapse"),
            ),
            ("R7_ridge", dict(model="ridge", note="the better out-of-sample model, previously unablated")),
        ):
            results.append(run_ablation(splits, name=name, **base, **kwargs))
            print(f"  {results[-1].name:<32} delta={results[-1].delta:+.4f}")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).date().isoformat(),
        "action_definition": ACTION_DEFINITION,
        "population_note": (
            "S denotes the closure-derived S_tilde proxy in this diagnostic; "
            "it is not intake-time latent actionability S_star"
        ),
        "seed_check": seed_check,
        "results": [r.as_dict() for r in results],
    }
    output = args.out if args.out is not None else paths.out("robustness.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
