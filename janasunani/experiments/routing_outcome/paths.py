"""Where the experiment reads and writes.

The Aug 11 run hard-coded `/tmp/*.parquet` in every script, which made the
stage boundaries invisible and the run un-reproducible on any other machine.
Everything derived now lands under one overridable directory.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Root for every derived artifact (splits, fitted models, OPE frames, summaries).
OUT_DIR = Path(
    os.environ.get("ROUTING_OUTCOME_OUT", "outputs/experiments/routing_outcome")
)

#: DVC-tracked master tables. Structure only, never outcomes.
MAPPING_DIR = Path(os.environ.get("JANASUNANI_MAPPING_DIR", "data/raw/janasunani-mappings"))

#: Read-only analytical lake produced by `janasunani/olap/materialize.py`.
LAKE_DIR = Path(os.environ.get("JANASUNANI_LAKE_DIR", "data/interim"))

COMPLAINTS_PARQUET = LAKE_DIR / "complaints.parquet"
ACTION_HISTORY_PARQUET = LAKE_DIR / "action_history.parquet"


def out(name: str) -> Path:
    """Absolute path for a derived artifact, creating `OUT_DIR` on demand."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR / name
