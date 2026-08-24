"""Order-invariant nuisance-model pipelines for routing outcomes.

`FeatureEncoder` maps nominal values to train-fitted integer labels so the same
value has the same representation in every split. Those integers are labels,
not measurements: changing a department from code 2 to code 20 must not imply a
tenfold distance. This module converts the labels before fitting:

* ridge receives a sparse one-hot design, which gives every nominal level its
  own penalised coefficient;
* gradient boosting receives cross-fitted target encodings, which keep the
  dense design small without imposing an arbitrary ordering.

Both constructions are invariant to a permutation of the integer labels and
handle unseen validation levels without refitting the encoder.
"""

from __future__ import annotations

from collections.abc import Sequence

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler, TargetEncoder

GBM_PARAMS = {
    "n_estimators": 200,
    "max_depth": 6,
    "learning_rate": 0.1,
    "random_state": 0,
}
CLF_PARAMS = {"n_estimators": 150, "max_depth": 4, "random_state": 0}


def nominal_columns(features: Sequence[str]) -> list[str]:
    """Columns whose integer values are train-fitted category labels."""
    return [
        column
        for column in features
        if isinstance(column, str) and column.endswith("_code")
    ]


def numeric_columns(features: Sequence[str]) -> list[str]:
    """Measured or binary columns for which numeric geometry is meaningful."""
    nominal = set(nominal_columns(features))
    return [column for column in features if column not in nominal]


def _one_hot_design(features: Sequence[str]) -> ColumnTransformer:
    return ColumnTransformer(
        [
            (
                "nominal",
                OneHotEncoder(handle_unknown="ignore", sparse_output=True),
                nominal_columns(features),
            ),
            ("numeric", StandardScaler(with_mean=False), numeric_columns(features)),
        ],
        sparse_threshold=1.0,
    )


def _target_design(
    features: Sequence[str], *, target_type: str, random_state: int
) -> ColumnTransformer:
    return ColumnTransformer(
        [
            (
                "nominal",
                TargetEncoder(
                    target_type=target_type,
                    cv=5,
                    shuffle=True,
                    random_state=random_state,
                ),
                nominal_columns(features),
            ),
            ("numeric", "passthrough", numeric_columns(features)),
        ],
        sparse_threshold=0.0,
    )


def ridge_duration_model(features: Sequence[str]) -> Pipeline:
    """Sparse one-hot ridge; LSQR avoids dense normal-equation products."""
    return Pipeline(
        [
            ("design", _one_hot_design(features)),
            ("ridge", Ridge(alpha=1.0, solver="lsqr")),
        ]
    )


def boosted_duration_model(features: Sequence[str], *, random_state: int = 0) -> Pipeline:
    """Gradient boosting after cross-fitted regression target encoding."""
    return Pipeline(
        [
            (
                "design",
                _target_design(
                    features, target_type="continuous", random_state=random_state
                ),
            ),
            (
                "gradientboostingregressor",
                GradientBoostingRegressor(
                    **{**GBM_PARAMS, "random_state": random_state}
                ),
            ),
        ]
    )


def boosted_correctness_model(
    features: Sequence[str], *, random_state: int = 0
) -> Pipeline:
    """Gradient boosting after cross-fitted binary target encoding."""
    return Pipeline(
        [
            (
                "design",
                _target_design(features, target_type="binary", random_state=random_state),
            ),
            (
                "gradientboostingclassifier",
                GradientBoostingClassifier(
                    **{**CLF_PARAMS, "random_state": random_state}
                ),
            ),
        ]
    )
