"""One short Word document over both bottleneck notes. Internal.

The two per-department notes are the evidence; this is the short version that
says what we found across both, what to ask the department, and what to study
next. Bullets only.

    uv run python scripts/analysis/summary_docx.py

Every count and duration is pulled live from `janasunani.analytics.bottlenecks`
-- the same module both notebooks run -- so this cannot quote a figure the
analysis does not produce. The one exception is the regression block, which
needs statsmodels; those coefficients are held in `REG` below with the script
that produced them named beside each set.

**Sample discipline.** The notebooks' rule is that every exhibit names the
funnel step it stands on and prints its n, because the sample narrows five
times across this analysis. Captions here carry the step, and `check_samples`
asserts the tables really do sum back to that step rather than trusting the
caption. Two kinds of table change the unit from the case to the hand-off;
those say so and are excluded from the sum check.

Output goes to `outputs/reports/`, which is gitignored: it carries figures and
counts derived from citizen grievances and is not a repository artifact.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from janasunani.analytics import journey
from janasunani.analytics.bottlenecks import (
    DEPARTMENTS, ENTRY_OFFICES, Bottlenecks, check_note, open_lake,
)

# Reuse the document furniture from the per-department note rather than a
# second copy of it: same type, same maroon, same table treatment, so the three
# documents read as one set.
# Run as a script, so this directory is already sys.path[0].
from bottleneck_report_docx import (
    FIGDIR, INK_SOFT, MAROON, OUTDIR, UNRECORDED, _styles, bullets, exhibit,
    para, table,
)

REPO = Path(__file__).resolve().parents[2]

# The one desk the rural-housing gap turned out to be. Named here because four
# queries use it and a typo in any of them would silently select nothing --
# which would read as "no desk effect" rather than as a bug.
PR_DESK = ("Department Secretary", "Panchayati Raj & DW")


# --- the regression, from scripts run in an ephemeral statsmodels env --------
#
# statsmodels is not a project dependency. These come from `reg_any.py` over
# `reg_<dept>.parquet`, which `build_reg2.py` assembles from this same `g`
# table joined to `phases_clean` and the text-marker export. Recorded here
# because the document cannot import statsmodels; regenerate with:
#
#   uv run python build_reg2.py <dept> <markers.csv>
#   uv run --with statsmodels --with lifelines --no-project python \
#       reg_any.py reg_<dept>.parquet --fy 2024-25
#
# The sample is P1 restricted to cases with a recorded block, closed only.
# Each spec is (label, coefficient on direct entry, standard error, R-squared).
REG = {
    "panchayati_raj": dict(
        n=115_505, blocks=381,
        specs=[("All cases", 98.67, 0.48, 0.381),
               ("Same district", 96.25, 4.42, 0.426),
               ("Same block", 97.44, 3.01, 0.503),
               ("Same block and month", 99.26, 2.74, 0.548),
               ("Same block, month and case type", 99.63, 2.73, 0.550)],
        open_direct=19.6, open_coll=2.0,
    ),
    "ssepd": dict(
        n=3_588, blocks=349,
        specs=[("All cases", 4.03, 2.25, 0.001),
               ("Same district", 1.11, 3.99, 0.151),
               ("Same block", -2.08, 3.48, 0.302),
               ("Same block and month", -19.21, 3.98, 0.452),
               ("Same block, month and case type", -22.43, 4.25, 0.456)],
        open_direct=2.6, open_coll=10.2,
    ),
}


def load(key: str) -> tuple[Bottlenecks, dict]:
    """The department's mart, with the journey and office-wait tables built.

    `install_office_wait` takes no scope and covers the whole portal, so every
    query below joins it back to `g` under this department's own filter. A
    table taken straight off it mixes police stations and Tahasildars into
    what is meant to be a pension caseload -- that mistake was made once.
    """
    cfg = dict(DEPARTMENTS[key])
    prefix = cfg.pop("prefix")
    cfg["dedup_csv"] = REPO / cfg["dedup_csv"]
    con = open_lake()
    check_note(con)
    b = Bottlenecks(con, figdir=FIGDIR, prefix=prefix, **cfg)
    b.define_phases()
    b.install_journey()
    journey.install_office_wait(con)
    return b, {"prefix": prefix, **cfg}


def _case() -> str:
    return "CASE " + " ".join(
        f"WHEN {p} THEN '{lab}'" for lab, p in ENTRY_OFFICES) + " END"


def entry_rows(b: Bottlenecks) -> pl.DataFrame:
    """Cases and timing per entry point, plus how many carry no block. Step P1.

    The block column is here because it is the finding in pensions: the slow
    group is slow *and* unlocatable, which is a records problem, not a route.
    """
    return b.q(f"""
      SELECT {_case()} AS "Entry point", COUNT(*) AS "Cases",
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS "% of cases",
        ROUND(100.0 * SUM((c.block IS NULL)::INT) / COUNT(*), 1) AS "% no block",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close)
             AS BIGINT) AS "Median days",
        ROUND(AVG(days_to_close), 1) AS "Mean days"
      FROM g JOIN complaints c USING (ticket_no)
      WHERE {b.WHERE['P1']}
      GROUP BY 1 ORDER BY "Cases" DESC""")


def dedup_row(b: Bottlenecks) -> dict:
    """Step S0 to S1: what collapsing repeat filings removes."""
    return b.q(f"""SELECT COUNT(*) AS filings,
                COUNT(*) FILTER (WHERE is_primary) AS claims,
                COUNT(*) FILTER (WHERE NOT is_primary) AS repeats
              FROM g WHERE {b.WHERE['S0']}""").row(0, named=True)


def desk_predicate(role: str, place: str) -> str:
    q = "'"
    return f"w.role_name = '{role}' AND w.place = '{place.replace(q, q * 2)}'"


def desk_pass(b: Bottlenecks, role: str, place: str) -> pl.DataFrame:
    """Which entry groups pass one named desk at all. Step P1."""
    return b.q(f"""
      SELECT {_case()} AS "Entry point", COUNT(*) AS "Cases",
        SUM((t.ticket_no IS NOT NULL)::INT) AS "Pass this desk",
        ROUND(100.0 * SUM((t.ticket_no IS NOT NULL)::INT) / COUNT(*), 1) AS "%"
      FROM g LEFT JOIN (SELECT DISTINCT w.ticket_no FROM office_wait w
                        WHERE {desk_predicate(role, place)}) t USING (ticket_no)
      WHERE {b.WHERE['P1']} GROUP BY 1 ORDER BY "Cases" DESC""")


def close_by_desk(b: Bottlenecks, role: str, place: str) -> pl.DataFrame:
    """Time to close by whether the case passed one desk. Step P1."""
    return b.q(f"""
      SELECT CASE WHEN t.ticket_no IS NOT NULL THEN 'Passed this desk'
                  ELSE 'Did not' END AS "Case", COUNT(*) AS "Cases",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close)
             AS BIGINT) AS "Median days",
        ROUND(AVG(days_to_close), 1) AS "Mean days"
      FROM g LEFT JOIN (SELECT DISTINCT w.ticket_no FROM office_wait w
                        WHERE {desk_predicate(role, place)}) t USING (ticket_no)
      WHERE {b.WHERE['P1']} GROUP BY 1 ORDER BY "Cases" DESC""")


def desk_wait(b: Bottlenecks, role: str, place: str) -> dict:
    """One desk's wait. Unit is the hand-off, over P1 cases."""
    return b.q(f"""
      SELECT COUNT(*) AS handoffs, COUNT(DISTINCT w.ticket_no) AS cases,
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY w.wait_days)
             AS BIGINT) AS median_wait, ROUND(AVG(w.wait_days), 1) AS mean_wait
      FROM office_wait w JOIN g USING (ticket_no)
      WHERE {b.WHERE['P1']} AND {desk_predicate(role, place)}
        AND w.wait_days IS NOT NULL""").row(0, named=True)


