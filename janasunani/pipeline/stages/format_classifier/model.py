"""Format classifier model wrapper.

Loads the trained scikit-learn pipeline from a pickle file and exposes
a single predict() method that takes a BGR OpenCV image and returns
normalized predictions for the schema columns this model populates.

The current trained model only predicts `handwritten` and `language`.
The schema also defines `scanned`, `printed`, `image_quality`, and
`color_scale` columns on the `pages` table, but no current stage
populates them — they will be written as NULL. Whether they get filled
by a future version of this classifier, by a separate stage, or stay
NULL permanently is an open question for the project owners.
"""
from __future__ import annotations

import pickle  # noqa: S403 — model file is trusted, see warning below
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .features import extract_features
from .normalize import normalize_value

# Columns this version of the trained model knows how to predict.
# Note: in the pickle the language key is "Language" (capital L) for
# historical reasons; we translate to lowercase "language" on output
# to match the pipeline's `pages` table.
_MODEL_PREDICTS = {"handwritten", "Language"}

# Schema columns this stage is responsible for. Anything in this list
# that the model doesn't predict will be left as None (-> SQL NULL).
SCHEMA_COLUMNS = (
    "scanned",
    "handwritten",
    "printed",
    "image_quality",
    "color_scale",
    "language",
)


class FormatClassifier:
    """Thin wrapper around the pickled scikit-learn model."""

    def __init__(self, model_path: Path, model_name: str = "format_classifier_v3") -> None:
        self.model_path = Path(model_path)
        self.model_name = model_name
        if not self.model_path.exists():
            raise FileNotFoundError(f"format classifier model not found: {self.model_path}")
        # SECURITY NOTE: pickle.load executes arbitrary code in the source
        # file. Only load models from sources you trust (i.e. ones you or
        # your team trained). Don't load model files received from outside
        # parties without inspecting them first.
        with self.model_path.open("rb") as f:
            self._model: dict[str, Any] = pickle.load(f)  # noqa: S301

    def predict(self, img_cv: np.ndarray) -> dict[str, str | None] | None:
        """Classify a single page image.

        Args:
            img_cv: BGR image as returned by cv2.imread / cv2.cvtColor.

        Returns:
            Dict mapping schema column names to canonical string values
            (or None). Returns None if feature extraction or classification
            fails — the caller should treat this as "page unreadable."
        """
        features = extract_features(img_cv)
        if features is None:
            return None

        try:
            X = pd.DataFrame([features])

            # Map predominant_lang to encoded form. If the OCR returned a
            # language the encoder never saw, fall back to the first class.
            lang_val = X["predominant_lang"].iloc[0]
            if lang_val not in self._model["lang_encoder"].classes_:
                lang_val = self._model["lang_encoder"].classes_[0]
                X["predominant_lang"] = lang_val
            X["predominant_lang_encoded"] = self._model["lang_encoder"].transform(
                X["predominant_lang"]
            )

            X_numeric = X.drop("predominant_lang", axis=1)
            # Pad missing features with zeros so column order matches training
            for feat in self._model["feature_columns"]:
                if feat not in X_numeric.columns:
                    X_numeric[feat] = 0.0
            X_numeric = X_numeric[self._model["feature_columns"]]
            X_scaled = self._model["scaler"].transform(X_numeric)

            raw_predictions: dict[str, str] = {}
            for col, clf in self._model["classifiers"].items():
                if col not in _MODEL_PREDICTS:
                    continue
                encoder = self._model["label_encoders"][col]
                raw_predictions[col] = encoder.inverse_transform(clf.predict(X_scaled))[0]
        except Exception:
            return None

        # Translate pickle's column names to schema names and normalize.
        out: dict[str, str | None] = {col: None for col in SCHEMA_COLUMNS}
        if "handwritten" in raw_predictions:
            out["handwritten"] = normalize_value(raw_predictions["handwritten"], "handwritten")
        if "Language" in raw_predictions:
            out["language"] = normalize_value(raw_predictions["Language"], "language")
        return out

