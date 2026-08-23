"""Local, advisory actionability scoring over PII-redacted text.

Actionability is deliberately not squeezed into the existing spam score.
An underspecified grievance may need one missing fact; an out-of-scope one
needs a better route; a policy-blocked one may be entirely legitimate.  Those
are different officer actions and different model errors.

The scorer is import-light and model-agnostic.  Training lives in
``janasunani.evaluation``; this module only validates a local classifier's
probabilities and turns them into an advisory result.  It never performs I/O,
changes submission status, or accepts raw grievance fields.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence


ACTIONABILITY_TAXONOMY_VERSION = "actionability-v1"
ACTIONABILITY_ARTIFACT_FORMAT = 1
BINARY_REVIEW_ARTIFACT_FORMAT = 2
BINARY_REVIEW_OBJECTIVE = "actionable_vs_officer_review"

ActionabilityLabel = Literal[
    "actionable",
    "underspecified",
    "irrelevant",
    "out_of_scope",
    "policy_blocked",
]

ACTIONABILITY_LABELS: tuple[ActionabilityLabel, ...] = (
    "actionable",
    "underspecified",
    "irrelevant",
    "out_of_scope",
    "policy_blocked",
)

BinaryReviewLabel = Literal["actionable", "review_required"]
BINARY_REVIEW_LABELS: tuple[BinaryReviewLabel, ...] = (
    "actionable",
    "review_required",
)

ActionabilityOutputLabel = ActionabilityLabel | BinaryReviewLabel
ActionabilityObjective = Literal["five_class_reason", "actionable_vs_officer_review"]

ActionabilityDecision = Literal["review", "abstained"]


class ProbabilityClassifier(Protocol):
    """The small subset of a fitted local classifier the scorer needs."""

    classes_: Sequence[str]

    def predict_proba(self, texts: Sequence[str]) -> object: ...


@dataclass(frozen=True)
class ActionabilityAssessment:
    """One non-blocking assessment suitable for an additive API field."""

    decision: ActionabilityDecision
    predicted_label: ActionabilityOutputLabel
    confidence: float
    probabilities: Mapping[ActionabilityOutputLabel, float]
    method: str
    objective: ActionabilityObjective = "five_class_reason"
    taxonomy_version: str = ACTIONABILITY_TAXONOMY_VERSION

    def __post_init__(self) -> None:
        if self.decision not in {"review", "abstained"}:
            raise ValueError("decision must be review or abstained")
        labels = (
            ACTIONABILITY_LABELS
            if self.objective == "five_class_reason"
            else BINARY_REVIEW_LABELS
        )
        if self.objective not in {"five_class_reason", BINARY_REVIEW_OBJECTIVE}:
            raise ValueError("unsupported actionability objective")
        if self.predicted_label not in labels:
            raise ValueError("predicted_label is outside the selected objective")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0.0 <= self.confidence <= 1.0
            or not math.isfinite(self.confidence)
        ):
            raise ValueError("confidence must be finite and in [0, 1]")
        _validate_probabilities(self.probabilities, labels=labels)
        expected_confidence = self.probabilities[self.predicted_label]
        if not math.isclose(self.confidence, expected_confidence, abs_tol=1e-9):
            raise ValueError("confidence must equal the predicted label probability")
        if self.decision == "review" and self.predicted_label == "actionable":
            raise ValueError("actionable predictions do not request extra review")
        if not isinstance(self.method, str) or not self.method.strip():
            raise ValueError("method must be non-empty")
        if self.taxonomy_version != ACTIONABILITY_TAXONOMY_VERSION:
            raise ValueError("taxonomy_version must match the actionability taxonomy")


def _validate_probabilities(
    probabilities: Mapping[str, float],
    *,
    labels: Sequence[str] = ACTIONABILITY_LABELS,
) -> None:
    if set(probabilities) != set(labels):
        raise ValueError("probabilities must cover the complete selected objective")
    values = tuple(probabilities[label] for label in labels)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
        or value > 1.0
        for value in values
    ):
        raise ValueError("probabilities must be finite and in [0, 1]")
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("probabilities must sum to one")


def _validate_scorer_metadata(method: object, review_threshold: object) -> None:
    if not isinstance(method, str) or not method.strip():
        raise ValueError("method must be non-empty")
    if (
        isinstance(review_threshold, bool)
        or not isinstance(review_threshold, (int, float))
        or not math.isfinite(review_threshold)
        or not 0.0 <= review_threshold <= 1.0
    ):
        raise ValueError("review_threshold must be in [0, 1]")


class LocalActionabilityScorer:
    """Validate and expose a fitted local classifier as an advisory scorer.

    ``review_threshold`` is selected on validation data, never on the held-out
    test split.  Below it the scorer abstains.  Above it, a non-actionable
    label asks an officer to review the reason; it still cannot reject or
    reroute the submission by itself.
    """

    def __init__(
        self,
        classifier: ProbabilityClassifier,
        *,
        method: str,
        review_threshold: float,
    ) -> None:
        _validate_scorer_metadata(method, review_threshold)
        classes = tuple(str(label) for label in classifier.classes_)
        if len(classes) != len(set(classes)) or set(classes) != set(ACTIONABILITY_LABELS):
            raise ValueError("classifier classes must exactly match the taxonomy")
        self._classifier = classifier
        self._classes = classes
        self.method = method
        self.review_threshold = review_threshold

    def score(self, redacted_text: str) -> ActionabilityAssessment:
        """Score one redacted string; callers remain responsible for redaction."""

        if not isinstance(redacted_text, str):
            raise TypeError("redacted_text must be a string")
        matrix = self._classifier.predict_proba([redacted_text])
        try:
            row = matrix[0]  # type: ignore[index]
            values = tuple(float(value) for value in row)
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("classifier returned an invalid probability row") from exc
        if len(values) != len(self._classes):
            raise ValueError("classifier probability width does not match its classes")
        probabilities = dict(zip(self._classes, values, strict=True))
        _validate_probabilities(probabilities)

        predicted = min(
            ACTIONABILITY_LABELS,
            key=lambda label: (-probabilities[label], label),
        )
        confidence = probabilities[predicted]
        decision: ActionabilityDecision = (
            "review"
            if (
                predicted != "actionable"
                and self.review_threshold < 1.0
                and confidence >= self.review_threshold
            )
            else "abstained"
        )
        return ActionabilityAssessment(
            decision=decision,
            predicted_label=predicted,
            confidence=confidence,
            probabilities=probabilities,
            method=self.method,
        )


class LocalBinaryReviewScorer:
    """Serve the validated actionable-versus-review objective without reasons.

    The binary scorer asks only whether an officer should inspect the case more
    closely.  It deliberately emits ``review_required`` rather than fabricating
    one of the five reason labels when the governed gold does not support that
    distinction.
    """

    def __init__(
        self,
        classifier: ProbabilityClassifier,
        *,
        method: str,
        review_threshold: float,
    ) -> None:
        _validate_scorer_metadata(method, review_threshold)
        classes = tuple(str(label) for label in classifier.classes_)
        if len(classes) != len(set(classes)) or set(classes) != set(
            BINARY_REVIEW_LABELS
        ):
            raise ValueError("classifier classes must exactly match the binary objective")
        self._classifier = classifier
        self._classes = classes
        self.method = method
        self.review_threshold = review_threshold

    def score(self, redacted_text: str) -> ActionabilityAssessment:
        if not isinstance(redacted_text, str):
            raise TypeError("redacted_text must be a string")
        matrix = self._classifier.predict_proba([redacted_text])
        try:
            row = matrix[0]  # type: ignore[index]
            values = tuple(float(value) for value in row)
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("classifier returned an invalid probability row") from exc
        if len(values) != len(self._classes):
            raise ValueError("classifier probability width does not match its classes")
        probabilities = dict(zip(self._classes, values, strict=True))
        _validate_probabilities(probabilities, labels=BINARY_REVIEW_LABELS)
        decision: ActionabilityDecision = (
            "review"
            if (
                probabilities["review_required"] >= self.review_threshold
                and self.review_threshold < 1.0
            )
            else "abstained"
        )
        # This objective is thresholded, not argmax-classified.  In particular,
        # the governed threshold may be below 0.5.  Keep the emitted label and
        # confidence aligned with the decision so a valid review request cannot
        # be rejected by ActionabilityAssessment as an actionable prediction.
        predicted: BinaryReviewLabel = (
            "review_required" if decision == "review" else "actionable"
        )
        confidence = probabilities[predicted]
        return ActionabilityAssessment(
            decision=decision,
            predicted_label=predicted,
            confidence=confidence,
            probabilities=probabilities,
            method=self.method,
            objective=BINARY_REVIEW_OBJECTIVE,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_actionability_artifact(path: Path) -> tuple[dict, Path]:
    """Validate local artifact structure and bytes without deserializing them."""
    artifact_dir = Path(path)
    manifest_path = artifact_dir / "manifest.json"
    if artifact_dir.is_symlink() or manifest_path.is_symlink():
        raise ValueError("actionability artifact paths must not be symlinks")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("actionability artifact manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise ValueError("actionability artifact manifest must be an object")
    artifact_format = manifest.get("artifact_format")
    expected_keys = {
        "artifact_format",
        "taxonomy_version",
        "labels",
        "method",
        "review_threshold",
        "model_file",
        "model_sha256",
    }
    if artifact_format == BINARY_REVIEW_ARTIFACT_FORMAT:
        expected_keys.add("objective")
    if set(manifest) != expected_keys:
        raise ValueError("actionability artifact manifest has an unexpected shape")
    if artifact_format not in {
        ACTIONABILITY_ARTIFACT_FORMAT,
        BINARY_REVIEW_ARTIFACT_FORMAT,
    }:
        raise ValueError("unsupported actionability artifact format")
    if manifest["taxonomy_version"] != ACTIONABILITY_TAXONOMY_VERSION:
        raise ValueError("actionability taxonomy version mismatch")
    if artifact_format == ACTIONABILITY_ARTIFACT_FORMAT:
        if manifest["labels"] != list(ACTIONABILITY_LABELS):
            raise ValueError("actionability artifact labels do not match the taxonomy")
    else:
        if manifest["objective"] != BINARY_REVIEW_OBJECTIVE:
            raise ValueError("binary actionability objective mismatch")
        if manifest["labels"] != list(BINARY_REVIEW_LABELS):
            raise ValueError("binary actionability labels do not match the objective")
    _validate_scorer_metadata(manifest["method"], manifest["review_threshold"])
    model_file = manifest["model_file"]
    if (
        not isinstance(model_file, str)
        or not model_file
        or Path(model_file).name != model_file
    ):
        raise ValueError("model_file must be one filename inside the artifact")
    model_path = artifact_dir / model_file
    if model_path.is_symlink():
        raise ValueError("actionability model file must not be a symlink")
    if not model_path.is_file() or model_path.stat().st_size == 0:
        raise ValueError("actionability model file is absent or empty")
    if _sha256(model_path) != manifest["model_sha256"]:
        raise ValueError("actionability model checksum mismatch")
    return manifest, model_path


def load_actionability_scorer(
    path: Path,
) -> LocalActionabilityScorer | LocalBinaryReviewScorer:
    """Load a checksummed local model directory, failing closed on drift."""

    manifest, model_path = validate_actionability_artifact(path)

    try:
        import joblib

        classifier = joblib.load(model_path)
    except Exception as exc:
        raise ValueError("actionability model could not be loaded") from exc
    artifact_format = manifest["artifact_format"]
    scorer_type = (
        LocalActionabilityScorer
        if artifact_format == ACTIONABILITY_ARTIFACT_FORMAT
        else LocalBinaryReviewScorer
    )
    return scorer_type(
        classifier,
        method=manifest["method"],
        review_threshold=manifest["review_threshold"],
    )
