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
from loguru import logger

# Max token length the model was trained with (MAX_LEN in the training script).
MAX_LEN = 256


class GrievanceCategorizer:
    """Wraps the fine-tuned MuRIL classifier + its label encoder."""

    def __init__(self, model_dir: str) -> None:
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

        # Label encoder: local dir (the DVC mirror) first; HF download only
        # when model_dir is a repo id.
        local = Path(model_dir) / "label_encoder_ROS_wDOCS_english.pkl"
        if local.exists():
            encoder_path = local
        else:
            encoder_path = hf_hub_download(
                repo_id=model_dir,
                filename="label_encoder_ROS_wDOCS_english.pkl",
            )

        with open(encoder_path, "rb") as f:
            self._label_encoder = pickle.load(f)

        # Load tokenizer + model directly from HF
        self._tokenizer = AutoTokenizer.from_pretrained(model_dir)

        self._model = (
            AutoModelForSequenceClassification
            .from_pretrained(model_dir)
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