def peer_desks(b: Bottlenecks, role: str, floor: int = 30) -> pl.DataFrame:
    """Every desk holding the same role in this caseload. Hand-offs over P1.

    The column is the gap before the *next* office acts, not this desk's own
    holding time -- `office_wait` cannot measure the latter. Ten of the eleven
    Secretary desks here are followed by a same-day action; one is followed by
    a wait of months.
    """
    return b.q(f"""
      SELECT w.place AS "Desk", COUNT(*) AS "Hand-offs",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY w.wait_days)
             AS BIGINT) AS "Median days before the next office acts"
      FROM office_wait w JOIN g USING (ticket_no)
      WHERE {b.WHERE['P1']} AND w.role_name = '{role}'
        AND w.wait_days IS NOT NULL
      GROUP BY 1 HAVING COUNT(*) >= {floor}
      ORDER BY "Hand-offs" DESC""")


def install_first_touch(b: Bottlenecks) -> None:
    """First and last action date per office role, per case, over P1."""
    b.con.execute(f"""
      CREATE OR REPLACE TABLE first_touch AS
      SELECT s.ticket_no, s.role_name,
             MIN(CAST(s.action_taken_date AS DATE)) AS first_action,
             MAX(CAST(s.action_taken_date AS DATE)) AS last_action,
             COUNT(*) AS actions
      FROM steps s JOIN (SELECT ticket_no FROM g WHERE {b.WHERE['P1']}) y
        USING (ticket_no)
      WHERE s.role_name IS NOT NULL
      GROUP BY 1, 2""")


