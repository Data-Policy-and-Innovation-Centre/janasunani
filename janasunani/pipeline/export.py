"""Export the pipeline's artifact DB into the OLTP store.

The document pipeline writes its outputs to a local SQLite artifact DB
(``pages``/``documents`` — see :mod:`janasunani.pipeline.db`). This exporter
upserts those rows into the OLTP store (any engine via ``OLTP_DB_URL``), from
where the ``materialize`` step carries them into the Parquet lake alongside
the complaints data.

Upsert semantics: ON CONFLICT on the primary key DO UPDATE — re-exporting
after further pipeline stages ran (e.g. redaction filled in ``redacted_text``)
refreshes the existing rows. Idempotent by construction.
"""

import argparse
import asyncio
import sqlite3
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from janasunani.config import settings
from janasunani.db.models import PipelineDocument, PipelinePage

BATCH_SIZE = 1000

# (ORM model, pipeline-DB table name, conflict key)
_EXPORTS = (
    (PipelinePage, "pages", "page_id"),
    (PipelineDocument, "documents", "doc_id"),
)


def _dialect_upsert(model, dialect_name: str, rows: list[dict], key: str):
    """Portable INSERT ... ON CONFLICT (key) DO UPDATE for sqlite/postgres."""
    if dialect_name == "postgresql":
        stmt = pg_insert(model).values(rows)
    elif dialect_name == "sqlite":
        stmt = sqlite_insert(model).values(rows)
    else:
        raise ValueError(f"Unsupported OLTP dialect for export: {dialect_name!r}")
    update_cols = {
        c.name: getattr(stmt.excluded, c.name)
        for c in model.__table__.columns
        if c.name != key
    }
    return stmt.on_conflict_do_update(index_elements=[key], set_=update_cols)


def _iter_batches(con: sqlite3.Connection, table: str, columns: list[str]):
    """Stream rows in BATCH_SIZE chunks — pages carry multi-KB text blobs, so
    materializing a full backfill's table would exhaust memory before the
    first upsert."""
    cur = con.execute(f"SELECT {', '.join(columns)} FROM {table}")  # noqa: S608
    while True:
        rows = cur.fetchmany(BATCH_SIZE)
        if not rows:
            return
        yield [dict(zip(columns, row, strict=True)) for row in rows]


async def _export(pipeline_db: Path, engine: AsyncEngine) -> dict[str, int]:
    counts: dict[str, int] = {}
    con = sqlite3.connect(pipeline_db)
    try:
        async with engine.begin() as conn:
            dialect_name = engine.dialect.name
            for model, table, key in _EXPORTS:
                columns = [c.name for c in model.__table__.columns]
                total = 0
                for batch in _iter_batches(con, table, columns):
                    await conn.execute(_dialect_upsert(model, dialect_name, batch, key))
                    total += len(batch)
                counts[table] = total
                logger.info(f"exported {table}: {total:,} rows upserted")
    finally:
        con.close()
    return counts


def export_pipeline_db(
    pipeline_db: Path, oltp_url: Optional[str] = None
) -> dict[str, int]:
    """Upsert the pipeline artifact DB's tables into the OLTP store.

    Returns ``{table: rows_upserted}``. The OLTP schema must already be at
    head (``alembic upgrade head``).
    """
    pipeline_db = Path(pipeline_db)
    if not pipeline_db.exists():
        raise FileNotFoundError(f"pipeline artifact DB not found: {pipeline_db}")
    engine = create_async_engine(oltp_url or settings.OLTP_DB_URL)

    async def run() -> dict[str, int]:
        try:
            return await _export(pipeline_db, engine)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the pipeline artifact DB (pages/documents) into the OLTP store."
    )
    parser.add_argument("--db", type=Path, required=True, help="Pipeline SQLite artifact DB.")
    parser.add_argument(
        "--oltp-url", default=None, help="OLTP DB URL (default: settings.OLTP_DB_URL)."
    )
    args = parser.parse_args()
    counts = export_pipeline_db(args.db, oltp_url=args.oltp_url)
    for table, n in counts.items():
        print(f"{table}: {n:,}")


if __name__ == "__main__":
    main()
