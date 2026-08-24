"""Point-in-time feature builder: one encoder, fitted on train, reused everywhere.

The 11 Aug run built features with a bare `pd.Categorical(df[col]).codes` inside
three separate copies of `build_X` (train.py, ope.py, val_ope.py). Those codes
are positions in the *per-dataframe* level index, so `district_code=7` meant a
different district in train, val and test. Every model score computed off a
split other than the one it was fitted on was therefore reading permuted
categories, and every number downstream of that inherited it. `FeatureEncoder`
fixes the levels once, on train, and applies them everywhere.

Two other columns are gone rather than fixed:

* `govt_ticket = benefitted.notna()` leaked the label. `correct` is defined
  partly as ``benefitted LIKE '%yes%'`` (see `dataset.py`), so a null-indicator
  on `benefitted` is a direct function of the target the correctness classifier
  is asked to predict.
* `year` is collinear with the chronological split by construction: train only
  ever sees 2021--23, so a tree fitted on it sends every 2024 and 2025 row to a
  boundary leaf. Month and quarter carry the within-year seasonality that was
  actually wanted.

`X` still contains the treatment. `mu_a(x)` is a function of the flow, so
`entry_role`, `last_role` and `n_esc` must be in the design matrix; they are
tagged `FLOW_COLUMNS` so that the ablation can drop them and, more importantly,
so that `transform(..., flow=...)` can *re-score the same row under a different
flow*. That counterfactual is the whole point of the exercise and the as-run
code could not compute it (see the abandoned `best_mu_for_row` stub in the
original `ope.py`), which is why its "policy" fell back to a raw training cell
mean that ignores `x` entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

#: Categorical covariates. Officer-observable at assignment time.
CATEGORICAL_COLUMNS: tuple[str, ...] = ("district", "category", "block", "mode", "office")

#: Binary covariates, stored as "Yes"/"No" strings in the lake.
BINARY_COLUMNS: tuple[str, ...] = ("transfer_status", "self_assign")

#: Treatment-derived columns: the flow, not the case. Dropped by the ablation
#: and overridden when scoring a counterfactual flow.
#:
#: `pending_with_id` is the holder at closure, so it is post-treatment as well
#: as high-cardinality; it is grouped here rather than with the covariates.
FLOW_COLUMNS: tuple[str, ...] = (
    "entry_role_code",
    "last_role_code",
    "n_esc",
    "pending_code",
)


def _as_str(values: pd.Series) -> pd.Series:
    """Normalise to nullable strings, preserving nulls as <NA>.

    Both `fit` and `transform` must go through this. Stringifying on one side
    only silently kills the column: `pending_with_id` is int64, so levels fitted
    as "31" never matched a transform that compared the raw int, and the code
    came out -1 for every row in every split.
    """
    return values.astype("string")


def _codes(values: pd.Series, levels: pd.Index) -> np.ndarray:
    """Integer codes against *fixed* levels. Unseen and null both map to -1."""
    return pd.Categorical(_as_str(values), categories=levels).codes


@dataclass(frozen=True)
class FeatureEncoder:
    """Fitted category levels plus the column order the models expect.

    Fit on the training split only, then `transform` every other split with it.
    """

    levels: Mapping[str, pd.Index]
    columns: tuple[str, ...] = field(default=())

    @classmethod
    def fit(cls, df: pd.DataFrame) -> "FeatureEncoder":
        levels: dict[str, pd.Index] = {}
        for col in (*CATEGORICAL_COLUMNS, "entry_role", "last_role", "pending_with_id"):
            levels[col] = pd.Index(sorted(_as_str(df[col]).dropna().unique()))
        encoder = cls(levels=levels)
        # Column order is derived from a transform so that it can never drift
        # from what `transform` actually emits.
        return cls(levels=levels, columns=tuple(encoder.transform(df.head(1)).columns))

    def transform(
        self,
        df: pd.DataFrame,
        *,
        flow: str | pd.Series | None = None,
        include_flow: bool = True,
    ) -> pd.DataFrame:
        """Design matrix for `df`.

        Args:
            flow: counterfactual flow template (comma-separated roleIds), either
                one template applied to every row or a per-row Series. When set,
                the flow columns describe that template instead of the observed
                chain, which is what makes `mu_a(x)` evaluable off-policy.
            include_flow: drop `FLOW_COLUMNS` entirely (the no-flow ablation).
        """
        X = pd.DataFrame(index=df.index)

        for col in CATEGORICAL_COLUMNS:
            X[f"{col}_code"] = _codes(df[col], self.levels[col])
        for col in BINARY_COLUMNS:
            X[col] = (df[col] == "Yes").astype(int)

        entry, last, n_esc, pending = self._flow_columns(df, flow)
        X["entry_role_code"] = _codes(entry, self.levels["entry_role"])
        X["last_role_code"] = _codes(last, self.levels["last_role"])
        X["n_esc"] = n_esc
        X["pending_code"] = _codes(pending, self.levels["pending_with_id"])

        created = pd.to_datetime(df["created_on"], errors="coerce")
        X["month"] = created.dt.month.fillna(6).astype(int)
        X["quarter"] = ((X["month"] - 1) // 3) + 1

        if not include_flow:
            X = X.drop(columns=list(FLOW_COLUMNS), errors="ignore")
        if self.columns:
            keep = [c for c in self.columns if c in X.columns]
            X = X[keep]
        return X

    @staticmethod
    def _flow_columns(
        df: pd.DataFrame, flow: str | pd.Series | None
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """Observed flow columns, or the ones implied by a counterfactual `flow`."""
        if flow is None:
            return (
                df["entry_role"].astype("object"),
                df["last_role"].astype("object"),
                pd.to_numeric(df["n_esc"], errors="coerce").fillna(2.0),
                df["pending_with_id"].astype("object"),
            )

        templates = (
            pd.Series(flow, index=df.index) if isinstance(flow, str) else flow.reindex(df.index)
        )
        parts = templates.fillna("").astype(str).str.split(",")
        entry = parts.map(lambda p: p[0] if p and p[0] else None)
        last = parts.map(lambda p: p[-1] if p and p[-1] else None)
        n_esc = parts.map(lambda p: float(len(p)) if p and p[0] else np.nan).fillna(2.0)
        # The counterfactual says nothing about who ends up holding the file, so
        # the observed holder is kept rather than invented.
        return entry, last, n_esc, df["pending_with_id"].astype("object")

    def feature_names(self, *, include_flow: bool = True) -> list[str]:
        names = list(self.columns)
        if not include_flow:
            names = [c for c in names if c not in FLOW_COLUMNS]
        return names


def decode_flow_columns(df: pd.DataFrame, user_role: Mapping[str, str]) -> pd.DataFrame:
    """Attach `entry_role`, `last_role`, `flow_template` decoded from the chain.

    Mutates and returns `df`. A chain with any token missing from
    `t_user_role_details` yields nulls, which `_codes` maps to -1.
    """

    def parse(chain: object) -> tuple[str | None, str | None, str | None]:
        if chain is None or (isinstance(chain, float) and np.isnan(chain)) or chain == "":
            return (None, None, None)
        tokens = [t.strip() for t in str(chain).split(",") if t.strip()]
        roles = [user_role.get(t) for t in tokens]
        if not roles or any(r is None for r in roles):
            return (None, None, None)
        return (roles[0], roles[-1], ",".join(roles))

    parsed = df["all_esc_user"].map(parse)
    df["entry_role"] = [p[0] for p in parsed]
    df["last_role"] = [p[1] for p in parsed]
    df["flow_template"] = [p[2] for p in parsed]
    return df


def cell_key(df: pd.DataFrame, columns: Iterable[str] = ("category", "district")) -> pd.Series:
    """The `category|district` stratum used for eligibility and propensities."""
    parts = [df[c].fillna("MISSING").astype(str) for c in columns]
    key = parts[0]
    for part in parts[1:]:
        key = key + "|" + part
    return key
