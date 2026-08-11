import json

import pytest

from janasunani.evaluation.categorization import (
    CategorizationRecord,
    benchmark_hashing_classifier,
    load_jsonl,
    select_abstention_threshold,
    validate_records,
)
from janasunani.evaluation.classification import ScoredExample


def record(item_id, text, category, split, *, language="English", group=None):
    return CategorizationRecord(
        item_id=item_id,
        group_id=group or item_id,
        redacted_text=text,
        category=category,
        split=split,
        language=language,
    )


PHRASES = {
    "Water Supply": "broken hand pump drinking water pipeline leakage",
    "Pensions": "old age pension payment beneficiary allowance",
    "Roads": "damaged village road pothole bridge repair",
}


def test_private_jsonl_loader_rejects_raw_and_identity_fields(tmp_path):
    path = tmp_path / "categorization.jsonl"
    payload = {
        "item_id": "item-1",
        "group_id": "group-1",
        "redacted_text": "water supply is unavailable",
        "category": "Water Supply",
        "split": "train",
        "language": "unknown",
        "source_kind": "typed",
    }
    rows = []
    for split in ("train", "validation", "test"):
        row = dict(payload, item_id=f"item-{split}", group_id=f"group-{split}", split=split)
        rows.append(json.dumps(row))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert len(load_jsonl(path)) == 3

    bad = dict(payload, ticket_no="CMO1")
    path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        load_jsonl(path)


def dataset():
    rows = []
    for category, phrase in PHRASES.items():
        for i in range(15):
            rows.append(
                record(
                    f"train-{category}-{i}",
                    f"{phrase} training example {i}",
                    category,
                    "train",
                    language="Odia" if i % 2 else "English",
                )
            )
        for split in ("validation", "test"):
            for i in range(3):
                rows.append(
                    record(
                        f"{split}-{category}-{i}",
                        f"{phrase} heldout example {i}",
                        category,
                        split,
                        language="Odia" if i % 2 else "English",
                    )
                )
    return rows


def test_group_leakage_fails_closed():
    rows = dataset()
    rows[-1] = record(
        rows[-1].item_id,
        rows[-1].redacted_text,
        rows[-1].category,
        "test",
        group=rows[0].group_id,
    )
    with pytest.raises(ValueError, match="leaks"):
        validate_records(rows)


def test_abstention_threshold_is_selected_on_scored_validation_rows():
    labels = ("A", "B")
    examples = [
        ScoredExample("1", "A", {"A": 0.9, "B": 0.1}, "1"),
        ScoredExample("2", "A", {"A": 0.4, "B": 0.6}, "2"),
        ScoredExample("3", "B", {"A": 0.2, "B": 0.8}, "3"),
    ]
    threshold, metrics = select_abstention_threshold(
        examples,
        min_selective_accuracy=1.0,
        min_coverage=0.6,
    )

    assert labels == tuple(examples[0].probabilities)
    assert threshold == pytest.approx(0.8)
    assert metrics["coverage"] == pytest.approx(2 / 3)
    assert metrics["selective_accuracy"] == 1.0


def test_abstention_fallback_preserves_minimum_coverage_when_accuracy_is_unreachable():
    examples = [
        ScoredExample("1", "A", {"A": 0.9, "B": 0.1}, "1"),
        ScoredExample("2", "A", {"A": 0.4, "B": 0.6}, "2"),
        ScoredExample("3", "B", {"A": 0.45, "B": 0.55}, "3"),
        ScoredExample("4", "A", {"A": 0.51, "B": 0.49}, "4"),
    ]

    _, metrics = select_abstention_threshold(
        examples,
        min_selective_accuracy=1.0,
        min_coverage=0.75,
    )

    assert metrics["coverage"] >= 0.75


def test_hashing_baseline_reports_per_class_language_and_calibration():
    benchmark = benchmark_hashing_classifier(
        dataset(),
        alpha_values=(1e-5, 1e-4),
        n_features=2**12,
        min_selective_accuracy=0.5,
        min_coverage=0.5,
    )
    report = benchmark.report

    assert report["model_family"] == "hashing-word-char-sgd"
    assert report["selected_alpha"] in {1e-5, 1e-4}
    assert report["split_counts"] == {"train": 45, "validation": 9, "test": 9}
    assert report["test"]["n"] == 9
    assert set(report["test"]["per_class"]) == set(PHRASES)
    assert set(report["test_by_language"]) == {"English", "Odia"}
    assert 0.0 <= report["test"]["expected_calibration_error"] <= 1.0
    assert report["missing_training_labels"] == []
    assert len(report["candidate_validation"]) == 2


def test_invalid_hash_width_is_rejected():
    with pytest.raises(ValueError, match="power of two"):
        benchmark_hashing_classifier(dataset(), n_features=3_000)


def test_future_unseen_category_is_reported_not_silently_dropped():
    rows = dataset()
    rows.append(
        record(
            "test-new",
            "new category grievance",
            "New Category",
            "test",
        )
    )
    benchmark = benchmark_hashing_classifier(
        rows, alpha_values=(1e-4,), n_features=2**12
    )

    assert benchmark.report["missing_training_labels"] == ["New Category"]
    assert benchmark.report["test"]["accuracy"] < 1.0
