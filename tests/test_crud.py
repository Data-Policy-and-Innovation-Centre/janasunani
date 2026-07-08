"""Tests for the async CRUD layer against a real (SQLite) session — the
create/update/bulk-load/tracking helpers in janasunani.db.crud."""

from datetime import datetime, timedelta

import pytest
import pytz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from janasunani.db import crud
from janasunani.db.models import ActionHistoryAPIRequestTracking, APIRequestTracking, Base
from janasunani.ingestion.schemas import ActionHistory as ActionHistorySchema
from janasunani.ingestion.schemas import Complaint as ComplaintSchema
from janasunani.ingestion.schemas import District as DistrictSchema

IST = pytz.timezone("Asia/Kolkata")


def _district(dist_id, dist_name="Cuttack"):
    return DistrictSchema(distId=dist_id, distName=dist_name)


def _complaint(ticket_no, **kw):
    return ComplaintSchema(ticketNumber=ticket_no, **kw)


def _action(ticket_no, **kw):
    return ActionHistorySchema(ticketNumber=ticket_no, **kw)


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'crud.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


# --- districts ---


async def test_create_or_update_district_creates_then_updates(db):
    created = await crud.create_or_update_district(db, _district(1, "Cuttack"))
    assert created.dist_name == "Cuttack"

    updated = await crud.create_or_update_district(db, _district(1, "Cuttack Renamed"))
    assert updated.id == created.id
    assert updated.dist_name == "Cuttack Renamed"

    assert (await crud.get_district_by_id(db, 1)).dist_name == "Cuttack Renamed"
    assert (await crud.get_district_by_name(db, "Cuttack Renamed")) is not None
    assert await crud.get_district_by_id(db, 999) is None


async def test_get_all_districts(db):
    await crud.create_or_update_district(db, _district(1, "Cuttack"))
    await crud.create_or_update_district(db, _district(2, "Puri"))
    assert {d.dist_id for d in await crud.get_all_districts(db)} == {1, 2}


async def test_batch_create_or_update_districts(db):
    result = await crud.batch_create_or_update_districts(
        db, [_district(1, "Cuttack"), _district(2, "Puri")]
    )
    assert {d.dist_id for d in result} == {1, 2}


# --- complaints ---


async def test_create_or_update_complaint_creates_then_updates(db):
    created = await crud.create_or_update_complaint(
        db, _complaint("T1", grievanceSubject="water", districtName="Puri")
    )
    assert created.grievance == "water"

    updated = await crud.create_or_update_complaint(
        db, _complaint("T1", grievanceSubject="water fixed", districtName="Puri")
    )
    assert updated.id == created.id
    assert updated.grievance == "water fixed"

    fetched = await crud.get_complaint_by_ticket(db, "T1")
    assert fetched is not None and fetched.grievance == "water fixed"
    assert await crud.get_complaint_by_ticket(db, "missing") is None


async def test_get_all_complaints(db):
    await crud.create_or_update_complaint(db, _complaint("T1"))
    await crud.create_or_update_complaint(db, _complaint("T2"))
    assert {c.ticket_no for c in await crud.get_all_complaints(db)} == {"T1", "T2"}


async def test_get_complaints_by_district_and_status(db):
    await crud.create_or_update_complaint(
        db, _complaint("T1", districtName="Puri", StatusName="Pending")
    )
    await crud.create_or_update_complaint(
        db, _complaint("T2", districtName="Cuttack", StatusName="Disposed")
    )
    by_dist = await crud.get_complaints_by_district(db, "Puri")
    assert [c.ticket_no for c in by_dist] == ["T1"]

    by_status = await crud.get_complaints_by_status(db, "Disposed")
    assert [c.ticket_no for c in by_status] == ["T2"]


async def test_batch_create_or_update_complaints(db):
    result = await crud.batch_create_or_update_complaints(
        db, [_complaint("T1"), _complaint("T2")]
    )
    assert {c.ticket_no for c in result} == {"T1", "T2"}


# --- action history ---


async def test_create_action_history_and_get_by_ticket(db):
    await crud.create_or_update_complaint(db, _complaint("T1"))
    await crud.create_action_history(
        db, _action("T1", action_taken_by="Officer A", action_status="Forwarded")
    )
    await crud.create_action_history(
        db, _action("T1", action_taken_by="Officer B", action_status="Resolved")
    )
    history = await crud.get_action_history_by_ticket(db, "T1")
    assert {h.action_taken_by for h in history} == {"Officer A", "Officer B"}
    assert await crud.get_action_history_by_ticket(db, "missing") == []


