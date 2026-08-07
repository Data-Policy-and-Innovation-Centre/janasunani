"""Synthetic end-to-end canary: artifact DB -> OLTP -> lake -> API.

The Phase 13 end-to-end run has two variants. The real-data one
(``scripts/e2e_pipeline.sh``) needs S3, the model mirrors and the three
mutually-exclusive extras, so it cannot run here. This is the other one: the
same hops, over non-PII synthetic fixtures, with every model replaced by a
canned stand-in and *everything else* on the real code path.

It doubles as the readiness canary. Each hop below is something that has
silently degraded at least once, and degrades quietly rather than loudly:

* the exporter writes the artifact DB into OLTP;
* materialization carries OLTP into Parquet, and holds back the raw OCR column;
* the lake is queryable as history;
* the API's result store is the DB one, not the in-memory default that loses
  submissions on restart;
* routing resolves off the real master tables rather than reporting
  ``method="fallback"`` because the CSVs were never materialized.

**Where the wiring is asserted.** The store and history tests below go through
``inference.serve.create_live_app`` — the function the deployed API actually
calls — with only ``build_processor`` substituted, because that is the one
piece that loads models. Injecting ``LakeHistory``/``DatabaseResultStore``
directly would prove the components work while saying nothing about whether
production selects them, which is the failure worth catching.

Routing is the exception, and deliberately so: the router is wired inside
``build_processor`` (``router=DEFAULT_ROUTER``), which cannot run here. The
test below exercises ``MappingRouter`` directly and therefore covers the
loader, not the wire-up. Confirming the deployed processor routes off the
master tables is the real-data E2E's job.

Everything here is invented. No fixture in this file may carry real citizen
text: that is what makes it runnable in CI.

⚠️ Every test that reaches ``Settings`` sets ``OLTP_DB_URL`` explicitly. An
ambient value from the project ``.env`` could point at the production Postgres,
and these tests write. Never let one resolve OLTP by default.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from janasunani.config import DEFAULT_OLTP_DB_URL, directories
from janasunani.db.models import Base, Complaint
from janasunani.inference import serve
from janasunani.olap import lake
from janasunani.olap.materialize import materialize
from janasunani.pipeline.db import initialize_database
from janasunani.pipeline.export import export_pipeline_db
from janasunani.routing.rules import MappingRouter
from janasunani.serving.api import create_app
from janasunani.serving.history import LakeHistory
from janasunani.serving.schemas import (
    ClassificationResult,
    ExtractionResult,
    GrievanceResult,
    RedactionResult,
    TriageResult,
)
from janasunani.serving.store import DatabaseResultStore

# --- synthetic fixtures ------------------------------------------------------
#
# Invented tickets, invented text. The "raw" page text carries a marker string
# we can grep the whole lake for; the redacted text is what the pipeline would
# have produced.

_RAW_MARKER = "SYNTHETIC-RAW-ONLY-MUST-NOT-LEAVE-OLTP"

_PAGES = (
    # (page_id, doc_id, page_number, ticket_no, extracted_text, redacted_text)
    (
        "SYN1-p1",
        "SYN1",
        1,
        "SYN0000001",
        f"{_RAW_MARKER} Street light out on the main road since last month.",
        "<NAME> Street light out on the main road since last month.",
    ),
    (
        "SYN1-p2",
        "SYN1",
        2,
        "SYN0000001",
        f"{_RAW_MARKER} Requesting repair before the monsoon.",
        "<NAME> Requesting repair before the monsoon.",
    ),
    (
        "SYN2-p1",
        "SYN2",
        1,
        "SYN0000002",
        f"{_RAW_MARKER} The village hand pump has been broken for two months.",
        "<NAME> The village hand pump has been broken for two months.",
    ),
)

_DOCUMENTS = (
    ("SYN1", "SYN0000001", "Street light out", "Street lighting repair request", "Electricity"),
    ("SYN2", "SYN0000002", "Hand pump broken", "Hand pump repair request", "Drinking Water Supply"),
)

_COMPLAINTS = (
    {
        "ticket_no": "SYN0000001",
        "district": "Khordha",
        "category": "Electricity",
        "grievance": "Street light out on the main road since last month.",
    },
    {
        "ticket_no": "SYN0000002",
        "district": "Cuttack",
        "category": "Drinking Water Supply",
        "grievance": "The village hand pump has been broken for two months.",
    },
)

# Minimal master tables in the real CSV shape, so MappingRouter loads and
# resolves for real. "Energy" is the literal category==department name match
# the loader documents as the only non-fabricated bridge.
_MAPPING_CSVS = {
    "m_admin_category.csv": (
        "intCategoryId,vchCategory,vchCategoryOD\n"
        "1,Energy,ଶକ୍ତି\n"
        "2,Drinking Water Supply,ପାନୀୟ ଜଳ\n"
    ),
    "m_admin_hierarchy_value.csv": (
        "intAdminHierarchyValueId,vchAdminHierarchyValue,vchAdminHierarchyValueO\n"
        "5,Energy,ଶକ୍ତି\n"
    ),
    "m_admin_offices.csv": "intOfficeId,vchOfficeName\n5,Departments\n6,Collector\n",
    # vchDesignationSequence is itself comma-separated, hence the quoting.
    # bitDeletedFlag=0 (active) and a non-zero intDepartmentId are both load
    # conditions, not decoration.
    "t_admin_escalation.csv": (
        "intEscalationId,intDepartmentId,intOfficeId,intEscalation,"
        "vchDesignationSequence,bitDeletedFlag\n"
        '1,5,6,2,"17,18",0\n'
    ),
    "m_role.csv": "intRoleId,vchRoleName\n17,Executive Engineer\n18,CEO TPCODL\n",
}


def _write_mapping_dir(tmp_path: Path) -> Path:
    mapping_dir = tmp_path / "janasunani-mappings"
    mapping_dir.mkdir(exist_ok=True)  # a restart test builds the app twice
    for name, body in _MAPPING_CSVS.items():
        (mapping_dir / name).write_text(body, encoding="utf-8")
    return mapping_dir


def _seed_artifact_db(path: Path) -> None:
    """Populate the pipeline's own SQLite artifact DB, as the stages would."""
    initialize_database(path)
    with sqlite3.connect(path) as con:
        con.executemany(
            "INSERT OR REPLACE INTO pages (page_id, doc_id, page_number, full_path,"
            " ticket_number, language, page_type, extracted_text, redacted_text)"
            " VALUES (?, ?, ?, ?, ?, 'English', 'Letter', ?, ?)",
            [
                (page_id, doc_id, n, f"/synthetic/{doc_id}.pdf", ticket, raw, red)
                for page_id, doc_id, n, ticket, raw, red in _PAGES
            ],
        )
        con.executemany(
            "INSERT OR REPLACE INTO documents (doc_id, ticket_number, grievance,"
            " summary, grievance_category) VALUES (?, ?, ?, ?, ?)",
            list(_DOCUMENTS),
        )
        con.commit()


