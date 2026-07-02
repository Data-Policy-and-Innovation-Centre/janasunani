"""Migrate the grievance complaint store from a MySQL source into the local DB.

This is the **single validated insert routine** both migration paths share:

- the live-MySQL sync calls :func:`run_migration` with a running server's URL;
- the cold-start dump loader (:mod:`janasunani.migration.from_sql_dump`) restores the
  ``mysqldump`` into a throwaway MySQL and calls :func:`run_migration` against it.

Each source table (``t_janasunani_etl_pre_data`` and
``t_janasunani_etl_history_pre_data``) is read **once** via a server-side
streaming cursor; rows are validated/renamed through the Pydantic schemas (the
one source→ORM column map) and bulk-inserted with driver ``executemany`` +
on-conflict-do-nothing. Action-history rows resolve their complaint ``ticket_no``
from an in-memory ``{tracking_id: ticket_no}`` map (a dict lookup), so there is
no giant ``IN (...)`` filter and no ``OFFSET`` re-scan.
"""

import asyncio
from typing import Dict, Optional

from loguru import logger
from sqlalchemy import Engine, MetaData, Table, create_engine, func, select
from sqlalchemy import insert as core_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from janasunani.config import settings
from janasunani.db.models import ActionHistory as ActionHistoryModel
from janasunani.db.models import Base
from janasunani.db.models import Complaint as ComplaintModel
from janasunani.ingestion.schemas import ActionHistory as ActionHistorySchema
from janasunani.ingestion.schemas import Complaint as ComplaintSchema

COMPLAINT_TABLE = "t_janasunani_etl_pre_data"
ACTION_HISTORY_TABLE = "t_janasunani_etl_history_pre_data"
CHUNK_SIZE = 2000
LOG_EVERY = 100_000


def setup_engines(mysql_url: str, target_db_url: str) -> tuple[Engine, AsyncEngine]:
    """Build a sync MySQL source engine and an async target engine."""
    mysql_engine = create_engine(mysql_url, pool_pre_ping=True, pool_recycle=3600)
    target_engine = create_async_engine(target_db_url)
    return mysql_engine, target_engine


async def init_db(target_engine: AsyncEngine) -> None:
    """Create the target tables if they do not already exist."""
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_existing_ticket_no(db: AsyncSession) -> set[str]:
    result = await db.execute(select(ComplaintModel.ticket_no).distinct())
    return set(result.scalars().all())


def _dialect_insert(model, dialect_name: str):
    """Return ``(insert_construct, supports_on_conflict)`` for the target dialect,
    so the migration stays engine-portable (SQLite ↔ Postgres ↔ other)."""
    if dialect_name == "postgresql":
        return pg_insert(model), True
    if dialect_name == "sqlite":
        return sqlite_insert(model), True
    return core_insert(model), False  # generic: dedup via the per-row fallback


async def _insert_ignore(
    model, target_sess: AsyncSession, records: list[dict], dialect_name: str
) -> int:
    """Bulk insert ``records`` (driver executemany) with on-conflict-do-nothing,
    falling back to per-row inserts only if the batch errors."""
    if not records:
        return 0
    ins, on_conflict = _dialect_insert(model, dialect_name)
    stmt = ins.on_conflict_do_nothing() if on_conflict else ins
    try:
        await target_sess.execute(stmt, records)
        await target_sess.commit()
        return len(records)
    except (IntegrityError, OperationalError):
        await target_sess.rollback()

    inserted = 0
    for rec in records:
        try:
            ins1, oc1 = _dialect_insert(model, dialect_name)
            one = ins1.values(rec)
            one = one.on_conflict_do_nothing() if oc1 else one
            await target_sess.execute(one)
            await target_sess.commit()
            inserted += 1
        except (IntegrityError, OperationalError):
            await target_sess.rollback()
    return inserted


def _stream(mysql_engine: Engine, table_name: str):
    """Open a server-side streaming cursor over ``table_name`` (full scan).

    No ORDER BY on purpose: ``SELECT *`` over a ``longtext`` column forces a
    filesort that buffers the long text and exhausts MySQL's sort memory at this
    scale. Reproducibility instead comes from (1) the deterministic ``min``
    tracking map and (2) the source's stable clustered-scan order (a PK-less
    InnoDB table scans in insertion order; SQLite in rowid order), so insertion
    order — hence autoincrement ids and which duplicate is kept — is stable run
    to run. Returns ``(connection, mapping_result)``; caller must close it."""
    meta = MetaData()
    table = Table(table_name, meta, autoload_with=mysql_engine)
    conn = mysql_engine.connect().execution_options(stream_results=True)
    result = conn.execute(select(table)).mappings()
    return conn, result


