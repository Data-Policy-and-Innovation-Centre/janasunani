"""Aggregate-only monitoring publisher and serving contract tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import duckdb

from janasunani.analytics.monitoring import _atr, _flow, CORE_SCOPES, OFFICE_TABLE_TOP_N, PROXY_METRICS, UNRECORDED_FIELDS, _discards, _offices, _recording, _metric as published_metric, _pct, withhold_small_panel, refiling_summary, suppress_breakdown
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
    # The metric helpers inside panel builders (stage, share) emit ids too.
    emitted = set(re.findall(r'(?:_metric|stage|share)\(\s*"([a-z0-9-]+)"', source))
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
    # The frontend parser requires the key on every recorded panel, including
    # releases published before tables existed.
    assert all("tables" in p for p in response.json()["panels"])
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
          CASE WHEN i < 20 THEN 'Online' END AS mode,
          CASE WHEN i < 40 THEN 7 END AS category_id,
          CASE WHEN i < 10 THEN 'Scheme' END AS subcategory,
          CASE WHEN i < 25 THEN '11,22,33' END AS all_esc_user
        FROM range(40) r(i);
        CREATE TABLE scope_tickets AS SELECT ticket_no, created_on FROM complaints;
        CREATE TABLE action_history AS SELECT
          i AS id, 'T' || i AS ticket_no, TIMESTAMP '2024-08-02' AS action_taken_date,
          'Complaint Transfer' AS action_status
        FROM range(30) r(i);
    """)
    discards = {"metrics": [published_metric(
        "discard-reason-recognised", "Discards with a recognised reason", 50.0,
        unit="percent", numerator=10, denominator=20)]}
    atr = {"metrics": [
        published_metric("atr-replied", "ATR submitted", 75.0, unit="percent", numerator=30, denominator=40),
        published_metric("atr-standard-reason", "Send-backs with a standard reason", None, unit="percent", note="No ATR was sent back."),
    ]}
    metrics = {m["id"]: m for m in _recording(con, discards, atr)["metrics"]}

    assert (metrics["rec-entry"]["numerator"], metrics["rec-entry"]["denominator"]) == (20, 40)
    assert metrics["rec-classification"]["value"] == 100.0
    assert metrics["rec-classification"]["note"]  # only the current category
    assert metrics["rec-events"]["numerator"] == 30
    assert metrics["rec-scheme"]["numerator"] == 10
    # Whether review is required is read from the workflow chain.
    assert metrics["rec-review-required"]["numerator"] == 25
    assert (metrics["rec-atr"]["numerator"], metrics["rec-atr"]["label"]) == (30, "ATR request, receipt and closure events")
    # Another panel's unavailable figure stays unavailable, with its reason.
    assert metrics["rec-review-event"]["state"] == "unavailable"
    # The discard row is the discards panel's own figure, relabelled.
    assert (metrics["rec-discard-reason"]["numerator"], metrics["rec-discard-reason"]["label"]) == (10, "Discard reason")
    for metric_id, _label, unlocks in UNRECORDED_FIELDS:
        assert metrics[metric_id]["state"] == "unavailable"
        assert unlocks in metrics[metric_id]["reason"]


def _office_lake(districts: dict[str, int]) -> duckdb.DuckDBPyConnection:
    """One open, 40-day-old, transferred case per unit of each district's count."""
    con = duckdb.connect()
    con.execute("CREATE TABLE spec(district VARCHAR, n INTEGER)")
    con.executemany("INSERT INTO spec VALUES (?, ?)", list(districts.items()))
    con.execute("""
        CREATE TABLE complaints AS
          SELECT district || '-' || i AS ticket_no, district,
                 TIMESTAMP '2025-06-20' AS created_on, NULL::TIMESTAMP AS resolved_on,
                 'Pending' AS status, TIMESTAMP '2025-06-20' AS last_updated_on
          FROM spec, range(n) r(i);
        CREATE TABLE scope_tickets AS SELECT ticket_no, created_on FROM complaints;
        CREATE TABLE action_history AS
          SELECT row_number() OVER () AS id, * FROM (
            SELECT ticket_no, TIMESTAMP '2025-06-21' AS action_taken_date, 'Complaint Transfer' AS action_status FROM complaints
            UNION ALL
            -- the later action names the office now holding the case
            SELECT ticket_no, TIMESTAMP '2025-06-25', 'Forwarded' FROM complaints);
        CREATE TABLE acting_office_named AS
          SELECT id, CASE WHEN action_status='Forwarded' THEN 'Block Development Officer' ELSE 'District Collector' END AS role_name
          FROM action_history;
    """)
    return con


