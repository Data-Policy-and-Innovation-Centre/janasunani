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
from matplotlib.colors import LinearSegmentedColormap

from janasunani.analytics.figures import (
    BLUE, INK, INK_SOFT, MAROON, ORANGE, RAMP, barh, density, time_stats,
)
from janasunani.analytics.journey import (
    PHASE_LABEL, PHASES, ROLE_CASE, RUNG_NAME, STAGES_FIVE, STAGES_THREE, _sql_str,
)
from janasunani.analytics.variance import shapley_shares

__all__ = [
    "outcomes", "draw_outcomes_bar", "draw_outcomes_pie", "durations",
    "stage_label", "stage_means", "stage_table", "stage_rows", "top_types", "stage_office_splits", "OFFICE_SHORT",
    "draw_stage_bar", "draw_stage_densities", "draw_stage_stack",
    "route_table", "top_route_groups", "route_stage_roles", "ENTRY_SHORT",
    "entry_routes", "entry_route_groups",
    "FIELD_LAYERS", "FIELD_LAYER_DEFS", "office_volume", "draw_office_volume", "field_layer_table", "field_frame", "field_levels", "field_roles", "draw_field_levels",
    "field_drivers", "draw_field_drivers", "office_means",
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


def _omitted(stages) -> list[str]:
    """Phases a stage grouping leaves out (closure, for STAGES_THREE)."""
    covered = {p for cols in stages.values() for p in cols}
    return [p for p in PHASES if p not in covered]


def stage_means(s, stages=STAGES_FIVE) -> tuple[dict, float]:
    """The mean span of each stage over P4, and their total, asserted to agree.

    The total is the sum of the stages shown. It equals the mean time to
    close once any phase the grouping leaves out is added back, and the
    assert checks exactly that. Returned rather than only drawn, so a written
    report quotes the same numbers as the chart.
    """
    left_out = _omitted(stages)
    rest = f"AVG({_expr(left_out)})" if left_out else "0"
    kept = s.q(f"SELECT ROUND(AVG(days_to_close), 1) AS m "
               f"FROM {s.t('phases_clean')}")["m"][0]
    means = s.q(f"""SELECT {', '.join(f'AVG({_expr(c)}) AS "{k}"'
                                      for k, c in stages.items())},
                    {rest} AS _rest
                    FROM {s.t('phases_clean')}""").row(0, named=True)
    total = sum(float(means[k]) for k in stages)
    assert abs(total + float(means.pop("_rest")) - float(kept)) <= 0.15, (
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
    left_out = _omitted(stages)
    rest = f"ROUND(AVG({_expr(left_out)}), 1)" if left_out else "0"
    rows = []
    for lab, pred in groups:
        r = s.q(f"""SELECT COUNT(*) AS n, ROUND(AVG(days_to_close), 1) AS total,
                    {', '.join(f'ROUND(AVG({_expr(c)}), 1) AS "{k}"'
                               for k, c in stages.items())}, {rest} AS _rest
                  FROM {s.t('phases_pop')} WHERE {pred}""").row(0, named=True)
        if r["n"] < floor:
            continue
        parts = sum(float(r[k]) for k in stages)
        assert abs(parts + float(r["_rest"]) - float(r["total"])) <= 0.15, (
            f"{lab}: phases sum to {parts}, total is {r['total']}")
        # A grouping that leaves a phase out is drawn to the sum it shows.
        if left_out:
            r["total"] = round(parts, 1)
        del r["_rest"]
        rows.append((lab, r))
    return rows


def draw_stage_bar(rep, means: dict, total: float, title: str, subtitle: str,
                   save: str, stages=STAGES_FIVE) -> None:
    """One bar, the mean total, split into its stages and labelled in place."""
    _one_stack(rep, [(stage_label(k), float(means[k])) for k in stages], total,
               title, subtitle, save)


def _stage_shade(i: int, n: int) -> tuple[str, str]:
    """Fill and label colour for stage ``i`` of ``n``.

    With three stages the first is set apart (dark against two light ones):
    the note's point is how small Stage 1 is, and adjacent shades of the ramp
    are too close to read that at a glance. Five stages keep the full ramp.
    """
    if n == 3:
        return (RAMP[0], "white") if i == 0 else (RAMP[2 + i], INK)
    return RAMP[i % len(RAMP)], ("white" if i < 3 else INK)


def _one_stack(rep, parts, total: float, title: str, subtitle: str, save: str) -> None:
    """A single bar split into ``parts`` ``[(label, value)]``, labelled in place."""
    fig, ax = plt.subplots(figsize=(8.4, 2.3))
    left = 0.0
    for i, (lab, v) in enumerate(parts):
        fill, ink = _stage_shade(i, len(parts))
        ax.barh([0], [v], left=left, color=fill, height=0.5, zorder=3)
        if v > total * 0.04:
            ax.text(left + v / 2, 0,
                    f"{lab}\n{v:.0f}d ({100 * v / total:.0f}%)",
                    ha="center", va="center", fontsize=8.5, color=ink)
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
                     save: str, stages=STAGES_FIVE, share_of=None,
                     braces=None, count_label: str = "Claims",
                     show_table: bool = True, shares: bool = False,
                     splits=None, highlight: str | None = None) -> None:
    """A table and one stacked bar per group, each adding to its own mean.

    ``share_of`` adds a "% of sample" column against that denominator, for
    when the groups are a selection (the top routes) rather than a partition.
    ``braces`` is one ``{stage: text}`` per row, drawn as a brace under that
    stage's segment: which offices held the claim during it. ``count_label``
    names the count column, for a note that says "complaints".
    ``show_table=False`` draws the chart alone, for a reader who would take a
    table of the same numbers as a second, different finding. ``shares=True``
    draws every bar to 100%, each stage as its share of that group's own
    total, with the total in days at the end: for comparing the split across
    groups whose totals differ. ``splits`` is one ``{stage: [(office, days)]}``
    per row (from :func:`stage_office_splits`): that stage's segment is cut
    into its offices, each labelled where it fits, and the ``highlight``
    office drawn in blue.
    """
    table = {
        label_col: [lab for lab, _ in rows],
        count_label: [r["n"] for _, r in rows],
    }
    if share_of:
        table["% of sample"] = [round(100 * r["n"] / share_of, 1) for _, r in rows]
    table.update({stage_label(k): [float(r[k]) for _, r in rows] for k in stages})
    table["Total"] = [float(r["total"]) for _, r in rows]
    if show_table:
        display(pl.DataFrame(table))

    # A route name is a sentence, and unwrapped it takes half the canvas
    # before a single bar is drawn. Wrapped, the label column is bounded
    # and the axis gets the room.
    labels = ["\n".join(textwrap.wrap(lab, 34)) + f"\n({r['n']:,})"
              for lab, r in rows]
    deep = max(lab.count("\n") for lab in labels)
    # Braces need a band under each bar, so the rows spread apart.
    pitch = 1.9 if braces else 1
    pos = [j * pitch for j in range(len(rows))]
    height = 0.42 * len(rows) * (1 + 0.42 * deep) + 1.9
    if braces:
        height += 0.62 * len(rows)
    fig, ax = plt.subplots(figsize=(10.6, height))
    xmax = 100.0 if shares else max(float(r["total"]) for _, r in rows)
    for j, (lab, r) in zip(pos, rows):
        left, scale = 0.0, (100 / float(r["total"]) if shares else 1.0)
        for i, k in enumerate(stages):
            v = float(r[k]) * scale
            if braces and braces[pos.index(j)].get(k):
                _brace(ax, left, left + v, j + 0.4, braces[pos.index(j)][k], xmax)
            fill, ink = _stage_shade(i, len(stages))
            ax.barh([j], [v], left=left, color=fill,
                    height=0.6, zorder=3, label=stage_label(k) if j == 0 else None)
            cut = splits[pos.index(j)].get(k) if splits else None
            if cut:
                _split_segment(ax, j, left, v, cut, fill, ink, highlight, xmax)
            elif v > (xmax if shares else float(r["total"])) * 0.07:
                ax.text(left + v / 2, j, f"{v:.0f}%" if shares else f"{v:.0f}", ha="center",
                        va="center", fontsize=8,
                        color=ink)
            left += v
        ax.text(left + 1.5, j, f"{float(r['total']):.0f} days" if shares else f"{left:.0f}d",
                va="center", fontsize=8.5, color=INK)
    ax.set_yticks(pos, labels)
    if braces:
        ax.set_ylim(pos[-1] + 1.45, -0.6)
    else:
        ax.invert_yaxis()
    ax.set_xlabel("Share of time to disposal (%)" if shares else "Mean days")
    if shares:
        ax.set_xlim(0, 112)
        ax.set_xticks(range(0, 101, 20))
    ax.grid(False)
    # Below the axis, not inside it: an inside legend lands on the longest
    # bar, which is the one the reader most wants to read.
    # The offset is in axes fractions, so a fixed one lands a hand's width
    # below a short chart and on the axis labels of a tall one.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -1.05 / height),
              ncol=len(stages), fontsize=8)
    rep.finish(ax, title, subtitle, ygrid=False, save=save)


