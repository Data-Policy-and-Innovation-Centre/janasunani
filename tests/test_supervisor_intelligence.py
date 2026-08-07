"""Synthetic contract tests for the aggregate-only supervisor provider."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")

from fastapi.testclient import TestClient  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.intelligence import (  # noqa: E402
    ArtifactSupervisorProvider,
    supervisor_provider_from_env,
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


def _write_closure(
    findings_dir: Path,
    *,
    name: str = "closure_recording_no_action.csv",
    row: dict[str, str] | None = None,
    extra_fields: tuple[str, ...] = (),
) -> Path:
    path = findings_dir / name
    fields = (*_CLOSURE_FIELDS, *extra_fields)
    values = _closure_row() if row is None else row
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(values)
    return path


def _json(dashboard) -> str:
    return json.dumps(dashboard.model_dump(mode="json", by_alias=True)).lower()


def test_recorded_closure_artifact_is_served_with_explicit_artifact_provenance(tmp_path):
    _write_closure(tmp_path)

    dashboard = ArtifactSupervisorProvider(tmp_path).dashboard()

    assert dashboard.closure.provenance.state == "recorded"
    assert dashboard.closure.provenance.artifact == "closure_recording_no_action.csv"
    assert dashboard.closure.numerator == 3
    assert dashboard.closure.primary_denominator == 6
    assert dashboard.closure.secondary_denominator == 8
    assert dashboard.closure.primary_share_pct == 50.0
    assert dashboard.closure.secondary_share_pct == 37.5

    # #165's manual duplicate baseline must not be promoted into a capability.
    assert dashboard.workload.provenance.state == "unavailable"
    assert dashboard.spike.provenance.state == "unavailable"
    assert "not a substitute" in dashboard.workload.provenance.reason.lower()


def test_legacy_closure_artifact_name_is_supported_without_guessing_at_values(tmp_path):
    _write_closure(tmp_path, name="closure_finding_summary.csv")

    dashboard = ArtifactSupervisorProvider(tmp_path).dashboard()

    assert dashboard.closure.provenance.state == "recorded"
    assert dashboard.closure.provenance.artifact == "closure_finding_summary.csv"


def test_closure_fails_closed_for_extra_or_row_level_fields(tmp_path):
    fake_row_level_value = "synthetic-only-content-that-must-not-reach-the-api"
    _write_closure(
        tmp_path,
        extra_fields=("grievance",),
        row={**_closure_row(), "grievance": fake_row_level_value},
    )

    dashboard = ArtifactSupervisorProvider(tmp_path).dashboard()

    assert dashboard.closure.provenance.state == "unavailable"
    serialized = _json(dashboard)
    assert fake_row_level_value not in serialized
    assert '"grievance"' not in serialized


def test_closure_fails_closed_when_its_aggregate_identities_do_not_reconcile(tmp_path):
    _write_closure(tmp_path, row=_closure_row(bare="4"))

    dashboard = ArtifactSupervisorProvider(tmp_path).dashboard()

    assert dashboard.closure.provenance.state == "unavailable"
    assert "value" not in _json(dashboard.closure)


def test_closure_fails_closed_when_it_has_no_publishable_denominator(tmp_path):
    _write_closure(
        tmp_path,
        row=_closure_row(
            resolved_complaints="0",
            ladder_closures="0",
            bare="0",
            with_action="0",
            benefit="0",
            claims_action="0",
            off_ladder="0",
            bare_share_of_ladder_pct="0.0",
            bare_share_of_resolved_pct="0.0",
            ladder_coverage_pct="0.0",
            off_ladder_share_pct="0.0",
        ),
    )

    dashboard = ArtifactSupervisorProvider(tmp_path).dashboard()

    assert dashboard.closure.provenance.state == "unavailable"


def test_two_closure_artifacts_are_ambiguous_and_withheld(tmp_path):
    _write_closure(tmp_path, name="closure_recording_no_action.csv")
    _write_closure(tmp_path, name="closure_finding_summary.csv")

    dashboard = ArtifactSupervisorProvider(tmp_path).dashboard()

    assert dashboard.closure.provenance.state == "unavailable"


def test_unconfigured_provider_fails_closed_without_touching_an_artifact_dir(monkeypatch):
    monkeypatch.delenv("JANASUNANI_SUPERVISOR_FINDINGS_DIR", raising=False)

    dashboard = supervisor_provider_from_env().dashboard()

    assert dashboard.closure.provenance.state == "unavailable"
    assert dashboard.workload.provenance.state == "unavailable"
    assert dashboard.spike.provenance.state == "unavailable"


def test_supervisor_endpoint_uses_the_camel_case_aggregate_dto(tmp_path):
    _write_closure(tmp_path)
    client = TestClient(create_app(supervisor=ArtifactSupervisorProvider(tmp_path)))

    response = client.get("/supervisor")

    assert response.status_code == 200
    body = response.json()
    assert body["closure"]["provenance"] == {
        "state": "recorded",
        "label": "Recorded aggregate artifact",
        "artifact": "closure_recording_no_action.csv",
        "artifactWrittenAt": body["closure"]["provenance"]["artifactWrittenAt"],
    }
    assert body["closure"]["numerator"] == 3
    assert body["closure"]["primaryDenominator"] == 6
    assert body["closure"]["secondaryDenominator"] == 8
    assert body["workload"]["provenance"]["state"] == "unavailable"
    assert body["spike"]["provenance"]["state"] == "unavailable"

    serialized = json.dumps(body).lower()
    for forbidden_key in (
        '"grievance"',
        '"ticket_no"',
        '"mobile"',
        '"email"',
        '"identity_key"',
    ):
        assert forbidden_key not in serialized
