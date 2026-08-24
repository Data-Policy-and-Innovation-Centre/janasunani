import hashlib
import json
from pathlib import Path

import pytest
import yaml

from janasunani.evaluation.summary import (
    SummaryJudgment,
    build_scorecard,
    load_judgments,
    validate_provenance,
)


def generated(item_id, *, language="English", source="typed", **overrides):
    values = {
        "item_id": item_id,
        "group_id": item_id,
        "language": language,
        "source_type": source,
        "should_skip": False,
        "skipped": False,
        "critical_facts_total": 4,
        "critical_facts_present": 3,
        "unsupported_claims": 0,
        "contradictions": 0,
        "pii_leak": False,
        "usefulness": 2,
        "usable_without_edit": True,
        "edit_seconds": 15.0,
    }
    values.update(overrides)
    return SummaryJudgment(**values)


def skipped(item_id, *, should_skip=True, language="unknown", source="typed"):
    return SummaryJudgment(
        item_id=item_id,
        group_id=item_id,
        language=language,
        source_type=source,
        should_skip=should_skip,
        skipped=True,
        critical_facts_total=0,
        critical_facts_present=0,
        unsupported_claims=0,
        contradictions=0,
        pii_leak=False,
        usefulness=None,
        usable_without_edit=None,
        edit_seconds=None,
    )


def write_provenance_contract(tmp_path, judgments_path, rows):
    provenance = tmp_path / "provenance.json"
    generated_n = sum(not row.skipped for row in rows)
    payload = {
        "schema_version": "summary-development-provenance/v1",
        "evidence_status": "single-frontier-judge-development-only",
        "publication_ready": False,
        "source": {"redacted_only": True, "split": "test", "sha256": "a" * 64},
        "selection": {
            "sample_size": len(rows),
            "generated": generated_n,
            "skipped": len(rows) - generated_n,
            "not_prevalence_representative": True,
            "private_review_sha256": "b" * 64,
        },
        "model": {"local_files_only": True, "weights_sha256": "c" * 64},
        "adjudication": {
            "structured_judgments_only_in_governed_artifacts": True,
            "officer_validated": False,
        },
    }
    provenance.write_text(json.dumps(payload), encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text(
        json.dumps(
            {
                "schema_version": "janasunani.summary-benchmark-binding/v1",
                "dataset_id": "summary-bart-development-v1",
                "judgments_md5": hashlib.md5(
                    judgments_path.read_bytes(), usedforsecurity=False
                ).hexdigest(),
                "provenance_md5": hashlib.md5(
                    provenance.read_bytes(), usedforsecurity=False
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return provenance, binding


def test_provenance_contract_binds_judgments_and_review_metadata(tmp_path):
    rows = [generated("a"), skipped("b")]
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text(
        "".join(json.dumps(row.__dict__) + "\n" for row in rows), encoding="utf-8"
    )
    provenance, binding = write_provenance_contract(tmp_path, judgments, rows)

    validate_provenance(
        judgments,
        provenance,
        binding,
        rows,
        dataset_id="summary-bart-development-v1",
    )

    judgments.write_text(judgments.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="judgments fingerprint"):
        validate_provenance(
            judgments,
            provenance,
            binding,
            rows,
            dataset_id="summary-bart-development-v1",
        )


def test_dvc_stage_consumes_the_summary_provenance_binding():
    root = Path(__file__).resolve().parents[1]
    pipeline = yaml.safe_load((root / "dvc.yaml").read_text(encoding="utf-8"))
    lock = yaml.safe_load((root / "dvc.lock").read_text(encoding="utf-8"))

    for document in (pipeline, lock):
        stage = document["stages"]["summary-development-benchmark"]
        command = stage["cmd"]
        dependencies = {
            dependency if isinstance(dependency, str) else dependency["path"]
            for dependency in stage["deps"]
        }
        assert (
            "--provenance data/external/summary_development_v1/provenance.json"
            in command
        )
        assert "--binding config/summary_benchmark_binding.json" in command
        assert "config/summary_benchmark_binding.json" in dependencies


def test_scorecard_covers_factuality_usefulness_editing_and_abstention():
    rows = [
        generated("good"),
        generated(
            "bad",
            language="Odia",
            source="scan",
            critical_facts_present=2,
            unsupported_claims=1,
            contradictions=1,
            pii_leak=True,
            usefulness=0,
            usable_without_edit=False,
            edit_seconds=90.0,
        ),
        skipped("low-signal"),
        generated(
            "should-have-skipped",
            should_skip=True,
            critical_facts_total=0,
            critical_facts_present=0,
        ),
    ]

    report = build_scorecard(rows, dataset_id="summary-gold-v1")

    overall = report["overall"]
    assert overall["n"] == 4
    assert overall["critical_fact_recall"]["rate"] == pytest.approx(5 / 8)
    assert overall["unsupported_claim_case_rate"]["rate"] == pytest.approx(1 / 3)
    assert overall["pii_leak_case_rate"]["rate"] == pytest.approx(1 / 3)
    assert overall["correct_skip_rate"]["rate"] == pytest.approx(3 / 4)
    assert overall["median_edit_seconds"] == 15.0
    assert set(report["by_language"]) == {"English", "Odia", "unknown"}
    assert set(report["by_source_type"]) == {"scan", "typed"}
    assert report["safety"]["wilson_interval_units"] == {
        "critical_fact_recall": "pooled_fact",
        "case_rates": "item",
    }
    assert report["safety"]["critical_fact_within_item_dependence_adjusted"] is False


def test_exact_screenshot_behavior_is_a_correct_skip_judgment():
    row = skipped("screenshot-i-am-an-idiot", language="unknown")
    report = build_scorecard([row], dataset_id="summary-regressions-v1")

    assert report["overall"]["correct_skip_rate"]["rate"] == 1.0
    assert report["overall"]["generated_n"] == 0


def test_loader_rejects_narrative_or_identity_fields(tmp_path):
    payload = generated("x").__dict__ | {"candidate_summary": "narrative"}
    path = tmp_path / "judgments.jsonl"
    path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="forbidden"):
        load_judgments(path)


def test_loader_is_strict_and_rejects_duplicate_items(tmp_path):
    payload = generated("same").__dict__
    path = tmp_path / "judgments.jsonl"
    path.write_text(json.dumps(payload) + "\n" + json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="unique"):
        load_judgments(path)


def test_skipped_rows_cannot_hide_generated_output_findings():
    with pytest.raises(ValueError, match="skipped summary"):
        SummaryJudgment(**(skipped("x").__dict__ | {"unsupported_claims": 1}))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("usefulness", True, "usefulness"),
        ("usable_without_edit", 1, "usable_without_edit"),
        ("edit_seconds", True, "edit_seconds"),
        ("edit_seconds", float("nan"), "edit_seconds"),
    ],
)
def test_generated_judgment_rejects_boolean_or_nonfinite_metrics(field, value, message):
    with pytest.raises(ValueError, match=message):
        generated("invalid", **{field: value})


@pytest.mark.parametrize("field", ["should_skip", "skipped", "pii_leak"])
def test_judgment_requires_actual_booleans(field):
    with pytest.raises(ValueError, match=field):
        generated("invalid-boolean", **{field: "false"})
