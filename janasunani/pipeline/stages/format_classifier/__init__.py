"""Format classifier stage with lazy heavy imports.

Public surface:
  run_format_classifier — the entry point pipeline.py calls.
  FormatClassifier      — the model wrapper, if you want to use it standalone.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import FormatClassifier
    from .stage import run_format_classifier

__all__ = ["FormatClassifier", "run_format_classifier"]


def __getattr__(name: str) -> Any:
    if name == "FormatClassifier":
        from .model import FormatClassifier

        return FormatClassifier
    if name == "run_format_classifier":
        from .stage import run_format_classifier

        return run_format_classifier
    raise AttributeError(name)
