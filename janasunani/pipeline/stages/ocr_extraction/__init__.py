"""OCR extraction stage.

Public surface:
  run_ocr_extraction — the entry point pipeline.py calls.
"""
from .stage import run_ocr_extraction

__all__ = ["run_ocr_extraction"]
