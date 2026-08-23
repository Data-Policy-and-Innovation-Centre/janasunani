"""Unified benchmark report Table 2 — generator and CLI."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from janasunani.config import DEMO_SLICE_LABEL, DEMO_SLICE_SIZE, DEMO_SLICE_GROUPS
from janasunani.evaluation import benchmark_report
from janasunani.evaluation import dsi_baselines as dsi
from janasunani.evaluation import pricing


def test_dsi_baselines_frozen():
    assert dsi.REFERENCE_ONLY is True
    assert dsi.FORMAT_CLASSIFIER_AVG_ACCURACY == pytest.approx(0.7571)
    assert dsi.OCR_ALL_THREE_PASS_RATE == pytest.approx(0.7789)
    assert dsi.PII_ANY_OVERLAP_RECALL == pytest.approx(0.8056)
    assert dsi.PII_EXACT_SPAN_RECALL == pytest.approx(0.50)
    assert dsi.PAGE_TYPE_VIT_ACCURACY == pytest.approx(0.67)
    assert dsi.CATEGORIZER_MURIL_ACCURACY == pytest.approx(0.7104)
    assert dsi.SUMMARIZER_BART_USEFULNESS == pytest.approx(1.9)
    assert dsi.EFFICIENCY_SECONDS_PER_500_TOKENS_A100 == {
        "format": 4.53,
        "ocr": 18.86,
        "pii": 4.67,
        "page_type": 2.49,
        "summarizer": 1.84,
        "categorizer": 2.82,
    }
    for k, v in dsi.DSI_BASELINES.items():
        assert v["reference_only"] is True, k
        assert dsi.REFERENCE_ONLY_BY_STAGE[k] is True


def test_build_report_always_has_dsi_rows():
    report = benchmark_report.build_report()
    assert report["reference_only"] is True
    assert report["slice"] == DEMO_SLICE_LABEL
    assert report["slice_size"] == DEMO_SLICE_SIZE
    assert report["slice_groups"] == DEMO_SLICE_GROUPS
    rows = report["rows"]
    assert len(rows) >= 8
    for r in rows:
        for k in ("stage", "metric", "dsi_reference", "our_measurement", "status", "dsi_reference_only"):
            assert k in r, f"missing {k} in row {r.get('stage')}"
    values = []
    for r in rows:
        v = r["dsi_reference"]
        if isinstance(v, float):
            values.append(v)
        elif isinstance(v, dict):
            values.extend([x for x in v.values() if isinstance(x, float)])
    assert any(abs(v - 0.7571) < 1e-9 for v in values)
    assert any(abs(v - 0.7789) < 1e-9 for v in values)
    assert any(abs(v - 0.8056) < 1e-9 for v in values)
    assert any(abs(v - 0.67) < 1e-9 for v in values)


def test_missing_inputs_are_not_measured():
    report = benchmark_report.build_report()
    pii_rows = [r for r in report["rows"] if r["stage"] == "PII redaction"]
    assert any(r["our_measurement"] == benchmark_report.NOT_MEASURED for r in pii_rows)
    sarvam_rows = [r for r in report["rows"] if r["stage"] == "Sarvam Vision"]
    assert all(r["our_measurement"] == benchmark_report.NOT_MEASURED for r in sarvam_rows)


def test_build_report_with_injected_measurements():
    pii = {"english": {"recall": 0.44}}
    sarvam = {
        "category": {
            "n_tickets": 40,
            "pipeline_accuracy": 0.60,
            "sarvam_accuracy": 0.65,
            "difference": 0.05,
            "se": 0.03,
            "ci_low": -0.01,
            "ci_high": 0.11,
            "n_clusters": 40,
        },
        "transcription_divergence": {"rate": 0.35, "se": 0.04, "ci_low": 0.27, "ci_high": 0.43, "n": 100, "n_clusters": 40},
    }
    spam = {"slice": "Sambalpur/2024", "prevalence": 0.07, "total": 1000, "flagged": 70}
    latency = {
        "stages": {
            "format": {"mean_seconds": 0.02, "se_seconds": 0.005, "n_clusters": 20, "p50": 0.019, "p95": 0.03},
            "ocr": {"mean_seconds": 0.8, "se_seconds": 0.1, "n_clusters": 20, "p50": 0.75, "p95": 1.1},
            "pii": {"mean_seconds": 0.12, "se_seconds": 0.02, "n_clusters": 20, "p50": 0.11, "p95": 0.18},
        },
        "e2e": {"mean_seconds": 1.5, "se_seconds": 0.15, "n_clusters": 20, "p50": 1.4, "p95": 1.9},
    }
    report = benchmark_report.build_report(
        pii_result=pii, sarvam_result=sarvam, spam_result=spam, latency_result=latency
    )
    pii_rows = [r for r in report["rows"] if r["stage"] == "PII redaction" and r["metric"] == "any-overlap recall"]
    assert pii_rows[0]["our_measurement"] == pii
    assert pii_rows[0]["status"] == "measured"
    cat_rows = [r for r in report["rows"] if r["metric"] == "category accuracy difference (Extract vs pipeline)"]
    assert cat_rows[0]["our_measurement"] == sarvam["category"]
    assert cat_rows[0]["status"] == "measured"
    fmt_rows = [r for r in report["rows"] if r["stage"] == "Format classifier"]
    assert fmt_rows[0]["latency"] == latency["stages"]["format"]
    assert report["pricing"]["vision_digitise_rupees_per_page"] == 0.50
    assert report["pricing"]["sarvam_105b_input_rupees_per_million_tokens"] == 29.28


def test_dedup_row_always_measured_from_config():
    report = benchmark_report.build_report()
    dedup_rows = [r for r in report["rows"] if r["stage"] == "Duplicate detection"]
    assert len(dedup_rows) == 1
    m = dedup_rows[0]["our_measurement"]
    assert m["total_signatures"] == DEMO_SLICE_SIZE
    assert m["duplicate_groups"] == DEMO_SLICE_GROUPS
    assert dedup_rows[0]["status"] == "measured"


def test_cost_appendix_matches_pricing():
    report = benchmark_report.build_report()
    rc = report["cost"]["rate_card"]
    assert rc["vision_digitise_rupees_per_page"] == pricing.VISION_DIGITISE_RUPEES_PER_PAGE
    assert rc["vision_extract_rupees_per_page"] == pricing.VISION_EXTRACT_RUPEES_PER_PAGE
    assert rc["vision_both_rupees_per_page"] == pricing.VISION_BOTH_RUPEES_PER_PAGE
    assert rc["sarvam_105b_input_rupees_per_million_tokens"] == pricing.SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS
    assert report["cost"]["per_document_formula"]
    assert "pages" in report["cost"]["per_document_formula"]


def test_render_markdown_contains_required_sections():
    report = benchmark_report.build_report()
    md = benchmark_report.render_markdown(report)
    assert "Benchmark Table 2" in md
    assert "DSI reference" in md
    assert "Our measurement" in md
    assert "0.7571" in md or "75.71%" in md
    assert "0.7789" in md or "77.89%" in md
    assert "not_measured" in md
    assert "Vision digitise" in md
    assert "₹0.50" in md
    assert "₹1.00" in md
    assert "₹1.50" in md
    assert "₹29.28" in md
    assert "₹73.20" in md
    assert "Local pipeline" in md
    assert "Latency" in md
    assert DEMO_SLICE_LABEL in md


def test_render_markdown_with_latency_table():
    latency = {
        "stages": {
            "format": {"mean_seconds": 0.02, "se_seconds": 0.005, "n_clusters": 20, "p50": 0.019, "p95": 0.03},
        },
        "e2e": {"mean_seconds": 1.5, "se_seconds": 0.15, "n_clusters": 20, "p50": 1.4, "p95": 1.9},
    }
    report = benchmark_report.build_report(latency_result=latency)
    md = benchmark_report.render_markdown(report)
    assert "mean" in md.lower()
    assert "p50" in md.lower()


def test_live_latency_names_flow_into_historical_report_labels():
    latency = {
        "stages": {
            "redact": {"n": 4, "mean_seconds": 0.02, "se_seconds": 0.005, "n_clusters": 2, "p50": 0.019, "p90": 0.025, "p95": 0.03, "throughput_per_second": 50.0},
            "categorize": {"n": 4, "mean_seconds": 0.4, "se_seconds": 0.05, "n_clusters": 2, "p50": 0.39, "p90": 0.48, "p95": 0.5, "throughput_per_second": 2.5},
            "summarize": {"n": 4, "mean_seconds": 1.2, "se_seconds": 0.1, "n_clusters": 2, "p50": 1.1, "p90": 1.4, "p95": 1.5, "throughput_per_second": 0.83},
            "e2e": {"n": 4, "mean_seconds": 1.8, "se_seconds": 0.2, "n_clusters": 2, "p50": 1.7, "p90": 2.1, "p95": 2.2, "throughput_per_second": 0.56},
        }
    }
    report = benchmark_report.build_report(latency_result=latency)
    rows = {row["stage"]: row for row in report["rows"]}
    pii_overlap = next(
        row
        for row in report["rows"]
        if row["stage"] == "PII redaction" and row["metric"] == "any-overlap recall"
    )
    assert pii_overlap["latency"] == latency["stages"]["redact"]
    assert rows["Categorizer"]["latency"] == latency["stages"]["categorize"]
    assert rows["Summarizer"]["latency"] == latency["stages"]["summarize"]
    rendered = benchmark_report.render_markdown(report)
    assert "| Stage | n |" in rendered
    assert "p90 (s)" in rendered
    assert "throughput/s" in rendered


def test_live_pii_latency_replaces_empty_batch_placeholder():
    latency = {
        "stages": {
            "pii": {"n": 0, "mean_seconds": 0.0},
            "redact": {"n": 2, "mean_seconds": 0.2},
        }
    }

    report = benchmark_report.build_report(latency_result=latency)
    pii_row = next(row for row in report["rows"] if row["stage"] == "PII redaction")

    assert pii_row["latency"] == latency["stages"]["redact"]


def test_write_outputs(tmp_path: Path):
    report = benchmark_report.build_report()
    paths = benchmark_report.write_outputs(report, tmp_path)
    assert paths["json"].exists()
    assert paths["markdown"].exists()
    data = json.loads(paths["json"].read_text())
    assert data["reference_only"] is True
    assert data["slice"] == DEMO_SLICE_LABEL
    assert "rows" in data
    md = paths["markdown"].read_text()
    assert "Benchmark Table 2" in md


def test_validate_report_on_generated_dict():
    report = benchmark_report.build_report()
    errors = benchmark_report.validate_report(report)
    assert errors == [], f"unexpected validation errors: {errors}"


def test_validate_report_detects_bad_reference_flag():
    report = benchmark_report.build_report()
    report["reference_only"] = False
    errors = benchmark_report.validate_report(report)
    assert any("reference_only" in e for e in errors)


def test_validate_report_detects_wrong_pricing():
    report = benchmark_report.build_report()
    report["pricing"]["vision_digitise_rupees_per_page"] = 0.99
    errors = benchmark_report.validate_report(report)
    assert any("vision_digitise" in e for e in errors)


def test_validate_report_detects_mutated_dsi():
    report = benchmark_report.build_report()
    for r in report["rows"]:
        if r["stage"] == "Format classifier":
            r["dsi_reference"] = 0.99
            break
    errors = benchmark_report.validate_report(report)
    assert any("format" in e.lower() for e in errors)


def test_cli_check_without_file(tmp_path: Path):
    rc = benchmark_report.main(["--out", str(tmp_path), "--check"])
    assert rc == 1


def test_cli_generate_then_check(tmp_path: Path):
    rc = benchmark_report.main(["--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "table2.json").exists()
    assert (tmp_path / "table2.md").exists()
    rc = benchmark_report.main(["--out", str(tmp_path), "--check"])
    assert rc == 0


def test_cli_check_detects_corrupt_json(tmp_path: Path):
    benchmark_report.main(["--out", str(tmp_path)])
    p = tmp_path / "table2.json"
    data = json.loads(p.read_text())
    data["pricing"]["vision_extract_rupees_per_page"] = 5.0
    p.write_text(json.dumps(data))
    rc = benchmark_report.main(["--out", str(tmp_path), "--check"])
    assert rc == 1


def test_cli_check_detects_corrupt_markdown(tmp_path: Path):
    # #212: --check must validate the Markdown artifact too, not just
    # confirm table2.md exists. A corrupt/stale Markdown must fail --check
    # even though table2.json is untouched and schema-valid.
    rc = benchmark_report.main(["--out", str(tmp_path)])
    assert rc == 0
    md_path = tmp_path / "table2.md"
    md_path.write_text("CORRUPT")
    rc = benchmark_report.main(["--out", str(tmp_path), "--check"])
    assert rc == 1


def test_cli_check_passes_when_markdown_matches_json(tmp_path: Path):
    rc = benchmark_report.main(["--out", str(tmp_path)])
    assert rc == 0
    rc = benchmark_report.main(["--out", str(tmp_path), "--check"])
    assert rc == 0


def test_cli_check_fails_on_stale_report_without_allow_stale(tmp_path: Path):
    # #214: --check must fail (nonzero) on a >7d-old generated_at unless
    # --allow-stale is passed — a warning-only check cannot enforce the
    # rehearsal freeze window.
    rc = benchmark_report.main(["--out", str(tmp_path)])
    assert rc == 0
    json_path = tmp_path / "table2.json"
    md_path = tmp_path / "table2.md"
    data = json.loads(json_path.read_text())
    stale_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).isoformat()
    data["generated_at"] = stale_ts
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
    # Regenerate the Markdown from the same stale data so only the
    # staleness check (not the #212 Markdown check) is under test here.
    md_path.write_text(benchmark_report.render_markdown(data))

    rc = benchmark_report.main(["--out", str(tmp_path), "--check"])
    assert rc == 1

    rc = benchmark_report.main(["--out", str(tmp_path), "--check", "--allow-stale"])
    assert rc == 0


def _regenerate_with(tmp_path: Path, mutate) -> None:
    """Write table2.json through *mutate*, keeping the Markdown consistent."""
    json_path = tmp_path / "table2.json"
    data = json.loads(json_path.read_text())
    mutate(data)
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
    (tmp_path / "table2.md").write_text(benchmark_report.render_markdown(data))


def test_check_fails_when_generated_at_is_missing(tmp_path: Path):
    """Codex finding on #226: the freshness gate must not be skippable.

    A stale artifact could otherwise pass --check by deleting the field the
    gate reads and regenerating the Markdown around it, which is exactly how
    an out-of-date table reaches a demo unnoticed.
    """
    assert benchmark_report.main(["--out", str(tmp_path)]) == 0
    _regenerate_with(tmp_path, lambda d: d.pop("generated_at", None))

    assert benchmark_report.main(["--out", str(tmp_path), "--check"]) == 1
    # The escape hatch still works, and says what it is not checking.
    assert benchmark_report.main(["--out", str(tmp_path), "--check", "--allow-stale"]) == 0


@pytest.mark.parametrize("bad", ["", "not-a-timestamp", "2026-13-45T99:99:99", 12345])
def test_check_fails_when_generated_at_is_unparseable(tmp_path: Path, bad):
    assert benchmark_report.main(["--out", str(tmp_path)]) == 0
    _regenerate_with(tmp_path, lambda d: d.__setitem__("generated_at", bad))

    assert benchmark_report.main(["--out", str(tmp_path), "--check"]) == 1
    assert benchmark_report.main(["--out", str(tmp_path), "--check", "--allow-stale"]) == 0


def test_a_fresh_report_still_passes_check(tmp_path: Path):
    """The guard must not turn a good report red."""
    assert benchmark_report.main(["--out", str(tmp_path)]) == 0
    assert benchmark_report.main(["--out", str(tmp_path), "--check"]) == 0


def test_cli_latency_path(tmp_path: Path):
    latency = {
        "stages": {
            "format": {"mean_seconds": 0.01, "se_seconds": 0.002, "n_clusters": 5, "p50": 0.01, "p95": 0.02},
        },
        "e2e": {"mean_seconds": 0.5, "se_seconds": 0.05, "n_clusters": 5, "p50": 0.48, "p95": 0.6},
    }
    lp = tmp_path / "latency.json"
    lp.write_text(json.dumps(latency))
    out = tmp_path / "out"
    rc = benchmark_report.main(["--out", str(out), "--latency", str(lp)])
    assert rc == 0
    data = json.loads((out / "table2.json").read_text())
    fmt = [r for r in data["rows"] if r["stage"] == "Format classifier"][0]
    assert fmt["latency"]["mean_seconds"] == pytest.approx(0.01)
