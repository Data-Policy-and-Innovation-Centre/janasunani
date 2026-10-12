"""What is computed over a sample, independent of which note asks.

The second of the three layers described in ``janasunani.analytics.sample``.
Every function here takes a :class:`~janasunani.analytics.sample.Sample` and a
step key, never a department. Numbers and pictures are separate functions: a
numbers function returns a DataFrame (or plain values), and a ``draw_``
function takes that result and a :class:`~janasunani.analytics.figures.Report`.
A notebook, a docx script and a deck therefore quote the same figure from the
same query.

**Stages are a parameter, not a separate exhibit.** The five phase spans of
``journey.PHASES`` can be shown as they are (``STAGES_FIVE``) or folded
(``STAGES_THREE``); the means of folded spans still add to the total, because
the mean of a sum is the sum of the means. The add-up assert sits in the
numbers functions, once.

Only exhibits more than one note uses live here. A department-only exhibit
stays with its note until a second note needs it.
"""

from __future__ import annotations

import textwrap

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from IPython.display import display

from janasunani.analytics.figures import (
    BLUE, INK, MAROON, ORANGE, RAMP, barh, density, time_stats,
)
from janasunani.analytics.journey import (
    PHASE_LABEL, ROLE_CASE, STAGES_FIVE, _sql_str,
)

__all__ = [
    "outcomes", "draw_outcomes_bar", "draw_outcomes_pie", "durations",
    "stage_label", "stage_means", "stage_table", "stage_rows",
    "draw_stage_bar", "draw_stage_densities", "draw_stage_stack",
    "route_table", "top_route_groups",
]


# -- outcomes -----------------------------------------------------------------

def outcomes(s, step: str) -> pl.DataFrame:
    """How the claims in ``step`` ended: one row per outcome, largest first."""
    return s.q(f"""SELECT outcome, COUNT(*) AS n FROM {s.t('g')}
                   WHERE {s.WHERE[step]} GROUP BY 1 ORDER BY n DESC""")


def draw_outcomes_bar(rep, d: pl.DataFrame, title: str, subtitle: str,
                      save: str, figsize=(7.4, 2.7)) -> None:
    fig, ax = plt.subplots(figsize=figsize)
    barh(ax, list(d["outcome"]), list(d["n"]), shares=True)
    ax.set_xlabel("Claims")
    rep.finish(ax, title, subtitle, xgrid=True, ygrid=False, save=save)


def draw_outcomes_pie(rep, d: pl.DataFrame, title: str, subtitle: str,
                      save: str) -> None:
    """Disposed, discarded and still open, as shares of the step.

    Three slices, because the house palette clears its separation checks for
    three hues and fails at four. "Disposed with benefit" is part of disposed,
    so it rides in that slice's label rather than taking a fourth colour.
    """
    n = dict(zip(d["outcome"], d["n"]))
    benefit = n.get("Disposed with benefit", 0)
    parts = [("Disposed", n.get("Disposed", 0) + benefit, MAROON),
             ("Discarded", n.get("Discarded", 0), BLUE),
             ("Open", n.get("Open", 0), ORANGE)]
    parts = [p for p in parts if p[1]]
    total = sum(v for _, v, _ in parts)
    text = [f"{lab}\n{v:,} ({100 * v / total:.1f}%)" for lab, v, _ in parts]
    if benefit:
        text[0] += f"\nof which with benefit: {benefit:,}"
    # A label on a sliver collides with its neighbour's. The slice stays, so
    # the shares are still drawn to scale; its count moves under the chart.
    small = [i for i, (_, v, _) in enumerate(parts) if v / total < 0.03]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.pie([v for _, v, _ in parts],
           labels=["" if i in small else t for i, t in enumerate(text)],
           colors=[c for _, _, c in parts], startangle=90, counterclock=False,
           wedgeprops={"edgecolor": "white", "linewidth": 2},
           textprops={"color": INK, "fontsize": 9})
    ax.set_aspect("equal")
    note = "; ".join(text[i].replace("\n", " ") for i in small) or None
    rep.finish(ax, title, subtitle, note=note and f"Too small to label: {note}.",
               ygrid=False, save=save)


# -- stages -------------------------------------------------------------------

def stage_label(key: str) -> str:
    return PHASE_LABEL.get(key, key)


def _expr(cols) -> str:
    return cols[0] if len(cols) == 1 else "(" + " + ".join(cols) + ")"


