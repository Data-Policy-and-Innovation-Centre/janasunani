import json

import pytest

from janasunani.evaluation.actionability import (
    ActionabilityRecord,
    _binary_review_metrics,
    _select_binary_review_threshold,
    benchmark_binary_review,
    benchmark_tfidf,
    load_jsonl,
    office_variation_audit,
    select_review_threshold,
    weak_label_for_discard_family,
)
from janasunani.evaluation.classification import ScoredExample
from janasunani.inference.actionability import (
    ACTIONABILITY_LABELS,
    BINARY_REVIEW_LABELS,
    BINARY_REVIEW_OBJECTIVE,
)
from janasunani.inference.actionability import load_actionability_scorer


def record(
    item_id,
    text,
    label,
    split,
    *,
    language="English",
    source="adjudicated",
    office=None,
):
    return ActionabilityRecord(
        item_id=item_id,
        redacted_text=text,
        label=label,
        group_id=item_id,
        language=language,
        split=split,
        label_source=source,
        office=office,
    )


PHRASES = {
    "actionable": [
        "hand pump broken near school for two weeks",
        "pension payment not received since January",
        "street light is not working in ward seven",
    ],
    "underspecified": [
        "please solve my problem details not provided",
        "document missing please attach required paper",
        "location and address are not given",
    ],
    "irrelevant": [
        "test message no specific grievance hello",
        "nothing to report this is a demo entry",
        "random greeting without any grievance",
    ],
    "out_of_scope": [
        "private company matter outside government jurisdiction",
        "court appeal is outside grievance cell purview",
        "request belongs to another jurisdiction and department",
    ],
    "policy_blocked": [
        "benefit requires a new government policy decision",
        "scheme change cannot proceed until policy is approved",
        "requested entitlement needs cabinet policy approval",
    ],
}


def dataset():
    rows = []
    for label, phrases in PHRASES.items():
        for i in range(10):
            rows.append(
                record(
                    f"train-{label}-{i}",
                    f"{phrases[i % len(phrases)]} case {i}",
                    label,
                    "train",
                    language="Odia" if i % 2 else "English",
                    source=(
                        "administrative_weak"
                        if i < 4 and label != "actionable"
                        else "adjudicated"
                    ),
                    office="Office A" if i < 2 else "Office B",
                )
            )
        rows.append(
            record(
                f"validation-{label}",
                f"{phrases[0]} validation",
                label,
                "validation",
            )
        )
        rows.append(
            record(
                f"test-{label}",
                f"{phrases[1]} heldout",
                label,
                "test",
                language="Odia" if label in {"actionable", "irrelevant"} else "English",
            )
        )
    return rows


def test_weak_labels_keep_non_spam_reasons_distinct_and_exclude_duplicates():
    assert weak_label_for_discard_family("details_inadequate").label == "underspecified"
    out_of_scope = weak_label_for_discard_family("outside_grievance_cell_purview")
    assert out_of_scope.label == "out_of_scope"
    assert "never spam" in out_of_scope.rationale
    duplicate = weak_label_for_discard_family("duplicate_copy")
    assert duplicate.label is None
    assert duplicate.eligible_for_training is False
    with pytest.raises(ValueError, match="unknown discard"):
        weak_label_for_discard_family("anything_else")


def test_jsonl_rejects_raw_text_and_weak_holdout(tmp_path):
    raw = {
        "item_id": "1",
        "redacted_text": "safe",
        "grievance": "raw",
        "label": "actionable",
        "group_id": "g1",
        "language": "English",
        "split": "train",
        "label_source": "adjudicated",
    }
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(raw) + "\n")
    with pytest.raises(ValueError, match="raw-text fields"):
        load_jsonl(path)

    raw.pop("grievance")
    raw["split"] = "test"
    raw["label_source"] = "administrative_weak"
    path.write_text(json.dumps(raw) + "\n")
    with pytest.raises(ValueError, match="must be adjudicated"):
        load_jsonl(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("label", ["actionable"], "outside the taxonomy"),
        ("split", ["train"], "invalid split"),
        ("label_source", ["adjudicated"], "invalid label_source"),
    ],
)
def test_jsonl_rejects_non_string_enum_fields(tmp_path, field, value, message):
    payload = {
        "item_id": "item-1",
        "redacted_text": "redacted grievance",
        "label": "actionable",
        "group_id": "group-1",
        "language": "English",
        "split": "train",
        "label_source": "adjudicated",
    }
    payload[field] = value
    path = tmp_path / "malformed.jsonl"
    path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match=message):
        load_jsonl(path)


def test_jsonl_requires_group_disjoint_splits(tmp_path):
    rows = []
    for split, suffix in (("train", "a"), ("validation", "b"), ("test", "c")):
        rows.append(
            {
                "item_id": suffix,
                "redacted_text": "safe redacted content",
                "label": "actionable",
                "group_id": "same-group" if split != "validation" else "other-group",
                "language": "English",
                "split": split,
                "label_source": "adjudicated",
            }
        )
    path = tmp_path / "manifest.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="leaks"):
        load_jsonl(path)


