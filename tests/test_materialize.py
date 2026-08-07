"""Tests for the OLTP → Parquet materialization and the lake read helpers."""

import os
from datetime import datetime, timezone

import polars as pl
import pytest
from sqlalchemy import create_engine, insert

from janasunani.db.models import ActionHistory, Base, Complaint, PipelinePage
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
    assert counts == {
        "complaints": 2,
        "action_history": 1,
        "pages": 0,
        "documents": 0,
        "grievance_redactions": 0,
    }
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


def test_raw_ocr_text_never_reaches_the_lake(tmp_path):
    """``pages.extracted_text`` is un-redacted OCR and must not be materialized.

    ``pii_tagger`` writes ``redacted_text`` and leaves ``extracted_text`` as the
    raw page text. Parquet is not access-controlled per-column, so the raw
    column stays behind in OLTP while everything else rides along.
    """
    oltp = tmp_path / "oltp.db"
    engine = create_engine(f"sqlite:///{oltp}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(PipelinePage),
            [
                {
                    "page_id": "D1-p1",
                    "doc_id": "D1",
                    "page_number": 1,
                    "full_path": "/x/D1.pdf",
                    "ticket_number": "T1",
                    "language": "English",
                    "extracted_text": "Ramesh Kumar, 9876543210, needs water",
                    "redacted_text": "<NAME>, <PHONE>, needs water",
                }
            ],
        )
    engine.dispose()

    out = tmp_path / "interim"
    counts = materialize(oltp_url=f"sqlite+aiosqlite:///{oltp}", out_dir=out)
    assert counts["pages"] == 1

    df = lake.read("pages", lake_dir=out)
    assert "extracted_text" not in df.columns
    # everything else still rides along, redaction included
    assert "redacted_text" in df.columns
    assert df["redacted_text"][0] == "<NAME>, <PHONE>, needs water"
    assert df["ticket_number"][0] == "T1"

    # and the raw text is nowhere in the file, under any column
    raw = (out / "pages.parquet").read_bytes()
    assert b"Ramesh Kumar" not in raw
    assert b"9876543210" not in raw


def test_lake_freshness_reports_naive_utc_mtimes(tmp_path):
    """`/history` and the metrics layer both read the lake, and a live
    grievance is invisible there until the next materialization run (#36) --
    this is how a caller finds out the snapshot is stale. Mtimes must come
    back naive: a tz-aware value passes SQLite in tests and fails asyncpg
    against the deployed Postgres (this has bitten twice already)."""
    pl.DataFrame({"ticket_no": ["T1"]}).write_parquet(tmp_path / "complaints.parquet")
    pl.DataFrame({"ticket_no": ["T1"]}).write_parquet(tmp_path / "action_history.parquet")

    stat_before = (tmp_path / "complaints.parquet").stat().st_mtime
    freshness = lake.lake_freshness(tmp_path)

    assert set(freshness) == {"complaints", "action_history"}
    expected = datetime.fromtimestamp(stat_before, tz=timezone.utc).replace(tzinfo=None)
    assert freshness["complaints"] == expected
    assert freshness["complaints"].tzinfo is None


def test_lake_freshness_distinguishes_stale_tables_by_mtime(tmp_path):
    pl.DataFrame({"ticket_no": ["T1"]}).write_parquet(tmp_path / "complaints.parquet")
    pl.DataFrame({"ticket_no": ["T1"]}).write_parquet(tmp_path / "action_history.parquet")

    older = (tmp_path / "complaints.parquet").stat().st_mtime - 3600
    os.utime(tmp_path / "complaints.parquet", (older, older))

    freshness = lake.lake_freshness(tmp_path)
    assert freshness["complaints"] < freshness["action_history"]


def test_lake_freshness_on_an_empty_lake_is_empty(tmp_path):
    assert lake.lake_freshness(tmp_path) == {}


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
