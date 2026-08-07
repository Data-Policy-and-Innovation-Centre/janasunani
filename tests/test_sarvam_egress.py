"""Recorded-transport coverage for the sole authorized-external client."""

from __future__ import annotations

import sqlite3
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pytest

from janasunani.egress.sarvam import (
    AUTHORIZATION_REFERENCE,
    MODEL_ID,
    PROVIDER_REGISTRY,
    SarvamAuditContext,
    SarvamError,
    SarvamVisionAdapter,
    SqliteAuditLog,
)


@dataclass
class RecordedResponse:
    status_code: int
    payload: Any = None
    text: str = ""
    content: bytes = b""

    def json(self) -> Any:
        return self.payload


class RecordedTransport:
    """A deliberately inert HTTP transport replaying checked-in shaped data."""

    def __init__(self, responses: list[RecordedResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> RecordedResponse:
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> RecordedResponse:
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


def _context() -> SarvamAuditContext:
    return SarvamAuditContext(ticket="T-42", stage="ocr_extraction", document_id="doc:1")


def _zip_output(**members: str) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue()


def _audit_rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            """
            SELECT ticket, stage, provider, model_id, bytes_sent, timestamp,
                   authorization_reference, operation, event, language, job_id
            FROM authorized_external_audit ORDER BY id
            """
        ).fetchall()


def test_digitise_replays_submit_poll_and_download_with_an_auditable_job(tmp_path):
    audit_path = tmp_path / "pipeline.sqlite"
    transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-7", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-7", "status": "running"}),
            RecordedResponse(
                200,
                {
                    "job_id": "job-7",
                    "status": "completed",
                    "usage": {"pages_total": 1, "pages_succeeded": 1, "pages_failed": 0},
                },
            ),
            RecordedResponse(200, {"download_url": "https://download.example/job-7"}),
            RecordedResponse(
                200,
                content=_zip_output(
                    **{
                        "document.md": "# Water complaint\n\nName removed",
                        "pages.json": "{}",
                    }
                ),
            ),
        ]
    )
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        transport=transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    actual = adapter.digitise(b"fixture-png", "page.png", "od-IN", _context())

    assert actual == "# Water complaint\n\nName removed"
    assert [method for method, _, _ in transport.calls] == ["POST", "GET", "GET", "GET", "GET"]
    submit = transport.calls[0]
    assert submit[1].endswith("/doc-ai/v1/job/digitise")
    assert submit[2]["data"] == {"language": "od-IN", "output_format": "md"}
    assert "model" not in submit[2]["data"]
    assert submit[2]["headers"]["Idempotency-Key"] == adapter._idempotency_key(
        _context(), "digitise", b"fixture-png"
    )

    rows = _audit_rows(audit_path)
    assert [row[8] for row in rows] == [
        "submission",
        "poll",
        "poll",
        "result_lookup",
        "download",
    ]
    assert rows[0][:7] == (
        "T-42",
        "ocr_extraction",
        "sarvam-hosted",
        MODEL_ID,
        len(b"fixture-png"),
        rows[0][5],
        AUTHORIZATION_REFERENCE,
    )
    assert all(row[10] == "job-7" for row in rows)
    assert [row[4] for row in rows] == [len(b"fixture-png"), 0, 0, 0, 0]


def test_kill_switch_never_constructs_or_calls_the_remote_transport(tmp_path):
    transport = RecordedTransport([])
    adapter = SarvamVisionAdapter(
        enabled=False,
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
        transport=transport,
    )

    result = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local pytesseract text"
    )

    assert result == "local pytesseract text"
    assert transport.calls == []
    assert [row[8] for row in _audit_rows(tmp_path / "audit.sqlite")] == ["disabled"]


def test_nonterminating_recorded_job_falls_back_after_its_bounded_poll_loop(tmp_path):
    audit_path = tmp_path / "audit.sqlite"
    transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-stuck", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-stuck", "status": "running"}),
            RecordedResponse(200, {"job_id": "job-stuck", "status": "running"}),
        ]
    )
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        transport=transport,
        poll_interval_seconds=0,
        max_poll_attempts=2,
        sleep=lambda _seconds: None,
    )

    actual = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local text"
    )

    assert actual == "local text"
    assert [row[8] for row in _audit_rows(audit_path)] == [
        "submission",
        "poll",
        "poll",
        "fallback",
    ]


