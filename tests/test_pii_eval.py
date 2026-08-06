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


def test_score_predictions_excludes_government_email_from_denominator():
    """#56: government email addresses are not PII by policy, so a gold
    EMAIL span on nic.in/gov.in/mil.in must not enter the gold denominator
    -- otherwise the tagger is scored against spans it is now deliberately
    built not to redact."""
    text = "Contact officer@nic.in or citizen@gmail.com for help."
    gov_start = text.index("officer@nic.in")
    gov_end = gov_start + len("officer@nic.in")
    citizen_start = text.index("citizen@gmail.com")
    citizen_end = citizen_start + len("citizen@gmail.com")

    examples = [
        GoldExample(
            id="p1",
            text=text,
            entities=(
                PIISpan(entity="EMAIL", start=gov_start, end=gov_end),
                PIISpan(entity="EMAIL", start=citizen_start, end=citizen_end),
            ),
        ),
    ]
    predictions = {
        "p1": (PIISpan(entity="EMAIL", start=citizen_start, end=citizen_end),),
    }

    report = score_predictions(examples, predictions, baseline_overlap_recall=0.0)

    assert report.excluded_by_policy == 1
    assert report.by_entity["EMAIL"].gold == 1  # only the citizen span counts
    assert report.by_entity["EMAIL"].overlap_hits == 1
    assert report.by_entity["EMAIL"].overlap_recall == 1.0
    assert report.overall.gold == 1
    assert report.to_dict()["excluded_by_policy"] == 1
    assert "excluded_by_policy=1" in format_report(report)


def _span_for(text: str, substring: str) -> tuple[int, int]:
    start = text.index(substring)
    return start, start + len(substring)


def test_score_predictions_excluded_count_sums_across_domains_and_examples():
    """Subdomains and every government suffix count, spread across pages."""
    text1 = "Reach the BDO at bdo.khordha@nic.in for the update."
    start1, end1 = _span_for(text1, "bdo.khordha@nic.in")

    text2 = "CC the PMO at officer@pmo.gov.in and the base at officer@station.mil.in."
    start2, end2 = _span_for(text2, "officer@pmo.gov.in")
    start3, end3 = _span_for(text2, "officer@station.mil.in")

    examples = [
        GoldExample(
            id="p1",
            text=text1,
            entities=(PIISpan(entity="EMAIL", start=start1, end=end1),),
        ),
        GoldExample(
            id="p2",
            text=text2,
            entities=(
                PIISpan(entity="EMAIL", start=start2, end=end2),
                PIISpan(entity="EMAIL", start=start3, end=end3),
            ),
        ),
    ]

    report = score_predictions(examples, {}, baseline_overlap_recall=0.0)

    assert report.excluded_by_policy == 3
    assert report.by_entity == {}  # every gold span in this fixture was excluded
    assert report.overall.gold == 0


def test_score_predictions_excluded_by_policy_defaults_to_zero_when_no_government_email():
    examples = [
        GoldExample(
            id="p1",
            text="Ramesh called 9876543210",
            entities=(PIISpan(entity="PHONE", start=14, end=24),),
        ),
    ]

    report = score_predictions(examples, {"p1": ()}, baseline_overlap_recall=0.0)

    assert report.excluded_by_policy == 0
    assert report.to_dict()["excluded_by_policy"] == 0


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


def test_an_entity_absent_from_the_gold_reports_na_not_zero():
    """#139/#67. An entity the gold never labels has no recall to report.
    Printing 0.0000 reads as total failure when it means the measurement
    cannot see the class -- and this table gets published."""
    from janasunani.pipeline.pii_eval import EntityMetrics, _format_metrics

    row = _format_metrics(
        "BANK_ACCOUNT", EntityMetrics(gold=0, predicted=7, overlap_hits=0, exact_hits=0)
    )
    assert "0.0000" not in row
    assert "n/a" in row
    assert "not labelled in this gold" in row


def test_a_labelled_entity_still_reports_numeric_recall():
    from janasunani.pipeline.pii_eval import EntityMetrics, _format_metrics

    row = _format_metrics(
        "NAME", EntityMetrics(gold=404, predicted=497, overlap_hits=176, exact_hits=106)
    )
    assert "0.4356" in row


class TestCorruptGoldIsRejectedOnTheScoringPath:
    """#89. verify_pii_gold caught duplicate spans; pii_eval could not, and
    pii_eval is the path that produces the published figure. A duplicated gold
    span inflates the denominator while contributing at most one hit, so recall
    is depressed and the output still looks plausible -- worse than a crash,
    because nothing downstream can tell a corrupt gold from a bad model."""

    TEXT = "Ravi Patra called 9876543210"

    def _write(self, tmp_path, entities):
        path = tmp_path / "gold.jsonl"
        path.write_text(
            json.dumps({"id": "t1_p1", "text": self.TEXT, "entities": entities}) + "\n",
            encoding="utf-8",
        )
        return path

    def test_exact_duplicate_span_is_rejected(self, tmp_path):
        path = self._write(
            tmp_path,
            [
                {"start": 0, "end": 10, "entity": "NAME"},
                {"start": 0, "end": 10, "entity": "NAME"},
            ],
        )
        with pytest.raises(ValueError, match="duplicate span"):
            load_gold_jsonl(path)

    def test_a_clean_file_still_loads(self, tmp_path):
        path = self._write(
            tmp_path,
            [
                {"start": 0, "end": 10, "entity": "NAME"},
                {"start": 18, "end": 28, "entity": "PHONE"},
            ],
        )
        assert len(load_gold_jsonl(path)) == 1

    def test_nested_overlap_is_reported_not_rejected(self, tmp_path):
        """A nested span is a labelling judgement, not file corruption."""
        from janasunani.pipeline.pii_eval import overlapping_spans

        path = self._write(
            tmp_path,
            [
                {"start": 0, "end": 10, "entity": "NAME"},
                {"start": 0, "end": 4, "entity": "NAME"},
            ],
        )
        examples = load_gold_jsonl(path)
        assert len(overlapping_spans(examples[0].entities)) == 1

    def test_no_overlaps_reported_for_a_clean_file(self, tmp_path):
        from janasunani.pipeline.pii_eval import overlapping_spans

        path = self._write(
            tmp_path,
            [
                {"start": 0, "end": 10, "entity": "NAME"},
                {"start": 18, "end": 28, "entity": "PHONE"},
            ],
        )
        examples = load_gold_jsonl(path)
        assert overlapping_spans(examples[0].entities) == []
