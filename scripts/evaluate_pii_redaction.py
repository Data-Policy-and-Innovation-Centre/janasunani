#!/usr/bin/env python
"""Evaluate Presidio PII detection against an explicit gold JSONL file."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from janasunani.pipeline.pii_eval import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