def test_offices_is_for_department_views_only():
    statewide = next(s for s in CORE_SCOPES if s.kind == "statewide")
    panel = _offices(_office_lake({"Puri": 12}), statewide)
    assert panel["state"] == "unavailable"


def test_offices_orders_by_workload_folds_small_rows_and_withholds_small_cells():
    department = next(s for s in CORE_SCOPES if s.kind == "department")
    # Twelve districts large enough to keep, one more beyond the top N, and
    # one under the minimum cell: both fold into "Other districts".
    districts = {f"D{k:02d}": 40 - k for k in range(OFFICE_TABLE_TOP_N + 1)} | {"Tiny": 4}
    panel = _offices(_office_lake(districts), department)
    by_district, by_office = panel["tables"]

    assert panel["denominator"]["value"] == sum(districts.values())
    labels = [row["label"] for row in by_district["rows"]]
    assert labels == [f"D{k:02d}" for k in range(OFFICE_TABLE_TOP_N)] + ["Other districts"]
    first = by_district["rows"][0]["values"]
    # Open, open 30+ days, no action 7+ days, filed in FY, transferred.
    assert first == [40, 100.0, 100.0, 40, 100.0]
    folded = by_district["rows"][-1]["values"]
    extra = 40 - OFFICE_TABLE_TOP_N
    assert folded[0] == extra + 4
    # The office holding the case is the one on its latest action.
    assert [row["label"] for row in by_office["rows"]] == ["Block Development Officer"]


def test_a_small_rate_cell_is_withheld_but_its_row_stays():
    rows = [("Puri", 50, 5, 0)]
    from janasunani.analytics.monitoring import _drilldown_rows
    assert _drilldown_rows(rows, [(0, None), (1, 0), (2, 0)], "Other") == [
        {"label": "Puri", "values": [50, None, 0.0]},
    ]


def test_atr_reads_review_from_the_assigned_workflow():
    # (kind, workflow chain, final status, [(office, status, remark, day)])
    cases = [
        # Three offices: the Collector reviews and passes the ATR on.
        ("reviewed", "1,2,3", "Disposed", [("BDO", "Replied", None, 2), ("Collector", "Replied", None, 4), ("CMO", "Disposed", None, 6)]),
        # Three offices, but closed straight after the field office's reply.
        ("skipped", "1,2,3", "Disposed", [("BDO", "Replied", None, 2), ("CMO", "Disposed", None, 3)]),
        # The reviewer's only act is sending it back: that is still review.
        ("sent_back", "1,2,3", "Disposed", [("BDO", "Replied", None, 2), ("Collector", "Reopen", "Required  more clarification.", 3),
                                             ("BDO", "Replied", None, 5), ("CMO", "Disposed", None, 7)]),
        # Collector -> BDO: no review required.
        ("direct", "1,2", "Disposed", [("BDO", "Replied", None, 2), ("Collector", "Disposed", None, 3)]),
        # An ATR waiting at the Collector at the snapshot.
        ("waiting", "1,2,3", "Pending", [("BDO", "Replied", None, 10)]),
        # No workflow recorded.
        ("none", "", "Pending", []),
    ]
    con = duckdb.connect()
    con.execute("CREATE TABLE complaints(ticket_no VARCHAR, created_on TIMESTAMP, status VARCHAR, resolved_on TIMESTAMP, all_esc_user VARCHAR)")
    con.execute("CREATE TABLE action_history(id INTEGER, ticket_no VARCHAR, action_taken_date TIMESTAMP, action_status VARCHAR, action_taken_remark VARCHAR)")
    con.execute("CREATE TABLE acting_office(id INTEGER, ticket_no VARCHAR, action_taken_date TIMESTAMP, action_status VARCHAR, code VARCHAR)")
    next_id = 0
    for kind, chain, status, steps in cases:
        for i in range(10):
            ticket = f"{kind}-{i}"
            resolved = "2025-07-10" if status == "Disposed" else None
            con.execute("INSERT INTO complaints VALUES (?, TIMESTAMP '2025-07-01' - INTERVAL 30 DAY, ?, ?, ?)", [ticket, status, resolved, chain])
            for office, action, remark, day in steps:
                next_id += 1
                when = f"2025-07-{day:02d}"
                con.execute("INSERT INTO action_history VALUES (?, ?, ?, ?, ?)", [next_id, ticket, when, action, remark])
                con.execute("INSERT INTO acting_office VALUES (?, ?, ?, ?, ?)", [next_id, ticket, when, action, office])
    con.execute("CREATE TABLE scope_tickets AS SELECT ticket_no, created_on FROM complaints")

    panel = _atr(con)
    metrics = {m["id"]: m for m in panel["metrics"]}
    def fraction(metric_id):
        return metrics[metric_id]["numerator"], metrics[metric_id]["denominator"]

    assert fraction("review-required") == (40, 50)       # four three-office kinds of five with a workflow
    assert fraction("atr-replied") == (50, 60)
    assert fraction("review-done") == (20, 30)            # reviewed + sent_back, of the closed required cases
    assert fraction("closed-without-review") == (10, 30)
    assert fraction("atr-sent-back") == (10, 50)
    assert fraction("atr-standard-reason") == (10, 10)
    assert metrics["atr-waiting"]["value"] == 10
    assert metrics["atr-wait"]["value"] == 20.0            # 30 July less 10 July
    assert panel["tables"][0]["rows"] == [{"label": "More clarification required", "values": [10]}]
    assert {metrics[m]["basis"] for m in ("review-done", "closed-without-review")} == {"proxy"}


