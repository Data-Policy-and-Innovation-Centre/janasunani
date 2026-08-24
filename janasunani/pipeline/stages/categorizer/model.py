"""MuRIL grievance categorizer model wrapper.

Loads the fine-tuned MuRIL sequence-classification model and its label
encoder, and predicts a category string for a piece of text.

The model was trained with HuggingFace's Trainer on `grievance_and_docs`
text -> `category`. The saved model directory contains the standard HF
files (config.json, model.safetensors, tokenizer files) plus a pickled
sklearn LabelEncoder that maps the model's output integer back to the
category string.

Like DeepSeek, this needs transformers + torch and runs best on GPU.
Imports are done inside methods so merely importing this module doesn't
require those heavy packages.
"""
from __future__ import annotations

import pickle  # noqa: S403 — label encoder is a trusted local artifact
from pathlib import Path

from janasunani.tracking.artifacts import (
    ALLOW_REMOTE_MODELS_ENV_VAR,
    remote_models_allowed,
)
from loguru import logger

# Max token length the model was trained with (MAX_LEN in the training script).
MAX_LEN = 256
LABEL_ENCODER_FILENAME = "label_encoder_ROS_wDOCS_english.pkl"


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def require_complete_local_artifact(path: Path) -> Path:
    """Validate all bytes required by the local Hugging Face loader."""

    artifact = Path(path)
    if not artifact.is_dir():
        raise RuntimeError(f"local categorizer artifact is not a directory: {artifact}")
    requirements = {
        "config": (artifact / "config.json",),
        "weights": (
            artifact / "model.safetensors",
            artifact / "pytorch_model.bin",
        ),
        "tokenizer": (
            artifact / "tokenizer.json",
            artifact / "vocab.txt",
        ),
        "label encoder": (artifact / LABEL_ENCODER_FILENAME,),
    }
    missing = [
        name
        for name, candidates in requirements.items()
        if not any(_nonempty_file(candidate) for candidate in candidates)
    ]
    if missing:
        raise RuntimeError(
            "incomplete local categorizer artifact; missing non-empty "
            + ", ".join(missing)
        )
    return artifact


def resolve_model_source(model_dir: str | Path) -> tuple[str, bool, Path | None]:
    """Resolve a complete local directory or an explicitly allowed repo ID."""

    source = str(model_dir)
    candidate = Path(source)
    if candidate.exists():
        artifact = require_complete_local_artifact(candidate)
        return str(artifact), True, artifact / LABEL_ENCODER_FILENAME
    if not remote_models_allowed():
        raise RuntimeError(
            "remote categorizer model IDs require explicit development opt-in via "
            f"{ALLOW_REMOTE_MODELS_ENV_VAR}=1"
        )
    return source, False, None


class GrievanceCategorizer:
    """Wraps the fine-tuned MuRIL classifier + its label encoder."""

    def __init__(self, model_dir: str | Path) -> None:
        import torch
        from huggingface_hub import hf_hub_download
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        self._torch = torch

        if not torch.cuda.is_available():
            logger.warning(
                "no CUDA GPU detected; categorizer will run on CPU (much slower)."
            )
            self._device = "cpu"
        else:
            self._device = "cuda"

        source, local_only, encoder_path = resolve_model_source(model_dir)
        if encoder_path is None:
            encoder_path = hf_hub_download(
                repo_id=source,
                filename=LABEL_ENCODER_FILENAME,
                local_files_only=False,
            )

        with open(encoder_path, "rb") as f:
            self._label_encoder = pickle.load(f)

        self._tokenizer = AutoTokenizer.from_pretrained(
            source,
            local_files_only=local_only,
            trust_remote_code=False,
        )

        self._model = (
            AutoModelForSequenceClassification
            .from_pretrained(
                source,
                local_files_only=local_only,
                trust_remote_code=False,
            )
            .to(self._device)
            .eval()
        )

    def predict(self, text: str) -> str:
        """Predict the grievance category for a piece of text.

        Returns the category string (decoded via the label encoder).
        """
        torch = self._torch
        enc = self._tokenizer(
            text,
            truncation=True,
            padding="max_length",
            max_length=MAX_LEN,
            return_tensors="pt",
        )
        enc = {k: v.to(self._device) for k, v in enc.items()}

        with torch.no_grad():
            logits = self._model(**enc).logits
        pred_idx = int(torch.argmax(logits, dim=1).item())
        # inverse_transform expects an array-like, returns an array.
        return str(self._label_encoder.inverse_transform([pred_idx])[0])
