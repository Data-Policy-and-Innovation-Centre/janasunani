"""Tests for document ingestion: download (respx-mocked HTTP) → S3 (moto) or local
disk, with download status written back into the OLTP store."""

import boto3
import httpx
import pytest
import respx
from moto import mock_aws
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from janasunani.config import settings
from janasunani.db import crud
from janasunani.db.models import Base, Complaint
from janasunani.ingestion.document_ingestion import DocumentService

DOC_URL = "http://example.test/file~pdf"
BUCKET = "test-docs"


@pytest.fixture
async def session(tmp_path):
    """A fresh OLTP session with one complaint that has a document URL."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'oltp.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        s.add(Complaint(ticket_no="T1", document_url=DOC_URL, document_downloaded=False))
        await s.commit()
        yield s
    await engine.dispose()


@respx.mock
async def test_download_to_local_updates_status(session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path / "docs"))
    respx.get(DOC_URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 data"))

    svc = DocumentService(db=session)
    complaint = await crud.get_complaint_by_ticket(session, "T1")
    results = await svc.batch_download_documents([complaint])

    assert results["T1"] == "success"
    # file written locally as {ticket}_complaint_{ts}.pdf
    written = list((tmp_path / "docs").glob("T1_complaint_*.pdf"))
    assert len(written) == 1 and written[0].read_bytes() == b"%PDF-1.4 data"
    # OLTP status updated
    refreshed = await crud.get_complaint_by_ticket(session, "T1")
    assert refreshed.document_downloaded is True
    assert refreshed.local_document_path == str(written[0])
    assert refreshed.document_download_error is None


@respx.mock
async def test_download_to_s3_updates_status(session, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "main")  # non-local -> S3 path
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    respx.get(DOC_URL).mock(return_value=httpx.Response(200, content=b"%PDF s3"))

    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        svc = DocumentService(db=session, s3_bucket=BUCKET)
        complaint = await crud.get_complaint_by_ticket(session, "T1")
        results = await svc.batch_download_documents([complaint])

        assert results["T1"] == "success"
        # object uploaded under {ticket}_complaint_{ts}.pdf
        keys = [o["Key"] for o in svc.s3_service.list_objects(prefix="T1_complaint_")]
        assert len(keys) == 1 and keys[0].endswith(".pdf")

    refreshed = await crud.get_complaint_by_ticket(session, "T1")
    assert refreshed.document_downloaded is True
    assert refreshed.local_document_path == keys[0]


@respx.mock
async def test_invalid_url_is_skipped(session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path / "docs"))
    # complaint T1 has a URL; add one with an invalid URL
    session.add(Complaint(ticket_no="T2", document_url="N/A"))
    await session.commit()

    svc = DocumentService(db=session)
    t2 = await crud.get_complaint_by_ticket(session, "T2")
    results = await svc.batch_download_documents([t2])

    assert results["T2"] == "skipped"
    refreshed = await crud.get_complaint_by_ticket(session, "T2")
    assert refreshed.document_downloaded is False
