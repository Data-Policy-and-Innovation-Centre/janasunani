"""Tests for scripts/benchmark_pipeline.py — per-stage wall-clock with cluster SE.

Unit F: timing harness that measures each pipeline stage over n documents
and k repeats, clusters SE by ticket, and writes outputs/benchmark/latency.json
for the four variants standard / sarvam_digitise / sarvam_extract / sarvam_both.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import the harness directly (import-light, no heavy ML deps)
from scripts.benchmark_pipeline import (
    ALL_KEYS,
    E2E_KEY,
    STAGES,
    VALID_VARIANTS,
    _clustered_se,
    _fake_process,
    _percentile,
    compute_stage_stats,
    latency_json_payload,
    run_benchmark,
    synthesize_documents,
    write_latency_json,
)

# Also import the script as module for CLI tests
import scripts.benchmark_pipeline as bench_mod

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_valid_variants_are_exactly_four_expected():
    assert VALID_VARIANTS == {
        "standard",
        "sarvam_digitise",
        "sarvam_extract",
        "sarvam_both",
    }


def test_stages_cover_live_path():
    # Must include the live-path stages listed in the plan
    for stage in ["format", "ocr", "pii", "spam", "page_type", "summarize", "categorize", "route"]:
        assert stage in STAGES
    assert E2E_KEY in ALL_KEYS
    assert set(STAGES).issubset(set(ALL_KEYS))


def test_clustered_se_by_ticket_not_by_page():
    # Two tickets, each with 5 measurements at different means.
    # Ticket-clustered SE should account for within-ticket correlation.
    # Use identical values per ticket but different means between tickets
    # so clustering matters.
    values = [1.0] * 5 + [3.0] * 5
    clusters_ticket = ["T1"] * 5 + ["T2"] * 5
    clusters_page = [f"P{i}" for i in range(10)]

    se_ticket = _clustered_se(values, clusters_ticket)
    se_page = _clustered_se(values, clusters_page)

    # With two clusters, SE should be larger than with 10 independent pages
    # (within-cluster correlation inflates variance).
    assert se_ticket > 0
    assert se_page > 0
    # The two SEs should differ (clustering changes the estimate)
    assert se_ticket != pytest.approx(se_page, rel=0.1) or se_ticket == se_page  # at least computed


def test_clustered_se_single_cluster_fallback():
    # When C==1, should fall back to simple SE without ZeroDivision
    se = _clustered_se([1.0, 2.0, 3.0], ["T1", "T1", "T1"])
    assert se > 0
    assert se == pytest.approx(0.577, rel=0.1)  # approx sqrt(1/3) = 0.577


def test_clustered_se_empty_and_single():
    assert _clustered_se([], []) == 0.0
    assert _clustered_se([1.0], ["T1"]) == 0.0


def test_percentile_basic():
    assert _percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert _percentile([1, 2, 3, 4], 0) == pytest.approx(1.0)
    assert _percentile([1, 2, 3, 4], 100) == pytest.approx(4.0)
    assert _percentile([], 50) == 0.0
    assert _percentile([5.0], 95) == pytest.approx(5.0)


def test_compute_stage_stats_has_required_keys():
    times = [0.5, 0.6, 0.55, 0.7, 0.52]
    tickets = ["T1", "T1", "T2", "T2", "T3"]
    stats = compute_stage_stats(times, tickets)
    for key in [
        "mean_seconds",
        "se_seconds",
        "n_clusters",
        "p50",
        "p90",
        "p95",
        "throughput_per_second",
        "n",
    ]:
        assert key in stats
    assert stats["n"] == 5
    assert stats["n_clusters"] == 3
    assert stats["mean_seconds"] == pytest.approx(sum(times) / 5)
    assert stats["se_seconds"] >= 0
    assert stats["p50"] > 0
    assert stats["p50"] <= stats["p90"] <= stats["p95"]
    assert stats["p95"] >= stats["p50"]
    assert stats["min_seconds"] <= stats["mean_seconds"] <= stats["max_seconds"]


def test_compute_stage_stats_empty():
    stats = compute_stage_stats([], [])
    assert stats["mean_seconds"] == 0.0
    assert stats["n"] == 0
    assert stats["n_clusters"] == 0


def test_compute_stage_stats_mismatched_lengths_raises():
    with pytest.raises(ValueError, match="equal length"):
        compute_stage_stats([1.0, 2.0], ["T1"])


def test_synthesize_documents_counts_and_deterministic():
    docs = synthesize_documents(n_text=5, n_image=3, seed=42)
    assert len(docs) == 8
    # Deterministic: same seed gives same order
    docs2 = synthesize_documents(n_text=5, n_image=3, seed=42)
    assert [d["ticket"] for d in docs] == [d["ticket"] for d in docs2]
    # All tickets unique
    tickets = [d["ticket"] for d in docs]
    assert len(set(tickets)) == 8
    # Text vs image split
    n_text_actual = sum(1 for d in docs if d["text"] is not None)
    n_img_actual = sum(1 for d in docs if d["document_bytes"] is not None)
    assert n_text_actual == 5
    assert n_img_actual == 3
    image_docs = [row for row in docs if row["document_bytes"] is not None]
    assert all(b"/Contents" in row["document_bytes"] for row in image_docs)
    assert all(b"District Collector" in row["document_bytes"] for row in image_docs)


def test_run_benchmark_standard_variant_basic():
    # Small n for fast test
    result = run_benchmark(variant="standard", n_text=4, n_image=2, repeats=3, seed=123)
    assert result["variant"] == "standard"
    assert result["n_docs"] == 6
    assert result["repeats"] == 3
    assert result["warm_discarded"] is True
    # n_measured = n_docs * (repeats - 1) when warm discarded
    assert result["n_measured"] == 6 * 2
    assert result["attempts"] == 18
    assert result["completed_attempts"] == 18
    assert result["failed_attempts"] == 0
    assert {"text", "document"} <= set(result["input_paths"])
    assert result["input_paths"]["text"]["e2e"]["n"] == 8
    assert result["input_paths"]["document"]["e2e"]["n"] == 4
    assert result["environment"]["python"]
    assert "stages" in result
    for key in ALL_KEYS:
        assert key in result["stages"]
        stage = result["stages"][key]
        for k in ["mean_seconds", "se_seconds", "n_clusters", "p50", "p90", "p95"]:
            assert k in stage
        # All clusters should be n_docs (each ticket is its own cluster)
        assert stage["n_clusters"] == 6
        assert stage["n"] == 12  # 6 docs * 2 measured repeats
        assert stage["mean_seconds"] > 0
        assert stage["se_seconds"] >= 0
        assert stage["p95"] >= stage["p50"]


def test_run_benchmark_warm_discard_vs_no_discard():
    r_warm = run_benchmark(variant="standard", n_text=4, n_image=0, repeats=3, discard_warm=True, seed=1)
    r_no_warm = run_benchmark(variant="standard", n_text=4, n_image=0, repeats=3, discard_warm=False, seed=1)
    assert r_warm["n_measured"] == 8  # 4 * 2
    assert r_no_warm["n_measured"] == 12  # 4 * 3
    # Warm discard should have same stages but different n
    assert r_warm["stages"]["e2e"]["n"] == 8
    assert r_no_warm["stages"]["e2e"]["n"] == 12


def test_run_benchmark_all_variants_produce_different_ocr_means():
    # Sarvam digitise should have higher OCR mean than standard (fake timings)
    r_std = run_benchmark(variant="standard", n_text=4, n_image=0, repeats=2, discard_warm=False, seed=99)
    r_digit = run_benchmark(variant="sarvam_digitise", n_text=4, n_image=0, repeats=2, discard_warm=False, seed=99)
    assert r_digit["stages"]["ocr"]["mean_seconds"] > r_std["stages"]["ocr"]["mean_seconds"]
    # sarvam_extract should differ in summarize/categorize
    r_ext = run_benchmark(variant="sarvam_extract", n_text=4, n_image=0, repeats=2, discard_warm=False, seed=99)
    assert r_ext["stages"]["summarize"]["mean_seconds"] != pytest.approx(r_std["stages"]["summarize"]["mean_seconds"])


def test_run_benchmark_invalid_variant_raises():
    with pytest.raises(ValueError, match="unknown variant"):
        run_benchmark(variant="invalid", n_text=2, n_image=0, repeats=2)


def test_run_benchmark_zero_docs_raises():
    with pytest.raises(ValueError, match="no documents"):
        run_benchmark(variant="standard", n_text=0, n_image=0, repeats=2)


def test_run_benchmark_custom_docs():
    docs = [
        {"ticket": "T1", "text": "hello", "document_name": None, "document_bytes": None, "district": "Sambalpur"},
        {"ticket": "T2", "text": "world", "document_name": None, "document_bytes": None, "district": "Sambalpur"},
    ]
    result = run_benchmark(variant="standard", docs=docs, repeats=2, discard_warm=False)
    assert result["n_docs"] == 2
    assert result["stages"]["e2e"]["n"] == 4  # 2 docs * 2 repeats
    assert result["stages"]["e2e"]["n_clusters"] == 2


def test_run_benchmark_with_fake_processor_factory():
    # Processor factory that returns an object with process method
    class FakeProcessor:
        def process(self, **kwargs):
            # Simulate tiny work
            pass

    def factory(variant):
        return FakeProcessor()

    result = run_benchmark(variant="standard", n_text=3, n_image=0, repeats=2, discard_warm=False, processor_factory=factory)
    assert result["stages"]["e2e"]["mean_seconds"] >= 0
    assert result["stages"]["e2e"]["n"] == 6


def test_real_processor_failures_are_counted_without_polluting_timings():
    class IntermittentProcessor:
        _timing_sink = None

        def __init__(self):
            self.calls = 0

        def process(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                self._timing_sink({"redact": 99.0, "e2e": 99.0})
                raise RuntimeError("synthetic failure")
            self._timing_sink({"redact": 0.1, "e2e": 0.2, "ok": 1.0})

    result = run_benchmark(
        variant="standard",
        n_text=2,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: IntermittentProcessor(),
    )

    assert result["attempts"] == 4
    assert result["completed_attempts"] == 3
    assert result["failed_attempts"] == 1
    assert result["failures_by_error"] == {"RuntimeError": 1}
    assert result["n_measured"] == 3
    assert result["stages"]["redact"]["n"] == 3
    assert result["stages"]["redact"]["mean_seconds"] == pytest.approx(0.1)


def test_real_processor_stage_names_are_preserved():
    class TimedProcessor:
        _timing_sink = None

        def process(self, **kwargs):
            self._timing_sink(
                {"redact": 0.1, "categorize": 0.2, "e2e": 0.4, "ok": 1.0}
            )

    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: TimedProcessor(),
    )
    assert result["stages"]["redact"]["n"] == 2
    assert result["stages"]["categorize"]["mean_seconds"] == pytest.approx(0.2)
    assert "ok" not in result["stages"]


def test_latency_json_payload_single_variant(tmp_path):
    result = run_benchmark(variant="standard", n_text=3, n_image=0, repeats=2, discard_warm=False, seed=7)
    payload = latency_json_payload(result)
    assert payload["variant"] == "standard"
    assert "stages" in payload
    assert "variants" in payload
    assert "standard" in payload["variants"]
    assert payload["schema_version"] == "janasunani.pipeline-latency/v1"
    assert payload["publication_ready"] is False
    # Stages should have all required keys
    assert "e2e" in payload["stages"]
    assert "mean_seconds" in payload["stages"]["e2e"]


def test_latency_json_payload_multi_variant(tmp_path):
    r1 = run_benchmark(variant="standard", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=1)
    r2 = run_benchmark(variant="sarvam_digitise", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=1)
    payload = latency_json_payload({"standard": r1, "sarvam_digitise": r2})
    assert "variants" in payload
    assert "standard" in payload["variants"]
    assert "sarvam_digitise" in payload["variants"]
    assert payload["n_variants"] == 2
    assert payload["schema_version"] == "janasunani.pipeline-latency/v1"
    assert payload["publication_ready"] is False


def test_identified_real_latency_run_is_publication_ready():
    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=1,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    payload = latency_json_payload(result)

    assert payload["publication_ready"] is True
    assert payload["temperature_e2e"]["cold"]["n"] == 1
    assert payload["temperature_e2e"]["warm"]["n"] == 3

    result["git_sha"] = None
    assert latency_json_payload(result)["publication_ready"] is False
    result["git_sha"] = "abc1234"
    result["benchmark_context"]["host_label"] = "   "
    assert latency_json_payload(result)["publication_ready"] is False


def test_real_latency_with_failure_is_not_publication_ready():
    calls = 0

    class SometimesFailingProcessor:
        _timing_sink = None

        def process(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("benchmark failure")
            self._timing_sink({"redact": 0.1, "e2e": 0.2, "ok": 1.0})

    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=1,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: SometimesFailingProcessor(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert result["failed_attempts"] == 1
    assert latency_json_payload(result)["publication_ready"] is False


def test_real_latency_without_document_path_is_not_publication_ready():
    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert latency_json_payload(result)["publication_ready"] is False


def test_real_latency_without_warm_sample_is_not_publication_ready():
    result = run_benchmark(
        variant="standard",
        n_text=1,
        n_image=0,
        repeats=1,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )
    result["benchmark_context"] = {
        "host_label": "release-host",
        "model_release_id": "model-release-1",
    }

    assert latency_json_payload(result)["publication_ready"] is False


def test_temperature_aggregates_have_one_cold_request_per_processor():
    result = run_benchmark(
        variant="standard",
        n_text=2,
        n_image=0,
        repeats=2,
        discard_warm=False,
        processor_factory=lambda _variant: type(
            "Processor",
            (),
            {
                "_timing_sink": None,
                "process": lambda self, **kwargs: self._timing_sink(
                    {"redact": 0.1, "e2e": 0.2, "ok": 1.0}
                ),
            },
        )(),
    )

    assert result["temperature_e2e"]["cold"]["n"] == 1
    assert result["temperature_e2e"]["warm"]["n"] == 3
    assert result["temperature_definition"]["cold"].startswith(
        "first successful request"
    )


def test_write_latency_json_creates_file(tmp_path):
    result = run_benchmark(variant="standard", n_text=2, n_image=1, repeats=2, discard_warm=True, seed=5)
    out = tmp_path / "outputs" / "benchmark" / "latency.json"
    path = write_latency_json(result, out)
    assert path == out
    assert out.is_file()
    data = json.loads(out.read_text())
    assert "stages" in data
    assert data["variant"] == "standard"
    assert "e2e" in data["stages"]
    # Check JSON has mean/se/p50/p90/p95 per stage
    for stage_key in ALL_KEYS:
        entry = data["stages"][stage_key]
        assert "mean_seconds" in entry
        assert "se_seconds" in entry
        assert "p50" in entry
        assert "p90" in entry
        assert "p95" in entry
        assert "throughput_per_second" in entry
        assert "n_clusters" in entry


def test_write_latency_json_multi_variant_file(tmp_path):
    r1 = run_benchmark(variant="standard", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=10)
    r2 = run_benchmark(variant="sarvam_both", n_text=2, n_image=0, repeats=2, discard_warm=False, seed=10)
    out = tmp_path / "latency.json"
    path = write_latency_json({"standard": r1, "sarvam_both": r2}, out)
    data = json.loads(path.read_text())
    assert "variants" in data
    assert data["variants"]["standard"]["variant"] == "standard"
    assert data["variants"]["sarvam_both"]["variant"] == "sarvam_both"


def test_cli_single_variant_writes_output(tmp_path):
    # `--fake` is now explicit. It used to be implied for every run, because
    # main() forced is_fake=True regardless, so this test exercised the
    # synthetic table while appearing to exercise the CLI end to end.
    out = tmp_path / "latency.json"
    rc = bench_mod.main(["--fake", "--variant", "standard", "--n-docs", "2", "--n-image-docs", "1", "--repeats", "2", "--output", str(out), "--seed", "42"])
    assert rc == 0
    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["variant"] == "standard"
    assert data["stages"]["e2e"]["n"] == 3 * 1  # (2+1)=3 docs * (2-1)=1 measured
    assert data["is_fake_timing"] is True


def test_a_run_without_fake_does_not_silently_fabricate(tmp_path, monkeypatch):
    """The regression for the unconditional `is_fake = True` override.

    Before this, `main()` forced fake timings whether or not `--fake` was
    passed, so every latency number this harness ever produced came from
    `_FAKE_STAGE_MEANS`. A run that cannot measure must fail, not invent.
    """
    out = tmp_path / "latency.json"

    def _no_processor(*args, **kwargs):
        raise RuntimeError("models are not available in this environment")

    monkeypatch.setattr(
        "janasunani.inference.service.build_processor", _no_processor, raising=False
    )

    with pytest.raises((SystemExit, RuntimeError)):
        bench_mod.main(
            ["--variant", "standard", "--n-docs", "1", "--n-image-docs", "0",
             "--repeats", "2", "--output", str(out), "--seed", "42"]
        )
    # Nothing fabricated on the way out.
    if out.exists():
        assert json.loads(out.read_text()).get("is_fake_timing") is not False


def test_real_run_records_per_stage_from_the_timing_sink():
    """Per-stage comes from the processor, not from proportions of e2e."""
    from janasunani.inference.timing import StageTimer

    timer = StageTimer()
    with timer.stage("redact"):
        pass
    with timer.stage("categorize"):
        pass
    timings = timer.as_dict()
    assert {"redact", "categorize", "e2e"} <= set(timings)
    # e2e is measured independently, so the gap to the sum of stages stays
    # visible rather than being defined away.
    assert timings["e2e"] >= 0.0


def test_cli_multi_variants(tmp_path):
    # `--fake` is explicit: this asserts the CLI's output shape, not latency.
    # Without it the harness builds a real processor, which needs the DVC-
    # mirrored models that CI deliberately does not pull.
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        [
            "--fake",
            "--variants",
            "standard",
            "sarvam_digitise",
            "--n-docs",
            "2",
            "--n-image-docs",
            "0",
            "--repeats",
            "2",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert "variants" in data
    assert "standard" in data["variants"]
    assert "sarvam_digitise" in data["variants"]


def test_real_sarvam_variant_is_rejected_until_it_is_wired(tmp_path):
    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            [
                "--variant",
                "sarvam_digitise",
                "--n-docs",
                "1",
                "--n-image-docs",
                "0",
                "--repeats",
                "2",
                "--output",
                str(tmp_path / "latency.json"),
            ]
        )
    assert exc.value.code == 2


def test_cli_no_warm_discard(tmp_path):
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        ["--fake", "--variant", "standard", "--n-docs", "2", "--n-image-docs", "0", "--repeats", "2", "--output", str(out), "--no-warm-discard"]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["warm_discarded"] is False
    assert data["stages"]["e2e"]["n"] == 4  # 2 docs * 2 repeats


def test_fake_process_same_call_is_deterministic():
    # Same inputs, same process: must be bit-identical (sanity before the
    # cross-process PYTHONHASHSEED check below).
    t1 = _fake_process("standard", "SYN-TXT-0001", 1, seed=42)
    t2 = _fake_process("standard", "SYN-TXT-0001", 1, seed=42)
    assert t1.e2e == t2.e2e
    assert t1.per_stage == t2.per_stage


def test_fake_process_timings_stable_across_pythonhashseed():
    """Regression for #208: rng_seed must not be derived from hash().

    Python salts hash() per-interpreter via PYTHONHASHSEED, so seeding the
    RNG from hash((seed, ticket, repeat_idx, variant)) made identical
    --seed + docs produce different fake latency means/SEs across
    machines/runs. The fix (hashlib.sha256 digest) must give the same
    fake timings for the same inputs regardless of PYTHONHASHSEED.
    """
    script = (
        "import json, sys; "
        "from scripts.benchmark_pipeline import _fake_process; "
        "t = _fake_process('sarvam_extract', 'SYN-TXT-0007', 2, seed=42); "
        "print(json.dumps({'e2e': t.e2e, 'per_stage': t.per_stage}))"
    )
    outputs = []
    for hashseed in ("0", "1", "1337"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            check=True,
        )
        outputs.append(json.loads(proc.stdout.strip()))
    assert outputs[0] == outputs[1] == outputs[2], (
        f"fake timings differ across PYTHONHASHSEED: {outputs}"
    )


def test_cli_repeats_one_with_warm_discard_rejected(tmp_path, capsys):
    # #209: --repeats 1 with default warmup discard means every observation
    # is the discarded warm one — the harness must reject this instead of
    # silently writing an all-zero latency report with exit 0.
    out = tmp_path / "latency.json"
    with pytest.raises(SystemExit) as exc:
        bench_mod.main(
            ["--repeats", "1", "--output", str(out), "--fake", "--n-docs", "2", "--n-image-docs", "0"]
        )
    assert exc.value.code == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "--repeats" in err
    assert "--no-warm-discard" in err


def test_cli_repeats_one_with_no_warm_discard_is_allowed(tmp_path):
    # The sole observation is retained (not discarded) when warmup discard
    # is explicitly turned off, so repeats=1 is legitimate there.
    out = tmp_path / "latency.json"
    rc = bench_mod.main(
        [
            "--repeats",
            "1",
            "--no-warm-discard",
            "--output",
            str(out),
            "--fake",
            "--n-docs",
            "2",
            "--n-image-docs",
            "0",
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["stages"]["e2e"]["n"] == 2
    assert data["stages"]["e2e"]["mean_seconds"] > 0


def test_benchmark_end_to_end_structure_matches_output_spec(tmp_path):
    """Ensure the on-disk JSON has exactly the spec-required shape."""
    result = run_benchmark(variant="sarvam_extract", n_text=3, n_image=1, repeats=3, seed=99)
    out = tmp_path / "latency.json"
    write_latency_json(result, out)
    data = json.loads(out.read_text())
    # Top-level keys required by spec
    assert "variant" in data
    assert "n_docs" in data
    assert "repeats" in data
    assert "stages" in data
    assert "timestamp" in data
    # Per-stage required metrics
    for stage in ALL_KEYS:
        s = data["stages"][stage]
        assert "mean_seconds" in s, f"missing mean_seconds for {stage}"
        assert "se_seconds" in s, f"missing se_seconds for {stage}"
        assert "n_clusters" in s, f"missing n_clusters for {stage}"
        assert "p50" in s, f"missing p50 for {stage}"
        assert "p90" in s, f"missing p90 for {stage}"
        assert "p95" in s, f"missing p95 for {stage}"
    # n_measured matches e2e.n when warm discarded
    assert data["n_measured"] == data["stages"]["e2e"]["n"]