def _split_segment(ax, y, left, width, parts, fill, ink, highlight, xmax) -> None:
    """Cut one stage's segment into its offices, scaled to fill it exactly.

    The offices' held days and the stage's span differ slightly where a
    record's dates run backwards; scaling keeps the bar's total the stage's
    own mean, and the labels give each office's held days.
    """
    total = sum(d for _, d in parts) or 1.0
    x = left
    for office, days in parts:
        w = width * days / total
        hot = office == highlight
        ax.barh([y], [w], left=x, color=BLUE if hot else fill, height=0.6,
                zorder=4, edgecolor="white", linewidth=1.2)
        text = f"{OFFICE_SHORT.get(office, office)} {days:.0f}d"
        # About 0.75% of axis width per character at this size.
        if w >= 0.0075 * xmax * len(text) + 0.5:
            ax.text(x + w / 2, y, text, ha="center", va="center", fontsize=7.5,
                    color="white" if hot else ink, zorder=5)
        x += w


def _brace(ax, x0: float, x1: float, y: float, text: str, xmax: float) -> None:
    """A square brace under [x0, x1] at y (axis inverted: larger y is lower)."""
    t, notch = 0.1, 0.12
    xm = (x0 + x1) / 2
    ax.plot([x0, x0, x1, x1], [y - t, y, y, y - t], color=INK_SOFT, lw=0.9,
            solid_capstyle="butt", zorder=2)
    ax.plot([xm, xm], [y, y + notch], color=INK_SOFT, lw=0.9, zorder=2)
    # Wrap to the segment's own width so neighbouring labels do not meet.
    width = max(12, int(100 * (x1 - x0) / xmax))
    ax.text(xm, y + notch + 0.05, textwrap.fill(text, width), ha="center",
            va="top", fontsize=7.5, color=INK, linespacing=1.1)


