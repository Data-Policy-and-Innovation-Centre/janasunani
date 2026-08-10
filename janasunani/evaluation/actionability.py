"""Train and evaluate a cheap local actionability baseline.

The model is intentionally a strong classical baseline: word and character
TF-IDF feeding multinomial logistic regression.  It is fast on CPU, handles
misspellings and romanized text better than a word-only model, emits class
probabilities, and gives every heavier candidate a real number to beat.

Administrative discard reasons are weak labels, not gold.  They may augment
training after the office-variation audit, but validation and test records
must be independently adjudicated.  Duplicate families are excluded because
deduplication is a separate task; out-of-scope and policy-blocked records keep
their own labels rather than being called spam.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from janasunani.evaluation.classification import (
    ScoredExample,
    assert_group_disjoint,
    classification_metrics,
    metrics_by_language,
)
from janasunani.evaluation.stats import wilson_interval
from janasunani.inference.actionability import (
    ACTIONABILITY_ARTIFACT_FORMAT,
    ACTIONABILITY_LABELS,
    ACTIONABILITY_TAXONOMY_VERSION,
    ActionabilityLabel,
    LocalActionabilityScorer,
    _sha256,
)

Split = Literal["train", "validation", "test"]
LabelSource = Literal[
    "adjudicated",
    "frontier_adjudicated",
    "administrative_weak",
]

MODEL_FAMILY = "tfidf-word-char-logreg"
MODEL_VERSION = "actionability-tfidf-v1"
BINARY_MODEL_VERSION = "actionability-review-tfidf-v1"


@dataclass(frozen=True)
class ActionabilityRecord:
    item_id: str
    redacted_text: str
    label: ActionabilityLabel
    group_id: str
    language: str
    split: Split
    label_source: LabelSource
    office: str | None = None
    sampling_stratum: str | None = None


@dataclass(frozen=True)
class WeakLabel:
    label: ActionabilityLabel | None
    eligible_for_training: bool
    rationale: str


WEAK_LABELS_BY_DISCARD_FAMILY: Mapping[str, WeakLabel] = {
    "details_inadequate": WeakLabel(
        "underspecified", True, "officer requested more grievance detail"
    ),
    "documents_not_attached": WeakLabel(
        "underspecified", True, "required supporting material was absent"
    ),
    "address_not_given": WeakLabel(
        "underspecified", True, "required location or contact detail was absent"
    ),
    "no_specific_grievance": WeakLabel(
        "irrelevant", True, "officer recorded no specific grievance"
    ),
    "outside_grievance_cell_purview": WeakLabel(
        "out_of_scope", True, "routing or jurisdiction failure, never spam"
    ),
    "policy_decision_required": WeakLabel(
        "policy_blocked", True, "resolution requires a policy decision"
    ),
    "case_already_taken_up": WeakLabel(
        None, False, "duplicate signal belongs to the deduplication task"
    ),
    "duplicate_copy": WeakLabel(
        None, False, "duplicate signal belongs to the deduplication task"
    ),
}

_RAW_TEXT_KEYS = {
    "grievance",
    "complaint_text",
    "raw_text",
    "grievance_text",
    "unredacted_text",
}


def weak_label_for_discard_family(family: str) -> WeakLabel:
    """Map an exact administrative family without inventing a catch-all."""

    try:
        return WEAK_LABELS_BY_DISCARD_FAMILY[family]
    except KeyError as exc:
        raise ValueError(f"unknown discard family {family!r}") from exc


def load_jsonl(path: Path) -> list[ActionabilityRecord]:
    """Load a governed manifest containing redacted text only."""

    records: list[ActionabilityRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number}: record must be an object")
        forbidden = _RAW_TEXT_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(
                f"line {line_number}: raw-text fields are forbidden: {sorted(forbidden)!r}"
            )
        required = {
            "item_id",
            "redacted_text",
            "label",
            "group_id",
            "language",
            "split",
            "label_source",
        }
        missing = required.difference(payload)
        if missing:
            raise ValueError(f"line {line_number}: missing fields {sorted(missing)!r}")
        unknown = set(payload).difference(required | {"office", "sampling_stratum"})
        if unknown:
            raise ValueError(f"line {line_number}: unknown fields {sorted(unknown)!r}")

        label = payload["label"]
        split = payload["split"]
        label_source = payload["label_source"]
        if label not in ACTIONABILITY_LABELS:
            raise ValueError(f"line {line_number}: label is outside the taxonomy")
        if split not in {"train", "validation", "test"}:
            raise ValueError(f"line {line_number}: invalid split")
        if label_source not in {
            "adjudicated",
            "frontier_adjudicated",
            "administrative_weak",
        }:
            raise ValueError(f"line {line_number}: invalid label_source")
        if split != "train" and label_source == "administrative_weak":
            raise ValueError(
                f"line {line_number}: validation/test labels must be adjudicated independently"
            )

        values = {
            key: payload[key]
            for key in ("item_id", "redacted_text", "group_id", "language")
        }
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ValueError(f"line {line_number}: string fields must be non-empty")
        office = payload.get("office")
        if office is not None and (not isinstance(office, str) or not office.strip()):
            raise ValueError(f"line {line_number}: office must be null or non-empty")
        sampling_stratum = payload.get("sampling_stratum")
        if sampling_stratum is not None and (
            not isinstance(sampling_stratum, str) or not sampling_stratum.strip()
        ):
            raise ValueError(
                f"line {line_number}: sampling_stratum must be null or non-empty"
            )
        records.append(
            ActionabilityRecord(
                item_id=payload["item_id"],
                redacted_text=payload["redacted_text"],
                label=label,
                group_id=payload["group_id"],
                language=payload["language"],
                split=split,
                label_source=label_source,
                office=office,
                sampling_stratum=sampling_stratum,
            )
        )
    validate_records(records)
    return records


def validate_records(records: Sequence[ActionabilityRecord]) -> None:
    if not records:
        raise ValueError("actionability manifest is empty")
    item_ids = [record.item_id for record in records]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("item_id values must be unique")
    assert_group_disjoint((record.group_id, record.split) for record in records)
    splits = {record.split for record in records}
    if splits != {"train", "validation", "test"}:
        raise ValueError("manifest must contain train, validation, and test splits")
    for record in records:
        if record.split != "train" and record.label_source == "administrative_weak":
            raise ValueError("validation/test labels must be adjudicated independently")
        if record.label_source == "administrative_weak" and record.label == "actionable":
            raise ValueError("administrative weak labels cannot establish actionability")


def sample_design_summary(
    records: Sequence[ActionabilityRecord],
) -> dict[str, object]:
    """Describe whether prevalence-sensitive metrics can be generalized."""

    strata = Counter(
        record.sampling_stratum
        for record in records
        if record.sampling_stratum is not None
    )
    if strata:
        return {
            "sampling_scheme": "fixed quotas across opaque sampling strata",
            "sampling_stratum_counts": dict(sorted(strata.items())),
            "production_prevalence_representative": False,
            "limitation": (
                "accuracy, precision, PPV, and review workload are specific to "
                "this designed sample composition and are not production prevalence"
            ),
        }
    return {
        "sampling_scheme": "unavailable_not_recorded",
        "sampling_stratum_counts": {},
        "production_prevalence_representative": "unavailable",
        "limitation": (
            "sampling design is unavailable; prevalence-sensitive metrics must not "
            "be generalized to production"
        ),
    }


def office_variation_audit(
    records: Sequence[ActionabilityRecord], *, min_office_support: int = 20
) -> dict[str, object]:
    """Measure how strongly weak administrative labels vary by office.

    Total-variation distance compares each sufficiently represented office's
    label distribution with the global weak-label distribution.  It does not
    prove bias or remove confounding; it is the pre-training alarm required by
    issue #74.  No office field is ever passed to the text model.
    """

    if min_office_support < 1:
        raise ValueError("min_office_support must be positive")
    weak = [
        record
        for record in records
        if record.label_source == "administrative_weak" and record.office
    ]
    if not weak:
        return {
            "status": "not_measured",
            "n": 0,
            "eligible_offices": 0,
            "max_total_variation": None,
            "by_office": {},
        }

    def distribution(rows: Sequence[ActionabilityRecord]) -> dict[str, float]:
        counts = {label: 0 for label in ACTIONABILITY_LABELS}
        for row in rows:
            counts[row.label] += 1
        return {label: counts[label] / len(rows) for label in ACTIONABILITY_LABELS}

    global_distribution = distribution(weak)
    offices = sorted({record.office for record in weak if record.office})
    by_office: dict[str, object] = {}
    max_tv = 0.0
    for office in offices:
        rows = [record for record in weak if record.office == office]
        if len(rows) < min_office_support:
            continue
        observed = distribution(rows)
        tv = 0.5 * sum(
            abs(observed[label] - global_distribution[label])
            for label in ACTIONABILITY_LABELS
        )
        max_tv = max(max_tv, tv)
        by_office[office] = {
            "n": len(rows),
            "total_variation": tv,
            "distribution": observed,
        }
    return {
        "status": "measured" if by_office else "insufficient_support",
        "n": len(weak),
        "eligible_offices": len(by_office),
        "max_total_variation": max_tv if by_office else None,
        "global_distribution": global_distribution,
        "by_office": by_office,
        "note": "descriptive bias alarm; not proof of office causation",
    }


def _build_classifier(c: float, *, min_df: int, max_features: int | None):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    features = FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    analyzer="word",
                    ngram_range=(1, 2),
                    min_df=min_df,
                    max_features=max_features,
                    sublinear_tf=True,
                    strip_accents=None,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    min_df=min_df,
                    max_features=max_features,
                    sublinear_tf=True,
                    strip_accents=None,
                ),
            ),
        ]
    )
    return Pipeline(
        [
            ("features", features),
            (
                "classifier",
                LogisticRegression(
                    C=c,
                    class_weight="balanced",
                    max_iter=2_000,
                    random_state=1729,
                ),
            ),
        ]
    )


def _scored_examples(classifier, records: Sequence[ActionabilityRecord]) -> list[ScoredExample]:
    matrix = classifier.predict_proba([record.redacted_text for record in records])
    classes = tuple(str(label) for label in classifier.classes_)
    return [
        ScoredExample(
            item_id=record.item_id,
            gold_label=record.label,
            probabilities={
                label: float(value)
                for label, value in zip(classes, row, strict=True)
            },
            group_id=record.group_id,
            language=record.language,
        )
        for record, row in zip(records, matrix, strict=True)
    ]


def review_metrics(
    examples: Sequence[ScoredExample], *, threshold: float
) -> dict[str, int | float]:
    """Binary review utility: non-actionable vs actionable."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    flagged = 0
    correct_review = 0
    actionable_flagged = 0
    actionable_total = 0
    non_actionable_total = 0
    for example in examples:
        predicted = min(
            ACTIONABILITY_LABELS,
            key=lambda label: (-example.probabilities[label], label),
        )
        confidence = example.probabilities[predicted]
        is_flagged = bool(
            predicted != "actionable"
            and threshold < 1.0
            and confidence >= threshold
        )
        is_non_actionable = example.gold_label != "actionable"
        actionable_total += not is_non_actionable
        non_actionable_total += is_non_actionable
        flagged += is_flagged
        correct_review += is_flagged and is_non_actionable
        actionable_flagged += is_flagged and not is_non_actionable
    return {
        "threshold": threshold,
        "flagged": flagged,
        "review_precision": correct_review / flagged if flagged else 1.0,
        "review_recall": (
            correct_review / non_actionable_total if non_actionable_total else 0.0
        ),
        "actionable_review_rate": (
            actionable_flagged / actionable_total if actionable_total else 0.0
        ),
    }


