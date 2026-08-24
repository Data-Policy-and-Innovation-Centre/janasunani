"""Shared classification metrics for categorization, triage, and routing.

The project has three classification-shaped decisions with the same failure
modes: a headline accuracy can hide a weak class, duplicated grievances can
leak across a random split, and a confident wrong answer is more dangerous
than an explicit abstention.  This module keeps those checks in one
dependency-light implementation so candidate models are compared on the same
contract.

Inputs are already-scored, non-sensitive records.  This module performs no
I/O and never sees grievance text.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from janasunani.evaluation.stats import wilson_interval


@dataclass(frozen=True)
class ScoredExample:
    """One held-out prediction with calibrated class probabilities.

    ``group_id`` identifies records that must stay in one split, such as a
    duplicate cluster or citizen-independent campaign group.  ``language`` is
    required so an aggregate cannot silently stand in for Odia, romanized
    Odia, and English performance.
    """

    item_id: str
    gold_label: str
    probabilities: Mapping[str, float]
    group_id: str
    language: str = "unknown"
    weight: int = 1


def group_split(
    group_id: str,
    *,
    test_fraction: float = 0.2,
    validation_fraction: float = 0.1,
    salt: str = "janasunani-eval-v1",
) -> str:
    """Assign a stable group to ``train``, ``validation``, or ``test``.

    Hashing the group, rather than the row, prevents duplicates from leaking
    across splits.  The salt is versioned so a changed split is an explicit
    artifact change rather than an accidental reshuffle.
    """

    if not group_id:
        raise ValueError("group_id must be non-empty")
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")
    if not 0.0 <= validation_fraction < 1.0:
        raise ValueError("validation_fraction must be in [0, 1)")
    if test_fraction + validation_fraction >= 1.0:
        raise ValueError("test and validation fractions must sum to less than 1")

    digest = hashlib.sha256(f"{salt}\0{group_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") / 2**64
    if bucket < test_fraction:
        return "test"
    if bucket < test_fraction + validation_fraction:
        return "validation"
    return "train"


def assert_group_disjoint(
    assignments: Iterable[tuple[str, str]],
) -> None:
    """Fail when one group appears in more than one named split."""

    seen: dict[str, str] = {}
    for group_id, split in assignments:
        if not group_id:
            raise ValueError("group_id must be non-empty")
        previous = seen.setdefault(group_id, split)
        if previous != split:
            raise ValueError(
                f"group {group_id!r} leaks across {previous!r} and {split!r}"
            )


def _validate(
    examples: Sequence[ScoredExample], expected_labels: Sequence[str] | None
) -> tuple[str, ...]:
    if not examples:
        raise ValueError("at least one scored example is required")
    item_ids = [example.item_id for example in examples]
    if any(not item_id for item_id in item_ids):
        raise ValueError("item_id must be non-empty")
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("item_id values must be unique")
    if any(not example.group_id for example in examples):
        raise ValueError("group_id must be non-empty")
    if any(not example.language for example in examples):
        raise ValueError("language must be non-empty")
    if any(
        isinstance(example.weight, bool)
        or not isinstance(example.weight, int)
        or example.weight < 1
        for example in examples
    ):
        raise ValueError("weight must be a positive integer")

    observed = {
        label
        for example in examples
        for label in (example.gold_label, *example.probabilities)
    }
    labels = tuple(expected_labels) if expected_labels is not None else tuple(sorted(observed))
    if not labels or any(not label for label in labels):
        raise ValueError("expected labels must be non-empty")
    if len(set(labels)) != len(labels):
        raise ValueError("expected labels must be unique")
    unknown = observed.difference(labels)
    if unknown:
        raise ValueError(f"labels outside the expected taxonomy: {sorted(unknown)!r}")

    for example in examples:
        if set(example.probabilities) != set(labels):
            raise ValueError(
                f"{example.item_id!r} must provide every expected class probability"
            )
        probabilities = tuple(example.probabilities[label] for label in labels)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0.0
            or value > 1.0
            for value in probabilities
        ):
            raise ValueError(f"{example.item_id!r} has an invalid probability")
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"{example.item_id!r} probabilities must sum to one")
    return labels


def _ranked(example: ScoredExample, labels: Sequence[str]) -> list[str]:
    return sorted(labels, key=lambda label: (-example.probabilities[label], label))


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def classification_metrics(
    examples: Sequence[ScoredExample],
    *,
    expected_labels: Sequence[str] | None = None,
    top_k: Sequence[int] = (1, 3),
    abstain_threshold: float = 0.0,
    calibration_bins: int = 10,
) -> dict[str, object]:
    """Return JSON-safe held-out metrics with abstention and calibration.

    Macro scores average over the complete declared taxonomy.  A missing
    class therefore contributes zero instead of disappearing from the
    headline.  ``support_complete`` makes the corresponding release gate
    explicit.
    """

    labels = _validate(examples, expected_labels)
    if not 0.0 <= abstain_threshold <= 1.0:
        raise ValueError("abstain_threshold must be in [0, 1]")
    if calibration_bins < 2:
        raise ValueError("calibration_bins must be at least 2")
    if not top_k or any(k < 1 for k in top_k):
        raise ValueError("top_k values must be positive")

    n = sum(example.weight for example in examples)
    predictions: list[str] = []
    confidences: list[float] = []
    correct: list[bool] = []
    ranked_labels: list[list[str]] = []
    for example in examples:
        ranked = _ranked(example, labels)
        prediction = ranked[0]
        confidence = float(example.probabilities[prediction])
        predictions.append(prediction)
        confidences.append(confidence)
        correct.append(prediction == example.gold_label)
        ranked_labels.append(ranked)

    confusion = {
        gold: {predicted: 0 for predicted in labels}
        for gold in labels
    }
    for example, prediction in zip(examples, predictions, strict=True):
        confusion[example.gold_label][prediction] += example.weight

    per_class: dict[str, dict[str, int | float]] = {}
    for label in labels:
        tp = confusion[label][label]
        support = sum(confusion[label].values())
        predicted = sum(confusion[gold][label] for gold in labels)
        precision = _safe_ratio(tp, predicted)
        recall = _safe_ratio(tp, support)
        f1 = _safe_ratio(2.0 * precision * recall, precision + recall)
        per_class[label] = {
            "support": support,
            "predicted": predicted,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    correct_n = sum(
        example.weight
        for example, is_correct in zip(examples, correct, strict=True)
        if is_correct
    )
    accuracy = correct_n / n
    macro_precision = sum(float(row["precision"]) for row in per_class.values()) / len(labels)
    macro_recall = sum(float(row["recall"]) for row in per_class.values()) / len(labels)
    macro_f1 = sum(float(row["f1"]) for row in per_class.values()) / len(labels)
    weighted_f1 = sum(
        float(row["f1"]) * int(row["support"])
        for row in per_class.values()
    ) / n

    top_k_accuracy = {
        str(k): sum(
            example.weight
            for example, ranked in zip(examples, ranked_labels, strict=True)
            if example.gold_label in ranked[: min(k, len(labels))]
        )
        / n
        for k in sorted(set(top_k))
    }
    epsilon = 1e-15
    log_loss = -sum(
        example.weight
        * math.log(max(float(example.probabilities[example.gold_label]), epsilon))
        for example in examples
    ) / n
    brier = sum(
        example.weight
        * sum(
            (float(example.probabilities[label]) - (1.0 if example.gold_label == label else 0.0)) ** 2
            for label in labels
        )
        for example in examples
    ) / n

    ece = 0.0
    calibration: list[dict[str, int | float]] = []
    for index in range(calibration_bins):
        lower = index / calibration_bins
        upper = (index + 1) / calibration_bins
        members = [
            row
            for row, confidence in enumerate(confidences)
            if lower <= confidence < upper or (index == calibration_bins - 1 and confidence == 1.0)
        ]
        if not members:
            continue
        bin_n = sum(examples[row].weight for row in members)
        bin_correct = sum(
            examples[row].weight for row in members if correct[row]
        )
        bin_accuracy = bin_correct / bin_n
        mean_confidence = sum(
            examples[row].weight * confidences[row] for row in members
        ) / bin_n
        ece += bin_n / n * abs(bin_accuracy - mean_confidence)
        calibration.append(
            {
                "lower": lower,
                "upper": upper,
                "n": bin_n,
                "accuracy": bin_accuracy,
                "mean_confidence": mean_confidence,
            }
        )

    retained = [index for index, confidence in enumerate(confidences) if confidence >= abstain_threshold]
    retained_n = sum(examples[index].weight for index in retained)
    retained_correct = sum(
        examples[index].weight for index in retained if correct[index]
    )
    coverage = retained_n / n
    selective_accuracy = _safe_ratio(retained_correct, retained_n)

    languages: Counter[str] = Counter()
    for example in examples:
        languages[example.language] += example.weight
    accuracy_ci = wilson_interval(correct_n, n)
    selective_ci = (
        wilson_interval(retained_correct, retained_n) if retained_n else None
    )
    return {
        "n": n,
        "labels": list(labels),
        "support_complete": all(int(row["support"]) > 0 for row in per_class.values()),
        "language_support": dict(sorted(languages.items())),
        "accuracy": accuracy,
        "accuracy_interval": {
            "ci_low": accuracy_ci.ci_low,
            "ci_high": accuracy_ci.ci_high,
            "alpha": accuracy_ci.alpha,
            "method": accuracy_ci.method,
        },
        "balanced_accuracy": macro_recall,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "top_k_accuracy": top_k_accuracy,
        "log_loss": log_loss,
        "multiclass_brier": brier,
        "expected_calibration_error": ece,
        "abstain_threshold": abstain_threshold,
        "coverage": coverage,
        "selective_accuracy": selective_accuracy,
        "selective_accuracy_interval": (
            {
                "ci_low": selective_ci.ci_low,
                "ci_high": selective_ci.ci_high,
                "alpha": selective_ci.alpha,
                "method": selective_ci.method,
            }
            if selective_ci
            else None
        ),
        "selective_risk": 1.0 - selective_accuracy if retained_n else 0.0,
        "abstained": n - retained_n,
        "per_class": per_class,
        "confusion": confusion,
        "calibration": calibration,
    }


def metrics_by_language(
    examples: Sequence[ScoredExample],
    *,
    expected_labels: Sequence[str],
    **metric_options: object,
) -> dict[str, dict[str, object]]:
    """Evaluate each language independently without dropping absent classes."""

    _validate(examples, expected_labels)
    languages = sorted({example.language for example in examples})
    return {
        language: classification_metrics(
            [example for example in examples if example.language == language],
            expected_labels=expected_labels,
            **metric_options,
        )
        for language in languages
    }