def stage_means(s, stages=STAGES_FIVE) -> tuple[dict, float]:
    """The mean span of each stage over P4, and their total, asserted to agree.

    Returned rather than only drawn, so a written report quotes the same
    numbers as the chart.
    """
    kept = s.q(f"SELECT ROUND(AVG(days_to_close), 1) AS m "
               f"FROM {s.t('phases_clean')}")["m"][0]
    means = s.q(f"""SELECT {', '.join(f'AVG({_expr(c)}) AS "{k}"'
                                      for k, c in stages.items())}
                    FROM {s.t('phases_clean')}""").row(0, named=True)
    total = sum(float(means[k]) for k in stages)
    assert abs(total - float(kept)) <= 0.15, (
        f"phases sum to {total}, total is {kept}")
    return means, total


def durations(s) -> pl.DataFrame:
    """Count, median, mean and slowest tenth of days to close over P4."""
    return s.q(f"SELECT {time_stats()} FROM {s.t('phases_clean')}")


def stage_table(s, total: float, stages=STAGES_FIVE) -> pl.DataFrame:
    """Mean, share and median per stage over P4.

    Review is zero, not missing, for a claim never sent back up, so every row
    stands on all of P4.
    """
    ex = [_expr(c) for c in stages.values()]
    return s.q(f"""
      SELECT UNNEST([{', '.join(repr(stage_label(k)) for k in stages)}]) AS "Phase",
        UNNEST([{', '.join(f'ROUND(AVG({e}), 1)' for e in ex)}]) AS "Mean days",
        UNNEST([{', '.join(f'ROUND(100.0 * AVG({e}) / {total}, 1)' for e in ex)}])
          AS "% of total",
        UNNEST([{', '.join(f'CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {e}) AS BIGINT)' for e in ex)}])
          AS "Median days"
      FROM {s.t('phases_clean')}""")


def stage_rows(s, groups, stages=STAGES_FIVE, floor: int = 100):
    """Mean stage spans per group of P4, dropping groups below the floor.

    ``groups`` is ``[(label, predicate over phases_pop)]``. Returns
    ``[(label, row)]`` where ``row`` has ``n``, ``total`` and one key per
    stage. The assert is the point: a group whose spans do not add to its own
    mean has been mis-tiled, and a stacked bar would hide that by drawing
    whatever it was given.
    """
    rows = []
    for lab, pred in groups:
        r = s.q(f"""SELECT COUNT(*) AS n, ROUND(AVG(days_to_close), 1) AS total,
                    {', '.join(f'ROUND(AVG({_expr(c)}), 1) AS "{k}"'
                               for k, c in stages.items())}
                  FROM {s.t('phases_pop')} WHERE {pred}""").row(0, named=True)
        if r["n"] < floor:
            continue
        parts = sum(float(r[k]) for k in stages)
        assert abs(parts - float(r["total"])) <= 0.15, (
            f"{lab}: phases sum to {parts}, total is {r['total']}")
        rows.append((lab, r))
    return rows


def draw_stage_bar(rep, means: dict, total: float, title: str, subtitle: str,
                   save: str, stages=STAGES_FIVE) -> None:
    """One bar, the mean total, split into its stages and labelled in place."""
    fig, ax = plt.subplots(figsize=(8.4, 2.3))
    left = 0.0
    for i, k in enumerate(stages):
        v = float(means[k])
        ax.barh([0], [v], left=left, color=RAMP[i % len(RAMP)], height=0.5,
                zorder=3)
        if v > total * 0.04:
            ax.text(left + v / 2, 0,
                    f"{stage_label(k)}\n{v:.0f}d ({100 * v / total:.0f}%)",
                    ha="center", va="center", fontsize=8.5,
                    color="white" if i < 3 else INK)
        left += v
    ax.set_yticks([])
    ax.set_xlabel("Mean days")
    ax.grid(False)
    rep.finish(ax, title, subtitle, ygrid=False, save=save)


def draw_stage_densities(rep, s, title: str, subtitle: str, save: str,
                         stages=STAGES_FIVE) -> None:
    """The shape of each stage over P4, one small panel each."""
    fig, axes = plt.subplots(1, len(stages), figsize=(2.6 * len(stages), 2.6))
    for ax, (k, cols) in zip(np.atleast_1d(axes), stages.items()):
        vals = s.q(f"SELECT {_expr(cols)} AS v "
                   f"FROM {s.t('phases_clean')}")["v"].to_list()
        arr = np.asarray([float(v) for v in vals if v is not None])
        if arr.size and float(np.percentile(arr, 95)) < 1:
            # A smoothed curve over a 0-1 axis is an artefact of the axis.
            ax.text(0.5, 0.55, f"{100 * float((arr == 0).mean()):.0f}%\nsame day",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=11, color=MAROON, fontweight="bold")
            ax.set_xticks([])
        else:
            density(ax, vals, colour=MAROON)
        ax.set_title(stage_label(k), fontsize=9, loc="left", color=INK, pad=3)
        ax.set_yticks([])
        ax.grid(False)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(labelsize=7.5)
        ax.set_xlabel("days", fontsize=8)
    rep.finish_panels(fig, title, subtitle, save=save)