def legs(b: Bottlenecks) -> pl.DataFrame:
    """Time before and after the block office first touches the case. Step P1.

    This is the table that keeps the reading honest. `office_wait` measures the
    gap *after* an office acts, which cannot tell a desk holding a file from a
    desk forwarding it at once and the next office being slow. Cutting each
    case at the block's first action does separate those: time in the first
    column is time before anyone has been to the field.

    Medians do not add, so the two columns are indicative of the split rather
    than a tiling of the total.
    """
    return b.q(f"""
      SELECT {_case()} AS "Entry point", COUNT(*) AS "Cases",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (
          ORDER BY t.first_action - CAST(g.created_on AS DATE)) AS BIGINT)
          AS "Filed to block's 1st action",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (
          ORDER BY CAST(g.resolved_on AS DATE) - t.first_action) AS BIGINT)
          AS "Block's 1st action to close",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY g.days_to_close)
             AS BIGINT) AS "Total days"
      FROM g JOIN first_touch t USING (ticket_no)
      WHERE {b.WHERE['P1']} AND g.days_to_close IS NOT NULL
        AND t.role_name = 'Block Development Officer'
      GROUP BY 1 ORDER BY "Cases" DESC""")


def desk_same_day(b: Bottlenecks, role: str, place: str) -> dict:
    """Does this desk hold files, or act and pass on the same day?

    The distinction the wait figure cannot make. If every action a desk takes
    on a case falls on one day, the desk is not the queue.
    """
    return b.q(f"""
      SELECT COUNT(*) AS cases,
        SUM((t.last_action = t.first_action)::INT) AS same_day,
        ROUND(100.0 * SUM((t.last_action = t.first_action)::INT) / COUNT(*), 1)
          AS pct_same_day,
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (
          ORDER BY t.first_action - CAST(g.created_on AS DATE)) AS BIGINT)
          AS filed_to_desk
      FROM g JOIN first_touch t USING (ticket_no)
      WHERE {b.WHERE['P1']} AND t.role_name = '{role}'""").row(0, named=True)


def next_after(b: Bottlenecks, role: str) -> pl.DataFrame:
    """Which office acts next after a given role, and how long it takes. P1."""
    return b.q(f"""
      WITH seq AS (
        SELECT s.role_name, CAST(s.action_taken_date AS DATE) AS d,
               LEAD(s.role_name) OVER (PARTITION BY s.ticket_no
                 ORDER BY s.action_taken_date, s.id) AS next_role,
               LEAD(CAST(s.action_taken_date AS DATE)) OVER
                 (PARTITION BY s.ticket_no ORDER BY s.action_taken_date, s.id)
                 AS next_d
        FROM steps s JOIN (SELECT ticket_no FROM g WHERE {b.WHERE['P1']}) y
          USING (ticket_no))
      SELECT next_role AS "Next office to act", COUNT(*) AS "Hand-offs",
        CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY next_d - d) AS BIGINT)
          AS "Median days until it acts"
      FROM seq WHERE role_name = '{role}' AND next_role IS NOT NULL
        AND next_role <> '{role}'
      GROUP BY 1 HAVING COUNT(*) >= 100 ORDER BY "Hand-offs" DESC""")


