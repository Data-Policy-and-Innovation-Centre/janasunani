"""Historical propensity e(flow | X) and the overlap diagnostics it needs.

The Aug 11 run used empirical `category x district` flow shares with a hard
clip to [0.01, 0.99], and reported no overlap diagnostic at all. That is kept
here as `EmpiricalSharePropensity` -- it is a defensible first pass -- with two
corrections:

* an unseen (cell, flow) pair returned a literal 0.01, i.e. the *clip floor*,
  which then multiplied the augmented residual by 100. It now returns the
  marginal share of that flow, falling back to the floor only when the flow is
  unseen everywhere.
* effective sample size is computed and returned, because a DR estimate whose
  correction term rests on a handful of matched rows is not evidence, and the
  as-run code had no way to notice.

The hierarchical form in the plan (`e(flow|X) = e(dept|X) * e(flow|dept,X)` by
penalized logistic regression per level) is not implemented. Nothing in this
package should describe it as if it were.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Propensities below this are unidentified from the data; clipping is an
#: assumption, and its sensitivity belongs in the spec curve.
CLIP_LOW = 0.01
CLIP_HIGH = 0.99


@dataclass(frozen=True)
class EmpiricalSharePropensity:
    """P(flow | cell) as the training share, with a marginal-share backoff."""

    by_cell: dict[str, dict[str, float]]
    marginal: dict[str, float]
    clip_low: float = CLIP_LOW
    clip_high: float = CLIP_HIGH

    @classmethod
    def fit(
        cls,
        df: pd.DataFrame,
        *,
        cell_col: str = "cell",
        action_col: str = "action_template",
    ) -> "EmpiricalSharePropensity":
        usable = df[df[action_col].notna()]
        counts = usable.groupby([cell_col, action_col]).size()
        totals = usable.groupby(cell_col).size()
        shares = (counts / totals).rename("share").reset_index()

        by_cell: dict[str, dict[str, float]] = {}
        for cell, flow, share in shares.itertuples(index=False):
            by_cell.setdefault(cell, {})[flow] = float(share)

        marginal = (usable[action_col].value_counts(normalize=True)).to_dict()
        return cls(by_cell=by_cell, marginal={k: float(v) for k, v in marginal.items()})

    def score(self, cells: pd.Series, flows: pd.Series) -> pd.Series:
        """Clipped e(flow | cell), aligned to `cells.index`."""

        def one(cell: object, flow: object) -> float:
            if flow is None or (isinstance(flow, float) and np.isnan(flow)):
                return self.clip_low
            in_cell = self.by_cell.get(cell)
            if in_cell is not None and flow in in_cell:
                return in_cell[flow]
            return self.marginal.get(flow, self.clip_low)

        raw = [one(c, f) for c, f in zip(cells, flows)]
        return pd.Series(raw, index=cells.index).clip(self.clip_low, self.clip_high)


def effective_sample_size(weights: np.ndarray) -> float:
    """Kish ESS = (sum w)^2 / sum w^2. Zero when no row carries weight."""
    weights = np.asarray(weights, dtype=float)
    denominator = np.sum(weights**2)
    if denominator <= 0:
        return 0.0
    return float(np.sum(weights) ** 2 / denominator)


def overlap_report(
    propensity: pd.Series, matched: pd.Series
) -> dict[str, float]:
    """Overlap summary for one policy arm.

    `matched` marks the rows where history happened to use the policy's flow --
    the only rows that contribute a residual correction.
    """
    weights = np.where(matched.to_numpy(dtype=bool), 1.0 / propensity.to_numpy(), 0.0)
    n = int(len(propensity))
    ess = effective_sample_size(weights)
    return {
        "n": n,
        "n_matched": int(matched.sum()),
        "match_rate": float(matched.mean()) if n else 0.0,
        "e_median": float(propensity.median()),
        "e_p10": float(propensity.quantile(0.10)),
        "e_p90": float(propensity.quantile(0.90)),
        "ess": ess,
        "ess_over_n": ess / n if n else 0.0,
        "max_weight_share": float(weights.max() / weights.sum()) if weights.sum() > 0 else 0.0,
    }
