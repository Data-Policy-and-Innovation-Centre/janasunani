"""The bottleneck note, as a department-shaped object rather than a notebook.

``notebooks/ssepd_bottlenecks.ipynb`` and
``notebooks/panchayati_raj_bottlenecks.ipynb`` ask the same question of two
departments: where does a grievance lose time, and which of those places could
an intervention move. The prose differs; the arithmetic does not. So every
query and every chart lives here once and the notebooks carry only their own
words and a call.

The parameters are the department, the subcategory it narrows to, and the
duplicate-group file for that department. Nothing else about the note changes
between the two.

**The sample narrows and never widens.** :class:`Bottlenecks` computes each
step's count once into ``FUNNEL`` and every exhibit takes its n from there, so
an exhibit cannot quietly stand on a population the reader has not been shown.
The constructor asserts the funnel is monotone and that the entry-office
partition accounts for every row of the step it partitions.

Reads ``grievance_base`` and the journey tables only: no citizen prose, no
officer name, no internal office code reaches an output.
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from IPython.display import Markdown, display

from janasunani.analytics import journey
from janasunani.analytics.figures import (
    BLUE, INK, MAROON, ORANGE, RAMP, Report, barh, bars, bottleneck_dumbbell,
    density, thousands, time_stats, use_dpic_style,
)
from janasunani.analytics.marts import install
from janasunani.olap.lake import connect

__all__ = ["open_lake", "check_note", "Bottlenecks", "ENTRY_OFFICES",
           "ROLE_CASE", "DEPARTMENTS"]

# The four entry routes, used as populations wherever a section cuts by office.
# Governor and police together are a handful of claims in either department and
# would make a panel out of noise, so they join the unnamed group.
ENTRY_OFFICES = [
    ("Collector", "entry_route = 'Forwarded from a District Collector'"),
    ("CM Cell", "entry_route = 'Forwarded from the Chief Minister''s Cell'"),
    ("Direct to dept", "received_directly"),
    ("Other or not recorded",
     "NOT received_directly "
     "AND entry_route NOT IN ('Forwarded from a District Collector', "
     "'Forwarded from the Chief Minister''s Cell')"),
]

# The CA&GR Analytics Note's published figures for 2024-25, statewide. They are
# not about either department: they hold the mart to the published document, so
# a redefinition upstream cannot silently change a note's numbers.
NOTE_2024_25 = {
    "online": 543_890, "offline": 144_411, "Disposed": 449_085,
    "Disposed with benefit": 25_182, "Discarded": 106_828, "Open": 107_206,
}

ROLES = [("District Collector", "district", 30),
         ("Block Development Officer", "block", 30)]


# The two notes' configurations, so a notebook and a written report cannot
# disagree about which department, subcategory or duplicate file they mean.
DEPARTMENTS = {
    "ssepd": dict(
        dept_id=40, dept="SSEPD", subcategory="OAP/ODP/MBPY/WP",
        claim="pension claim",
        dedup_csv="outputs/dedup/ssepd_dedup_groups.csv", prefix="b_",
        roles=[("District Collector", "district", 30),
               ("Block Development Officer", "block", 30),
               ("District social security officer", "district", 30)],
    ),
    "panchayati_raj": dict(
        dept_id=21, dept="Panchayati Raj", subcategory="Rural Housing",
        claim="rural housing claim",
        dedup_csv="outputs/dedup/panchayati_raj_dedup_groups.csv", prefix="pr_",
        roles=[("District Collector", "district", 30),
               ("Block Development Officer", "block", 30),
               ("DRDA Project Director", "district", 30)],
    ),
}


def _sql_str(x: str) -> str:
    """A SQL string literal. Doubled quotes, not raw interpolation.

    "Chief Minister's Grievance Cell" ends the literal otherwise, and the
    failure surfaces as a parser error partway through a generated CASE rather
    than as anything that names the role responsible.
    """
    return "'" + x.replace("'", "''") + "'"


# Roles shortened for a route string only. A table about one role keeps the
# mart's full name; a sequence of five of them does not fit.
ROLE_CASE = "CASE " + " ".join(
    f"WHEN x = {_sql_str(k)} THEN {_sql_str(v)}"
    for k, v in journey.SHORT_ROLE.items()) + " ELSE x END"


def open_lake(memory_limit: str = "8GB"):
    """A connection with the mart installed and the engine bounded.

    Uncapped, DuckDB sizes its buffer pool from total system RAM and never
    spills; an earlier notebook peaked at 55 GB.
    """
    con = connect(tables=["complaints", "action_history"])
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET temp_directory = '{tempfile.gettempdir()}/duckdb_spill'")
    con.execute("SET preserve_insertion_order = false")
    install(con, "grievance_journey")
    use_dpic_style()
    # Route names are long ("State dept > District > Block/field > District")
    # and polars truncates strings at 32 characters by default, which turns the
    # column the route tables exist for into an ellipsis.
    pl.Config.set_fmt_str_lengths(64)
    pl.Config.set_tbl_rows(20)
    return con


def check_note(con) -> None:
    """Validity gate, silent on pass. Called by the notebook, not the class.

    These are the CA&GR Analytics Note's published figures and they run on the
    statewide base, not on either department, so they hold a note to the
    published document even though no note shows the state comparator. The
    second assert is drift against the shipped handoff mart.

    It is a free function rather than a constructor step so that the class can
    be tested over a fixture lake, which by construction cannot reproduce a
    statewide count.
    """
    got = con.execute("""
      SELECT COUNT(*) FILTER (WHERE channel = 'Online')  AS online,
             COUNT(*) FILTER (WHERE channel = 'Offline') AS offline,
             COUNT(*) FILTER (WHERE outcome = 'Disposed') AS "Disposed",
             COUNT(*) FILTER (WHERE outcome = 'Disposed with benefit')
                 AS "Disposed with benefit",
             COUNT(*) FILTER (WHERE outcome = 'Discarded') AS "Discarded",
             COUNT(*) FILTER (WHERE outcome = 'Open') AS "Open"
      FROM grievance_base WHERE year = '2024-25'""").pl().row(0, named=True)
    for k, want in NOTE_2024_25.items():
        assert got[k] == want, f"{k}: {got[k]:,} against the note's {want:,}"

    install(con, "action_type", "handoff")
    assert con.execute(
        "SELECT COUNT(*) FROM handoff_coverage_summary").fetchone()[0] > 0


class Bottlenecks:
    """One department's bottleneck note.

    ``claim`` is the singular lowercase noun for the subcategory Part 2 follows
    ("pension claim"), and every title derives from it, so the two notebooks do
    not each carry a copy of the same sentence with one word changed.
    """

    def __init__(self, con, *, dept_id: int, dept: str, subcategory: str,
                 claim: str, dedup_csv, figdir, prefix: str = "",
                 roles=ROLES, first_fy: int = 2021, last_fy: int = 2024) -> None:
        self.con = con
        self.dept_id, self.dept = dept_id, dept
        self.subcategory, self.claim = subcategory, claim
        self.claims = claim + "s"
        self.Claims = claim[0].upper() + claim[1:] + "s"
        self.roles = list(roles)
        self.first_fy, self.last_fy = first_fy, last_fy
        self.span = f"July {first_fy} to June {last_fy + 1}"

        self._build_g(dedup_csv)
        self._build_funnel()

        self.rep_dept = Report(con, [(dept, "is_primary")], figdir, prefix=prefix)
        self.rep_sub = Report(con, [(self.Claims, "is_primary AND is_sub")],
                              figdir, prefix=prefix)
        self.rep_office = Report(con, ENTRY_OFFICES, figdir, prefix=prefix)
        self.q = self.rep_dept.q

    # -- setup ---------------------------------------------------------------

    def _build_g(self, dedup_csv) -> None:
        """The department's grievances, with repeat filings marked.

        Repeat filings are collapsed when they are the same complaint from the
        same person: candidate pairs share a petitioner identity key (a salted
        hash, never the number) and must still pass text verification. Derived
        from citizen prose, so the mapping stays out of the lake and out of git
        (ROADMAP 3.2); regenerate with
        ``scripts/analysis/dedup_groups_for_slice.py``.
        """
        self.con.register("dedup_groups", pl.read_csv(Path(dedup_csv)))
        self.con.execute(f"""
        CREATE OR REPLACE TABLE g AS
        WITH base AS (
            SELECT * FROM grievance_base
            WHERE dept_id = {self.dept_id}
              AND fy_start BETWEEN {self.first_fy} AND {self.last_fy}),
        ranked AS (
            SELECT b.*, d.duplicate_group_id, d.group_size,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(d.duplicate_group_id, b.ticket_no)
                       ORDER BY b.created_on, b.ticket_no) AS filing_index
            FROM base b LEFT JOIN dedup_groups d USING (ticket_no))
        SELECT *, (filing_index = 1) AS is_primary,
               (subcategory = '{self.subcategory}') AS is_sub
        FROM ranked
        """)

    def _build_funnel(self) -> None:
        sub = "is_primary AND is_sub"
        self.STEPS = {
            "S0": (f"All {self.dept} filings, {self.span}", "TRUE"),
            "S1": ("Distinct claims, repeat filings collapsed", "is_primary"),
            "S2": ("Claims filed in 2024-25", "is_primary AND year = '2024-25'"),
            "S3": ("Disposed, with a usable closing date",
                   "is_primary AND year = '2024-25' AND is_disposed "
                   "AND days_to_close IS NOT NULL"),
            "P0": (f"{self.Claims}, {self.span}", sub),
            "P1": (f"{self.Claims} filed in 2024-25", f"{sub} AND year = '2024-25'"),
            "P2": ("Disposed, with a usable closing date",
                   f"{sub} AND year = '2024-25' AND is_disposed "
                   "AND days_to_close IS NOT NULL"),
        }
        self.FUNNEL = {
            k: self.con.execute(f"SELECT COUNT(*) FROM g WHERE {w}").fetchone()[0]
            for k, (_, w) in self.STEPS.items()}
        self.WHERE = {k: w for k, (_, w) in self.STEPS.items()}

        # A funnel that ever grows is a funnel that has lost track of its base.
        for a, b in [("S0", "S1"), ("S1", "S2"), ("S2", "S3"),
                     ("P0", "P1"), ("P1", "P2")]:
            assert self.FUNNEL[a] >= self.FUNNEL[b], (
                f"{a} ({self.FUNNEL[a]:,}) < {b} ({self.FUNNEL[b]:,})")
        assert self.FUNNEL["P0"] <= self.FUNNEL["S1"], "Part 2 must be a subset"

        # A partition must account for every row in the step it partitions, or
        # an exhibit can drop grievances without anyone noticing.
        for key in ("S2", "P1", "P2"):
            parts = sum(
                self.con.execute(
                    f"SELECT COUNT(*) FROM g WHERE {self.WHERE[key]} AND ({p})"
                ).fetchone()[0] for _, p in ENTRY_OFFICES)
            assert parts == self.FUNNEL[key], (
                f"{key}: parts {parts:,} != {self.FUNNEL[key]:,}")

    # -- funnel presentation -------------------------------------------------

    def note(self, key: str, extra: str = "") -> str:
        """The subtitle stamp every exhibit carries: which sample, and how big."""
        return (f"{key} · {self.FUNNEL[key]:,} — {self.STEPS[key][0]}."
                + (f" {extra}" if extra else ""))

    def funnel_table(self, keys) -> pl.DataFrame:
        keys = list(keys)
        return pl.DataFrame({
            "Step": keys,
            "Sample": [self.STEPS[k][0] for k in keys],
            "Grievances": [self.FUNNEL[k] for k in keys],
            "% of the step above": [
                None if i == 0
                else round(100 * self.FUNNEL[k] / self.FUNNEL[keys[i - 1]], 1)
                for i, k in enumerate(keys)],
        })

    def part1_funnel(self) -> None:
        display(Markdown("**The Part 1 funnel**"))
        display(self.funnel_table(["S0", "S1", "S2", "S3"]))
        display(Markdown(
            "Read down. Every exhibit in Part 1 is stamped with one of these "
            "four codes, so the base is never left to inference."))

    def part2_funnel(self) -> None:
        share = 100 * self.FUNNEL["P0"] / self.FUNNEL["S1"]
        display(Markdown(
            f"{self.Claims} are **{self.FUNNEL['P0']:,} of the "
            f"{self.FUNNEL['S1']:,}** {self.dept} claims in Part 1 "
            f"({share:.0f}%). Part 2 is that subset, narrowed further:"))
        display(self.funnel_table(["P0", "P1", "P2"]))

    # -- Part 1 --------------------------------------------------------------

    def filings_by_year(self) -> None:
        fig, ax = plt.subplots(figsize=(8.0, 3.2))
        d = self.q(f"""SELECT year, COUNT(*) AS n FROM g
                       WHERE {self.WHERE['S0']} GROUP BY 1 ORDER BY 1""")
        bars(ax, list(d["year"]), list(d["n"]), shares=True)
        ax.set_ylabel("Filings")
        thousands(ax)
        self.rep_dept.finish(
            ax, f"Grievances filed against {self.dept}, by year",
            self.note("S0"), save="11_filings_year")

    def filings_by_month(self) -> None:
        fig, ax = plt.subplots(figsize=(8.6, 3.0))
        d = self.q(f"""SELECT DATE_TRUNC('month', created_on) AS m, COUNT(*) AS n
                       FROM g WHERE {self.WHERE['S0']} GROUP BY 1 ORDER BY 1""")
        ax.plot(d["m"], d["n"], color=MAROON, marker="o", markersize=3)
        ax.set_ylabel("Filings")
        ax.xaxis.set_major_locator(mdates.YearLocator(month=7))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        thousands(ax)
        ax.set_ylim(bottom=0)
        self.rep_dept.finish(
            ax, f"Grievances filed against {self.dept}, by month",
            self.note("S0", "Ticks mark the start of each July-June year."),
            save="12_filings_month")

    def dedup_summary(self) -> None:
        d = self.q(f"""SELECT COUNT(*) AS filings,
                    COUNT(*) FILTER (WHERE is_primary) AS claims,
                    COUNT(*) FILTER (WHERE NOT is_primary) AS repeats,
                    COUNT(*) FILTER (WHERE duplicate_group_id IS NULL) AS ungrouped
                  FROM g WHERE {self.WHERE['S0']}""").row(0, named=True)
        display(Markdown(
            f"**{d['filings']:,} filings** collapse to **{d['claims']:,} "
            f"claims**, removing {d['repeats']:,} repeats "
            f"({100 * d['repeats'] / d['filings']:.1f}%). {d['ungrouped']:,} "
            "filings carry no group assignment, because the duplicate index "
            "has no signature for them, and are kept as themselves."))

        display(Markdown("**How many filings each claim attracted**"))
        display(self.q(f"""
          SELECT CASE WHEN group_size IS NULL OR group_size = 1 THEN 'Filed once'
                      WHEN group_size = 2 THEN 'Filed twice'
                      WHEN group_size <= 5 THEN 'Filed 3 to 5 times'
                      ELSE 'Filed more than 5 times' END AS "Claim",
            COUNT(*) FILTER (WHERE is_primary) AS "Claims",
            ROUND(100.0 * COUNT(*) FILTER (WHERE is_primary)
                  / SUM(COUNT(*) FILTER (WHERE is_primary)) OVER (), 1) AS "% of claims",
            COUNT(*) AS "Filings"
          FROM g WHERE {self.WHERE['S0']}
          GROUP BY 1 ORDER BY MIN(COALESCE(group_size, 1))"""))
        display(Markdown(
            "Repeat filing is concentrated. The claims at the bottom of that "
            "table are the people the process has served worst: each extra "
            "filing is somebody deciding the last attempt failed."))

        # How much of the collapse comes from a handful of very large groups.
        # It differs by an order of magnitude between departments, and a group
        # of thousands is a shared intake device rather than a person, so the
        # figure is computed here rather than written into either notebook.
        big = self.q(f"""SELECT COUNT(DISTINCT duplicate_group_id) AS groups,
                           COUNT(*) AS filings, MAX(group_size) AS largest
                         FROM g WHERE {self.WHERE['S0']} AND group_size >= 100
                      """).row(0, named=True)
        if big["groups"]:
            display(Markdown(
                f"{big['groups']} groups hold 100 or more filings each, "
                f"covering {big['filings']:,} filings "
                f"({100 * big['filings'] / d['filings']:.1f}% of the total); "
                f"the largest holds {big['largest']:,}. A group that size is "
                "far more likely to be a shared intake device — one kiosk "
                "number used by many citizens — than one person filing that "
                "often, and inside a group that large the duplicate index "
                "compares against a fixed set of anchors rather than every "
                "pair. Treat the collapse there as an upper bound."))

        display(Markdown("**Filings against claims, by year**"))
        display(self.q(f"""
          SELECT year AS "Year", COUNT(*) AS "Filings",
            COUNT(*) FILTER (WHERE is_primary) AS "Claims",
            COUNT(*) - COUNT(*) FILTER (WHERE is_primary) AS "Repeats removed"
          FROM g WHERE {self.WHERE['S0']} GROUP BY 1 ORDER BY 1"""))
        display(Markdown(
            "**A claim belongs to the year of its first filing.** A case "
            "opened in March 2024 and re-filed in August 2024 is one claim in "
            "2023-24 and does not appear in 2024-25 at all. Everything from "
            "here on counts claims."))

    def _monthly_channels(self, step, rep, title, save, *, figsize, ticks,
                          ylabel="Claims filed", extra=""):
        """Volume by month with the online and offline halves beside it.

        Three series is the palette's hard cap, and this is where it is spent:
        the total alone hides that the two channels move differently.
        """
        fig, ax = plt.subplots(figsize=figsize)
        d = self.q(f"""SELECT DATE_TRUNC('month', created_on) AS m, COUNT(*) AS n,
                    COUNT(*) FILTER (WHERE channel = 'Online')  AS online,
                    COUNT(*) FILTER (WHERE channel = 'Offline') AS offline
                  FROM g WHERE {self.WHERE[step]} GROUP BY 1 ORDER BY 1""")
        for col, colour, label in [("n", MAROON, "All"), ("online", BLUE, "Online"),
                                   ("offline", ORANGE, "Offline")]:
            ax.plot(d["m"], d[col], color=colour, marker="o", markersize=3,
                    label=label)
        if ticks == "months":
            ax.set_xticks(d["m"].to_list())
            ax.set_xticklabels([x.strftime("%b") for x in d["m"]], fontsize=8.5)
        else:
            ax.xaxis.set_major_locator(mdates.YearLocator(month=7))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.set_ylabel(ylabel)
        thousands(ax)
        ax.set_ylim(bottom=0)
        ax.legend(loc="upper left")
        rep.finish(ax, title, self.note(step, extra), save=save)

    def claims_by_month_2425(self) -> None:
        self._monthly_channels(
            "S2", self.rep_dept,
            f"{self.dept} claims filed each month of 2024-25",
            "13_claims_month_2425", figsize=(8.6, 3.2), ticks="months")

    def claims_by_mode(self, step, rep, title, save) -> None:
        fig, ax = plt.subplots(figsize=(7.6, 3.2))
        d = self.q(f"""SELECT mode, channel, COUNT(*) AS n FROM g
                       WHERE {self.WHERE[step]} GROUP BY 1, 2 ORDER BY n DESC""")
        barh(ax, list(d["mode"]), list(d["n"]),
             color=[MAROON if c == "Offline" else BLUE for c in d["channel"]],
             shares=True)
        ax.set_xlabel("Claims")
        rep.finish(ax, title, self.note(step, "Maroon is offline, blue online."),
                   xgrid=True, ygrid=False, save=save)

    def dept_modes(self) -> None:
        self.claims_by_mode("S2", self.rep_dept,
                            "Every route a claim arrives by, 2024-25", "14_modes")

    def sub_modes(self) -> None:
        self.claims_by_mode("P1", self.rep_sub,
                            f"Every route a {self.claim} arrives by, 2024-25",
                            "23_sub_modes")

    def dept_outcomes(self) -> None:
        self.outcomes("S2", self.rep_dept,
                      f"How {self.dept} claims ended, 2024-25", "16_outcomes")

    def sub_outcomes(self) -> None:
        self.outcomes("P1", self.rep_sub, f"How {self.claims} ended, 2024-25",
                      "25_sub_outcomes", figsize=(7.4, 2.5))

    def claims_by_entry_route(self) -> None:
        fig, ax = plt.subplots(figsize=(8.2, 2.9))
        d = self.q(f"""SELECT entry_route, COUNT(*) AS n FROM g
                       WHERE {self.WHERE['S2']} GROUP BY 1 ORDER BY n DESC""")
        barh(ax, list(d["entry_route"]), list(d["n"]), shares=True)
        ax.set_xlabel("Claims")
        self.rep_dept.finish(ax, f"How claims reach {self.dept}, 2024-25",
                             self.note("S2"), xgrid=True, ygrid=False,
                             save="15_entry_route")

    def claims_by_category(self) -> None:
        for lvl, label in [("category", "Category"), ("subcategory", "Subcategory")]:
            display(Markdown(f"**Claims by {label.lower()}, 2024-25**"))
            display(self.q(f"""
              SELECT {lvl} AS "{label}", COUNT(*) AS "Claims",
                ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS "% of claims"
              FROM g WHERE {self.WHERE['S2']}
              GROUP BY 1 ORDER BY "Claims" DESC LIMIT 12"""))

    def outcomes(self, step, rep, title, save, figsize=(7.4, 2.7)) -> None:
        fig, ax = plt.subplots(figsize=figsize)
        d = self.q(f"""SELECT outcome, COUNT(*) AS n FROM g
                       WHERE {self.WHERE[step]} GROUP BY 1 ORDER BY n DESC""")
        barh(ax, list(d["outcome"]), list(d["n"]), shares=True)
        ax.set_xlabel("Claims")
        rep.finish(ax, title, self.note(step), xgrid=True, ygrid=False, save=save)

    _TIME_NAMES = {"n": "Disposed", "median_days": "Median days",
                   "mean_days": "Mean days", "slowest_tenth": "Slowest tenth"}

    def time_overall(self) -> None:
        display(Markdown(f"**Time to close, by year.** {self.note('S3')}"))
        display(self.q(f"""
          SELECT year AS "Year", {time_stats()},
            ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'Open') / COUNT(*), 1)
              AS pct_open
          FROM g WHERE is_primary GROUP BY 1 ORDER BY 1
        """).rename({**self._TIME_NAMES, "pct_open": "% still open"}))

        self.rep_dept.density_grid(
            lambda flag, where: f"""SELECT days_to_close AS v FROM g
                                    WHERE {self.WHERE['S3']} AND {where}""",
            [("", "TRUE")],
            f"How long a {self.dept} claim actually takes, 2024-25",
            self.note("S3", "Curve runs to the 95th percentile; "
                            "median dashed, mean dotted."),
            "17_time_distribution", figsize=(7.0, 2.6))
        display(Markdown(
            "The mean sits well above the median because a minority of claims "
            "run very long. Both are reported throughout for that reason."))

    def time_by_subcategory(self, floor: int = 200, top: int = 12) -> None:
        """The largest subcategories, and how differently they close.

        Ranked by volume, not by mean: a department with forty subcategories
        past the floor would otherwise put its slowest handful at the top of
        the exhibit, all of them small, and bury the ones the caseload is
        actually made of. The span sentence below still quotes every
        subcategory past the floor.
        """
        sub = self.q(f"""
          SELECT subcategory AS "Subcategory",
            COUNT(*) FILTER (WHERE is_disposed AND days_to_close IS NOT NULL)
              AS "Disposed",
            CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close)
                 AS BIGINT) AS "Median days",
            ROUND(AVG(days_to_close), 1) AS "Mean days"
          FROM g WHERE {self.WHERE['S2']} AND subcategory IS NOT NULL
          GROUP BY 1
          -- The floor is on *disposed* claims, not on claims: this exhibit is
          -- about how long a claim takes, and a subcategory with no closed
          -- claim has no answer to give. Panchayati Raj has one with 1,354
          -- claims and no disposals, which under a claims floor put a blank
          -- row on the chart.
          HAVING COUNT(*) FILTER (WHERE is_disposed AND days_to_close IS NOT NULL)
                 >= {floor}
          ORDER BY "Disposed" DESC LIMIT {top}""")
        display(Markdown(f"**Time to close by subcategory.** {self.note('S3')} "
                         f"The {len(sub)} largest subcategories with at least "
                         f"{floor} disposed claims."))
        display(sub)

        fig, ax = plt.subplots(figsize=(8.2, 0.34 * len(sub) + 2.2))
        d = sub.sort("Mean days", descending=True)
        y = list(range(len(d)))
        for i, (m, a) in enumerate(zip(d["Median days"], d["Mean days"])):
            ax.plot([m, a], [i, i], color="#DDDDDD", linewidth=2, zorder=2)
        ax.scatter(d["Median days"], y, s=44, color=BLUE, zorder=3, label="Median")
        ax.scatter(d["Mean days"], y, s=44, color=MAROON, zorder=3, label="Mean")
        for i, a in enumerate(d["Mean days"]):
            ax.text(float(a) + 1.5, i, f"{float(a):.0f}", va="center",
                    fontsize=8.5, color=INK)
        ax.set_yticks(y, list(d["Subcategory"]))
        ax.invert_yaxis()
        ax.set_xlim(0, float(max(d["Mean days"])) * 1.16)
        ax.set_xlabel("Days to close")
        ax.xaxis.grid(True)
        ax.yaxis.grid(False)
        ax.set_axisbelow(True)
        ax.legend(loc="lower right")
        self.rep_dept.finish(
            ax, "Complexity varies by what the claim is about",
            self.note("S3", f"Part 2 follows {self.claims}."),
            xgrid=True, ygrid=False, save="18_subcategory_time")
        span = self.q(f"""SELECT ROUND(MIN(m), 1) AS lo, ROUND(MAX(m), 1) AS hi,
                            COUNT(*) AS n
                          FROM (SELECT AVG(days_to_close) AS m FROM g
                                WHERE {self.WHERE['S2']} AND subcategory IS NOT NULL
                                GROUP BY subcategory
                                HAVING COUNT(*) FILTER (
                                    WHERE is_disposed AND days_to_close IS NOT NULL)
                                    >= {floor})""").row(0, named=True)
        display(Markdown(
            f"Mean time to close runs from {span['lo']} to {span['hi']} days "
            f"across the {span['n']} subcategories past the floor. An office "
            "holding mostly one kind of claim "
            "will look faster or slower than an office holding mostly another, "
            "whatever either is doing."))

    # -- Part 2 --------------------------------------------------------------

    def sub_over_time(self) -> None:
        self._monthly_channels(
            "P0", self.rep_sub,
            f"{self.Claims} filed each month, {self.span}",
            "21_sub_month_full", figsize=(8.6, 3.0), ticks="years",
            extra="Ticks mark the start of each July-June year.")

        fig, ax = plt.subplots(figsize=(7.6, 3.0))
        d = self.q(f"""SELECT year, COUNT(*) AS n FROM g
                       WHERE {self.WHERE['P0']} GROUP BY 1 ORDER BY 1""")
        bars(ax, list(d["year"]), list(d["n"]), shares=True)
        ax.set_ylabel("Claims")
        thousands(ax)
        self.rep_sub.finish(ax, f"{self.Claims} by year", self.note("P0"),
                            save="22_sub_year")

    def sub_entry(self) -> None:
        display(Markdown(f"**Where {self.claims} enter.** {self.note('P1')}"))
        display(self.q(f"""
          SELECT entry_route AS "Entry point", COUNT(*) AS "Claims",
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS "% of claims"
          FROM g WHERE {self.WHERE['P1']} GROUP BY 1 ORDER BY "Claims" DESC"""))

        fig, ax = plt.subplots(figsize=(8.2, 2.7))
        d = self.q(f"""SELECT CASE {' '.join(
                f"WHEN {p} THEN '{lab}'" for lab, p in ENTRY_OFFICES)} END AS grp,
                    COUNT(*) AS n
                  FROM g WHERE {self.WHERE['P1']} GROUP BY 1 ORDER BY n DESC""")
        barh(ax, list(d["grp"]), list(d["n"]), shares=True)
        ax.set_xlabel("Claims")
        self.rep_sub.finish(ax, "The four entry points used through the rest of Part 2",
                            self.note("P1"), xgrid=True, ygrid=False,
                            save="24_entry_groups")

        # How much of the caseload the unnamed group carries decides how much
        # weight the sections after this one can bear, and it differs sharply
        # between departments, so it is computed rather than written down.
        unrec = self.q(f"""SELECT COUNT(*) AS n FROM g
                           WHERE {self.WHERE['P1']}
                             AND entry_route = 'Entry point not recorded'""")["n"][0]
        share = 100 * unrec / self.FUNNEL["P1"]
        display(Markdown(
            f"**{unrec:,} {self.claims} ({share:.1f}%) record no entry point at "
            "all.** That group is kept rather than dropped, because dropping "
            "part of the caseload to tidy a chart would change every number "
            "after it. But it is not a place — it is the absence of one — so "
            "where it behaves differently from the named entry points, that is "
            "as likely to be a recording artefact as a finding about how "
            "grievances travel. It is labelled, never explained."))

    def sub_time(self) -> None:
        display(Markdown(f"**Time to close {self.claims}.** {self.note('P2')}"))
        display(self.q(f"""
          SELECT year AS "Year", {time_stats()}
          FROM g WHERE {self.WHERE['P0']} GROUP BY 1 ORDER BY 1
        """).rename(self._TIME_NAMES))

        self.rep_sub.density_grid(
            lambda flag, where: f"""SELECT days_to_close AS v FROM g
                                    WHERE {self.WHERE['P2']} AND {where}""",
            [("", "TRUE")],
            f"How long a {self.claim} actually takes, 2024-25",
            self.note("P2", "Curve runs to the 95th percentile; "
                            "median dashed, mean dotted."),
            "26_sub_time_distribution", figsize=(7.0, 2.6))

    def entry_time_table(self) -> pl.DataFrame:
        """One row per entry point, one column per statistic.

        Offices down the side rather than across the top: the reader is
        comparing four offices on the same four numbers, and a table that puts
        the offices in the header makes that a sideways scan.
        """
        return self.rep_office.side_by_side(
            lambda flag: f"""SELECT {time_stats()} FROM g
                             WHERE {self.WHERE['P2']} AND {flag}"""
        ).rename({"Population": "Entry point", **self._TIME_NAMES})

    def time_by_entry(self) -> None:
        display(Markdown(f"**Time to close by entry point.** {self.note('P2')}"))
        display(self.entry_time_table())

        self.rep_office.density_grid(
            lambda flag, where: f"""SELECT days_to_close AS v FROM g
                                    WHERE {self.WHERE['P2']} AND {flag}
                                      AND {where}""",
            [("", "TRUE")],
            "Time to close by where the claim entered",
            self.note("P2", "One panel per entry point; each carries its own n. "
                            "Median dashed, mean dotted."),
            "27_time_by_entry", figsize=(6.4, 8.0))

    # -- routes and phases ---------------------------------------------------

    def install_journey(self) -> None:
        """Materialise the per-grievance journey tables and open step P3."""
        journey.install_steps(self.con, self.WHERE["P1"])
        journey.install_route(self.con)
        journey.install_office_wait(self.con)
        journey.install_returns(self.con)

        self.FUNNEL["P3"] = self.q(
            f"""SELECT COUNT(*) AS n FROM route r JOIN g USING (ticket_no)
                WHERE {self.WHERE['P2']}""")["n"][0]
        self.STEPS["P3"] = ("Dated history, every office on the rung ladder", None)
        self.WHERE["P3"] = self.WHERE["P2"]
        assert self.FUNNEL["P3"] <= self.FUNNEL["P2"], (
            "route coverage cannot exceed its own base")
        display(Markdown(
            f"**P3 · {self.FUNNEL['P3']:,}** of the {self.FUNNEL['P2']:,} "
            f"disposed {self.claims} "
            f"({100 * self.FUNNEL['P3'] / self.FUNNEL['P2']:.0f}%) have a route "
            "the ladder recognises end to end. The rest contain at least one "
            "office the ladder does not name and are excluded from sections "
            "2.7 to 2.11."))

    def _route_sql(self, flag: str, limit: int, pct_label: str) -> str:
        return f"""
          SELECT LIST_REDUCE(LIST_TRANSFORM(r.roles, x -> {ROLE_CASE}),
                             (a, b) -> a || ' > ' || b) AS "Route",
            COUNT(*) AS "Claims",
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS "{pct_label}",
            CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY g.days_to_close)
                 AS BIGINT) AS "Median days",
            ROUND(AVG(g.days_to_close), 1) AS "Mean days"
          FROM route r JOIN g USING (ticket_no)
          WHERE {self.WHERE['P2']} AND {flag}
          -- Route breaks the tie, so a table with several equal-sized routes
          -- at its cut-off is the same table on the next run.
          GROUP BY 1 ORDER BY "Claims" DESC, "Route" LIMIT {limit}"""

    def routes(self) -> None:
        routes = self.q(self._route_sql("TRUE", 8, "% of claims"))
        display(Markdown(
            f"**The routes {self.claims} travel most often.** "
            f"P3 · {self.FUNNEL['P3']:,} claims. The eight most common are "
            f"listed, covering {routes['Claims'].sum():,} of them."))
        display(routes)

        fig, axes = plt.subplots(1, 2, figsize=(12.4, 3.2))
        top = routes.head(6)
        barh(axes[0], list(top["Route"]), list(top["Claims"]),
             shares=[float(p) for p in top["% of claims"]])
        axes[0].set_xlabel("Claims")
        axes[0].set_title("Volume", fontsize=10, loc="left", color=INK, pad=6)
        barh(axes[1], list(top["Route"]), [float(v) for v in top["Mean days"]])
        axes[1].set_xlabel("Mean days to close")
        axes[1].set_title("Time", fontsize=10, loc="left", color=INK, pad=6)
        for ax in axes:
            ax.xaxis.grid(True)
            ax.yaxis.grid(False)
            ax.set_axisbelow(True)
        self.rep_sub.fig_title(
            fig, "The six commonest routes: how many, and how long",
            f"P3 · {self.FUNNEL['P3']:,} claims. Longer routes are not "
            "automatically slower; the shape of the journey matters more than "
            "its length.")
        self.rep_sub.save(fig, "28_routes")
        plt.show()

    def routes_by_entry(self, top: int = 3) -> None:
        """One table per entry point, not one table with a column naming it.

        The reader is asking what the journey looks like from each starting
        point, which is four separate readings; stacked into one frame the
        route names repeat and the eye has to re-find the boundary between
        offices on every row.
        """
        display(Markdown(
            f"**The {top} commonest routes for each entry point.** "
            f"P3, partitioned · {self.FUNNEL['P3']:,} claims in total. Each "
            "table stands on its own entry point's claims, and the share is "
            "within that entry point."))
        for label, flag in ENTRY_OFFICES:
            n = self.q(f"""SELECT COUNT(*) AS n FROM route r JOIN g USING (ticket_no)
                           WHERE {self.WHERE['P2']} AND {flag}""")["n"][0]
            df = self.q(self._route_sql(flag, top, "% within entry point"))
            display(Markdown(
                f"**{label}** · {n:,} claims, of which the {len(df)} routes "
                f"below cover {df['Claims'].sum():,} "
                f"({100 * df['Claims'].sum() / n:.0f}%)." if n else
                f"**{label}** · no claims with a recognised route."))
            if n:
                display(df)

        fig, pairs = self.rep_office.panels(lambda flag: f"""
          SELECT r.offices_touched AS offices, COUNT(*) AS n,
            ROUND(AVG(g.days_to_close), 1) AS mean_days
          FROM route r JOIN g USING (ticket_no)
          WHERE {self.WHERE['P2']} AND {flag}
          GROUP BY 1 ORDER BY 1""", figsize=(13.0, 2.9))
        for ax, name, d in pairs:
            bars(ax, [str(v) for v in d["offices"]], list(d["n"]), shares=True)
            ax.set_title(f"{name}  ({d['n'].sum():,})", fontsize=10, loc="left",
                         color=INK, pad=6)
            ax.set_xlabel("Distinct offices that held the claim")
            ax.set_ylabel("Claims")
            ax.yaxis.grid(True)
            ax.set_axisbelow(True)
        self.rep_office.finish_panels(
            fig, "How many desks a claim passes, by where it entered",
            f"P3, partitioned · {self.FUNNEL['P3']:,} claims.",
            save="29_levels_by_entry")

    def define_phases(self) -> None:
        """Open steps P3 and P4, before any exhibit stands on either.

        Everything after this compares the same five spans over the same
        claims, cut three ways: the subcategory whole, by where the claim
        entered, and by the route it took. Defining the decomposition once,
        here, is what lets those three be read against each other.
        """
        self.install_journey()
        journey.install_phase_bounds(self.con)
        chk = journey.install_phases(self.con, self.WHERE["P1"])
        # The route travels with the phases, so a decomposition can be cut by
        # it without rebuilding the join at each call site.
        self.con.execute(f"""
        CREATE OR REPLACE VIEW phases_pop AS
        SELECT p.*, g.entry_route, g.received_directly,
               LIST_REDUCE(LIST_TRANSFORM(r.roles, x -> {ROLE_CASE}),
                           (a, b) -> a || ' > ' || b) AS route
        FROM phases_clean p JOIN g USING (ticket_no)
        LEFT JOIN route r USING (ticket_no)""")
        self.FUNNEL["P4"] = chk["tiling_rows"]
        self.STEPS["P4"] = ("The five phase spans tile the total", None)
        self.WHERE["P4"] = "TRUE"
        assert self.FUNNEL["P4"] <= self.FUNNEL["P2"]

        display(Markdown(
            "**What the phase sample leaves out, and what it does to the average**"))
        display(self.q(f"""
          SELECT UNNEST(['P2 · disposed, usable closing date',
                         'P3 · dated history, every office recognised',
                         'P4 · the five spans tile — used below']) AS "Step",
            UNNEST([{self.FUNNEL['P2']}, {chk['all_rows']},
                    {chk['tiling_rows']}]) AS "Claims",
            UNNEST([
              (SELECT ROUND(AVG(days_to_close), 1) FROM g WHERE {self.WHERE['P2']}),
              (SELECT ROUND(AVG(days_to_close), 1) FROM phases),
              (SELECT ROUND(AVG(days_to_close), 1) FROM phases_clean)]) AS "Mean days",
            UNNEST([
              (SELECT CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close)
                      AS BIGINT) FROM g WHERE {self.WHERE['P2']}),
              (SELECT CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close)
                      AS BIGINT) FROM phases),
              (SELECT CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close)
                      AS BIGINT) FROM phases_clean)]) AS "Median days"
        """))

        lost = self.q("""SELECT COUNT(*) AS n, ROUND(AVG(days_to_close), 1) AS mean_days
                         FROM phases p
                         WHERE p.registration + p.first_assignment + p.field_action
                             + p.review + p.closure <> p.days_to_close
                      """).row(0, named=True)
        kept = self.q("SELECT ROUND(AVG(days_to_close), 1) AS m FROM phases_clean")["m"][0]
        display(Markdown(
            f"The phase averages below add to the mean of **P4** ({kept} days), "
            f"not to the P2 figure in section 2.5. {lost['n']:,} claims "
            f"({100 * lost['n'] / chk['all_rows']:.0f}%) fail the tiling test, "
            "every one of them because its last recorded action is dated "
            "*after* its recorded closing date, so forcing the spans forward "
            f"pushes them past the total. They are slower than the ones kept "
            f"({lost['mean_days']} days against {kept}), so this decomposition "
            "is mildly optimistic. The aggregate figure below remains the one "
            f"to quote for how long a {self.claim} takes."))

    def phase_means(self) -> tuple[dict, float]:
        """The five mean spans and their total, asserted to agree.

        Returned rather than only drawn, so a written report quotes the same
        numbers as the chart.
        """
        P = journey.PHASES
        kept = self.q("SELECT ROUND(AVG(days_to_close), 1) AS m FROM phases_clean")["m"][0]
        means = self.q(f"""SELECT {', '.join(f'AVG({p}) AS {p}' for p in P)}
                           FROM phases_clean""").row(0, named=True)
        total = sum(float(means[p]) for p in P)
        assert abs(total - float(kept)) <= 0.15, (
            f"phases sum to {total}, total is {kept}")
        return means, total

    def phases_aggregate(self) -> None:
        """The decomposition over the whole subcategory: the first of three cuts."""
        P, LBL = journey.PHASES, journey.PHASE_LABEL
        means, total = self.phase_means()

        fig, ax = plt.subplots(figsize=(8.4, 2.3))
        left = 0.0
        for i, p in enumerate(P):
            v = float(means[p])
            ax.barh([0], [v], left=left, color=RAMP[i % len(RAMP)], height=0.5,
                    zorder=3)
            if v > total * 0.04:
                ax.text(left + v / 2, 0,
                        f"{LBL[p]}\n{v:.0f}d ({100 * v / total:.0f}%)",
                        ha="center", va="center", fontsize=8.5,
                        color="white" if i < 3 else INK)
            left += v
        ax.set_yticks([])
        ax.set_xlabel("Mean days")
        ax.grid(False)
        self.rep_sub.finish(
            ax, f"Where the time goes on a {self.claim}",
            f"P4 · {self.FUNNEL['P4']:,} claims. Means, so the five spans add "
            f"to the {total:.0f}-day average.", ygrid=False, save="30_phases")

        display(Markdown(
            f"**Mean and median days per phase.** P4 · {self.FUNNEL['P4']:,} "
            "claims in every row — review is zero, not missing, for a claim "
            "never sent back up, so the denominator does not change."))
        display(self.q(f"""
          SELECT UNNEST([{', '.join(repr(LBL[p]) for p in P)}]) AS "Phase",
            UNNEST([{', '.join(f'ROUND(AVG({p}), 1)' for p in P)}]) AS "Mean days",
            UNNEST([{', '.join(f'ROUND(100.0 * AVG({p}) / {total}, 1)' for p in P)}])
              AS "% of total",
            UNNEST([{', '.join(f'CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {p}) AS BIGINT)' for p in P)}])
              AS "Median days"
          FROM phases_clean"""))

        fig, axes = plt.subplots(1, 5, figsize=(13.0, 2.6))
        for c, p in enumerate(P):
            ax = axes[c]
            vals = self.q(f"SELECT {p} AS v FROM phases_clean")["v"].to_list()
            arr = np.asarray([float(v) for v in vals if v is not None])
            if arr.size and float(np.percentile(arr, 95)) < 1:
                # A smoothed curve over a 0-1 axis is an artefact of the axis.
                ax.text(0.5, 0.55, f"{100 * float((arr == 0).mean()):.0f}%\nsame day",
                        transform=ax.transAxes, ha="center", va="center",
                        fontsize=11, color=MAROON, fontweight="bold")
                ax.set_xticks([])
            else:
                density(ax, vals, colour=MAROON)
            ax.set_title(LBL[p], fontsize=9, loc="left", color=INK, pad=3)
            ax.set_yticks([])
            ax.grid(False)
            for s in ("top", "right", "left"):
                ax.spines[s].set_visible(False)
            ax.tick_params(labelsize=7.5)
            ax.set_xlabel("days", fontsize=8)
        self.rep_sub.finish_panels(
            fig, "The shape of each phase, not just its middle",
            f"P4 · {self.FUNNEL['P4']:,} claims. Curves run to each phase's "
            "95th percentile; median dashed, mean dotted.",
            save="31_phase_distributions")

    def _phase_rows(self, groups, floor: int):
        """Mean phase spans per group, dropping groups below the floor.

        The assert is the point: a group whose five spans do not add to its own
        mean has been mis-tiled, and a stacked bar would hide that by drawing
        whatever it was given.
        """
        P = journey.PHASES
        rows = []
        for lab, pred in groups:
            r = self.q(f"""SELECT COUNT(*) AS n, ROUND(AVG(days_to_close), 1) AS total,
                        {', '.join(f'ROUND(AVG({p}), 1) AS {p}' for p in P)}
                      FROM phases_pop WHERE {pred}""").row(0, named=True)
            if r["n"] < floor:
                continue
            parts = sum(float(r[p]) for p in P)
            assert abs(parts - float(r["total"])) <= 0.15, (
                f"{lab}: phases sum to {parts}, total is {r['total']}")
            rows.append((lab, r))
        return rows

    def _phase_stack(self, rows, label_col: str, title: str, subtitle: str,
                     save: str) -> None:
        P, LBL = journey.PHASES, journey.PHASE_LABEL
        display(pl.DataFrame({
            label_col: [lab for lab, _ in rows],
            "Claims": [r["n"] for _, r in rows],
            **{LBL[p]: [float(r[p]) for _, r in rows] for p in P},
            "Total": [float(r["total"]) for _, r in rows],
        }))

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
            for i, p in enumerate(P):
                v = float(r[p])
                ax.barh([j], [v], left=left, color=RAMP[i % len(RAMP)],
                        height=0.6, zorder=3, label=LBL[p] if j == 0 else None)
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
                  ncol=5, fontsize=8)
        self.rep_sub.finish(ax, title, subtitle, ygrid=False, save=save)

    def phases_by_entry(self, floor: int = 100) -> None:
        """The second cut: the same five spans, by where the claim entered."""
        rows = self._phase_rows(ENTRY_OFFICES, floor)
        display(Markdown(
            f"**Mean days per phase, by entry point.** P4, partitioned · "
            f"{self.FUNNEL['P4']:,} claims. Entry points with fewer than "
            f"{floor} claims are omitted; the spans sum to each group's own mean."))
        self._phase_stack(
            rows, "Entry point", "Where the time goes, by where the claim entered",
            f"P4, partitioned · {self.FUNNEL['P4']:,} claims. Means, so each "
            "bar adds to that group's average.", "32_phases_by_entry")

    def phases_by_route(self, top: int = 6, floor: int = 100) -> None:
        """The third cut: the same five spans, by the route the claim took.

        A route is a sequence of offices, so this is the exhibit that says
        *where along the journey* a longer route spends its extra time, rather
        than only that it takes longer.
        """
        common = self.q(f"""SELECT route, COUNT(*) AS n FROM phases_pop
                            WHERE route IS NOT NULL
                            GROUP BY 1 HAVING COUNT(*) >= {floor}
                            ORDER BY n DESC, route LIMIT {top}""")
        groups = [(r, f"route = {_sql_str(r)}") for r in common["route"]]
        rows = self._phase_rows(groups, floor)
        covered = sum(r["n"] for _, r in rows)
        display(Markdown(
            f"**Mean days per phase, by route.** P4, partitioned · "
            f"{self.FUNNEL['P4']:,} claims, of which the {len(rows)} routes "
            f"below carry {covered:,} ({100 * covered / self.FUNNEL['P4']:.0f}%). "
            f"Routes with fewer than {floor} claims are omitted; the spans sum "
            "to each route's own mean."))
        self._phase_stack(
            rows, "Route", "Where the time goes, by the route the claim took",
            f"P4, partitioned · {covered:,} claims on the {len(rows)} commonest "
            "routes. Means, so each bar adds to that route's average.",
            "33_phases_by_route")

    # -- nodes ---------------------------------------------------------------

    def level_table(self) -> pl.DataFrame:
        """Wait by level of government, one row per level.

        Split out from `level_wait` so a written report takes the same numbers
        the chart is drawn from rather than a second query that could drift.
        """
        lvl = self.q(f"""
          SELECT s.rung, COUNT(*) AS handoffs
          FROM (SELECT ticket_no, rung, action_taken_date,
                  LEAD(action_taken_date) OVER (PARTITION BY ticket_no
                                                ORDER BY action_taken_date, id) AS nxt
                FROM steps WHERE rung IS NOT NULL) s
          JOIN g USING (ticket_no)
          WHERE {self.WHERE['P2']} AND s.nxt IS NOT NULL
          GROUP BY 1 ORDER BY 1""")
        lvl_time = self.q(f"""
          SELECT s.rung,
            CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.gap) AS BIGINT)
              AS median_days,
            ROUND(AVG(s.gap), 1) AS mean_days,
            CAST(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY s.gap) AS BIGINT)
              AS slowest_tenth
          FROM (SELECT ticket_no, rung,
                  CAST(LEAD(action_taken_date) OVER (PARTITION BY ticket_no
                         ORDER BY action_taken_date, id) AS DATE)
                  - CAST(action_taken_date AS DATE) AS gap
                FROM steps WHERE rung IS NOT NULL) s
          JOIN g USING (ticket_no)
          WHERE {self.WHERE['P2']} AND s.gap IS NOT NULL
          GROUP BY 1 ORDER BY 1""")
        level = (lvl.join(lvl_time, on="rung")
                 .with_columns(Level=pl.col("rung").replace_strict(journey.RUNG_NAME))
                 .select(["Level", "handoffs", "median_days", "mean_days",
                          "slowest_tenth"])
                 .rename({"handoffs": "Hand-offs", "median_days": "Median days",
                          "mean_days": "Mean days", "slowest_tenth": "Slowest tenth"}))
        return level

    def level_wait(self, floor: int = 100) -> None:
        level = self.level_table()
        total = level["Hand-offs"].sum()
        display(Markdown(f"**By level of government.** P3 · {self.FUNNEL['P3']:,} "
                         f"claims, {total:,} hand-offs."))
        display(level)

        # A level with a handful of hand-offs is shown in the table and kept
        # out of the ranking: Panchayati Raj records 18 sub-district hand-offs,
        # which would otherwise place second on a mean drawn from 18 numbers.
        thin = level.filter(pl.col("Hand-offs") < floor)
        d = level.filter(pl.col("Hand-offs") >= floor).sort("Mean days",
                                                            descending=True)
        fig, ax = plt.subplots(figsize=(8.2, 2.9))
        barh(ax, list(d["Level"]), [float(v) for v in d["Mean days"]],
             shares=[100 * float(h) / total for h in d["Hand-offs"]], fmt="{:,.0f}d")
        ax.set_xlabel("Mean days before the next recorded step")
        thin_note = ("" if thin.is_empty() else " " + "; ".join(
            f"{r['Level']} ({r['Hand-offs']:,}) is not ranked"
            for r in thin.iter_rows(named=True)) + ".")
        self.rep_sub.finish(
            ax, "How long a claim waits at each level of government",
            f"P3 · {total:,} hand-offs. In parentheses, that level's share of "
            "all hand-offs." + thin_note,
            xgrid=True, ygrid=False, save="34_level_wait")
        display(Markdown(
            "A level can be slow and rare, or quick and everywhere. The share "
            "in parentheses is what separates a bottleneck worth an "
            "intervention from one that would barely move the average."))

    def nodes(self, role: str, floor: int, worst: bool = True, n: int = 10):
        return self.q(f"""
          SELECT w.role_name || ', ' || w.place AS "Office", COUNT(*) AS "Hand-offs",
            CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY w.wait_days) AS BIGINT)
              AS "Median days",
            ROUND(AVG(w.wait_days), 1) AS "Mean days",
            CAST(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY w.wait_days) AS BIGINT)
              AS "Slowest tenth"
          FROM office_wait w JOIN g USING (ticket_no)
          WHERE {self.WHERE['P2']} AND w.role_name = '{role}'
          GROUP BY 1 HAVING COUNT(*) >= {floor}
          ORDER BY "Mean days" {'DESC' if worst else 'ASC'}, "Office" LIMIT {n}""")

    def node_tables(self, roles=None) -> None:
        for role, geo, floor in (roles or self.roles):
            tot = self.q(f"""SELECT COUNT(*) AS n, COUNT(DISTINCT w.place) AS places
                             FROM office_wait w JOIN g USING (ticket_no)
                             WHERE {self.WHERE['P2']} AND w.role_name = '{role}'
                          """).row(0, named=True)
            display(Markdown(
                f"### {role}s, by {geo}\n\n"
                f"P3 · {tot['n']:,} hand-offs across {tot['places']} {geo}s; "
                f"those with at least {floor} hand-offs are ranked."))
            display(Markdown(f"**Slowest {role.lower()}s**"))
            display(self.nodes(role, floor, worst=True))
            display(Markdown(f"**Fastest {role.lower()}s**"))
            display(self.nodes(role, floor, worst=False))

    def node_charts(self, roles=None) -> None:
        roles = roles or self.roles
        fig, axes = plt.subplots(1, len(roles), figsize=(5.9 * len(roles), 4.2),
                                 squeeze=False)
        bottleneck_dumbbell(axes[0], [(f"{role}s — slowest", self.nodes(role, floor))
                                      for role, _, floor in roles])
        self.rep_sub.fig_title(
            fig, f"The individual offices where {self.claims} wait longest",
            "P3 · unit is hand-offs, not claims. Ordered on the mean, with the "
            "median beside it: a high mean can come from an office that is "
            "usually quick and occasionally catastrophic, or one that is "
            "uniformly slow, and those need different fixes.")
        self.rep_sub.save(fig, "35_slowest_nodes")
        plt.show()

        fig, axes = plt.subplots(1, len(roles), figsize=(5.9 * len(roles), 4.2),
                                 squeeze=False)
        bottleneck_dumbbell(
            axes[0], [(f"{role}s — fastest", self.nodes(role, floor, worst=False))
                      for role, _, floor in roles])
        self.rep_sub.fig_title(
            fig, "And the offices where they wait least",
            "P3 · unit is hand-offs. These are the comparison cases: same "
            "role, same kind of claim, a fraction of the wait.")
        self.rep_sub.save(fig, "36_fastest_nodes")
        plt.show()