def test_recorded_submission_is_resumed_instead_of_resubmitted_and_rebilled(tmp_path):
    audit_path = tmp_path / "audit.sqlite"
    audit_log = SqliteAuditLog(audit_path)
    first_transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-resume", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-resume", "status": "running"}),
        ]
    )
    first = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        transport=first_transport,
        max_poll_attempts=1,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )
    assert first.digitise_or_fallback(b"fixture-png", "page.png", "en-IN", _context(), lambda: "local") == "local"

    resumed_transport = RecordedTransport(
        [
            RecordedResponse(200, {"job_id": "job-resume", "status": "completed"}),
            RecordedResponse(200, {"download_url": "https://download.example/job-resume"}),
            RecordedResponse(200, content=_zip_output(**{"result.md": "resumed markdown"})),
        ]
    )
    resumed = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        transport=resumed_transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    assert resumed.digitise(b"fixture-png", "page.png", "en-IN", _context()) == "resumed markdown"
    assert [method for method, _, _ in resumed_transport.calls] == ["GET", "GET", "GET"]
    assert "resume" in [row[8] for row in _audit_rows(audit_path)]


@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (_zip_output(**{"pages.json": "{}"}), "exactly one"),
        (_zip_output(**{"one.md": "one", "two.md": "two"}), "exactly one"),
        (b"not-a-zip", "valid ZIP"),
    ],
)
def test_digitise_rejects_invalid_or_ambiguous_zip_results(archive, message):
    with pytest.raises(SarvamError, match=message):
        SarvamVisionAdapter._primary_markdown(archive)


def test_idempotency_key_includes_the_actual_uploaded_bytes():
    same_context = _context()
    assert SarvamVisionAdapter._idempotency_key(same_context, "digitise", b"first") != (
        SarvamVisionAdapter._idempotency_key(same_context, "digitise", b"changed")
    )


def test_failed_submission_is_audited_with_only_its_actual_upload_bytes(tmp_path):
    audit_path = tmp_path / "audit.sqlite"
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        transport=RecordedTransport([RecordedResponse(503, {"error": "unavailable"})]),
    )

    assert adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local"
    ) == "local"
    rows = _audit_rows(audit_path)
    assert [(row[8], row[4]) for row in rows] == [
        ("submission_error", len(b"fixture-png")),
        ("fallback", 0),
    ]


def test_prefers_documented_api_key_environment_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("SARVAM_API_KEY", "preferred")
    monkeypatch.setenv("SARVAM_API_SUBSCRIPTION_KEY", "legacy")
    adapter = SarvamVisionAdapter(enabled=False, audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"))
    assert adapter.api_key == "preferred"


def test_extract_is_a_distinct_operation_and_requires_one_pinned_schema_source(tmp_path):
    adapter = SarvamVisionAdapter(enabled=False, audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"))

    with pytest.raises(ValueError, match="exactly one"):
        adapter.extract(b"page", "page.png", "en-IN", _context())
    with pytest.raises(ValueError, match="exactly one"):
        adapter.extract(
            b"page", "page.png", "en-IN", _context(), schema={}, config_id="config-1"
        )

    route = PROVIDER_REGISTRY["sarvam-vision"]
    assert route.trust_tier == "authorized-external"
    assert route.fallback == "pytesseract"
    assert route.authorization_reference == AUTHORIZATION_REFERENCE


def test_pipeline_sarvam_switch_uses_the_existing_pytesseract_callback_when_disabled(
    tmp_path, monkeypatch
):
    # Base CI deliberately excludes the heavy pipeline OCR stack.  Keep this
    # integration assertion in the adapter module, but skip only this test
    # there; the pipeline-core job exercises the real pytesseract fallback.
    pytest.importorskip("pdf2image")
    from PIL import Image

    from janasunani.pipeline.stages.ocr_extraction import stage
    from janasunani.pipeline.stages.ocr_extraction import pytesseract_backend

    monkeypatch.setattr(stage, "render_page", lambda _path, _number: Image.new("RGB", (2, 2)))
    monkeypatch.setattr(pytesseract_backend, "extract_text", lambda _image, force_lang=None: "local OCR")
    stage._worker_init("sarvam", sarvam_enabled=False, db_path=tmp_path / "pipeline.sqlite")

    result = stage._process_page(
        {
            "page_id": "page-1",
            "doc_id": "doc-1",
            "page_number": 1,
            "file_path": str(tmp_path / "page.png"),
            "language": "Odia",
            "ticket": "T-1",
        }
    )

    assert result["text"] == "local OCR"
    assert result["error"] is None
    assert [row[8] for row in _audit_rows(tmp_path / "pipeline.sqlite")] == ["disabled"]
