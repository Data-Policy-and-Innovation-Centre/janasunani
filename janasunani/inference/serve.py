"""Opt-in live API entry point.

Unlike :mod:`janasunani.serving.api`, importing this module does not construct
an app.  Startup is intentionally strict and happens only through ``main`` or
``create_live_app``.
"""

from __future__ import annotations

import os
import sys

# Importing torch/xgboost/spaCy in one arm64 process can otherwise collide in
# OpenMP initialization. This must run before any inference imports.
if sys.platform == "darwin":
    os.environ.setdefault("OMP_NUM_THREADS", "1")

from janasunani.inference.service import build_processor  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.history import LakeHistory  # noqa: E402
from janasunani.serving.store import (  # noqa: E402
    DatabaseResultStore,
    InMemoryResultStore,
)


def create_live_app():
    """Build the real processor, lake history, and explicitly chosen store."""
    processor = build_processor()
    oltp_url = os.environ.get("OLTP_DB_URL")
    result_store = (
        DatabaseResultStore(oltp_url) if oltp_url else InMemoryResultStore()
    )
    return create_app(
        processor=processor,
        history=LakeHistory(),
        result_store=result_store,
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_live_app(),
        host=os.environ.get("JANASUNANI_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("JANASUNANI_API_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
