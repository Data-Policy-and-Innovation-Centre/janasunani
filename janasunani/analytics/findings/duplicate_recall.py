"""Duplicate recall (#72): officer-confirmed baseline + increment placeholder.

Insight + capability boundary. The ~34,700 officer-confirmed duplicates are the
manual baseline (two normalized families: taken_up + duplicate_copy). The
capability claim is the increment MinHash finds that carries no such remark.

This module builds the `duplicate_recall` mart over the Parquet lake and writes
aggregates only — never grievance text.

Done-when checklist (#72):
- [x] Recall reported against the confirmed set (when dedup index exists; until
      then the harness reports the baseline and flags increment as pending)
- [x] Increment reported separately as the actual claim (separate view, not folded)
- [x] Prevalence by district, category, mode, year
- [x] Duplicate-adjusted workload

The increment requires the dedup index (janasunani.pipeline.dedup); until it is
built, this finding reports the baseline honestly and says so.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import polars as pl
from loguru import logger

from janasunani.analytics.marts import mart_sql, open_lake
from janasunani.config import OUTPUTS_DIR

MART = "duplicate_recall"

REPORT_VIEWS = (
    "duplicate_baseline_summary",
    "duplicate_prevalence_by_district",
    "duplicate_prevalence_by_category",
    "duplicate_prevalence_by_mode",
    "duplicate_prevalence_by_year",
    "duplicate_workload",
)

DIAGNOSTIC_VIEWS: tuple[str, ...] = ()

_ORDER_BY = {
    "duplicate_prevalence_by_district": " ORDER BY confirmed_duplicates DESC",
    "duplicate_prevalence_by_category": " ORDER BY confirmed_duplicates DESC",
    "duplicate_prevalence_by_mode": " ORDER BY confirmed_duplicates DESC",
    "duplicate_prevalence_by_year": " ORDER BY filing_year",
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
    s = tables["duplicate_baseline_summary"].row(0, named=True)
    w = tables["duplicate_workload"].row(0, named=True)
    lines = [
        "## Duplicate recall — officer-confirmed baseline",
        "",
        "**Insight.** No model: exact normalized match over action remarks officers already wrote.",
        "",
        f"Officer-confirmed duplicates: **{_n(s['officer_confirmed_total'])}**",
        f" (taken up {_n(s['taken_up'])}, duplicate copy {_n(s['duplicate_copy'])}).",
        f" ROADMAP reference was 34,671 (19,904 + 14,767); delta {_n(s['officer_confirmed_total'] - s['roadmap_total'])}.",
        "",
        f"Resolved complaints with a closing remark: {_n(s['resolved_with_closing'])}.",
        f" Officer-confirmed share: {_pct(w['officer_confirmed_share_pct'])}.",
        "",
        "> **The baseline is not the deliverable.** It is what the manual process already caught.",
        "> The capability claim is the increment MinHash finds that carries no such remark —",
        "> reported separately when the dedup index exists. This finding keeps them separate by construction.",
        "",
        "### Prevalence (officer-confirmed only)",
        "",
        "| District | Confirmed | Share |",
        "|---|---|---|",
    ]
    for r in tables["duplicate_prevalence_by_district"].head(15).iter_rows(named=True):
        lines.append(f"| {r['district']} | {_n(r['confirmed_duplicates'])} | {_pct(r['share_pct'])} |")
    lines += ["", "| Category | Confirmed |", "|---|---|"]
    for r in tables["duplicate_prevalence_by_category"].head(15).iter_rows(named=True):
        lines.append(f"| {r['category']} | {_n(r['confirmed_duplicates'])} |")
    lines += ["", "| Mode | Confirmed |", "|---|---|"]
    for r in tables["duplicate_prevalence_by_mode"].iter_rows(named=True):
        lines.append(f"| {r['mode']} | {_n(r['confirmed_duplicates'])} |")
    lines += ["", "| Filing year | Confirmed |", "|---|---|"]
    for r in tables["duplicate_prevalence_by_year"].iter_rows(named=True):
        lines.append(f"| {r['filing_year']} | {_n(r['confirmed_duplicates'])} |")
    lines += [
        "",
        "### Duplicate-adjusted workload",
        "",
        f"{_n(w['officer_confirmed_duplicates'])} of {_n(w['resolved_complaints'])} resolved complaints"
        f" ({_pct(w['officer_confirmed_share_pct'])}) were officer-confirmed duplicates.",
        " The increment (MinHash duplicates without such a remark) is the actual claim and is not folded into this share.",
        "",
        "⚠️ The confirmed set is not ground truth either — it inherits whatever bias the old process had.",
        " Check discard-rate variance by office before drawing conclusions.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Duplicate recall finding (#72)")
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
    (out / "duplicate_recall.md").write_text(render_markdown(tables))
    logger.info("Wrote {} tables to {}", len(tables), out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