def _make_oltp(tmp_path: Path) -> str:
    """Create the OLTP schema and seed the synthetic complaint history."""
    url = f"sqlite+aiosqlite:///{tmp_path}/oltp.db"
    sync = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(sync)
    with sync.begin() as conn:
        conn.execute(insert(Complaint), list(_COMPLAINTS))
    sync.dispose()
    return url


class _CanaryProcessor:
    """Canned extraction/classification, **real** routing.

    The models are what the synthetic variant cannot run. Routing is not a
    model, so it stays on the real code path: that is the half of the
    submit-path this canary is actually guarding.
    """

    name = "canary"

    def __init__(self, router: MappingRouter) -> None:
        self._router = router

    def process(
        self,
        *,
        grievance_id: str,
        ticket_no: str,
        text=None,
        document_name=None,
        document_bytes=None,
        district=None,
    ) -> GrievanceResult:
        from datetime import UTC, datetime

        body = text or f"[canary OCR of {document_name}]"
        return GrievanceResult(
            id=grievance_id,
            ticket_no=ticket_no,
            status="Submitted",
            submitted_on=datetime.now(UTC),
            extraction=ExtractionResult(source="text", extracted_text=body),
            redaction=RedactionResult(redacted_text=body, entities=[]),
            classification=ClassificationResult(category="Energy", language="en"),
            summary="Canary submission.",
            routing=self._router.route(category="Energy", district=district),
            triage=TriageResult(),
        )


# --- the canary --------------------------------------------------------------


def test_synthetic_pipeline_end_to_end(tmp_path):
    """Every hop, with row counts asserted where they are checkable."""
    artifact_db = tmp_path / "pipeline.sqlite"
    _seed_artifact_db(artifact_db)
    oltp_url = _make_oltp(tmp_path)
    lake_dir = tmp_path / "interim"

    # hop 1: artifact DB -> OLTP
    exported = export_pipeline_db(artifact_db, oltp_url=oltp_url)
    assert exported == {"pages": len(_PAGES), "documents": len(_DOCUMENTS)}

    # hop 2: OLTP -> Parquet lake. Counts must survive the crossing.
    materialized = materialize(oltp_url=oltp_url, out_dir=lake_dir)
    assert materialized == {
        "complaints": len(_COMPLAINTS),
        "action_history": 0,
        "pages": len(_PAGES),
        "documents": len(_DOCUMENTS),
        # Empty here: redacting the historical grievance text is its own job
        # (#69), not a stage of the document pipeline this exercises.
        "grievance_redactions": 0,
    }

    # hop 3: the lake reads back, and the pipeline's own output survived it
    pages = lake.read("pages", lake_dir=lake_dir)
    assert pages.height == len(_PAGES)
    assert set(pages["ticket_number"]) == {"SYN0000001", "SYN0000002"}
    assert all(t.startswith("<NAME>") for t in pages["redacted_text"])

    documents = lake.read("documents", lake_dir=lake_dir)
    assert set(documents["grievance_category"]) == {"Electricity", "Drinking Water Supply"}


