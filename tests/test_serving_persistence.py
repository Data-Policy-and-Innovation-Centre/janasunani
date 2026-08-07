import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")

from fastapi.testclient import TestClient  # noqa: E402

from janasunani.db import crud  # noqa: E402
from janasunani.db.models import Base, LiveGrievance  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.schemas import (  # noqa: E402
    ClassificationResult,
    ExtractionResult,
    GrievanceResult,
    RedactionResult,
    RoutingResult,
    TriageResult,
)
from janasunani.serving.store import DatabaseResultStore  # noqa: E402


def _make_oltp(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/live.db"
    sync = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(sync)
    sync.dispose()
    return url


def _sample_result(grievance_id: str = "aware-1") -> GrievanceResult:
    """A GrievanceResult with a tz-aware ``submitted_on``, as the processor
    actually produces (``datetime.now(UTC)``) and as
    ``model_dump(mode="json")`` serializes it (offset-bearing ISO string)."""
    return GrievanceResult(
        id=grievance_id,
        ticket_no=f"JS{grievance_id}",
        status="submitted",
        submitted_on=datetime(2026, 7, 8, 12, 30, tzinfo=timezone.utc),
        extraction=ExtractionResult(source="text", extracted_text="road is bad"),
        redaction=RedactionResult(redacted_text="road is bad", entities=[]),
        classification=ClassificationResult(category="Roads", language="en"),
        summary="road is bad",
        routing=RoutingResult(dept="PWD", office="PWD Cuttack", confidence=0.5, method="mock"),
        triage=TriageResult(),
    )


def test_submit_persists_and_fetches_from_oltp(tmp_path):
    url = _make_oltp(tmp_path)
    store = DatabaseResultStore(url)
    client = TestClient(create_app(result_store=store))

    submitted = client.post(
        "/grievance",
        data={"text": "The road is damaged. Call 9876543210.", "district": "Ganjam"},
    ).json()

    fresh_store = DatabaseResultStore(url)
    fresh_client = TestClient(create_app(result_store=fresh_store))
    fetched = fresh_client.get(f"/grievance/{submitted['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == submitted

    sync = create_engine(url.replace("+aiosqlite", ""))
    with Session(sync) as session:
        row = session.execute(select(LiveGrievance)).scalar_one()
        assert row.ticket_no == submitted["ticket_no"]
        assert row.district == "Ganjam"
        assert row.source == "text"
        assert row.routing_method == "mock"
        assert row.result_json["id"] == submitted["id"]
    sync.dispose()
    asyncio.run(store.dispose())
    asyncio.run(fresh_store.dispose())


def test_parse_datetime_normalizes_aware_values_to_naive_utc():
    """Regression (Codex on PR #19): ``submitted_on`` arrives as an
    offset-bearing ISO string (the processor stamps ``datetime.now(UTC)``),
    but ``LiveGrievance.submitted_on`` is a naive ``DateTime`` column.
    Postgres/asyncpg raises on a tz-aware bind into a naive column (SQLite
    silently accepts it), so ``_parse_datetime`` must strip tzinfo after
    converting to UTC."""
    aware_iso = "2026-07-08T18:00:00+05:30"
    parsed = crud._parse_datetime(aware_iso)
    assert parsed.tzinfo is None
    assert parsed == datetime(2026, 7, 8, 12, 30, 0)

    aware_dt = datetime(2026, 7, 8, 12, 30, tzinfo=timezone.utc)
    assert crud._parse_datetime(aware_dt) == datetime(2026, 7, 8, 12, 30)

    naive_dt = datetime(2026, 7, 8, 12, 30)
    assert crud._parse_datetime(naive_dt) == naive_dt
    assert crud._parse_datetime(naive_dt).tzinfo is None


def test_persist_tz_aware_submitted_on_stores_and_reads_back_naive(tmp_path):
    """Real-code-path regression for the same bug via the actual
    ``DatabaseResultStore`` -> ``crud.create_or_update_live_grievance`` path,
    using the exact JSON shape ``GrievanceResult.model_dump(mode="json")``
    produces (an offset-bearing ``submitted_on`` string)."""
    url = _make_oltp(tmp_path)
    store = DatabaseResultStore(url)
    result = _sample_result()

    asyncio.run(store.save(result, district="Cuttack"))

    sync = create_engine(url.replace("+aiosqlite", ""))
    with Session(sync) as session:
        row = session.execute(select(LiveGrievance)).scalar_one()
        assert row.submitted_on == datetime(2026, 7, 8, 12, 30)
        assert row.submitted_on.tzinfo is None
    sync.dispose()

    fetched = asyncio.run(store.get(result.id))
    assert fetched is not None
    assert fetched.submitted_on == result.submitted_on

    asyncio.run(store.dispose())


PG_URL = os.getenv(
    "TEST_OLTP_PG_URL", "postgresql+asyncpg://postgres:pass@127.0.0.1:5433/janasunani"
)


def _pg_available() -> bool:
    try:
        engine = create_engine(PG_URL.replace("+asyncpg", "+psycopg2"), connect_args={"connect_timeout": 2})
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _pg_available(), reason="no reachable Postgres test DB (set TEST_OLTP_PG_URL)"
)
def test_persist_tz_aware_submitted_on_against_postgres():
    """The bug this regression guards against only reproduces against a real
    Postgres/asyncpg backend (SQLite accepts tz-aware values into a naive
    column silently); see README for the throwaway container this targets."""
    sync_url = PG_URL.replace("+asyncpg", "+psycopg2")
    sync_engine = create_engine(sync_url)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    result = _sample_result(grievance_id="aware-pg-1")

    async def _save_and_fetch():
        # A single event loop for the whole store lifetime: asyncpg
        # connections can't be reused across `asyncio.run()` calls (each
        # mints a fresh loop), so save/get/dispose must share one here.
        store = DatabaseResultStore(PG_URL)
        try:
            await store.save(result, district="Cuttack")
            return await store.get(result.id)
        finally:
            await store.dispose()

    try:
        fetched = asyncio.run(_save_and_fetch())
        assert fetched is not None
        assert fetched.submitted_on == result.submitted_on

        with Session(create_engine(sync_url)) as session:
            row = session.execute(select(LiveGrievance)).scalar_one()
            assert row.submitted_on == datetime(2026, 7, 8, 12, 30)
            assert row.submitted_on.tzinfo is None
    finally:
        cleanup_engine = create_engine(sync_url)
        Base.metadata.drop_all(cleanup_engine)
        cleanup_engine.dispose()