async def test_batch_create_action_history(db):
    await crud.create_or_update_complaint(db, _complaint("T1"))
    result = await crud.batch_create_action_history(
        db,
        [
            _action("T1", action_taken_by="Officer A"),
            _action("T1", action_taken_by="Officer B"),
        ],
    )
    assert len(result) == 2


# --- bulk loaders ---


async def test_bulk_load_districts_skips_existing(db):
    await crud.create_or_update_district(db, _district(1, "Cuttack"))
    inserted = await crud.bulk_load_districts(
        db, [_district(1, "Cuttack"), _district(2, "Puri")]
    )
    assert [d.dist_id for d in inserted] == [2]
    assert {d.dist_id for d in await crud.get_all_districts(db)} == {1, 2}


async def test_bulk_load_districts_noop_when_all_exist(db):
    await crud.create_or_update_district(db, _district(1, "Cuttack"))
    inserted = await crud.bulk_load_districts(db, [_district(1, "Cuttack")])
    assert inserted == []


async def test_bulk_load_complaints_inserts_and_updates(db):
    await crud.create_or_update_complaint(db, _complaint("T1", grievanceSubject="old"))

    result = await crud.bulk_load_complaints(
        db,
        [
            _complaint("T1", grievanceSubject="new"),  # update
            _complaint("T2", grievanceSubject="brand new"),  # insert
        ],
    )
    assert len(result) == 2

    t1 = await crud.get_complaint_by_ticket(db, "T1")
    t2 = await crud.get_complaint_by_ticket(db, "T2")
    assert t1.grievance == "new"
    assert t2.grievance == "brand new"


async def test_bulk_load_complaints_noop_when_unchanged(db):
    await crud.create_or_update_complaint(db, _complaint("T1", grievanceSubject="same"))
    result = await crud.bulk_load_complaints(db, [_complaint("T1", grievanceSubject="same")])
    assert result == []


async def test_bulk_load_action_histories_inserts_and_updates(db):
    await crud.create_or_update_complaint(db, _complaint("T1"))
    await crud.create_action_history(
        db,
        _action(
            "T1",
            action_taken_by="Officer A",
            action_taken_date=datetime(2024, 1, 1),
            action_status="Forwarded",
        ),
    )

    result = await crud.bulk_load_action_histories(
        db,
        [
            _action(
                "T1",
                action_taken_by="Officer A",
                action_taken_date=datetime(2024, 1, 1),
                action_status="Resolved",  # changed -> update
            ),
            _action(
                "T1",
                action_taken_by="Officer B",
                action_taken_date=datetime(2024, 1, 2),
                action_status="Forwarded",  # new key -> insert
            ),
        ],
    )
    assert len(result) == 2
    history = await crud.get_action_history_by_ticket(db, "T1")
    statuses = {h.action_taken_by: h.action_status for h in history}
    assert statuses == {"Officer A": "Resolved", "Officer B": "Forwarded"}


# --- document status / lookups ---


async def test_update_document_status_is_deprecated_and_updates_fields(db):
    await crud.create_or_update_complaint(db, _complaint("T1"))
    with pytest.deprecated_call():
        updated = await crud.update_document_status(
            db, ticket_no="T1", local_path="/tmp/t1.pdf", success=True
        )
    assert updated.document_downloaded is True
    assert updated.local_document_path == "/tmp/t1.pdf"
    assert updated.document_download_error is None


async def test_update_document_status_missing_ticket_returns_none(db):
    with pytest.deprecated_call():
        result = await crud.update_document_status(
            db, ticket_no="missing", local_path="/tmp/x.pdf", success=False, error="404"
        )
    assert result is None


async def test_get_complaints_without_documents_filters_by_error_flag(db):
    await crud.create_or_update_complaint(
        db, _complaint("T1", Document="http://x/1.pdf")
    )
    await crud.create_or_update_complaint(
        db, _complaint("T2", Document="http://x/2.pdf")
    )
    t2 = await crud.get_complaint_by_ticket(db, "T2")
    t2.document_download_error = "boom"
    await db.commit()

    no_errors = await crud.get_complaints_without_documents(db)
    assert [c.ticket_no for c in no_errors] == ["T1"]

    with_errors = await crud.get_complaints_without_documents(
        db, get_docs_where_errors_occurred=True
    )
    assert [c.ticket_no for c in with_errors] == ["T2"]


