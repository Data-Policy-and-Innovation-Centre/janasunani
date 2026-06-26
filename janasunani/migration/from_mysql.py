"""Migrate the grievance complaint store from a MySQL source into the local DB.

This is the **single validated insert routine** both migration paths share:

- the live-MySQL sync calls :func:`run_migration` with a running server's URL;
- the cold-start dump loader (:mod:`janasunani.migration.from_sql_dump`) restores the
  ``mysqldump`` into a throwaway MySQL and calls :func:`run_migration` against it.

Source rows are read from the two ETL tables (``t_janasunani_etl_pre_data`` and
``t_janasunani_etl_history_pre_data``), validated/renamed through the Pydantic
schemas (the one source→ORM column map), and bulk-inserted into the ORM with
chunking and on-conflict-do-nothing dedup.
"""

import asyncio
from typing import Dict, Optional

from loguru import logger
from more_itertools import chunked
from pydantic import ValidationError
from sqlalchemy import (
    Engine,
    MetaData,
    Table,
    create_engine,
    distinct,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from janasunani.config import settings
from janasunani.db.models import ActionHistory as ActionHistoryModel
from janasunani.db.models import Base
from janasunani.db.models import Complaint as ComplaintModel
from janasunani.ingestion.schemas import ActionHistory as ActionHistorySchema
from janasunani.ingestion.schemas import Complaint as ComplaintSchema

COMPLAINT_TABLE = "t_janasunani_etl_pre_data"
ACTION_HISTORY_TABLE = "t_janasunani_etl_history_pre_data"
CHUNK_SIZE = 1000


def setup_engines(mysql_url: str, target_db_url: str) -> tuple[Engine, AsyncEngine]:
    """Build a sync MySQL source engine and an async target engine."""
    mysql_engine = create_engine(mysql_url, pool_pre_ping=True, pool_recycle=3600)
    sqlite_engine = create_async_engine(target_db_url)
    return mysql_engine, sqlite_engine


async def init_db(target_engine: AsyncEngine) -> None:
    """Create the target tables if they do not already exist."""
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_existing_ticket_no(db: AsyncSession) -> list[str]:
    result = await db.execute(select(ComplaintModel.ticket_no).distinct())
    return result.scalars().all()


async def migrate_complaints(
    mysql_sess: Session, target_sess: AsyncSession, chunk_size: int = CHUNK_SIZE
) -> Dict[str, str]:
    """Copy not-yet-migrated complaints from MySQL and return a
    ``{tracking_id: ticket_no}`` map for the action-history pass."""
    meta = MetaData()
    complaint_t = Table(COMPLAINT_TABLE, meta, autoload_with=mysql_sess.bind)

    def fetch_mysql_tickets() -> set:
        query = select(distinct(complaint_t.c.ticketNumber))
        return set(mysql_sess.execute(query).scalars().all())

    async def build_tracking_map() -> Dict[str, str]:
        res = await target_sess.execute(
            select(ComplaintModel.tracking_id, ComplaintModel.ticket_no)
        )
        tracking_map = {tid: tno for tid, tno in res.all() if tid is not None}
        logger.info(f"Tracking map covers {len(tracking_map)} complaints")
        return tracking_map

    ticket_nos = await asyncio.to_thread(fetch_mysql_tickets)
    complaints_in_db = set(await get_existing_ticket_no(target_sess))
    pending_tickets = ticket_nos.difference(complaints_in_db)

    total = len(pending_tickets)
    if total == 0:
        logger.success("All complaints already migrated")
        return await build_tracking_map()

    logger.info(f"Starting complaints migration ({total} rows)")

    for batch_no, chunk in enumerate(chunked(pending_tickets, chunk_size)):

        def fetch_chunk(chunk=chunk):
            stmt = select(complaint_t).where(complaint_t.c.ticketNumber.in_(chunk))
            return mysql_sess.execute(stmt).mappings().all()

        results: list[dict] = await asyncio.to_thread(fetch_chunk)
        validated = [ComplaintSchema(**r).model_dump(by_alias=False) for r in results]
        to_insert = [ComplaintModel(**c) for c in validated]

        try:
            target_sess.add_all(to_insert)
            await target_sess.commit()
            logger.info(f"Complaint batch {batch_no}: inserted {len(to_insert)} rows")
        except IntegrityError:
            await target_sess.rollback()
            for c in validated:
                try:
                    target_sess.add(ComplaintModel(**c))
                    await target_sess.commit()
                except IntegrityError:
                    await target_sess.rollback()
                    logger.warning(f"Skipping duplicated {c['ticket_no']}")

    return await build_tracking_map()


async def migrate_action_history(
    mysql_sess: Session,
    target_sess: AsyncSession,
    tracking_map: Dict[str, str],
    chunk_size: int = CHUNK_SIZE,
) -> None:
    """Copy action-history rows for the migrated complaints, resolving each
    source ``trackingId`` to its complaint's ``ticket_no`` via ``tracking_map``."""
    if not tracking_map:
        logger.info("No action_history to migrate (empty tracking_map).")
        return

    meta = MetaData()
    history_t = Table(ACTION_HISTORY_TABLE, meta, autoload_with=mysql_sess.bind)

    total = mysql_sess.execute(
        select(func.count())
        .select_from(history_t)
        .where(history_t.c.trackingId.in_(tracking_map.keys()))
    ).scalar_one()

    logger.info(f"Starting action_history migration ({total} rows)")

    offset = 0
    batch_no = 0
    inserted = 0
    while offset < total:
        rows = (
            mysql_sess.execute(
                select(history_t)
                .where(history_t.c.trackingId.in_(tracking_map.keys()))
                .limit(chunk_size)
                .offset(offset)
            )
            .mappings()
            .all()
        )
        if not rows:
            break

        logger.info(f"History batch {batch_no} (rows {offset}–{offset + len(rows)})")
        to_insert = []
        for r in rows:
            r = dict(r)
            r["ticketNumber"] = tracking_map.get(r["trackingId"])
            try:
                rec = ActionHistorySchema(**r).model_dump(by_alias=False)
            except ValidationError as e:
                logger.error(f"Validation error for {r.get('trackingId')}: {e}")
                continue
            to_insert.append(rec)

        if to_insert:
            inserted += await _insert_ignore(target_sess, to_insert)

        offset += chunk_size
        batch_no += 1

    logger.info(f"Inserted {inserted}/{total} action_history records")


async def _insert_ignore(target_sess: AsyncSession, records: list[dict]) -> int:
    """Bulk insert with on-conflict-do-nothing (any unique constraint), falling
    back to per-row inserts if the batch hits an integrity/operational error."""
    stmt = sqlite_insert(ActionHistoryModel).values(records).on_conflict_do_nothing()
    try:
        await target_sess.execute(stmt)
        await target_sess.commit()
        return len(records)
    except (IntegrityError, OperationalError):
        await target_sess.rollback()

    inserted = 0
    for rec in records:
        try:
            one = sqlite_insert(ActionHistoryModel).values(rec).on_conflict_do_nothing()
            await target_sess.execute(one)
            await target_sess.commit()
            inserted += 1
        except (IntegrityError, OperationalError):
            await target_sess.rollback()
            logger.warning(
                f"Skipping bad history record for trackingId {rec.get('tracking_id')}"
            )
    return inserted


async def run_migration(
    mysql_url: Optional[str] = None,
    target_db_url: Optional[str] = None,
    chunk_size: int = CHUNK_SIZE,
) -> None:
    """Run the full complaint + action-history migration from ``mysql_url`` into
    ``target_db_url`` (both default to config)."""
    mysql_url = mysql_url or settings.MYSQL_URL
    target_db_url = target_db_url or settings.DB_URL
    if not mysql_url:
        raise ValueError(
            "No MySQL URL given. Set MYSQL_URL in the environment/.env or pass mysql_url."
        )

    logger.info(f"Starting migration from {mysql_url} -> {target_db_url}")
    mysql_engine, target_engine = setup_engines(mysql_url, target_db_url)
    try:
        await init_db(target_engine)
        MySQLSession = sessionmaker(bind=mysql_engine, expire_on_commit=False)
        TargetSession = sessionmaker(
            bind=target_engine, class_=AsyncSession, expire_on_commit=False
        )
        with MySQLSession() as mysql_sess:
            async with TargetSession() as target_sess:
                tracking_map = await migrate_complaints(
                    mysql_sess, target_sess, chunk_size
                )
                await migrate_action_history(
                    mysql_sess, target_sess, tracking_map, chunk_size
                )
        logger.success(f"Migration completed into {target_db_url}")
    finally:
        mysql_engine.dispose()
        await target_engine.dispose()


def main() -> None:
    """Console entry point: sync from the live MySQL server in ``MYSQL_URL``."""
    asyncio.run(run_migration())


if __name__ == "__main__":
    main()
