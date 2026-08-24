"""Lightweight format-classifier artifact resolution.

This module deliberately has no image or model imports so release-path checks
can run in the light CI environment without OpenCV and the pipeline extras.
"""

from __future__ import annotations

from pathlib import Path

from janasunani.tracking.artifacts import resolve_artifact

from ...config import PipelineConfig


def resolve_model_path(config: PipelineConfig) -> Path:
    artifact = resolve_artifact("format_classifier", models_dir=config.models_dir)
    if artifact is None:
        raise FileNotFoundError(
            "no format-classifier artifact resolved from operator override, "
            "active release, or DVC mirror"
        )
    if artifact.is_file():
        if artifact.suffix.lower() != ".pkl":
            raise ValueError("format-classifier artifact file must have .pkl suffix")
        return artifact
    candidates = sorted(artifact.glob("*.pkl"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"format-classifier directory {artifact} contains {len(candidates)} .pkl "
            "files; pin one exact file with JANASUNANI_FORMAT_CLASSIFIER_ARTIFACT "
            "or the active release manifest"
        )
    return candidates[0]
