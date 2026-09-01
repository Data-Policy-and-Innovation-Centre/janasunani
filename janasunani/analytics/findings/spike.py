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


SPIKE_AGG_FIELDS = (
    "slice_district",
    "slice_category",
    "slice_period",
    "filings",
    "distinct_problems",
    "distinct_citizens",
    "source_name",
    "source_snapshot_id",
    "grouping_scope_snapshot_id",
    "interpretation",
)


def _read_lake_source_records(lake_dir: Optional[Path], district: str, year: int):
    con = open_lake(lake_dir=lake_dir, tables=("complaints", "grievance_redactions"))
    try:
        sql = """
            SELECT c.ticket_no, c.district, c.created_year, c.created_on,
                   c.petitioner_mobile, c.petitioner_email, c.petitioner_name,
                   g.grievance_redacted
            FROM complaints c
            JOIN grievance_redactions g USING (ticket_no)
            WHERE c.district = ? AND c.created_year = ?
              AND g.grievance_redacted IS NOT NULL
        """
        df = con.execute(sql, [district, year]).pl()
        records = []
        for row in df.iter_rows(named=True):
            records.append(
                {
                    "ticket_no": row["ticket_no"],
                    "district": row["district"],
                    "created_year": row["created_year"],
                    "created_on": row["created_on"],
                    "petitioner_mobile": row["petitioner_mobile"],
                    "petitioner_email": row["petitioner_email"],
                    "petitioner_name": row["petitioner_name"],
                    "grievance_redacted": row["grievance_redacted"],
                }
            )
        return records, df
    finally:
        con.close()


def _read_oltp_groups(oltp_url: str, district: str, year: int):
    sync_url = oltp_url.replace("sqlite+aiosqlite://", "sqlite://").replace("postgresql+asyncpg://", "postgresql://")
    from sqlalchemy import create_engine, text

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT ticket_no, duplicate_group_id, group_size, source_name, source_snapshot_id, "
                    "grouping_scope_snapshot_id, block_key "
                    "FROM dedup_groups WHERE district = :d AND created_year = :y"
                ),
                {"d": district, "y": year},
            ).mappings().all()
            return list(rows)
    finally:
        engine.dispose()


def _read_oltp_signatures(oltp_url: str, district: str, year: int):
    sync_url = oltp_url.replace("sqlite+aiosqlite://", "sqlite://").replace("postgresql+asyncpg://", "postgresql://")
    from sqlalchemy import create_engine, text

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT ticket_no, identity_key_mobile, identity_key_email "
                    "FROM dedup_signatures WHERE district = :d AND created_year = :y"
                ),
                {"d": district, "y": year},
            ).mappings().all()
            return list(rows)
    finally:
        engine.dispose()


def compute_spike_with_dedup(
    lake_dir: Optional[Path] = None,
    oltp_url: Optional[str] = None,
    district: Optional[str] = None,
    year: Optional[int] = None,
) -> dict[str, object]:
    from janasunani.config import DEMO_SLICE_DISTRICT, DEMO_SLICE_YEAR, settings
    from janasunani.pipeline.dedup import DEDUP_SOURCE_NAME, assert_group_source_snapshot, source_snapshot_id

    district = district or DEMO_SLICE_DISTRICT
    year = year or DEMO_SLICE_YEAR
    oltp_url = oltp_url or settings.OLTP_DB_URL

    source_records, _ = _read_lake_source_records(lake_dir, district, year)
    if not source_records:
        raise ValueError(f"No source records for {district}/{year}")
    expected_snapshot = source_snapshot_id(source_records)

    group_rows_raw = _read_oltp_groups(oltp_url, district, year)
    if not group_rows_raw:
        raise ValueError(f"No dedup_groups for {district}/{year}")
    group_rows = [
        {
            "ticket_no": r["ticket_no"],
            "source_name": r["source_name"],
            "source_snapshot_id": r["source_snapshot_id"],
            "grouping_scope_snapshot_id": r["grouping_scope_snapshot_id"],
        }
        for r in group_rows_raw
    ]
    assert_group_source_snapshot(group_rows, source_records)

    sig_rows = _read_oltp_signatures(oltp_url, district, year)
    sig_by_ticket = {r["ticket_no"]: r for r in sig_rows}
    groups_by_ticket = {r["ticket_no"]: r["duplicate_group_id"] for r in group_rows_raw}

    tables = compute(lake_dir=lake_dir)
    cands = tables["spike_candidates"]
    if cands.height == 0:
        raise ValueError("No spike candidates found")
    top = cands.row(0, named=True)
    spike_cat = top["category"]
    spike_district = top["district"]
    spike_filings = int(top["filings"])

    con = open_lake(lake_dir=lake_dir, tables=("complaints",))
    try:
        import datetime
        week_start = top["week"]
        if isinstance(week_start, str):
            week_start = datetime.date.fromisoformat(week_start)
        elif isinstance(week_start, datetime.datetime):
            week_start = week_start.date()
        week_end = week_start + datetime.timedelta(days=6)
        sql = """
            SELECT ticket_no, category, district, created_on
            FROM complaints
            WHERE category = ? AND district = ?
              AND CAST(created_on AS DATE) BETWEEN ? AND ?
        """
        week_df = con.execute(sql, [spike_cat, spike_district, week_start.isoformat(), week_end.isoformat()]).pl()
    finally:
        con.close()

    if week_df.height == 0:
        distinct_problems = spike_filings
        distinct_citizens = spike_filings
    else:
        tickets = week_df["ticket_no"].to_list()
        distinct_problems = len({groups_by_ticket.get(t, t) for t in tickets})
        citizen_keys = set()
        for t in tickets:
            sig = sig_by_ticket.get(t)
            if sig is None:
                continue
            key = sig["identity_key_mobile"] or sig["identity_key_email"]
            if key:
                citizen_keys.add(key)
            else:
                citizen_keys.add(f"ticket:{t}")
        distinct_citizens = len(citizen_keys) if citizen_keys else len(tickets)
        distinct_problems = min(distinct_problems, spike_filings)
        distinct_citizens = min(distinct_citizens, spike_filings)
        if distinct_problems == 0:
            distinct_problems = 1
        if distinct_citizens == 0:
            distinct_citizens = distinct_problems

    interpretation = (
        f"{spike_filings} filings in spike week, {distinct_problems} distinct problems, "
        f"{distinct_citizens} distinct citizens. Lift {top['lift_vs_trailing']:.1f}x vs trailing mean."
        if top.get("lift_vs_trailing") is not None
        else f"{spike_filings} filings, {distinct_problems} problems, {distinct_citizens} citizens."
    )

    return {
        "slice_district": spike_district,
        "slice_category": spike_cat,
        "slice_period": str(week_start),
        "filings": spike_filings,
        "distinct_problems": distinct_problems,
        "distinct_citizens": distinct_citizens,
        "source_name": DEDUP_SOURCE_NAME,
        "source_snapshot_id": expected_snapshot,
        "grouping_scope_snapshot_id": group_rows_raw[0]["grouping_scope_snapshot_id"],
        "interpretation": interpretation,
    }