# How an office reads inside a bar, where the full title does not fit.
OFFICE_SHORT = {
    "Block Development Officer": "BDO",
    "District social security officer": "SSO",
    "District Collector": "Collector",
    "Department Secretary": "Secretary",
}


def stage_office_splits(s, groups, stage: str, stages=STAGES_THREE) -> list[dict]:
    """Per group, the days each office held its claims during ``stage``.

    ``groups`` is the :func:`stage_rows` list. Days are per claim in the
    group, offices in the order they usually hold the claim, for
    :func:`draw_stage_stack`'s ``splits``.
    """
    phases = ", ".join(_sql_str(p) for p in stages[stage])
    out = []
    for _, pred in groups:
        t = s.q(f"""
          WITH c AS (SELECT ticket_no FROM {s.t('phases_pop')} WHERE {pred})
          SELECT role, SUM(gap) / (SELECT COUNT(*) FROM c) AS days
          FROM {s.t('step_stages')}
          WHERE phase IN ({phases}) AND gap IS NOT NULL
            AND ticket_no IN (SELECT ticket_no FROM c)
          GROUP BY role ORDER BY AVG(step_index), role""")
        out.append({stage: [(r, float(d)) for r, d in t.iter_rows() if d > 0]})
    return out


def top_types(s, n: int = 10, also=()):
    """The ``n`` largest complaint types in P4, as :func:`stage_rows` groups.

    A type is a department and subcategory: the same subcategory name under
    two departments (Rural Housing) is two types, labelled by both. ``also``
    lists ``(dept, subcategory)`` types to show whatever their rank; each is
    added after the largest, unless it is already among them.
    """
    t = s.q(f"""
      SELECT g.dept, g.subcategory, COUNT(*) AS n
      FROM {s.t('phases_pop')} p JOIN {s.t('g')} g USING (ticket_no)
      WHERE g.subcategory IS NOT NULL
      GROUP BY 1, 2 ORDER BY n DESC, 1, 2 LIMIT {n}""")
    types = [(r["dept"], r["subcategory"]) for r in t.iter_rows(named=True)]
    types += [x for x in also if x not in types]
    return [(f"{sub} ({dept})",
             f"ticket_no IN (SELECT ticket_no FROM {s.t('g')} "
             f"WHERE dept = {_sql_str(dept)} AND subcategory = {_sql_str(sub)})")
            for dept, sub in types]


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