def test_no_raw_page_text_reaches_the_lake(tmp_path):
    """The guarantee that actually holds (ROADMAP §3.2, issue #49).

    Not "no un-redacted PII reaches the lake" — that is false, because
    ``complaints.grievance`` is carried from the dump verbatim and has never
    met ``pii_tagger``. The page-text guarantee is the one this pipeline can
    make: raw OCR of a scanned document stays behind in OLTP.
    """
    artifact_db = tmp_path / "pipeline.sqlite"
    _seed_artifact_db(artifact_db)
    oltp_url = _make_oltp(tmp_path)
    lake_dir = tmp_path / "interim"

    export_pipeline_db(artifact_db, oltp_url=oltp_url)
    materialize(oltp_url=oltp_url, out_dir=lake_dir)

    # the marker only ever appears in pages.extracted_text
    for parquet in sorted(lake_dir.glob("*.parquet")):
        assert _RAW_MARKER.encode() not in parquet.read_bytes(), (
            f"raw page text reached the lake in {parquet.name}"
        )

    assert "extracted_text" not in lake.read("pages", lake_dir=lake_dir).columns

    # and it really is still in OLTP — the assertion above is not passing
    # because the text was lost somewhere upstream
    sync = create_engine(oltp_url.replace("+aiosqlite", ""))
    with sync.connect() as conn:
        from sqlalchemy import text as sql

        raw = conn.execute(sql("SELECT extracted_text FROM pages")).scalars().all()
    sync.dispose()
    assert all(_RAW_MARKER in r for r in raw)


def test_history_is_queryable_from_the_lake(tmp_path):
    """`/history` reads real rows, not the mock provider's 120 fake ones."""
    oltp_url = _make_oltp(tmp_path)
    lake_dir = tmp_path / "interim"
    materialize(oltp_url=oltp_url, out_dir=lake_dir)

    app = create_app(history=LakeHistory(lake_dir=lake_dir))
    with TestClient(app) as client:
        page = client.get("/history", params={"limit": 50}).json()
    assert page["total"] == len(_COMPLAINTS)
    assert {item["ticket_no"] for item in page["items"]} == {
        "SYN0000001",
        "SYN0000002",
    }

    # the filters the frontend sends are wired to the same rows
    with TestClient(app) as client:
        filtered = client.get("/history", params={"district": "Cuttack"}).json()
    assert [item["ticket_no"] for item in filtered["items"]] == ["SYN0000002"]

    with TestClient(app) as client:
        searched = client.get("/history", params={"q": "hand pump"}).json()
    assert [item["ticket_no"] for item in searched["items"]] == ["SYN0000002"]


def test_routing_resolves_off_master_tables_not_fallback(tmp_path):
    """A silent ``method="fallback"`` is the failure this catches.

    The master CSVs are DVC-tracked and absent on a fresh machine, and
    ``load_mapping_tables`` degrades to ``None`` rather than raising. The demo
    then routes on the illustrative rules while still looking healthy.
    """
    router = MappingRouter(mapping_dir=_write_mapping_dir(tmp_path))
    assert router.mapping_loaded

    result = router.route(category="Energy", district="Khordha")
    assert result.method == "rules"
    assert result.dept == "Energy"
    assert result.designation == "Executive Engineer"
    assert result.escalation_authority == "CEO TPCODL"

    # the negative control: same call, no CSVs -> the router still answers, and
    # says so honestly
    degraded = MappingRouter(mapping_dir=tmp_path / "absent")
    assert not degraded.mapping_loaded


def _live_app(monkeypatch, tmp_path, *, oltp_url: str, lake_dir: Path):
    """Build the app through the real ``create_live_app``.

    Only ``build_processor`` is replaced — it is the piece that loads models.
    Store selection, history selection and the ``Settings`` lookup all run for
    real, which is the point.
    """
    processor = _CanaryProcessor(MappingRouter(mapping_dir=_write_mapping_dir(tmp_path)))
    monkeypatch.setattr(serve, "build_processor", lambda: processor)
    # LakeHistory() takes no lake_dir, so it resolves directories.INTERIM —
    # which is the real data/interim/. Redirecting it is what keeps this test
    # off real citizen data, not just a convenience.
    monkeypatch.setattr(directories, "INTERIM", lake_dir)
    monkeypatch.setenv("OLTP_DB_URL", oltp_url)
    return serve.create_live_app()


