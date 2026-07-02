"""Prove the OLTP store is engine-swappable: the migration and CRUD run against
**Postgres** as well as SQLite (which the other tests already cover).

Skipped automatically when no Postgres test DB is reachable, so the suite stays
green without Docker. To run it, start one (see README) and/or set
``TEST_OLTP_PG_URL``; the default matches the throwaway container:
    docker run -d --name jana-pg -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=janasunani -p 5433:5432 postgres:16
"""

import os
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from janasunani.db import crud
from janasunani.db.models import Base
from janasunani.ingestion.schemas import Complaint as ComplaintSchema
from janasunani.migration.from_mysql import run_migration

PG_URL = os.getenv(
    "TEST_OLTP_PG_URL", "postgresql+asyncpg://postgres:pass@127.0.0.1:5433/janasunani"
)


def _sync_pg_url() -> str:
    return PG_URL.replace("+asyncpg", "+psycopg2")


def _pg_available() -> bool:
    try:
        engine = create_engine(_sync_pg_url(), connect_args={"connect_timeout": 2})
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(), reason="no reachable Postgres test DB (set TEST_OLTP_PG_URL)"
)

# Minimal messy "MySQL" source rows (source column names), as in test_migration.
COMPLAINTS = [
    ("T1", "TR1", "need caste cert", "Cuttack", "Yes", "2021-01-01 10:00:00", "Disposed", "Certificates", "Collector"),
    ("T2", "TR2", "water issue", "Puri", "No", "2021-02-01 09:00:00", "Disposed", "Water Supply", "Collector"),
    ("T3", None, "no tracking id", "Khordha", "No", "2021-03-01 08:00:00", "Pending", "Other", "Collector"),
]
HISTORY = [
    ("TR1", "Officer A", "2021-01-02 10:00:00", "Forwarded", "r1", "pending"),
    ("TR1", "Officer A", "2021-01-02 10:00:00", "Forwarded", "r1", "pending"),  # dup
    ("TR2", "Officer B", "2021-02-02 09:00:00", "Resolved", "done", "closed"),
    ("UNKNOWN", "Officer C", "2021-02-03 09:00:00", "Noted", "x", "open"),  # skipped
]


def _build_sqlite_source(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE t_janasunani_etl_pre_data ("
        "ticketNumber TEXT, trackingId TEXT, grievanceSubject TEXT, districtName TEXT, "
        "govtTicket TEXT, CreatedOn TEXT, StatusName TEXT, category TEXT, officeNAme TEXT)"
    )
    con.executemany("INSERT INTO t_janasunani_etl_pre_data VALUES (?,?,?,?,?,?,?,?,?)", COMPLAINTS)
    con.execute(
        "CREATE TABLE t_janasunani_etl_history_pre_data ("
        "trackingId TEXT, action_taken_by TEXT, action_taken_date TEXT, action_status TEXT, "
        "action_taken_remark TEXT, complaint_status_with_authority TEXT)"
    )
    con.executemany("INSERT INTO t_janasunani_etl_history_pre_data VALUES (?,?,?,?,?,?)", HISTORY)
    con.commit()
    con.close()


@pytest.fixture
def clean_pg():
    """Drop/recreate app tables on the Postgres test DB around each test."""
    engine = create_engine(_sync_pg_url())
    Base.metadata.drop_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()


async def test_migration_into_postgres(tmp_path, clean_pg):
    source = tmp_path / "source.db"
    _build_sqlite_source(source)
    await run_migration(f"sqlite:///{source}", PG_URL)

    engine = create_engine(_sync_pg_url())
    with engine.connect() as conn:
        nc = conn.execute(text("SELECT count(*) FROM complaints")).scalar_one()
        nh = conn.execute(text("SELECT count(*) FROM action_history")).scalar_one()
        orphans = conn.execute(
            text(
                "SELECT count(*) FROM action_history a "
                "WHERE NOT EXISTS (SELECT 1 FROM complaints c WHERE c.ticket_no = a.ticket_no)"
            )
        ).scalar_one()
    engine.dispose()
    assert (nc, nh, orphans) == (3, 2, 0)


async def test_crud_roundtrip_postgres(clean_pg):
    engine = create_async_engine(PG_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        await crud.create_or_update_complaint(
            session, ComplaintSchema(ticketNumber="T9", grievanceSubject="hello", govtTicket="Yes")
        )
        got = await crud.get_complaint_by_ticket(session, "T9")
        assert got is not None and got.ticket_no == "T9" and got.govt_ticket is True
    await engine.dispose()