def route_stage_roles(s, groups, stages=STAGES_FIVE) -> pl.DataFrame:
    """Which offices held each route's claims during each stage.

    The stage boundaries are the phase boundaries (``journey.
    install_step_stages``). Each step of each claim in P4 is tagged with its
    stage, consecutive steps in the same role and stage are
    collapsed, and the offices are joined per stage. Registration and closure
    have no office, so a stage made only of them is blank.

    Claims on one route can split differently (a transfer between two
    Collectors is a hand-off but not a change of role), so this returns the
    commonest split per group and the share of the group's claims that follow
    it. ``groups`` is ``[(label, predicate over phases_pop)]``, as from
    :func:`top_route_groups` or :func:`entry_route_groups`; the ``Route``
    column carries the label. A claim matching two predicates counts in the
    first.
    """
    stage_of = "CASE " + " ".join(
        f"WHEN phase IN ({', '.join(_sql_str(p) for p in cols)}) THEN {_sql_str(k)}"
        for k, cols in stages.items()) + " END"
    group_of = "CASE " + " ".join(
        f"WHEN {pred} THEN {_sql_str(lab)}" for lab, pred in groups) + " END"
    keys = list(stages)
    d = s.q(f"""
      WITH grouped AS (
        SELECT ticket_no, {group_of} AS route FROM {s.t('phases_pop')}),
      named AS (
        SELECT ss.ticket_no, gr.route, ss.step_index, ss.phase, ss.role AS x
        FROM {s.t('step_stages')} ss JOIN grouped gr USING (ticket_no)
        WHERE gr.route IS NOT NULL),
      phased AS (SELECT *, {ROLE_CASE} AS role FROM named),
      tagged AS (SELECT *, {stage_of} AS stage FROM phased),
      kept AS (
        SELECT * FROM (
          SELECT *, LAG(role || '|' || stage) OVER (
                   PARTITION BY ticket_no ORDER BY step_index) AS prev
          FROM tagged)
        WHERE prev IS NULL OR prev <> role || '|' || stage),
      per_ticket AS (
        SELECT ticket_no, ANY_VALUE(route) AS route,
          {', '.join(f"COALESCE(STRING_AGG(role, ' > ' ORDER BY step_index) FILTER (WHERE stage = {_sql_str(k)}), '') AS \"{k}\"" for k in keys)}
        FROM kept GROUP BY 1),
      splits AS (
        SELECT route, {', '.join(f'"{k}"' for k in keys)}, COUNT(*) AS n,
          SUM(COUNT(*)) OVER (PARTITION BY route) AS route_n,
          ROW_NUMBER() OVER (PARTITION BY route ORDER BY COUNT(*) DESC,
                             {', '.join(f'"{k}"' for k in keys)}) AS rk
        FROM per_ticket GROUP BY route, {', '.join(f'"{k}"' for k in keys)})
      SELECT route AS "Route", {', '.join(f'"{k}"' for k in keys)},
        ROUND(100.0 * n / route_n, 1) AS "% of route's claims"
      FROM splits WHERE rk = 1""")
    order = {r: i for i, (r, _) in enumerate(groups)}
    return (d.with_columns(pl.col("Route").replace_strict(order).alias("_o"))
             .sort("_o").drop("_o")
             .rename({k: stage_label(k) for k in keys}))


