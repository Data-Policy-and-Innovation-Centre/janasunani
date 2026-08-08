"""Tests for Unit 4b — workload / spike / themes intelligence.

Validates schema/arithmetic, Unavailable on stale/missing, forbids extra columns,
and that workload/spike numbers correctly use dedup (not filings alone).
Also ensures serving path never queries the lake.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")

from fastapi.testclient import TestClient  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.intelligence import ArtifactSupervisorProvider  # noqa: E402

_WORKLOAD_FIELDS = (
    "slice_district",
    "slice_category",
    "slice_period",
    "total_filings",
    "distinct_problems",
    "duplicate_adjustment",
    "source_name",
    "source_snapshot_id",
)
_SPIKE_FIELDS = (
    "slice_district",
    "slice_category",
    "slice_period",
    "filings",
    "distinct_problems",
    "distinct_citizens",
    "source_name",
    "source_snapshot_id",
    "interpretation",
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

DEDUP_SOURCE = "oltp:complaints+grievance_redactions"
FAKE_DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def _write_closure(tmp_path: Path):
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
    path = tmp_path / "closure_recording_no_action.csv"
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=list(_CLOSURE_FIELDS))
        w.writeheader()
        w.writerow(row)
    return path


def _write_workload(tmp_path: Path, digest: str = FAKE_DIGEST, extra: tuple[str, ...] = (), overrides: dict | None = None):
    row = {
        "slice_district": "Sambalpur",
        "slice_category": "all",
        "slice_period": "2024",
        "total_filings": "10",
        "distinct_problems": "7",
        "duplicate_adjustment": "3",
        "source_name": DEDUP_SOURCE,
        "source_snapshot_id": digest,
    }
    if overrides:
        row.update(overrides)
    fields = (*_WORKLOAD_FIELDS, *extra)
    if extra:
        for e in extra:
            row[e] = "evil"
    path = tmp_path / "workload.csv"
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=list(fields))
        w.writeheader()
        w.writerow(row)
    return path


def _write_spike(tmp_path: Path, digest: str = FAKE_DIGEST, extra: tuple[str, ...] = (), overrides: dict | None = None):
    row = {
        "slice_district": "Sambalpur",
        "slice_category": "Water",
        "slice_period": "2024-03-04",
        "filings": "20",
        "distinct_problems": "5",
        "distinct_citizens": "15",
        "source_name": DEDUP_SOURCE,
        "source_snapshot_id": digest,
        "interpretation": "20 filings, 5 problems, 15 citizens. Lift 3.0x vs trailing mean.",
    }
    if overrides:
        row.update(overrides)
    fields = (*_SPIKE_FIELDS, *extra)
    if extra:
        for e in extra:
            row[e] = "evil"
    path = tmp_path / "spike.csv"
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=list(fields))
        w.writeheader()
        w.writerow(row)
    return path


def test_workload_is_served_with_dedup_counts(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path)
    _write_spike(tmp_path)
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.workload.provenance.state == "recorded"
    assert dash.workload.total_filings.value == 10
    assert dash.workload.distinct_problems.value == 7
    assert dash.workload.duplicate_adjustment.value == 3
    assert dash.workload.distinct_problems.value < dash.workload.total_filings.value
    assert dash.workload.duplicate_adjustment.value == dash.workload.total_filings.value - dash.workload.distinct_problems.value
    assert dash.spike.provenance.state == "recorded"
    assert dash.spike.counts[0].value == 20
    assert dash.spike.counts[1].value == 5
    assert dash.spike.counts[2].value == 15
    assert dash.spike.counts[1].value < dash.spike.counts[0].value


def test_workload_forbids_extra_columns(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path, extra=("grievance",))
    _write_spike(tmp_path)
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.workload.provenance.state == "unavailable"
    serialized = json.dumps(dash.model_dump(mode="json", by_alias=True)).lower()
    assert '"grievance"' not in serialized
    assert "evil" not in serialized


def test_spike_forbids_extra_columns(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path)
    _write_spike(tmp_path, extra=("identity_key",))
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.spike.provenance.state == "unavailable"


def test_workload_fails_on_bad_arithmetic(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path, overrides={"duplicate_adjustment": "2"})
    _write_spike(tmp_path)
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.workload.provenance.state == "unavailable"


def test_spike_fails_on_bad_arithmetic(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path)
    _write_spike(tmp_path, overrides={"distinct_citizens": "25"})
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.spike.provenance.state == "unavailable"


def test_digest_mismatch_fails_loudly(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path, digest=FAKE_DIGEST)
    _write_spike(tmp_path, digest=OTHER_DIGEST)
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.workload.provenance.state == "unavailable"
    assert dash.spike.provenance.state == "unavailable"
    assert "digest" in dash.workload.provenance.reason.lower()
    assert "digest" in dash.spike.provenance.reason.lower()


def test_missing_aggregates_are_unavailable(tmp_path):
    _write_closure(tmp_path)
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.workload.provenance.state == "unavailable"
    assert dash.spike.provenance.state == "unavailable"


def test_circular_mismatch_is_unavailable(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path)
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.workload.provenance.state == "recorded"
    assert dash.spike.provenance.state == "unavailable"


def test_workload_and_spike_numbers_use_dedup_not_filings(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path, overrides={"total_filings": "100", "distinct_problems": "40", "duplicate_adjustment": "60"})
    _write_spike(tmp_path, overrides={"filings": "50", "distinct_problems": "10", "distinct_citizens": "30"})
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.workload.distinct_problems.value == 40
    assert dash.workload.distinct_problems.value != dash.workload.total_filings.value
    assert dash.spike.counts[1].value == 10
    assert dash.spike.counts[1].value != dash.spike.counts[0].value
    assert dash.spike.counts[2].value == 30
    client = TestClient(create_app(supervisor=ArtifactSupervisorProvider(tmp_path)))
    resp = client.get("/supervisor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workload"]["totalFilings"]["value"] == 100
    assert body["workload"]["distinctProblems"]["value"] == 40
    assert body["spike"]["counts"][1]["value"] == 10
    assert body["spike"]["counts"][2]["value"] == 30


def test_serving_does_not_import_lake(tmp_path):
    import janasunani.serving.intelligence as mod
    import inspect

    source = inspect.getsource(mod)
    assert "janasunani.olap.lake" not in source
    assert "read_parquet" not in source
    assert "duckdb" not in source.lower()


def test_supervisor_endpoint_uses_camel_case_for_workload_spike(tmp_path):
    _write_closure(tmp_path)
    _write_workload(tmp_path)
    _write_spike(tmp_path)
    client = TestClient(create_app(supervisor=ArtifactSupervisorProvider(tmp_path)))
    resp = client.get("/supervisor")
    assert resp.status_code == 200
    body = resp.json()
    assert "totalFilings" in body["workload"]
    assert "distinctProblems" in body["workload"]
    assert "duplicateAdjustment" in body["workload"]
    assert "slice" in body["workload"]
    assert body["workload"]["slice"]["district"] == "Sambalpur"
    assert "counts" in body["spike"]
    assert len(body["spike"]["counts"]) == 3


def test_closure_still_served_when_workload_spike_missing(tmp_path):
    _write_closure(tmp_path)
    dash = ArtifactSupervisorProvider(tmp_path).dashboard()
    assert dash.closure.provenance.state == "recorded"
    assert dash.workload.provenance.state == "unavailable"
    assert dash.spike.provenance.state == "unavailable"
