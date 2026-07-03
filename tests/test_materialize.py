"""Tests for the OLTP → Parquet materialization and the lake read helpers."""

import os

import pytest
from sqlalchemy import create_engine, insert

from janasunani.db.models import ActionHistory, Base, Complaint
from janasunani.olap import lake
from janasunani.olap.materialize import materialize


def _seed_sqlite_oltp(db_path) -> None:
    """Create the OLTP schema in a SQLite file and insert a few rows."""
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(Complaint),
            [
                {"ticket_no": "T1", "district": "Cuttack", "category": "Certificates", "govt_ticket": True},
                {"ticket_no": "T2", "district": "Puri", "category": "Water Supply", "govt_ticket": False},
            ],
        )
        conn.execute(
            insert(ActionHistory),
            [
                {"ticket_no": "T1", "action_taken_by": "Officer A", "action_status": "Forwarded"},
            ],
        )
    engine.dispose()


def test_materialize_sqlite_and_read_back(tmp_path):
    oltp = tmp_path / "oltp.db"
    _seed_sqlite_oltp(oltp)
    out = tmp_path / "interim"

    counts = materialize(oltp_url=f"sqlite+aiosqlite:///{oltp}", out_dir=out)
    assert counts == {"complaints": 2, "action_history": 1, "pages": 0, "documents": 0}
    assert (out / "complaints.parquet").exists()
    assert (out / "pages.parquet").exists()  # pipeline tables ride along, empty pre-export

    # Polars read-back
    df = lake.read("complaints", lake_dir=out)
    assert df.height == 2
    assert set(df["ticket_no"]) == {"T1", "T2"}

    # DuckDB SQL query over the lake views
    res = lake.query(
        "SELECT count(*) AS n FROM complaints WHERE govt_ticket", lake_dir=out
    )
    assert res["n"][0] == 1


PG_URL = os.getenv(
    "TEST_OLTP_PG_URL", "postgresql+asyncpg://postgres:pass@127.0.0.1:5433/janasunani"
)


def _pg_available() -> bool:
    try:
        engine = create_engine(
            PG_URL.replace("+asyncpg", "+psycopg2"), connect_args={"connect_timeout": 2}
        )
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_available(), reason="no reachable Postgres test DB")
def test_materialize_from_postgres(tmp_path):
    """Materialization is engine-agnostic: it also exports from a Postgres OLTP."""
    sync = PG_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(Complaint), [{"ticket_no": "P1"}, {"ticket_no": "P2"}])
    try:
        counts = materialize(oltp_url=PG_URL, out_dir=tmp_path)
        assert counts["complaints"] == 2
        assert set(lake.read("complaints", lake_dir=tmp_path)["ticket_no"]) == {"P1", "P2"}
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
