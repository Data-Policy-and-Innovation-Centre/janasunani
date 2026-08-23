"""Scalable local baseline and held-out scorecard for grievance categories.

The baseline uses stateless word and character hashing followed by a
probabilistic linear classifier.  It can train over a large corpus without a
vocabulary-sized memory spike and gives MuRIL or a modern multilingual encoder
an inexpensive CPU number to beat.  Promotion still depends on per-language,
per-class, calibration, and abstention results from adjudicated or frozen
administrative labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from janasunani.evaluation.classification import (
    ScoredExample,
    assert_group_disjoint,
    classification_metrics,
    metrics_by_language,
)


MODEL_FAMILY = "hashing-word-char-sgd"
MODEL_VERSION = "categorizer-hashing-v1"
REPORT_VERSION = "janasunani.categorization-scorecard/v1"
_REQUIRED_FIELDS = {
    "item_id",
    "group_id",
    "redacted_text",
    "category",
    "split",
    "language",
    "source_kind",
}
_FORBIDDEN_FIELDS = {
    "grievance",
    "raw_text",
    "ticket_no",
    "petitioner_name",
    "petitioner_mobile",
    "petitioner_email",
}


@dataclass(frozen=True)
class CategorizationRecord:
    item_id: str
    group_id: str
    redacted_text: str
    category: str
    split: str
    language: str = "unknown"
    source_kind: str = "typed"
    subcategory: str | None = None


def load_jsonl(path: Path) -> list[CategorizationRecord]:
    """Load the strict redacted-only private benchmark manifest."""

    records: list[CategorizationRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number} must be an object")
        forbidden = set(payload).intersection(_FORBIDDEN_FIELDS)
        if forbidden:
            raise ValueError(
                f"line {line_number} contains forbidden raw/identity fields: "
                f"{sorted(forbidden)}"
            )
        unknown = set(payload) - (_REQUIRED_FIELDS | {"subcategory"})
        missing = _REQUIRED_FIELDS - set(payload)
        if unknown or missing:
            raise ValueError(
                f"line {line_number} schema mismatch; missing={sorted(missing)} "
                f"unknown={sorted(unknown)}"
            )
        records.append(CategorizationRecord(**payload))
    validate_records(records)
    return records


def validate_records(records: Sequence[CategorizationRecord]) -> None:
    if not records:
        raise ValueError("categorization benchmark is empty")
    ids = [record.item_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("item_id values must be unique")
    if {record.split for record in records} != {"train", "validation", "test"}:
        raise ValueError("benchmark requires train, validation, and test splits")
    assert_group_disjoint((record.group_id, record.split) for record in records)
    for record in records:
        if record.split not in {"train", "validation", "test"}:
            raise ValueError(f"invalid split {record.split!r}")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                record.item_id,
                record.group_id,
                record.redacted_text,
                record.category,
                record.language,
                record.source_kind,
            )
        ):
            raise ValueError("categorization string fields must be non-empty")


def _build_classifier(*, alpha: float, n_features: int):
    from sklearn.feature_extraction.text import HashingVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.pipeline import FeatureUnion, Pipeline

    features = FeatureUnion(
        [
            (
                "word",
                HashingVectorizer(
                    analyzer="word",
                    ngram_range=(1, 2),
                    n_features=n_features,
                    alternate_sign=False,
                    norm="l2",
                    lowercase=True,
                ),
            ),
            (
                "char",
                HashingVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    n_features=n_features,
                    alternate_sign=False,
                    norm="l2",
                    lowercase=True,
                ),
            ),
        ]
    )
    return Pipeline(
        [
            ("features", features),
            (
                "classifier",
                SGDClassifier(
                    loss="log_loss",
                    penalty="l2",
                    alpha=alpha,
                    class_weight="balanced",
                    max_iter=100,
                    tol=1e-4,
                    average=True,
                    random_state=1729,
                ),
            ),
        ]
    )


def _score(
    classifier,
    records: Sequence[CategorizationRecord],
    *,
    expected_labels: Sequence[str],
) -> list[ScoredExample]:
    matrix = classifier.predict_proba([record.redacted_text for record in records])
    trained_labels = tuple(str(label) for label in classifier.classes_)
    return [
        ScoredExample(
            item_id=record.item_id,
            gold_label=record.category,
            probabilities={
                label: (
                    float(row[trained_labels.index(label)])
                    if label in trained_labels
                    else 0.0
                )
                for label in expected_labels
            },
            group_id=record.group_id,
            language=record.language,
        )
        for record, row in zip(records, matrix, strict=True)
    ]


def select_abstention_threshold(
    examples: Sequence[ScoredExample],
    *,
    min_selective_accuracy: float,
    min_coverage: float,
) -> tuple[float, dict[str, object]]:
    """Maximize validation coverage subject to a minimum retained accuracy."""

    if not 0.0 <= min_selective_accuracy <= 1.0:
        raise ValueError("min_selective_accuracy must be in [0, 1]")
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage must be in [0, 1]")
    thresholds = {0.0, 1.0}
    thresholds.update(
        max(float(value) for value in example.probabilities.values())
        for example in examples
    )
    candidates = [
        classification_metrics(
            examples,
            expected_labels=tuple(examples[0].probabilities),
            abstain_threshold=threshold,
        )
        for threshold in sorted(thresholds)
    ]
    eligible = [
        metrics
        for metrics in candidates
        if float(metrics["selective_accuracy"]) >= min_selective_accuracy
        and float(metrics["coverage"]) >= min_coverage
    ]
    if not eligible:
        coverage_eligible = [
            metrics
            for metrics in candidates
            if float(metrics["coverage"]) >= min_coverage
        ]
        fallback = max(
            coverage_eligible or candidates,
            key=lambda metrics: (
                float(metrics["selective_accuracy"]),
                float(metrics["coverage"]),
                float(metrics["abstain_threshold"]),
            ),
        )
        return float(fallback["abstain_threshold"]), fallback
    chosen = max(
        eligible,
        key=lambda metrics: (
            float(metrics["coverage"]),
            float(metrics["selective_accuracy"]),
            float(metrics["abstain_threshold"]),
        ),
    )
    return float(chosen["abstain_threshold"]), chosen


@dataclass
class CategorizationBenchmark:
    classifier: Any
    abstain_threshold: float
    report: dict[str, object]


def benchmark_hashing_classifier(
    records: Sequence[CategorizationRecord],
    *,
    alpha_values: Sequence[float] = (1e-6, 1e-5, 1e-4, 1e-3),
    n_features: int = 2**18,
    min_selective_accuracy: float = 0.8,
    min_coverage: float = 0.5,
) -> CategorizationBenchmark:
    """Tune on validation and report the untouched test split once."""

    validate_records(records)
    if not alpha_values or any(
        not math.isfinite(alpha) or alpha <= 0.0 for alpha in alpha_values
    ):
        raise ValueError("alpha_values must contain positive finite values")
    if n_features < 2**10 or n_features & (n_features - 1):
        raise ValueError("n_features must be a power of two of at least 1024")
    train = [record for record in records if record.split == "train"]
    validation = [record for record in records if record.split == "validation"]
    test = [record for record in records if record.split == "test"]
    expected_labels = tuple(sorted({record.category for record in records}))
    missing_train = set(expected_labels).difference(record.category for record in train)

    candidates: list[tuple[float, Any, list[ScoredExample], dict[str, object]]] = []
    for alpha in alpha_values:
        classifier = _build_classifier(alpha=alpha, n_features=n_features)
        classifier.fit(
            [record.redacted_text for record in train],
            [record.category for record in train],
        )
        validation_examples = _score(
            classifier, validation, expected_labels=expected_labels
        )
        metrics = classification_metrics(
            validation_examples,
            expected_labels=expected_labels,
            top_k=(1, 3, 5),
        )
        candidates.append((alpha, classifier, validation_examples, metrics))
    alpha, classifier, validation_examples, validation_metrics = max(
        candidates,
        key=lambda row: (
            float(row[3]["macro_f1"]),
            float(row[3]["top_k_accuracy"]["3"]),
            -float(row[3]["log_loss"]),
            -row[0],
        ),
    )
    threshold, validation_selective = select_abstention_threshold(
        validation_examples,
        min_selective_accuracy=min_selective_accuracy,
        min_coverage=min_coverage,
    )
    test_examples = _score(classifier, test, expected_labels=expected_labels)
    test_metrics = classification_metrics(
        test_examples,
        expected_labels=expected_labels,
        top_k=(1, 3, 5),
        abstain_threshold=threshold,
    )
    report: dict[str, object] = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "selected_alpha": alpha,
        "n_features_per_analyzer": n_features,
        "abstain_threshold": threshold,
        "split_counts": Counter(record.split for record in records),
        "source_counts": Counter(record.source_kind for record in records),
        "missing_training_labels": sorted(missing_train),
        "validation": validation_metrics,
        "validation_selective": validation_selective,
        "selective_constraints_met": (
            float(validation_selective["selective_accuracy"])
            >= min_selective_accuracy
            and float(validation_selective["coverage"]) >= min_coverage
        ),
        "test": test_metrics,
        "test_by_language": metrics_by_language(
            test_examples,
            expected_labels=expected_labels,
            top_k=(1, 3, 5),
            abstain_threshold=threshold,
        ),
        "test_by_source_kind": {
            source_kind: classification_metrics(
                [
                    example
                    for example, record in zip(test_examples, test, strict=True)
                    if record.source_kind == source_kind
                ],
                expected_labels=expected_labels,
                top_k=(1, 3, 5),
                abstain_threshold=threshold,
            )
            for source_kind in sorted({record.source_kind for record in test})
        },
        "candidate_validation": [
            {
                "alpha": candidate_alpha,
                "macro_f1": metrics["macro_f1"],
                "top3_accuracy": metrics["top_k_accuracy"]["3"],
                "log_loss": metrics["log_loss"],
            }
            for candidate_alpha, _, _, metrics in candidates
        ],
        "limitations": [
            "administrative categories measure historical labels, not policy correctness",
            "typed and scanned sources must be reported separately when both are present",
            "subcategory remains a separate hierarchical task",
        ],
    }
    return CategorizationBenchmark(
        classifier=classifier,
        abstain_threshold=threshold,
        report=report,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alpha-values", type=float, nargs="+", default=[1e-6, 1e-5, 1e-4, 1e-3])
    parser.add_argument("--n-features", type=int, default=2**18)
    parser.add_argument("--min-selective-accuracy", type=float, default=0.8)
    parser.add_argument("--min-coverage", type=float, default=0.5)
    args = parser.parse_args(argv)
    benchmark = benchmark_hashing_classifier(
        load_jsonl(args.dataset),
        alpha_values=args.alpha_values,
        n_features=args.n_features,
        min_selective_accuracy=args.min_selective_accuracy,
        min_coverage=args.min_coverage,
    )
    digest = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    report = {
        "report_version": REPORT_VERSION,
        "dataset_sha256": f"sha256:{digest}",
        "label_interpretation": "historical administrative agreement, not policy correctness",
        "release_eligible": False,
        **benchmark.report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "dataset_sha256": report["dataset_sha256"],
                "test_top1": report["test"]["top_k_accuracy"]["1"],
                "test_top3": report["test"]["top_k_accuracy"]["3"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
