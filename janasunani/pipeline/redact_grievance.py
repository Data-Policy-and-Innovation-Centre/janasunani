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

    uv run --extra pipeline-core janasunani-redact-grievance \\
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


def _analyzer_version() -> str:
    """Stamp identifying the analyzer that produced a redaction.

    Package versions rather than a git commit: the recognizers are ours but the
    NER underneath them is not, and a redaction that differs between runs will
    differ because presidio or the spaCy model moved, not because the commit
    did.
    """
    from importlib.metadata import PackageNotFoundError, version

    parts = []
    for dist in ("presidio-analyzer", "spacy", "en-core-web-sm"):
        try:
            parts.append(f"{dist}={version(dist)}")
        except PackageNotFoundError:  # pragma: no cover - environment-dependent
            parts.append(f"{dist}=unknown")
    return " ".join(parts)


async def _load_pending_batch(
    conn, district: str, year: int, limit: int
) -> list[tuple[str, str]]:
    """Complaints in the slice with grievance text and no redaction row yet.

    The pending predicate *is* the resume mechanism, so it must stay a NOT
    EXISTS against the output table rather than an offset: rows land in
    ``grievance_redactions`` as the run proceeds, and an offset would skip
    unprocessed rows once earlier ones stopped matching.
    """
    done = select(GrievanceRedaction.ticket_no).where(
        GrievanceRedaction.ticket_no == Complaint.ticket_no
    )
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


async def _redact_slice(
    engine: AsyncEngine,
    district: str,
    year: int,
    redact: Any,
    limit: Optional[int] = None,
) -> dict[str, int]:
    version = _analyzer_version()
    processed = 0

    async with engine.begin() as conn:
        total, already = await _count_slice(conn, district, year)
    logger.info(
        "slice {}/{}: {} complaints with grievance text, {} already redacted",
        district,
        year,
        total,
        already,
    )

    while True:
        remaining = None if limit is None else limit - processed
        if remaining is not None and remaining <= 0:
            break
        size = BATCH_SIZE if remaining is None else min(BATCH_SIZE, remaining)

        async with engine.begin() as conn:
            batch = await _load_pending_batch(conn, district, year, size)
            if not batch:
                break

            now = datetime.now(timezone.utc)
            rows = [
                {
                    "ticket_no": ticket_no,
                    "grievance_redacted": redact(text),
                    "redacted_at": now,
                    "analyzer_version": version,
                }
                for ticket_no, text in batch
            ]
            await conn.execute(
                _dialect_upsert(
                    GrievanceRedaction, conn.dialect.name, rows, "ticket_no"
                )
            )

        processed += len(batch)
        logger.info("redacted {} of {} ({} this batch)", processed + already, total, len(batch))

    return {"total": total, "already_redacted": already, "processed": processed}


def redact_grievances(
    district: str,
    year: int,
    oltp_url: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict[str, int]:
    """Redact one district-year slice. Returns per-run counts."""
    # Imported here, not at module scope: pipeline-core is an optional extra,
    # and importing it eagerly would make this module unimportable in the
    # serving environment.
    from janasunani.pipeline.stages.pii_tagger import redact_text

    engine = create_async_engine(oltp_url or settings.OLTP_DB_URL)

    async def run() -> dict[str, int]:
        try:
            return await _redact_slice(engine, district, year, redact_text, limit=limit)
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
    args = parser.parse_args()

    counts = redact_grievances(
        args.district, args.year, oltp_url=args.oltp_url, limit=args.limit
    )
    logger.info(
        "done: {} processed this run, {} of {} redacted in slice {}/{}",
        counts["processed"],
        counts["already_redacted"] + counts["processed"],
        counts["total"],
        args.district,
        args.year,
    )


if __name__ == "__main__":
    main()
