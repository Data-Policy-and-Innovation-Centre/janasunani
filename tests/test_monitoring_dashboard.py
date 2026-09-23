"""Aggregate-only monitoring publisher and serving contract tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import duckdb

from janasunani.analytics.monitoring import PROXY_METRICS, UNRECORDED_FIELDS, _discards, _recording, _metric as published_metric, _pct, withhold_small_panel, refiling_summary, suppress_breakdown
from janasunani.serving.api import create_app
from janasunani.serving.schemas import MONITORING_PANEL_IDS
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
        "basis": "direct",
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
        for panel_id in MONITORING_PANEL_IDS
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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"value": 3, "unit": "closures"},
        {"value": 0.1, "unit": "percent", "numerator": 3, "denominator": 3000},
        {"value": 4.0, "unit": "days", "denominator": 7},
    ],
)
def test_small_metric_cells_are_withheld(kwargs):
    # The minimum reportable cell applies to headline metrics, not only to
    # breakdowns: a subcategory published benefit-recorded = 3.
    metric = published_metric("m", "M", **kwargs)
    assert metric["state"] == "unavailable"
    assert "value" not in metric
    assert "below 10" in metric["reason"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"value": 0, "unit": "grievances"},
        {"value": 10, "unit": "closures"},
        {"value": 0.0, "unit": "percent", "numerator": 0, "denominator": 500},
        {"value": 12.5, "unit": "days"},
    ],
)
def test_zero_and_reportable_metric_cells_are_published(kwargs):
    assert published_metric("m", "M", **kwargs)["state"] == "recorded"


def _recorded_panel(denominator: int) -> dict:
    return {
        "id": "journey", "title": "End-to-end journey", "state": "recorded",
        "denominator": {"label": "Disposed journeys that tile", "value": denominator},
        "metrics": [], "breakdown": None, "breakdownUnavailableReason": None,
        "caveats": ["c"],
    }


def test_a_panel_with_a_small_denominator_is_withheld():
    # The dashboard always renders the panel denominator, so a cohort of 1-9
    # is itself a small cell, whatever _metric withheld inside it.
    panel = withhold_small_panel(_recorded_panel(4))
    assert panel == {
        "id": "journey", "title": "End-to-end journey", "state": "unavailable",
        "reason": "Withheld because the panel's cohort is below 10.", "caveats": ["c"],
    }


@pytest.mark.parametrize("denominator", [0, 10])
def test_a_panel_with_a_reportable_denominator_is_kept(denominator):
    panel = _recorded_panel(denominator)
    assert withhold_small_panel(panel) is panel


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


def test_each_recorded_metric_says_whether_it_is_direct_or_a_proxy():
    assert published_metric("loop-rate", "L", 12.0, unit="percent")["basis"] == "proxy"
    assert published_metric("transfer-rate", "T", 12.0, unit="percent")["basis"] == "direct"
    # Unavailable metrics carry no value, so they carry no basis either.
    assert "basis" not in published_metric("problems", "P", None, unit="groups")


def test_every_proxy_id_is_a_metric_the_publisher_emits():
    # A typo in PROXY_METRICS would silently publish a proxy as direct.
    source = Path("janasunani/analytics/monitoring.py").read_text()
    emitted = set(re.findall(r'(?:_metric|share)\(\s*"([a-z0-9-]+)"', source))
    emitted |= {key for key, _label in re.findall(r'\("([a-z-]+)", "([^"]+)"\)', source)}
    assert PROXY_METRICS <= emitted, PROXY_METRICS - emitted


def test_a_metric_without_a_basis_fails_closed(tmp_path):
    # The intact release loads, so the failure below is the missing field and
    # nothing else.
    ArtifactMonitoringProvider(_write(tmp_path)).dashboard("department-21", "fy-2024-25")

    release = _release()
    del release["dashboards"]["department-21:fy-2024-25"]["panels"][0]["metrics"][0]["basis"]
    (tmp_path / "missing").mkdir()
    provider = ArtifactMonitoringProvider(_write(tmp_path / "missing", release))

    with pytest.raises(MonitoringArtifactError):
        provider.dashboard("department-21", "fy-2024-25")


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
    assert [p["id"] for p in response.json()["panels"]] == list(MONITORING_PANEL_IDS)
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


def test_a_dashboard_missing_a_governed_panel_fails_closed(tmp_path):
    release = _release()
    panels = release["dashboards"]["department-21:fy-2024-25"]["panels"]
    panels[:] = [p for p in panels if p["id"] != "discards"]
    provider = ArtifactMonitoringProvider(_write(tmp_path, release))
    with pytest.raises(MonitoringArtifactError, match="every governed panel"):
        provider.dashboard("department-21", "fy-2024-25")


def test_discards_reports_reason_and_timing_together():
    # Each case is repeated ten times so no cell falls under the minimum.
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE cases(kind VARCHAR, created DATE, status VARCHAR,
                           transfer_first BOOLEAN, remark VARCHAR, remark_on DATE);
        INSERT INTO cases VALUES
          -- transfer, then an officer template with odd spacing and a full stop
          ('dup_after', '2024-08-01', 'Discard', TRUE, '  Duplicate   copy.', '2024-08-10'),
          -- no transfer before the reason
          ('details_before', '2024-09-01', 'Discard', FALSE, 'Complaint details inadequate', '2024-09-02'),
          -- discarded, but the wording is not a governed template
          ('unrecognised', '2024-10-01', 'Discard', FALSE, 'not a template', '2024-10-02'),
          -- a governed reason on a grievance that was later disposed
          ('taken_up', '2024-11-01', 'Disposed', FALSE, 'case taken up earlier hence closed', '2024-11-02'),
          -- filed outside the period
          ('outside_fy', '2025-07-05', 'Discard', FALSE, 'duplicate copy', '2025-07-06'),
          -- reason recorded after the snapshot
          ('after_snapshot', '2025-06-01', 'Discard', FALSE, 'duplicate copy', '2025-08-01');
        CREATE TABLE complaints AS
          SELECT kind || '-' || i AS ticket_no, created AS created_on, status
          FROM cases, range(10) r(i);
        CREATE TABLE scope_tickets AS SELECT ticket_no, created_on FROM complaints;
        CREATE TABLE action_history AS
          SELECT row_number() OVER () AS id, * FROM (
            SELECT kind || '-' || i AS ticket_no, CAST(created AS TIMESTAMP) AS action_taken_date,
                   'Complaint Transfer' AS action_status, NULL AS action_taken_remark
            FROM cases, range(10) r(i) WHERE transfer_first
            UNION ALL
            SELECT kind || '-' || i, CAST(remark_on AS TIMESTAMP), 'Disposed', remark
            FROM cases, range(10) r(i));
    """)
    panel = _discards(con)
    metrics = {m["id"]: m for m in panel["metrics"]}

    assert panel["denominator"]["value"] == 50  # outside_fy excluded
    # 40 discards in the period; 20 carry a governed reason before the snapshot.
    assert (metrics["discard-rate"]["numerator"], metrics["discard-rate"]["denominator"]) == (40, 50)
    assert (metrics["discard-reason-recognised"]["numerator"], metrics["discard-reason-recognised"]["denominator"]) == (20, 40)
    # 30 grievances have a governed reason (taken_up counts too); 10 after a transfer.
    assert (metrics["discard-after-transfer"]["numerator"], metrics["discard-after-transfer"]["denominator"]) == (10, 30)
    assert {m["basis"] for m in panel["metrics"]} == {"direct"}
    assert panel["breakdown"] == [
        {"label": "Details inadequate · before any transfer", "value": 10},
        {"label": "Case already taken up / taken up earlier · before any transfer", "value": 10},
        {"label": "Duplicate copy · after a transfer", "value": 10},
    ]


