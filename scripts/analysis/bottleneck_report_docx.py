"""A Word version of a bottleneck note, for department leadership.

The notebook is the working document: every exhibit, every sample, every
caveat, in the order the analysis was done. This is the read-once version of
the same result, arranged around one finding -- **where a grievance is first
received predicts how long it waits** -- with the method behind it.

    uv run python scripts/analysis/bottleneck_report_docx.py ssepd
    uv run python scripts/analysis/bottleneck_report_docx.py panchayati_raj

Numbers come from `janasunani.analytics.bottlenecks`, the same module the
notebook runs, so the document cannot quote a figure the notebook does not
produce. Exhibits are the PNGs that notebook already wrote; run it first.

Output goes to `outputs/reports/`, which is gitignored: it carries figures and
counts derived from citizen grievances and is not a repository artifact.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from janasunani.analytics import journey
from janasunani.analytics.figures import time_stats
from janasunani.analytics.bottlenecks import (
    DEPARTMENTS, ENTRY_OFFICES, Bottlenecks, check_note, open_lake,
)

REPO = Path(__file__).resolve().parents[2]
FIGDIR = REPO / "outputs" / "figures"
OUTDIR = REPO / "outputs" / "reports"

MAROON = RGBColor(0x8B, 0x15, 0x24)
INK = RGBColor(0x1A, 0x1A, 0x1A)
INK_SOFT = RGBColor(0x66, 0x66, 0x66)


# --- document furniture ------------------------------------------------------


def _styles(doc: Document) -> None:
    """House type. Calibri is the DPIC brand face; headings carry the maroon."""
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(8)
    for name, size in (("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11)):
        st = doc.styles[name]
        st.paragraph_format.keep_with_next = True
        st.font.name = "Calibri"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = MAROON
        st.paragraph_format.space_before = Pt(16)
        st.paragraph_format.space_after = Pt(6)


def para(doc, text, *, style=None, bold=False, size=None, colour=None):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    run.bold = bold
    if size:
        run.font.size = Pt(size)
    if colour:
        run.font.color.rgb = colour
    return p


def bullets(doc, items) -> None:
    """Bullets, with every "**...**" span emboldened.

    Every span, not just a leading one: a first draft handled only the leading
    case and printed the literal asterisks around a figure mid-sentence.
    """
    for item in items:
        p = doc.add_paragraph(style="List Bullet")
        for i, part in enumerate(item.split("**")):
            if part:
                p.add_run(part).bold = bool(i % 2)


# The entry points read as chart labels. In a sentence they need to be
# ordinary English, and this is the one place that mapping lives.
ENTRY_PROSE = {
    "Collector": "a District Collector",
    "CM Cell": "the Chief Minister's Cell",
    "Direct to dept": "the department directly",
    "Other or not recorded": "another or unrecorded desk",
}


def prose(label: str) -> str:
    return ENTRY_PROSE.get(label, label.lower())


UNRECORDED = "Other or not recorded"

# How large a spread between named entry points has to be before the note
# calls the entry point an explanation rather than a thing it checked. Twenty
# days is roughly half the median time to close in both departments.
ENTRY_EFFECT_DAYS = 20.0


def exhibit(doc, name: str, caption: str, width: float = 6.3) -> None:
    """A figure the notebook already produced, with its caption.

    Missing figures are a hard error, not a gap in the document: it means the
    notebook has not been run since the report was last changed, and a report
    that silently drops an exhibit is worse than one that refuses to build.
    """
    path = FIGDIR / f"{name}.png"
    if not path.exists():
        raise SystemExit(f"missing exhibit {path}; run the notebook first")
    doc.add_picture(str(path), width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.keep_with_next = True
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = cap.add_run(caption)
    run.font.size = Pt(9)
    run.font.color.rgb = INK_SOFT
    run.italic = True


HEADER_TINT = "F3E7E9"   # the maroon at 8%, so the header reads as one band


def _shade(cell, hexfill: str) -> None:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hexfill)
    cell._tc.get_or_add_tcPr().append(shd)


def _no_split(row, header: bool = False) -> None:
    """Keep a row whole, and repeat the header when a table spans pages.

    Word will otherwise break a row mid-cell, which on the route tables left
    an orphaned strip of one route at the top of the next page.
    """
    pr = row._tr.get_or_add_trPr()
    pr.append(OxmlElement("w:cantSplit"))
    if header:
        pr.append(OxmlElement("w:tblHeader"))


def table(doc, df: pl.DataFrame, *, fmt=None, widths=None) -> None:
    fmt = fmt or {}
    t = doc.add_table(rows=1, cols=len(df.columns))
    # "Table Grid", not one of Word's accent styles: those are blue, and the
    # document's own colour is the DPIC maroon.
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, col in enumerate(df.columns):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(col))
        run.bold = True
        run.font.size = Pt(9)
        _shade(cell, HEADER_TINT)
    _no_split(t.rows[0], header=True)
    for row in df.iter_rows(named=True):
        cells = t.add_row().cells
        for i, col in enumerate(df.columns):
            v = row[col]
            if v is None:
                text = ""
            elif col in fmt:
                text = fmt[col](v)
            elif isinstance(v, float):
                text = f"{v:,.1f}"
            elif isinstance(v, int):
                text = f"{v:,}"
            else:
                text = str(v)
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(text)
            run.font.size = Pt(9)
        _no_split(t.rows[-1])
    if widths:
        for row in t.rows:
            for cell, w in zip(row.cells, widths):
                cell.width = Inches(w)


def n(x) -> str:
    return f"{int(x):,}"


# --- the note ----------------------------------------------------------------


def pct(part, whole) -> str:
    return f"{100 * float(part) / float(whole):.0f}%"


def build(key: str) -> Path:
    cfg = dict(DEPARTMENTS[key])
    prefix = cfg.pop("prefix")
    cfg["dedup_csv"] = REPO / cfg["dedup_csv"]

    con = open_lake()
    check_note(con)
    b = Bottlenecks(con, figdir=FIGDIR, prefix=prefix, **cfg)
    b.define_phases()

    def ex(name, caption, width=6.3):
        exhibit(doc, f"{prefix}{name}", caption, width)

    def one(sql):
        return b.q(sql).row(0, named=True)

    F, claims, W = b.FUNNEL, b.claims, b.WHERE
    LBL = journey.PHASE_LABEL

    entry = b.entry_time_table()
    by_entry = {lab: r for lab, r in b._phase_rows(ENTRY_OFFICES, 100)}
    named = [k for k in by_entry if k != UNRECORDED]
    fastest = min(named, key=lambda k: float(by_entry[k]["total"]))
    slowest = max(named, key=lambda k: float(by_entry[k]["total"]))
    fast, slow = by_entry[fastest], by_entry[slowest]
    unrec = by_entry.get(UNRECORDED)
    gap = float(slow["total"]) - float(fast["total"])
    strong = gap >= ENTRY_EFFECT_DAYS
    means, phase_total = b.phase_means()
    levels = b.level_table()
    routes = b.q(b._route_sql("TRUE", 5, "% of claims"))
    diffs = sorted(((LBL[p], float(slow[p]) - float(fast[p]))
                    for p in journey.PHASES), key=lambda kv: -kv[1])
    heaviest = max(journey.PHASES, key=lambda p: float(means[p]))

    doc = Document()
    _styles(doc)
    para(doc, f"Where {b.dept} grievances lose time", style="Title")
    para(doc, f"{claims.capitalize()}, {b.span}. Sections follow the analysis "
              "notebook of the same name.", size=10, colour=INK_SOFT)

    # -- in brief -------------------------------------------------------------
    doc.add_heading("In brief", level=1)
    bullets(doc, [
        f"**{n(F['S0'])} filings** against {b.dept}, {b.span}.",
        f"**{n(F['S1'])} distinct claims** once repeat filings of the same "
        f"complaint are collapsed, {pct(F['S0'] - F['S1'], F['S0'])} removed.",
        f"**This note follows {claims}**, {pct(F['P0'], F['S1'])} of them.",
        (f"**Entry point predicts the wait.** {float(fast['total']):.0f} days "
         f"via {prose(fastest)}, {float(slow['total']):.0f} via "
         f"{prose(slowest)}." if strong else
         f"**Entry point explains little.** {float(fast['total']):.0f} to "
         f"{float(slow['total']):.0f} days across named entry points."),
        (f"**The gap sits in {diffs[0][0].lower()}**, {diffs[0][1]:+.0f} days."
         if strong else
         f"**{LBL[heaviest]} carries the time**, {float(means[heaviest]):.0f} "
         f"of {phase_total:.0f} days."),
        "**Variation between individual offices exceeds variation between "
        "roles.** Section 2.9.",
    ])

    # ================= PART 1 =================================================
    doc.add_heading(f"Part 1 — {b.dept} as a whole", level=1)

    doc.add_heading("1.1 Filings", level=2)
    yr = b.q(f"""SELECT year, COUNT(*) AS n FROM g WHERE {W['S0']}
                 GROUP BY 1 ORDER BY 1""")
    last = yr.row(len(yr) - 1, named=True)
    bullets(doc, [
        f"{n(F['S0'])} filings, {b.span}.",
        f"{n(last['n'])} in {last['year']}, {pct(last['n'], F['S0'])} of the "
        "four years.",
        "Rising every year.",
    ])
    ex("11_filings_year", "Filings by year.")

    doc.add_heading("1.2 Repeat filings", level=2)
    d = one(f"""SELECT COUNT(*) FILTER (WHERE NOT is_primary) AS repeats,
                  COUNT(*) FILTER (WHERE duplicate_group_id IS NULL) AS ungrouped
                FROM g WHERE {W['S0']}""")
    bullets(doc, [
        f"{n(F['S0'])} filings, {n(F['S1'])} claims.",
        f"{n(d['repeats'])} removed as repeats, {pct(d['repeats'], F['S0'])}.",
        "A match needs the same petitioner and at least half the same text.",
        "Grouped across all four years, not year by year.",
        f"{n(d['ungrouped'])} filings carry no group and are kept as themselves.",
    ])
    table(doc, b.q(f"""
      SELECT year AS "Year", COUNT(*) AS "Filings",
        COUNT(*) FILTER (WHERE is_primary) AS "Claims",
        COUNT(*) - COUNT(*) FILTER (WHERE is_primary) AS "Repeats removed"
      FROM g WHERE {W['S0']} GROUP BY 1 ORDER BY 1"""),
        widths=[1.2, 1.5, 1.5, 1.8])

    doc.add_heading("1.3 2024-25", level=2)
    ch = one(f"""SELECT COUNT(*) FILTER (WHERE channel = 'Online') AS on_,
                   COUNT(*) FILTER (WHERE channel = 'Offline') AS off
                 FROM g WHERE {W['S2']}""")
    bullets(doc, [
        f"{n(F['S2'])} claims filed in 2024-25, {pct(F['S2'], F['S1'])} of all "
        "claims.",
        f"Online {pct(ch['on_'], F['S2'])}, offline {pct(ch['off'], F['S2'])}.",
        "The rest of the note works inside this year.",
    ])
    ex("13_claims_month_2425", "Claims by month, 2024-25.")

    doc.add_heading("1.4 Channels", level=2)
    md = b.q(f"""SELECT mode, COUNT(*) AS n FROM g WHERE {W['S2']}
                 GROUP BY 1 ORDER BY n DESC LIMIT 3""")
    bullets(doc, [f"{r['mode']}: {n(r['n'])}, {pct(r['n'], F['S2'])}."
                  for r in md.iter_rows(named=True)]
            + ["Online is email, website, app and social; everything else is a "
               "walk-in or a piece of paper."])
    ex("14_modes", "Every route a claim arrives by, 2024-25.")

    doc.add_heading("1.5 Entry points", level=2)
    er = b.q(f"""SELECT entry_route AS r, COUNT(*) AS n FROM g WHERE {W['S2']}
                 GROUP BY 1 ORDER BY n DESC LIMIT 4""")
    bullets(doc, [f"{r['r']}: {pct(r['n'], F['S2'])}."
                  for r in er.iter_rows(named=True)]
            + ["Records the desk that first received the grievance, not the "
               "office that acted."])
    ex("15_entry_route", "How claims reach the department, 2024-25.")

    doc.add_heading("1.6 Subjects", level=2)
    sub = b.q(f"""SELECT subcategory AS "Subcategory", COUNT(*) AS "Claims",
                    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS "Share"
                  FROM g WHERE {W['S2']} AND subcategory IS NOT NULL
                  GROUP BY 1 ORDER BY "Claims" DESC LIMIT 6""")
    bullets(doc, [
        f"{sub['Subcategory'][0]} is the largest, "
        f"{pct(sub['Claims'][0], F['S2'])} of 2024-25 claims.",
        "Part 2 holds this fixed.",
    ])
    table(doc, sub.with_columns(pl.col("Share").round(0).cast(pl.Int64)
                                .cast(pl.Utf8).add("%")),
          widths=[3.2, 1.3, 1.2])

    doc.add_heading("1.7 Outcomes and time", level=2)
    oc = b.q(f"""SELECT outcome AS o, COUNT(*) AS n FROM g WHERE {W['S2']}
                 GROUP BY 1 ORDER BY n DESC""")
    tm = one(f"SELECT {time_stats()} FROM g WHERE {W['S3']}")
    bullets(doc, [f"{r['o']}: {pct(r['n'], F['S2'])}."
                  for r in oc.iter_rows(named=True)]
            + [f"Median {tm['median_days']} days, mean {tm['mean_days']:.0f}, "
               f"slowest tenth {tm['slowest_tenth']}.",
               "\u201cDisposed\u201d records a file closed, not a citizen served.",
               "Open claims are excluded from every timing figure."])
    ex("17_time_distribution", "Time to close, 2024-25.")

    doc.add_heading("1.8 Complexity varies", level=2)
    span = one(f"""SELECT ROUND(MIN(m), 0) AS lo, ROUND(MAX(m), 0) AS hi
                   FROM (SELECT AVG(days_to_close) AS m FROM g
                         WHERE {W['S2']} AND subcategory IS NOT NULL
                         GROUP BY subcategory
                         HAVING COUNT(*) FILTER (
                             WHERE is_disposed AND days_to_close IS NOT NULL) >= 200)""")
    bullets(doc, [
        f"Mean time to close runs {span['lo']:.0f} to {span['hi']:.0f} days "
        "across subcategories.",
        "An office holding one kind of claim looks faster or slower than an "
        "office holding another, whatever either does.",
        "Part 2 therefore fixes the subcategory.",
    ])
    ex("18_subcategory_time", "Time to close by subcategory.")

    # ================= PART 2 =================================================
    doc.add_heading(f"Part 2 — {claims}", level=1)

    doc.add_heading("2.1 Volume", level=2)
    bullets(doc, [
        f"{n(F['P0'])} claims, {pct(F['P0'], F['S1'])} of the department.",
        f"{n(F['P1'])} filed in 2024-25.",
        f"{n(F['P2'])} of those closed with a usable date.",
    ])
    ex("21_sub_month_full", f"{claims.capitalize()} by month, {b.span}.")

    doc.add_heading("2.2 Channels", level=2)
    md2 = b.q(f"""SELECT mode, COUNT(*) AS n FROM g WHERE {W['P1']}
                  GROUP BY 1 ORDER BY n DESC LIMIT 3""")
    bullets(doc, [f"{r['mode']}: {pct(r['n'], F['P1'])}."
                  for r in md2.iter_rows(named=True)])
    ex("23_sub_modes", "Every route a claim arrives by, 2024-25.")

    doc.add_heading("2.3 Entry points", level=2)
    er2 = b.q(f"""SELECT CASE {' '.join(f"WHEN {p} THEN '{lab}'"
                                        for lab, p in ENTRY_OFFICES)} END AS grp,
                    COUNT(*) AS n
                  FROM g WHERE {W['P1']} GROUP BY 1 ORDER BY n DESC""")
    bullets(doc, [f"{r['grp']}: {pct(r['n'], F['P1'])}."
                  for r in er2.iter_rows(named=True)]
            + ["Four groups. The rest of Part 2 cuts by these."])
    ex("24_entry_groups", "The four entry points, 2024-25.")

    doc.add_heading("2.4 Outcomes", level=2)
    oc2 = b.q(f"""SELECT outcome AS o, COUNT(*) AS n FROM g WHERE {W['P1']}
                  GROUP BY 1 ORDER BY n DESC""")
    bullets(doc, [f"{r['o']}: {pct(r['n'], F['P1'])}."
                  for r in oc2.iter_rows(named=True)])
    ex("25_sub_outcomes", "How claims ended, 2024-25.")

    doc.add_heading("2.5 The five phases", level=2)
    bullets(doc, [
        "Time to close divides into five consecutive spans that add back to it.",
        "Means, not medians: only the mean of a total is the sum of its parts.",
        f"P3, {n(F['P3'])} claims: dated history, every office recognised.",
        f"P4, {n(F['P4'])} claims: the five spans add to the total.",
        "P4 drops claims whose last action postdates their closing date. Those "
        "are slower, so the split is mildly optimistic.",
    ])
    table(doc, pl.DataFrame({
        "Phase": ["Registration", "First assignment", "Field action",
                  "Review", "Closure"],
        "From": ["filed", "first action", "first hand-off", "sent back up",
                 "last action"],
        "To": ["first action recorded", "passed to a different office",
               "sent back up a level", "last action recorded",
               "recorded as resolved"],
    }), widths=[1.5, 1.6, 2.6])

    doc.add_heading("2.6 Time overall", level=2)
    tm2 = one(f"SELECT {time_stats()} FROM g WHERE {W['P2']}")
    bullets(doc, [
        f"Median {tm2['median_days']} days, mean {tm2['mean_days']:.0f}.",
        f"Slowest tenth {tm2['slowest_tenth']} days.",
        f"{LBL[heaviest]} is the largest span, {float(means[heaviest]):.0f} of "
        f"{phase_total:.0f} days.",
    ])
    table(doc, pl.DataFrame({
        "Phase": [LBL[p] for p in journey.PHASES],
        "Mean days": [round(float(means[p]), 1) for p in journey.PHASES],
        "Share": [pct(float(means[p]), phase_total) for p in journey.PHASES],
    }), widths=[2.2, 1.4, 1.2])
    ex("26_sub_time_distribution", "Time to close, 2024-25.")

    doc.add_heading("2.7 By entry point", level=2)
    bullets(doc, [
        f"{prose(fastest).capitalize()}: {float(fast['total']):.0f} days mean.",
        f"{prose(slowest).capitalize()}: {float(slow['total']):.0f} days mean.",
        (f"Spread {gap:.0f} days on like-for-like claims."
         if strong else
         f"Spread only {gap:.0f} days. Entry point is not the lever here."),
    ] + [f"{name}: {d:+.0f} days." for name, d in diffs if abs(d) >= 1.0]
      + ([f"{n(unrec['n'])} claims record no entry point, {float(unrec['total']):.0f} "
          "days. Reported, never used as an explanation."] if unrec is not None else []))
    table(doc, entry, widths=[1.9, 1.0, 1.0, 1.0, 1.1])
    ex("32_phases_by_entry", "Where the time goes, by entry point.")

    doc.add_heading("2.8 By route", level=2)
    rr = routes.row(0, named=True)
    slow_route = routes.sort("Mean days", descending=True).row(0, named=True)
    bullets(doc, [
        "A route is the sequence of offices that held the claim, by role, "
        "never by place.",
        f"Commonest: {rr['Route']}, {n(rr['Claims'])} claims, "
        f"{float(rr['Mean days']):.0f} days.",
        f"Slowest of the five: {slow_route['Route']}, "
        f"{float(slow_route['Mean days']):.0f} days.",
    ])
    table(doc, routes, fmt={"% of claims": lambda v: f"{float(v):.0f}%"},
          widths=[2.4, 0.9, 0.9, 0.9, 0.9])
    ex("33_phases_by_route", "Where the time goes, by route.")

    doc.add_heading("2.9 By desk", level=2)
    total_h = levels["Hand-offs"].sum()
    lv = levels.sort("Mean days", descending=True)
    slowest_role = b.nodes(b.roles[0][0], b.roles[0][2]).row(0, named=True)
    fastest_role = b.nodes(b.roles[0][0], b.roles[0][2], worst=False).row(0, named=True)
    bullets(doc, [
        "Unit changes: hand-offs, not claims.",
        f"{n(total_h)} hand-offs across {len(levels)} levels.",
        f"Slowest level {lv['Level'][0]}, {float(lv['Mean days'][0]):.0f} days, "
        f"{pct(lv['Hand-offs'][0], total_h)} of hand-offs.",
        f"Within {b.roles[0][0].lower()}s alone: "
        f"{float(fastest_role['Mean days']):.0f} to "
        f"{float(slowest_role['Mean days']):.0f} days.",
        "Not a ranking of effort. A long wait may be enquiry, a statutory "
        "period, or waiting on the citizen.",
    ])
    table(doc, levels.with_columns(
        pl.col("Hand-offs").truediv(total_h).mul(100).round(0)
        .cast(pl.Int64).cast(pl.Utf8).add("%").alias("Share")),
        widths=[1.6, 1.1, 1.0, 1.0, 1.0, 0.9])
    # The notebook's node exhibit is three panels side by side, which is 4:1
    # and unreadable at page width. The level chart carries the point; the
    # individual desks read better as a table anyway.
    ex("34_level_wait", "Wait by level of government.")
    role = b.roles[0][0]
    para(doc, f"Slowest and fastest {role.lower()}s", bold=True, size=10)
    table(doc, pl.concat([
        b.nodes(role, b.roles[0][2], n=5).with_columns(
            pl.lit("Slowest").alias("")),
        b.nodes(role, b.roles[0][2], worst=False, n=5).with_columns(
            pl.lit("Fastest").alias("")),
    ]).select(["", "Office", "Hand-offs", "Median days", "Mean days"]),
        widths=[0.8, 2.4, 1.1, 1.1, 1.1])

    # -- options --------------------------------------------------------------
    doc.add_heading("What could move it", level=1)
    para(doc, "Options, not conclusions. Each names what it rests on and what "
              "the record cannot say.", size=10, colour=INK_SOFT)

    doc.add_heading("A. Route at intake" if strong
                    else "A. Routing at intake is not the lever", level=2)
    bullets(doc, ([
        f"Rests on 2.7: {gap:.0f} days between entry points, like-for-like.",
        "Would mean: choosing the faster path at receipt, not after assignment.",
        "Cannot see: whether the slower route carries harder cases.",
    ] if strong else [
        f"Rests on 2.7: named entry points differ by only {gap:.0f} days.",
        "Ruled out here. The same comparison in the other department gives the "
        "opposite answer.",
    ]))

    doc.add_heading("B. Target the phase that carries the time", level=2)
    bullets(doc, [
        (f"Rests on 2.7: the gap concentrates in {diffs[0][0].lower()}, "
         f"{diffs[0][1]:+.0f} days." if strong else
         f"Rests on 2.6: {LBL[heaviest].lower()} is "
         f"{pct(float(means[heaviest]), phase_total)} of the total."),
        "Would mean: a target on that step, not on total time to close.",
        "Cannot see: what happens during a span.",
    ])

    doc.add_heading("C. Bring the slowest desks to the median", level=2)
    bullets(doc, [
        "Rests on 2.9: same role, same claim type, order-of-magnitude spread.",
        "Would mean: asking what the fast offices do differently.",
        "Cannot see: caseload per officer, staffing, vacancies.",
        "No officer is named or identified anywhere in this note.",
    ])

    # -- appendices -----------------------------------------------------------
    doc.add_page_break()
    doc.add_heading("Appendix A. Sample", level=1)
    para(doc, "The sample narrows and never widens.", size=10, colour=INK_SOFT)
    table(doc, b.funnel_table(["S0", "S1", "S2", "S3"]),
          fmt={"% of the step above": lambda v: f"{float(v):.0f}%"},
          widths=[0.6, 3.0, 1.2, 1.5])
    table(doc, b.funnel_table(["P0", "P1", "P2"]),
          fmt={"% of the step above": lambda v: f"{float(v):.0f}%"},
          widths=[0.6, 3.0, 1.2, 1.5])
    bullets(doc, [
        f"P3, {n(F['P3'])}. Dated history, every office on the rung ladder.",
        f"P4, {n(F['P4'])}. The five spans add to the total.",
    ])

    doc.add_heading("Appendix B. Caveats", level=1)
    bullets(doc, [
        "\u201cDisposed\u201d means a file was closed. Only \u201cdisposed with "
        "benefit\u201d records that something reached the citizen.",
        "A long wait is not idleness.",
        "No officer-name column is ever read.",
        "Repeat filings are collapsed only within this department.",
        "Open claims are excluded from every timing figure.",
        "Fixing the subcategory removes only the complexity we can see.",
        "Years run July to June.",
    ])

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"{key}_bottlenecks.docx"
    doc.save(out)
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("department", choices=sorted(DEPARTMENTS), nargs="?",
                    help="omit to build both")
    args = ap.parse_args()
    for key in ([args.department] if args.department else sorted(DEPARTMENTS)):
        out = build(key)
        print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