def test_live_app_wiring_selects_lake_history_and_db_store(monkeypatch, tmp_path):
    """submit -> persist -> fetch and /history, through ``create_live_app``.

    Two failures this catches that injecting the components could not: the
    deployed app silently keeping ``InMemoryResultStore`` because OLTP was
    never explicitly configured, and it serving ``MockHistory``'s fake rows.
    """
    oltp_url = _make_oltp(tmp_path)
    lake_dir = tmp_path / "interim"
    materialize(oltp_url=oltp_url, out_dir=lake_dir)

    app = _live_app(monkeypatch, tmp_path, oltp_url=oltp_url, lake_dir=lake_dir)
    with TestClient(app) as client:
        assert client.get("/health").json()["processor"] == "canary"

        history = client.get("/history", params={"limit": 50}).json()
        assert {item["ticket_no"] for item in history["items"]} == {
            "SYN0000001",
            "SYN0000002",
        }

        submitted = client.post(
            "/grievance", data={"text": "Street light out.", "district": "Khordha"}
        )
    assert submitted.status_code == 201
    result = submitted.json()
    assert result["routing"]["method"] == "rules"
    assert result["routing"]["dept"] == "Energy"

    # a restart rebuilds the app from scratch; the submission must survive it
    second = _live_app(monkeypatch, tmp_path, oltp_url=oltp_url, lake_dir=lake_dir)
    with TestClient(second) as client:
        fetched = client.get(f"/grievance/{result['id']}")
        missing = client.get("/grievance/deadbeefdead")

    assert fetched.status_code == 200
    assert fetched.json()["ticket_no"] == result["ticket_no"]
    assert missing.status_code == 404


def test_live_app_loses_submissions_when_oltp_is_not_explicitly_configured(
    monkeypatch, tmp_path
):
    """The silent fallback, pinned as behaviour rather than left as a surprise.

    ``_resolve_explicit_oltp_url`` treats the built-in SQLite default as "not
    configured" and hands back ``InMemoryResultStore``. The API stays healthy
    and submissions still return 201; they just do not survive a restart. If
    the box's ``deploy/.env`` is missing ``OLTP_DB_URL``, this is what the demo
    does.
    """
    lake_dir = tmp_path / "interim"
    lake_dir.mkdir()
    processor = _CanaryProcessor(MappingRouter(mapping_dir=_write_mapping_dir(tmp_path)))
    monkeypatch.setattr(serve, "build_processor", lambda: processor)
    monkeypatch.setattr(directories, "INTERIM", lake_dir)
    # set explicitly to the built-in default: "unset" would let a real .env
    # through, and this test must never resolve the production database
    monkeypatch.setenv("OLTP_DB_URL", DEFAULT_OLTP_DB_URL)

    with TestClient(serve.create_live_app()) as client:
        submitted = client.post("/grievance", data={"text": "Street light out."})
        assert submitted.status_code == 201
        grievance_id = submitted.json()["id"]
        # same process, same store: still there
        assert client.get(f"/grievance/{grievance_id}").status_code == 200

    with TestClient(serve.create_live_app()) as client:
        assert client.get(f"/grievance/{grievance_id}").status_code == 404


def test_submit_persists_and_fetches_through_the_db_store(tmp_path):
    """The store itself, isolated from how the live app selects it."""
    oltp_url = _make_oltp(tmp_path)
    mapping_dir = _write_mapping_dir(tmp_path)
    processor = _CanaryProcessor(MappingRouter(mapping_dir=mapping_dir))

    store = DatabaseResultStore(oltp_url)
    app = create_app(processor=processor, result_store=store)
    try:
        with TestClient(app) as client:
            submitted = client.post(
                "/grievance",
                data={"text": "Street light out.", "district": "Khordha"},
            )
        assert submitted.status_code == 201
        result = submitted.json()
    finally:
        asyncio.run(store.dispose())

    second_store = DatabaseResultStore(oltp_url)
    second_app = create_app(processor=processor, result_store=second_store)
    try:
        with TestClient(second_app) as client:
            fetched = client.get(f"/grievance/{result['id']}")
    finally:
        asyncio.run(second_store.dispose())

    assert fetched.status_code == 200
    assert fetched.json()["ticket_no"] == result["ticket_no"]


@pytest.mark.parametrize("table", ["complaints", "action_history", "pages", "documents"])
def test_every_lake_table_is_materialized(tmp_path, table):
    """All four tables exist after a run, empty ones included.

    A missing Parquet file and an empty one are different failures, and
    ``LakeHistory`` returns an empty page for both.
    """
    oltp_url = _make_oltp(tmp_path)
    lake_dir = tmp_path / "interim"
    materialize(oltp_url=oltp_url, out_dir=lake_dir)
    assert lake.lake_path(table, lake_dir).exists()