def phase_by_desk(b: Bottlenecks, role: str, place: str) -> pl.DataFrame:
    """Mean phase spans for cases that pass a desk and cases that do not.

    Step P4: joining `phases_clean` is what narrows P1 to the cases whose five
    spans tile, so this table stands on P4 and `check_samples` proves it.

    This is the exhibit that stops the phase decomposition being read as two
    separate problems: one queue straddles the field-action/review boundary.
    """
    return b.q(f"""
      SELECT CASE WHEN t.ticket_no IS NOT NULL THEN 'Passed this desk'
                  ELSE 'Did not' END AS "Case", COUNT(*) AS "Cases",
        ROUND(AVG(p.first_assignment), 1) AS "First assignment",
        ROUND(AVG(p.field_action), 1) AS "Field action",
        ROUND(AVG(p.review), 1) AS "Review"
      FROM (SELECT * FROM g WHERE {b.WHERE['P1']}) g
      JOIN phases_clean p USING (ticket_no)
      LEFT JOIN (SELECT DISTINCT w.ticket_no FROM office_wait w
                 WHERE {desk_predicate(role, place)}) t USING (ticket_no)
      GROUP BY 1 ORDER BY "Cases" DESC""")


def office_spread(b: Bottlenecks, floor: int = 30) -> pl.DataFrame:
    """Spread across the individual offices doing the same job. Hand-offs, P1.

    A long wait is not a measure of effort: it may be field enquiry, a
    statutory waiting period, or time spent waiting on the citizen.
    """
    return b.q(f"""
      WITH scoped AS (
        SELECT w.role_name, w.place, w.wait_days
        FROM office_wait w JOIN g USING (ticket_no)
        WHERE {b.WHERE['P1']} AND w.role_name IS NOT NULL
          AND w.place IS NOT NULL AND w.wait_days IS NOT NULL),
      per_office AS (
        SELECT role_name, place, COUNT(*) AS n, AVG(wait_days) AS mean_wait
        FROM scoped GROUP BY 1, 2 HAVING COUNT(*) >= {floor})
      SELECT role_name AS "Role", COUNT(*) AS "Offices",
        ROUND(AVG(mean_wait), 1) AS "Average wait",
        ROUND(MIN(mean_wait), 1) AS "Fastest",
        ROUND(MAX(mean_wait), 1) AS "Slowest",
        ROUND(STDDEV(mean_wait), 1) AS "Spread"
      FROM per_office GROUP BY 1 HAVING COUNT(*) >= 3
      ORDER BY "Spread" DESC""")


def slowest_desks(b: Bottlenecks, limit: int = 6, floor: int = 30) -> pl.DataFrame:
    """Hand-offs, over P1 cases."""
    return b.q(f"""
      SELECT w.role_name AS "Role", w.place AS "Desk",
        COUNT(*) AS "Hand-offs", ROUND(AVG(w.wait_days), 1) AS "Mean wait days"
      FROM office_wait w JOIN g USING (ticket_no)
      WHERE {b.WHERE['P1']} AND w.role_name IS NOT NULL
        AND w.place IS NOT NULL AND w.wait_days IS NOT NULL
      GROUP BY 1, 2 HAVING COUNT(*) >= {floor}
      ORDER BY AVG(w.wait_days) DESC, "Desk" LIMIT {limit}""")


def check_samples(b: Bottlenecks, key: str) -> None:
    """Prove each case-level table sums back to the funnel step it claims.

    The captions say which step a table stands on. Without this they are just
    assertions in prose, and the failure mode is silent: a join that drops rows
    still renders a table, and a partition that has quietly stopped covering
    its step still looks like a partition.
    """
    F = b.FUNNEL
    for name, df, step in (
        ("entry_rows", entry_rows(b), "P1"),
        ("desk_pass", desk_pass(b, *PR_DESK), "P1"),
        ("close_by_desk", close_by_desk(b, *PR_DESK), "P1"),
        ("phase_by_desk", phase_by_desk(b, *PR_DESK), "P4"),
    ):
        total = int(df["Cases"].sum())
        assert total == F[step], (
            f"{key}/{name}: sums to {total:,}, but step {step} is "
            f"{F[step]:,}. Either the join drops cases or the caption is wrong.")
    print(f"  {key}: case tables tie to P1 = {F['P1']:,} and P4 = {F['P4']:,}")


