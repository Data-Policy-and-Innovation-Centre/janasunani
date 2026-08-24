from __future__ import annotations

import json

import pytest

from janasunani.evaluation.value_add_benchmark_facts import (
    category_benchmark_summary,
    load_benchmark_facts,
)
from scripts.create_officer_brief import _routing as officer_routing
from scripts.create_public_systems_capability_brief import (
    _routing_case_study as capability_routing,
)
from scripts.update_value_add_report import _routing as report_routing


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
        "routing_historical_all": {
            "test": {"n": 10, "accuracy": 0.4, "top_k_accuracy": {"3": 0.7}}
        },
        "routing_historical_informative": {
            "test": {"n": 8, "accuracy": 0.5, "top_k_accuracy": {"3": 0.8}}
        },
        "routing_outcome_development": {
            "schema_version": "routing-outcome-evidence-v1",
            "_provenance": {
                "action_definition": "department_id::complete_role_chain/v1",
                "assignment_field_provenance": "unresolved",
            },
            "validation_2024": {
                "support": {"n_before": 12, "n_evaluated": 10, "n_excluded": 2},
                "historical": {"correctness": 0.4},
                "tau_0": {
                    model: {
                        "delta_dm": 2.0,
                        "delta_aipw": 1.0,
                        "aipw_se": 0.5,
                        "ess_over_n": 0.2,
                    }
                    for model in ("ridge_top_three", "gbm_top_three")
                },
                "ridge_frontier": {
                    "tau_star": None,
                    "status": "unresolved_estimator_disagreement",
                },
            },
            "test_2025": {
                "support": {"n_before": 8, "n_evaluated": 7, "n_excluded": 1},
                "historical": {"correctness": 0.3},
                "tau_0": {
                    model: {
                        "delta_dm": 2.0,
                        "delta_aipw": 0.0,
                        "aipw_se": 0.5,
                        "ess_over_n": 0.1,
                    }
                    for model in ("ridge_top_three", "gbm_top_three")
                },
            },
            "robustness_ladder_2024": {
                "rungs": {
                    rung: {
                        "n_validation": 10,
                        "delta": 0.01,
                        "delta_evaluation_se": 0.02,
                    }
                    for rung in (
                        "R0_binary_completers",
                        "R1_proxy_actionable_completers",
                        "R2_proxy_actionable_restricted",
                        "R3_proxy_actionable_restricted_ipcw",
                    )
                }
            },
            "interpretation": "No gain established.",
        },
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
    assert facts.routing_outcome["test_2025"]["support"]["n_evaluated"] == 7
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


def test_refuses_routing_outcome_without_temporal_holdout(tmp_path):
    bundle = _bundle()
    routing = next(
        row for row in bundle["artifacts"] if row["id"] == "routing_outcome_development"
    )
    del routing["payload"]["test_2025"]
    path = tmp_path / "full_benchmark.json"
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="test_2025"):
        load_benchmark_facts(path)


def test_report_routing_sections_use_the_temporal_holdout(tmp_path):
    path = tmp_path / "full_benchmark.json"
    path.write_text(json.dumps(_bundle()))
    facts = load_benchmark_facts(path)
    sections = (
        officer_routing(facts),
        capability_routing(facts),
        report_routing(facts),
    )
    for section in sections:
        assert "2025" in section
        assert "gain" in section.lower()
        assert "Between 11 and 23 days" not in section
        assert "10.6 and 23.5" not in section
        assert "454,232 grievances" not in section