def _flow_lake() -> duckdb.DuckDBPyConnection:
    """Ten of each path: discarded, no workflow, no ATR, skipped review,
    reviewed but open, and two that close (one needing no review)."""
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE shapes(kind VARCHAR, status VARCHAR, nodes INT, replied BOOL, required BOOL, reviewed BOOL, closed BOOL);
        INSERT INTO shapes VALUES
          ('discarded', 'Discard',  3, FALSE, TRUE,  FALSE, FALSE),
          ('no_flow',   'Pending',  0, FALSE, FALSE, FALSE, FALSE),
          ('no_atr',    'Pending',  3, FALSE, TRUE,  FALSE, FALSE),
          ('skipped',   'Disposed', 3, TRUE,  TRUE,  FALSE, TRUE),
          ('open',      'Pending',  3, TRUE,  TRUE,  TRUE,  FALSE),
          ('closed',    'Disposed', 3, TRUE,  TRUE,  TRUE,  TRUE),
          ('direct',    'Disposed', 2, TRUE,  FALSE, FALSE, TRUE);
        CREATE TABLE atr_cases AS SELECT kind || '-' || i AS ticket_no, nodes, required, replied, reviewed, closed
          FROM shapes, range(10) r(i);
        CREATE TABLE complaints AS SELECT kind || '-' || i AS ticket_no, status FROM shapes, range(10) r(i);
    """)
    return con


def test_flow_stages_nest_and_say_when_repeats_are_not_removed():
    panel = _flow(_flow_lake(), None)
    stages = {m["id"]: m for m in panel["metrics"]}
    assert [m["id"] for m in panel["metrics"]] == [
        "flow-filed", "flow-kept", "flow-unique", "flow-routed", "flow-atr", "flow-reviewed", "flow-closed"]
    assert stages["flow-unique"]["state"] == "unavailable"
    # Each stage is (count, the stage before): without dedup, routing follows "kept".
    got = {k: (m["numerator"], m["denominator"]) for k, m in stages.items() if m["state"] == "recorded"}
    assert got == {
        "flow-filed": (70, None), "flow-kept": (60, 70), "flow-routed": (50, 60),
        "flow-atr": (40, 50), "flow-reviewed": (30, 40), "flow-closed": (20, 30),
    }


def test_flow_keeps_one_filing_per_duplicate_group(tmp_path):
    # The ten 'closed' filings are five people filing twice each.
    groups = tmp_path / "groups.csv"
    groups.write_text("ticket_no,duplicate_group_id,group_size\n" + "".join(
        f"closed-{i},g{i // 2},2\n" for i in range(10)))
    stages = {m["id"]: m for m in _flow(_flow_lake(), groups)["metrics"]}
    assert (stages["flow-unique"]["numerator"], stages["flow-unique"]["denominator"]) == (55, 60)
    assert stages["flow-unique"]["basis"] == "proxy"
    assert stages["flow-closed"]["numerator"] == 15
