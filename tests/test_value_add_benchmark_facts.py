from __future__ import annotations

import json

import pytest

from janasunani.evaluation.value_add_benchmark_facts import (
    category_benchmark_summary,
    load_benchmark_facts,
)


def _bundle() -> dict:
    artifacts = {
        "pipeline_latency_development": {
            "is_fake_timing": False,
            "failed_attempts": 0,
            "stages": {"e2e": {"n": 4}},
            "input_paths": {
                "text": {"e2e": {"n": 2}},
                "document": {"e2e": {"n": 2}},
            },
        },
        "actionability_candidates": {
            "validation_selected_candidate": "local",
            "release_eligible": False,
            "candidates": {"local": {"test": {"n": 57}}},
        },
        "actionability_weak_label_audit": {"eligible_ticket_labels": {}},
        "categorization_historical_chronological": {"test": {"n": 12}},
        "pii_development_scorecard": {"unknown": {"overall": {}}},
        "routing_historical_all": {"test": {"n": 10}},
        "routing_historical_informative": {"test": {"n": 8}},
        "summary_development": {"overall": {"n": 6}},
    }
    return {
        "schema_version": "janasunani-full-benchmark-v1",
        "bundle_id": "abc123",
        "publication_ready": False,
        "section_status": {
            "speed": {"required": 1, "available_required": 0},
            "accuracy": {"required": 5, "available_required": 0},
            "impact": {"required": 2, "available_required": 0},
        },
        "artifacts": [
            {"id": artifact_id, "status": "available", "payload": payload}
            for artifact_id, payload in artifacts.items()
        ],
    }


def test_loads_all_report_claims_from_one_bundle(tmp_path):
    path = tmp_path / "full_benchmark.json"
    path.write_text(json.dumps(_bundle()))
    facts = load_benchmark_facts(path)
    assert facts.bundle_id == "abc123"
    assert facts.actionability["selected_candidate"] == "local"
    assert facts.categorization["n"] == 12
    assert facts.routing_all["n"] == 10
    assert facts.pii["overall"] == {}
    assert facts.summary["n"] == 6
    assert facts.impact_available_required == 0
    assert facts.impact_required == 2


def test_formats_category_claims_from_the_bundle_values():
    category = {
        "accuracy": 0.25,
        "top_k_accuracy": {"3": 0.75},
        "macro_f1": 0.5,
        "n": 1234,
    }

    assert category_benchmark_summary(category) == (
        "25.00% top-1 / 75.00% top-3 / 50.00% macro-F1 on a viewed 2024 "
        "chronological, exact-text-group-disjoint development test (n=1,234)"
    )


def test_refuses_fake_latency(tmp_path):
    bundle = _bundle()
    bundle["artifacts"][0]["payload"]["is_fake_timing"] = True
    path = tmp_path / "full_benchmark.json"
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="fake or unlabeled latency"):
        load_benchmark_facts(path)