# Entry points as a reader says them. The mart's wording names the forwarding
# act ("Forwarded from a District Collector"); a sentence wants the office.
ENTRY_SHORT = {
    "Forwarded from a District Collector": "Collector",
    "Forwarded from the Chief Minister's Cell": "CM Cell",
    "Received directly by the department": "Department, directly",
    "Forwarded from the Chief Secretary": "Chief Secretary",
    "Forwarded from the police": "Police",
    "Forwarded from the Governor's office": "Governor's office",
    "Entry point not recorded": "Entry not recorded",
}


def entry_routes(s, top: int = 3) -> pl.DataFrame:
    """The ``top`` entry offices in P4 by volume, and each one's commonest route.

    Shares: the entry office's of all P4 claims, and the route's of that
    entry office's claims. Ties break on the name, so a rerun is the same table.
    """
    pp = s.t("phases_pop")
    return s.q(f"""
      WITH e AS (
        SELECT entry_route, COUNT(*) AS n FROM {pp}
        GROUP BY 1 ORDER BY n DESC, entry_route LIMIT {top}),
      r AS (
        SELECT entry_route, route, COUNT(*) AS n,
               ROW_NUMBER() OVER (PARTITION BY entry_route
                                  ORDER BY COUNT(*) DESC, route) AS rk
        FROM {pp} WHERE route IS NOT NULL
          AND entry_route IN (SELECT entry_route FROM e)
        GROUP BY 1, 2)
      SELECT e.entry_route AS "Entry", e.n AS "Claims",
             ROUND(100.0 * e.n / (SELECT COUNT(*) FROM {pp}), 1) AS "% of sample",
             r.route AS "Commonest route", r.n AS "Route claims",
             ROUND(100.0 * r.n / e.n, 1) AS "% of entry"
      FROM e JOIN r ON r.entry_route = e.entry_route AND r.rk = 1
      ORDER BY e.n DESC, e.entry_route""")


def entry_route_groups(table: pl.DataFrame) -> list[tuple[str, str]]:
    """One group per row of :func:`entry_routes`: that entry office, on its route."""
    return [(f"{ENTRY_SHORT.get(r['Entry'], r['Entry'])}: {r['Commonest route']}",
             f"entry_route = {_sql_str(r['Entry'])} AND route = {_sql_str(r['Commonest route'])}")
            for r in table.iter_rows(named=True)]


# -- the field stage ----------------------------------------------------------

