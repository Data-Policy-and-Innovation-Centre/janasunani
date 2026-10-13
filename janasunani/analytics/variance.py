"""Which layer explains a duration: fixed effects, scored out of sample.

Used where a note asks "is it the office, the district or the kind of
complaint?" and must answer in shares a reader can rank, not in coefficients.

Each layer is a categorical column. A model is ordinary least squares on the
one-hot columns of a set of layers (fixed effects), solved sparse with
``lsqr``, so thousands of offices cost nothing. Every R² is **out of sample**:
fit on a random half of the rows, scored on the other half. A layer with
thousands of small levels can fit noise in sample; it cannot predict the
other half with it.

The layers overlap (an office sits in a district), so no single ordering is
fair. :func:`shapley_shares` averages each layer's added R² over every order,
which splits the shared part evenly and sums exactly to the full model's R².
With k layers that is 2**k fits; at 300k rows and eight layers, a few minutes.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import polars as pl
import scipy.sparse as sp
from scipy.sparse.linalg import lsqr

__all__ = ["oos_r2", "shapley_shares"]


def _onehots(df: pl.DataFrame, cols, train: np.ndarray) -> dict:
    """Sparse one-hot per column, keeping only levels seen in training.

    A level absent from the training half gets no coefficient, so its rows
    are scored at the intercept rather than at an effect nobody estimated.
    """
    n, out = df.height, {}
    for c in cols:
        codes = (df[c].cast(pl.Utf8).fill_null("(missing)").cast(pl.Categorical)
                 .to_physical().to_numpy())
        seen = np.zeros(int(codes.max()) + 1, bool)
        seen[codes[train]] = True
        m = sp.csr_matrix((np.ones(n), (np.arange(n), codes)),
                          shape=(n, seen.size))
        out[c] = m[:, seen]
    return out


def _fit_score(y, X: dict, cols, train) -> float:
    if not cols:
        return 0.0
    A = sp.hstack([X[c] for c in cols]).tocsr()
    mu = y[train].mean()
    # A small damping keeps the solve stable where layers are collinear
    # (an office determines its district); it does not move a well-fed level.
    b = lsqr(A[train], y[train] - mu, damp=1e-3, atol=1e-6, btol=1e-6,
             iter_lim=1000)[0]
    test = y[~train]
    resid = test - (mu + A[~train] @ b)
    return float(1 - (resid ** 2).sum() / ((test - test.mean()) ** 2).sum())


def oos_r2(df: pl.DataFrame, y: str, cols, seed: int = 0) -> float:
    """Out-of-sample R² of the fixed-effects model on ``cols``."""
    train = np.random.default_rng(seed).random(df.height) < 0.5
    return _fit_score(df[y].to_numpy().astype(float),
                      _onehots(df, cols, train), list(cols), train)


def shapley_shares(df: pl.DataFrame, y: str, layers: dict, seed: int = 0) -> tuple[pl.DataFrame, float]:
    """Each layer's Shapley share of the full model's out-of-sample R².

    ``layers`` maps a reader's label to a column. Layers with a single level
    in ``df`` (the department, inside one subcategory) explain nothing by
    construction and are dropped before fitting. Returns the table, largest
    share first, and the full model's R².
    """
    layers = {lab: c for lab, c in layers.items() if df[c].n_unique() > 1}
    cols = list(layers.values())
    train = np.random.default_rng(seed).random(df.height) < 0.5
    yv = df[y].to_numpy().astype(float)
    X = _onehots(df, cols, train)
    r2 = {frozenset(s): _fit_score(yv, X, list(s), train)
          for r in range(len(cols) + 1) for s in itertools.combinations(cols, r)}
    full, k = r2[frozenset(cols)], len(cols)
    rows = []
    for lab, c in layers.items():
        rest = [x for x in cols if x != c]
        v = sum(math.factorial(r) * math.factorial(k - r - 1) / math.factorial(k)
                * (r2[frozenset(s) | {c}] - r2[frozenset(s)])
                for r in range(k) for s in itertools.combinations(rest, r))
        rows.append((lab, v, r2[frozenset([c])]))
    out = pl.DataFrame(rows, schema=["Layer", "shapley_r2", "alone_r2"], orient="row")
    out = out.with_columns(
        (100 * pl.col("shapley_r2") / full).round(1).alias("% of what the record explains"))
    return out.sort("shapley_r2", descending=True), full
