"""Grievance categorizer stage (final pipeline step).

Public surface:
  run_categorizer       — the entry point pipeline.py calls.
  ingest_grievances     — load complaints JSON into the documents table
                          (usable standalone, independent of the summarizer).
"""
from .ingest_grievances import ingest_grievances
from .stage import run_categorizer

__all__ = ["ingest_grievances", "run_categorizer"]
