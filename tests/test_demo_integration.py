"""Unified demo integration contract — frozen 14 August 2026 gate.

Covers the six demo-blocking surfaces from DELIVERY Table 1 / 2026-08-08-demo-closure
as a single CI-safe module:

* live app wiring via ``create_live_app`` (DatabaseResultStore + LakeHistory when
  OLTP_DB_URL is set) — same seam as test_e2e_synthetic
* full GrievanceResult shape after submit (extraction, redaction, classification,
  summary, routing, triage with spam_score and duplicate_review)
* routing ladder never returning ``mock`` when the crosswalk artifact is present
* supervisor aggregate contract with numeric reconciliation (closure + workload + spike)
* slice label frozen to Sambalpur/2024

Heavy model paths delegate to injected fakes; real weights stay opt-in via
JANASUNANI_RUN_MODEL_SMOKE.

Every test in this module is marked ``@pytest.mark.demo_contract`` so CI can
collect it with ``-m demo_contract`` or run it unconditionally in the rehearsal.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, insert  # noqa: E402

from janasunani.config import DEMO_SLICE_LABEL, directories  # noqa: E402
from janasunani.db.models import Base, Complaint  # noqa: E402
from janasunani.inference import serve  # noqa: E402
from janasunani.olap.materialize import materialize  # noqa: E402
from janasunani.pipeline.db import initialize_database  # noqa: E402
from janasunani.routing.crosswalk import DEFAULT_ARTIFACT  # noqa: E402
from janasunani.routing.rules import MappingRouter  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.intelligence import ArtifactSupervisorProvider  # noqa: E402
from janasunani.serving.schemas import (  # noqa: E402
    ClassificationResult,
    ExtractionResult,
    GrievanceResult,
    RedactionResult,
)

pytestmark = pytest.mark.demo_contract

_RAW_MARKER = "DEMO-CONTRACT-RAW-MUST-NOT-REACH-LAKE-42"

_PAGES = (
    ("DEM1-p1", "DEM1", 1, "DEM0000001", f"{_RAW_MARKER} hand pump broken", "hand pump broken [redacted]"),
    ("DEM2-p1", "DEM2", 1, "DEM0000002", f"{_RAW_MARKER} street light out", "street light out [redacted]"),
)

_DOCUMENTS = (
    ("DEM1", "DEM0000001", "hand pump broken", "Hand pump repair summary", "Drinking Water Supply"),
    ("DEM2", "DEM0000002", "street light out", "Street light summary", "Electricity"),
)

_COMPLAINTS = (
    {"ticket_no": "DEM0000001", "district": "Khordha", "category": "Drinking Water Supply", "grievance": "hand pump broken"},
    {"ticket_no": "DEM0000002", "district": "Cuttack", "category": "Electricity", "grievance": "street light out"},
)

_CLOSURE_FIELDS = (
    "resolved_complaints",
    "ladder_closures",
    "bare",
    "with_action",
    "benefit",
    "claims_action",
    "off_ladder",
    "bare_share_of_ladder_pct",
    "bare_share_of_resolved_pct",
    "ladder_coverage_pct",
    "off_ladder_share_pct",
)


def _closure_row(**overrides: str) -> dict[str, str]:
    row = {
        "resolved_complaints": "8",
        "ladder_closures": "6",
        "bare": "3",
        "with_action": "2",
        "benefit": "1",
        "claims_action": "3",
        "off_ladder": "2",
        "bare_share_of_ladder_pct": "50.0",
        "bare_share_of_resolved_pct": "37.5",
        "ladder_coverage_pct": "75.0",
        "off_ladder_share_pct": "25.0",
    }
    return {**row, **overrides}


def _write_closure(findings_dir: Path, name: str = "closure_recording_no_action.csv") -> Path:
    path = findings_dir / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_CLOSURE_FIELDS))
        writer.writeheader()
        writer.writerow(_closure_row())
    return path


def _write_workload(agg_dir: Path, digest: str, scope: str = "sha256:" + "c" * 64) -> Path:
    path = agg_dir / "workload.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "slice_district",
                "slice_category",
                "slice_period",
                "total_filings",
                "distinct_problems",
                "duplicate_adjustment",
                "source_name",
                "source_snapshot_id",
                "grouping_scope_snapshot_id",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "slice_district": "Sambalpur",
                "slice_category": "all",
                "slice_period": "2024",
                "total_filings": "100",
                "distinct_problems": "80",
                "duplicate_adjustment": "20",
                "source_name": "oltp:complaints+grievance_redactions",
                "source_snapshot_id": digest,
                "grouping_scope_snapshot_id": scope,
            }
        )
    return path


def _write_spike(agg_dir: Path, digest: str, scope: str = "sha256:" + "c" * 64) -> Path:
    path = agg_dir / "spike.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "slice_district",
                "slice_category",
                "slice_period",
                "filings",
                "distinct_problems",
                "distinct_citizens",
                "source_name",
                "source_snapshot_id",
                "grouping_scope_snapshot_id",
                "interpretation",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "slice_district": "Sambalpur",
                "slice_category": "all",
                "slice_period": "2024",
                "filings": "50",
                "distinct_problems": "40",
                "distinct_citizens": "45",
                "source_name": "oltp:complaints+grievance_redactions",
                "source_snapshot_id": digest,
                "grouping_scope_snapshot_id": scope,
                "interpretation": "Sambalpur 2024 spike: 50 filings decomposed into 40 problems and 45 citizens",
            }
        )
    return path


def _seed_artifact_db(path: Path) -> None:
    initialize_database(path)
    with sqlite3.connect(path) as con:
        con.executemany(
            "INSERT OR REPLACE INTO pages (page_id, doc_id, page_number, full_path,"
            " ticket_number, language, page_type, extracted_text, redacted_text)"
            " VALUES (?, ?, ?, ?, ?, 'English', 'Letter', ?, ?)",
            [
                (page_id, doc_id, n, f"/syn/{doc_id}.pdf", ticket, raw, red)
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
    url = f"sqlite+aiosqlite:///{tmp_path}/oltp.db"
    sync = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(sync)
    with sync.begin() as conn:
        conn.execute(insert(Complaint), list(_COMPLAINTS))
    sync.dispose()
    return url


_MAPPING_CSVS = {
    "m_admin_category.csv": "intCategoryId,vchCategory,vchCategoryOD\n1,Energy,ଶକ୍ତି\n2,Drinking Water Supply,ପାନୀୟ ଜଳ\n",
    "m_admin_hierarchy_value.csv": "intAdminHierarchyValueId,vchAdminHierarchyValue,vchAdminHierarchyValueO\n5,Energy,ଶକ୍ତି\n",
    "m_admin_offices.csv": "intOfficeId,vchOfficeName\n5,Departments\n6,Collector\n",
    "t_admin_escalation.csv": "intEscalationId,intDepartmentId,intOfficeId,intEscalation,vchDesignationSequence,bitDeletedFlag\n1,5,6,2,\"17,18\",0\n",
    "m_role.csv": "intRoleId,vchRoleName\n17,Executive Engineer\n18,CEO TPCODL\n",
}


def _write_mapping_dir(tmp_path: Path) -> Path:
    mapping_dir = tmp_path / "janasunani-mappings"
    mapping_dir.mkdir(exist_ok=True)
    for name, body in _MAPPING_CSVS.items():
        (mapping_dir / name).write_text(body, encoding="utf-8")
    return mapping_dir


class _CanaryProcessor:
    """Canned processor with real routing for live-app wiring tests."""

    name = "canary-demo"

    def __init__(self, router: MappingRouter) -> None:
        self._router = router

    def process(  # type: ignore[no-untyped-def]
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
        # Use bounded spam scorer over redacted text for triage shape
        from janasunani.serving.triage import UnwiredTriageProvider

        triage = UnwiredTriageProvider().assess(
            redacted_text=body, district=district, submitted_on=datetime.now(UTC)
        )
        return GrievanceResult(
            id=grievance_id,
            ticket_no=ticket_no,
            status="Submitted",
            submitted_on=datetime.now(UTC),
            extraction=ExtractionResult(source="text", extracted_text=body),
            redaction=RedactionResult(redacted_text=body, entities=[]),
            classification=ClassificationResult(category="Energy", language="en"),
            summary="Canary demo summary.",
            routing=self._router.route(category="Energy", district=district),
            triage=triage,
        )


def _live_app(monkeypatch, tmp_path, *, oltp_url: str, lake_dir: Path):
    processor = _CanaryProcessor(MappingRouter(mapping_dir=_write_mapping_dir(tmp_path)))
    monkeypatch.setattr(serve, "build_processor", lambda: processor)
    monkeypatch.setattr(directories, "INTERIM", lake_dir)
    monkeypatch.setenv("OLTP_DB_URL", oltp_url)
    return serve.create_live_app()


@pytest.mark.demo_contract
def test_demo_slice_label_is_frozen() -> None:
    assert DEMO_SLICE_LABEL == "Sambalpur/2024"


@pytest.mark.demo_contract
def test_live_app_wiring_selects_db_store_and_lake_history(monkeypatch, tmp_path):
    oltp_url = _make_oltp(tmp_path)
    lake_dir = tmp_path / "interim"
    materialize(oltp_url=oltp_url, out_dir=lake_dir)

    app = _live_app(monkeypatch, tmp_path, oltp_url=oltp_url, lake_dir=lake_dir)
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["processor"] == "canary-demo"

        history = client.get("/history", params={"limit": 50}).json()
        assert history["total"] == 2
        assert {item["ticket_no"] for item in history["items"]} == {"DEM0000001", "DEM0000002"}

        submitted = client.post("/grievance", data={"text": "Street light out near school.", "district": "Khordha"})
        assert submitted.status_code == 201
        body = submitted.json()
        # Full shape check
        for key in ("extraction", "redaction", "classification", "summary", "routing", "triage"):
            assert key in body, f"missing {key} in GrievanceResult"
        assert body["extraction"]["extracted_text"]
        assert body["redaction"]["redacted_text"]
        assert body["classification"]["category"]
        assert body["summary"]
        assert body["routing"]["method"] != "mock"
        assert body["triage"]["spam"]["spam_score"] is not None
        assert 0.0 <= body["triage"]["spam"]["spam_score"] <= 1.0
        assert body["triage"]["duplicate_review"]["decision"] in {
            "matched",
            "no_match",
            "abstained",
            "not_indexed",
            "unavailable",
        }

    # persistence across restart
    second = _live_app(monkeypatch, tmp_path, oltp_url=oltp_url, lake_dir=lake_dir)
    with TestClient(second) as client:
        fetched = client.get(f"/grievance/{body['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["ticket_no"] == body["ticket_no"]


@pytest.mark.demo_contract
def test_grievance_result_full_shape_via_direct_app(tmp_path):
    from janasunani.serving.history import LakeHistory
    from janasunani.serving.store import DatabaseResultStore

    oltp_url = _make_oltp(tmp_path)
    router = MappingRouter(mapping_dir=_write_mapping_dir(tmp_path))
    processor = _CanaryProcessor(router)
    store = DatabaseResultStore(oltp_url)
    # lake not needed for this shape test but wire one anyway
    lake_dir = tmp_path / "interim"
    materialize(oltp_url=oltp_url, out_dir=lake_dir)
    app = create_app(processor=processor, history=LakeHistory(lake_dir=lake_dir), result_store=store)

    with TestClient(app) as client:
        resp = client.post("/grievance", data={"text": "Hand pump broken for two months.", "district": "Cuttack"})
        assert resp.status_code == 201
        body = resp.json()
        assert set(body) == {"id", "ticket_no", "status", "submitted_on", "extraction", "redaction", "classification", "summary", "routing", "triage"}
        assert body["extraction"]["source"] == "text"
        assert body["redaction"]["redacted_text"]
        assert body["classification"]["category"]
        assert body["classification"]["language"]
        assert body["summary"]
        assert body["routing"]["dept"]
        assert body["routing"]["office"]
        assert 0.0 <= body["routing"]["confidence"] <= 1.0
        assert body["routing"]["method"] in {"learned", "rules", "fallback"}
        assert body["routing"]["method"] != "mock"
        if body["routing"]["method"] == "learned":
            assert body["routing"]["empirical_evidence"] is not None
            ev = body["routing"]["empirical_evidence"]
            assert ev["support"] >= 3
            assert 0.0 < ev["concentration"] <= 1.0
            assert ev["width"] in {"category", "category+district", "category+subcategory", "category+subcategory+district"}
        else:
            assert body["routing"]["empirical_evidence"] is None
        # triage contract
        triage = body["triage"]
        assert triage["duplicate_review"]["decision"] in {"matched", "no_match", "abstained", "not_indexed", "unavailable"}
        spam = triage["spam"]
        assert spam["decision"] in {"review", "abstained"}
        assert spam["spam_score"] is not None
        assert 0.0 <= spam["spam_score"] <= 1.0
        assert spam["spam_reason"] in {"low_signal_details_inadequate", "low_signal_no_grievance", "repetition_collapse", "length_too_short", "clean"}

    import asyncio

    asyncio.run(store.dispose())


@pytest.mark.demo_contract
def test_routing_ladder_never_mock_when_crosswalk_present(tmp_path):
    assert DEFAULT_ARTIFACT.exists(), "routing_crosswalk.json must be committed for demo"
    from janasunani.routing.rules import DEFAULT_ROUTER

    # Known category should hit learned rung
    learned = DEFAULT_ROUTER.route(category="Energy")
    assert learned.method in {"learned", "rules", "fallback"}
    assert learned.method != "mock"
    if learned.method == "learned":
        assert learned.empirical_evidence is not None
        assert learned.empirical_evidence.support >= 3

    # Known category+district where district rung exists
    district_hit = DEFAULT_ROUTER.route(category="Water Supply", district="Angul")
    assert district_hit.method in {"learned", "rules", "fallback"}
    assert district_hit.method != "mock"

    # Unknown category falls through to rules or fallback, never mock
    unknown = DEFAULT_ROUTER.route(category="Astrophysics-xyz-unknown-999")
    assert unknown.method in {"rules", "fallback"}
    assert unknown.empirical_evidence is None

    # Direct mapping router also never mock
    router = MappingRouter(mapping_dir=_write_mapping_dir(tmp_path))
    r = router.route(category="Energy", district="Khordha")
    assert r.method != "mock"


@pytest.mark.demo_contract
def test_supervisor_with_fixture_aggregates_returns_recorded_panels(tmp_path):
    findings_dir = tmp_path / "findings"
    findings_dir.mkdir()
    agg_dir = tmp_path / "aggregates"
    agg_dir.mkdir()

    _write_closure(findings_dir)
    digest = "sha256:" + "a" * 64
    _write_workload(agg_dir, digest)
    _write_spike(agg_dir, digest)

    provider = ArtifactSupervisorProvider(findings_dir, aggregates_dir=agg_dir)
    dashboard = provider.dashboard()

    # All three panels must be recorded (not unavailable)
    assert dashboard.closure.provenance.state == "recorded"
    assert dashboard.workload.provenance.state == "recorded"
    assert dashboard.spike.provenance.state == "recorded"

    # Numeric reconciliation
    assert dashboard.closure.numerator == 3
    assert dashboard.closure.primary_denominator == 6
    assert dashboard.closure.secondary_denominator == 8
    assert dashboard.closure.primary_share_pct == 50.0
    assert dashboard.closure.secondary_share_pct == 37.5

    assert dashboard.workload.total_filings.value == 100
    assert dashboard.workload.distinct_problems.value == 80
    assert dashboard.workload.duplicate_adjustment.value == 20
    assert dashboard.workload.total_filings.value - dashboard.workload.distinct_problems.value == dashboard.workload.duplicate_adjustment.value

    assert dashboard.spike.counts[0].value == 50
    assert dashboard.spike.counts[1].value == 40
    assert dashboard.spike.counts[2].value == 45
    assert dashboard.spike.counts[1].value <= dashboard.spike.counts[0].value
    assert dashboard.spike.counts[2].value <= dashboard.spike.counts[0].value

    # Via HTTP
    app = create_app(supervisor=provider)
    with TestClient(app) as client:
        resp = client.get("/supervisor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["closure"]["provenance"]["state"] == "recorded"
        assert body["workload"]["provenance"]["state"] == "recorded"
        assert body["spike"]["provenance"]["state"] == "recorded"
        # camelCase DTO
        assert "primaryDenominator" in body["closure"]
        assert "totalFilings" in body["workload"]
        assert "counts" in body["spike"]
        assert body["closure"]["numerator"] == 3
        assert body["workload"]["totalFilings"]["value"] == 100
        assert body["workload"]["distinctProblems"]["value"] == 80
        assert body["workload"]["duplicateAdjustment"]["value"] == 20


@pytest.mark.demo_contract
def test_supervisor_fails_closed_without_aggregates(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    provider = ArtifactSupervisorProvider(empty)
    dashboard = provider.dashboard()
    assert dashboard.closure.provenance.state == "unavailable"
    assert dashboard.workload.provenance.state == "unavailable"
    assert dashboard.spike.provenance.state == "unavailable"
    # No PII or row-level fields in serialized unavailable dashboard
    import json as _json

    serialized = _json.dumps(dashboard.model_dump(mode="json", by_alias=True)).lower()
    for forbidden in ('"grievance"', '"ticket_no"', '"mobile"', '"email"'):
        assert forbidden not in serialized