def select_review_threshold(
    examples: Sequence[ScoredExample],
    *,
    min_precision: float = 0.9,
    max_actionable_review_rate: float = 0.05,
) -> tuple[float, dict[str, int | float]]:
    """Maximize review recall subject to validation-only harm constraints."""

    if not 0.0 <= min_precision <= 1.0:
        raise ValueError("min_precision must be in [0, 1]")
    if not 0.0 <= max_actionable_review_rate <= 1.0:
        raise ValueError("max_actionable_review_rate must be in [0, 1]")
    thresholds = {1.0}
    for example in examples:
        thresholds.update(float(value) for value in example.probabilities.values())
    candidates = [
        review_metrics(examples, threshold=threshold)
        for threshold in sorted(thresholds)
    ]
    eligible = [
        row
        for row in candidates
        if float(row["review_precision"]) >= min_precision
        and float(row["actionable_review_rate"]) <= max_actionable_review_rate
    ]
    chosen = max(
        eligible,
        key=lambda row: (
            float(row["review_recall"]),
            int(row["flagged"]),
            float(row["threshold"]),
        ),
    )
    return float(chosen["threshold"]), chosen


@dataclass
class ActionabilityBenchmark:
    classifier: Any
    method: str
    review_threshold: float
    report: dict[str, object]

    def scorer(self) -> LocalActionabilityScorer:
        return LocalActionabilityScorer(
            self.classifier,
            method=self.method,
            review_threshold=self.review_threshold,
        )

    def save(self, out_dir: Path) -> dict[str, Path]:
        """Write a checksummed model artifact and its non-sensitive report.

        Refuses a non-empty target so a prior promoted artifact cannot be
        silently replaced.  Promotion remains a separate DVC-tracked action.
        """

        import joblib

        target = Path(out_dir)
        if target.exists() and any(target.iterdir()):
            raise FileExistsError(f"artifact directory is not empty: {target}")
        target.mkdir(parents=True, exist_ok=True)
        model_path = target / "classifier.joblib"
        report_path = target / "benchmark.json"
        manifest_path = target / "manifest.json"

        report_json = json.dumps(self.report, indent=2, sort_keys=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=".classifier-", suffix=".joblib", dir=target
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        try:
            joblib.dump(self.classifier, temporary_path)
            temporary_path.replace(model_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        report_path.write_text(report_json, encoding="utf-8")
        manifest = {
            "artifact_format": ACTIONABILITY_ARTIFACT_FORMAT,
            "taxonomy_version": ACTIONABILITY_TAXONOMY_VERSION,
            "labels": list(ACTIONABILITY_LABELS),
            "method": self.method,
            "review_threshold": self.review_threshold,
            "model_file": model_path.name,
            "model_sha256": _sha256(model_path),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        return {
            "model": model_path,
            "manifest": manifest_path,
            "benchmark": report_path,
        }


def benchmark_tfidf(
    records: Sequence[ActionabilityRecord],
    *,
    c_values: Sequence[float] = (0.5, 1.0, 2.0, 4.0),
    min_df: int = 2,
    max_features: int | None = 250_000,
    min_review_precision: float = 0.9,
    max_actionable_review_rate: float = 0.05,
    office_min_support: int = 20,
    max_office_total_variation: float = 0.25,
) -> ActionabilityBenchmark:
    """Select hyperparameters and threshold on validation, report test once."""

    validate_records(records)
    if not c_values or any(not math.isfinite(c) or c <= 0 for c in c_values):
        raise ValueError("c_values must contain positive finite values")
    if min_df < 1:
        raise ValueError("min_df must be positive")
    train = [record for record in records if record.split == "train"]
    validation = [record for record in records if record.split == "validation"]
    test = [record for record in records if record.split == "test"]

    missing_train = set(ACTIONABILITY_LABELS).difference(record.label for record in train)
    if missing_train:
        raise ValueError(f"training split is missing classes: {sorted(missing_train)!r}")
    for split_name, rows in (("validation", validation), ("test", test)):
        missing = set(ACTIONABILITY_LABELS).difference(record.label for record in rows)
        if missing:
            raise ValueError(
                f"{split_name} split is missing classes: {sorted(missing)!r}"
            )

    weak_train = [record for record in train if record.label_source == "administrative_weak"]
    office_audit = office_variation_audit(
        records, min_office_support=office_min_support
    )
    if weak_train:
        if int(office_audit["eligible_offices"]) < 2:
            raise ValueError(
                "administrative weak labels require at least two offices with "
                "enough support for the office-variation audit"
            )
        max_variation = float(office_audit["max_total_variation"])
        if max_variation > max_office_total_variation:
            raise ValueError(
                "office-label total variation exceeds the training gate: "
                f"{max_variation:.3f} > {max_office_total_variation:.3f}"
            )

    candidates: list[tuple[float, Any, list[ScoredExample], dict[str, object]]] = []
    for c in c_values:
        classifier = _build_classifier(c, min_df=min_df, max_features=max_features)
        classifier.fit(
            [record.redacted_text for record in train],
            [record.label for record in train],
        )
        validation_examples = _scored_examples(classifier, validation)
        metrics = classification_metrics(
            validation_examples,
            expected_labels=ACTIONABILITY_LABELS,
            top_k=(1, 2, 3),
        )
        candidates.append((c, classifier, validation_examples, metrics))

    c, classifier, validation_examples, validation_metrics = max(
        candidates,
        key=lambda row: (
            float(row[3]["macro_f1"]),
            -float(row[3]["log_loss"]),
            -row[0],
        ),
    )
    threshold, validation_review = select_review_threshold(
        validation_examples,
        min_precision=min_review_precision,
        max_actionable_review_rate=max_actionable_review_rate,
    )
    test_examples = _scored_examples(classifier, test)
    test_metrics = classification_metrics(
        test_examples,
        expected_labels=ACTIONABILITY_LABELS,
        top_k=(1, 2, 3),
        abstain_threshold=threshold,
    )
    test_by_language = metrics_by_language(
        test_examples,
        expected_labels=ACTIONABILITY_LABELS,
        top_k=(1, 2, 3),
        abstain_threshold=threshold,
    )
    method = f"{MODEL_VERSION}-c{c:g}"
    report: dict[str, object] = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "method": method,
        "taxonomy_version": ACTIONABILITY_TAXONOMY_VERSION,
        "selected_c": c,
        "review_threshold": threshold,
        "split_counts": {
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        },
        "validation": validation_metrics,
        "validation_review": validation_review,
        "test": test_metrics,
        "test_review": review_metrics(test_examples, threshold=threshold),
        "test_by_language": test_by_language,
        "office_variation": office_audit,
        "candidate_validation": [
            {
                "c": candidate_c,
                "macro_f1": metrics["macro_f1"],
                "log_loss": metrics["log_loss"],
            }
            for candidate_c, _, _, metrics in candidates
        ],
        "sample_design": sample_design_summary(records),
        "safety": {
            "raw_text_fields_forbidden": True,
            "validation_test_require_adjudication": True,
            "duplicate_families_excluded": True,
            "advisory_only": True,
        },
    }
    return ActionabilityBenchmark(
        classifier=classifier,
        method=method,
        review_threshold=threshold,
        report=report,
    )


def _binary_review_metrics(
    review_probabilities: Sequence[float],
    records: Sequence[ActionabilityRecord],
    *,
    threshold: float,
) -> dict[str, object]:
    if len(review_probabilities) != len(records) or not records:
        raise ValueError("binary review metrics require aligned non-empty inputs")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    if any(
        not math.isfinite(value) or not 0.0 <= value <= 1.0
        for value in review_probabilities
    ):
        raise ValueError("review probabilities must be finite and in [0, 1]")
    flagged = [threshold < 1.0 and value >= threshold for value in review_probabilities]
    gold_review = [record.label != "actionable" for record in records]
    true_positive = sum(
        predicted and gold
        for predicted, gold in zip(flagged, gold_review, strict=True)
    )
    false_positive = sum(
        predicted and not gold
        for predicted, gold in zip(flagged, gold_review, strict=True)
    )
    true_negative = sum(
        not predicted and not gold
        for predicted, gold in zip(flagged, gold_review, strict=True)
    )
    false_negative = sum(
        not predicted and gold
        for predicted, gold in zip(flagged, gold_review, strict=True)
    )
    predicted_review = true_positive + false_positive
    actual_review = true_positive + false_negative
    actionable = true_negative + false_positive
    precision = true_positive / predicted_review if predicted_review else 1.0
    recall = true_positive / actual_review if actual_review else 0.0
    actionable_review_rate = false_positive / actionable if actionable else 0.0
    accuracy = (true_positive + true_negative) / len(records)
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0

    def interval(successes: int, total: int) -> list[float]:
        estimate = wilson_interval(successes, total)
        return [estimate.ci_low, estimate.ci_high]

    by_reason: dict[str, dict[str, int | float]] = {}
    for label in ACTIONABILITY_LABELS:
        if label == "actionable":
            continue
        indices = [index for index, record in enumerate(records) if record.label == label]
        caught = sum(flagged[index] for index in indices)
        by_reason[label] = {
            "support": len(indices),
            "review_recall": caught / len(indices) if indices else 0.0,
        }
    return {
        "n": len(records),
        "threshold": threshold,
        "actual_review": actual_review,
        "flagged": predicted_review,
        "confusion": {
            "true_review": true_positive,
            "false_review": false_positive,
            "true_actionable": true_negative,
            "missed_review": false_negative,
        },
        "accuracy": accuracy,
        "accuracy_ci": interval(true_positive + true_negative, len(records)),
        "review_precision": precision,
        "review_precision_ci": (
            interval(true_positive, predicted_review) if predicted_review else None
        ),
        "review_recall": recall,
        "review_recall_ci": (
            interval(true_positive, actual_review) if actual_review else None
        ),
        "actionable_review_rate": actionable_review_rate,
        "actionable_review_rate_ci": (
            interval(false_positive, actionable) if actionable else None
        ),
        "f1": f1,
        "by_non_actionable_reason": by_reason,
    }


def _select_binary_review_threshold(
    review_probabilities: Sequence[float],
    records: Sequence[ActionabilityRecord],
    *,
    min_precision: float,
    max_actionable_review_rate: float,
) -> tuple[float, dict[str, object]]:
    thresholds = sorted({0.0, 1.0, *(float(value) for value in review_probabilities)})
    candidates = [
        _binary_review_metrics(review_probabilities, records, threshold=threshold)
        for threshold in thresholds
    ]
    eligible = [
        metrics
        for metrics in candidates
        if float(metrics["review_precision"]) >= min_precision
        and float(metrics["actionable_review_rate"]) <= max_actionable_review_rate
    ]
    chosen = max(
        eligible,
        key=lambda metrics: (
            float(metrics["review_recall"]),
            float(metrics["review_precision"]),
            float(metrics["accuracy"]),
            float(metrics["threshold"]),
        ),
    )
    return float(chosen["threshold"]), chosen


@dataclass
class BinaryReviewBenchmark:
    classifier: Any
    review_threshold: float
    report: dict[str, object]


def benchmark_binary_review(
    records: Sequence[ActionabilityRecord],
    *,
    c_values: Sequence[float] = (0.1, 0.5, 1.0, 2.0, 10.0),
    min_df: int = 1,
    max_features: int | None = 250_000,
    min_review_precision: float = 0.9,
    max_actionable_review_rate: float = 0.05,
) -> BinaryReviewBenchmark:
    """Benchmark actionable-versus-review when five-class support is incomplete.

    This is a development scorecard, not a deployable substitute for the full
    five-class contract. It answers the immediate operational question—whether
    a grievance should be shown for extra officer review—while retaining each
    non-actionable reason in the held-out breakdown.
    """

    validate_records(records)
    if not c_values or any(not math.isfinite(c) or c <= 0.0 for c in c_values):
        raise ValueError("c_values must contain positive finite values")
    if not 0.0 <= min_review_precision <= 1.0:
        raise ValueError("min_review_precision must be in [0, 1]")
    if not 0.0 <= max_actionable_review_rate <= 1.0:
        raise ValueError("max_actionable_review_rate must be in [0, 1]")
    splits = {
        name: [record for record in records if record.split == name]
        for name in ("train", "validation", "test")
    }
    for name, rows in splits.items():
        binary_labels = {record.label != "actionable" for record in rows}
        if binary_labels != {False, True}:
            raise ValueError(f"{name} split must contain actionable and review examples")

    candidates: list[tuple[float, Any, float, dict[str, object]]] = []
    for c in c_values:
        classifier = _build_classifier(c, min_df=min_df, max_features=max_features)
        # ``liblinear`` is a stable sparse binary solver and avoids routing this
        # two-class objective through the multinomial-oriented LBFGS path.
        classifier.set_params(classifier__solver="liblinear")
        classifier.fit(
            [record.redacted_text for record in splits["train"]],
            [
                "review" if record.label != "actionable" else "actionable"
                for record in splits["train"]
            ],
        )
        classes = tuple(str(label) for label in classifier.classes_)
        review_index = classes.index("review")
        validation_probabilities = [
            float(row[review_index])
            for row in classifier.predict_proba(
                [record.redacted_text for record in splits["validation"]]
            )
        ]
        threshold, metrics = _select_binary_review_threshold(
            validation_probabilities,
            splits["validation"],
            min_precision=min_review_precision,
            max_actionable_review_rate=max_actionable_review_rate,
        )
        candidates.append((c, classifier, threshold, metrics))
    c, classifier, threshold, validation_metrics = max(
        candidates,
        key=lambda row: (
            float(row[3]["review_recall"]),
            float(row[3]["review_precision"]),
            float(row[3]["accuracy"]),
            -row[0],
        ),
    )
    classes = tuple(str(label) for label in classifier.classes_)
    review_index = classes.index("review")
    test_probabilities = [
        float(row[review_index])
        for row in classifier.predict_proba(
            [record.redacted_text for record in splits["test"]]
        )
    ]
    test_metrics = _binary_review_metrics(
        test_probabilities, splits["test"], threshold=threshold
    )
    observed_labels = set(record.label for record in records)
    missing_labels = sorted(set(ACTIONABILITY_LABELS).difference(observed_labels))
    report: dict[str, object] = {
        "model_family": MODEL_FAMILY,
        "model_version": BINARY_MODEL_VERSION,
        "objective": "actionable_vs_officer_review",
        "selected_c": c,
        "review_threshold": threshold,
        "split_counts": {name: len(rows) for name, rows in splits.items()},
        "gold_label_distribution": dict(
            sorted(Counter(record.label for record in records).items())
        ),
        "missing_five_class_support": missing_labels,
        "validation": validation_metrics,
        "test": test_metrics,
        "candidate_validation": [
            {
                "c": candidate_c,
                "threshold": candidate_threshold,
                "review_precision": metrics["review_precision"],
                "review_recall": metrics["review_recall"],
                "actionable_review_rate": metrics["actionable_review_rate"],
            }
            for candidate_c, _, candidate_threshold, metrics in candidates
        ],
        "sample_design": sample_design_summary(records),
        "release_eligible": False,
        "limitations": [
            "frontier-adjudicated development gold is not officer-confirmed truth",
            "single-snapshot hash splits are not chronological release evidence",
            "duplicate-group isolation is unavailable in this pilot sample",
            "binary review does not replace the complete five-class production contract",
        ],
    }
    return BinaryReviewBenchmark(
        classifier=classifier,
        review_threshold=threshold,
        report=report,
    )
