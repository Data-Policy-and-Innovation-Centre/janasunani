import math

import pytest

from janasunani.inference.actionability import (
    ACTIONABILITY_LABELS,
    ACTIONABILITY_TAXONOMY_VERSION,
    BINARY_REVIEW_LABELS,
    BINARY_REVIEW_OBJECTIVE,
    ActionabilityAssessment,
    LocalActionabilityScorer,
    LocalBinaryReviewScorer,
)


class Classifier:
    classes_ = ACTIONABILITY_LABELS

    def __init__(self, row):
        self.row = row
        self.seen = None

    def predict_proba(self, texts):
        self.seen = texts
        return [self.row]


class BinaryClassifier(Classifier):
    classes_ = BINARY_REVIEW_LABELS


def probabilities(**overrides):
    values = {
        "actionable": 0.7,
        "underspecified": 0.1,
        "irrelevant": 0.05,
        "out_of_scope": 0.1,
        "policy_blocked": 0.05,
    }
    values.update(overrides)
    return [values[label] for label in ACTIONABILITY_LABELS]


def test_high_confidence_non_actionable_prediction_requests_review():
    classifier = Classifier(
        probabilities(
            actionable=0.1,
            underspecified=0.7,
            irrelevant=0.1,
            out_of_scope=0.05,
        )
    )
    scorer = LocalActionabilityScorer(
        classifier,
        method="tfidf-logreg-v1",
        review_threshold=0.6,
    )

    result = scorer.score("[VILLAGE] road location missing")

    assert result.decision == "review"
    assert result.predicted_label == "underspecified"
    assert result.confidence == pytest.approx(0.7)
    assert result.taxonomy_version == ACTIONABILITY_TAXONOMY_VERSION
    assert classifier.seen == ["[VILLAGE] road location missing"]


def test_actionable_prediction_abstains_instead_of_approving_or_rejecting():
    scorer = LocalActionabilityScorer(
        Classifier(probabilities()),
        method="model-v1",
        review_threshold=0.6,
    )

    result = scorer.score("The hand pump has been broken for two weeks")

    assert result.predicted_label == "actionable"
    assert result.decision == "abstained"


def test_binary_review_scorer_requests_review_without_inventing_a_reason():
    scorer = LocalBinaryReviewScorer(
        BinaryClassifier([0.15, 0.85]),
        method="tfidf-review-v1",
        review_threshold=0.7,
    )

    result = scorer.score("please help with this incomplete request")

    assert result.decision == "review"
    assert result.predicted_label == "review_required"
    assert result.objective == BINARY_REVIEW_OBJECTIVE
    assert result.probabilities == {
        "actionable": 0.15,
        "review_required": 0.85,
    }


def test_binary_review_scorer_uses_governed_threshold_below_argmax_boundary():
    scorer = LocalBinaryReviewScorer(
        BinaryClassifier([0.55, 0.45]),
        method="tfidf-review-v1",
        review_threshold=0.4350314715184363,
    )

    result = scorer.score("borderline review request")

    assert result.decision == "review"
    assert result.predicted_label == "review_required"
    assert result.confidence == pytest.approx(0.45)


def test_binary_review_scorer_abstains_below_validation_threshold():
    scorer = LocalBinaryReviewScorer(
        BinaryClassifier([0.45, 0.55]),
        method="tfidf-review-v1",
        review_threshold=0.7,
    )

    result = scorer.score("uncertain request")

    assert result.decision == "abstained"
    assert result.predicted_label == "actionable"
    assert result.confidence == pytest.approx(0.45)


def test_low_confidence_non_actionable_prediction_abstains():
    row = [0.19, 0.24, 0.25, 0.18, 0.14]
    scorer = LocalActionabilityScorer(
        Classifier(row),
        method="model-v1",
        review_threshold=0.6,
    )

    result = scorer.score("uncertain text")

    assert result.predicted_label == "irrelevant"
    assert result.decision == "abstained"


def test_threshold_one_is_explicit_review_nothing_failsafe():
    scorer = LocalActionabilityScorer(
        Classifier([0.0, 0.0, 1.0, 0.0, 0.0]),
        method="model-v1",
        review_threshold=1.0,
    )

    result = scorer.score("certain but unsafe candidate")

    assert result.predicted_label == "irrelevant"
    assert result.decision == "abstained"


@pytest.mark.parametrize(
    "row, message",
    [
        ([0.2] * 5, None),
        ([0.2, 0.2, 0.2, 0.2, 0.1], "sum to one"),
        ([math.nan, 0.25, 0.25, 0.25, 0.25], "finite"),
        ([1.0, 0.0], "width"),
    ],
)
def test_probability_contract(row, message):
    scorer = LocalActionabilityScorer(
        Classifier(row),
        method="model-v1",
        review_threshold=0.6,
    )
    if message is None:
        assert scorer.score("text").confidence == pytest.approx(0.2)
    else:
        with pytest.raises(ValueError, match=message):
            scorer.score("text")


def test_classifier_classes_must_match_closed_taxonomy():
    classifier = Classifier(probabilities())
    classifier.classes_ = (*ACTIONABILITY_LABELS[:-1], "spam")

    with pytest.raises(ValueError, match="exactly match"):
        LocalActionabilityScorer(
            classifier,
            method="model-v1",
            review_threshold=0.6,
        )


def test_assessment_rejects_review_for_actionable_label():
    probs = dict(zip(ACTIONABILITY_LABELS, probabilities(), strict=True))
    with pytest.raises(ValueError, match="do not request"):
        ActionabilityAssessment(
            decision="review",
            predicted_label="actionable",
            confidence=0.7,
            probabilities=probs,
            method="model-v1",
        )


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("decision", "approve", "decision"),
        ("confidence", True, "confidence"),
        ("method", None, "method"),
        ("taxonomy_version", "actionability-v2", "taxonomy_version"),
    ],
)
def test_assessment_rejects_invalid_runtime_contract_values(field, value, message):
    kwargs = {
        "decision": "abstained",
        "predicted_label": "actionable",
        "confidence": 0.7,
        "probabilities": dict(zip(ACTIONABILITY_LABELS, probabilities(), strict=True)),
        "method": "model-v1",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        ActionabilityAssessment(**kwargs)


@pytest.mark.parametrize("threshold", [True, float("nan"), float("inf")])
def test_scorer_rejects_boolean_or_nonfinite_thresholds(threshold):
    with pytest.raises(ValueError, match="review_threshold"):
        LocalActionabilityScorer(
            Classifier(probabilities()),
            method="model-v1",
            review_threshold=threshold,
        )


def test_only_redacted_text_shape_is_accepted():
    scorer = LocalActionabilityScorer(
        Classifier(probabilities()),
        method="model-v1",
        review_threshold=0.6,
    )
    with pytest.raises(TypeError, match="redacted_text"):
        scorer.score({"grievance": "raw citizen text"})  # type: ignore[arg-type]
