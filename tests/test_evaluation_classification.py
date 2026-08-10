import math

import pytest

from janasunani.evaluation.classification import (
    ScoredExample,
    assert_group_disjoint,
    classification_metrics,
    group_split,
    metrics_by_language,
)


LABELS = ("actionable", "irrelevant", "underspecified")


def example(
    item_id: str,
    gold: str,
    probabilities: tuple[float, float, float],
    *,
    group: str | None = None,
    language: str = "English",
) -> ScoredExample:
    return ScoredExample(
        item_id=item_id,
        gold_label=gold,
        probabilities=dict(zip(LABELS, probabilities, strict=True)),
        group_id=group or item_id,
        language=language,
    )


def sample() -> list[ScoredExample]:
    return [
        example("a", "actionable", (0.8, 0.1, 0.1), group="campaign-1"),
        example("b", "actionable", (0.4, 0.5, 0.1), group="campaign-1"),
        example("c", "irrelevant", (0.1, 0.7, 0.2), language="Odia"),
        example("d", "underspecified", (0.2, 0.3, 0.5), language="Odia"),
    ]


def test_group_split_is_stable_and_keeps_a_group_together():
    first = group_split("duplicate-group-7")
    assert first == group_split("duplicate-group-7")
    assert {group_split(f"group-{i}") for i in range(200)} == {
        "train",
        "validation",
        "test",
    }


def test_group_split_validates_fractions():
    with pytest.raises(ValueError, match="sum"):
        group_split("g", test_fraction=0.6, validation_fraction=0.4)
    with pytest.raises(ValueError, match="group_id"):
        group_split("")


def test_group_disjointness_rejects_duplicate_leakage():
    assert_group_disjoint([("campaign-1", "train"), ("campaign-2", "test")])
    with pytest.raises(ValueError, match="leaks"):
        assert_group_disjoint([("campaign-1", "train"), ("campaign-1", "test")])


def test_metrics_include_per_class_top_k_calibration_and_abstention():
    result = classification_metrics(
        sample(),
        expected_labels=LABELS,
        top_k=(1, 2),
        abstain_threshold=0.6,
        calibration_bins=5,
    )

    assert result["n"] == 4
    assert result["accuracy"] == pytest.approx(0.75)
    assert result["balanced_accuracy"] == pytest.approx((0.5 + 1.0 + 1.0) / 3)
    assert result["top_k_accuracy"] == {"1": 0.75, "2": 1.0}
    assert result["coverage"] == pytest.approx(0.5)
    assert result["selective_accuracy"] == 1.0
    assert result["selective_risk"] == 0.0
    assert result["abstained"] == 2
    assert result["per_class"]["actionable"]["support"] == 2
    assert result["confusion"]["actionable"]["irrelevant"] == 1
    assert result["support_complete"] is True
    assert result["language_support"] == {"English": 2, "Odia": 2}
    assert 0.0 <= result["expected_calibration_error"] <= 1.0
    assert result["log_loss"] > 0.0
    assert result["multiclass_brier"] > 0.0


def test_perfect_probabilities_have_zero_loss_brier_and_ece():
    rows = [
        example("a", "actionable", (1.0, 0.0, 0.0)),
        example("b", "irrelevant", (0.0, 1.0, 0.0)),
        example("c", "underspecified", (0.0, 0.0, 1.0)),
    ]
    result = classification_metrics(rows, expected_labels=LABELS)

    assert result["accuracy"] == 1.0
    assert result["macro_f1"] == 1.0
    assert result["log_loss"] == 0.0
    assert result["multiclass_brier"] == 0.0
    assert result["expected_calibration_error"] == 0.0


def test_declared_but_absent_class_is_not_dropped_from_macro_score():
    rows = [
        example("a", "actionable", (0.8, 0.1, 0.1)),
        example("b", "irrelevant", (0.1, 0.8, 0.1)),
    ]
    result = classification_metrics(rows, expected_labels=LABELS)

    assert result["accuracy"] == 1.0
    assert result["macro_f1"] == pytest.approx(2 / 3)
    assert result["support_complete"] is False
    assert result["per_class"]["underspecified"]["support"] == 0


@pytest.mark.parametrize(
    "probabilities, message",
    [
        ((0.5, 0.2, 0.2), "sum to one"),
        ((math.nan, 0.5, 0.5), "invalid probability"),
        ((-0.1, 0.5, 0.6), "invalid probability"),
    ],
)
def test_invalid_probabilities_fail_closed(probabilities, message):
    with pytest.raises(ValueError, match=message):
        classification_metrics(
            [example("a", "actionable", probabilities)],
            expected_labels=LABELS,
        )


def test_unknown_taxonomy_label_fails_closed():
    row = ScoredExample(
        item_id="a",
        gold_label="spam",
        probabilities={"actionable": 0.5, "irrelevant": 0.5, "underspecified": 0.0},
        group_id="a",
    )
    with pytest.raises(ValueError, match="outside the expected taxonomy"):
        classification_metrics([row], expected_labels=LABELS)


def test_metrics_by_language_keeps_full_taxonomy_in_each_slice():
    result = metrics_by_language(
        sample(),
        expected_labels=LABELS,
        abstain_threshold=0.0,
    )

    assert set(result) == {"English", "Odia"}
    assert result["English"]["n"] == 2
    assert result["Odia"]["n"] == 2
    assert result["English"]["labels"] == list(LABELS)
    assert result["English"]["support_complete"] is False


def test_duplicate_item_ids_and_missing_probabilities_are_rejected():
    rows = sample()
    with pytest.raises(ValueError, match="unique"):
        classification_metrics([rows[0], rows[0]], expected_labels=LABELS)

    incomplete = ScoredExample(
        item_id="x",
        gold_label="actionable",
        probabilities={"actionable": 1.0},
        group_id="x",
    )
    with pytest.raises(ValueError, match="every expected class"):
        classification_metrics([incomplete], expected_labels=LABELS)


def test_metrics_honor_aggregate_weights_and_report_wilson_interval():
    metrics = classification_metrics(
        [
            ScoredExample("a", "yes", {"yes": 0.9, "no": 0.1}, "ga", weight=9),
            ScoredExample("b", "yes", {"yes": 0.1, "no": 0.9}, "gb", weight=1),
        ],
        expected_labels=("yes", "no"),
    )

    assert metrics["n"] == 10
    assert metrics["accuracy"] == pytest.approx(0.9)
    assert metrics["confusion"]["yes"] == {"yes": 9, "no": 1}
    assert metrics["accuracy_interval"]["method"] == "wilson"
    assert metrics["accuracy_interval"]["ci_low"] < 0.9
    assert metrics["accuracy_interval"]["ci_high"] > 0.9


def test_metrics_reject_invalid_weight():
    with pytest.raises(ValueError, match="weight"):
        classification_metrics(
            [ScoredExample("a", "yes", {"yes": 1.0}, "ga", weight=0)]
        )