def draw_stage_stack(rep, rows, label_col: str, title: str, subtitle: str,
                     save: str, stages=STAGES_FIVE, share_of=None) -> None:
    """A table and one stacked bar per group, each adding to its own mean.

    ``share_of`` adds a "% of sample" column against that denominator, for
    when the groups are a selection (the top routes) rather than a partition.
    """
    table = {
        label_col: [lab for lab, _ in rows],
        "Claims": [r["n"] for _, r in rows],
    }
    if share_of:
        table["% of sample"] = [round(100 * r["n"] / share_of, 1) for _, r in rows]
    table.update({stage_label(k): [float(r[k]) for _, r in rows] for k in stages})
    table["Total"] = [float(r["total"]) for _, r in rows]
    display(pl.DataFrame(table))

    # A route name is a sentence, and unwrapped it takes half the canvas
    # before a single bar is drawn. Wrapped, the label column is bounded
    # and the axis gets the room.
    labels = ["\n".join(textwrap.wrap(lab, 34)) + f"\n({r['n']:,})"
              for lab, r in rows]
    deep = max(lab.count("\n") for lab in labels)
    height = 0.42 * len(rows) * (1 + 0.42 * deep) + 1.9
    fig, ax = plt.subplots(figsize=(10.6, height))
    for j, (lab, r) in enumerate(rows):
        left = 0.0
        for i, k in enumerate(stages):
            v = float(r[k])
            ax.barh([j], [v], left=left, color=RAMP[i % len(RAMP)],
                    height=0.6, zorder=3, label=stage_label(k) if j == 0 else None)
            if v > float(r["total"]) * 0.07:
                ax.text(left + v / 2, j, f"{v:.0f}", ha="center",
                        va="center", fontsize=8,
                        color="white" if i < 3 else INK)
            left += v
        ax.text(left + 1.5, j, f"{left:.0f}d", va="center", fontsize=8.5,
                color=INK)
    ax.set_yticks(range(len(rows)), labels)
    ax.invert_yaxis()
    ax.set_xlabel("Mean days")
    ax.grid(False)
    # Below the axis, not inside it: an inside legend lands on the longest
    # bar, which is the one the reader most wants to read.
    # The offset is in axes fractions, so a fixed one lands a hand's width
    # below a short chart and on the axis labels of a tall one.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -1.05 / height),
              ncol=len(stages), fontsize=8)
    rep.finish(ax, title, subtitle, ygrid=False, save=save)


# -- routes -------------------------------------------------------------------

def route_table(s, step: str, flag: str = "TRUE", limit: int = 8,
                pct_label: str = "% of claims") -> pl.DataFrame:
    """The commonest routes among the claims in ``step`` that have one (P3).

    A route is the sequence of roles that held the claim, never a place.
    """
    return s.q(f"""
      SELECT LIST_REDUCE(LIST_TRANSFORM(r.roles, x -> {ROLE_CASE}),
                         (a, b) -> a || ' > ' || b) AS "Route",
        COUNT(*) AS "Claims",
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS "{pct_label}",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY g.days_to_close)
             AS BIGINT) AS "Median days",
        ROUND(AVG(g.days_to_close), 1) AS "Mean days"
      FROM {s.t('route')} r JOIN {s.t('g')} g USING (ticket_no)
      WHERE {s.WHERE[step]} AND {flag}
      -- Route breaks the tie, so a table with several equal-sized routes
      -- at its cut-off is the same table on the next run.
      GROUP BY 1 ORDER BY "Claims" DESC, "Route" LIMIT {limit}""")


def top_route_groups(s, top: int = 6, floor: int = 100) -> list[tuple[str, str]]:
    """The ``top`` commonest routes in P4, as groups for :func:`stage_rows`."""
    common = s.q(f"""SELECT route, COUNT(*) AS n FROM {s.t('phases_pop')}
                     WHERE route IS NOT NULL
                     GROUP BY 1 HAVING COUNT(*) >= {floor}
                     ORDER BY n DESC, route LIMIT {top}""")
    return [(r, f"route = {_sql_str(r)}") for r in common["route"]]
