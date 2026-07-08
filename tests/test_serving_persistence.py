import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")

from fastapi.testclient import TestClient  # noqa: E402

from janasunani.db.models import Base, LiveGrievance  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.store import DatabaseResultStore  # noqa: E402


def _make_oltp(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/live.db"
    sync = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(sync)
    sync.dispose()
    return url


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