def test_recording_reports_coverage_and_names_what_is_missing():
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE complaints AS SELECT
          'T' || i AS ticket_no, DATE '2024-08-01' AS created_on,
          CASE WHEN i < 20 THEN 'Online' WHEN i < 25 THEN '  ' END AS mode,
          CASE WHEN i < 40 THEN 7 END AS category_id,
          CASE WHEN i < 10 THEN 'Scheme' END AS subcategory,
          NULL::VARCHAR AS review_authority
        FROM range(40) r(i);
        CREATE TABLE scope_tickets AS SELECT ticket_no, created_on FROM complaints;
        CREATE TABLE action_history AS SELECT
          i AS id, 'T' || i AS ticket_no,
          -- 30 in-period transfers; three 'Forwarded', the spelling the action
          -- taxonomy classifies; then disposals, which are not assignment
          -- events; then transfers dated after the snapshot.
          CASE WHEN i < 35 THEN TIMESTAMP '2024-08-02' ELSE TIMESTAMP '2025-08-15' END AS action_taken_date,
          CASE WHEN i < 30 OR i >= 35 THEN 'Complaint Transfer'
               WHEN i < 33 THEN 'Forwarded' ELSE 'Disposed' END AS action_status
        FROM range(40) r(i);
    """)
    discards = {"metrics": [published_metric(
        "discard-reason-recognised", "Discards with a recognised reason", 50.0,
        unit="percent", numerator=10, denominator=20)]}
    metrics = {m["id"]: m for m in _recording(con, discards)["metrics"]}

    assert (metrics["rec-entry"]["numerator"], metrics["rec-entry"]["denominator"]) == (20, 40)
    assert metrics["rec-classification"]["value"] == 100.0
    assert metrics["rec-classification"]["note"]  # only the current category
    assert metrics["rec-events"]["numerator"] == 33
    assert metrics["rec-scheme"]["numerator"] == 10
    # Both count a stand-in field, not the one the row names.
    assert metrics["rec-scheme"]["basis"] == metrics["rec-review-required"]["basis"] == "proxy"
    assert metrics["rec-entry"]["basis"] == "direct"
    # Zero is published, not withheld: nothing names a review authority.
    assert metrics["rec-review-required"]["value"] == 0.0
    # The discard row is the discards panel's own figure, relabelled.
    assert (metrics["rec-discard-reason"]["numerator"], metrics["rec-discard-reason"]["label"]) == (10, "Discard reason")
    # No ATR event in the extract: explicit, and says what it would unlock.
    assert metrics["rec-atr"]["state"] == "unavailable"
    assert "ATR queue" in metrics["rec-atr"]["reason"]
    for metric_id, _label, unlocks in UNRECORDED_FIELDS:
        assert metrics[metric_id]["state"] == "unavailable"
        assert unlocks in metrics[metric_id]["reason"]
