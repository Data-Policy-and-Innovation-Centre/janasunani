"""Thin wrapper around janasunani-evaluate-sarvam (digitise-only legacy).

This script is retained for backward compatibility. New code should invoke
``janasunani-evaluate-sarvam`` directly (Unit E) with ``--arm digitise``,
``--arm extract`` or ``--arm both``. The wrapper delegates to
``janasunani.evaluation.sarvam_evaluate`` so the same-pixels-to-both-engines
invariant stays in one place.
"""

from __future__ import annotations

import sys

from loguru import logger


def main(argv: list[str] | None = None) -> int:
    logger.warning("scripts/analysis/sarvam_sample.py is deprecated — use janasunani-evaluate-sarvam --arm digitise")
    from janasunani.evaluation.sarvam_evaluate import main as evaluate_main

    # Legacy default: digitise arm.  If caller already passes --arm, honour it.
    if argv is not None:
        has_arm = any(a == "--arm" or a.startswith("--arm=") for a in argv)
        if not has_arm:
            argv = ["--arm", "digitise", *argv]
    else:
        # argv=None -> evaluate_main reads sys.argv; inject default arm if missing
        has_arm = any(a == "--arm" or a.startswith("--arm=") for a in sys.argv[1:])
        if not has_arm:
            sys.argv = [sys.argv[0], "--arm", "digitise", *sys.argv[1:]]
    return evaluate_main(argv)


if __name__ == "__main__":
    sys.exit(main())
