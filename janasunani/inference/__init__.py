"""Single-item inference wrappers for the live warm processor.

Unlike the batch `pipeline/` package, modules here orchestrate a single
document/grievance at a time and take their heavy dependencies (OCR
backends, model calls) as injected callables, so they stay importable and
unit-testable without tesseract/poppler/torch installed.
"""
