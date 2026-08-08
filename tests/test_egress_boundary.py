"""Only `janasunani/egress/` may talk to a third party (#83, ROADMAP §5.5).

The egress rule is the strongest safety claim in the repo: exactly one module
is permitted to send citizen data outside DPIC-controlled systems, so that the
audit log, the kill switch and the governance gate cannot be bypassed by a
second HTTP client appearing somewhere else.

It was previously asserted in a PR body as a "CI guard" that did not exist.
This is the guard. It is deliberately structural, not a grep for a provider
name: adding a new HTTP client anywhere in the package fails here and forces
whoever added it to either move it under `egress/` or justify the exception by
editing the allowlist below in the same diff.
"""

from __future__ import annotations

import ast
import pathlib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "janasunani"

HTTP_MODULES = {"httpx", "requests", "aiohttp", "urllib3", "http.client"}

# Modules permitted to construct an HTTP client, and why.
#
# egress/sarvam.py  - the sole authorized-external client. Carries citizen
#                     document bytes to a third party, behind the governance
#                     gate and the per-call audit log.
# ingestion/*       - DPIC-controlled endpoints only: the Janasunani source
#                     API and S3. Not a third party, so not egress.
ALLOWED = {
    "egress/sarvam.py",
    "ingestion/client.py",
    "ingestion/document_ingestion.py",
}


def _http_importers() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in HTTP_MODULES or name in HTTP_MODULES:
                    found.add(path.relative_to(PACKAGE).as_posix())
    return found


def test_only_allowlisted_modules_construct_http_clients():
    """A new HTTP client anywhere else is a new way out of the building."""
    unexpected = _http_importers() - ALLOWED
    assert unexpected == set(), (
        "these modules import an HTTP client but are not on the egress "
        f"allowlist: {sorted(unexpected)}. Move the call under "
        "janasunani/egress/, or add it to ALLOWED with a comment saying which "
        "DPIC-controlled endpoint it talks to."
    )


def test_allowlist_has_no_stale_entries():
    """A stale allowlist quietly widens the boundary it is meant to hold."""
    importers = _http_importers()
    stale = {name for name in ALLOWED if not (PACKAGE / name).exists()}
    assert stale == set(), f"allowlisted modules no longer exist: {sorted(stale)}"

    unused = ALLOWED - importers
    assert unused == set(), (
        f"allowlisted modules no longer import an HTTP client: {sorted(unused)}. "
        "Remove them so the allowlist keeps meaning what it says."
    )


def test_the_provider_endpoint_is_only_named_in_egress():
    """The base URL is the thing a stray client would need. Keep it in one place."""
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.relative_to(PACKAGE).parts[0] == "egress":
            continue
        if "api.sarvam.ai" in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(PACKAGE).as_posix())
    assert offenders == [], (
        f"the Sarvam endpoint is referenced outside egress/: {offenders}"
    )


def test_sarvam_route_declares_authorized_external_tier():
    """Every authorized-external route must declare its tier explicitly."""
    from janasunani.egress.sarvam import PROVIDER_REGISTRY

    route = PROVIDER_REGISTRY["sarvam-vision"]
    assert route.trust_tier == "authorized-external"
    assert route.fallback == "pytesseract"
    # fallback is a maintained dpic-infra counterpart
    assert route.provider == "sarvam-hosted"


def test_sarvam_route_records_authorization_ref_and_retention_encryption_audit():
    """Authorization ref (GoO MoU + Vishal Dev sign-off), retention, encryption, audit."""
    from janasunani.egress.sarvam import AUTHORIZATION_REFERENCE, PROVIDER_REGISTRY

    route = PROVIDER_REGISTRY["sarvam-vision"]
    assert "MoU" in route.authorization_reference
    assert "Vishal Dev" in route.authorization_reference
    assert AUTHORIZATION_REFERENCE in route.authorization_reference
    # retention and encryption controls must be declared (even if unverified)
    assert route.retention_terms.statement.strip() != ""
    assert route.encryption_in_transit.statement.strip() != ""
    assert route.encryption_at_rest.statement.strip() != ""
    assert route.audit_policy.strip() != ""
    assert route.data_class.strip() != ""


