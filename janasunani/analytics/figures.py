"""Chart and table helpers for the grievance analysis notebooks.

Extracted from ``notebooks/aggregate_vs_ssepd.ipynb`` so the OAP notebook reads
one copy rather than a fork of it. Nothing here computes a finding: the numbers
come from ``sql/grievance_journey.sql`` and the notebook's own queries. This is
presentation only -- the palette, the rcParams, and the handful of layout
helpers that took several rounds of looking at rendered PNGs to get right.

Populations are a parameter, not a constant. The aggregate notebook compares
two (all Odisha against SSEPD); the OAP notebook has one. Everything that draws
a panel per population takes them from :class:`Report`, so neither notebook
carries a copy of the layout code to suit its own panel count.

Style is the DPIC design system's (``dpic.branding.colors``). The three
categorical hues were validated with the dataviz palette checker: maroon, blue
and orange clear both discrimination gates all-pairs (CVD dE 14.8,
normal-vision dE 20.8). A fourth categorical hue fails the normal-vision floor,
so **three series is a hard cap** -- past that use one hue sorted by value, or
small multiples.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import polars as pl

__all__ = [
    "MAROON", "BLUE", "ORANGE", "SERIES", "RAMP", "INK", "INK_SOFT", "RULE",
    "use_dpic_style", "barh", "bars", "thousands", "label_points",
    "label_line_ends", "density", "time_stats", "bottleneck_dumbbell", "Report",
]

# --- palette -----------------------------------------------------------------

MAROON, BLUE, ORANGE = "#8B1524", "#155F83", "#C16622"
SERIES = [MAROON, BLUE, ORANGE]

# Rank-ordered series (a top-five list) are ordinal, not categorical: swapping
# the order changes the meaning, so they take a one-hue ramp rather than five
# competing hues. Five DPIC hues fail the colour-separation checks badly (green
# against orange, CVD dE 3.4). Contrast against white: 9.4, 6.7, 4.4, 3.1, 2.3
# -- the lightest is below 3:1, so every line it draws carries a direct label.
RAMP = ["#8B1524", "#9E3A47", "#B0606A", "#C08189", "#CE9DA3"]
INK, INK_SOFT, RULE = "#1A1A1A", "#666666", "#DDDDDD"


def use_dpic_style() -> str:
    """Apply the house rcParams. Returns the font family actually resolved.

    Calibri is the DPIC brand face, but leaving a missing family in the list
    makes matplotlib emit a findfont warning for *every* text object -- 13,000
    of them on one run, which buried the real output and tripled the file size.
    So the family is resolved once against what is installed.
    """
    installed = {f.name for f in mpl.font_manager.fontManager.ttflist}
    font = next(
        (f for f in ("Calibri", "Helvetica Neue", "Arial", "DejaVu Sans")
         if f in installed),
        "DejaVu Sans",
    )
    mpl.rcParams.update({
        "figure.dpi": 130,
        # inline PNGs must expand for the title block drawn above the axes
        "savefig.bbox": "tight",
        "savefig.dpi": 130,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        # A list, not a single family: matplotlib falls back per glyph, so a
        # character the brand face lacks still renders.
        "font.family": "sans-serif",
        "font.sans-serif": [font, "DejaVu Sans"],
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
        "axes.titlelocation": "left",
        "axes.labelcolor": INK_SOFT,
        "axes.labelsize": 9.5,
        "axes.edgecolor": RULE,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": RULE,
        "grid.linewidth": 0.6,
        "xtick.color": INK_SOFT,
        "ytick.color": INK_SOFT,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "lines.linewidth": 2.0,
        "lines.markersize": 5,
    })
    return font


# --- marks -------------------------------------------------------------------


def thousands(ax, axis: str = "y") -> None:
    fmt = mticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _labels_with_share(values, shares, fmt: str) -> list[str]:
    """Bar labels, with the share in parentheses where one is meaningful.

    A count alone makes the reader do arithmetic against a total they may not
    have. ``shares=True`` computes each value's share of the bar total; a
    sequence is used as given, for the case where the denominator is not the
    bars on screen.
    """
    if shares is None or shares is False:
        return [fmt.format(v) for v in values]
    if shares is True:
        total = sum(values)
        pct = [100 * v / total if total else 0.0 for v in values]
    else:
        pct = [float(p) for p in shares]
    return [f"{fmt.format(v)} ({p:.0f}%)" for v, p in zip(values, pct)]


def barh(ax, labels, values, color: str = MAROON, fmt: str = "{:,.0f}",
         shares=None):
    """Horizontal bars, sorted by the caller, with a direct label on each end.

    ``shares`` adds the share in parentheses: ``True`` for a share of the bars
    shown, or a sequence of percentages when the denominator is something else.
    """
    # DuckDB hands back DECIMAL for ratio columns, which will not multiply with
    # the float padding below.
    values = [float(v) for v in values]
    text = _labels_with_share(values, shares, fmt)
    y = range(len(labels))
    ax.barh(list(y), list(values), color=color, height=0.68, zorder=3)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    span = max(values) if len(values) else 1
    for i, v in enumerate(values):
        ax.text(v + span * 0.012, i, text[i], va="center", fontsize=9, color=INK)
    # Wider headroom when the label carries a share as well as a count.
    ax.set_xlim(0, span * (1.30 if shares is not None and shares is not False else 1.16))
    ax.spines["left"].set_color(RULE)
    return ax


def bars(ax, labels, values, color: str = MAROON, fmt: str = "{:,.0f}",
         shares=None, width: float = 0.55):
    """Vertical bars with a direct label on each top. Counterpart of `barh`."""
    values = [float(v) for v in values]
    text = _labels_with_share(values, shares, fmt)
    x = range(len(labels))
    ax.bar(list(x), list(values), color=color, width=width, zorder=3)
    ax.set_xticks(list(x), labels)
    span = max(values) if len(values) else 1
    for i, v in enumerate(values):
        ax.text(i, v + span * 0.02, text[i], ha="center", fontsize=9, color=INK)
    ax.set_ylim(0, span * 1.18)
    return ax


def time_stats(col: str = "days_to_close", prefix: str = "") -> str:
    """SQL for the four numbers every duration in these notes must carry.

    Count, median, mean and slowest tenth, named consistently. A median without
    its mean hides a tail that is often twice the middle, and every table here
    has been asked for both at least once.
    """
    c = f"{prefix}{col}"
    return (
        f"COUNT(*) FILTER (WHERE {c} IS NOT NULL) AS n, "
        f"CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {c}) AS BIGINT) AS median_days, "
        f"ROUND(AVG({c}), 1) AS mean_days, "
        f"CAST(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY {c}) AS BIGINT) AS slowest_tenth"
    )


def _xnum(x) -> float:
    """Data x as a number. `transData` works in numbers; a date axis does not."""
    return mdates.date2num(x) if hasattr(x, "toordinal") else float(x)


def label_points(ax, xs, ys, labels, fontsize: float = 7.8) -> None:
    """Direct labels that step out of each other's way.

    Tries a few offsets per point and takes the first that does not overlap a
    label already placed. Beats hand-tuning when two districts sit on the same
    spot, which several do.
    """
    ax.figure.canvas.draw()
    placed: list[tuple[float, float, float, float]] = []
    cands = [(5, 3), (5, -10), (-5, 3), (-5, -10), (5, 12), (5, -20)]
    # reverse=True rather than negating the key: x may be a datetime, which has
    # no unary minus.
    for x, y, text in sorted(zip(xs, ys, labels), key=lambda t: t[0], reverse=True):
        px, py = ax.transData.transform((_xnum(x), float(y)))
        w, h = len(text) * fontsize * 0.55, fontsize * 1.35
        for dx, dy in cands:
            ha = "left" if dx > 0 else "right"
            x0 = px + dx if dx > 0 else px + dx - w
            box = (x0, py + dy - h / 2, x0 + w, py + dy + h / 2)
            if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                       for b in placed):
                placed.append(box)
                ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points",
                            fontsize=fontsize, color=INK, ha=ha, va="center")
                break


def label_line_ends(ax, ends, colours=None, fontsize: float = 7.5) -> None:
    """Label a set of line ends to the right of the plot, none overlapping.

    The greedy scatter labeller is wrong for this case: it will happily place a
    label left of its point or below the axis. Here every label belongs to the
    right of the data at its own line's height, so the heights are simply
    pushed apart until they clear each other.
    """
    if not ends:
        return
    ax.figure.canvas.draw()
    inv = ax.transData.inverted()
    rows = []
    for i, (x, y, text) in enumerate(ends):
        _, py = ax.transData.transform((_xnum(x), float(y)))
        rows.append([py, text, i])
    rows.sort(key=lambda r: r[0])
    gap = fontsize * 1.55
    for i in range(1, len(rows)):
        if rows[i][0] - rows[i - 1][0] < gap:
            rows[i][0] = rows[i - 1][0] + gap
    x_at = max(_xnum(e[0]) for e in ends)
    for py, text, i in rows:
        _, dy = inv.transform((0, py))
        ax.annotate(text, (x_at, dy), xytext=(6, 0), textcoords="offset points",
                    fontsize=fontsize, va="center", ha="left",
                    color=(colours[i] if colours else INK))


def density(ax, values, colour: str = MAROON, label: Optional[str] = None,
            clip: Optional[float] = None, sample: int = 60_000,
            annotate: bool = True, unit: str = "d"):
    """Smoothed distribution curve with its median and mean marked.

    Durations here are heavily right-skewed, so the curve is drawn over the
    bulk of the data (to the 95th percentile by default) and the long tail
    beyond it is left off the axis rather than flattening everything into the
    first bin. The mean often sits past that clip; when it does it is labelled
    at the edge rather than dropped, so it is never silently missing.

    Returns ``(median, mean)`` over the full data, not the clipped view.
    """
    from scipy.stats import gaussian_kde  # heavy; only needed when drawing

    v = np.asarray([float(x) for x in values if x is not None])
    if v.size == 0:
        return None, None
    med, avg = float(np.median(v)), float(np.mean(v))
    hi = max(clip if clip is not None else float(np.percentile(v, 95)), 1.0)
    if np.ptp(v) > 0:
        fit = v if v.size <= sample else np.random.default_rng(0).choice(v, sample, replace=False)
        kde = gaussian_kde(fit)
        xs = np.linspace(0, hi, 240)
        ax.plot(xs, kde(xs), color=colour, linewidth=2, label=label, zorder=3)
        ax.fill_between(xs, kde(xs), color=colour, alpha=0.10, zorder=2)
    ax.set_xlim(0, hi)
    if not annotate:
        return med, avg

    # The count the curve is drawn from. A distribution without its n invites
    # the reader to trust a shape that might rest on forty cases. It goes in the
    # top-right corner, not the bottom: the curve's own tail runs along the
    # bottom edge and swallowed it there. The median and mean labels below sit
    # a row lower so the corner stays free for it.
    ax.text(0.995, 0.995, f"n = {v.size:,}", transform=ax.transAxes,
            ha="right", va="top", fontsize=7.5, color=INK_SOFT, zorder=5)

    # Headroom so the labels clear the curve's peak, and each label beside its
    # own line rather than in a corner away from it. The two sit at different
    # heights because the median and the mean are often close together.
    #
    # 1.45, not 1.28: the lower label sits at 0.79 of the axis, and at 1.28 the
    # peak reaches 0.78, so a broad or bimodal curve ran straight through it.
    # At 1.45 the peak tops out at 0.69 and both label rows are above it.
    ax.set_ylim(top=ax.get_ylim()[1] * 1.45)
    tr = ax.get_xaxis_transform()

    # When the median and the mean are within a few days of each other, a label
    # padded off its own line still lands on the other one. Both labels then
    # anchor to whichever line is further right, so each clears both.
    close = abs(avg - med) < 0.10 * hi
    pad = 0.012 * hi

    def mark(x, y, col, style, text, anchor=None):
        ax.axvline(x, color=col, linewidth=1.4, linestyle=style, zorder=4)
        a = x if anchor is None else anchor
        # Flip the label left of its line near the right edge, where a
        # right-hand label would run off the axis.
        right = a > 0.72 * hi
        ax.text(a - pad if right else a + pad, y, text, transform=tr,
                ha="right" if right else "left", va="top",
                fontsize=8, color=col, zorder=5)

    anchor = max(med, avg) if (close and avg <= hi) else None
    mark(med, 0.90, BLUE, "--", f"median {med:.0f}{unit}", anchor)
    if avg <= hi:
        mark(avg, 0.79, ORANGE, ":", f"mean {avg:.0f}{unit}", anchor)
    else:
        ax.text(0.995, 0.79, f"mean {avg:.0f}{unit} >", transform=ax.transAxes,
                ha="right", va="top", fontsize=8, color=ORANGE, zorder=5)
    return med, avg


def bottleneck_dumbbell(axes, panels, lo_col: str = "Median days",
                        hi_col: str = "Mean days",
                        lo_label: str = "Typical case (median)",
                        hi_label: str = "Mean",
                        xlabel: str = "Days before the next recorded step"):
    """Two statistics per office, one panel per role.

    ``panels`` is ``[(role_title, DataFrame), ...]`` where each frame has
    ``Office`` and the two named columns, already ordered and limited by the
    caller. **Order the frame on ``hi_col``**, the statistic the labels show, or
    the chart reads as an arbitrary ranking.

    Both are drawn because one alone misleads. The mean is what a backlog is
    made of, but a high mean can come from an office that is usually quick and
    occasionally catastrophic, or one that is uniformly slow — a different
    problem needing a different fix. The median beside it separates the two.
    """
    for ax, (title, d) in zip(axes, panels):
        names = [r.split(", ", 1)[1] if ", " in r else r for r in d["Office"]]
        y = list(range(len(names)))
        lo = [float(v) for v in d[lo_col]]
        hi = [float(v) for v in d[hi_col]]
        for i, (a, b) in enumerate(zip(lo, hi)):
            ax.plot([a, b], [i, i], color=RULE, linewidth=2, zorder=2)
        ax.scatter(lo, y, s=46, color=BLUE, zorder=3, label=lo_label)
        ax.scatter(hi, y, s=46, color=MAROON, zorder=3, label=hi_label)
        top = max(hi) if hi else 1
        for i, v in enumerate(hi):
            ax.text(v + top * 0.02, i, f"{v:,.0f}", va="center", fontsize=8.5, color=INK)
        ax.set_yticks(y, names)
        ax.invert_yaxis()
        ax.set_xlim(0, top * 1.18)
        ax.set_title(title, fontsize=10, loc="left", color=INK, pad=6)
        ax.set_xlabel(xlabel)
        ax.xaxis.grid(True)
        ax.yaxis.grid(False)
        ax.set_axisbelow(True)
        ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
        ax.spines["left"].set_color(RULE)
    axes[0].legend(loc="lower right")


# --- the report ---------------------------------------------------------------


class Report:
    """Queries and panel layout for one notebook.

    ``populations`` is a list of ``(label, sql_predicate)``. One entry draws a
    single panel per exhibit; two draw them side by side. Every method that
    lays out panels reads it, so a notebook with a different number of
    populations needs no layout code of its own.
    """

    def __init__(self, con, populations: Sequence[tuple[str, str]], figdir: Path,
                 prefix: str = "") -> None:
        self.con = con
        self.populations = list(populations)
        self.figdir = Path(figdir)
        self.figdir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix

    # -- queries --

    def q(self, sql: str) -> pl.DataFrame:
        return self.con.execute(sql).pl()

    def save(self, fig, name: str) -> None:
        fig.savefig(self.figdir / f"{self.prefix}{name}.png",
                    bbox_inches="tight", facecolor="white")

    # -- single-axis charts --

    def finish(self, ax, title, subtitle=None, note=None, xgrid=False, ygrid=True,
               save=None) -> None:
        """Titles, a recessive grid, and the caveat that must travel with a chart."""
        if subtitle:
            ax.set_title(title, pad=32)
            ax.text(0, 1.022, subtitle, transform=ax.transAxes,
                    fontsize=9.5, color=INK_SOFT, va="bottom")
        else:
            ax.set_title(title, pad=12)
        ax.xaxis.grid(xgrid)
        ax.yaxis.grid(ygrid)
        ax.set_axisbelow(True)
        if note:
            ax.figure.text(0.005, -0.02, note, fontsize=8, color=INK_SOFT, va="top")
        ax.figure.tight_layout()
        if save:
            self.save(ax.figure, save)
        plt.show()

    # -- panel charts --

    def panels(self, sql_for: Callable[[str], str], figsize=(9.6, 3.6), sharey=False):
        """One query per population, plus the axes to draw them on.

        Returns ``(fig, [(ax, label, df), ...])``. Volumes can differ by ~27x
        between populations, so panels get independent y-scales unless asked
        otherwise.
        """
        n = len(self.populations)
        fig, axes = plt.subplots(1, n, figsize=figsize, sharey=sharey, squeeze=False)
        pairs = [(ax, label, self.q(sql_for(flag)))
                 for ax, (label, flag) in zip(axes[0], self.populations)]
        return fig, pairs

    def fig_title(self, fig, title, subtitle=None) -> None:
        """Title block above a multi-panel figure.

        Offsets are in inches, not figure fractions: a fraction that clears the
        title on a 5in figure lands on top of it on a 3in one, which is how the
        first draft of every panel chart collided.
        """
        fig.tight_layout()
        h = fig.get_figheight()
        if subtitle:
            fig.text(0.005, 1 + 0.34 / h, title, ha="left", va="bottom",
                     fontsize=12, fontweight="bold", color=INK)
            fig.text(0.005, 1 + 0.10 / h, subtitle, ha="left", va="bottom",
                     fontsize=9.5, color=INK_SOFT)
        else:
            fig.text(0.005, 1 + 0.10 / h, title, ha="left", va="bottom",
                     fontsize=12, fontweight="bold", color=INK)

    def finish_panels(self, fig, title, subtitle=None, save=None) -> None:
        self.fig_title(fig, title, subtitle)
        if save:
            self.save(fig, save)
        plt.show()

    def density_grid(self, sql: Callable[[str, str], str],
                     groups: Sequence[tuple[str, str]],
                     title: str, subtitle: str, save: str,
                     xlabel: str = "Days to close", figsize=None) -> None:
        """A distribution per (population, group), medians and means marked.

        ``sql(flag, where)`` returns a query yielding one column ``v``;
        ``groups`` is a list of ``(label, where)``. One helper so every
        duration in a notebook is drawn and annotated the same way.
        """
        n, rows = len(groups), len(self.populations)
        fig, axes = plt.subplots(
            rows, n, squeeze=False,
            figsize=figsize or (3.6 * n + 1.6, 2.5 * rows))
        for r, (label, flag) in enumerate(self.populations):
            for c, (glabel, where) in enumerate(groups):
                ax = axes[r][c]
                density(ax, self.q(sql(flag, where))["v"].to_list(), colour=MAROON)
                if r == 0 and n > 1:
                    ax.set_title(glabel, fontsize=9.5, loc="left", color=INK, pad=4)
                if c == 0:
                    ax.set_ylabel(label, fontsize=9, color=INK)
                if r == rows - 1:
                    ax.set_xlabel(xlabel, fontsize=8)
                ax.set_yticks([])
                ax.grid(False)
                for sp in ("top", "right", "left"):
                    ax.spines[sp].set_visible(False)
                ax.tick_params(labelsize=7.5)
        self.fig_title(fig, title, subtitle)
        self.save(fig, save)
        plt.show()

    # -- tables --

    def side_by_side(self, sql_for: Callable[[str], str],
                     rename: Optional[dict] = None) -> pl.DataFrame:
        """One row block per population, stacked with a Population column."""
        frames = []
        for label, flag in self.populations:
            df = self.q(sql_for(flag))
            if rename:
                df = df.rename({k: v for k, v in rename.items() if k in df.columns})
            frames.append(df.with_columns(pl.lit(label).alias("Population")))
        out = pl.concat(frames, how="vertical_relaxed")
        return out.select(["Population"] + [c for c in out.columns if c != "Population"])

    def pivot_pop(self, sql_for: Callable[[str], str], index: str,
                  values: Sequence[str], rename: Optional[dict] = None,
                  sort_by: Optional[str] = None,
                  labels: Optional[dict] = None) -> pl.DataFrame:
        """One row per ``index`` value, one column group per population.

        Populations across the top rather than stacked: the comparison the
        reader is making is between them, and a stacked table makes that a
        scroll instead of a glance.
        """
        frames = []
        for label, flag in self.populations:
            df = self.q(sql_for(flag))
            if rename:
                df = df.rename({k: v for k, v in rename.items() if k in df.columns})
            frames.append(df.with_columns(pl.lit(label).alias("Population")))
        df = pl.concat(frames, how="vertical_relaxed")

        order = (df.filter(pl.col("Population") == self.populations[0][0])
                   .sort(sort_by or values[0], descending=True)[index].to_list()
                 if sort_by or values else df[index].unique().to_list())
        seen, ordered_index = set(), []
        for v in order + df[index].to_list():
            if v not in seen:
                seen.add(v)
                ordered_index.append(v)

        wide = df.pivot(index=index, on="Population", values=list(values))
        rename_map = {}
        for pop, _ in self.populations:
            for v in values:
                src = f"{v}_{pop}" if len(values) > 1 else pop
                if src in wide.columns:
                    rename_map[src] = f"{pop} — {(labels or {}).get(v, v)}"
        wide = wide.rename(rename_map)
        cols = [index] + [c for pop, _ in self.populations
                          for c in wide.columns if c.startswith(f"{pop} — ")]
        wide = wide.select([c for c in cols if c in wide.columns])
        return wide.sort(pl.col(index).map_elements(
            lambda v: ordered_index.index(v) if v in ordered_index else 10**6,
            return_dtype=pl.Int64))

    def facts(self, title: str, metric: str = "COUNT(*)",
              metric_name: str = "Grievances", where: str = "TRUE",
              monthly_ylabel: Optional[str] = None, save: Optional[str] = None,
              fmt: str = "{:,.0f}"):
        """Section opener: the figure overall, by year, and by month.

        Three parts because a single total hides both the trend and the
        seasonality, and every section here has been misread at least once
        when shown only one of them.
        """
        from IPython.display import Markdown, display

        head = []
        for name, flag in self.populations:
            v = self.q(f"SELECT {metric} AS v FROM g WHERE {flag} AND {where}")["v"][0]
            head.append(f"**{name}: {fmt.format(v)}**")
        display(Markdown(f"{title} &nbsp;&nbsp; " + " &nbsp;·&nbsp; ".join(head)))

        by_year = self.side_by_side(
            lambda flag: f"""SELECT year, {metric} AS value FROM g
                             WHERE {flag} AND {where} GROUP BY 1 ORDER BY 1"""
        ).pivot(on="Population", index="year", values="value").sort("year")
        display(by_year.rename({"year": "Year"}))

        n = len(self.populations)
        fig, axes = plt.subplots(1, n, figsize=(4.8 * n, 3.2), squeeze=False)
        for ax, (name, flag) in zip(axes[0], self.populations):
            m = self.q(f"""SELECT DATE_TRUNC('month', created_on) AS month,
                                  {metric} AS value
                           FROM g WHERE {flag} AND {where}
                           GROUP BY 1 ORDER BY 1""")
            ax.plot(m["month"], m["value"], color=MAROON)
            ax.fill_between(m["month"], m["value"], color=MAROON, alpha=0.10)
            ax.set_ylim(bottom=0)
            ax.set_title(name, fontsize=10, loc="left", color=INK, pad=6)
            ax.set_ylabel(monthly_ylabel or metric_name)
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            thousands(ax)
            ax.yaxis.grid(True)
            ax.set_axisbelow(True)
        self.fig_title(fig, f"{title}, by month",
                       "Note the different vertical scales."
                       if n > 1 else None)
        if save:
            self.save(fig, save)
        plt.show()
        return by_year
