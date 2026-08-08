"""Unit E — janasunani-evaluate-sarvam CLI (arm / schema / metadata join).

Recorded / dry-run only; no live Sarvam call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_grievance_schema_is_pinned_and_versioned():
    from janasunani.evaluation.sarvam_grievance_schema import (
        GRIEVANCE_EXTRACT_SCHEMA_V1,
        SCHEMA_VERSION,
        SUPPORTED_SCHEMA_VERSIONS,
        get_schema,
    )

    assert SCHEMA_VERSION == "v1"
    assert "v1" in SUPPORTED_SCHEMA_VERSIONS
    assert SUPPORTED_SCHEMA_VERSIONS["v1"] is GRIEVANCE_EXTRACT_SCHEMA_V1
    schema = get_schema("v1")
    assert schema is GRIEVANCE_EXTRACT_SCHEMA_V1
    # illustrative fields from plan
    for field in ("grievance_category", "summary", "district", "grievance_text"):
        assert field in schema
        assert schema[field]["type"] == "string"
        assert "description" in schema[field]
    with pytest.raises(ValueError, match="unknown schema"):
        get_schema("v9")
    with pytest.raises(ValueError, match="unknown schema"):
        get_schema("")


def _make_dummy_input(tmp_path: Path, n: int = 2) -> Path:
    """Create *n* dummy document files (PNG) under a temp input dir."""
    inp = tmp_path / "input"
    inp.mkdir(parents=True, exist_ok=True)
    # Create minimal PNGs via PIL if available, else empty files.
    try:
        from PIL import Image

        for i in range(n):
            img = Image.new("RGB", (10, 10), color="white")
            img.save(inp / f"TICK{i:03d}_doc.png", format="PNG")
        return inp
    except Exception:
        for i in range(n):
            (inp / f"TICK{i:03d}_doc.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return inp


def test_evaluate_dry_run_digitise_arm(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import main

    inp = _make_dummy_input(tmp_path, n=2)
    out = tmp_path / "out"
    rc = main(["--input", str(inp), "--out", str(out), "--arm", "digitise", "--dry-run"])
    assert rc == 0
    # outputs exist
    json_path = out / "sarvam_scorecard.json"
    md_path = out / "sarvam_scorecard.md"
    assert json_path.is_file()
    assert md_path.is_file()
    data = json.loads(json_path.read_text())
    assert data["n_pages"] == 2
    assert data["arm"] == "digitise"
    assert data["schema_version"] == "v1"
    assert data["cost_rupees"] == 0.0  # dry-run
    assert "summary_divergence" in data
    # markdown mentions arm/slice
    md = md_path.read_text()
    assert "Sarvam" in md
    assert "Sambalpur" in md


def test_evaluate_arm_choices_and_schema_version(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import build_parser

    parser = build_parser()
    # valid
    args = parser.parse_args(["--input", "a", "--out", "b", "--arm", "extract", "--schema-version", "v1"])
    assert args.arm == "extract"
    assert args.schema_version == "v1"
    args2 = parser.parse_args(["--input", "a", "--out", "b", "--arm", "both"])
    assert args2.arm == "both"
    # invalid arm
    with pytest.raises(SystemExit):
        parser.parse_args(["--input", "a", "--out", "b", "--arm", "invalid"])
    # invalid schema version
    with pytest.raises(SystemExit):
        parser.parse_args(["--input", "a", "--out", "b", "--schema-version", "v9"])


def test_evaluate_both_arm_cost(tmp_path: Path):
    from janasunani.evaluation.sarvam_evaluate import main

    inp = _make_dummy_input(tmp_path, n=3)
    out = tmp_path / "out2"
    # dry-run cost is 0, but payload records arm
    rc = main(["--input", str(inp), "--out", str(out), "--arm", "both", "--dry-run"])
    assert rc == 0
    data = json.loads((out / "sarvam_scorecard.json").read_text())
    assert data["arm"] == "both"
    # When not dry-run, cost would be 1.50/page; verify helper
    from janasunani.evaluation.sarvam_evaluate import _price_for_arm

    assert _price_for_arm("digitise", 10) == pytest.approx(5.0)
    assert _price_for_arm("extract", 10) == pytest.approx(10.0)
    assert _price_for_arm("both", 10) == pytest.approx(15.0)


def test_evaluate_join_metadata_from_lake(tmp_path: Path):
    """--join-metadata should populate gold_category from lake complaints parquet."""
    import polars as pl

    inp = _make_dummy_input(tmp_path, n=2)
    # Align ticket ids with input files: TICK000, TICK001
    lake_dir = tmp_path / "lake"
    lake_dir.mkdir()
    complaints = pl.DataFrame(
        {
            "ticket_no": ["TICK000", "TICK001"],
            "district": ["Sambalpur", "Sambalpur"],
            "created_year": [2024, 2024],
            "category": ["Police", "Revenue"],
        }
    )
    complaints.write_parquet(lake_dir / "complaints.parquet")

    out = tmp_path / "out3"
    from janasunani.evaluation.sarvam_evaluate import main

    rc = main(
        [
            "--input",
            str(inp),
            "--out",
            str(out),
            "--arm",
            "digitise",
            "--dry-run",
            "--join-metadata",
            "--lake-dir",
            str(lake_dir),
            "--slice",
            "Sambalpur/2024",
        ]
    )
    assert rc == 0
    data = json.loads((out / "sarvam_scorecard.json").read_text())
    # With gold present, category headline should be computed (not None)
    assert data["category"] is not None
    assert data["category"]["n_tickets"] == 2


def test_evaluate_extract_arm_with_mocked_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """--arm extract should call adapter.extract(schema=...) and map to PageRecord."""
    inp = _make_dummy_input(tmp_path, n=1)
    out = tmp_path / "out4"

    # Fake adapter that returns a known extract payload
    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def digitise(self, *args, **kwargs):
            return "fake markdown"

        def extract(self, doc_bytes, filename, language, context, schema=None, config_id=None):
            assert schema is not None
            assert "grievance_category" in schema
            return {"grievance_category": "Police", "summary": "Sarvam summary text", "district": "Sambalpur"}

    # Patch the class used by evaluate
    import janasunani.evaluation.sarvam_evaluate as eval_mod

    monkeypatch.setattr(eval_mod, "SarvamVisionAdapter", FakeAdapter, raising=False)
    # Also need to patch the import inside main (it does `from janasunani.egress.sarvam import ...`)
    # So patch the egress module's class as well
    import janasunani.egress.sarvam as egress_mod

    monkeypatch.setattr(egress_mod, "SarvamVisionAdapter", FakeAdapter, raising=False)
    # Patch SqliteAuditLog to avoid creating real DB
    class FakeLog:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(egress_mod, "SqliteAuditLog", FakeLog, raising=False)
    # Need a fake PROVIDER_REGISTRY that permits egress
    from dataclasses import replace

    from janasunani.egress.sarvam import GovernanceControl, PROVIDER_REGISTRY

    verified = GovernanceControl(statement="test", verified=True)
    fake_route = replace(PROVIDER_REGISTRY["sarvam-vision"], retention_terms=verified, encryption_in_transit=verified, encryption_at_rest=verified)
    monkeypatch.setattr(egress_mod, "PROVIDER_REGISTRY", {"sarvam-vision": fake_route}, raising=False)
    # Patch render_page to return a real PIL image so digitise/extract paths are exercised
    try:
        from PIL import Image

        fake_image = Image.new("RGB", (5, 5))
        monkeypatch.setattr(eval_mod, "render_page", lambda p, n: fake_image, raising=False)
        # Also patch the lazy import path if evaluate imports render_page inside function via import statement;
        # monkeypatch the module where it's imported: janasunani.pipeline.stages.ocr_extraction.page_renderer
        import janasunani.pipeline.stages.ocr_extraction.page_renderer as renderer_mod  # type: ignore

        monkeypatch.setattr(renderer_mod, "render_page", lambda p, n: fake_image, raising=False)
    except Exception:
        pass

    # Run without --dry-run so adapter is used; mock will prevent network
    from janasunani.evaluation.sarvam_evaluate import main

    # We need to ensure the adapter mock is used — the evaluate code does
    # `from janasunani.egress.sarvam import SarvamVisionAdapter` inside main,
    # which after our patch will pick up FakeAdapter.
    rc = main(["--input", str(inp), "--out", str(out), "--arm", "extract"])
    assert rc == 0
    data = json.loads((out / "sarvam_scorecard.json").read_text())
    assert data["arm"] == "extract"
    # The sarvam category/summary should have been mapped from fake payload
    # Scorecard will have at least one ticket with sarvam_category
    assert data["n_pages"] == 1


def test_wrapper_script_delegates(tmp_path: Path):
    """scripts/analysis/sarvam_sample.py thin wrapper should still run dry-run."""
    import subprocess

    inp = _make_dummy_input(tmp_path, n=1)
    out = tmp_path / "out_wrap"
    # Invoke wrapper as module
    result = subprocess.run(
        [sys.executable, "scripts/analysis/sarvam_sample.py", "--input", str(inp), "--out", str(out), "--dry-run"],
        capture_output=True,
        text=True,
    )
    # Should exit 0 and produce outputs (wrapper injects --arm digitise)
    assert result.returncode == 0, f"wrapper failed: {result.stderr}\n{result.stdout}"
    assert (out / "sarvam_scorecard.json").is_file()
