import json

import pytest

from janasunani.pipeline.pii_eval import (
    LEGACY_OVERLAP_BASELINE,
    GoldExample,
    format_report,
    load_gold_jsonl,
    main,
    score_predictions,
)
from janasunani.pipeline.stages.pii_tagger import PIISpan


def test_load_gold_jsonl_normalizes_label_aliases(tmp_path):
    gold = tmp_path / "gold.jsonl"
    text = "Ramesh called 9876543210"
    gold.write_text(
        json.dumps(
            {
                "id": "p1",
                "text": text,
                "entities": [
                    {"start": 0, "end": 6, "label": "PERSON"},
                    {"start": 14, "end": 24, "entity_type": "IN_MOBILE"},
                ],
            }
        )
        + "\n"
    )

    [example] = load_gold_jsonl(gold)

    assert example.id == "p1"
    assert example.entities == (
        PIISpan(entity="NAME", start=0, end=6, score=0.0),
        PIISpan(entity="PHONE", start=14, end=24, score=0.0),
    )


def test_load_gold_jsonl_rejects_invalid_offsets(tmp_path):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps(
            {
                "text": "short",
                "entities": [{"start": 0, "end": 99, "entity": "NAME"}],
            }
        )
        + "\n"
    )

    with pytest.raises(ValueError, match="invalid span offsets"):
        load_gold_jsonl(gold)


def test_score_predictions_counts_overlap_and_exact_by_entity():
    examples = [
        GoldExample(
            id="p1",
            text="Ramesh called 9876543210",
            entities=(
                PIISpan(entity="NAME", start=0, end=6),
                PIISpan(entity="PHONE", start=14, end=24),
            ),
        ),
        GoldExample(
            id="p2",
            text="PAN ABCDE1234F",
            entities=(PIISpan(entity="PAN", start=4, end=14),),
        ),
    ]
    predictions = {
        "p1": (
            PIISpan(entity="PERSON", start=0, end=6),  # exact after normalization
            PIISpan(entity="PHONE_NUMBER", start=15, end=24),  # overlap only
        ),
        "p2": (),
    }

    report = score_predictions(examples, predictions, baseline_overlap_recall=0.5)

    assert report.by_entity["NAME"].exact_recall == 1.0
    assert report.by_entity["PHONE"].overlap_recall == 1.0
    assert report.by_entity["PHONE"].exact_recall == 0.0
    assert report.by_entity["PAN"].overlap_recall == 0.0
    assert report.overall.overlap_hits == 2
    assert report.overall.gold == 3
    assert report.passed_baseline
    assert "OVERALL 3 2 2 1 0.6667 0.3333" in format_report(report)


def test_load_gold_jsonl_rejects_duplicate_ids(tmp_path):
    gold = tmp_path / "gold.jsonl"
    row = {
        "id": "p1",
        "text": "Ramesh",
        "entities": [{"start": 0, "end": 6, "entity": "NAME"}],
    }
    gold.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")

    with pytest.raises(ValueError, match="duplicate gold example id"):
        load_gold_jsonl(gold)


def test_coverage_counts_untyped_hits_and_gates():
    """The legacy 80.56% baseline is untyped: a span redacted under the wrong
    label is still redacted. Coverage must count it; typed metrics must not."""
    examples = [
        GoldExample(
            id="p1",
            text="Ramesh called 9876543210",
            entities=(PIISpan(entity="PHONE", start=14, end=24),),
        ),
    ]
    # Detected the span, but labeled it AADHAAR instead of PHONE.
    predictions = {"p1": (PIISpan(entity="IN_AADHAAR", start=14, end=24),)}

    report = score_predictions(examples, predictions, baseline_overlap_recall=1.0)

    assert report.by_entity["PHONE"].overlap_recall == 0.0  # typed: miss
    assert report.overall.overlap_hits == 0
    assert report.coverage.overlap_recall == 1.0  # untyped: redacted
    assert report.coverage.exact_recall == 1.0
    assert report.passed_baseline  # the gate reads coverage, not typed
    assert "COVERAGE 1 1 1 1 1.0000 1.0000" in format_report(report)


def test_cli_gate_exits_nonzero_when_below_baseline(tmp_path, capsys, monkeypatch):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps(
            {
                "id": "p1",
                "text": "Ramesh",
                "entities": [{"start": 0, "end": 6, "entity": "NAME"}],
            }
        )
        + "\n"
    )

    def fake_score_examples(examples, baseline_overlap_recall=LEGACY_OVERLAP_BASELINE):
        return score_predictions(
            examples,
            {"p1": ()},
            baseline_overlap_recall=baseline_overlap_recall,
        )

    monkeypatch.setattr("janasunani.pipeline.pii_eval.score_examples", fake_score_examples)

    code = main(["--gold", str(gold), "--baseline-overlap", "1.0"])

    assert code == 1
    assert "passed=false" in capsys.readouterr().out
