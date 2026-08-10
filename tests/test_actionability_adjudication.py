import json
import stat

from janasunani.evaluation.actionability import load_jsonl
from janasunani.evaluation.adjudication import (
    finalize_gold,
    normalize_adjudication_provenance,
    prepare_resolution,
)


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _sample_rows():
    return [
        {
            "item_id": item_id,
            "group_id": item_id,
            "redacted_text": text,
            "created_year": 2024,
            "split": split,
            "language": "unknown_pending_adjudication",
            "sampling_stratum": "s5",
        }
        for item_id, text, split in (
            ("a", "road is broken", "train"),
            ("b", "hello", "validation"),
            ("c", "need help", "test"),
        )
    ]


def _judgment(item_id, label, *, uncertain=False):
    return {
        "item_id": item_id,
        "label": label,
        "confidence": 0.9,
        "uncertain": uncertain,
        "rationale_code": "unit_test",
    }


def test_prepare_and_finalize_frontier_gold(tmp_path):
    sample = tmp_path / "sample.jsonl"
    judge_a = tmp_path / "judge-a.jsonl"
    judge_b = tmp_path / "judge-b.jsonl"
    consensus = tmp_path / "consensus.jsonl"
    resolver_input = tmp_path / "resolver-input.jsonl"
    report_path = tmp_path / "report.json"
    _write(sample, _sample_rows())
    _write(
        judge_a,
        [
            _judgment("a", "actionable"),
            _judgment("b", "irrelevant"),
            _judgment("c", "underspecified", uncertain=True),
        ],
    )
    _write(
        judge_b,
        [
            _judgment("a", "actionable"),
            _judgment("b", "underspecified"),
            _judgment("c", "underspecified"),
        ],
    )

    report = prepare_resolution(
        sample,
        judge_a,
        judge_b,
        consensus_path=consensus,
        resolver_input_path=resolver_input,
        report_path=report_path,
    )
    assert report["records"] == 3
    assert report["confident_consensus"] == 1
    assert report["sent_to_resolver"] == 2
    assert report["adjudication_provenance"]["judge_a_model"] == "unavailable"
    assert report["sample_design"]["production_prevalence_representative"] is False
    assert "redacted_text" not in report_path.read_text()
    assert len(resolver_input.read_text().splitlines()) == 2

    resolver = tmp_path / "resolver.jsonl"
    _write(
        resolver,
        [
            _judgment("b", "irrelevant"),
            _judgment("c", "underspecified"),
        ],
    )
    gold = tmp_path / "gold.jsonl"
    manifest_path = tmp_path / "gold-manifest.json"
    manifest = finalize_gold(
        sample,
        consensus,
        resolver,
        gold_path=gold,
        manifest_path=manifest_path,
    )
    assert manifest["label_distribution"] == {
        "actionable": 1,
        "irrelevant": 1,
        "underspecified": 1,
    }
    assert manifest["resolution"]["unresolved_excluded"] == 0
    assert manifest["resolution"]["uncertain_resolver_labels_enter_gold"] is False
    assert manifest["sample_design"]["sample_stratum_counts"] == {"s5": 3}
    assert manifest["adjudication_provenance"]["resolver_model"] == "unavailable"
    records = load_jsonl(gold)
    assert len(records) == 3
    assert stat.S_IMODE(gold.stat().st_mode) == 0o600
    assert {record.label_source for record in records} == {"frontier_adjudicated"}
    assert {record.sampling_stratum for record in records} == {"s5"}


def test_uncertain_resolver_label_is_excluded_with_aggregate_counts(tmp_path):
    sample = tmp_path / "sample.jsonl"
    consensus = tmp_path / "consensus.jsonl"
    resolver = tmp_path / "resolver.jsonl"
    _write(sample, _sample_rows())
    _write(consensus, [{"item_id": "a", "label": "actionable"}])
    _write(
        resolver,
        [
            _judgment("b", "irrelevant", uncertain=True),
            _judgment("c", "underspecified"),
        ],
    )

    gold = tmp_path / "gold.jsonl"
    manifest = finalize_gold(
        sample,
        consensus,
        resolver,
        gold_path=gold,
        manifest_path=tmp_path / "manifest.json",
    )

    assert manifest["records"] == 2
    assert manifest["resolution"]["resolver_judgments_received"] == 2
    assert manifest["resolution"]["confident_resolver_accepted"] == 1
    assert manifest["resolution"]["unresolved_excluded"] == 1
    assert manifest["resolution"]["unresolved_excluded_by_split"] == {
        "validation": 1
    }
    assert '"item_id": "b"' not in gold.read_text()


