"""Duplicate-adjusted workload (#76-style): filings vs distinct problems.

Computes the workload aggregate from the OLTP dedup index with a digest guard
per #137, and from the lake redacted text only — never raw grievance.

The arithmetic is simple: duplicate_adjustment = filings - dedup_groups size>1
more precisely, distinct_problems = count distinct duplicate_group_id in slice,
total_filings = count of complaints in slice (with redacted text), and
duplicate_adjustment = total_filings - distinct_problems.

Both workload and spike must share the same dedup_groups.digest; the publisher
writes them together and the serving layer refuses a mixed snapshot.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Optional

from loguru import logger

from janasunani.analytics.marts import open_lake
from janasunani.config import DATA_DIR
from janasunani.pipeline.dedup import (
    DEDUP_SOURCE_NAME,
    assert_group_source_snapshot,
    source_snapshot_id,
)

WORKLOAD_FIELDS = (
    "slice_district",
    "slice_category",
    "slice_period",
    "total_filings",
    "distinct_problems",
    "duplicate_adjustment",
    "source_name",
    "source_snapshot_id",
    "grouping_scope_snapshot_id",
)


def _read_lake_source_records(
    lake_dir: Optional[Path], district: str, year: int
) -> list[dict]:
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
        return records
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
                    "grouping_scope_snapshot_id FROM dedup_groups WHERE district = :d AND created_year = :y"
                ),
                {"d": district, "y": year},
            ).mappings().all()
            return list(rows)
    finally:
        engine.dispose()


def compute_workload(
    lake_dir: Optional[Path] = None,
    oltp_url: Optional[str] = None,
    district: Optional[str] = None,
    year: Optional[int] = None,
) -> dict[str, object]:
    from janasunani.config import DEMO_SLICE_DISTRICT, DEMO_SLICE_YEAR, settings

    district = district or DEMO_SLICE_DISTRICT
    year = year or DEMO_SLICE_YEAR
    oltp_url = oltp_url or settings.OLTP_DB_URL

    source_records = _read_lake_source_records(lake_dir, district, year)
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

    distinct_problems = len({r["duplicate_group_id"] for r in group_rows_raw})
    total_filings = len(group_rows_raw)
    duplicate_adjustment = total_filings - distinct_problems

    return {
        "slice_district": district,
        "slice_category": "all",
        "slice_period": str(year),
        "total_filings": total_filings,
        "distinct_problems": distinct_problems,
        "duplicate_adjustment": duplicate_adjustment,
        "source_name": DEDUP_SOURCE_NAME,
        "source_snapshot_id": expected_snapshot,
        # #317. The slice digest above cannot certify a corpus grouping: a
        # ticket outside this slice can bridge two groups inside it. This is
        # the digest of everything the grouping run read, so two artifacts
        # built from different assignments are distinguishable.
        "grouping_scope_snapshot_id": group_rows_raw[0]["grouping_scope_snapshot_id"],
    }


def write_workload(row: dict[str, object], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "workload.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(WORKLOAD_FIELDS))
        writer.writeheader()
        writer.writerow({k: row[k] for k in WORKLOAD_FIELDS})
    return path


def publish_workload(
    lake_dir: Optional[Path] = None,
    oltp_url: Optional[str] = None,
    district: Optional[str] = None,
    year: Optional[int] = None,
    out_dir: Optional[Path] = None,
) -> Path:
    row = compute_workload(lake_dir=lake_dir, oltp_url=oltp_url, district=district, year=year)
    dest = Path(out_dir) if out_dir else DATA_DIR / "aggregates"
    path = write_workload(row, dest)
    logger.success(f"workload -> {path} {row['total_filings']} filings, {row['distinct_problems']} distinct")
    return path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Duplicate-adjusted workload publisher")
    parser.add_argument("--lake-dir", type=Path, default=None)
    parser.add_argument("--oltp-url", type=str, default=None)
    parser.add_argument("--district", type=str, default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR / "aggregates")
    args = parser.parse_args(argv)
    publish_workload(
        lake_dir=args.lake_dir,
        oltp_url=args.oltp_url,
        district=args.district,
        year=args.year,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
