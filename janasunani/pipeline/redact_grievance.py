"""Presidio pass over the historical ``complaints.grievance`` text.

``complaints.grievance`` (``grievanceSubject``) is citizen prose that predates
the document pipeline and has never met ``pii_tagger``. It is controlled by
access today; ROADMAP §3.2 requires it to be controlled by redaction before it
reaches the lake. This is that pass.

Output goes to ``grievance_redactions``, a side table keyed on ``ticket_no``,
so the original text is never overwritten. A redaction is a derived artifact of
one analyzer version, and re-running a better analyzer must not have destroyed
its own input.

**Resumable by construction.** Each batch selects complaints in the slice that
have no ``grievance_redactions`` row yet, so an interrupted run is restarted by
running the same command again. No checkpoint file, no bookkeeping to get
wrong. Re-running after the slice is complete is a no-op.

Run:

    uv run --extra pii janasunani-redact-grievance \\
        --district Khordha --year 2024

``--district`` and ``--year`` have no defaults on purpose: the slice is a
decision (#64), and a job that redacts citizen text should not have a default
scope that someone can trip over.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from janasunani.config import settings
from janasunani.db.models import Complaint, GrievanceRedaction

# Reuses the exporter's helper rather than writing a third upsert. A shared
# home for this and migration/from_mysql.py is the right end state; it is
# review surface this sprint cannot afford. Tracked as tech debt.
from janasunani.pipeline.export import _dialect_upsert

BATCH_SIZE = 500


def _repo_commit() -> str:
    """Short commit of this checkout, suffixed -dirty if the source is modified.

    Pathspecs are scoped to the analyzer's own tree so an unrelated edit does
    not mark a redaction dirty.
    """
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "janasunani"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return f"{commit}-dirty" if dirty else commit
    except (subprocess.CalledProcessError, OSError):  # pragma: no cover
        return "unknown"


def _analyzer_version() -> str:
    """Stamp identifying the analyzer that produced a redaction.

    Two halves, because either can change the output on its own. The NER and
    its model are third-party, so their versions matter; but the recognizers,
    thresholds and replacement behaviour in ``pii_tagger`` are ours and change
    without any dependency moving. #55, #56 and #91 all altered what gets
    redacted while every package version stayed put, so a package-only stamp
    would mark materially different redactions as identical and make targeted
    reprocessing impossible.
    """
    from importlib.metadata import PackageNotFoundError, version

    parts = [f"janasunani={_repo_commit()}"]
    for dist in ("presidio-analyzer", "spacy", "en-core-web-sm"):
        try:
            parts.append(f"{dist}={version(dist)}")
        except PackageNotFoundError:  # pragma: no cover - environment-dependent
            parts.append(f"{dist}=unknown")
    return " ".join(parts)


async def _load_pending_batch(
    conn,
    district: str,
    year: int,
    limit: int,
    version: str | None = None,
) -> list[tuple[str, str]]:
    """Complaints in the slice with grievance text and no current redaction.

    The pending predicate *is* the resume mechanism, so it must stay a NOT
    EXISTS against the output table rather than an offset: rows land in
    ``grievance_redactions`` as the run proceeds, and an offset would skip
    unprocessed rows once earlier ones stopped matching.

    When ``version`` is given, a row stamped with a *different*
    ``analyzer_version`` also counts as pending (#130). Without this the
    stamp is write-only: a completed slice is excluded forever, so a better
    analyzer can never reach the rows it was built to fix.
    """
    done = select(GrievanceRedaction.ticket_no).where(
        GrievanceRedaction.ticket_no == Complaint.ticket_no
    )
    if version is not None:
        done = done.where(GrievanceRedaction.analyzer_version == version)
    stmt = (
        select(Complaint.ticket_no, Complaint.grievance)
        .where(
            Complaint.district == district,
            Complaint.created_year == year,
            Complaint.grievance.isnot(None),
            Complaint.grievance != "",
            ~done.exists(),
        )
        .order_by(Complaint.ticket_no)
        .limit(limit)
    )
    result = await conn.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def _count_slice(conn, district: str, year: int) -> tuple[int, int]:
    """(complaints with grievance text in the slice, already redacted)."""
    total = await conn.scalar(
        select(func.count())
        .select_from(Complaint)
        .where(
            Complaint.district == district,
            Complaint.created_year == year,
            Complaint.grievance.isnot(None),
            Complaint.grievance != "",
        )
    )
    done = await conn.scalar(
        select(func.count())
        .select_from(GrievanceRedaction)
        .join(Complaint, Complaint.ticket_no == GrievanceRedaction.ticket_no)
        .where(
            Complaint.district == district,
            Complaint.created_year == year,
        )
    )
    return int(total or 0), int(done or 0)


async def _count_stale(conn, district: str, year: int, version: str) -> int:
    """Rows in the slice redacted by a different analyzer than the current one.

    Reported by every run whether or not it acts on them, so the need for a
    refresh is visible rather than something you have to think to check.
    """
    return int(
        await conn.scalar(
            select(func.count())
            .select_from(GrievanceRedaction)
            .join(Complaint, Complaint.ticket_no == GrievanceRedaction.ticket_no)
            .where(
                Complaint.district == district,
                Complaint.created_year == year,
                GrievanceRedaction.analyzer_version != version,
            )
        )
        or 0
    )


async def _redact_slice(
    engine: AsyncEngine,
    district: str,
    year: int,
    redact: Any,
    limit: Optional[int] = None,
    refresh_stale: bool = False,
) -> dict[str, int]:
    version = _analyzer_version()
    processed = 0

    async with engine.begin() as conn:
        total, already = await _count_slice(conn, district, year)
        stale = await _count_stale(conn, district, year, version)
    logger.info(
        "slice {}/{}: {} complaints with grievance text, {} already redacted",
        district,
        year,
        total,
        already,
    )
    if stale:
        logger.warning(
            "{} row(s) were redacted by a different analyzer than this one. "
            "{}",
            stale,
            "Reprocessing them (--refresh-stale)."
            if refresh_stale
            else "Leaving them as they are; pass --refresh-stale to reprocess.",
        )

    while True:
        remaining = None if limit is None else limit - processed
        if remaining is not None and remaining <= 0:
            break
        size = BATCH_SIZE if remaining is None else min(BATCH_SIZE, remaining)

        async with engine.begin() as conn:
            batch = await _load_pending_batch(
                conn, district, year, size, version=version if refresh_stale else None
            )
        if not batch:
            break

        # Presidio runs here, off any open transaction. It is CPU-bound and
        # sequential (one call per row -- parallelising it is blocked on the
        # pii/pipeline-core numpy ABI conflict), so doing this inside
        # engine.begin() held a write transaction open on the box that also
        # hosts production Postgres for as long as the batch took to redact.
        # If the process dies right here, the batch was never upserted, so
        # the NOT EXISTS predicate in _load_pending_batch picks it back up on
        # the next run -- same outcome as a rollback of the old transaction.
        #
        # Naive UTC. Every timestamp column in this schema is TIMESTAMP
        # WITHOUT TIME ZONE, and asyncpg refuses to bind a tz-aware value
        # into one while SQLite silently accepts it -- so an aware value
        # here passes every local test and fails on the first batch against
        # the deployed Postgres. Same normalisation as db/crud.py.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            {
                "ticket_no": ticket_no,
                "grievance_redacted": redact(text),
                "redacted_at": now,
                "analyzer_version": version,
            }
            for ticket_no, text in batch
        ]

        async with engine.begin() as conn:
            await conn.execute(
                _dialect_upsert(
                    GrievanceRedaction, conn.dialect.name, rows, "ticket_no"
                )
            )

        processed += len(batch)
        # Rows carrying a *current* redaction. One definition for both modes.
        #
        # `already + processed` double-counts in a refresh, because `already`
        # includes the stale rows being reprocessed ("62544 of 55544"). Bare
        # `processed` is wrong the other way: a slice of 48,544 current plus
        # 7,000 stale rows would finish at "7000 of 55544" and read as a run
        # that gave up. Subtracting the stale rows from `already` gives the
        # ones that were current at the start, and `processed` adds the ones
        # made current since.
        current = already - stale + processed
        logger.info("redacted {} of {} ({} this batch)", current, total, len(batch))

    return {
        "total": total,
        "already_redacted": already,
        "processed": processed,
        "stale_at_start": stale,
    }


def redact_grievances(
    district: str,
    year: int,
    oltp_url: Optional[str] = None,
    limit: Optional[int] = None,
    refresh_stale: bool = False,
) -> dict[str, int]:
    """Redact one district-year slice. Returns per-run counts."""
    # Imported here, not at module scope: pipeline-core is an optional extra,
    # and importing it eagerly would make this module unimportable in the
    # serving environment.
    from janasunani.pipeline.stages.pii_tagger import redact_text

    engine = create_async_engine(oltp_url or settings.OLTP_DB_URL)

    async def run() -> dict[str, int]:
        try:
            return await _redact_slice(
                engine,
                district,
                year,
                redact_text,
                limit=limit,
                refresh_stale=refresh_stale,
            )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Redact the historical complaints.grievance text for one "
            "district-year slice into grievance_redactions."
        )
    )
    parser.add_argument("--district", required=True, help="District name, as stored in complaints.district.")
    parser.add_argument("--year", required=True, type=int, help="created_year to process.")
    parser.add_argument(
        "--oltp-url", default=None, help="OLTP DB URL (default: settings.OLTP_DB_URL)."
    )
    parser.add_argument(
        "--limit",
        default=None,
        type=int,
        help="Stop after this many complaints. For a smoke test before the full run.",
    )
    parser.add_argument(
        "--refresh-stale",
        action="store_true",
        help=(
            "Also reprocess rows redacted by a different analyzer version. "
            "Off by default: a backfill over citizen text should not silently "
            "change scope because an unrelated commit landed."
        ),
    )
    args = parser.parse_args()

    counts = redact_grievances(
        args.district,
        args.year,
        oltp_url=args.oltp_url,
        limit=args.limit,
        refresh_stale=args.refresh_stale,
    )
    # Same arithmetic as the per-batch progress line: rows carrying a *current*
    # redaction. The batch line was fixed in #141/#147 and this one was not, so
    # tonight's refresh ended on "111088 of 55544" -- the number that gets
    # pasted into a run record, which is worse than the one that scrolls past.
    logger.info(
        "done: {} processed this run, {} of {} redacted in slice {}/{}",
        counts["processed"],
        counts["already_redacted"] - counts["stale_at_start"] + counts["processed"],
        counts["total"],
        args.district,
        args.year,
    )


if __name__ == "__main__":
    main()