def test_adjudication_provenance_is_complete_and_hash_only():
    provenance = normalize_adjudication_provenance(
        {"prompt_sha256": "a" * 64, "rubric_version": "rubric-v1"}
    )
    assert provenance["prompt_sha256"] == "sha256:" + "a" * 64
    assert provenance["judge_a_model"] == "unavailable"
    assert set(provenance) == {
        "protocol_version",
        "rubric_version",
        "prompt_sha256",
        "judge_a_model",
        "judge_b_model",
        "resolver_model",
        "inference_environment",
        "egress_policy",
        "retention_policy",
    }

    try:
        normalize_adjudication_provenance({"prompt_sha256": "raw prompt text"})
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("raw prompts must not enter aggregate provenance")


def test_prepare_requires_exact_judge_coverage(tmp_path):
    sample = tmp_path / "sample.jsonl"
    judge_a = tmp_path / "judge-a.jsonl"
    judge_b = tmp_path / "judge-b.jsonl"
    _write(sample, _sample_rows())
    _write(judge_a, [_judgment("a", "actionable")])
    _write(judge_b, [_judgment("a", "actionable")])

    try:
        prepare_resolution(
            sample,
            judge_a,
            judge_b,
            consensus_path=tmp_path / "consensus.jsonl",
            resolver_input_path=tmp_path / "resolver-input.jsonl",
            report_path=tmp_path / "report.json",
        )
    except ValueError as exc:
        assert "exactly the sample IDs" in str(exc)
    else:
        raise AssertionError("partial adjudication must fail closed")


def test_finalize_accepts_empty_consensus_when_every_case_needs_resolution(tmp_path):
    sample = tmp_path / "sample.jsonl"
    consensus = tmp_path / "consensus.jsonl"
    resolver = tmp_path / "resolver.jsonl"
    _write(sample, _sample_rows())
    consensus.write_text("")
    _write(
        resolver,
        [
            _judgment("a", "actionable"),
            _judgment("b", "irrelevant"),
            _judgment("c", "underspecified"),
        ],
    )

    manifest = finalize_gold(
        sample,
        consensus,
        resolver,
        gold_path=tmp_path / "gold.jsonl",
        manifest_path=tmp_path / "manifest.json",
    )

    assert manifest["records"] == 3
    assert manifest["resolution"]["confident_consensus_accepted"] == 0


def test_finalize_accepts_empty_resolver_when_every_case_is_consensus(tmp_path):
    sample = tmp_path / "sample.jsonl"
    consensus = tmp_path / "consensus.jsonl"
    resolver = tmp_path / "resolver.jsonl"
    _write(sample, _sample_rows())
    _write(
        consensus,
        [
            {"item_id": "a", "label": "actionable"},
            {"item_id": "b", "label": "irrelevant"},
            {"item_id": "c", "label": "underspecified"},
        ],
    )
    resolver.write_text("")

    manifest = finalize_gold(
        sample,
        consensus,
        resolver,
        gold_path=tmp_path / "gold.jsonl",
        manifest_path=tmp_path / "manifest.json",
    )

    assert manifest["records"] == 3
    assert manifest["resolution"]["resolver_judgments_received"] == 0


def test_sample_rejects_invalid_year_and_sampling_stratum(tmp_path):
    sample = tmp_path / "sample.jsonl"
    rows = _sample_rows()
    rows[0]["created_year"] = "2024"
    rows[1]["sampling_stratum"] = ""
    _write(sample, rows)

    try:
        from janasunani.evaluation.adjudication import load_sample

        load_sample(sample)
    except ValueError as exc:
        assert "created_year" in str(exc) or "string field" in str(exc)
    else:
        raise AssertionError("invalid sample metadata must fail closed")
