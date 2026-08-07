"""Contract tests for the Phase 10 API skeleton (TestClient, mocked processor).

These pin the API *shape* the frontend is built against: endpoint paths,
status codes, response field names, filter/pagination semantics, CORS. The
Phase 8/9 wire-up swaps the processor/history implementations behind
``create_app`` — every test here must still pass unchanged afterwards.
"""

import pytest

# fastapi alone isn't enough: it arrives transitively (mlflow) in envs without
# the serving extra, but the Form/File routes need python-multipart — and
# api.py builds the app at import time, so collection itself would crash.
pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")

from fastapi.testclient import TestClient  # noqa: E402

from janasunani.serving import api as api_module  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.history import LakeHistory, MockHistory  # noqa: E402

_TEXT = (
    "The hand pump in Balianta village has been broken for two months. "
    "Please contact me at 9876543210 or ramesh@example.com."
)


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_health(client):
    body = client.get("/health").json()
    assert body == {"status": "ok", "processor": "mock"}


def test_submit_text_returns_full_result_shape(client):
    resp = client.post("/grievance", data={"text": _TEXT, "district": "Cuttack"})
    assert resp.status_code == 201
    body = resp.json()

    assert set(body) == {
        "id",
        "ticket_no",
        "status",
        "submitted_on",
        "extraction",
        "redaction",
        "classification",
        "summary",
        "routing",
        "triage",
    }
    assert body["extraction"]["source"] == "text"
    assert body["extraction"]["extracted_text"] == _TEXT
    # the mock masks the phone and email; spans point into the extracted text
    assert "9876543210" not in body["redaction"]["redacted_text"]
    assert "<PHONE>" in body["redaction"]["redacted_text"]
    entities = body["redaction"]["entities"]
    assert {e["entity"] for e in entities} == {"PHONE", "EMAIL"}
    for e in entities:
        assert _TEXT[e["start"] : e["end"]] in ("9876543210", "ramesh@example.com")
    assert body["classification"]["category"]
    assert body["routing"]["method"] == "mock"
    assert "Cuttack" in body["routing"]["office"]
    assert 0.0 <= body["routing"]["confidence"] <= 1.0
    assert body["triage"]["duplicate_review"]["decision"] in {
        "matched",
        "no_match",
        "abstained",
        "not_indexed",
        "unavailable",
    }
    assert body["triage"]["spam"]["decision"] in {
        "flagged",
        "abstained",
        "not_scored",
    }


def test_submit_file_reports_document_source(client):
    resp = client.post(
        "/grievance",
        files={"file": ("complaint.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["extraction"]["source"] == "document"
    assert body["extraction"]["ocr_model"] is not None


def test_submitted_grievance_is_fetchable_by_id(client):
    submitted = client.post("/grievance", data={"text": _TEXT}).json()
    fetched = client.get(f"/grievance/{submitted['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == submitted


def test_missing_grievance_404s(client):
    assert client.get("/grievance/nope").status_code == 404


def test_text_and_file_are_mutually_exclusive(client):
    assert client.post("/grievance").status_code == 422  # neither
    both = client.post(
        "/grievance",
        data={"text": _TEXT},
        files={"file": ("c.pdf", b"x", "application/pdf")},
    )
    assert both.status_code == 422
    assert client.post("/grievance", data={"text": "   "}).status_code == 422


def test_history_pagination_and_shape(client):
    body = client.get("/history", params={"limit": 5, "offset": 5}).json()
    assert body["limit"] == 5 and body["offset"] == 5
    assert len(body["items"]) == 5
    assert body["total"] > 5
    # lake column names, unchanged
    assert set(body["items"][0]) == {
        "ticket_no",
        "created_on",
        "district",
        "category",
        "subcategory",
        "dept",
        "status",
        "office",
        "grievance",
    }


def test_history_filters_and_search(client):
    filtered = client.get("/history", params={"district": "Cuttack"}).json()
    assert filtered["total"] > 0
    assert all(item["district"] == "Cuttack" for item in filtered["items"])

    searched = client.get("/history", params={"q": filtered["items"][0]["ticket_no"]})
    assert searched.json()["total"] == 1

    assert client.get("/history", params={"limit": 101}).status_code == 422


def test_cors_header_present(client):
    resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "*"


async def test_concurrent_uploads_get_distinct_ticket_numbers():
    # Regression (Codex on PR #12): the ticket number used to be
    # len(results)+1, computed before `await file.read()` — overlapping
    # uploads could mint the same user-visible ticket_no.
    import asyncio

    import httpx

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/grievance",
                    files={"file": (f"c{i}.pdf", b"%PDF fake", "application/pdf")},
                )
                for i in range(8)
            )
        )
    tickets = [r.json()["ticket_no"] for r in responses]
    assert all(r.status_code == 201 for r in responses)
    assert len(set(tickets)) == len(tickets)


def test_same_text_gets_deterministic_classification(client):
    a = client.post("/grievance", data={"text": _TEXT}).json()
    b = client.post("/grievance", data={"text": _TEXT}).json()
    assert a["classification"] == b["classification"]
    assert a["ticket_no"] != b["ticket_no"]  # but each submission is distinct


def test_ticket_no_carries_full_grievance_id_suffix(client):
    # Regression (Codex P2 on PR #19): the ticket_no used to suffix only the
    # first 4 hex chars of grievance_id. ticket_seq is an in-process
    # itertools.count that repeats across process restarts/workers, so a
    # 4-hex suffix could collide against the DB's UNIQUE(ticket_no) index
    # once persistence is live. The suffix must be the *entire* id.
    body = client.post("/grievance", data={"text": _TEXT}).json()
    assert body["ticket_no"].endswith(body["id"].upper())
    assert len(body["ticket_no"]) == len("JS") + 7 + len(body["id"])


# --- Codex P1 on PR #25: the module-level app must default to MockHistory ---
# (never real citizen /history rows) unless an operator explicitly opts in.


def test_history_from_env_defaults_to_mock_history_when_flag_is_unset(monkeypatch):
    monkeypatch.delenv("JANASUNANI_REAL_HISTORY", raising=False)
    assert isinstance(api_module._history_from_env(), MockHistory)


@pytest.mark.parametrize("falsy_value", ["", "0", "false", "False", "no", "off"])
def test_history_from_env_treats_falsy_values_as_mock(monkeypatch, falsy_value):
    monkeypatch.setenv("JANASUNANI_REAL_HISTORY", falsy_value)
    assert isinstance(api_module._history_from_env(), MockHistory)


@pytest.mark.parametrize("truthy_value", ["1", "true", "True", "TRUE", "yes", "Yes"])
def test_history_from_env_selects_lake_history_when_flag_is_truthy(
    monkeypatch, truthy_value
):
    monkeypatch.setenv("JANASUNANI_REAL_HISTORY", truthy_value)
    # LakeHistory() itself never touches the lake on disk -- it only opens
    # the Parquet file lazily inside `.search()` -- so this stays a type
    # assertion and never reads real citizen data.
    assert isinstance(api_module._history_from_env(), LakeHistory)


def test_module_app_serves_mock_history_data_by_default(monkeypatch):
    """End-to-end: the same construction the module-level `app` uses, with
    the flag unset, must serve the deterministic MockHistory rows (never
    reach for the real lake)."""
    monkeypatch.delenv("JANASUNANI_REAL_HISTORY", raising=False)
    app = create_app(history=api_module._history_from_env())
    client = TestClient(app)

    body = client.get("/history", params={"limit": 1}).json()
    assert body["items"][0]["ticket_no"].startswith("CMO2024")
