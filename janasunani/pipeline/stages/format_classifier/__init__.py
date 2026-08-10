"""Format classifier stage with lazy heavy imports.

Public surface:
  run_format_classifier — the entry point pipeline.py calls.
  FormatClassifier      — the model wrapper, if you want to use it standalone.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import FormatClassifier

__all__ = ["FormatClassifier", "run_format_classifier"]


def run_format_classifier(config: Any) -> None:
    """Run the stage without importing its optional dependencies at discovery time."""
    from .stage import run_format_classifier as _run_format_classifier

    _run_format_classifier(config)


def __getattr__(name: str) -> Any:
    if name == "FormatClassifier":
        from .model import FormatClassifier

        return FormatClassifier
    raise AttributeError(name)
