"""The sole authorized-external client for Sarvam document intelligence.

The pipeline normally uses pytesseract on the same host.  Hosted Sarvam is an
explicit, disabled-by-default exception: this module is the only place that
may send document bytes to it, and records the approval relied upon before a
submitted asynchronous job can be polled.

This module deliberately has no credential default and does not create an HTTP
client until an enabled adapter is used.  Tests supply recorded transports, so
they never make a live network call.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zipfile
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from janasunani.config import Settings

API_BASE_URL = "https://api.sarvam.ai"
PROVIDER_ID = "sarvam-hosted"
# The hosted job API chooses the Document AI model; it does not accept a
# caller-selected model multipart field.  Record the reviewed provider model
# locally instead of implying a request parameter that does not exist.
MODEL_ID = "Sarvam Vision 1.5"
SARVAM_OCR_MODEL = f"{PROVIDER_ID}:{MODEL_ID}"
TRUST_TIER = "authorized-external"

# This is documentation of the settled authorization, not a runtime approval
# gate.  Keeping the reference beside the route makes the boundary reviewable.
AUTHORIZATION_REFERENCE = "GoO-Sarvam MoU; ACS Vishal Dev (IT) sign-off"

TERMINAL_STATUSES = frozenset({"completed", "partially_completed", "failed", "rejected"})
VISION_SUBMISSIONS_PER_MINUTE = 10
SUBMISSION_WINDOW_SECONDS = 60.0

# Sarvam documents 10 req/min for Document Intelligence, uniform across the
# Starter, Pro and Business plans -- upgrading does not raise it -- and status
# polls are billed against that same budget. Their guidance is an explicit
# five-second poll interval. A one-second default polls a single job at up to
# 60 req/min, so the first live job would rate-limit itself before any second
# job could be submitted. Recorded-transport tests pass an explicit smaller
# value, so this default only governs real calls.
# https://docs.sarvam.ai/api/getting-started/ratelimits
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
MAX_SUBMISSION_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 60.0


@dataclass(frozen=True)
class GovernanceControl:
    """A provider control statement and whether repo evidence verifies it."""

    statement: str
    verified: bool


@dataclass(frozen=True)
class RiskAcceptance:
    """A named authority knowingly accepting unverified provider controls.

    This is deliberately not the same thing as verification, and setting one
    must never flip a control to ``verified``. Verification is a claim about
    what the provider does with the data once it has it; acceptance is a claim
    about who decided we may send it anyway, and on what basis. Recording an
    acceptance as a verification would put a false statement about a third
    party into the registry, and the registry is the artifact a reviewer reads.

    ``unverified_controls`` therefore keeps reporting every unverified control
    even when an acceptance is present, and the audit log records which of the
    two bases permitted each call.
    """

    authority: str
    basis: str
    scope: str
    accepted_on: str


@dataclass(frozen=True)
class ProviderRoute:
    """The registered, reviewable declaration for a provider route."""

    name: str
    provider: str
    model_id: str
    endpoint: str
    trust_tier: str
    data_class: str
    authorization_reference: str
    retention_terms: GovernanceControl
    encryption_in_transit: GovernanceControl
    encryption_at_rest: GovernanceControl
    audit_policy: str
    fallback: str
    accepted_risk: RiskAcceptance | None = None

    @property
    def unverified_controls(self) -> tuple[str, ...]:
        controls = {
            "retention_terms": self.retention_terms,
            "encryption_in_transit": self.encryption_in_transit,
            "encryption_at_rest": self.encryption_at_rest,
        }
        return tuple(name for name, control in controls.items() if not control.verified)

    @property
    def declared_controls_complete(self) -> bool:
        """Whether the architecture-mandated registry fields are all declared."""
        required_text = (
            self.trust_tier,
            self.data_class,
            self.endpoint,
            self.authorization_reference,
            self.retention_terms.statement,
            self.encryption_in_transit.statement,
            self.encryption_at_rest.statement,
            self.audit_policy,
            self.fallback,
        )
        return all(value.strip() for value in required_text)

    @property
    def live_use_ready(self) -> bool:
        """Whether every provider-held-data control has verified repo evidence.

        Unchanged by an accepted risk, on purpose. This property answers "is
        the provider's handling of our data verified", and an acceptance does
        not make it so. Callers deciding whether a call may proceed want
        :attr:`egress_permitted`.
        """
        return self.declared_controls_complete and not self.unverified_controls

    @property
    def egress_permitted(self) -> bool:
        """Whether a live call may proceed, by verification or by acceptance."""
        if not self.declared_controls_complete:
            return False
        return not self.unverified_controls or self.accepted_risk is not None

    @property
    def egress_basis(self) -> str:
        """Which of the two bases permits egress, for the audit record."""
        if self.live_use_ready:
            return "verified_controls"
        if self.egress_permitted:
            return "accepted_risk"
        return "blocked"


PROVIDER_REGISTRY = {
    "sarvam-vision": ProviderRoute(
        name="sarvam-vision",
        provider=PROVIDER_ID,
        model_id=MODEL_ID,
        endpoint=API_BASE_URL,
        trust_tier=TRUST_TIER,
        data_class="raw grievance document bytes, including citizen PII",
        authorization_reference=AUTHORIZATION_REFERENCE,
        retention_terms=GovernanceControl(
            statement="Sarvam provider retention terms are not verified in this repository.",
            verified=False,
        ),
        encryption_in_transit=GovernanceControl(
            statement="Sarvam transport-encryption guarantees are not verified in this repository.",
            verified=False,
        ),
        encryption_at_rest=GovernanceControl(
            statement="Sarvam at-rest encryption guarantees are not verified in this repository.",
            verified=False,
        ),
        audit_policy=(
            "Append one local record for every remote attempt and fallback with ticket, stage, "
            "document, provider, model, bytes sent, timestamp, authorization reference, "
            "operation, event, language, request key, job id, and safe response metadata."
        ),
        fallback="pytesseract",
        # The three controls above stay `verified=False` because they are still
        # not verified: Sarvam publishes no retention or residency commitment,
        # and their documentation says these vary by plan and must be settled
        # with an account representative. What exists is authorization, not
        # verification, and the two are recorded separately on purpose.
        accepted_risk=RiskAcceptance(
            authority=(
                "Additional Chief Secretary, Electronics & IT Department, "
                "Government of Odisha"
            ),
            basis=(
                "Sign-off that citizen PII may be remitted to Sarvam. No state "
                "statute currently governs this transfer, and the residual risk "
                "was judged acceptable on that basis. Provider retention and "
                "encryption terms remain unverified."
            ),
            scope=(
                "Sarvam Vision document digitisation and extraction for the "
                "grievance corpus."
            ),
            accepted_on="2026-08-07",
        ),
    )
}


class SarvamError(RuntimeError):
    """A remote Sarvam request could not yield a usable result."""


class SarvamDisabled(SarvamError):
    """Raised internally when the configured kill switch blocks submission."""


class SarvamPollTimeout(SarvamError):
    """The job did not reach a terminal state within the bounded poll loop."""


class SarvamGovernanceError(SarvamError):
    """The external route lacks verified provider-held-data controls."""


#: Sarvam Extract caps schema nesting at this depth.
MAX_EXTRACT_SCHEMA_DEPTH = 4


def _validate_extract_schema(schema: dict[str, Any]) -> None:
    """Reject a schema Sarvam would answer with HTTP 400.

    The provider requires a whole JSON Schema document, not the bare field
    map. Handing it the field map fails at submission on every page, and the
    audit row records only ``SarvamError``, so the cause is invisible in the
    log. Every test injects a fake transport and cannot see a 400 either.
    This guard is what turns that silent, uniform failure into an error that
    names the problem before any bytes leave the box.

    Raises ``ValueError`` (a caller bug, not a remote failure), so it is not
    swallowed by the fallback-to-pytesseract path.
    """
    if schema.get("type") != "object":
        raise ValueError(
            "Sarvam Extract schema root must be {'type': 'object', ...}; got "
            f"type={schema.get('type')!r}. A bare field map is rejected with "
            "HTTP 400 — wrap it as {'type': 'object', 'properties': {...}}."
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError(
            "Sarvam Extract schema needs a non-empty 'properties' map; got "
            f"{properties!r}."
        )
    for name, field in properties.items():
        if not isinstance(field, dict):
            raise ValueError(f"Extract schema field {name!r} must be an object.")
        if not field.get("type"):
            raise ValueError(f"Extract schema field {name!r} needs a 'type'.")
        if not str(field.get("description") or "").strip():
            raise ValueError(
                f"Extract schema field {name!r} needs a non-empty 'description'. "
                "The description is what steers the model, so an empty one "
                "silently degrades extraction quality."
            )

    def depth(node: object, level: int = 1) -> int:
        if not isinstance(node, dict):
            return level
        nested = node.get("properties")
        if not isinstance(nested, dict) or not nested:
            return level
        return max(depth(child, level + 1) for child in nested.values())

    found = depth(schema)
    if found > MAX_EXTRACT_SCHEMA_DEPTH:
        raise ValueError(
            f"Extract schema nests {found} levels; Sarvam caps it at "
            f"{MAX_EXTRACT_SCHEMA_DEPTH}."
        )


@dataclass(frozen=True)
class SarvamAuditContext:
    """Identity required to make a document egress call auditable."""

    ticket: str
    stage: str
    document_id: str

    def __post_init__(self) -> None:
        if not self.ticket.strip() or not self.stage.strip() or not self.document_id.strip():
            raise ValueError("ticket, stage, and document_id are required for Sarvam egress")


@dataclass(frozen=True)
class DigitiseOutcome:
    """Digitise text paired with the OCR engine that actually produced it."""

    text: str
    ocr_model: str


@dataclass(frozen=True)
class AuditRecord:
    ticket: str
    stage: str
    document_id: str
    provider: str
    model_id: str
    bytes_sent: int
    timestamp: str
    authorization_reference: str
    operation: str
    event: str
    language: str
    idempotency_key: str
    job_id: str | None = None
    response_metadata: dict[str, Any] | None = None


class AuditLog(Protocol):
    def append(self, record: AuditRecord) -> None: ...

    def submitted_job(
        self, context: SarvamAuditContext, operation: str, idempotency_key: str
    ) -> str | None: ...


class HttpResponse(Protocol):
    status_code: int
    text: str
    content: bytes
    headers: Any

    def json(self) -> Any: ...


class HttpTransport(Protocol):
    def post(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def get(self, url: str, **kwargs: Any) -> HttpResponse: ...


class SqliteAuditLog:
    """Append-only audit records stored beside pipeline state.

    The payload is JSON rather than a collection of optional columns so result
    metadata can grow without silently dropping vendor fields.  Mandatory
    governance fields remain indexed columns for review and reconciliation.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authorized_external_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    bytes_sent INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    authorization_reference TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    event TEXT NOT NULL,
                    language TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    job_id TEXT,
                    response_metadata TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_authorized_external_audit_job
                ON authorized_external_audit(job_id)
                """
            )

    def append(self, record: AuditRecord) -> None:
        payload = asdict(record)
        payload["response_metadata"] = json.dumps(
            payload["response_metadata"], sort_keys=True
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO authorized_external_audit (
                    ticket, stage, document_id, provider, model_id, bytes_sent,
                    timestamp, authorization_reference, operation, event, language,
                    idempotency_key, job_id, response_metadata
                ) VALUES (
                    :ticket, :stage, :document_id, :provider, :model_id, :bytes_sent,
                    :timestamp, :authorization_reference, :operation, :event, :language,
                    :idempotency_key, :job_id, :response_metadata
                )
                """,
                payload,
            )

    def submitted_job(
        self, context: SarvamAuditContext, operation: str, idempotency_key: str
    ) -> str | None:
        """Return a resumable recorded job before issuing a billable retry.

        Sarvam's Idempotency-Key semantics are undocumented.  This local log,
        not that header, is therefore the duplicate-billing control.  A
        completed job is also returned: its result can be downloaded again
        without submitting the same document a second time.  Terminal
        unsuccessful jobs are deliberately excluded so a later call may make
        a fresh submission.
        """
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT job_id
                FROM authorized_external_audit
                WHERE ticket = ? AND stage = ? AND document_id = ?
                  AND operation = ? AND event = 'submission'
                  AND idempotency_key = ? AND job_id IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (
                    context.ticket,
                    context.stage,
                    context.document_id,
                    operation,
                    idempotency_key,
                ),
            ).fetchone()
            if not row:
                return None
            job_id = row[0]
            metadata_rows = connection.execute(
                """
                SELECT response_metadata
                FROM authorized_external_audit
                WHERE job_id = ? AND idempotency_key = ?
                ORDER BY id DESC
                """,
                (job_id, idempotency_key),
            ).fetchall()

        for (raw_metadata,) in metadata_rows:
            if not raw_metadata:
                continue
            try:
                status = json.loads(raw_metadata).get("status")
            except (AttributeError, json.JSONDecodeError):
                continue
            if status in {"failed", "rejected", "partially_completed"}:
                return None
            if status:
                return job_id
        return job_id


