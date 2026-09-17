"""Aggregate-only monitoring publisher and serving contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import duckdb

from janasunani.analytics.monitoring import _pct, refiling_summary, suppress_breakdown
from janasunani.serving.api import create_app
from janasunani.serving.monitoring import (
    ArtifactMonitoringProvider,
    MonitoringArtifactError,
)


def _metric(metric_id: str = "count") -> dict:
    return {
        "id": metric_id,
        "label": "Synthetic aggregate",
        "state": "recorded",
        "value": 10,
        "unit": "grievances",
        "numerator": 10,
        "denominator": 20,
        "coveragePct": 50.0,
        "note": None,
    }


def _release() -> dict:
    panels = [
        {
            "id": panel_id,
            "title": panel_id.title(),
            "state": "recorded",
            "denominator": {"label": "Synthetic denominator", "value": 20},
            "metrics": [_metric(panel_id)],
            "breakdown": None,
            "breakdownUnavailableReason": None,
            "caveats": ["Synthetic fixture."],
        }
        for panel_id in ("aging", "transfers", "journey", "atr", "demand", "closure")
    ]
    return {
        "schemaVersion": 1,
        "generatedAt": "2025-07-30T12:00:00+00:00",
        "sourceFreshness": {"extractMaximum": "2025-07-30"},
        "inputDigests": {"complaints": "a", "actionHistory": "b"},
        "periods": [{
            "id": "fy-2024-25", "label": "FY 2024-25",
            "start": "2024-07-01", "endExclusive": "2025-07-01",
        }],
        "scopes": [{
            "id": "department-21", "label": "Panchayati Raj",
            "kind": "department", "parentId": None,
            "definition": "Grievances recorded against department 21.",
            "quickView": True, "availablePeriods": ["fy-2024-25"],
        }],
        "dashboards": {
            "department-21:fy-2024-25": {
                "scopeId": "department-21", "scopeLabel": "Panchayati Raj",
                "scopeKind": "department",
                "scopeDefinition": "Grievances recorded against department 21.",
                "periodId": "fy-2024-25", "periodLabel": "FY 2024-25",
                "snapshotDate": "2025-07-30", "panels": panels,
            }
        },
    }


def _write(tmp_path: Path, release: dict | None = None) -> Path:
    path = tmp_path / "monitoring_dashboard_v1.json"
    path.write_text(json.dumps(release or _release()))
    return path


def test_formula_boundaries_and_small_cell_suppression():
    assert _pct(0, 0) is None
    assert _pct(1, 3) == 33.3
    assert suppress_breakdown([{"value": 0}, {"value": 10}]) is not None
    assert suppress_breakdown([{"value": 9}, {"value": 100}]) is None


def test_refiling_censoring_includes_post_fy_followup_and_window_boundaries():
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE scope_tickets(
          ticket_no VARCHAR, created_on DATE, resolved_on DATE
        );
        INSERT INTO scope_tickets VALUES
          ('closed-30', '2024-07-01', '2025-06-10'),
          ('refile-at-30', '2025-07-10', NULL),
          ('closed-90', '2024-08-01', '2025-05-01'),
          ('refile-at-90', '2025-07-30', NULL),
          ('too-recent', '2024-09-01', '2025-07-01'),
          ('refile-after-snapshot', '2025-07-31', NULL);
        CREATE TABLE full_groups(ticket_no VARCHAR, duplicate_group_id VARCHAR);
        INSERT INTO full_groups VALUES
          ('closed-30', 'g30'), ('refile-at-30', 'g30'),
          ('closed-90', 'g90'), ('refile-at-90', 'g90'),
          ('too-recent', 'late'), ('refile-after-snapshot', 'late');
    """)
    assert refiling_summary(con) == {
        "den30": 2,
        "num30": 1,
        "den90": 1,
        "num90": 1,
    }


def test_catalog_and_dashboard_are_allowlisted(tmp_path):
    provider = ArtifactMonitoringProvider(_write(tmp_path))
    client = TestClient(create_app(monitoring=provider))

    catalog = client.get("/supervisor/monitoring/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["scopes"][0]["id"] == "department-21"

    response = client.get(
        "/supervisor/monitoring",
        params={"scope_id": "department-21", "period": "fy-2024-25"},
    )
    assert response.status_code == 200
    assert len(response.json()["panels"]) == 6
    assert "inputDigests" not in response.json()


@pytest.mark.parametrize(
    ("scope", "period"),
    [
        ("unknown", "fy-2024-25"),
        ("department-21", "fy-1900-01"),
        ("../data/complaints.parquet", "fy-2024-25"),
        ("department-21", "1+1"),
    ],
)
def test_unknown_or_malformed_query_fails_closed(tmp_path, scope, period):
    client = TestClient(create_app(monitoring=ArtifactMonitoringProvider(_write(tmp_path))))
    response = client.get(
        "/supervisor/monitoring", params={"scope_id": scope, "period": period}
    )
    assert response.status_code in {404, 422}


@pytest.mark.parametrize("field", ["grievance", "ticket_no", "identity_key", "signature"])
def test_sensitive_or_row_level_fields_fail_closed(tmp_path, field):
    release = _release()
    release["dashboards"]["department-21:fy-2024-25"][field] = "synthetic"
    provider = ArtifactMonitoringProvider(_write(tmp_path, release))
    with pytest.raises(MonitoringArtifactError, match="forbidden"):
        provider.dashboard("department-21", "fy-2024-25")


def test_extra_fields_fail_strict_validation(tmp_path):
    release = _release()
    release["dashboards"]["department-21:fy-2024-25"]["unexpected"] = 1
    provider = ArtifactMonitoringProvider(_write(tmp_path, release))
    with pytest.raises(MonitoringArtifactError, match="failed validation"):
        provider.dashboard("department-21", "fy-2024-25")
