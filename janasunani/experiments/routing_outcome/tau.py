"""Calibrating the correctness constraint, and the frontier it traces.

Corollary 4.6 and §6.2 of `docs/experiments/routing-outcome-model.tex`.

WHY THERE IS A CONSTRAINT AT ALL
---------------------------------
Closure is an action the officer controls directly: a grievance can be closed at
any moment by recording that it was disposed. An objective that rewards short
durations without qualification is therefore maximised by closing everything
immediately and doing nothing. This is not a hypothetical failure mode -- the
most common closing remark in the system claims no action, and under the binary
label "correct" disposals run 18 days *slower* at the median than "incorrect"
ones. An unconstrained speed objective would learn exactly the wrong policy.

So the program is: minimise expected restricted duration *subject to* the
correctness rate not falling below the historical one. Not *conditional on*
correctness, which conditions on a post-treatment variable and compares
different populations across flows (Example 2.9).

WHY tau IS CALIBRATED RATHER THAN CHOSEN
-----------------------------------------
`tau` is the floor on `p_a(x) = P(C = 1 | x, a)` below which a flow is
inadmissible for a case. Picking it by hand sets the speed-correctness trade-off
by fiat. Corollary 4.6 instead defines `tau*` as the *smallest* floor at which
the constraint holds: any larger value buys correctness the constraint did not
ask for and pays in speed.

The whole curve is reported, not just `tau*`. `tau -> (V_T, V_C)` is the
speed-correctness frontier, and it is the single most informative object this
analysis produces -- it says what a day of delay buys, which is the question an
administrator actually has.

CALIBRATION IS A PRECONDITION, NOT A REFINEMENT
------------------------------------------------
`p_hat` enters only through the threshold test `p_hat >= tau`, so what matters
is not global fit but calibration *near tau*. An uncalibrated classifier makes
`tau` a number about the classifier rather than about correctness, and the
frontier stops being interpretable. The gradient-boosted `pi` in `train.py` had
a train-validation AUC gap of 0.921 to 0.767 and no calibration step at all,
so `calibrate()` here is required before any sweep.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Default sweep. Dense at the bottom because the constraint usually binds
#: early, and a coarse grid there would overshoot `tau*` and overstate its cost.
DEFAULT_GRID: tuple[float, ...] = (
    0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30,
    0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80,
)


@dataclass(frozen=True)
class FrontierPoint:
    """One point on the speed-correctness frontier."""

    tau: float
    v_duration: float
    v_correct: float
    feasible: bool
    n_fallback: int
    mean_eligible: float

    def as_dict(self) -> dict:
        return {
            "tau": self.tau,
            "v_duration": self.v_duration,
            "v_correct": self.v_correct,
            "feasible": self.feasible,
            "n_fallback": self.n_fallback,
            "mean_eligible": self.mean_eligible,
        }


def calibrate(classifier, x_calibration, y_calibration):
    """Isotonic recalibration of `pi` on held-out rows.

    Isotonic rather than Platt: it assumes only monotonicity, and the
    miscalibration of a boosted classifier is not reliably sigmoid-shaped.
    Fitted on rows the classifier did not train on -- calibrating on the
    training set reproduces the overfitting it is meant to correct.

    `FrozenEstimator` is how an already-fitted classifier is wrapped from
    scikit-learn 1.6 onward; the older `cv="prefit"` spelling was removed in
    1.9 and now raises rather than warning. Without the freeze,
    `CalibratedClassifierCV` refits the classifier by cross-validation on the
    calibration rows, which is a different and much slower model.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator

    calibrated = CalibratedClassifierCV(FrozenEstimator(classifier), method="isotonic")
    calibrated.fit(x_calibration, y_calibration)
    return calibrated


def calibration_report(probability: np.ndarray, actual: np.ndarray, *, bins: int = 10) -> dict:
    """Reliability of `pi`, and the expected calibration error it implies."""
    probability = np.asarray(probability, dtype=float)
    actual = np.asarray(actual, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows = []
    error = 0.0
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        # The final bin closes on the right. Isotonic regression routinely
        # emits exactly 1.0, and a half-open top bin drops those rows from both
        # the bin counts and the error -- silently, and in the direction that
        # flatters the model, since a confident prediction is where
        # miscalibration costs most.
        upper = probability <= high if index == bins - 1 else probability < high
        mask = (probability >= low) & upper
        if not mask.any():
            continue
        predicted = float(probability[mask].mean())
        observed = float(actual[mask].mean())
        share = float(mask.mean())
        error += share * abs(predicted - observed)
        rows.append(
            {"low": float(low), "high": float(high), "n": int(mask.sum()),
             "predicted": predicted, "observed": observed}
        )
    return {"expected_calibration_error": error, "bins": rows}


def sweep(
    evaluate,
    *,
    historical_correct: float,
    grid: tuple[float, ...] = DEFAULT_GRID,
) -> list[FrontierPoint]:
    """Trace the frontier over `grid`.

    `evaluate(tau)` must return `(v_duration, v_correct, n_fallback,
    mean_eligible)` for the policy formed at that floor. It is injected rather
    than built here so this module needs neither a fitted model nor the lake,
    and so the sweep can be tested against a closed-form stub.
    """
    points: list[FrontierPoint] = []
    for tau in grid:
        v_duration, v_correct, n_fallback, mean_eligible = evaluate(tau)
        points.append(
            FrontierPoint(
                tau=float(tau),
                v_duration=float(v_duration),
                v_correct=float(v_correct),
                feasible=bool(v_correct >= historical_correct),
                n_fallback=int(n_fallback),
                mean_eligible=float(mean_eligible),
            )
        )
    return points


def smallest_feasible(points: list[FrontierPoint]) -> FrontierPoint | None:
    """`tau*`: the smallest floor meeting the correctness constraint.

    None when no point on the grid is feasible, which is a real answer and not
    an error -- it says the constraint cannot be met by thresholding `pi` alone,
    and the caller must report that rather than fall back to the largest `tau`
    and call it optimal.
    """
    feasible = [p for p in points if p.feasible]
    return min(feasible, key=lambda p: p.tau) if feasible else None


def frontier_frame(points: list[FrontierPoint]) -> pd.DataFrame:
    return pd.DataFrame([p.as_dict() for p in points])