class SarvamDocumentProvider(Protocol):
    """Two-operation interface exposed by Sarvam Document Intelligence."""

    def digitise(
        self,
        document_bytes: bytes,
        filename: str,
        language: str,
        context: SarvamAuditContext,
    ) -> str: ...

    def extract(
        self,
        document_bytes: bytes,
        filename: str,
        language: str,
        context: SarvamAuditContext,
        *,
        schema: dict[str, Any] | None = None,
        config_id: str | None = None,
    ) -> dict[str, Any]: ...


_Result = TypeVar("_Result")


class SarvamVisionAdapter:
    """Submit/poll Sarvam jobs and route safe failures to a local counterpart."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        api_key: str | None = None,
        audit_log: AuditLog,
        route: ProviderRoute | None = None,
        transport: HttpTransport | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_poll_attempts: int = 60,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        max_submission_attempts: int = MAX_SUBMISSION_ATTEMPTS,
        submission_backoff_seconds: float = 6.0,
    ) -> None:
        if max_submission_attempts < 1:
            raise ValueError("max_submission_attempts must be at least 1")
        if submission_backoff_seconds < 0:
            raise ValueError("submission_backoff_seconds must be non-negative")
        self.enabled = enabled
        # Centralized via janasunani/config.Settings: single source for the
        # hosted key (env var or .env). Fresh Settings() so monkeypatched env
        # in tests is visible; singleton `settings` is frozen at import time.
        if api_key is not None:
            self.api_key = api_key
        else:
            live = Settings()
            self.api_key = live.SARVAM_API_KEY or live.SARVAM_API_SUBSCRIPTION_KEY
        self.audit_log = audit_log
        self.route = route or PROVIDER_REGISTRY["sarvam-vision"]
        self.transport = transport
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts
        self.sleep = sleep
        self.monotonic = monotonic
        self.max_submission_attempts = max_submission_attempts
        self.submission_backoff_seconds = submission_backoff_seconds
        self._submission_times: deque[float] = deque()

    def digitise(
        self,
        document_bytes: bytes,
        filename: str,
        language: str,
        context: SarvamAuditContext,
    ) -> str:
        """Run the OCR/Digitise endpoint and return its Markdown output."""
        form = self._digitise_form(language)
        return self._run_job(
            operation="digitise",
            document_bytes=document_bytes,
            filename=filename,
            language=language,
            context=context,
            form=form,
            result_reader=self._read_digitise_result,
        )

    def extract(
        self,
        document_bytes: bytes,
        filename: str,
        language: str,
        context: SarvamAuditContext,
        *,
        schema: dict[str, Any] | None = None,
        config_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the separate structured Extract endpoint.

        A benchmark pins exactly one of ``schema`` or ``config_id``.  The
        operation is intentionally separate from digitise: they differ in
        task, result shape, and billing.
        """
        if (schema is None) == (config_id is None):
            raise ValueError("provide exactly one of schema or config_id for Sarvam Extract")
        form: dict[str, Any] = {"language": language, "output_format": "json"}
        if schema is not None:
            _validate_extract_schema(schema)
            form["schema"] = json.dumps(
                schema,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        else:
            form["config_id"] = config_id
        return self._run_job(
            operation="extract",
            document_bytes=document_bytes,
            filename=filename,
            language=language,
            context=context,
            form=form,
            result_reader=self._read_extract_result,
        )

    def digitise_or_fallback(
        self,
        document_bytes: bytes,
        filename: str,
        language: str,
        context: SarvamAuditContext,
        fallback: Callable[[], str],
    ) -> DigitiseOutcome:
        """Use Digitise only while enabled and report the actual OCR engine.

        Disabled means no remote request of any kind, including a resume/poll.
        Enabled runs may resume a recorded job rather than resubmitting it.
        """
        form = self._digitise_form(language)
        idempotency_key = self._idempotency_key(
            context,
            "digitise",
            document_bytes,
            filename=filename,
            form=form,
        )
        if not self.enabled:
            self._audit(
                context,
                "digitise",
                "disabled",
                language,
                0,
                None,
                {},
                idempotency_key,
            )
            return DigitiseOutcome(text=fallback(), ocr_model="pytesseract")
        try:
            return DigitiseOutcome(
                text=self._run_job(
                    operation="digitise",
                    document_bytes=document_bytes,
                    filename=filename,
                    language=language,
                    context=context,
                    form=form,
                    result_reader=self._read_digitise_result,
                ),
                ocr_model=f"{self.route.provider}:{self.route.model_id}",
            )
        except SarvamError as exc:
            self._audit(
                context,
                "digitise",
                "fallback",
                language,
                0,
                None,
                {"reason": type(exc).__name__},
                idempotency_key,
            )
            return DigitiseOutcome(text=fallback(), ocr_model="pytesseract")

    # ------------------------------------------------------------------
    # Transliteration probe — romanized Odia ↔ Odia script (Phase 17)
    # ------------------------------------------------------------------
    # Sarvam's transliteration surface is a synchronous text API (Roman ↔
    # native script, bidirectional, Odia is od-IN, 1 000 chars per
    # request) — distinct from the async Vision job API above. It is still
    # authorized-external (same MoU, same audit log, same kill switch), and
    # is the probe that lets romanized-Odia grievances be hashed/indexed in
    # the same script as native Odia. The chunking and per-chunk audit
    # follow the provider's documented limit; the fallback is identity
    # (dpic-infra IndicXlit would be the production counterpart, but
    # returning the original text keeps the contract fallback-safe).
    TRANSLITERATION_MAX_CHARS = 1000

    def transliterate(
        self,
        text: str,
        source_language_code: str,
        target_language_code: str,
        context: SarvamAuditContext,
    ) -> str:
        """Transliterate via Sarvam text API with chunking at 1000 chars.

        One audit row per chunk. Raises SarvamDisabled / SarvamGovernanceError
        / SarvamError like the Vision path; callers that need a fallback
        should use transliterate_or_fallback.
        """
        if not self.enabled:
            raise SarvamDisabled("Sarvam hosted egress is disabled")
        if not self.route.egress_permitted:
            controls = ", ".join(self.route.unverified_controls)
            raise SarvamGovernanceError(
                f"Sarvam hosted egress is gated by unverified controls: {controls}"
            )
        if not self.api_key:
            raise SarvamError(
                "SARVAM_API_KEY is required when Sarvam is enabled "
                "(SARVAM_API_SUBSCRIPTION_KEY is accepted for compatibility)"
            )
        if not text:
            return ""
        # Chunk at the provider's documented limit — Indic→Indic is not
        # supported, so callers must ensure source/target are valid.
        chunks = [
            text[i : i + self.TRANSLITERATION_MAX_CHARS]
            for i in range(0, len(text), self.TRANSLITERATION_MAX_CHARS)
        ]
        results: list[str] = []
        for idx, chunk in enumerate(chunks):
            # Idempotency key per chunk: same derivation as Vision, but with
            # the transliteration form (+ chunk index for chunking stability).
            form: dict[str, Any] = {
                "input": chunk,
                "source_language_code": source_language_code,
                "target_language_code": target_language_code,
                "chunk_index": idx,
                "chunk_count": len(chunks),
            }
            idempotency_key = self._idempotency_key(
                context,
                "transliterate",
                chunk.encode("utf-8"),
                filename=f"transliterate-chunk-{idx}",
                form=form,
            )
            chunk_bytes = len(chunk.encode("utf-8"))
            payload = self._post_transliterate(
                chunk=chunk,
                source_language_code=source_language_code,
                target_language_code=target_language_code,
                context=context,
                idempotency_key=idempotency_key,
                bytes_sent=chunk_bytes,
            )
            # Sarvam returns {"transliterated_text": "..."} or {"output": "..."}
            transliterated = (
                payload.get("transliterated_text")
                or payload.get("output")
                or payload.get("text")
                or ""
            )
            if not isinstance(transliterated, str):
                raise SarvamError("Sarvam transliteration response missing text")
            results.append(transliterated)
        return "".join(results)

    def transliterate_or_fallback(
        self,
        text: str,
        source_language_code: str,
        target_language_code: str,
        context: SarvamAuditContext,
        fallback: Callable[[str], str],
    ) -> str:
        """Transliterate if enabled, else return fallback (dpic-infra).

        Disabled path logs one audit row (bytes 0, event disabled) and
        returns fallback(text) without any network call. Enabled path that
        raises logs fallback and returns fallback(text). The fallback is the
        kill-switch contract: every authorized-external call has a
        maintained dpic-infra counterpart.
        """
        # Use a single idempotency key for the disabled audit — consistent
        # with digitise_or_fallback's single audit row for disabled.
        form: dict[str, Any] = {
            "input": text[: self.TRANSLITERATION_MAX_CHARS],
            "source_language_code": source_language_code,
            "target_language_code": target_language_code,
            "chunk_index": 0,
            "chunk_count": 1,
        }
        # For disabled we don't need chunk-accurate bytes; 0 signals no egress.
        idempotency_key = self._idempotency_key(
            context,
            "transliterate",
            text.encode("utf-8"),
            filename="transliterate",
            form=form,
        )
        if not self.enabled:
            self._audit(
                context,
                "transliterate",
                "disabled",
                target_language_code,
                0,
                None,
                {},
                idempotency_key,
            )
            return fallback(text)
        try:
            return self.transliterate(
                text, source_language_code, target_language_code, context
            )
        except SarvamError as exc:
            self._audit(
                context,
                "transliterate",
                "fallback",
                target_language_code,
                0,
                None,
                {"reason": type(exc).__name__},
                idempotency_key,
            )
            return fallback(text)

    def _post_transliterate(
        self,
        *,
        chunk: str,
        source_language_code: str,
        target_language_code: str,
        context: SarvamAuditContext,
        idempotency_key: str,
        bytes_sent: int,
    ) -> dict[str, Any]:
        self._wait_for_submission_slot()
        try:
            response = self._transport().post(
                f"{self.route.endpoint}/transliterate",
                headers={
                    "api-subscription-key": self.api_key,
                    "Idempotency-Key": idempotency_key,
                },
                json={
                    "input": chunk,
                    "source_language_code": source_language_code,
                    "target_language_code": target_language_code,
                },
            )
        except Exception as exc:
            self._audit(
                context,
                "transliterate",
                "transliterate_error",
                target_language_code,
                bytes_sent,
                None,
                {"error": type(exc).__name__},
                idempotency_key,
            )
            if isinstance(exc, SarvamError):
                raise
            raise SarvamError("Sarvam transliterate request failed") from exc
        try:
            payload = self._json_response(response, "transliterate")
        except Exception as exc:
            self._audit(
                context,
                "transliterate",
                "transliterate_error",
                target_language_code,
                bytes_sent,
                None,
                {"error": type(exc).__name__},
                idempotency_key,
            )
            if isinstance(exc, SarvamError):
                raise
            raise SarvamError("Sarvam transliterate request failed") from exc
        self._audit(
            context,
            "transliterate",
            "transliterate",
            target_language_code,
            bytes_sent,
            None,
            self._response_metadata(payload, response.status_code),
            idempotency_key,
        )
        return payload

    def _run_job(
        self,
        *,
        operation: str,
        document_bytes: bytes,
        filename: str,
        language: str,
        context: SarvamAuditContext,
        form: dict[str, Any],
        result_reader: Callable[
            [str, dict[str, Any], SarvamAuditContext, str, str, str], _Result
        ],
    ) -> _Result:
        if not self.enabled:
            raise SarvamDisabled("Sarvam hosted egress is disabled")
        if not self.route.egress_permitted:
            controls = ", ".join(self.route.unverified_controls)
            raise SarvamGovernanceError(
                f"Sarvam hosted egress is gated by unverified controls: {controls}"
            )
        if not self.api_key:
            raise SarvamError(
                "SARVAM_API_KEY is required when Sarvam is enabled "
                "(SARVAM_API_SUBSCRIPTION_KEY is accepted for compatibility)"
            )

        idempotency_key = self._idempotency_key(
            context,
            operation,
            document_bytes,
            filename=filename,
            form=form,
        )
        recorded_job = self.audit_log.submitted_job(context, operation, idempotency_key)
        if recorded_job:
            self._audit(
                context,
                operation,
                "resume",
                language,
                0,
                recorded_job,
                {},
                idempotency_key,
            )
            return self._finish_job(
                recorded_job,
                context,
                operation,
                language,
                idempotency_key,
                result_reader,
            )
        submitted = self._post_json(
            url=f"{self.route.endpoint}/doc-ai/v1/job/{operation}",
            headers={
                "api-subscription-key": self.api_key,
                "Idempotency-Key": idempotency_key,
            },
            files={"file": (filename, document_bytes)},
            data=form,
            label="submission",
            context=context,
            operation=operation,
            language=language,
            bytes_sent=len(document_bytes),
            job_id=None,
            idempotency_key=idempotency_key,
        )
        job_id = submitted["job_id"]

        return self._finish_job(
            job_id,
            context,
            operation,
            language,
            idempotency_key,
            result_reader,
        )

    def _finish_job(
        self,
        job_id: str,
        context: SarvamAuditContext,
        operation: str,
        language: str,
        idempotency_key: str,
        result_reader: Callable[
            [str, dict[str, Any], SarvamAuditContext, str, str, str], _Result
        ],
    ) -> _Result:
        status = self._poll(job_id, context, operation, language, idempotency_key)
        final_status = status.get("status")
        # A partially completed archive cannot be reconciled to individual
        # page results through the current adapter surface.  Treat the whole
        # job as unusable rather than silently dropping failed pages.
        if final_status != "completed":
            raise SarvamError(f"Sarvam job {job_id!r} ended with status {final_status!r}")
        result = result_reader(job_id, status, context, operation, language, idempotency_key)
        return result

    def _poll(
        self,
        job_id: str,
        context: SarvamAuditContext,
        operation: str,
        language: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        for attempt in range(self.max_poll_attempts):
            if attempt:
                self.sleep(self.poll_interval_seconds)
            self._wait_for_submission_slot()
            status = self._get_json(
                url=f"{self.route.endpoint}/doc-ai/v1/job/{job_id}/status",
                headers={"api-subscription-key": self.api_key},
                label="poll",
                context=context,
                operation=operation,
                language=language,
                job_id=job_id,
                idempotency_key=idempotency_key,
            )
            state = status.get("status")
            if state in TERMINAL_STATUSES:
                return status
        raise SarvamPollTimeout(f"Sarvam job {job_id!r} did not reach a terminal state")

    def _read_digitise_result(
        self,
        job_id: str,
        _status: dict[str, Any],
        context: SarvamAuditContext,
        operation: str,
        language: str,
        idempotency_key: str,
    ) -> str:
        response = self._get_json(
            url=f"{self.route.endpoint}/doc-ai/v1/job/{job_id}/download-url",
            headers={"api-subscription-key": self.api_key},
            label="result_lookup",
            context=context,
            operation=operation,
            language=language,
            job_id=job_id,
            idempotency_key=idempotency_key,
        )
        download_url = response.get("download_url") or response.get("url")
        if not isinstance(download_url, str) or not download_url:
            raise SarvamError("Sarvam Digitise result did not return a download URL")
        archive = self._get_binary(
            url=download_url,
            headers=None,
            label="download",
            context=context,
            operation=operation,
            language=language,
            job_id=job_id,
            idempotency_key=idempotency_key,
        )
        return self._primary_markdown(archive)

    def _read_extract_result(
        self,
        job_id: str,
        _status: dict[str, Any],
        context: SarvamAuditContext,
        operation: str,
        language: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._get_json(
            url=f"{self.route.endpoint}/doc-ai/v1/job/{job_id}/results",
            headers={"api-subscription-key": self.api_key},
            label="result_lookup",
            context=context,
            operation=operation,
            language=language,
            job_id=job_id,
            idempotency_key=idempotency_key,
        )

    def _post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        files: dict[str, tuple[str, bytes]],
        data: dict[str, Any],
        label: str,
        context: SarvamAuditContext,
        operation: str,
        language: str,
        bytes_sent: int,
        job_id: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        for attempt in range(1, self.max_submission_attempts + 1):
            self._wait_for_submission_slot()
            try:
                response = self._transport().post(
                    url, headers=headers, files=files, data=data
                )
            except Exception as exc:
                self._audit(
                    context,
                    operation,
                    f"{label}_error",
                    language,
                    bytes_sent,
                    job_id,
                    {"error": type(exc).__name__, "attempt": attempt},
                    idempotency_key,
                )
                raise SarvamError(f"Sarvam {label} request failed") from exc

            if response.status_code == 429:
                exhausted = attempt == self.max_submission_attempts
                retry_delay = self._retry_delay_seconds(response, attempt)
                self._audit(
                    context,
                    operation,
                    f"{label}_rate_limit_exhausted"
                    if exhausted
                    else f"{label}_rate_limited",
                    language,
                    bytes_sent,
                    job_id,
                    {
                        "http_status": 429,
                        "attempt": attempt,
                        "retry_after_seconds": retry_delay,
                    },
                    idempotency_key,
                )
                if exhausted:
                    raise SarvamError(
                        f"Sarvam {label} remained rate limited after "
                        f"{self.max_submission_attempts} attempts"
                    )
                self.sleep(retry_delay)
                continue

            try:
                payload = self._json_response(response, label)
            except Exception as exc:
                self._audit(
                    context,
                    operation,
                    f"{label}_error",
                    language,
                    bytes_sent,
                    job_id,
                    {"error": type(exc).__name__, "attempt": attempt},
                    idempotency_key,
                )
                if isinstance(exc, SarvamError):
                    raise
                raise SarvamError(f"Sarvam {label} request failed") from exc

            returned_job_id = payload.get("job_id")
            self._audit(
                context,
                operation,
                label,
                language,
                bytes_sent,
                returned_job_id if isinstance(returned_job_id, str) else job_id,
                {
                    **self._response_metadata(payload, response.status_code),
                    "attempt": attempt,
                },
                idempotency_key,
            )
            if label == "submission" and not isinstance(returned_job_id, str):
                raise SarvamError("Sarvam submission did not return a job_id")
            return payload
        raise AssertionError("bounded Sarvam submission loop did not return or raise")

    def _wait_for_submission_slot(self) -> None:
        """Enforce the documented ten Document Intelligence calls per minute.

        The budget is shared: a status poll is billed the same as a submission,
        so both take a slot. Counting submissions alone let a single job's poll
        loop exhaust the quota and then rate-limit the next submission.
        """
        now = self.monotonic()
        while self._submission_times and (
            now - self._submission_times[0] >= SUBMISSION_WINDOW_SECONDS
        ):
            self._submission_times.popleft()
        if len(self._submission_times) >= VISION_SUBMISSIONS_PER_MINUTE:
            wait_seconds = SUBMISSION_WINDOW_SECONDS - (
                now - self._submission_times[0]
            )
            self.sleep(max(0.0, wait_seconds))
            now = self.monotonic()
            while self._submission_times and (
                now - self._submission_times[0] >= SUBMISSION_WINDOW_SECONDS
            ):
                self._submission_times.popleft()
        self._submission_times.append(now)

    def _retry_delay_seconds(self, response: HttpResponse, attempt: int) -> float:
        headers = getattr(response, "headers", {})
        retry_after = headers.get("Retry-After") if headers is not None else None
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            delay = self.submission_backoff_seconds * (2 ** (attempt - 1))
        return min(MAX_RETRY_DELAY_SECONDS, max(0.0, delay))

    def _get_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        label: str,
        context: SarvamAuditContext,
        operation: str,
        language: str,
        job_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            response = self._transport().get(url, headers=headers)
            payload = self._json_response(response, label)
        except Exception as exc:
            self._audit(
                context,
                operation,
                f"{label}_error",
                language,
                0,
                job_id,
                {"error": type(exc).__name__},
                idempotency_key,
            )
            if isinstance(exc, SarvamError):
                raise
            raise SarvamError(f"Sarvam {label} request failed") from exc
        self._audit(
            context,
            operation,
            label,
            language,
            0,
            job_id,
            self._response_metadata(payload, response.status_code),
            idempotency_key,
        )
        return payload

    def _get_binary(
        self,
        *,
        url: str,
        headers: dict[str, str] | None,
        label: str,
        context: SarvamAuditContext,
        operation: str,
        language: str,
        job_id: str,
        idempotency_key: str,
    ) -> bytes:
        try:
            response = self._transport().get(url, headers=headers)
            if not 200 <= response.status_code < 300:
                raise SarvamError(f"Sarvam {label} returned HTTP {response.status_code}")
        except Exception as exc:
            self._audit(
                context,
                operation,
                f"{label}_error",
                language,
                0,
                job_id,
                {"error": type(exc).__name__},
                idempotency_key,
            )
            if isinstance(exc, SarvamError):
                raise
            raise SarvamError(f"Sarvam {label} request failed") from exc
        self._audit(
            context,
            operation,
            label,
            language,
            0,
            job_id,
            {"http_status": response.status_code},
            idempotency_key,
        )
        return response.content

    @staticmethod
    def _primary_markdown(archive: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(archive)) as result_zip:
                markdown_members = [
                    member
                    for member in result_zip.namelist()
                    if not member.endswith("/") and Path(member).suffix.lower() == ".md"
                ]
                if len(markdown_members) != 1:
                    raise SarvamError(
                        "Sarvam Digitise ZIP must contain exactly one primary Markdown file"
                    )
                return result_zip.read(markdown_members[0]).decode("utf-8")
        except zipfile.BadZipFile as exc:
            raise SarvamError("Sarvam Digitise download was not a valid ZIP archive") from exc
        except UnicodeDecodeError as exc:
            raise SarvamError("Sarvam Digitise Markdown was not UTF-8") from exc

    def _transport(self) -> HttpTransport:
        if self.transport is None:
            # The import is intentionally local: disabled/default installations
            # must not construct a network client at import or startup.
            import httpx

            self.transport = httpx.Client(timeout=30.0)
        return self.transport

    @staticmethod
    def _json_response(response: HttpResponse, label: str) -> dict[str, Any]:
        if not 200 <= response.status_code < 300:
            raise SarvamError(f"Sarvam {label} returned HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise SarvamError(f"Sarvam {label} response was not a JSON object")
        return payload

    @staticmethod
    def _response_metadata(payload: dict[str, Any], http_status: int) -> dict[str, Any]:
        """Keep auditable operational metadata, never provider document output."""
        safe_keys = {"job_id", "status", "usage", "model", "request_id"}
        return {
            **{key: value for key, value in payload.items() if key in safe_keys},
            "http_status": http_status,
        }

    @staticmethod
    def _digitise_form(language: str) -> dict[str, str]:
        return {"language": language, "output_format": "md"}

    def _idempotency_key(
        self,
        context: SarvamAuditContext,
        operation: str,
        document_bytes: bytes,
        *,
        filename: str,
        form: dict[str, Any],
    ) -> str:
        try:
            normalized_form = json.dumps(
                form,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Sarvam request form must be canonical JSON data") from exc
        content_hash = hashlib.sha256(document_bytes).hexdigest()
        source = "\0".join(
            (
                context.ticket,
                context.document_id,
                context.stage,
                operation,
                self.route.provider,
                self.route.model_id,
                self.route.endpoint,
                filename,
                normalized_form,
                content_hash,
            )
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _authorization_reference(self) -> str:
        """The route's authorization, naming the accepted risk when that is the basis.

        A reader of the audit table should not have to go to the source to find
        out whether a call went out on verified controls or on someone's
        signature accepting unverified ones.
        """
        reference = self.route.authorization_reference
        accepted = self.route.accepted_risk
        if self.route.egress_basis == "accepted_risk" and accepted is not None:
            return (
                f"{reference} | risk accepted by {accepted.authority}"
                f" on {accepted.accepted_on}: {accepted.basis}"
                f" (unverified: {', '.join(self.route.unverified_controls)})"
            )
        return reference

    def _audit(
        self,
        context: SarvamAuditContext,
        operation: str,
        event: str,
        language: str,
        bytes_sent: int,
        job_id: str | None,
        metadata: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        self.audit_log.append(
            AuditRecord(
                ticket=context.ticket,
                stage=context.stage,
                document_id=context.document_id,
                provider=self.route.provider,
                model_id=self.route.model_id,
                bytes_sent=bytes_sent,
                timestamp=datetime.now(UTC).isoformat(),
                authorization_reference=self._authorization_reference(),
                operation=operation,
                event=event,
                language=language,
                idempotency_key=idempotency_key,
                job_id=job_id,
                response_metadata=metadata,
            )
        )