def test_office_variation_is_descriptive_and_never_a_model_feature():
    rows = [
        record(
            f"train-{i}",
            "redacted",
            "irrelevant" if i < 3 else "actionable",
            "train",
            source="administrative_weak",
            office="A" if i < 3 else "B",
        )
        for i in range(6)
    ]
    result = office_variation_audit(rows, min_office_support=2)

    assert result["status"] == "measured"
    assert result["eligible_offices"] == 2
    assert result["max_total_variation"] == pytest.approx(0.5)
    assert "office" not in benchmark_tfidf.__doc__.lower()


def test_threshold_selection_maximizes_recall_under_precision_constraint():
    def scored(item_id, gold, actionable, irrelevant):
        rest = (1.0 - actionable - irrelevant) / 3
        return ScoredExample(
            item_id=item_id,
            gold_label=gold,
            probabilities={
                "actionable": actionable,
                "underspecified": rest,
                "irrelevant": irrelevant,
                "out_of_scope": rest,
                "policy_blocked": rest,
            },
            group_id=item_id,
        )

    rows = [
        scored("a", "actionable", 0.8, 0.05),
        scored("b", "actionable", 0.4, 0.45),
        scored("c", "irrelevant", 0.1, 0.7),
        scored("d", "irrelevant", 0.2, 0.6),
    ]
    threshold, metrics = select_review_threshold(
        rows,
        min_precision=1.0,
        max_actionable_review_rate=0.0,
    )

    assert threshold == pytest.approx(0.6)
    assert metrics["review_precision"] == 1.0
    assert metrics["review_recall"] == 1.0


def test_tfidf_benchmark_selects_on_validation_and_reports_heldout_slices():
    benchmark = benchmark_tfidf(
        dataset(),
        c_values=(0.5, 1.0),
        min_df=1,
        max_features=5_000,
        min_review_precision=0.0,
        max_actionable_review_rate=1.0,
        office_min_support=5,
    )

    report = benchmark.report
    assert report["model_family"] == "tfidf-word-char-logreg"
    assert report["selected_c"] in {0.5, 1.0}
    assert report["split_counts"] == {"train": 50, "validation": 5, "test": 5}
    assert report["test"]["n"] == 5
    assert set(report["test"]["per_class"]) == set(ACTIONABILITY_LABELS)
    assert set(report["test_by_language"]) == {"English", "Odia"}
    assert report["safety"]["advisory_only"] is True
    assert len(report["candidate_validation"]) == 2

    result = benchmark.scorer().score("random greeting without any grievance")
    assert result.method == benchmark.method
    assert result.predicted_label in ACTIONABILITY_LABELS


def test_binary_threshold_selection_honors_harm_constraints():
    rows = [
        record("a1", "actionable one", "actionable", "validation"),
        record("a2", "actionable two", "actionable", "validation"),
        record("r1", "irrelevant", "irrelevant", "validation"),
        record("r2", "underspecified", "underspecified", "validation"),
        record("r3", "policy", "policy_blocked", "validation"),
    ]

    threshold, metrics = _select_binary_review_threshold(
        [0.05, 0.4, 0.9, 0.8, 0.3],
        rows,
        min_precision=1.0,
        max_actionable_review_rate=0.0,
    )

    assert threshold == pytest.approx(0.8)
    assert metrics["review_precision"] == 1.0
    assert metrics["review_recall"] == pytest.approx(2 / 3)
    assert metrics["actionable_review_rate"] == 0.0
    assert metrics["confusion"] == {
        "true_review": 2,
        "false_review": 0,
        "true_actionable": 2,
        "missed_review": 1,
    }


def test_binary_metrics_report_missing_reason_support_and_intervals():
    rows = [
        record("a", "actionable", "actionable", "test"),
        record("i", "irrelevant", "irrelevant", "test"),
        record("u", "underspecified", "underspecified", "test"),
    ]

    metrics = _binary_review_metrics([0.1, 0.9, 0.2], rows, threshold=0.5)

    assert metrics["confusion"] == {
        "true_review": 1,
        "false_review": 0,
        "true_actionable": 1,
        "missed_review": 1,
    }
    assert metrics["accuracy"] == pytest.approx(2 / 3)
    assert len(metrics["accuracy_ci"]) == 2
    assert metrics["by_non_actionable_reason"]["out_of_scope"] == {
        "support": 0,
        "review_recall": 0.0,
    }


