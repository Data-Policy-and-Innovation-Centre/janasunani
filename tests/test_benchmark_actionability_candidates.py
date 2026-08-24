import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import yaml

from scripts import benchmark_actionability_candidates as benchmark


class _Classifier:
    classes_ = ("actionable", "review_required")

    def predict_proba(self, texts):
        return [[1.0, 0.0] for _ in texts]


def _write_manifest(path, gold, records):
    payload = {
        "schema_version": "actionability-gold-manifest-v1",
        "gold_sha256": "sha256:" + hashlib.sha256(gold.read_bytes()).hexdigest(),
        "records": len(records),
        "label_distribution": dict(
            sorted(Counter(record.label for record in records).items())
        ),
        "split_distribution": dict(
            sorted(Counter(record.split for record in records).items())
        ),
        "resolution": {"uncertain_resolver_labels_enter_gold": False},
        "sample_design": {
            "production_prevalence_representative": False,
            "sample_manifest_sha256": "sha256:" + "a" * 64,
        },
        "adjudication_provenance": {
            "protocol_version": "test-v1",
            "rubric_version": "test-v1",
            "prompt_sha256": "sha256:" + "b" * 64,
            "judge_a_model": "test-a",
            "judge_b_model": "test-b",
            "resolver_model": "test-r",
            "inference_environment": "test",
            "egress_policy": "test",
            "retention_policy": "test",
        },
        "provenance": {
            "source": "PII-redacted DPIC-controlled sample",
            "adjudication": (
                "two independent frontier judges; confident third-resolver labels "
                "for disagreement or uncertainty; unresolved rows excluded"
            ),
            "claim_status": "development gold, not officer-confirmed truth",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_report_metadata_is_stable_for_dvc(monkeypatch, tmp_path):
    gold = tmp_path / "gold.jsonl"
    gold.write_text("{}\n", encoding="utf-8")
    records = [
        SimpleNamespace(
            split="validation",
            label="actionable",
            label_source="frontier_adjudicated",
            redacted_text="example",
        ),
        SimpleNamespace(
            split="test",
            label="underspecified",
            label_source="frontier_adjudicated",
            redacted_text="example",
        ),
    ]
    manifest = tmp_path / "gold.manifest.json"
    _write_manifest(manifest, gold, records)
    report = {
        "validation": {
            "review_recall": 1.0,
            "review_precision": 1.0,
            "accuracy": 1.0,
        }
    }
    monkeypatch.setattr(benchmark, "load_jsonl", lambda _path: records)
    monkeypatch.setattr(
        benchmark,
        "benchmark_binary_review",
        lambda *_args, **_kwargs: SimpleNamespace(
            classifier=_Classifier(), report=report.copy()
        ),
    )
    monkeypatch.setattr(benchmark, "binary_discrimination_metrics", lambda *_args: {})
    monkeypatch.setattr(
        benchmark,
        "sample_design_summary",
        lambda _records: {"limitation": "development sample"},
    )
    monkeypatch.setattr(benchmark.importlib.metadata, "version", lambda _name: "1")

    first = benchmark.build_report(
        gold_path=gold,
        manifest_path=manifest,
        encoder_specs=[],
        c_values=[1.0],
        min_review_precision=0.6,
        max_actionable_review_rate=0.1,
    )
    second = benchmark.build_report(
        gold_path=gold,
        manifest_path=manifest,
        encoder_specs=[],
        c_values=[1.0],
        min_review_precision=0.6,
        max_actionable_review_rate=0.1,
    )

    assert first == second
    assert "created_at" not in first
    assert "platform" not in first["software"]
    assert first["gold"]["path"] == gold.as_posix()


def test_gold_manifest_must_match_the_evaluated_gold(monkeypatch, tmp_path):
    gold = tmp_path / "gold.jsonl"
    gold.write_text("{}\n", encoding="utf-8")
    records = [
        SimpleNamespace(
            split="validation",
            label="actionable",
            label_source="frontier_adjudicated",
        )
    ]
    manifest = tmp_path / "gold.manifest.json"
    payload = _write_manifest(manifest, gold, records)
    payload["gold_sha256"] = "sha256:" + "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(benchmark, "load_jsonl", lambda _path: records)

    with __import__("pytest").raises(ValueError, match="gold_sha256"):
        benchmark.build_report(
            gold_path=gold,
            manifest_path=manifest,
            encoder_specs=[],
            c_values=[1.0],
            min_review_precision=0.6,
            max_actionable_review_rate=0.1,
        )


def test_dvc_stage_consumes_the_gold_manifest():
    root = Path(__file__).resolve().parents[1]
    pipeline = yaml.safe_load((root / "dvc.yaml").read_text(encoding="utf-8"))
    lock = yaml.safe_load((root / "dvc.lock").read_text(encoding="utf-8"))

    for document in (pipeline, lock):
        stage = document["stages"]["actionability-local-candidate-benchmark"]
        command = stage["cmd"]
        dependencies = {
            dependency if isinstance(dependency, str) else dependency["path"]
            for dependency in stage["deps"]
        }
        manifest = "data/external/actionability_frontier_v1/gold.manifest.json"
        assert f"--manifest {manifest}" in command
        assert manifest in dependencies