def write_spike_aggregate(row: dict[str, object], out_dir: Path) -> Path:
    import csv
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "spike.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SPIKE_AGG_FIELDS))
        writer.writeheader()
        writer.writerow({k: row[k] for k in SPIKE_AGG_FIELDS})
    return path


def publish_spike(
    lake_dir: Optional[Path] = None,
    oltp_url: Optional[str] = None,
    district: Optional[str] = None,
    year: Optional[int] = None,
    out_dir: Optional[Path] = None,
) -> Path:
    from janasunani.config import DATA_DIR
    row = compute_spike_with_dedup(lake_dir=lake_dir, oltp_url=oltp_url, district=district, year=year)
    dest = Path(out_dir) if out_dir else DATA_DIR / "aggregates"
    path = write_spike_aggregate(row, dest)
    logger.success(f"spike -> {path} {row['filings']} filings, {row['distinct_problems']} problems")
    return path


def publish_intelligence_aggregates(
    lake_dir: Optional[Path] = None,
    oltp_url: Optional[str] = None,
    district: Optional[str] = None,
    year: Optional[int] = None,
    out_dir: Optional[Path] = None,
) -> dict[str, Path]:
    from janasunani.analytics.findings.workload import compute_workload, write_workload
    from janasunani.config import DATA_DIR
    dest = Path(out_dir) if out_dir else DATA_DIR / "aggregates"
    dest.mkdir(parents=True, exist_ok=True)
    w_row = compute_workload(lake_dir=lake_dir, oltp_url=oltp_url, district=district, year=year)
    s_row = compute_spike_with_dedup(lake_dir=lake_dir, oltp_url=oltp_url, district=district, year=year)
    if w_row["source_snapshot_id"] != s_row["source_snapshot_id"]:
        raise ValueError("workload and spike digests diverged — refusing to publish mixed snapshot")
    # #317. Equal slice digests are no longer sufficient: a corpus grouping
    # depends on records outside this slice, so two runs can agree here and
    # still carry different group assignments.
    if w_row["grouping_scope_snapshot_id"] != s_row["grouping_scope_snapshot_id"]:
        raise ValueError(
            "workload and spike grouping scopes diverged — refusing to publish "
            "figures from two different group assignments"
        )
    if w_row["source_name"] != s_row["source_name"]:
        raise ValueError("workload and spike source names diverged")
    w_path = write_workload(w_row, dest)
    s_path = write_spike_aggregate(s_row, dest)
    return {"workload": w_path, "spike": s_path}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Spike decomposition finding (#78)")
    parser.add_argument("--lake-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=OUTPUTS_DIR / "findings")
    parser.add_argument("--print-sql", action="store_true")
    parser.add_argument("--publish-aggregates", action="store_true", help="Also publish workload+spike aggregates to DATA_DIR/aggregates with digest guard")
    parser.add_argument("--oltp-url", type=str, default=None)
    parser.add_argument("--district", type=str, default=None)
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args(argv)
    if args.print_sql:
        print(sql_text())
        return 0
    if args.publish_aggregates:
        publish_intelligence_aggregates(
            lake_dir=args.lake_dir,
            oltp_url=args.oltp_url,
            district=args.district,
            year=args.year,
        )
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