# The layers a field span can be explained by, keyed by their column in
# :func:`field_frame`. FIELD_LAYERS gives each the name a reader sees;
# FIELD_LAYER_DEFS says what it is, for the table a note shows beside the chart.
FIELD_LAYER_DEFS = {
    "field_office": "The office holding the complaint when it first reached the "
                    "lowest level on its route, e.g. a BDO",
    "sender_office": "The office through which the complaint first entered, "
                     "e.g. a Collector",
    "route": "The sequence of offices that held the complaint, by role",
    "subcategory": "The complaint type recorded on the portal",
    "district": "The district recorded on the complaint",
    "entry_route": "How the complaint entered: forwarded by a Collector or the CM "
                   "Cell, filed directly with the department, and so on",
    "dept": "The department the complaint was assigned to",
    "month": "The month the complaint was filed",
}
FIELD_LAYERS = {
    "Field office": "field_office",
    "Entry office": "sender_office",
    "Route": "route",
    "Complaint type": "subcategory",
    "District": "district",
    "Entry route": "entry_route",
    "Department": "dept",
    "Month filed": "month",
}
assert set(FIELD_LAYERS.values()) == set(FIELD_LAYER_DEFS)


def field_layer_table(layers=FIELD_LAYERS) -> pl.DataFrame:
    """What each layer in :func:`draw_field_drivers` means, in chart order."""
    return pl.DataFrame({"Factor": list(layers),
                         "What it is": [FIELD_LAYER_DEFS[c] for c in layers.values()]})


_OFFICE = "role || COALESCE(', ' || NULLIF(place, ''), '')"


def field_frame(s) -> pl.DataFrame:
    """One row per P4 claim that reached field action, with its layers.

    The field office is the desk holding the claim when it first reached its
    deepest rung; the sender is the desk it first entered (every step before
    the first hand-off is at one office). Both are a role and a place, never
    an internal code. Rows come in ticket order:
    :func:`field_drivers` splits by row position, so an unordered frame would
    give different halves, and different shares, on each run (#422).
    """
    ss = s.t("step_stages")
    return s.q(f"""
      WITH o AS (
        SELECT ticket_no,
          ARG_MIN({_OFFICE}, step_index) FILTER (WHERE rung = deepest_rung) AS field_office,
          ARG_MIN(role, step_index) FILTER (WHERE rung = deepest_rung) AS field_role,
          ARG_MAX({_OFFICE}, step_index) FILTER (WHERE phase = 'first_assignment')
            AS sender_office,
          BOOL_OR(phase = 'field_action') AS reached_field
        FROM {ss} GROUP BY 1)
      SELECT p.ticket_no, p.field_action, p.route, p.entry_route,
             g.dept, g.dept || ' / ' || COALESCE(g.subcategory, '(none)') AS subcategory,
             g.district, STRFTIME(g.created_on, '%Y-%m') AS month,
             o.field_office, o.field_role, o.sender_office
      FROM {s.t('phases_pop')} p JOIN {s.t('g')} g USING (ticket_no)
      JOIN o USING (ticket_no)
      WHERE o.reached_field
      ORDER BY p.ticket_no""")


def _field_days_by(s, frame: pl.DataFrame, col: str) -> pl.DataFrame:
    s.con.register("_field_tickets", frame.select("ticket_no"))
    return s.q(f"""
      SELECT {col} AS k, CAST(SUM(gap) AS DOUBLE) AS days FROM {s.t('step_stages')}
      WHERE phase = 'field_action' AND gap IS NOT NULL
        AND ticket_no IN (SELECT ticket_no FROM _field_tickets)
      GROUP BY 1""").with_columns(
        (pl.col("days") / frame.height).round(1).alias("Mean days per claim"),
        (100 * pl.col("days") / pl.col("days").sum()).round(1).alias("% of field days"))


def field_levels(s, frame: pl.DataFrame) -> pl.DataFrame:
    """Field days by the level of the desk holding the claim; adds to the mean."""
    return (_field_days_by(s, frame, "rung").sort("k")
            .with_columns(pl.col("k").replace_strict(RUNG_NAME).alias("Level"))
            .select("Level", "Mean days per claim", "% of field days"))


