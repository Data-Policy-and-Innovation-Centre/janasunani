"""Exporter: pipeline artifact DB -> OLTP pages/documents (real code path).

SQLite OLTP always runs; the Postgres variant reuses the reachability skip
from test_oltp_swap (same throwaway container).
"""

import os
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from janasunani.db.models import Base
from janasunani.pipeline.db import initialize_database
from janasunani.pipeline.export import export_pipeline_db

# Same throwaway Postgres as test_oltp_swap (tests/ is not a package, so the
# reachability helpers are duplicated here rather than imported).
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


def _seed_pipeline_db(path, redacted: str | None = None):
    initialize_database(path)
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT OR REPLACE INTO pages (doc_id, page_number, full_path, page_id,"
            " language, extracted_text, redacted_text, ticket_number)"
            " VALUES ('D1', 1, '/x/D1.pdf', 'D1-p1', 'English', 'need water', ?, 'T1')",
            (redacted,),
        )
        con.execute(
            "INSERT OR REPLACE INTO documents (doc_id, ticket_number, grievance, summary)"
            " VALUES ('D1', 'T1', 'need water', 'water complaint')"
        )
        con.commit()


def _make_sqlite_oltp(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/oltp.db"
    sync = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(sync)
    sync.dispose()
    return url


def test_export_roundtrip_and_upsert_sqlite(tmp_path):
    pipeline_db = tmp_path / "pipeline.sqlite"
    _seed_pipeline_db(pipeline_db)
    oltp_url = _make_sqlite_oltp(tmp_path)

    counts = export_pipeline_db(pipeline_db, oltp_url=oltp_url)
    assert counts == {"pages": 1, "documents": 1}

    # idempotent re-run
    counts = export_pipeline_db(pipeline_db, oltp_url=oltp_url)
    assert counts == {"pages": 1, "documents": 1}

    # a later pipeline stage fills redacted_text -> re-export must UPDATE
    _seed_pipeline_db(pipeline_db, redacted="need [WATER]")
    export_pipeline_db(pipeline_db, oltp_url=oltp_url)

    sync = create_engine(oltp_url.replace("+aiosqlite", ""))
    with sync.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM pages")).scalar_one()
        red = conn.execute(
            text("SELECT redacted_text FROM pages WHERE page_id = 'D1-p1'")
        ).scalar_one()
        doc = conn.execute(
            text("SELECT summary FROM documents WHERE doc_id = 'D1'")
        ).scalar_one()
    sync.dispose()
    assert (n, red, doc) == (1, "need [WATER]", "water complaint")


@pytest.mark.skipif(not _pg_available(), reason="no reachable Postgres test DB")
def test_export_into_postgres(tmp_path):
    pipeline_db = tmp_path / "pipeline.sqlite"
    _seed_pipeline_db(pipeline_db, redacted="need [WATER]")

    sync = create_engine(_sync_pg_url())
    Base.metadata.drop_all(sync)
    Base.metadata.create_all(sync)

    counts = export_pipeline_db(pipeline_db, oltp_url=PG_URL)
    counts2 = export_pipeline_db(pipeline_db, oltp_url=PG_URL)  # idempotent
    assert counts == counts2 == {"pages": 1, "documents": 1}

    with sync.connect() as conn:
        row = conn.execute(
            text("SELECT ticket_number, redacted_text FROM pages")
        ).one()
    Base.metadata.drop_all(sync)
    sync.dispose()
    assert tuple(row) == ("T1", "need [WATER]")