async def migrate_complaints(
    mysql_engine: Engine,
    target_sess: AsyncSession,
    dialect_name: str,
    chunk_size: int = CHUNK_SIZE,
) -> Dict[str, str]:
    """Stream all complaints from MySQL, insert the new ones, and return a
    ``{tracking_id: ticket_no}`` map for the action-history pass."""
    existing = await get_existing_ticket_no(target_sess)
    logger.info(f"Streaming complaints (target already has {len(existing)})")

    conn, result = _stream(mysql_engine, COMPLAINT_TABLE)
    seen = inserted = 0
    try:
        while True:
            rows = await asyncio.to_thread(result.fetchmany, chunk_size)
            if not rows:
                break
            recs = []
            for r in rows:
                try:
                    rec = ComplaintSchema(**r).model_dump(by_alias=False)
                except Exception as e:  # noqa: BLE001 - skip/log a bad source row
                    logger.error(f"Complaint validation error: {e}")
                    continue
                if rec["ticket_no"] in existing:
                    continue
                existing.add(rec["ticket_no"])
                recs.append(rec)
            inserted += await _insert_ignore(ComplaintModel, target_sess, recs, dialect_name)
            seen += len(rows)
            if seen % LOG_EVERY < chunk_size:
                logger.info(f"complaints: read {seen:,}, inserted {inserted:,}")
    finally:
        await asyncio.to_thread(conn.close)

    logger.success(f"Complaints done: read {seen:,}, inserted {inserted:,}")
    # Deterministic tracking map: when a tracking_id maps to multiple complaints,
    # always pick min(ticket_no) (GROUP BY) so the resolution — and therefore the
    # action-history dedup outcome — is reproducible run to run.
    res = await target_sess.execute(
        select(ComplaintModel.tracking_id, func.min(ComplaintModel.ticket_no))
        .where(ComplaintModel.tracking_id.isnot(None))
        .group_by(ComplaintModel.tracking_id)
    )
    tracking_map = {tid: tno for tid, tno in res.all()}
    logger.info(f"Tracking map covers {len(tracking_map):,} complaints")
    return tracking_map


async def migrate_action_history(
    mysql_engine: Engine,
    target_sess: AsyncSession,
    tracking_map: Dict[str, str],
    dialect_name: str,
    chunk_size: int = CHUNK_SIZE,
) -> None:
    """Stream all action-history rows once, resolving each source ``trackingId``
    to its complaint's ``ticket_no`` via ``tracking_map`` (rows with no match are
    skipped)."""
    if not tracking_map:
        logger.info("No action_history to migrate (empty tracking_map).")
        return

    conn, result = _stream(mysql_engine, ACTION_HISTORY_TABLE)
    seen = matched = inserted = 0
    try:
        while True:
            rows = await asyncio.to_thread(result.fetchmany, chunk_size)
            if not rows:
                break
            recs = []
            for r in rows:
                ticket_no = tracking_map.get(r["trackingId"])
                if ticket_no is None:
                    continue
                d = dict(r)
                d["ticketNumber"] = ticket_no
                try:
                    recs.append(ActionHistorySchema(**d).model_dump(by_alias=False))
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Action-history validation error: {e}")
            matched += len(recs)
            inserted += await _insert_ignore(ActionHistoryModel, target_sess, recs, dialect_name)
            seen += len(rows)
            if seen % LOG_EVERY < chunk_size:
                logger.info(
                    f"action_history: read {seen:,}, matched {matched:,}, inserted {inserted:,}"
                )
    finally:
        await asyncio.to_thread(conn.close)

    logger.success(
        f"Action history done: read {seen:,}, matched {matched:,}, inserted {inserted:,}"
    )


async def run_migration(
    mysql_url: Optional[str] = None,
    target_db_url: Optional[str] = None,
    chunk_size: int = CHUNK_SIZE,
) -> None:
    """Run the full complaint + action-history migration from ``mysql_url`` into
    ``target_db_url`` (both default to config)."""
    mysql_url = mysql_url or settings.MYSQL_URL
    target_db_url = target_db_url or settings.OLTP_DB_URL
    if not mysql_url:
        raise ValueError(
            "No MySQL URL given. Set MYSQL_URL in the environment/.env or pass mysql_url."
        )

    # Log URLs with credentials masked (they land in shipped log files).
    safe_src = make_url(mysql_url).render_as_string(hide_password=True)
    safe_dst = make_url(target_db_url).render_as_string(hide_password=True)
    logger.info(f"Starting migration from {safe_src} -> {safe_dst}")
    mysql_engine, target_engine = setup_engines(mysql_url, target_db_url)
    dialect_name = target_engine.dialect.name  # 'sqlite' | 'postgresql' | …
    try:
        await init_db(target_engine)
        TargetSession = sessionmaker(
            bind=target_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with TargetSession() as target_sess:
            tracking_map = await migrate_complaints(
                mysql_engine, target_sess, dialect_name, chunk_size
            )
            await migrate_action_history(
                mysql_engine, target_sess, tracking_map, dialect_name, chunk_size
            )
        logger.success(f"Migration completed into {safe_dst}")
    finally:
        mysql_engine.dispose()
        await target_engine.dispose()


def main() -> None:
    """Console entry point: sync from the live MySQL server in ``MYSQL_URL``."""
    asyncio.run(run_migration())


if __name__ == "__main__":
    main()