def field_roles(s, frame: pl.DataFrame, top: int = 6) -> pl.DataFrame:
    """Field days by the role holding the claim, the ``top`` roles and the rest."""
    d = _field_days_by(s, frame, "role").sort("days", descending=True)
    head, rest = d.head(top), d.slice(top)
    if rest.height:
        head = pl.concat([head, pl.DataFrame({
            "k": ["All other desks"], "days": [rest["days"].sum()],
            "Mean days per claim": [round(rest["days"].sum() / frame.height, 1)],
            "% of field days": [round(rest["% of field days"].sum(), 1)]})],
            how="vertical_relaxed")
    return head.rename({"k": "Desk"}).select("Desk", "Mean days per claim",
                                             "% of field days")


def draw_field_levels(rep, levels: pl.DataFrame, roles: pl.DataFrame,
                      title: str, subtitle: str, save: str,
                      days_label: str = "field days") -> None:
    """Where field days sit: one bar by level, then the desks by role."""
    parts = [(r["Level"], float(r["Mean days per claim"]))
             for r in levels.iter_rows(named=True) if r["Mean days per claim"] > 0]
    _one_stack(rep, parts, sum(v for _, v in parts), title, subtitle, save)
    fig, ax = plt.subplots(figsize=(8.2, 0.36 * roles.height + 1.4))
    barh(ax, list(roles["Desk"]), list(roles["Mean days per claim"]),
         shares=list(roles["% of field days"]), fmt="{:,.1f}d")
    ax.set_xlabel(f"Average {days_label} spent at this office")
    rep.finish(ax, f"{days_label[0].upper()}{days_label[1:]} by office", None,
               xgrid=True, ygrid=False,
               save=save + "_roles")


def field_drivers(frame: pl.DataFrame, layers=FIELD_LAYERS, seed: int = 0):
    """Each layer's share of the field-time variation the record explains."""
    return shapley_shares(frame, "field_action", layers, seed=seed)


def draw_field_drivers(rep, shares: pl.DataFrame, full: float, title: str,
                       subtitle: str, save: str, days_label: str = "field days",
                       always=("Complaint type",)) -> None:
    """All the variation in one bar: each factor's part, then what is unexplained.

    The Shapley parts add exactly to the model's out-of-sample R², so they are
    parts of a whole and belong in one stacked bar, not side by side. Every
    factor is drawn by name, largest first; one too narrow to hold its label
    is labelled above or below the bar with an arrow. The grey tail is what the
    factors do not explain. A factor in ``always`` that is missing from
    ``shares`` (one value in this population, so dropped) is said in the note.
    """
    parts = [(lab, 100 * float(v)) for lab, v in zip(shares["Layer"], shares["shapley_r2"])]
    # Out of sample a factor can lose fit; it has no span to draw.
    neg = [(lab, v) for lab, v in parts if v <= 0]
    parts = [(lab, v) for lab, v in parts if v > 0]
    # The house ramp stretched to one shade per factor, so none repeats.
    shades = LinearSegmentedColormap.from_list("", [RAMP[0], "#E3C4C8"])(
        np.linspace(0, 1, max(len(parts), 2)))
    fig, ax = plt.subplots(figsize=(10.0, 3.2))
    left, outside = 0.0, []
    for i, (lab, v) in enumerate(parts):
        ax.barh([0], [v], left=left, color=shades[i], height=0.5,
                edgecolor="white", linewidth=1.5, zorder=3)
        # About 0.65% of axis width per character at this size.
        if v >= 0.65 * len(lab) + 1:
            ax.text(left + v / 2, 0, f"{lab}\n{v:.0f}%", ha="center", va="center",
                    fontsize=8, color="white" if i < 3 else INK)
        else:
            outside.append((left + v / 2, f"{lab} {v:.0f}%"))
        left += v
    # Narrow ones alternate above and below; on each side a label is pushed
    # right until it clears the one before, and an arrow points to its span.
    for side, y in ((0, 0.52), (1, -0.52)):
        prev_end = -1.0
        for x, text in outside[side::2]:
            half = 0.65 * len(text) / 2
            tx = max(x, prev_end + half + 0.5)
            prev_end = tx + half
            ax.annotate(text, (x, 0.25 if y > 0 else -0.25), (tx, y), ha="center",
                        va="bottom" if y > 0 else "top", fontsize=8, color=INK,
                        arrowprops=dict(arrowstyle="-|>", color=INK, lw=0.6,
                                        shrinkA=1, shrinkB=0, mutation_scale=7))
    ax.barh([0], [100 - left], left=left, color="#E6E6E6", height=0.5, zorder=3)
    ax.text(left + (100 - left) / 2, 0, f"Not explained\n{100 - left:.0f}%",
            ha="center", va="center", fontsize=8, color=INK)
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.8, 0.8)
    ax.set_yticks([])
    ax.set_xlabel(f"Share of all variation in {days_label} (%)")
    fixed = [lab for lab in always if lab not in shares["Layer"].to_list()]
    note = " ".join([f"{lab}: one value here, so it explains nothing." for lab in fixed]
                    + [f"{lab} explains nothing beyond chance ({v:.1f}%)." for lab, v in neg])
    rep.finish(ax, title, subtitle, note=note or None, ygrid=False, save=save)