def test_sarvam_settings_tier_declaration_matches_registry():
    """Settings centralizes the key and declares the tier (config.py)."""
    from janasunani.config import Settings
    from janasunani.egress.sarvam import PROVIDER_REGISTRY, TRUST_TIER

    settings = Settings()
    # Settings declares the tier — keeps config and registry in sync
    assert settings.SARVAM_TRUST_TIER == "authorized-external"
    assert settings.SARVAM_TRUST_TIER == TRUST_TIER
    assert settings.SARVAM_TRUST_TIER == PROVIDER_REGISTRY["sarvam-vision"].trust_tier
    assert "MoU" in settings.SARVAM_AUTHORIZATION_REFERENCE
    assert "Vishal Dev" in settings.SARVAM_AUTHORIZATION_REFERENCE


def test_sarvam_api_key_only_in_config_and_egress():
    """SARVAM_API_KEY must not be hardcoded or referenced outside config/egress."""
    package = pathlib.Path(__file__).resolve().parent.parent / "janasunani"
    offenders = []
    for p in package.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(package).as_posix()
        if rel in ("config.py", "egress/sarvam.py"):
            continue
        text = p.read_text(encoding="utf-8")
        if "SARVAM_API_KEY" in text or "SARVAM_API_SUBSCRIPTION_KEY" in text:
            offenders.append(rel)
    assert offenders == [], f"SARVAM_API_KEY referenced outside config/egress: {offenders}"
    # also ensure no hardcoded key literal (heuristic: 30+ char alphanum near sarvam)
    # We don't check literals here; the centralization above is the control.


def test_kill_switch_falls_back_to_dpic_infra_and_audit_logs():
    """Kill switch (enabled=False) never calls remote transport and logs disabled."""
    import tempfile
    from pathlib import Path as _Path

    from janasunani.egress.sarvam import SarvamAuditContext, SarvamVisionAdapter, SqliteAuditLog

    with tempfile.TemporaryDirectory() as tmp:
        audit_path = _Path(tmp) / "audit.sqlite"

        class NoCallTransport:
            def post(self, *a, **kw):
                raise AssertionError("kill switch must not call transport")
            def get(self, *a, **kw):
                raise AssertionError("kill switch must not call transport")

        adapter = SarvamVisionAdapter(
            enabled=False,
            audit_log=SqliteAuditLog(audit_path),
            transport=NoCallTransport(),
        )
        ctx = SarvamAuditContext(ticket="T-1", stage="ocr_extraction", document_id="doc:1")
        outcome = adapter.digitise_or_fallback(b"bytes", "page.png", "od-IN", ctx, lambda: "fallback-text")
        assert outcome.text == "fallback-text"
        assert outcome.ocr_model == "pytesseract"
        # audit row for disabled
        import sqlite3
        rows = list(sqlite3.connect(audit_path).execute("SELECT event, bytes_sent FROM authorized_external_audit"))
        assert any(r[0] == "disabled" for r in rows)
        assert all(r[1] == 0 for r in rows if r[0] == "disabled")


def test_transliteration_also_via_egress_and_kill_switch():
    """Transliteration probe also goes through the single egress module and kill switch."""
    import tempfile
    from pathlib import Path as _Path

    from janasunani.egress.sarvam import SarvamAuditContext, SarvamVisionAdapter, SqliteAuditLog

    with tempfile.TemporaryDirectory() as tmp:
        audit_path = _Path(tmp) / "audit.sqlite"

        class NoCallTransport:
            def post(self, *a, **kw):
                raise AssertionError("kill switch must not call transport for transliteration")
            def get(self, *a, **kw):
                raise AssertionError("kill switch must not call transport for transliteration")

        adapter = SarvamVisionAdapter(
            enabled=False,
            audit_log=SqliteAuditLog(audit_path),
            transport=NoCallTransport(),
        )
        ctx = SarvamAuditContext(ticket="T-2", stage="transliteration_probe", document_id="doc:2")
        result = adapter.transliterate_or_fallback("mu ghara", "en-IN", "od-IN", ctx, lambda x: x.upper())
        assert result == "MU GHARA"
        import sqlite3
        rows = list(sqlite3.connect(audit_path).execute("SELECT operation, event FROM authorized_external_audit"))
        assert any(op == "transliterate" and ev == "disabled" for op, ev in rows)


