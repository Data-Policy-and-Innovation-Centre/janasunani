"""Check that committed provenance sidecars hold metadata and nothing else.

`data/external/*.provenance.json` is the one exception to the rule that nothing
under `data/` enters git: a sidecar records analyzer versions, checksums and
span counts so a gold artifact can be reviewed without pulling citizen text.
The exception is granted by filename, so something has to verify that the
contents actually match that description.

This is an allowlist, deliberately. Enumerating forbidden keys cannot work:
citizen text under a key named "content", "excerpt" or "raw" would pass a
denylist and be committed to a public repo. Anything not named here fails, so a
new field is a conscious edit rather than a silent addition.

The value rules are the real barrier. Prose does not survive a 200-character
scalar cap, and a counter's keys must be canonical entity labels, so a
label-to-count map lifted from an annotation tool whose labels are surface
forms is rejected rather than committed.

Nothing here prints the value it rejected. This runs in CI, whose logs are
public, so a rejected key is reported by position and never by content --
publishing the thing you are refusing to publish defeats the gate.

Stdlib only: it runs on a bare runner before any dependency is installed.

    python3 scripts/check_provenance_sidecars.py data/external/*.provenance.json

Exits 0 when every file passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

# Mirrors provenance() in scripts/rederive_pii_draft.py.
ALLOWED_TOP = {
    "kind",
    "note",
    "created_utc",
    "out",
    "source_gold",
    "source_gold_md5",
    "records",
    "spans",
    "spans_by_entity",
    "analyzer",
    "environment",
}

# Nested objects with a fixed key set.
ALLOWED_NESTED = {
    "analyzer": {"git_commit", "presidio_analyzer", "spacy", "en_core_web_sm"},
    "environment": {"python", "system", "machine"},
}

# Objects keyed by entity label rather than by a fixed key set. The keys are
# data, so they are constrained too: this is the field a surface form would
# arrive in. Must equal KNOWN_ENTITIES in scripts/verify_pii_gold.py; a test
# asserts that, because the two drifting apart is how a gap reopens.
COUNTER_OBJECTS = {"spans_by_entity"}
ENTITY_LABELS = {"NAME", "PHONE", "EMAIL", "AADHAAR", "PAN"}

MAX_BYTES = 16 * 1024
MAX_STRING = 200
# "note" is the one deliberately long field: a fixed caveat written by the
# script, never derived from a record.
MAX_NOTE = 1000

_MD5_RE = re.compile(r"^[0-9a-f]{32}$")


def _check_scalar(path: str, value: Any, limit: int) -> list[str]:
    if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
        return []
    if not isinstance(value, str):
        return [f"{path} is {type(value).__name__}, expected a scalar"]
    if len(value) > limit:
        return [f"{path} is {len(value)} chars, over the {limit}-char cap"]
    return []


def _check_counter(key: str, value: Any) -> list[str]:
    if not isinstance(value, dict):
        return [f"'{key}' must be an object of counts"]

    problems: list[str] = []
    for position, (label, count) in enumerate(value.items()):
        # Reported by position: the label is the thing that may need withholding.
        if label not in ENTITY_LABELS:
            problems.append(
                f"'{key}' entry {position} is not a canonical entity label "
                f"(expected one of {sorted(ENTITY_LABELS)}); value withheld, "
                "it may contain citizen text"
            )
        if isinstance(count, bool) or not isinstance(count, int):
            problems.append(f"'{key}' entry {position} must map to an integer count")
    return problems


def check_payload(payload: Any) -> list[str]:
    """Every way this document fails the metadata contract. Empty means it passes."""
    if not isinstance(payload, dict):
        return [f"top level is {type(payload).__name__}, expected an object"]

    problems: list[str] = []
    for key, value in payload.items():
        if key not in ALLOWED_TOP:
            # The key itself is untrusted, so it is located, not quoted.
            problems.append(
                f"unknown top-level key at position {list(payload).index(key)} "
                f"(expected a subset of {sorted(ALLOWED_TOP)}); name withheld"
            )
            continue

        if key in COUNTER_OBJECTS:
            problems += _check_counter(key, value)
            continue

        if key in ALLOWED_NESTED:
            if not isinstance(value, dict):
                problems.append(f"'{key}' must be an object")
                continue
            for sub, sub_value in value.items():
                if sub not in ALLOWED_NESTED[key]:
                    problems.append(
                        f"unknown key in '{key}' at position {list(value).index(sub)} "
                        f"(expected a subset of {sorted(ALLOWED_NESTED[key])}); name withheld"
                    )
                    continue
                problems += _check_scalar(f"{key}.{sub}", sub_value, MAX_STRING)
            continue

        if key == "source_gold_md5" and isinstance(value, str) and not _MD5_RE.match(value):
            problems.append("'source_gold_md5' is not a 32-character hex digest")
            continue

        problems += _check_scalar(key, value, MAX_NOTE if key == "note" else MAX_STRING)

    return problems


def check_file(path: Path) -> list[str]:
    size = path.stat().st_size
    if size > MAX_BYTES:
        return [f"{size} bytes exceeds the {MAX_BYTES}-byte metadata cap"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"not valid JSON ({exc})"]
    return check_payload(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Sidecar files to check")
    args = parser.parse_args()

    if not args.paths:
        print("No provenance sidecars to check.")
        return 0

    failures: list[str] = []
    for path in args.paths:
        failures += [f"{path}: {problem}" for problem in check_file(path)]

    if failures:
        print("Provenance sidecars must hold metadata only:")
        for failure in failures:
            print(f"  {failure}")
        print("")
        print("Never put document text or spans in a sidecar; it is committed to Git.")
        print("Rejected values are withheld on purpose: these logs are public.")
        return 1

    print(f"Checked {len(args.paths)} provenance sidecar(s) against the metadata schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
