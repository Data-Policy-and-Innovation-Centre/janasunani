"""Format classifier stage.

Public surface:
  run_format_classifier — the entry point pipeline.py calls.
  FormatClassifier      — the model wrapper, if you want to use it standalone.
"""
from .model import FormatClassifier
from .stage import run_format_classifier

__all__ = ["FormatClassifier", "run_format_classifier"]
