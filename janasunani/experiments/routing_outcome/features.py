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

`X` still contains a representation of the treatment. The action is the joint
department-and-complete-chain assignment, not the chain alone. `mu_a(x)` is
therefore evaluated with the candidate department, entry role, last role and
chain length all changed together. They are tagged `ACTION_COLUMNS` so the
ablation can drop them and, more importantly, so `transform(..., action=...)`
can re-score the same row under a different joint action.

`pending_with_id` is deliberately absent. It is the holder after routing has
unfolded, so retaining the observed holder while changing the assigned action
would feed a post-treatment cross-world value into every counterfactual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

#: Categorical covariates. Officer-observable at assignment time.
CATEGORICAL_COLUMNS: tuple[str, ...] = ("district", "category", "block", "mode", "office")

#: Binary covariates, stored as "Yes"/"No" strings in the lake.
#:
#: ``transfer_status`` is deliberately absent.  The lake audit shows that it
#: is a transient complaint state: every ``Yes`` row is currently
#: ``Not Assigned`` and carries no assigned chain, while complaints with an
#: earlier transfer revert to ``No`` after assignment.  It is therefore not an
#: assignment-time case characteristic or an "ever transferred" history flag.
BINARY_COLUMNS: tuple[str, ...] = ("self_assign",)

#: Treatment-derived columns: the joint assignment, not the case. Dropped by
#: the ablation and overridden together for a counterfactual action.
ACTION_COLUMNS: tuple[str, ...] = (
    "department_code",
    "entry_role_code",
    "last_role_code",
    "n_esc",
)

ACTION_SEPARATOR = "::"
ACTION_DEFINITION = "department_id::complete_role_chain/v1"


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
        for col in (*CATEGORICAL_COLUMNS, "department_id", "entry_role", "last_role"):
            levels[col] = pd.Index(sorted(_as_str(df[col]).dropna().unique()))
        encoder = cls(levels=levels)
        # Column order is derived from a transform so that it can never drift
        # from what `transform` actually emits.
        return cls(levels=levels, columns=tuple(encoder.transform(df.head(1)).columns))

    def transform(
        self,
        df: pd.DataFrame,
        *,
        action: str | pd.Series | None = None,
        include_action: bool = True,
    ) -> pd.DataFrame:
        """Design matrix for `df`.

        Args:
            action: counterfactual ``department_id::role,sequence`` identifier,
                either one action applied to every row or a per-row Series.
                Department and chain columns are overridden together.
            include_action: drop `ACTION_COLUMNS` entirely (the no-action ablation).
        """
        X = pd.DataFrame(index=df.index)

        for col in CATEGORICAL_COLUMNS:
            X[f"{col}_code"] = _codes(df[col], self.levels[col])
        for col in BINARY_COLUMNS:
            X[col] = (df[col] == "Yes").astype(int)

        department, entry, last, n_esc = self._action_columns(df, action)
        X["department_code"] = _codes(department, self.levels["department_id"])
        X["entry_role_code"] = _codes(entry, self.levels["entry_role"])
        X["last_role_code"] = _codes(last, self.levels["last_role"])
        X["n_esc"] = n_esc

        created = pd.to_datetime(df["created_on"], errors="coerce")
        X["month"] = created.dt.month.fillna(6).astype(int)
        X["quarter"] = ((X["month"] - 1) // 3) + 1

        if not include_action:
            X = X.drop(columns=list(ACTION_COLUMNS), errors="ignore")
        if self.columns:
            keep = [c for c in self.columns if c in X.columns]
            X = X[keep]
        return X

    @staticmethod
    def _action_columns(
        df: pd.DataFrame, action: str | pd.Series | None
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """Observed joint-action columns, or those implied by `action`."""
        if action is None:
            return (
                _as_str(df["department_id"]),
                df["entry_role"].astype("object"),
                df["last_role"].astype("object"),
                pd.to_numeric(df["n_esc"], errors="coerce").fillna(2.0),
            )

        actions = (
            pd.Series(action, index=df.index)
            if isinstance(action, str)
            else action.reindex(df.index)
        )
        decoded = actions.map(decode_action)
        department = decoded.map(lambda value: value[0] if value else None)
        templates = decoded.map(lambda value: value[1] if value else None)
        parts = templates.fillna("").astype(str).str.split(",")
        entry = parts.map(lambda p: p[0] if p and p[0] else None)
        last = parts.map(lambda p: p[-1] if p and p[-1] else None)
        n_esc = parts.map(lambda p: float(len(p)) if p and p[0] else np.nan).fillna(2.0)
        return department, entry, last, n_esc

    def feature_names(self, *, include_action: bool = True) -> list[str]:
        names = list(self.columns)
        if not include_action:
            names = [c for c in names if c not in ACTION_COLUMNS]
        return names


def encode_action(department_id: object, chain_template: object) -> str | None:
    """Stable identifier for the jointly assigned department and complete chain."""
    if pd.isna(department_id) or pd.isna(chain_template):
        return None
    department = str(department_id).strip()
    chain = str(chain_template).strip()
    if not department or not chain or ACTION_SEPARATOR in department:
        return None
    return f"{department}{ACTION_SEPARATOR}{chain}"


def decode_action(action: object) -> tuple[str, str] | None:
    """Inverse of :func:`encode_action`; malformed identifiers are unsupported."""
    if action is None or (isinstance(action, float) and np.isnan(action)):
        return None
    parts = str(action).split(ACTION_SEPARATOR, maxsplit=1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def decode_flow_columns(df: pd.DataFrame, user_role: Mapping[str, str]) -> pd.DataFrame:
    """Attach chain columns and the joint department-chain action identifier.

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
    df["chain_template"] = [p[2] for p in parsed]
    department = _as_str(df["dept_id"])
    df["department_id"] = department
    df["action_template"] = [
        encode_action(dept, chain)
        for dept, chain in zip(department, df["chain_template"])
    ]
    return df


def cell_key(df: pd.DataFrame, columns: Iterable[str] = ("category", "district")) -> pd.Series:
    """The `category|district` stratum used for eligibility and propensities."""
    parts = [df[c].fillna("MISSING").astype(str) for c in columns]
    key = parts[0]
    for part in parts[1:]:
        key = key + "|" + part
    return key