def test_binary_benchmark_allows_development_gold_with_missing_class_support():
    rows = [row for row in dataset() if row.label != "out_of_scope"]
    rows = [
        ActionabilityRecord(
            **{
                **row.__dict__,
                "label_source": "frontier_adjudicated",
                "sampling_stratum": "s5",
            }
        )
        for row in rows
    ]

    benchmark = benchmark_binary_review(
        rows,
        c_values=(0.5, 1.0),
        min_df=1,
        max_features=5_000,
        min_review_precision=0.0,
        max_actionable_review_rate=1.0,
    )

    assert benchmark.report["objective"] == "actionable_vs_officer_review"
    assert benchmark.report["selected_c"] in {0.5, 1.0}
    assert benchmark.report["split_counts"] == {
        "train": 40,
        "validation": 4,
        "test": 4,
    }
    assert benchmark.report["missing_five_class_support"] == ["out_of_scope"]
    assert benchmark.report["test"]["by_non_actionable_reason"]["out_of_scope"] == {
        "support": 0,
        "review_recall": 0.0,
    }
    assert benchmark.report["release_eligible"] is False
    assert benchmark.report["sample_design"] == {
        "sampling_scheme": "fixed quotas across opaque sampling strata",
        "sampling_stratum_counts": {"s5": 48},
        "production_prevalence_representative": False,
        "limitation": (
            "accuracy, precision, PPV, and review workload are specific to "
            "this designed sample composition and are not production prevalence"
        ),
    }
    assert len(benchmark.report["candidate_validation"]) == 2


def test_artifact_round_trip_is_checksummed_and_refuses_overwrite(tmp_path):
    benchmark = benchmark_tfidf(
        dataset(),
        c_values=(1.0,),
        min_df=1,
        max_features=2_000,
        min_review_precision=0.0,
        max_actionable_review_rate=1.0,
        office_min_support=5,
    )
    artifact_dir = tmp_path / "actionability"
    paths = benchmark.save(artifact_dir)

    assert set(paths) == {"model", "manifest", "benchmark"}
    loaded = load_actionability_scorer(artifact_dir)
    assert loaded.method == benchmark.method
    assert loaded.score("test message no specific grievance").predicted_label in (
        ACTIONABILITY_LABELS
    )

    with pytest.raises(FileExistsError, match="not empty"):
        benchmark.save(artifact_dir)

    paths["model"].write_bytes(paths["model"].read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_actionability_scorer(artifact_dir)


def test_actionability_loader_rejects_symlinked_model_file(tmp_path):
    benchmark = benchmark_binary_review(
        dataset(),
        c_values=(1.0,),
        min_df=1,
        max_features=2_000,
        min_review_precision=0.0,
        max_actionable_review_rate=1.0,
    )
    artifact_dir = tmp_path / "actionability"
    paths = benchmark.save(artifact_dir)
    outside_model = tmp_path / "outside.joblib"
    outside_model.write_bytes(paths["model"].read_bytes())
    paths["model"].unlink()
    paths["model"].symlink_to(outside_model)

    with pytest.raises(ValueError, match="must not be a symlink"):
        load_actionability_scorer(artifact_dir)


def test_binary_review_artifact_round_trip_is_serving_compatible(tmp_path):
    benchmark = benchmark_binary_review(
        dataset(),
        c_values=(1.0,),
        min_df=1,
        max_features=2_000,
        min_review_precision=0.0,
        max_actionable_review_rate=1.0,
    )
    artifact_dir = tmp_path / "binary-actionability"

    paths = benchmark.save(artifact_dir)
    loaded = load_actionability_scorer(artifact_dir)
    result = loaded.score("test message no specific grievance")

    assert set(loaded._classes) == set(BINARY_REVIEW_LABELS)
    assert result.objective == BINARY_REVIEW_OBJECTIVE
    assert result.predicted_label in BINARY_REVIEW_LABELS
    assert set(paths) == {"model", "manifest", "benchmark"}

    paths["model"].write_bytes(paths["model"].read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_actionability_scorer(artifact_dir)


def test_artifact_loader_rejects_manifest_path_escape(tmp_path):
    artifact_dir = tmp_path / "actionability"
    artifact_dir.mkdir()
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "artifact_format": 1,
                "taxonomy_version": "actionability-v1",
                "labels": list(ACTIONABILITY_LABELS),
                "method": "model",
                "review_threshold": 0.5,
                "model_file": "../outside.joblib",
                "model_sha256": "0" * 64,
            }
        )
    )
    with pytest.raises(ValueError, match="one filename"):
        load_actionability_scorer(artifact_dir)


def test_weak_labels_are_blocked_when_office_variation_is_too_large():
    rows = dataset()
    rows = [
        ActionabilityRecord(
            **{
                **row.__dict__,
                "office": (
                    "Biased Office"
                    if row.label_source == "administrative_weak"
                    and row.label == "irrelevant"
                    else row.office
                ),
            }
        )
        for row in rows
    ]
    with pytest.raises(ValueError, match="total variation exceeds"):
        benchmark_tfidf(
            rows,
            c_values=(1.0,),
            min_df=1,
            max_features=1_000,
            office_min_support=2,
            max_office_total_variation=0.01,
        )


def test_administrative_labels_cannot_call_a_record_actionable():
    rows = dataset()
    rows[0] = ActionabilityRecord(
        **{**rows[0].__dict__, "label_source": "administrative_weak"}
    )
    with pytest.raises(ValueError, match="cannot establish actionability"):
        benchmark_tfidf(rows, c_values=(1.0,), min_df=1)