def spec_table(doc, key: str) -> None:
    """The same estimate under five progressively fairer comparisons.

    A likely range rather than a standard error: the question is how far the
    figure could move, not the statistic that says so. R-squared is here
    because in pensions it carries the finding -- entry point explains 0.1% of
    the variation and which block handles the case explains 30%.
    """
    r = REG[key]
    df = pl.DataFrame({
        "Compared": [s[0] for s in r["specs"]],
        "Extra days if sent to the department": [s[1] for s in r["specs"]],
        # round(), not ":.0f": a bound of -0.37 formats as "-0" and reads as a
        # typo. round() returns an int and drops the sign.
        "Range": [f"{round(s[1] - 1.96 * s[2])} to {round(s[1] + 1.96 * s[2])}"
                  for s in r["specs"]],
        # A decimal below 1%: pensions' first row is 0.001, and "0%"
        # contradicts the bullet beside it that says 0.1%.
        "Variation explained": [f"{s[3]:.1%}" if s[3] < 0.01
                                else f"{s[3]:.0%}" for s in r["specs"]],
    })
    table(doc, df, widths=[1.9, 1.7, 1.2, 1.1])


def build() -> Path:
    doc = Document()
    _styles(doc)

    para(doc, "Grievance bottlenecks: pensions and rural housing",
         bold=True, size=18, colour=MAROON)
    para(doc, "Internal. Complaints filed July 2021 to June 2025.",
         size=10, colour=INK_SOFT)

    pr, pr_cfg = load("panchayati_raj")
    ss, ss_cfg = load("ssepd")
    print("sample conformance")
    check_samples(pr, "panchayati_raj")
    check_samples(ss, "ssepd")

    pr_entry, ss_entry = entry_rows(pr), entry_rows(ss)
    pr_dd, ss_dd = dedup_row(pr), dedup_row(ss)
    install_first_touch(pr)
    install_first_touch(ss)
    desk = desk_wait(pr, *PR_DESK)
    pr_legs = legs(pr)
    sec = desk_same_day(pr, *PR_DESK)
    after_sec = next_after(pr, PR_DESK[0])
    pr_pass, pr_close = desk_pass(pr, *PR_DESK), close_by_desk(pr, *PR_DESK)
    peers = peer_desks(pr, PR_DESK[0])
    same_day = peers.filter(
        pl.col("Median days before the next office acts") == 0).height

    def entry(df: pl.DataFrame, label: str) -> dict:
        return df.filter(pl.col("Entry point") == label).row(0, named=True)

    def closed(df: pl.DataFrame, label: str) -> dict:
        return df.filter(pl.col("Case") == label).row(0, named=True)

    pr_coll, pr_direct = entry(pr_entry, "Collector"), entry(pr_entry, "Direct to dept")
    ss_coll, ss_direct = entry(ss_entry, "Collector"), entry(ss_entry, "Direct to dept")
    ss_cm = entry(ss_entry, "CM Cell")
    ss_unrec = entry(ss_entry, UNRECORDED)
    passed = closed(pr_close, "Passed this desk")
    not_passed = closed(pr_close, "Did not")
    leg_coll = entry(pr_legs, "Collector")
    leg_direct = entry(pr_legs, "Direct to dept")
    pass_direct = entry(pr_pass, "Direct to dept")
    pass_coll = entry(pr_pass, "Collector")
    R = REG["panchayati_raj"]

    def cap(text: str) -> None:
        para(doc, text, size=9, colour=INK_SOFT)
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_heading("Summary", level=1)
    bullets(doc, [
        "Two departments, same portal, same steps. Different problems.",
        f"**Rural housing: the case takes "
        f"{leg_direct['Filed to block\'s 1st action']} days to reach the "
        f"office that does the work.** Sent to a Collector it takes "
        f"{leg_coll['Filed to block\'s 1st action']} day. Nobody has been "
        f"to the field in that time.",
        "**Pensions: the block office.** Where the complaint is sent makes no "
        "difference. Which block office handles it makes a large one.",
        f"Entry point explains **{R['specs'][0][3]:.0%}** of the variation in "
        f"rural housing and **{REG['ssepd']['specs'][0][3]:.1%}** in pensions.",
        "Both departments: **heavy repeat filing**, **missing intake "
        "details**.",
    ])

    # -- rural housing -------------------------------------------------------

    doc.add_heading("Rural housing", level=1)
    doc.add_heading("Findings", level=2)
    bullets(doc, [
        f"{pr.FUNNEL['S0']:,} complaints, {pr_dd['claims']:,} cases after "
        f"collapsing repeats. Rural housing is {pr.FUNNEL['P0']:,} of them, "
        f"{pr.FUNNEL['P1']:,} filed in 2024-25.",
        f"Median close: **Collector {pr_coll['Median days']} days, department "
        f"{pr_direct['Median days']} days**.",
        f"{pr_direct['% of cases']}% of cases go to the department.",
        f"**{R['open_direct']}% of the department's cases are still open**, "
        f"against {R['open_coll']}%. The gap above is understated.",
    ])
    table(doc, pr_entry, widths=[1.5, 1.0, 0.95, 0.95, 0.95, 0.95])
    cap(f"P1 · {pr.FUNNEL['P1']:,} rural housing cases filed 2024-25, by "
        f"where the complaint was sent.")

    doc.add_heading("Checks", level=2)
    bullets(doc, [
        "Held fixed: block, month, complaint length, online or paper, "
        "documents attached, and what the complaint says.",
        f"Estimate barely moves. {R['n']:,} cases, {R['blocks']} blocks.",
        "Including still-open cases, the department's close at under a quarter "
        "of the rate.",
    ])
    spec_table(doc, "panchayati_raj")
    cap("P1 restricted to cases with a recorded block, closed cases only.")

    doc.add_heading("The gap", level=2)
    bullets(doc, [
        f"**A case sent to the department takes "
        f"{leg_direct["Filed to block's 1st action"]} days to reach the block "
        f"office. Sent to a Collector, "
        f"{leg_coll["Filed to block's 1st action"]} day.**",
        f"After the block engages, closing takes "
        f"{leg_direct["Block's 1st action to close"]} days against "
        f"{leg_coll["Block's 1st action to close"]}.",
        "So roughly half the gap happens **before anyone has been to the "
        "field**. It is transit between offices, not enquiry.",
        f"**The Secretary is not holding the files.** Every action that desk "
        f"takes on a case falls on one day for {sec['pct_same_day']}% of "
        f"cases, and the case reaches it {sec['filed_to_desk']} days after "
        f"filing.",
        "The wait is what happens after it forwards.",
    ])
    table(doc, pr_legs, widths=[1.5, 0.9, 1.5, 1.5, 0.9])
    cap("P1 · rural housing cases with a block action and a closing date. "
        "Medians, which do not add, so the two middle columns indicate the "
        "split rather than tiling the total.")

    bullets(doc, [
        "**The next office to act after the Secretary is the DRDA.** That is "
        "where the transit time sits.",
    ])
    table(doc, after_sec, widths=[2.4, 1.3, 1.9])
    cap("What acts next after the Secretary, and how long until it does. "
        "Unit is the hand-off.")

    bullets(doc, [
        f"Cases that pass the Panchayati Raj and Drinking Water Secretary "
        f"close in **{passed['Median days']} days** at the median; cases that "
        f"do not, **{not_passed['Median days']}**. "
        f"{pass_direct['%']}% of department cases pass it and "
        f"{pass_coll['%']}% of Collector cases do, so it marks the route "
        f"rather than causing the wait.",
        f"**{same_day} of the {peers.height} Secretary desks in this caseload "
        f"are followed by a same-day action.** After this one, the next office "
        f"takes {desk['median_wait']} days.",
    ])
    table(doc, peers, widths=[2.0, 1.2, 2.2])
    cap("Every Secretary desk appearing in rural housing cases, 2024-25. The "
        "column is the gap before the next office acts, not this desk's own "
        "holding time. Unit is the hand-off.")

    bullets(doc, [
        "**The five-step breakdown does not separate these.** Field action is "
        "defined as running from the first hand-off to another office until "
        "the case is sent back up, so it contains the transit and the field "
        "work together.",
    ])
    table(doc, phase_by_desk(pr, *PR_DESK), widths=[1.7, 1.0, 1.4, 1.2, 1.0])
    cap(f"P4 · {pr.FUNNEL['P4']:,} cases whose five spans tile. Average days "
        f"per step.")
    exhibit(doc, f"{pr_cfg['prefix']}32_phases_by_entry",
            f"P4 · {pr.FUNNEL['P4']:,} cases. Average days per step, by where "
            f"the complaint was sent.")

    # -- pensions ------------------------------------------------------------

    doc.add_heading("Pensions", level=1)
    doc.add_heading("Findings", level=2)
    bullets(doc, [
        f"{ss.FUNNEL['S0']:,} complaints, {ss_dd['claims']:,} cases. Pensions "
        f"are {ss.FUNNEL['P0']:,} of them, {ss.FUNNEL['P1']:,} filed in "
        f"2024-25.",
        f"Median close: Collector {ss_coll['Median days']}, department "
        f"{ss_direct['Median days']}, CM Cell {ss_cm['Median days']}. "
        f"**Six-day spread.**",
        "The raw figures look worse for the department. Artefact: department "
        "filing fell from 39% to 1% across the year, and cases filed late have "
        "not had time to age. A same-month comparison removes it.",
        "Including still-open cases, **no difference at all**.",
        f"**Entry point explains {REG['ssepd']['specs'][0][3]:.1%} of the "
        f"variation here.** It is the wrong place to look, and we had to check "
        f"to know that.",
    ])
    spec_table(doc, "ssepd")
    cap("P1 restricted to cases with a recorded block, closed cases only.")

    doc.add_heading("Block offices", level=2)
    bullets(doc, [
        f"**Which block office handles the case is what varies.** Adding block "
        f"to the comparison explains {REG['ssepd']['specs'][2][3]:.0%} of the "
        f"variation; district alone explains "
        f"{REG['ssepd']['specs'][1][3]:.0%}.",
        "**128 block offices**, average wait 14.6 days, range **1 to 54**.",
        "The district tier is fast and consistent by comparison: Collectors "
        "average 5.1 days, social security officers 4.1.",
        "The slowest desks in the pension caseload are all block offices.",
    ])
    table(doc, office_spread(ss), widths=[2.0, 0.9, 1.1, 0.85, 0.85, 0.9])
    cap(f"P1 · {ss.FUNNEL['P1']:,} pension cases. Spread across offices doing "
        f"the same job, offices with 30 or more hand-offs. Unit is the "
        f"hand-off.")
    table(doc, slowest_desks(ss), widths=[1.9, 1.4, 1.1, 1.4])
    cap("Slowest desks in the pension caseload. Unit is the hand-off.")

    doc.add_heading("Missing records", level=2)
    bullets(doc, [
        f"**{ss_unrec['Cases']:,} cases ({ss_unrec['% of cases']}%)** record "
        f"no entry point. **{ss_unrec['% no block']}% of those record no "
        f"block.**",
        f"Slowest group: median {ss_unrec['Median days']} days against "
        f"{ss_coll['Median days']}.",
        "No block means no automatic routing, no district visibility, and no "
        "way to compare the office that handled it.",
        "Under half a percent in rural housing, so this is fixable.",
    ])
    table(doc, ss_entry, widths=[1.5, 1.0, 0.95, 0.95, 0.95, 0.95])
    cap(f"P1 · {ss.FUNNEL['P1']:,} pension cases filed 2024-25, by where the "
        f"complaint was sent.")

    # -- both ----------------------------------------------------------------

    doc.add_heading("Both departments", level=1)
    doc.add_heading("Repeat filing", level=2)
    bullets(doc, [
        f"Pensions {100 * ss_dd['repeats'] / ss_dd['filings']:.0f}% repeats, "
        f"rural housing {100 * pr_dd['repeats'] / pr_dd['filings']:.0f}%.",
        "Same complaint rewritten near word for word, not different people.",
        "Each repeat is a new case: clock restarts, someone has to close it.",
    ])

    doc.add_heading("Complaint text", level=2)
    bullets(doc, [
        "Tested: earlier attempts mentioned, already on a list, names an "
        "officer, length, script.",
        "**All null, both departments.** The gap is not the complaints.",
        "Online goes with shorter waits in housing, longer in pensions. "
        "Separate question.",
    ])

    doc.add_heading("Data gaps", level=2)
    bullets(doc, [
        "**Outcomes.** 25,182 of 449,085 closed cases statewide record a "
        "benefit given. We measure duration and almost nothing else.",
        "**Attachments.** Three quarters of the shortest complaints have one; "
        "the shortest are eight characters. The system sorts them on a title.",
    ])

    # -- proposals -----------------------------------------------------------

    doc.add_heading("Proposals", level=1)
    para(doc, "Strongest evidence first.", size=10, colour=INK_SOFT)
    bullets(doc, [
        f"**Get rural housing cases to the block office faster.** They "
        f"take {leg_direct["Filed to block's 1st action"]} days to get "
        f"there when sent to the department and "
        f"{leg_coll["Filed to block's 1st action"]} day when sent to a "
        f"Collector, and nothing has happened in the field in between. "
        f"The DRDA step is where that time sits.",
        "**Publish block-office waiting times inside the department.** In "
        "pensions the block office is the whole story, and a range of 1 to 54 "
        "days across 128 offices is not visible to anyone today.",
        "**Check the person's open cases at filing.** \"Looks like your "
        "complaint from N days ago, now with [office]. Add to it?\" The tool "
        "exists.",
        "**Require the block at intake**, or derive it from the address. "
        "Without it a case cannot be routed, tracked, or compared.",
        "**Show the person where the case is.** Root fix for repeat filing.",
        "**Record whether the benefit was given**, one box at closing. "
        "Otherwise only speed is manageable, and speed alone can be met by "
        "closing faster.",
    ])

    doc.add_heading("Questions", level=2)
    bullets(doc, [
        "Why does a case forwarded by this department wait months for the "
        "DRDA to act, when a case forwarded by a Collector reaches the "
        "block the next day? Staffing, a required check, or habit?",
        "Is the DRDA review required? If so, can it run alongside the field "
        "work rather than before it?",
        "Both answered by a document. **Ask for the routing settings and the "
        "housing sanction procedure.**",
    ])

    # -- further study -------------------------------------------------------

    doc.add_heading("Further study", level=1)
    table(doc, pl.DataFrame({
        "Study": [
            "Follow cases from the department to the block",
            "Read the rules",
            "Compare a fast and a slow block office, pensions",
            "Ring 100 people whose housing case closed",
            "Ring repeat filers",
            "Time staff per case",
            "Randomise where new cases are sent",
            "Read 200 short-complaint attachments",
        ],
        "Tells us": [
            "Why the DRDA takes months on cases the department forwards",
            "Whether sending cases straight to the block is allowed",
            "What a 1-day office does that a 54-day office does not",
            "Whether a closed case means anything to the person",
            "Why they filed again, and what they were told",
            "How much work is the same complaint twice",
            "Whether routing causes the housing gap",
            "Whether attachments must be read",
        ],
        "Time": ["Days", "Days", "1-2 weeks", "2 weeks", "Days",
                 "With the block visit", "After the rules", "Days"],
    }), widths=[2.2, 2.9, 1.2])

    doc.add_heading("Priority", level=2)
    bullets(doc, [
        "**The transit to the block first.** Forty-five days before "
        "anyone reaches the field is the largest single number we have, "
        "and the DRDA step is inside it.",
        "Then the fast-versus-slow block comparison for pensions, and the "
        "phone calls, in the same fortnight.",
        "**Phone calls matter most.** Every number here is a duration. No "
        "independent check on whether anyone was helped.",
    ])

    doc.add_heading("Caveats", level=2)
    bullets(doc, [
        "Records study, not an experiment. Something unobserved could still be "
        "at work.",
        "Duration is how long a case stayed open, not effort. An office's wait "
        "may be field enquiry, a waiting period, or time waiting on the "
        "citizen.",
        "The desk figures and the entry-point estimate are the same fact "
        "measured two ways, not two independent results.",
        "An office's wait figure is the gap before the following "
        "office acts. "
        "It cannot tell a desk holding a file from a desk forwarding it at "
        "once and the next office being slow. Only the block-arrival "
        "table separates those.",
        "The time after the block engages is not explained by where the "
        "case was sent, and contains the return trip up for review. It "
        "needs its own investigation.",
        "Office tables count hand-offs, not cases, so they do not add to the "
        "case totals elsewhere.",
        "Repeats counted once throughout.",
        "Times use closed cases only, so the housing gap is understated.",
    ])

    pr.con.close()
    ss.con.close()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / "grievance_bottlenecks_summary.docx"
    doc.save(out)
    return out


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")