async def test_get_complaints_with_document_urls(db):
    # `isnot("")` is a null-safe SQL comparison: NULL passes through (NULL IS
    # NOT '' is TRUE), only an explicit empty string is excluded.
    await crud.create_or_update_complaint(db, _complaint("T1", Document="http://x/1.pdf"))
    await crud.create_or_update_complaint(db, _complaint("T2"))  # document_url NULL
    t3 = await crud.create_or_update_complaint(db, _complaint("T3", Document="http://x/3.pdf"))
    t3.document_url = ""
    await db.commit()

    urls = await crud.get_complaints_with_document_urls(db)
    assert {c.ticket_no for c in urls} == {"T1", "T2"}


# --- API request tracking ---


async def test_record_and_get_complaint_api_request_success_create_then_update(db):
    first = await crud.record_complaint_api_request_success(
        db, year=2024, dist_id=1, status=1, office=1, record_count=5
    )
    assert first.records_count == 5 and first.failure_count == 0

    second = await crud.record_complaint_api_request_success(
        db, year=2024, dist_id=1, status=1, office=1, record_count=9
    )
    assert second.id == first.id
    assert second.records_count == 9


async def test_mark_complaints_api_request_failed_create_then_increment(db):
    first = await crud.mark_complaints_api_request_failed(
        db, year=2024, dist_id=1, status=1, office=1
    )
    assert first.failure_count == 1

    second = await crud.mark_complaints_api_request_failed(
        db, year=2024, dist_id=1, status=1, office=1
    )
    assert second.id == first.id
    assert second.failure_count == 2


async def test_filter_complaints_api_request(db):
    # No tracking row at all -> not recently processed.
    assert (
        await crud.filter_complaints_api_request(db, year=2024, dist_id=1, status=1, office=1)
        is False
    )

    await crud.record_complaint_api_request_success(
        db, year=2024, dist_id=1, status=1, office=1, record_count=1
    )
    # Just succeeded -> within threshold -> True.
    assert (
        await crud.filter_complaints_api_request(db, year=2024, dist_id=1, status=1, office=1)
        is True
    )

    # Old success but few failures -> False.
    await crud.record_complaint_api_request_success(
        db, year=2024, dist_id=2, status=1, office=1, record_count=1
    )
    tracking = (
        await db.execute(select(APIRequestTracking).filter(APIRequestTracking.dist_id == 2))
    ).scalars().first()
    tracking.last_successful_fetch = datetime.now(IST) - timedelta(days=30)
    await db.commit()
    assert (
        await crud.filter_complaints_api_request(db, year=2024, dist_id=2, status=1, office=1)
        is False
    )

    # Old success but failure_count over threshold -> True.
    tracking.failure_count = 5
    await db.commit()
    assert (
        await crud.filter_complaints_api_request(db, year=2024, dist_id=2, status=1, office=1)
        is True
    )


async def test_record_and_mark_action_history_api_request(db):
    await crud.create_or_update_complaint(db, _complaint("T1"))

    created = await crud.record_action_history_api_request_success(db, "T1", record_count=3)
    assert created.records_count == 3 and created.failure_count == 0

    updated = await crud.record_action_history_api_request_success(db, "T1", record_count=7)
    assert updated.id == created.id and updated.records_count == 7

    failed_first = await crud.mark_action_history_api_request_failed(db, "T2")
    assert failed_first.failure_count == 1
    failed_second = await crud.mark_action_history_api_request_failed(db, "T2")
    assert failed_second.id == failed_first.id and failed_second.failure_count == 2


async def test_get_tickets_needing_action_history(db):
    await crud.create_or_update_complaint(db, _complaint("NEVER_TRACKED"))
    await crud.create_or_update_complaint(db, _complaint("STALE"))
    await crud.create_or_update_complaint(db, _complaint("RECENT"))
    await crud.create_or_update_complaint(db, _complaint("EXHAUSTED"))

    # STALE: successful fetch far in the past -> needs refresh.
    await crud.record_action_history_api_request_success(db, "STALE", record_count=1)
    stale_tracking = (
        await db.execute(
            select(ActionHistoryAPIRequestTracking).filter_by(ticket_no="STALE")
        )
    ).scalars().first()
    stale_tracking.last_successful_fetch = datetime.now(IST) - timedelta(days=30)
    await db.commit()

    # RECENT: successful fetch just now -> does not need refresh.
    await crud.record_action_history_api_request_success(db, "RECENT", record_count=1)

    # EXHAUSTED: never succeeded, failure count past threshold -> excluded.
    for _ in range(4):
        await crud.mark_action_history_api_request_failed(db, "EXHAUSTED")

    needing = set(await crud.get_tickets_needing_action_history(db))
    assert needing == {"NEVER_TRACKED", "STALE"}
