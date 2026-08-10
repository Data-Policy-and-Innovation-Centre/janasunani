"""Recorded-transport coverage for the sole authorized-external client."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Any

import pytest

from janasunani.config import Settings
from janasunani.egress.sarvam import (
    AUTHORIZATION_REFERENCE,
    GovernanceControl,
    MODEL_ID,
    PROVIDER_REGISTRY,
    SARVAM_OCR_MODEL,
    SarvamAuditContext,
    SarvamError,
    SarvamPollTimeout,
    SarvamVisionAdapter,
    SqliteAuditLog,
)


@dataclass
class RecordedResponse:
    status_code: int
    payload: Any = None
    text: str = ""
    content: bytes = b""
    headers: dict[str, str] | None = None

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


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _wrap(fields: dict[str, Any]) -> dict[str, Any]:
    """Wrap a field map as the JSON Schema document Sarvam Extract requires.

    The provider rejects a bare field map with HTTP 400, so fixtures that pass
    one are testing a request that could never succeed. See
    ``_validate_extract_schema``.
    """
    return {"type": "object", "properties": fields}


def _context() -> SarvamAuditContext:
    return SarvamAuditContext(ticket="T-42", stage="ocr_extraction", document_id="doc:1")


def _verified_test_route():
    """Recorded transports use explicit synthetic governance evidence."""
    control = GovernanceControl(statement="verified recorded-test fixture", verified=True)
    return replace(
        PROVIDER_REGISTRY["sarvam-vision"],
        retention_terms=control,
        encryption_in_transit=control,
        encryption_at_rest=control,
    )


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


def _audit_keys(path):
    with sqlite3.connect(path) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT idempotency_key FROM authorized_external_audit ORDER BY id"
            )
        ]


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
        route=_verified_test_route(),
        transport=transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    actual = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "od-IN", _context(), lambda: "local OCR"
    )

    assert actual.text == "# Water complaint\n\nName removed"
    assert actual.ocr_model == SARVAM_OCR_MODEL
    assert [method for method, _, _ in transport.calls] == ["POST", "GET", "GET", "GET", "GET"]
    submit = transport.calls[0]
    assert submit[1].endswith("/doc-ai/v1/job/digitise")
    assert submit[2]["data"] == {"language": "od-IN", "output_format": "md"}
    assert "model" not in submit[2]["data"]
    assert submit[2]["headers"]["Idempotency-Key"] == adapter._idempotency_key(
        _context(),
        "digitise",
        b"fixture-png",
        filename="page.png",
        form={"language": "od-IN", "output_format": "md"},
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
    assert set(_audit_keys(audit_path)) == {submit[2]["headers"]["Idempotency-Key"]}


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

    assert result.text == "local pytesseract text"
    assert result.ocr_model == "pytesseract"
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
        route=_verified_test_route(),
        transport=transport,
        poll_interval_seconds=0,
        max_poll_attempts=2,
        sleep=lambda _seconds: None,
    )

    actual = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local text"
    )

    assert actual.text == "local text"
    assert actual.ocr_model == "pytesseract"
    assert [row[8] for row in _audit_rows(audit_path)] == [
        "submission",
        "poll",
        "poll",
        "fallback",
    ]
    assert len(set(_audit_keys(audit_path))) == 1


@pytest.mark.parametrize(
    "usage",
    [
        {"pages_total": 3, "pages_succeeded": 2, "pages_failed": 1},
        {"pages_total": 1, "pages_succeeded": 1, "pages_failed": 0},
    ],
)
def test_partially_completed_job_is_rejected_and_audited_as_fallback(tmp_path, usage):
    audit_path = tmp_path / "audit.sqlite"
    transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-partial", "status": "pending"}),
            RecordedResponse(
                200,
                {
                    "job_id": "job-partial",
                    "status": "partially_completed",
                    "usage": usage,
                },
            ),
        ]
    )
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        route=_verified_test_route(),
        transport=transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    outcome = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local text"
    )

    assert outcome.text == "local text"
    assert outcome.ocr_model == "pytesseract"
    assert [method for method, _, _ in transport.calls] == ["POST", "GET"]
    assert [row[8] for row in _audit_rows(audit_path)] == [
        "submission",
        "poll",
        "fallback",
    ]


@pytest.mark.parametrize("terminal_status", ["failed", "rejected", "partially_completed"])
def test_terminal_unsuccessful_job_is_not_resumed_on_retry(tmp_path, terminal_status):
    audit_path = tmp_path / "audit.sqlite"
    audit_log = SqliteAuditLog(audit_path)
    first_transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-failed", "status": "pending"}),
            RecordedResponse(
                200,
                {
                    "job_id": "job-failed",
                    "status": terminal_status,
                    "usage": {
                        "pages_total": 1,
                        "pages_succeeded": 0,
                        "pages_failed": 1,
                    },
                },
            ),
        ]
    )
    first = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        route=_verified_test_route(),
        transport=first_transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )
    first_outcome = first.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local OCR"
    )
    assert first_outcome.ocr_model == "pytesseract"

    retry_transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-retry", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-retry", "status": "completed"}),
            RecordedResponse(200, {"download_url": "https://download.example/job-retry"}),
            RecordedResponse(200, content=_zip_output(**{"result.md": "retry OCR"})),
        ]
    )
    retry = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        route=_verified_test_route(),
        transport=retry_transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    retry_outcome = retry.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local OCR"
    )

    assert retry_outcome.text == "retry OCR"
    assert retry_outcome.ocr_model == SARVAM_OCR_MODEL
    assert [method for method, _, _ in retry_transport.calls] == [
        "POST",
        "GET",
        "GET",
        "GET",
    ]
    assert [row[8] for row in _audit_rows(audit_path)].count("submission") == 2


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
        route=_verified_test_route(),
        transport=first_transport,
        max_poll_attempts=1,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )
    first_result = first.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local"
    )
    assert first_result.text == "local"
    assert first_result.ocr_model == "pytesseract"

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
        route=_verified_test_route(),
        transport=resumed_transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    assert resumed.digitise(b"fixture-png", "page.png", "en-IN", _context()) == "resumed markdown"
    assert [method for method, _, _ in resumed_transport.calls] == ["GET", "GET", "GET"]
    assert "resume" in [row[8] for row in _audit_rows(audit_path)]


def test_completed_job_is_resumed_without_a_new_submission(tmp_path):
    audit_path = tmp_path / "audit.sqlite"
    audit_log = SqliteAuditLog(audit_path)
    completed_transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-completed", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-completed", "status": "completed"}),
            RecordedResponse(
                200, {"download_url": "https://download.example/job-completed"}
            ),
            RecordedResponse(200, content=_zip_output(**{"result.md": "first result"})),
        ]
    )
    completed = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        route=_verified_test_route(),
        transport=completed_transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )
    assert completed.digitise(
        b"fixture-png", "page.png", "en-IN", _context()
    ) == "first result"

    resume_transport = RecordedTransport(
        [
            RecordedResponse(200, {"job_id": "job-completed", "status": "completed"}),
            RecordedResponse(
                200, {"download_url": "https://download.example/job-completed"}
            ),
            RecordedResponse(200, content=_zip_output(**{"result.md": "cached result"})),
        ]
    )
    resumed = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        route=_verified_test_route(),
        transport=resume_transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    result = resumed.digitise(b"fixture-png", "page.png", "en-IN", _context())

    assert result == "cached result"
    assert [method for method, _, _ in resume_transport.calls] == ["GET", "GET", "GET"]


def test_extract_resume_key_is_stable_across_schema_dict_order(tmp_path):
    audit_path = tmp_path / "audit.sqlite"
    audit_log = SqliteAuditLog(audit_path)
    first_transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-schema", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-schema", "status": "running"}),
        ]
    )
    first = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        route=_verified_test_route(),
        transport=first_transport,
        max_poll_attempts=1,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(SarvamPollTimeout):
        first.extract(
            b"fixture-png",
            "page.png",
            "en-IN",
            _context(),
            schema=_wrap({"z_field": {"type": "string", "description": "z"}, "a_field": {"type": "number", "description": "a"}}),
        )

    resumed_transport = RecordedTransport(
        [
            RecordedResponse(200, {"job_id": "job-schema", "status": "completed"}),
            RecordedResponse(200, {"results": [{"a_field": 1, "z_field": "ok"}]}),
        ]
    )
    resumed = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=audit_log,
        route=_verified_test_route(),
        transport=resumed_transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    result = resumed.extract(
        b"fixture-png",
        "page.png",
        "en-IN",
        _context(),
        schema=_wrap({"a_field": {"type": "number", "description": "a"}, "z_field": {"type": "string", "description": "z"}}),
    )

    assert result == {"results": [{"a_field": 1, "z_field": "ok"}]}
    assert [method for method, _, _ in resumed_transport.calls] == ["GET", "GET"]
    assert "resume" in [row[8] for row in _audit_rows(audit_path)]


@pytest.mark.parametrize("changed_parameter", ["language", "schema", "config_id"])
def test_changed_result_defining_parameter_does_not_resume_recorded_job(
    tmp_path, changed_parameter
):
    audit_path = tmp_path / "audit.sqlite"
    audit_log = SqliteAuditLog(audit_path)

    def call(adapter, changed):
        if changed_parameter == "language":
            return adapter.digitise(
                b"fixture-png",
                "page.png",
                "od-IN" if changed else "en-IN",
                _context(),
            )
        if changed_parameter == "schema":
            return adapter.extract(
                b"fixture-png",
                "page.png",
                "en-IN",
                _context(),
                schema=_wrap({"field": {"type": "number" if changed else "string", "description": "f"}}),
            )
        return adapter.extract(
            b"fixture-png",
            "page.png",
            "en-IN",
            _context(),
            config_id="config-new" if changed else "config-old",
        )

    transports = []
    for changed, job_id in ((False, "job-old"), (True, "job-new")):
        transport = RecordedTransport(
            [
                RecordedResponse(202, {"job_id": job_id, "status": "pending"}),
                RecordedResponse(200, {"job_id": job_id, "status": "running"}),
            ]
        )
        transports.append(transport)
        adapter = SarvamVisionAdapter(
            enabled=True,
            api_key="recorded-test-key",
            audit_log=audit_log,
            route=_verified_test_route(),
            transport=transport,
            max_poll_attempts=1,
            poll_interval_seconds=0,
            sleep=lambda _seconds: None,
        )
        with pytest.raises(SarvamPollTimeout):
            call(adapter, changed)

    assert [method for method, _, _ in transports[1].calls] == ["POST", "GET"]
    first_key = transports[0].calls[0][2]["headers"]["Idempotency-Key"]
    changed_key = transports[1].calls[0][2]["headers"]["Idempotency-Key"]
    assert changed_key != first_key


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


def test_idempotency_key_includes_the_actual_uploaded_bytes(tmp_path):
    same_context = _context()
    request = {
        "filename": "page.png",
        "form": {"language": "en-IN", "output_format": "md"},
    }
    adapter = SarvamVisionAdapter(
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
        route=_verified_test_route(),
    )
    assert adapter._idempotency_key(
        same_context, "digitise", b"first", **request
    ) != (
        adapter._idempotency_key(
            same_context, "digitise", b"changed", **request
        )
    )


def test_idempotency_key_canonicalizes_the_complete_request_form(tmp_path):
    base_form = {
        "language": "en-IN",
        "output_format": "json",
        "schema": {"b": {"type": "string"}, "a": {"type": "number"}},
        "future_option": {"beta": True, "modes": ["layout", "tables"]},
    }
    reordered_form = {
        "future_option": {"modes": ["layout", "tables"], "beta": True},
        "schema": {"a": {"type": "number"}, "b": {"type": "string"}},
        "output_format": "json",
        "language": "en-IN",
    }

    adapter = SarvamVisionAdapter(
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
        route=_verified_test_route(),
    )

    def key(form):
        return adapter._idempotency_key(
            _context(),
            "extract",
            b"fixture-png",
            filename="page.png",
            form=form,
        )

    assert key(base_form) == key(reordered_form)
    for changed in (
        {**base_form, "language": "od-IN"},
        {**base_form, "output_format": "csv"},
        {**base_form, "schema": {"a": {"type": "string"}}},
        {**base_form, "config_id": "config-2"},
        {**base_form, "future_option": {"beta": False}},
    ):
        assert key(changed) != key(base_form)


@pytest.mark.parametrize("route_field", ["provider", "model_id", "endpoint"])
def test_idempotency_key_includes_route_identity(tmp_path, route_field):
    route = _verified_test_route()
    original = SarvamVisionAdapter(
        audit_log=SqliteAuditLog(tmp_path / "original.sqlite"),
        route=route,
    )
    changed = SarvamVisionAdapter(
        audit_log=SqliteAuditLog(tmp_path / "changed.sqlite"),
        route=replace(route, **{route_field: f"changed-{route_field}"}),
    )
    request = {
        "filename": "page.png",
        "form": {"language": "en-IN", "output_format": "md"},
    }

    assert original._idempotency_key(
        _context(), "digitise", b"fixture-png", **request
    ) != changed._idempotency_key(
        _context(), "digitise", b"fixture-png", **request
    )


def test_submission_limiter_enforces_ten_requests_per_rolling_minute(tmp_path):
    clock = FakeClock()
    responses = []
    for index in range(11):
        job_id = f"job-{index}"
        responses.extend(
            [
                RecordedResponse(202, {"job_id": job_id, "status": "pending"}),
                RecordedResponse(200, {"job_id": job_id, "status": "failed"}),
            ]
        )
    transport = RecordedTransport(responses)
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
        route=_verified_test_route(),
        transport=transport,
        max_poll_attempts=1,
        poll_interval_seconds=0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    for index in range(11):
        context = SarvamAuditContext(
            ticket=f"T-{index}",
            stage="ocr_extraction",
            document_id=f"doc:{index}",
        )
        outcome = adapter.digitise_or_fallback(
            f"fixture-{index}".encode(),
            f"page-{index}.png",
            "en-IN",
            context,
            lambda: "local OCR",
        )
        assert outcome.ocr_model == "pytesseract"

    assert [method for method, _, _ in transport.calls].count("POST") == 11

    # The 10/min budget is shared between submissions and status polls: Sarvam
    # bills a poll the same as a submission, which is why their guidance is a
    # five-second poll interval. Each of the 11 jobs here is one POST plus one
    # poll, so 22 calls cross the rolling window twice.
    assert [method for method, _, _ in transport.calls].count("GET") == 11
    assert clock.sleeps == [60.0, 60.0]


def test_status_polls_consume_the_shared_rate_limit_budget(tmp_path):
    """A single job's poll loop must not exhaust the quota unmetered.

    Before this was counted, one job polling at the old one-second default
    issued up to 60 requests a minute against a ten-per-minute budget, so the
    first live job would rate-limit itself before a second could be submitted.
    """
    clock = FakeClock()
    responses = [RecordedResponse(202, {"job_id": "job-poll", "status": "pending"})]
    # Nine polls that stay pending, then a terminal one: 1 POST + 10 GETs, so
    # the eleventh call is the one that has to wait for the window.
    responses.extend(
        RecordedResponse(200, {"job_id": "job-poll", "status": "pending"})
        for _ in range(9)
    )
    responses.append(RecordedResponse(200, {"job_id": "job-poll", "status": "failed"}))

    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
        route=_verified_test_route(),
        transport=RecordedTransport(responses),
        max_poll_attempts=10,
        poll_interval_seconds=0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    outcome = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local OCR"
    )

    assert outcome.ocr_model == "pytesseract"
    # 1 submission + 10 polls = 11 metered calls, so the window is hit once
    # even though only one job was ever submitted. The zero-length sleeps are
    # the poll interval itself, set to 0 here to isolate the limiter.
    assert [pause for pause in clock.sleeps if pause] == [60.0]


def test_default_poll_interval_matches_the_documented_guidance():
    """Sarvam documents a five-second poll interval for this rate limit."""
    from janasunani.egress.sarvam import DEFAULT_POLL_INTERVAL_SECONDS

    assert DEFAULT_POLL_INTERVAL_SECONDS == 5.0


def test_429_retries_after_provider_delay_and_can_succeed(tmp_path):
    clock = FakeClock()
    audit_path = tmp_path / "audit.sqlite"
    transport = RecordedTransport(
        [
            RecordedResponse(429, {"error": "rate limited"}, headers={"Retry-After": "2.5"}),
            RecordedResponse(202, {"job_id": "job-retried", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-retried", "status": "completed"}),
            RecordedResponse(200, {"download_url": "https://download.example/job-retried"}),
            RecordedResponse(200, content=_zip_output(**{"result.md": "remote OCR"})),
        ]
    )
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        route=_verified_test_route(),
        transport=transport,
        poll_interval_seconds=0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    outcome = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local OCR"
    )

    assert outcome.text == "remote OCR"
    assert outcome.ocr_model == SARVAM_OCR_MODEL
    assert [method for method, _, _ in transport.calls] == [
        "POST",
        "POST",
        "GET",
        "GET",
        "GET",
    ]
    assert clock.sleeps == [2.5]
    assert [row[8] for row in _audit_rows(audit_path)] == [
        "submission_rate_limited",
        "submission",
        "poll",
        "result_lookup",
        "download",
    ]
    assert [row[4] for row in _audit_rows(audit_path)][:2] == [
        len(b"fixture-png"),
        len(b"fixture-png"),
    ]


def test_429_exhaustion_falls_back_after_bounded_backoff(tmp_path):
    clock = FakeClock()
    audit_path = tmp_path / "audit.sqlite"
    transport = RecordedTransport(
        [
            RecordedResponse(429, {"error": "rate limited"}),
            RecordedResponse(429, {"error": "rate limited"}),
            RecordedResponse(429, {"error": "rate limited"}),
        ]
    )
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        route=_verified_test_route(),
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        submission_backoff_seconds=6.0,
    )

    outcome = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local OCR"
    )

    assert outcome.text == "local OCR"
    assert outcome.ocr_model == "pytesseract"
    assert [method for method, _, _ in transport.calls] == ["POST", "POST", "POST"]
    assert clock.sleeps == [6.0, 12.0]
    assert [row[8] for row in _audit_rows(audit_path)] == [
        "submission_rate_limited",
        "submission_rate_limited",
        "submission_rate_limit_exhausted",
        "fallback",
    ]
    assert len(set(_audit_keys(audit_path))) == 1


def test_failed_submission_is_audited_with_only_its_actual_upload_bytes(tmp_path):
    audit_path = tmp_path / "audit.sqlite"
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        route=_verified_test_route(),
        transport=RecordedTransport([RecordedResponse(503, {"error": "unavailable"})]),
    )

    outcome = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local"
    )
    assert outcome.text == "local"
    assert outcome.ocr_model == "pytesseract"
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


def test_adapter_reads_sarvam_key_from_dotenv_only(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SARVAM_API_KEY=dotenv-only-key\n")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.delenv("SARVAM_API_SUBSCRIPTION_KEY", raising=False)
    monkeypatch.setattr(
        "janasunani.egress.sarvam.Settings",
        lambda: Settings(_env_file=env_file),
    )
    adapter = SarvamVisionAdapter(
        enabled=False,
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
    )
    assert adapter.api_key == "dotenv-only-key"


def test_falls_back_to_legacy_subscription_key(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SARVAM_API_SUBSCRIPTION_KEY=legacy-only\n")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.delenv("SARVAM_API_SUBSCRIPTION_KEY", raising=False)
    monkeypatch.setattr(
        "janasunani.egress.sarvam.Settings",
        lambda: Settings(_env_file=env_file),
    )
    adapter = SarvamVisionAdapter(
        enabled=False,
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
    )
    assert adapter.api_key == "legacy-only"


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
    assert route.data_class == "raw grievance document bytes, including citizen PII"
    assert route.endpoint == "https://api.sarvam.ai"
    assert route.fallback == "pytesseract"
    assert route.authorization_reference == AUTHORIZATION_REFERENCE
    assert route.retention_terms.statement
    assert route.encryption_in_transit.statement
    assert route.encryption_at_rest.statement
    assert route.audit_policy
    assert route.declared_controls_complete is True
    assert route.unverified_controls == (
        "retention_terms",
        "encryption_in_transit",
        "encryption_at_rest",
    )
    assert route.live_use_ready is False


def test_unverified_provider_controls_gate_enabled_route_without_remote_call(tmp_path):
    """Unverified controls and no accepted risk must still block every call.

    This is the invariant the accepted-risk field must not weaken: an
    acceptance is an explicit, named decision, and its absence has to keep
    failing closed.
    """
    audit_path = tmp_path / "audit.sqlite"
    transport = RecordedTransport([])
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        transport=transport,
        route=replace(PROVIDER_REGISTRY["sarvam-vision"], accepted_risk=None),
    )

    outcome = adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local OCR"
    )

    assert outcome.text == "local OCR"
    assert outcome.ocr_model == "pytesseract"
    assert transport.calls == []
    with sqlite3.connect(audit_path) as connection:
        event, metadata = connection.execute(
            "SELECT event, response_metadata FROM authorized_external_audit"
        ).fetchone()
    assert event == "fallback"
    assert json.loads(metadata)["reason"] == "SarvamGovernanceError"


def test_accepted_risk_permits_egress_without_claiming_verification():
    """An acceptance unblocks the call and never rewrites the control state.

    The registry is what a reviewer reads. Recording someone's signature as
    though it were evidence about Sarvam's retention would be a false
    statement about a third party, so `verified` and `unverified_controls`
    must be unmoved by it.
    """
    route = PROVIDER_REGISTRY["sarvam-vision"]

    assert route.accepted_risk is not None
    assert route.egress_permitted is True
    assert route.egress_basis == "accepted_risk"

    # Still not verified, and still reported as such.
    assert route.live_use_ready is False
    assert route.unverified_controls == (
        "retention_terms",
        "encryption_in_transit",
        "encryption_at_rest",
    )
    assert route.retention_terms.verified is False


def test_accepted_risk_is_named_in_the_audit_authorization(tmp_path):
    """The audit row must say a call went out on acceptance, not verification."""
    audit_path = tmp_path / "audit.sqlite"
    transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-accepted", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-accepted", "status": "completed"}),
            RecordedResponse(
                200, {"download_url": "https://example.invalid/out.zip"}
            ),
            RecordedResponse(200, _zip_output(**{"page-1.md": "recognised text"})),
        ]
    )
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        transport=transport,
        poll_interval_seconds=0,
    )

    adapter.digitise_or_fallback(
        b"fixture-png", "page.png", "en-IN", _context(), lambda: "local OCR"
    )

    with sqlite3.connect(audit_path) as connection:
        references = [
            row[0]
            for row in connection.execute(
                "SELECT authorization_reference FROM authorized_external_audit"
            )
        ]
    assert references
    for reference in references:
        assert "risk accepted by" in reference
        assert "Additional Chief Secretary" in reference
        # A reader must be able to see which controls were unverified.
        assert "retention_terms" in reference


def test_verified_route_audit_does_not_mention_accepted_risk(tmp_path):
    """When controls are genuinely verified, the acceptance wording is absent."""
    audit_path = tmp_path / "audit.sqlite"
    transport = RecordedTransport([])
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(audit_path),
        transport=transport,
        route=_verified_test_route(),
    )

    assert adapter.route.egress_basis == "verified_controls"
    assert adapter._authorization_reference() == adapter.route.authorization_reference


def test_extract_submission_omits_an_unsupported_model_field(tmp_path):
    transport = RecordedTransport(
        [
            RecordedResponse(202, {"job_id": "job-extract", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-extract", "status": "completed"}),
            RecordedResponse(200, {"results": [{"complainant_name": "Example"}]}),
        ]
    )
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(tmp_path / "audit.sqlite"),
        route=_verified_test_route(),
        transport=transport,
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    result = adapter.extract(
        b"fixture-png",
        "page.png",
        "en-IN",
        _context(),
        schema=_wrap({"complainant_name": {"type": "string", "description": "name"}}),
    )

    assert result == {"results": [{"complainant_name": "Example"}]}
    submission_data = transport.calls[0][2]["data"]
    assert submission_data == {
        "language": "en-IN",
        "output_format": "json",
        "schema": '{"properties":{"complainant_name":{"description":"name","type":"string"}},"type":"object"}',
    }
    assert "model" not in submission_data


def _process_and_persist_stage_page(
    *, stage, pytesseract_backend, adapter, db_path, monkeypatch, ticket
):
    from PIL import Image

    from janasunani.pipeline.db import initialize_database

    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO pages "
            "(doc_id, page_number, full_path, page_id, ticket_number) "
            "VALUES ('doc-1', 1, 'page.png', 'page-1', ?)",
            (ticket,),
        )

    monkeypatch.setattr(stage, "render_page", lambda _path, _number: Image.new("RGB", (2, 2)))
    monkeypatch.setattr(
        pytesseract_backend,
        "extract_text",
        lambda _image, force_lang=None: "local OCR",
    )
    monkeypatch.setattr(stage, "_worker_backend", "sarvam")
    monkeypatch.setattr(stage, "_worker_sarvam", adapter)

    result = stage._process_page(
        {
            "page_id": "page-1",
            "doc_id": "doc-1",
            "page_number": 1,
            "file_path": str(db_path.parent / "page.png"),
            "language": "Odia",
            "ticket": ticket,
        }
    )
    with sqlite3.connect(db_path) as connection:
        assert stage._write_results_with_retry(
            connection, [result], "2026-08-07", "sarvam"
        ) == 1
        persisted = connection.execute(
            "SELECT extracted_text, ocr_model FROM pages WHERE page_id = 'page-1'"
        ).fetchone()
    return result, persisted


@pytest.mark.parametrize(
    ("mode", "ticket", "expected_events"),
    [
        ("disabled", "T-1", ["disabled"]),
        ("missing_ticket", None, []),
        ("missing_credentials", "T-1", ["fallback"]),
        ("request_error", "T-1", ["submission_error", "fallback"]),
        ("timeout", "T-1", ["submission", "poll", "fallback"]),
    ],
)
def test_sarvam_stage_fallback_persists_pytesseract_per_page(
    tmp_path, monkeypatch, mode, ticket, expected_events
):
    # Base CI deliberately excludes the heavy pipeline OCR stack.  Keep this
    # integration assertion in the adapter module, but skip only this test
    # there; the pipeline-core job exercises the real pytesseract fallback.
    pytest.importorskip("pdf2image")

    from janasunani.pipeline.stages.ocr_extraction import stage
    from janasunani.pipeline.stages.ocr_extraction import pytesseract_backend

    db_path = tmp_path / "pipeline.sqlite"
    responses = {
        "request_error": [RecordedResponse(503, {"error": "unavailable"})],
        "timeout": [
            RecordedResponse(202, {"job_id": "job-stuck", "status": "pending"}),
            RecordedResponse(200, {"job_id": "job-stuck", "status": "running"}),
        ],
    }.get(mode, [])
    adapter = SarvamVisionAdapter(
        enabled=mode != "disabled",
        api_key="" if mode == "missing_credentials" else "recorded-test-key",
        audit_log=SqliteAuditLog(db_path),
        route=_verified_test_route(),
        transport=RecordedTransport(responses),
        poll_interval_seconds=0,
        max_poll_attempts=1,
        sleep=lambda _seconds: None,
    )

    result, persisted = _process_and_persist_stage_page(
        stage=stage,
        pytesseract_backend=pytesseract_backend,
        adapter=adapter,
        db_path=db_path,
        monkeypatch=monkeypatch,
        ticket=ticket,
    )

    assert result["text"] == "local OCR"
    assert result["ocr_model"] == "pytesseract"
    assert result["error"] is None
    assert persisted == ("local OCR", "pytesseract")
    assert [row[8] for row in _audit_rows(db_path)] == expected_events


def test_sarvam_stage_success_persists_remote_route_and_model(tmp_path, monkeypatch):
    pytest.importorskip("pdf2image")

    from janasunani.pipeline.stages.ocr_extraction import stage
    from janasunani.pipeline.stages.ocr_extraction import pytesseract_backend

    db_path = tmp_path / "pipeline.sqlite"
    adapter = SarvamVisionAdapter(
        enabled=True,
        api_key="recorded-test-key",
        audit_log=SqliteAuditLog(db_path),
        route=_verified_test_route(),
        transport=RecordedTransport(
            [
                RecordedResponse(202, {"job_id": "job-ok", "status": "pending"}),
                RecordedResponse(200, {"job_id": "job-ok", "status": "completed"}),
                RecordedResponse(200, {"download_url": "https://download.example/job-ok"}),
                RecordedResponse(200, content=_zip_output(**{"result.md": "remote OCR"})),
            ]
        ),
        poll_interval_seconds=0,
        sleep=lambda _seconds: None,
    )

    result, persisted = _process_and_persist_stage_page(
        stage=stage,
        pytesseract_backend=pytesseract_backend,
        adapter=adapter,
        db_path=db_path,
        monkeypatch=monkeypatch,
        ticket="T-1",
    )

    assert result["text"] == "remote OCR"
    assert result["ocr_model"] == SARVAM_OCR_MODEL
    assert result["error"] is None
    assert persisted == ("remote OCR", SARVAM_OCR_MODEL)
