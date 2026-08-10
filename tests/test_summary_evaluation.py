import json

import pytest

from janasunani.evaluation.summary import (
    SummaryJudgment,
    build_scorecard,
    load_judgments,
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
        SummaryJudgment(
            **(skipped("x").__dict__ | {"unsupported_claims": 1})
        )


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
