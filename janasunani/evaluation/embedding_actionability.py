"""Local frozen-encoder probes for the actionability review decision.

The encoder is never fine-tuned.  Its normalized mean-pooled embeddings feed a
small logistic-regression probe trained on the governed training split.  Probe
regularization and the officer-review threshold are selected on validation;
the test split is evaluated exactly once and is never used to rank candidates.

Only local model directories are accepted.  ``local_files_only=True`` and
``trust_remote_code=False`` make a missing artifact a clear failure instead of
an implicit provider call.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from janasunani.evaluation.actionability import (
    ActionabilityRecord,
    _binary_review_metrics,
    _select_binary_review_threshold,
    validate_records,
)

EncoderRole = Literal["multilingual_candidate", "english_diagnostic"]

MODEL_FAMILY = "frozen-transformer-mean-pool-logreg"
MODEL_VERSION = "actionability-frozen-encoder-v1"


@dataclass(frozen=True)
class LocalEncoderSpec:
    """Pinned local encoder identity and its intended interpretation."""

    name: str
    model_path: Path
    role: EncoderRole
    max_length: int = 256
    batch_size: int = 16


class TextEncoder(Protocol):
    """Small seam used by the benchmark and lightweight unit tests."""

    @property
    def provenance(self) -> dict[str, object]: ...

    def encode(self, texts: Sequence[str]) -> Any: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_fingerprint(path: Path) -> tuple[str, int]:
    """Hash all model files, including the content behind cache symlinks."""

    resolved = resolve_local_model_dir(path)
    files = sorted(candidate for candidate in resolved.rglob("*") if candidate.is_file())
    if not files:
        raise FileNotFoundError(f"local model directory contains no files: {resolved}")
    digest = hashlib.sha256()
    for candidate in files:
        relative = candidate.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(candidate.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_sha256_file(candidate).encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}", len(files)


def resolve_local_model_dir(path: Path) -> Path:
    """Resolve and minimally validate a Transformers model directory."""

    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"local encoder is absent: {resolved}; benchmark will not download it"
        )
    if not resolved.is_dir():
        raise ValueError(f"local encoder path is not a directory: {resolved}")
    if not (resolved / "config.json").is_file():
        raise FileNotFoundError(f"local encoder has no config.json: {resolved}")
    return resolved


def resolve_cached_huggingface_snapshot(
    repo_id: str, *, cache_dir: Path | None = None, revision: str = "main"
) -> Path:
    """Resolve an already cached Hugging Face snapshot without network access."""

    if not repo_id or "/" not in repo_id:
        raise ValueError("repo_id must have the form owner/model")
    cache_root = (
        cache_dir.expanduser().resolve()
        if cache_dir is not None
        else Path.home() / ".cache" / "huggingface" / "hub"
    )
    repository = cache_root / f"models--{repo_id.replace('/', '--')}"
    revision_ref = repository / "refs" / revision
    if revision_ref.is_file():
        snapshot_id = revision_ref.read_text(encoding="utf-8").strip()
        snapshot = repository / "snapshots" / snapshot_id
        return resolve_local_model_dir(snapshot)
    direct_snapshot = repository / "snapshots" / revision
    if direct_snapshot.is_dir():
        return resolve_local_model_dir(direct_snapshot)
    raise FileNotFoundError(
        f"Hugging Face model {repo_id!r} revision {revision!r} is not cached "
        f"under {cache_root}; benchmark will not download it"
    )


class TransformersMeanPoolEncoder:
    """Frozen local Transformers encoder with masked mean pooling."""

    def __init__(self, spec: LocalEncoderSpec, *, device: str = "cpu") -> None:
        if not spec.name.strip():
            raise ValueError("encoder name must be non-empty")
        if spec.role not in {"multilingual_candidate", "english_diagnostic"}:
            raise ValueError(f"invalid encoder role {spec.role!r}")
        if spec.max_length < 8:
            raise ValueError("max_length must be at least 8")
        if spec.batch_size < 1:
            raise ValueError("batch_size must be positive")
        model_path = resolve_local_model_dir(spec.model_path)
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "frozen encoder benchmark needs the pipeline-core extra"
            ) from exc

        self._torch = torch
        self._spec = spec
        self._model_path = model_path
        self._device = torch.device(device)
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            local_files_only=True,
            trust_remote_code=False,
        )
        self._model = AutoModel.from_pretrained(
            model_path,
            local_files_only=True,
            trust_remote_code=False,
        ).to(self._device)
        self._model.eval()
        for parameter in self._model.parameters():
            parameter.requires_grad_(False)

        fingerprint, file_count = directory_fingerprint(model_path)
        revision = (
            model_path.name if model_path.parent.name == "snapshots" else None
        )
        config = self._model.config
        self._provenance: dict[str, object] = {
            "name": spec.name,
            "role": spec.role,
            # Preserve the configured reference rather than the resolved
            # checkout path so aggregate benchmark evidence is portable.
            "local_path": spec.model_path.as_posix(),
            "revision": revision,
            "artifact_fingerprint": fingerprint,
            "artifact_file_count": file_count,
            "transformers_model_type": getattr(config, "model_type", None),
            "architectures": list(getattr(config, "architectures", None) or []),
            "pooling": "attention_masked_mean_then_l2_normalize",
            "max_length": spec.max_length,
            "batch_size": spec.batch_size,
            "device": str(self._device),
            "frozen": True,
            "local_files_only": True,
            "trust_remote_code": False,
        }

    @property
    def provenance(self) -> dict[str, object]:
        return dict(self._provenance)

    def encode(self, texts: Sequence[str]) -> Any:
        if not texts:
            raise ValueError("cannot encode an empty text collection")
        torch = self._torch
        batches = []
        for start in range(0, len(texts), self._spec.batch_size):
            batch = list(texts[start : start + self._spec.batch_size])
            tokens = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self._spec.max_length,
                return_tensors="pt",
            )
            tokens = {key: value.to(self._device) for key, value in tokens.items()}
            with torch.inference_mode():
                hidden = self._model(**tokens).last_hidden_state
            mask = tokens["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            batches.append(pooled.cpu())
        return torch.cat(batches, dim=0).numpy()


@dataclass
class FrozenEncoderBenchmark:
    classifier: Any
    review_threshold: float
    report: dict[str, object]


def binary_discrimination_metrics(
    probabilities: Sequence[float], records: Sequence[ActionabilityRecord]
) -> dict[str, float | None]:
    """Return threshold-free binary diagnostics for an aligned split."""

    if len(probabilities) != len(records) or not records:
        raise ValueError("discrimination metrics require aligned non-empty inputs")
    if any(
        not math.isfinite(probability) or not 0 <= probability <= 1
        for probability in probabilities
    ):
        raise ValueError("probabilities must be finite and in [0, 1]")
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    labels = [record.label != "actionable" for record in records]
    if len(set(labels)) < 2:
        roc_auc = None
    else:
        roc_auc = float(roc_auc_score(labels, probabilities))
    return {
        "roc_auc": roc_auc,
        "average_precision": float(average_precision_score(labels, probabilities)),
        "brier_score": float(brier_score_loss(labels, probabilities)),
    }


def _probabilities(classifier: Any, matrix: Any) -> list[float]:
    classes = tuple(str(label) for label in classifier.classes_)
    try:
        review_index = classes.index("review")
    except ValueError as exc:
        raise ValueError("probe did not learn the review class") from exc
    return [float(row[review_index]) for row in classifier.predict_proba(matrix)]


def benchmark_frozen_encoder(
    records: Sequence[ActionabilityRecord],
    encoder: TextEncoder,
    *,
    c_values: Sequence[float] = (0.1, 0.5, 1.0, 2.0, 10.0),
    min_review_precision: float = 0.9,
    max_actionable_review_rate: float = 0.05,
) -> FrozenEncoderBenchmark:
    """Tune a frozen-embedding binary probe on validation and score test."""

    validate_records(records)
    if not c_values or any(not math.isfinite(c) or c <= 0 for c in c_values):
        raise ValueError("c_values must contain positive finite values")
    if not 0 <= min_review_precision <= 1:
        raise ValueError("min_review_precision must be in [0, 1]")
    if not 0 <= max_actionable_review_rate <= 1:
        raise ValueError("max_actionable_review_rate must be in [0, 1]")
    splits = {
        split: [record for record in records if record.split == split]
        for split in ("train", "validation", "test")
    }
    for split, rows in splits.items():
        if {row.label != "actionable" for row in rows} != {False, True}:
            raise ValueError(f"{split} split must contain actionable and review examples")

    matrices = {
        split: encoder.encode([record.redacted_text for record in rows])
        for split, rows in splits.items()
    }
    for split, matrix in matrices.items():
        shape = getattr(matrix, "shape", None)
        if shape is None or len(shape) != 2 or shape[0] != len(splits[split]):
            raise ValueError(
                f"encoder returned an invalid {split} matrix shape: {shape!r}"
            )
        if shape[1] < 1:
            raise ValueError("encoder returned zero embedding features")
        try:
            finite = bool(matrix.dtype.kind in "biuf" and math.isfinite(float(matrix.sum())))
        except (AttributeError, TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"encoder returned non-finite {split} embeddings")
    from sklearn.linear_model import LogisticRegression

    candidates: list[tuple[float, Any, float, dict[str, object], list[float]]] = []
    train_labels = [
        "review" if record.label != "actionable" else "actionable"
        for record in splits["train"]
    ]
    for c in c_values:
        classifier = LogisticRegression(
            C=c,
            class_weight="balanced",
            max_iter=2_000,
            random_state=1729,
        )
        classifier.fit(matrices["train"], train_labels)
        validation_probabilities = _probabilities(classifier, matrices["validation"])
        threshold, validation_metrics = _select_binary_review_threshold(
            validation_probabilities,
            splits["validation"],
            min_precision=min_review_precision,
            max_actionable_review_rate=max_actionable_review_rate,
        )
        candidates.append(
            (c, classifier, threshold, validation_metrics, validation_probabilities)
        )
    c, classifier, threshold, validation_metrics, validation_probabilities = max(
        candidates,
        key=lambda row: (
            float(row[3]["review_recall"]),
            float(row[3]["review_precision"]),
            float(row[3]["accuracy"]),
            -row[0],
        ),
    )
    test_probabilities = _probabilities(classifier, matrices["test"])
    test_metrics = _binary_review_metrics(
        test_probabilities, splits["test"], threshold=threshold
    )
    by_language: dict[str, object] = {}
    for language in sorted({row.language for row in splits["test"]}):
        indices = [
            index
            for index, row in enumerate(splits["test"])
            if row.language == language
        ]
        by_language[language] = _binary_review_metrics(
            [test_probabilities[index] for index in indices],
            [splits["test"][index] for index in indices],
            threshold=threshold,
        )

    provenance = encoder.provenance
    role = provenance.get("role")
    limitations = [
        "frozen encoder with a train-only linear probe",
        "frontier-adjudicated development gold is not officer-confirmed truth",
        "single-snapshot hash splits are not chronological release evidence",
        "test split is report-only and did not select model or threshold",
        "Wilson intervals are item-level and do not establish population representativeness",
        "binary review does not replace the complete five-class production contract",
    ]
    if role == "english_diagnostic":
        limitations.append(
            "English-oriented encoder is diagnostic only for Odia and multilingual traffic"
        )
    report: dict[str, object] = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "objective": "actionable_vs_officer_review",
        "encoder": provenance,
        "probe": {
            "classifier": "sklearn.linear_model.LogisticRegression",
            "solver": "lbfgs",
            "class_weight": "balanced",
            "max_iter": 2_000,
            "random_state": 1729,
            "candidate_c_values": list(c_values),
            "training_split": "train",
        },
        "selected_c": c,
        "review_threshold": threshold,
        "split_counts": {split: len(rows) for split, rows in splits.items()},
        "gold_label_distribution": dict(
            sorted(Counter(record.label for record in records).items())
        ),
        "validation": validation_metrics,
        "validation_discrimination": binary_discrimination_metrics(
            validation_probabilities, splits["validation"]
        ),
        "test": test_metrics,
        "test_discrimination": binary_discrimination_metrics(
            test_probabilities, splits["test"]
        ),
        "test_by_language": by_language,
        "candidate_validation": [
            {
                "c": candidate_c,
                "threshold": candidate_threshold,
                "review_precision": metrics["review_precision"],
                "review_recall": metrics["review_recall"],
                "actionable_review_rate": metrics["actionable_review_rate"],
                **binary_discrimination_metrics(probabilities, splits["validation"]),
            }
            for candidate_c, _, candidate_threshold, metrics, probabilities in candidates
        ],
        "release_eligible": False,
        "limitations": limitations,
    }
    return FrozenEncoderBenchmark(
        classifier=classifier,
        review_threshold=threshold,
        report=report,
    )