def office_means(frame: pl.DataFrame, floor: int = 30, role=None,
                 subcategory=None) -> pl.DataFrame:
    """Mean field days per field office, offices with at least ``floor`` claims."""
    f = frame
    if role:
        f = f.filter(pl.col("field_role") == role)
    if subcategory:
        f = f.filter(pl.col("subcategory") == subcategory)
    return (f.group_by("field_office", "district")
             .agg(pl.len().alias("Claims"),
                  pl.col("field_action").mean().round(1).alias("Mean field days"))
             .filter(pl.col("Claims") >= floor)
             .sort("Mean field days", "field_office")
             .rename({"field_office": "Office", "district": "District"}))


def office_volume(s, frame: pl.DataFrame, role: str, floor: int = 30) -> pl.DataFrame:
    """Per ``role`` office: complaints it received, and its average field days.

    Volume counts every complaint in the sample's base (disposed or not) that
    reached the office, so a slow office with a backlog does not look quiet.
    Field days come from ``frame`` and need ``floor`` complaints to show.
    """
    vol = s.q(f"""
      SELECT role_name || COALESCE(', ' || NULLIF(place, ''), '') AS field_office,
             COUNT(DISTINCT ticket_no) AS received
      FROM acting_office_named
      WHERE role_name = {_sql_str(role)}
        AND ticket_no IN (SELECT ticket_no FROM {s.t('g')})
      GROUP BY 1""")
    return (office_means(frame, floor, role=role)
            .join(vol.rename({"field_office": "Office"}), on="Office", how="left")
            .rename({"received": "Complaints received"})
            .sort("Complaints received", "Office"))


def draw_office_volume(rep, offices: pl.DataFrame, title: str, subtitle: str,
                       save: str, days_label: str = "field days",
                       period: str = "2024-25") -> None:
    """Each office's volume against its average days, one dot per office."""
    x = offices["Complaints received"].to_numpy().astype(float)
    y = offices["Mean field days"].to_numpy().astype(float)
    fig, ax = plt.subplots(figsize=(9.6, 4.0))
    ax.scatter(x, y, s=14, color=MAROON, alpha=0.45, zorder=3, linewidths=0)
    ax.set_xlabel(f"Complaints received, {period}")
    ax.set_ylabel(f"Average {days_label}")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    rep.finish(ax, title, subtitle, save=save)


