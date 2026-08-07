"""Spike decomposition (#78): one worked spike as three numbers.

Insight + capability boundary. EWMA over (category × district × week) finds a
spike in a day; the capability is decomposing it into filings / distinct
problems (dedup clusters) / distinct citizens (signatories). A campaign is
not a false spike — spikes are labelled by which measure drove them.

Baselined against same period last year, not last month (monsoon seasonality).
Requires dedup index for full decomposition; until built, reports filings and
flags the other two as pending — the file still proves the mart and the
three-number contract.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import polars as pl
from loguru import logger

from janasunani.analytics.marts import mart_sql, open_lake
from janasunani.config import OUTPUTS_DIR

MART = "spike"

REPORT_VIEWS = (
    "spike_weekly_counts",
    "spike_candidates",
    "spike_decomposition",
    "spike_worked_example",
)

_ORDER_BY = {
    "spike_weekly_counts": " ORDER BY category, district, week",
    "spike_candidates": " ORDER BY lift_vs_trailing DESC",
}


def sql_text() -> str:
    return mart_sql(MART)


def compute(lake_dir: Optional[Path] = None) -> dict[str, pl.DataFrame]:
    con = open_lake(MART, lake_dir=lake_dir)
    try:
        return {
            view: con.execute(f"SELECT * FROM {view}{_ORDER_BY.get(view, '')}").pl()  # noqa: S608
            for view in REPORT_VIEWS
        }
    finally:
        con.close()


def _n(v) -> str:
    return "n/a" if v is None else f"{int(v):,}"


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def render_markdown(tables: dict[str, pl.DataFrame]) -> str:
    cand = tables["spike_candidates"]
    ex = tables["spike_worked_example"]
    lines = [
        "## One worked spike, decomposed",
        "",
        "**Capability claim is the decomposition, not the detection.** EWMA over (category × district × week) is ordinary; telling `up 300%, 1 cluster, 480 signatories` (campaign) from `up 300%, 260 clusters` (diffuse problems) needs the dedup index.",
        "",
        f"Candidate spikes found: **{_n(cand.height)}** (filings ≥2× trailing 8-week mean and ≥1.5× same week last year).",
        "",
        "| Category | District | Week | Filings | Trailing mean | Lift | YoY lift |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in cand.head(5).iter_rows(named=True):
        lift = r["lift_vs_trailing"]
        yoy = r["lift_vs_yoy"]
        lift_s = f"{lift:.1f}×" if lift is not None else "n/a"
        yoy_s = f"{yoy:.1f}×" if yoy is not None else "n/a"
        lines.append(
            f"| {r['category']} | {r['district']} | {r['week']} | {_n(r['filings'])} | "
            f"{_n(r['trailing_8wk_mean'])} | {lift_s} | {yoy_s} |"
        )
    if ex.height:
        row = ex.row(0, named=True)
        lines += [
            "",
            "### Worked example (top candidate)",
            "",
            f"**{row['category']} × {row['district']}** week {row['week']}: "
            f"{_n(row['filings'])} filings, trailing mean {_n(row['trailing_8wk_mean'])}, "
            f"lift {row['lift_vs_trailing']:.1f}×" + (f", YoY lift {row['lift_vs_yoy']:.1f}×" if row['lift_vs_yoy'] is not None else "") + "." if row["lift_vs_trailing"] is not None else
            f"**{row['category']} × {row['district']}** week {row['week']}: {_n(row['filings'])} filings.",
            "",
            "Decomposition (when dedup index exists): filings / distinct problems (clusters) / distinct citizens (signatories).",
            " Until built, the mart reports `distinct_clusters` and `distinct_signatories` as NULL with status `pending dedup index` — the three-number contract is still enforced.",
            "",
            "> ⚠️ **A campaign is not a false spike.** Spike detection must not run on de-duplicated counts.",
            "> 500 citizens filing about the same road is a real signal; collapsing them to 1 destroys what government needs to see. Spikes are labelled, never suppressed.",
        ]
    else:
        lines += ["", "No candidate spike met the threshold on this slice.", ""]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Spike decomposition finding (#78)")
    parser.add_argument("--lake-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=OUTPUTS_DIR / "findings")
    parser.add_argument("--print-sql", action="store_true")
    args = parser.parse_args(argv)
    if args.print_sql:
        print(sql_text())
        return 0
    tables = compute(lake_dir=args.lake_dir)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.write_csv(out / f"{name}.csv")
    (out / "spike.md").write_text(render_markdown(tables))
    logger.info("Wrote {} tables to {}", len(tables), out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
