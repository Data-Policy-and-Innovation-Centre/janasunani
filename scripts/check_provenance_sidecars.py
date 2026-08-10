"""Check that committed provenance sidecars hold metadata and nothing else.

Provenance sidecars at any depth below `data/external/` are the one exception
to the rule that nothing
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

    python3 scripts/check_provenance_sidecars.py PATH [PATH ...]

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
_SHA256_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
_ACTIONABILITY_SCHEMA = "actionability-adjudication-sample-v1"
_ACTIONABILITY_FRONTIER_SCHEMA = "janasunani.actionability-frontier-artifacts/v1"
_SARVAM_SCHEMA = "janasunani.sarvam-source-snapshots/v1"


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


def _check_string_list(key: str, value: Any, *, limit: int = MAX_STRING) -> list[str]:
    if not isinstance(value, list):
        return [f"'{key}' must be a list"]
    problems: list[str] = []
    for position, item in enumerate(value):
        problems += _check_scalar(f"{key}[{position}]", item, limit)
    return problems


def _check_actionability_sample(payload: dict[str, Any]) -> list[str]:
    allowed = {
        "counts",
        "dataset_fingerprint",
        "forbidden_fields",
        "parameters",
        "records",
        "schema_version",
        "selected_fields",
    }
    problems: list[str] = []
    if set(payload) - allowed:
        problems.append("actionability sidecar has unknown top-level metadata keys")
    fingerprint = payload.get("dataset_fingerprint")
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        problems.append("actionability dataset_fingerprint is not a SHA-256 digest")
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        problems.append("actionability counts must be an object")
    else:
        key_pattern = re.compile(r"^(train|validation|test)/s[1-5]$")
        for position, (key, value) in enumerate(counts.items()):
            if not isinstance(key, str) or not key_pattern.fullmatch(key):
                problems.append(f"actionability counts key {position} is not an allowed split/stratum")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problems.append(f"actionability counts value {position} must be a nonnegative integer")
    parameters = payload.get("parameters")
    allowed_parameters = {
        "adjudicator_blinding",
        "per_weak_stratum_split",
        "seed",
        "shaped_pii_excluded",
        "split_policy",
        "ticket_identifier",
        "unlabeled_per_split",
    }
    if not isinstance(parameters, dict):
        problems.append("actionability parameters must be an object")
    else:
        for position, (key, value) in enumerate(parameters.items()):
            if key not in allowed_parameters:
                problems.append(f"actionability parameter {position} is not allowlisted")
                continue
            problems += _check_scalar(f"parameters[{position}]", value, MAX_STRING)
    records = payload.get("records")
    if isinstance(records, bool) or not isinstance(records, int) or records < 0:
        problems.append("actionability records must be a nonnegative integer")
    problems += _check_string_list("forbidden_fields", payload.get("forbidden_fields"))
    problems += _check_string_list("selected_fields", payload.get("selected_fields"))
    return problems


def _check_exact_keys(name: str, value: Any, expected: set[str]) -> list[str]:
    if not isinstance(value, dict):
        return [f"{name} must be an object"]
    if set(value) != expected:
        return [f"{name} does not have the exact allowlisted metadata keys"]
    return []


def _check_sha256(path: str, value: Any) -> list[str]:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        return [f"{path} is not a SHA-256 digest"]
    return []


def _check_nonnegative_int(path: str, value: Any) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return [f"{path} must be a nonnegative integer"]
    return []


def _check_actionability_frontier(payload: dict[str, Any]) -> list[str]:
    """Validate the aggregate manifest for privately stored frontier artifacts."""
    top_keys = {
        "canonical_reproducible_gold",
        "claim_status",
        "deterministic_stages",
        "direct_inputs",
        "limitations",
        "preserved_historical_gold",
        "preserved_nonreproducible_reports",
        "privacy",
        "sample",
        "schema_version",
    }
    problems = _check_exact_keys("actionability frontier sidecar", payload, top_keys)
    if problems:
        return problems

    problems += _check_scalar("claim_status", payload["claim_status"], MAX_STRING)

    privacy = payload["privacy"]
    privacy_keys = {
        "contains_redacted_narratives",
        "git_contains_row_level_bytes",
        "residual_pii_risk",
        "source",
        "storage",
    }
    problems += _check_exact_keys("actionability frontier privacy", privacy, privacy_keys)
    if isinstance(privacy, dict):
        for position, value in enumerate(privacy.values()):
            problems += _check_scalar(f"privacy[{position}]", value, MAX_STRING)

    sample = payload["sample"]
    sample_keys = {
        "records",
        "sampling",
        "sha256",
        "split_counts",
        "split_policy",
        "tracking_mode",
        "tracking_reason",
    }
    problems += _check_exact_keys("actionability frontier sample", sample, sample_keys)
    if isinstance(sample, dict):
        problems += _check_nonnegative_int("sample.records", sample.get("records"))
        problems += _check_sha256("sample.sha256", sample.get("sha256"))
        for key in {"sampling", "split_policy", "tracking_mode", "tracking_reason"}:
            problems += _check_scalar(f"sample.{key}", sample.get(key), MAX_STRING)
        split_counts = sample.get("split_counts")
        problems += _check_exact_keys(
            "actionability frontier split_counts", split_counts, {"train", "validation", "test"}
        )
        if isinstance(split_counts, dict):
            for position, value in enumerate(split_counts.values()):
                problems += _check_nonnegative_int(f"split_counts[{position}]", value)

    inputs = payload["direct_inputs"]
    allowed_inputs = {"judge_a.jsonl", "judge_b.jsonl", "resolver.jsonl", "resolver_backup.jsonl"}
    problems += _check_exact_keys("actionability frontier direct_inputs", inputs, allowed_inputs)
    if isinstance(inputs, dict):
        for position, details in enumerate(inputs.values()):
            problems += _check_exact_keys(f"direct_inputs[{position}]", details, {"role", "sha256"})
            if isinstance(details, dict):
                problems += _check_scalar(f"direct_inputs[{position}].role", details.get("role"), MAX_STRING)
                problems += _check_sha256(f"direct_inputs[{position}].sha256", details.get("sha256"))

    stages = payload["deterministic_stages"]
    allowed_stages = {
        "actionability-adjudication-prepare",
        "actionability-adjudication-finalize",
        "actionability-local-candidate-benchmark",
    }
    problems += _check_exact_keys("actionability frontier deterministic_stages", stages, allowed_stages)
    if isinstance(stages, dict):
        for position, outputs in enumerate(stages.values()):
            problems += _check_string_list(f"deterministic_stages[{position}]", outputs)

    canonical = payload["canonical_reproducible_gold"]
    canonical_keys = {"excluded_uncertain_resolver_rows", "label_counts", "policy", "records", "sha256"}
    problems += _check_exact_keys("actionability frontier canonical_gold", canonical, canonical_keys)
    if isinstance(canonical, dict):
        for key in {"records", "excluded_uncertain_resolver_rows"}:
            problems += _check_nonnegative_int(f"canonical_gold.{key}", canonical.get(key))
        problems += _check_sha256("canonical_gold.sha256", canonical.get("sha256"))
        problems += _check_scalar("canonical_gold.policy", canonical.get("policy"), MAX_STRING)
        labels = canonical.get("label_counts")
        allowed_labels = {"actionable", "underspecified", "irrelevant", "policy_blocked", "out_of_scope"}
        problems += _check_exact_keys("actionability frontier label_counts", labels, allowed_labels)
        if isinstance(labels, dict):
            for position, value in enumerate(labels.values()):
                problems += _check_nonnegative_int(f"label_counts[{position}]", value)

    historical = payload["preserved_historical_gold"]
    historical_keys = {"artifact", "manifest", "records", "sha256", "status"}
    problems += _check_exact_keys("actionability frontier historical_gold", historical, historical_keys)
    if isinstance(historical, dict):
        for key in {"artifact", "manifest", "status"}:
            problems += _check_scalar(f"historical_gold.{key}", historical.get(key), MAX_STRING)
        problems += _check_nonnegative_int("historical_gold.records", historical.get("records"))
        problems += _check_sha256("historical_gold.sha256", historical.get("sha256"))

    reports = payload["preserved_nonreproducible_reports"]
    allowed_reports = {
        "historical_candidates_strict.json",
        "historical_candidates_sensitivity.json",
        "historical_candidates_high_catch.json",
        "historical_candidates_muril_minilm_high_catch.json",
    }
    problems += _check_exact_keys("actionability frontier preserved_reports", reports, allowed_reports)
    if isinstance(reports, dict):
        for position, digest in enumerate(reports.values()):
            problems += _check_sha256(f"preserved_reports[{position}]", digest)

    problems += _check_string_list("limitations", payload["limitations"], limit=MAX_NOTE)
    return problems


def _check_sarvam_snapshots(payload: dict[str, Any]) -> list[str]:
    allowed = {"artifacts", "claim_status", "limitations", "privacy", "schema_version"}
    problems: list[str] = []
    if set(payload) - allowed:
        problems.append("Sarvam sidecar has unknown top-level metadata keys")
    problems += _check_scalar("claim_status", payload.get("claim_status"), MAX_STRING)
    privacy = payload.get("privacy")
    allowed_privacy = {
        "contains_operational_ticket_and_document_identifiers",
        "contains_provider_response_metadata",
        "git_contains_row_level_bytes",
        "storage",
    }
    if not isinstance(privacy, dict):
        problems.append("Sarvam privacy must be an object")
    else:
        if set(privacy) - allowed_privacy:
            problems.append("Sarvam privacy has unknown metadata keys")
        for position, value in enumerate(privacy.values()):
            problems += _check_scalar(f"privacy[{position}]", value, MAX_STRING)
    artifacts = payload.get("artifacts")
    allowed_artifacts = {
        "interrupted_300_page_audit.sqlite",
        "validation_5_page_audit.sqlite",
        "validation_5_page_scorecard.json",
        "validation_5_page_scorecard.md",
    }
    allowed_artifact_fields = {
        "audit_events",
        "distinct_documents",
        "distinct_tickets",
        "role",
        "sha256",
    }
    if not isinstance(artifacts, dict):
        problems.append("Sarvam artifacts must be an object")
    else:
        if set(artifacts) - allowed_artifacts:
            problems.append("Sarvam artifacts includes an unknown filename")
        for artifact_position, details in enumerate(artifacts.values()):
            if not isinstance(details, dict):
                problems.append(f"Sarvam artifact {artifact_position} must be an object")
                continue
            if set(details) - allowed_artifact_fields:
                problems.append(f"Sarvam artifact {artifact_position} has unknown metadata keys")
            digest = details.get("sha256")
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                problems.append(f"Sarvam artifact {artifact_position} has no valid SHA-256")
            for field_position, (key, value) in enumerate(details.items()):
                if key == "sha256":
                    continue
                problems += _check_scalar(
                    f"artifacts[{artifact_position}][{field_position}]", value, MAX_STRING
                )
    problems += _check_string_list("limitations", payload.get("limitations"))
    return problems


def check_payload(payload: Any) -> list[str]:
    """Every way this document fails the metadata contract. Empty means it passes."""
    if not isinstance(payload, dict):
        return [f"top level is {type(payload).__name__}, expected an object"]

    schema = payload.get("schema_version")
    if schema == _ACTIONABILITY_SCHEMA:
        return _check_actionability_sample(payload)
    if schema == _ACTIONABILITY_FRONTIER_SCHEMA:
        return _check_actionability_frontier(payload)
    if schema == _SARVAM_SCHEMA:
        return _check_sarvam_snapshots(payload)

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

        # Negated as a whole, not guarded by isinstance: a non-string checksum
        # (123, null, true) would otherwise skip this rule and be waved through
        # by _check_scalar, which accepts every non-string scalar. fullmatch,
        # not match, because `$` also matches before a trailing newline.
        if key == "source_gold_md5" and not (
            isinstance(value, str) and _MD5_RE.fullmatch(value)
        ):
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