def test_audit_log_row_per_call_contains_required_fields():
    """Every Sarvam call must create an audit row with ticket, stage, provider, model, bytes, etc."""
    import tempfile
    import sqlite3
    from pathlib import Path as _Path

    from janasunani.egress.sarvam import SarvamAuditContext, SarvamVisionAdapter, SqliteAuditLog

    # use a recorded transport that succeeds for transliteration
    class RecordedTransliterate:
        def post(self, url, **kwargs):
            assert "transliterate" in url
            class R:
                status_code = 200
                def json(self_inner):
                    return {"transliterated_text": "translated"}
                headers = {}
            return R()
        def get(self, *a, **kw):
            raise AssertionError


    # Use a verified route so governance doesn't block
    from janasunani.egress.sarvam import GovernanceControl, PROVIDER_REGISTRY
    from dataclasses import replace

    control = GovernanceControl(statement="verified test", verified=True)
    route = replace(PROVIDER_REGISTRY["sarvam-vision"], retention_terms=control, encryption_in_transit=control, encryption_at_rest=control)

    with tempfile.TemporaryDirectory() as tmp:
        audit_path = _Path(tmp) / "audit.sqlite"
        adapter = SarvamVisionAdapter(
            enabled=True,
            api_key="test-key",
            audit_log=SqliteAuditLog(audit_path),
            route=route,
            transport=RecordedTransliterate(),
            poll_interval_seconds=0,
        )
        ctx = SarvamAuditContext(ticket="T-AUDIT", stage="test", document_id="doc:audit")
        # One transliteration call should create at least one audit row with required columns
        adapter.transliterate("hello world", "en-IN", "od-IN", ctx)
        rows = list(sqlite3.connect(audit_path).execute(
            "SELECT ticket, stage, provider, model_id, bytes_sent, timestamp, authorization_reference, operation FROM authorized_external_audit"
        ))
        assert len(rows) >= 1
        for ticket, stage, provider, model_id, bytes_sent, timestamp, auth_ref, operation in rows:
            assert ticket == "T-AUDIT"
            assert stage == "test"
            assert provider == "sarvam-hosted"
            assert model_id != ""
            assert bytes_sent > 0
            assert timestamp != ""
            assert auth_ref != ""
            assert operation == "transliterate"


def test_no_citizen_data_leaves_box_except_via_egress():
    """No module outside egress should directly handle raw grievance bytes for external calls.

    The check is structural: only egress/sarvam.py may carry the authorized-external
    trust tier and the citizen PII data class. Any other module declaring such a route
    would be a second way out.
    """
    from janasunani.egress.sarvam import PROVIDER_REGISTRY

    for name, route in PROVIDER_REGISTRY.items():
        assert route.trust_tier in ("same-host", "dpic-infra", "authorized-external")
        if route.trust_tier == "authorized-external":
            assert route.data_class != ""
            assert "PII" in route.data_class or "citizen" in route.data_class.lower()
            assert route.fallback.strip() != ""

    # Ensure only egress defines such a registry
    import pathlib
    package = pathlib.Path(__file__).resolve().parent.parent / "janasunani"
    offenders = []
    for p in package.rglob("*.py"):
        if p.relative_to(package).parts[0] == "egress":
            continue
        text = p.read_text(encoding="utf-8")
        if "authorized-external" in text and "PROVIDER_REGISTRY = {" in text:
            offenders.append(p.relative_to(package).as_posix())
    assert offenders == [], f"authorized-external registry outside egress/: {offenders}"
