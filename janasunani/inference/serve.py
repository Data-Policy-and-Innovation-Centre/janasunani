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

from janasunani.config import DEFAULT_OLTP_DB_URL, Settings  # noqa: E402
from janasunani.inference.service import build_processor  # noqa: E402
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.history import LakeHistory  # noqa: E402
from janasunani.serving.store import (  # noqa: E402
    DatabaseResultStore,
    InMemoryResultStore,
)


def _resolve_explicit_oltp_url() -> str | None:
    """Resolve `OLTP_DB_URL` through the same `Settings` layer the rest of the
    app uses, so a value set in the project `.env` (not just a shell-exported
    env var) is honored -- a raw `os.environ` read misses it and store
    selection silently falls back to `InMemoryResultStore`.

    Re-instantiates `Settings()` (rather than importing the module-level
    singleton) so this always reflects the current process environment and
    `.env` file at call time.

    Returns None unless OLTP is *explicitly* configured: a value equal to
    `DEFAULT_OLTP_DB_URL` (the built-in local-SQLite fallback `Settings`
    itself falls back to) is treated as "not configured", matching Phase 8C's
    contract of using `DatabaseResultStore` only for an explicit OLTP URL.
    """
    resolved = Settings().OLTP_DB_URL
    return resolved if resolved != DEFAULT_OLTP_DB_URL else None


def create_live_app():
    """Build the real processor, lake history, and explicitly chosen store."""
    processor = build_processor()
    oltp_url = _resolve_explicit_oltp_url()
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
