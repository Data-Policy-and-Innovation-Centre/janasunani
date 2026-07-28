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
    # The summarizer downloads facebook/bart-large-cnn from the HF hub on first
    # run. The Rust `hf_xet` transfer backend can hang on a dispatch semaphore
    # on macOS *after* the bytes land, wedging startup indefinitely. Fall back
    # to plain HTTPS locally; the Linux CPU box (where Xet works) is untouched.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from janasunani.inference.service import (  # noqa: E402
    build_processor,
    resolve_explicit_oltp_url as _resolve_explicit_oltp_url,
)
from janasunani.serving.api import create_app  # noqa: E402
from janasunani.serving.history import LakeHistory  # noqa: E402
from janasunani.serving.store import (  # noqa: E402
    DatabaseResultStore,
    InMemoryResultStore,
)


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


def preflight_main() -> None:
    """Print a demo-readiness report and exit non-zero if anything is missing.

    A fast, weight-free pre-check to run before the multi-minute warm start:
    it surfaces a missing model artifact or OCR binary in milliseconds instead
    of minutes into `build_processor`.

    Two severities. Required checks are things `build_processor` cannot start
    without, and always fail the run. Advisory checks (`WARN`) are the ones
    that let the demo start and then quietly show less than expected: routing
    silently on `method:"fallback"`, `/history` empty because the lake was
    never materialized, submissions dropped because OLTP was never configured.

    `--strict` promotes advisory failures to fatal. That is the mode for a
    box bring-up, where "starts but shows nothing" is not success.
    """
    import argparse

    from janasunani.inference.service import preflight

    parser = argparse.ArgumentParser(
        description="Report demo readiness without loading model weights."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat advisory checks as failures. Use for a box bring-up: "
            "routing mappings, the history lake and the OLTP store all fail "
            "silently at runtime."
        ),
    )
    args = parser.parse_args()

    checks = preflight()
    fatal = False
    degraded = False
    for check in checks:
        if check.ok:
            mark = "OK  "
        elif check.required or args.strict:
            mark = "FAIL"
            fatal = True
        else:
            mark = "WARN"
            degraded = True
        print(f"[{mark}] {check.name}: {check.detail}")

    if degraded and not args.strict:
        print(
            "[INFO] WARN items do not stop startup; the demo will run degraded. "
            "Re-run with --strict to treat them as failures."
        )
    raise SystemExit(1 if fatal else 0)


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_live_app(),
        host=os.environ.get("JANASUNANI_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("JANASUNANI_API_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
