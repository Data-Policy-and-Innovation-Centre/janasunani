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
    "schema_version",
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

_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)
_GIT_REVISION_RE = re.compile(r"^(?:[0-9a-f]{7,40}(?:-dirty)?|unknown)$")
_VERSION_RE = re.compile(r"^(?:unknown|\d{1,4}\.\d{1,4}(?:\.\d{1,4})?[A-Za-z0-9.+-]*)$")
_SUMMARY_MODEL_REVISION_RE = re.compile(
    r"^(?:summarizer|bart-large-cnn|[0-9a-f]{7,64})$"
)
_ACTIONABILITY_SCHEMA = "actionability-adjudication-sample-v1"
_ACTIONABILITY_FRONTIER_SCHEMA = "janasunani.actionability-frontier-artifacts/v1"
_CATEGORIZATION_SCHEMA = "categorization-benchmark-sample-v1"
_PII_REDERIVED_SCHEMA = "janasunani.pii-rederived-draft-provenance/v1"
_SARVAM_SCHEMA = "janasunani.sarvam-source-snapshots/v1"
_SUMMARY_SCHEMA = "summary-development-provenance/v1"
_RECOGNIZED_SCHEMAS = {
    _ACTIONABILITY_SCHEMA,
    _ACTIONABILITY_FRONTIER_SCHEMA,
    _CATEGORIZATION_SCHEMA,
    _PII_REDERIVED_SCHEMA,
    _SARVAM_SCHEMA,
    _SUMMARY_SCHEMA,
}

_ACTIONABILITY_FRONTIER_SAMPLING = (
    "five records from each of four opaque weak-label strata plus forty previously "
    "unlabeled records per split"
)
_ACTIONABILITY_FRONTIER_TRACKING_REASON = (
    "opaque item identifiers depend on a private salt that is intentionally not "
    "versioned; treating this as a reproducible stage would hide that dependency"
)
_ACTIONABILITY_FRONTIER_INPUT_ROLES = {
    "judge_a.jsonl": "frontier judge A output",
    "judge_b.jsonl": "frontier judge B output",
    "resolver.jsonl": "canonical frontier resolver output",
    "resolver_backup.jsonl": "non-canonical preserved resolver backup",
}
_ACTIONABILITY_FRONTIER_HISTORICAL_STATUS = (
    "the original benchmark input is retained for auditability but is not canonical "
    "because it admitted six resolver judgments marked uncertain"
)
_SARVAM_CLAIM_STATUS = (
    "cached provider evidence; not OCR accuracy evidence and not a release gate"
)
_SARVAM_ARTIFACT_ROLES = {
    "interrupted_300_page_audit.sqlite": (
        "audit log for the paid run interrupted by credit exhaustion"
    ),
    "validation_5_page_audit.sqlite": (
        "audit log for the completed five-page validation run"
    ),
    "validation_5_page_scorecard.json": (
        "machine-readable scorecard from the completed validation run"
    ),
    "validation_5_page_scorecard.md": (
        "human-readable scorecard from the completed validation run"
    ),
}

_PII_NOTE = (
    "Analyzer output on the gold's own text, NOT the original bootstrap draft. "
    "Cannot prove the human pass happened, cannot detect an edited text, cannot "
    "detect pages dropped from the drafted sample. See "
    "scripts/rederive_pii_draft.py."
)
_PII_LEGACY_NOTE = (
    "Analyzer output on the gold's own text, NOT the original bootstrap draft."
)

_ADMIN_CATEGORIES = {
    "Accident",
    "Agriculture & Farming",
    "BSKY",
    "CMRF",
    "COVID-19",
    "Culture",
    "Disaster Management",
    "Education",
    "Energy",
    "Environment",
    "Excise",
    "Financial Assistance",
    "General",
    "Health Care",
    "Housing",
    "ICDS",
    "Infrastructure",
    "Irrigation",
    "Land Matters",
    "Legal",
    "Miscellaneous",
    "Pension/Retirement Benefits",
    "Police Case",
    "Public Utility",
    "School & College",
    "Service Matters",
    "Social Welfare",
    "Sports",
    "Tourism",
    "Traffic",
    "Transport",
    "Waste Management",
    "Water Supply",
    "Women Issues",
}


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


def _check_closed_string(
    path: str,
    value: Any,
    *,
    allowed: set[str] | None = None,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    """Validate a scalar against a closed vocabulary or non-prose format."""

    if not isinstance(value, str):
        return [f"{path} must be a string in its closed metadata format"]
    if allowed is not None and value not in allowed:
        return [f"{path} is not an allowlisted metadata value; value withheld"]
    if pattern is not None and pattern.fullmatch(value) is None:
        return [f"{path} does not match its closed metadata format; value withheld"]
    return []


def _check_closed_vocabulary(
    path: str,
    value: Any,
    *,
    allowed_words: set[str],
) -> list[str]:
    """Allow bounded metadata prose made only from a field-specific vocabulary."""

    if not isinstance(value, str) or not value or len(value) > MAX_STRING:
        return [f"{path} must use its closed metadata vocabulary"]
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words or any(word.casefold() not in allowed_words for word in words):
        return [f"{path} is not in its closed metadata vocabulary; value withheld"]
    remainder = re.sub(r"[A-Za-z0-9]+", "", value)
    if re.fullmatch(r"[\s,;:._()/\-]*", remainder) is None:
        return [f"{path} has invalid metadata punctuation; value withheld"]
    return []


def _check_pii_rederived(payload: dict[str, Any]) -> list[str]:
    problems = _check_exact_keys("PII re-derived sidecar", payload, ALLOWED_TOP)
    if problems:
        return problems

    problems += _check_closed_string(
        "schema_version", payload["schema_version"], allowed={_PII_REDERIVED_SCHEMA}
    )
    problems += _check_closed_string(
        "kind", payload["kind"], allowed={"rederived_draft"}
    )
    problems += _check_closed_string(
        "note", payload["note"], allowed={_PII_NOTE, _PII_LEGACY_NOTE}
    )
    problems += _check_closed_string(
        "created_utc", payload["created_utc"], pattern=_UTC_TIMESTAMP_RE
    )
    problems += _check_closed_string(
        "out", payload["out"], allowed={"pii_draft_n50.jsonl"}
    )
    problems += _check_closed_string(
        "source_gold",
        payload["source_gold"],
        allowed={"pii_gold_draft_n50.jsonl"},
    )
    if not (
        isinstance(payload["source_gold_md5"], str)
        and _MD5_RE.fullmatch(payload["source_gold_md5"])
    ):
        problems.append("source_gold_md5 is not a 32-character hex digest")
    for key in {"records", "spans"}:
        problems += _check_nonnegative_int(key, payload[key])
    problems += _check_counter("spans_by_entity", payload["spans_by_entity"])
    counts = payload["spans_by_entity"]
    if isinstance(counts, dict) and all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in counts.values()
    ):
        if sum(counts.values()) != payload["spans"]:
            problems.append("spans must equal the spans_by_entity total")

    analyzer = payload["analyzer"]
    problems += _check_exact_keys("PII analyzer", analyzer, ALLOWED_NESTED["analyzer"])
    if isinstance(analyzer, dict):
        problems += _check_closed_string(
            "analyzer.git_commit", analyzer.get("git_commit"), pattern=_GIT_REVISION_RE
        )
        for key in {"presidio_analyzer", "spacy", "en_core_web_sm"}:
            problems += _check_closed_string(
                f"analyzer.{key}", analyzer.get(key), pattern=_VERSION_RE
            )

    environment = payload["environment"]
    problems += _check_exact_keys(
        "PII environment", environment, ALLOWED_NESTED["environment"]
    )
    if isinstance(environment, dict):
        problems += _check_closed_string(
            "environment.python", environment.get("python"), pattern=_VERSION_RE
        )
        problems += _check_closed_string(
            "environment.system",
            environment.get("system"),
            allowed={"Darwin", "Linux", "Windows"},
        )
        problems += _check_closed_string(
            "environment.machine",
            environment.get("machine"),
            allowed={"AMD64", "aarch64", "arm64", "x86_64"},
        )
    return problems


def _check_allowlisted_list(
    key: str,
    value: Any,
    *,
    allowed: set[str],
    require_all: bool = True,
) -> list[str]:
    """Validate a metadata list without admitting arbitrary short text."""
    if not isinstance(value, list):
        return [f"'{key}' must be a list"]
    problems: list[str] = []
    if not value:
        problems.append(f"{key} must not be empty")
    for position, item in enumerate(value):
        if not isinstance(item, str) or item not in allowed:
            problems.append(
                f"{key} entry {position} is not allowlisted; value withheld"
            )
    hashable_items = [item for item in value if isinstance(item, str)]
    if len(hashable_items) != len(set(hashable_items)):
        problems.append(f"{key} must not contain duplicates")
    if require_all and set(hashable_items) != allowed:
        problems.append(f"{key} must contain the complete allowlisted metadata set")
    return problems


def _check_actionability_sample(payload: dict[str, Any]) -> list[str]:
    allowed = {
        "counts",
        "dataset_fingerprint",
        "forbidden_fields",
        "parameters",
        "records",
        "sample_design",
        "schema_version",
        "selected_fields",
    }
    problems = _check_exact_keys("actionability sidecar", payload, allowed)
    if problems:
        return problems
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
                problems.append(
                    f"actionability counts key {position} is not an allowed split/stratum"
                )
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problems.append(
                    f"actionability counts value {position} must be a nonnegative integer"
                )
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
        problems += _check_exact_keys(
            "actionability parameters", parameters, allowed_parameters
        )
        for key in {
            "per_weak_stratum_split",
            "shaped_pii_excluded",
            "unlabeled_per_split",
        }:
            problems += _check_nonnegative_int(
                f"parameters.{key}", parameters.get(key)
            )
        fixed_parameters = {
            "adjudicator_blinding": {"sampling strata are opaque s1-s5"},
            "seed": {"actionability-gold-v1"},
            "split_policy": {
                "chronological_2021_2023_train_2024_validation_2025_test",
                "single_snapshot_hash_60_20_20_development_only",
            },
            "ticket_identifier": {"salted_sha256_not_reversible"},
        }
        for key, allowed_values in fixed_parameters.items():
            problems += _check_closed_string(
                f"parameters.{key}", parameters.get(key), allowed=allowed_values
            )
    records = payload.get("records")
    if isinstance(records, bool) or not isinstance(records, int) or records < 0:
        problems.append("actionability records must be a nonnegative integer")
    problems += _check_allowlisted_list(
        "forbidden_fields",
        payload.get("forbidden_fields"),
        allowed={
            "ticket_no",
            "raw grievance",
            "officer remark",
            "petitioner identifiers",
            "office",
        },
    )
    problems += _check_allowlisted_list(
        "selected_fields",
        payload.get("selected_fields"),
        allowed={
            "salted item/group id",
            "grievance_redacted",
            "created_year",
            "split",
            "opaque sampling stratum",
        },
    )
    sample_design = payload.get("sample_design")
    sample_design_keys = {
        "sampling_scheme",
        "production_prevalence_representative",
        "metric_interpretation",
        "intended_use",
    }
    problems += _check_exact_keys(
        "actionability sample_design", sample_design, sample_design_keys
    )
    if isinstance(sample_design, dict):
        problems += _check_bool(
            "sample_design.production_prevalence_representative",
            sample_design.get("production_prevalence_representative"),
        )
        if sample_design.get("production_prevalence_representative") is not False:
            problems.append(
                "sample_design.production_prevalence_representative must be False"
            )
        fixed_design = {
            "sampling_scheme": {"fixed quotas across opaque sampling strata"},
            "metric_interpretation": {
                "composition-specific development metrics",
                (
                    "accuracy, precision, PPV, and review workload measured on this "
                    "sample are composition-specific and are not production prevalence"
                ),
            },
            "intended_use": {"development model comparison and error analysis"},
        }
        for key, allowed_values in fixed_design.items():
            problems += _check_closed_string(
                f"sample_design.{key}",
                sample_design.get(key),
                allowed=allowed_values,
            )
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


def _check_bool(path: str, value: Any) -> list[str]:
    if not isinstance(value, bool):
        return [f"{path} must be a boolean"]
    return []


def _check_category_list(path: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        return [f"{path} must be a list"]
    problems: list[str] = []
    for position, label in enumerate(value):
        if label not in _ADMIN_CATEGORIES:
            problems.append(
                f"{path} entry {position} is not an allowlisted administrative category; "
                "value withheld"
            )
    hashable_labels = [label for label in value if isinstance(label, str)]
    if len(hashable_labels) != len(set(hashable_labels)):
        problems.append(f"{path} must not contain duplicates")
    return problems


def _check_count_map(
    path: str,
    value: Any,
    *,
    allowed_keys: set[str],
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object of counts"]
    problems: list[str] = []
    for position, (key, count) in enumerate(value.items()):
        if key not in allowed_keys:
            problems.append(f"{path} key {position} is not allowlisted; value withheld")
        problems += _check_nonnegative_int(f"{path}[{position}]", count)
    return problems


def _check_categorization_sample(payload: dict[str, Any]) -> list[str]:
    top_keys = {
        "category_counts",
        "conflicting_label_groups_excluded",
        "dataset_fingerprint",
        "eligible_categories",
        "exact_text_groups",
        "excluded_categories",
        "group_policy",
        "input_rows",
        "label_interpretation",
        "min_support_per_split",
        "privacy",
        "records",
        "schema_version",
        "shaped_pii_rows_excluded",
        "split_counts",
        "split_policy",
        "year",
    }
    problems = _check_exact_keys("categorization sidecar", payload, top_keys)
    if problems:
        return problems

    problems += _check_sha256("dataset_fingerprint", payload["dataset_fingerprint"])
    for key in {
        "conflicting_label_groups_excluded",
        "exact_text_groups",
        "input_rows",
        "min_support_per_split",
        "records",
        "shaped_pii_rows_excluded",
        "year",
    }:
        problems += _check_nonnegative_int(key, payload[key])
    fixed_metadata = {
        "group_policy": {
            "one earliest row per exact normalized-redacted-text group"
        },
        "label_interpretation": {
            "historical administrative agreement, not policy correctness"
        },
        "split_policy": {
            "chronological_months_1_6_train_7_9_validation_10_12_test"
        },
    }
    for key, allowed_values in fixed_metadata.items():
        problems += _check_closed_string(
            key, payload[key], allowed=allowed_values
        )

    eligible = payload["eligible_categories"]
    excluded = payload["excluded_categories"]
    problems += _check_category_list("eligible_categories", eligible)
    problems += _check_category_list("excluded_categories", excluded)
    if (
        isinstance(eligible, list)
        and isinstance(excluded, list)
        and set(eligible) & set(excluded)
    ):
        problems.append("eligible and excluded categories must be disjoint")
    problems += _check_count_map(
        "category_counts", payload["category_counts"], allowed_keys=_ADMIN_CATEGORIES
    )
    problems += _check_count_map(
        "split_counts",
        payload["split_counts"],
        allowed_keys={"train", "validation", "test"},
    )

    privacy = payload["privacy"]
    privacy_keys = {
        "narrative_output_private_dvc_only",
        "raw_grievance_read",
        "source_column",
        "ticket_identifiers_salted",
    }
    problems += _check_exact_keys("categorization privacy", privacy, privacy_keys)
    if isinstance(privacy, dict):
        problems += _check_closed_string(
            "privacy.source_column",
            privacy.get("source_column"),
            allowed={"grievance_redactions.grievance_redacted"},
        )
        expected_privacy = {
            "narrative_output_private_dvc_only": True,
            "raw_grievance_read": False,
            "ticket_identifiers_salted": True,
        }
        for key, expected in expected_privacy.items():
            problems += _check_bool(f"privacy.{key}", privacy.get(key))
            if privacy.get(key) is not expected:
                problems.append(f"privacy.{key} must be {expected}")

    split_counts = payload["split_counts"]
    if (
        isinstance(split_counts, dict)
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in split_counts.values()
        )
        and sum(split_counts.values()) != payload["records"]
    ):
        problems.append("split counts must sum to records")
    category_counts = payload["category_counts"]
    if (
        isinstance(category_counts, dict)
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in category_counts.values()
        )
        and sum(category_counts.values()) != payload["records"]
    ):
        problems.append("category counts must sum to records")
    return problems


def _check_summary_development(payload: dict[str, Any]) -> list[str]:
    top_keys = {
        "adjudication",
        "environment",
        "evidence_status",
        "limitations",
        "model",
        "publication_ready",
        "schema_version",
        "selection",
        "source",
    }
    problems = _check_exact_keys("summary sidecar", payload, top_keys)
    if problems:
        return problems
    problems += _check_closed_string(
        "evidence_status",
        payload["evidence_status"],
        allowed={"single-frontier-judge-development-only"},
    )
    problems += _check_bool("publication_ready", payload["publication_ready"])
    if payload["publication_ready"] is not False:
        problems.append("summary development evidence cannot be publication-ready")
    problems += _check_allowlisted_list(
        "limitations",
        payload["limitations"],
        allowed={
            "typed redacted inputs only",
            "language labels not adjudicated",
            "single frontier-agent judge",
            "development test viewed",
            "edit time is adjudicator time, not officer time saved",
        },
    )

    adjudication = payload["adjudication"]
    adjudication_keys = {
        "edit_seconds_source",
        "exact_served_model_revision",
        "independent_judges",
        "judge_type",
        "narrative_review_storage",
        "officer_validated",
        "one_time_redacted_egress_authorized",
        "prompt_and_sampling_metadata",
        "provider",
        "rubric",
        "rubric_sha256",
        "structured_judgments_only_in_governed_artifacts",
    }
    problems += _check_exact_keys(
        "summary adjudication", adjudication, adjudication_keys
    )
    if isinstance(adjudication, dict):
        expected_booleans = {
            "independent_judges": False,
            "officer_validated": False,
            "one_time_redacted_egress_authorized": True,
            "structured_judgments_only_in_governed_artifacts": True,
        }
        for key, expected in expected_booleans.items():
            problems += _check_bool(f"adjudication.{key}", adjudication.get(key))
            if adjudication.get(key) is not expected:
                problems.append(f"adjudication.{key} must be {expected}")
        problems += _check_sha256(
            "adjudication.rubric_sha256", adjudication.get("rubric_sha256")
        )
        expected_strings = {
            "edit_seconds_source": "frontier-judge estimate, not observed officer time",
            "exact_served_model_revision": "unavailable",
            "judge_type": "single-frontier-agent-context",
            "narrative_review_storage": "private-temporary-only",
            "prompt_and_sampling_metadata": "unavailable-beyond-committed-rubric",
            "provider": "OpenAI Codex",
            "rubric": "summary-scorecard-v1",
        }
        for key, expected in expected_strings.items():
            problems += _check_closed_string(
                f"adjudication.{key}", adjudication.get(key), allowed={expected}
            )

    environment = payload["environment"]
    environment_keys = {"device", "python", "torch", "transformers"}
    problems += _check_exact_keys("summary environment", environment, environment_keys)
    if isinstance(environment, dict):
        problems += _check_closed_string(
            "environment.device", environment.get("device"), allowed={"cpu", "cuda"}
        )
        for key in environment_keys - {"device"}:
            problems += _check_closed_string(
                f"environment.{key}", environment.get(key), pattern=_VERSION_RE
            )

    model = payload["model"]
    model_keys = {
        "family",
        "local_files_only",
        "max_input_tokens",
        "max_output_tokens",
        "min_output_tokens",
        "num_beams",
        "revision",
        "weights_sha256",
    }
    problems += _check_exact_keys("summary model", model, model_keys)
    if isinstance(model, dict):
        problems += _check_bool("model.local_files_only", model.get("local_files_only"))
        if model.get("local_files_only") is not True:
            problems.append("model.local_files_only must be True")
        problems += _check_sha256("model.weights_sha256", model.get("weights_sha256"))
        expected_numbers = {
            "max_input_tokens": 1024,
            "max_output_tokens": 100,
            "min_output_tokens": 20,
            "num_beams": 4,
        }
        for key, expected in expected_numbers.items():
            problems += _check_nonnegative_int(f"model.{key}", model.get(key))
            if model.get(key) != expected:
                problems.append(f"model.{key} must equal the committed generator value")
        problems += _check_closed_string(
            "model.family", model.get("family"), allowed={"facebook/bart-large-cnn"}
        )
        problems += _check_closed_string(
            "model.revision", model.get("revision"), pattern=_SUMMARY_MODEL_REVISION_RE
        )

    selection = payload["selection"]
    selection_keys = {
        "cohort_counts",
        "generated",
        "not_prevalence_representative",
        "policy",
        "private_review_sha256",
        "sample_size",
        "skipped",
    }
    problems += _check_exact_keys("summary selection", selection, selection_keys)
    if isinstance(selection, dict):
        problems += _check_bool(
            "selection.not_prevalence_representative",
            selection.get("not_prevalence_representative"),
        )
        if selection.get("not_prevalence_representative") is not True:
            problems.append("selection.not_prevalence_representative must be True")
        problems += _check_closed_string(
            "selection.policy",
            selection.get("policy"),
            allowed={"deterministic-enriched-category-short-long-language-v1"},
        )
        problems += _check_sha256(
            "selection.private_review_sha256", selection.get("private_review_sha256")
        )
        for key in {"generated", "sample_size", "skipped"}:
            problems += _check_nonnegative_int(f"selection.{key}", selection.get(key))
        cohort_keys = {
            "deterministic-fill",
            "language-abstention",
            "long-input",
            "short-input",
        } | {f"category:{label}" for label in _ADMIN_CATEGORIES}
        problems += _check_count_map(
            "selection.cohort_counts",
            selection.get("cohort_counts"),
            allowed_keys=cohort_keys,
        )
        if all(
            isinstance(selection.get(key), int)
            for key in {"generated", "sample_size", "skipped"}
        ):
            if (
                selection["generated"] + selection["skipped"]
                != selection["sample_size"]
            ):
                problems.append("generated and skipped must sum to sample_size")

    source = payload["source"]
    source_keys = {"path", "redacted_only", "sha256", "split"}
    problems += _check_exact_keys("summary source", source, source_keys)
    if isinstance(source, dict):
        problems += _check_bool("source.redacted_only", source.get("redacted_only"))
        if source.get("redacted_only") is not True:
            problems.append("source.redacted_only must be True")
        problems += _check_sha256("source.sha256", source.get("sha256"))
        problems += _check_closed_string(
            "source.path",
            source.get("path"),
            allowed={"data/external/categorization_historical_v1/benchmark.jsonl"},
        )
        problems += _check_closed_string(
            "source.split", source.get("split"), allowed={"test"}
        )
    return problems


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

    problems += _check_closed_vocabulary(
        "claim_status",
        payload["claim_status"],
        allowed_words={
            "180",
            "a",
            "audit",
            "adjudicated",
            "advisory",
            "benchmark",
            "binary",
            "canonical",
            "development",
            "evidence",
            "eligible",
            "for",
            "gate",
            "historical",
            "frontier",
            "is",
            "it",
            "not",
            "confirmed",
            "officer",
            "only",
            "or",
            "outside",
            "preserved",
            "release",
            "reproducible",
            "row",
            "support",
            "test",
            "the",
            "truth",
            "viewed",
        },
    )

    privacy = payload["privacy"]
    privacy_keys = {
        "contains_redacted_narratives",
        "git_contains_row_level_bytes",
        "residual_pii_risk",
        "source",
        "storage",
    }
    problems += _check_exact_keys(
        "actionability frontier privacy", privacy, privacy_keys
    )
    if isinstance(privacy, dict):
        problems += _check_closed_vocabulary(
            "privacy.source",
            privacy.get("source"),
            allowed_words={"controlled", "dpic", "pii", "redacted", "sample"},
        )
        problems += _check_closed_string(
            "privacy.storage",
            privacy.get("storage"),
            allowed={"private DVC remote"},
        )
        expected_privacy_booleans = {
            "contains_redacted_narratives": True,
            "git_contains_row_level_bytes": False,
            "residual_pii_risk": True,
        }
        for key, expected in expected_privacy_booleans.items():
            problems += _check_bool(f"privacy.{key}", privacy.get(key))
            if privacy.get(key) is not expected:
                problems.append(f"privacy.{key} must be {expected}")

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
        problems += _check_closed_string(
            "sample.sampling",
            sample.get("sampling"),
            allowed={_ACTIONABILITY_FRONTIER_SAMPLING},
        )
        problems += _check_closed_vocabulary(
            "sample.split_policy",
            sample.get("split_policy"),
            allowed_words={
                "20",
                "2021",
                "2023",
                "2024",
                "2025",
                "60",
                "chronological",
                "development",
                "only",
                "fixed",
                "hash",
                "not",
                "single",
                "snapshot",
                "split",
                "test",
                "train",
                "validation",
            },
        )
        problems += _check_closed_vocabulary(
            "sample.tracking_mode",
            sample.get("tracking_mode"),
            allowed_words={"direct", "dvc", "input", "tracked"},
        )
        problems += _check_closed_string(
            "sample.tracking_reason",
            sample.get("tracking_reason"),
            allowed={_ACTIONABILITY_FRONTIER_TRACKING_REASON},
        )
        split_counts = sample.get("split_counts")
        problems += _check_exact_keys(
            "actionability frontier split_counts",
            split_counts,
            {"train", "validation", "test"},
        )
        if isinstance(split_counts, dict):
            for position, value in enumerate(split_counts.values()):
                problems += _check_nonnegative_int(f"split_counts[{position}]", value)

    inputs = payload["direct_inputs"]
    allowed_inputs = {
        "judge_a.jsonl",
        "judge_b.jsonl",
        "resolver.jsonl",
        "resolver_backup.jsonl",
    }
    problems += _check_exact_keys(
        "actionability frontier direct_inputs", inputs, allowed_inputs
    )
    if isinstance(inputs, dict):
        for position, (filename, details) in enumerate(inputs.items()):
            problems += _check_exact_keys(
                f"direct_inputs[{position}]", details, {"role", "sha256"}
            )
            if isinstance(details, dict):
                problems += _check_closed_string(
                    f"direct_inputs[{position}].role",
                    details.get("role"),
                    allowed={_ACTIONABILITY_FRONTIER_INPUT_ROLES.get(filename)},
                )
                problems += _check_sha256(
                    f"direct_inputs[{position}].sha256", details.get("sha256")
                )

    stages = payload["deterministic_stages"]
    allowed_stages = {
        "actionability-adjudication-prepare",
        "actionability-adjudication-finalize",
        "actionability-local-candidate-benchmark",
    }
    problems += _check_exact_keys(
        "actionability frontier deterministic_stages", stages, allowed_stages
    )
    if isinstance(stages, dict):
        expected_outputs = {
            "actionability-adjudication-prepare": {
                "consensus.jsonl",
                "resolver_input.jsonl",
                "adjudication_report.json",
            },
            "actionability-adjudication-finalize": {
                "gold.jsonl",
                "gold.manifest.json",
            },
            "actionability-local-candidate-benchmark": {
                "outputs/evaluation/actionability_candidates_muril_high_catch.json",
            },
        }
        for position, (stage, outputs) in enumerate(stages.items()):
            if stage in expected_outputs:
                problems += _check_allowlisted_list(
                    f"deterministic_stages[{position}]",
                    outputs,
                    allowed=expected_outputs[stage],
                )

    canonical = payload["canonical_reproducible_gold"]
    canonical_keys = {
        "excluded_uncertain_resolver_rows",
        "label_counts",
        "policy",
        "records",
        "sha256",
    }
    problems += _check_exact_keys(
        "actionability frontier canonical_gold", canonical, canonical_keys
    )
    if isinstance(canonical, dict):
        for key in {"records", "excluded_uncertain_resolver_rows"}:
            problems += _check_nonnegative_int(
                f"canonical_gold.{key}", canonical.get(key)
            )
        problems += _check_sha256("canonical_gold.sha256", canonical.get("sha256"))
        problems += _check_closed_vocabulary(
            "canonical_gold.policy",
            canonical.get("policy"),
            allowed_words={
                "all",
                "every",
                "exclude",
                "judgment",
                "judgments",
                "marked",
                "resolver",
                "row",
                "rows",
                "uncertain",
            },
        )
        labels = canonical.get("label_counts")
        allowed_labels = {
            "actionable",
            "underspecified",
            "irrelevant",
            "policy_blocked",
            "out_of_scope",
        }
        problems += _check_exact_keys(
            "actionability frontier label_counts", labels, allowed_labels
        )
        if isinstance(labels, dict):
            for position, value in enumerate(labels.values()):
                problems += _check_nonnegative_int(f"label_counts[{position}]", value)

    historical = payload["preserved_historical_gold"]
    historical_keys = {"artifact", "manifest", "records", "sha256", "status"}
    problems += _check_exact_keys(
        "actionability frontier historical_gold", historical, historical_keys
    )
    if isinstance(historical, dict):
        filename_words = {
            "actionability",
            "data",
            "external",
            "frontier",
            "gold",
            "historical",
            "json",
            "jsonl",
            "manifest",
            "preserved",
            "180",
            "v1",
        }
        for key in {"artifact", "manifest"}:
            problems += _check_closed_vocabulary(
                f"historical_gold.{key}",
                historical.get(key),
                allowed_words=filename_words,
            )
        problems += _check_closed_string(
            "historical_gold.status",
            historical.get("status"),
            allowed={_ACTIONABILITY_FRONTIER_HISTORICAL_STATUS},
        )
        problems += _check_nonnegative_int(
            "historical_gold.records", historical.get("records")
        )
        problems += _check_sha256("historical_gold.sha256", historical.get("sha256"))

    reports = payload["preserved_nonreproducible_reports"]
    allowed_reports = {
        "historical_candidates_strict.json",
        "historical_candidates_sensitivity.json",
        "historical_candidates_high_catch.json",
        "historical_candidates_muril_minilm_high_catch.json",
    }
    problems += _check_exact_keys(
        "actionability frontier preserved_reports", reports, allowed_reports
    )
    if isinstance(reports, dict):
        for position, digest in enumerate(reports.values()):
            problems += _check_sha256(f"preserved_reports[{position}]", digest)

    problems += _check_allowlisted_list(
        "limitations",
        payload["limitations"],
        allowed={
            "The adjudicators were separate Codex contexts, not independent model families or providers.",
            "Exact hidden prompts, sampling configuration and provider retention evidence were unavailable.",
            "The sample contains no defensible out_of_scope example and cannot validate the five-class serving contract.",
            "The four historical candidate reports used the preserved 180-row historical gold; the reproducible benchmark uses the stricter 174-row canonical gold.",
            "The historical MiniLM comparisons depend on an untracked local Hugging Face cache and are preserved as direct evidence rather than represented as reproducible stages.",
        },
    )
    return problems


def _check_sarvam_snapshots(payload: dict[str, Any]) -> list[str]:
    allowed = {"artifacts", "claim_status", "limitations", "privacy", "schema_version"}
    problems = _check_exact_keys("Sarvam sidecar", payload, allowed)
    if problems:
        return problems
    problems += _check_closed_string(
        "claim_status",
        payload.get("claim_status"),
        allowed={_SARVAM_CLAIM_STATUS},
    )
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
        problems += _check_exact_keys("Sarvam privacy", privacy, allowed_privacy)
        expected_privacy = {
            "contains_operational_ticket_and_document_identifiers": True,
            "contains_provider_response_metadata": True,
            "git_contains_row_level_bytes": False,
        }
        for key, expected in expected_privacy.items():
            problems += _check_bool(f"privacy.{key}", privacy.get(key))
            if privacy.get(key) is not expected:
                problems.append(f"privacy.{key} must be {expected}")
        problems += _check_closed_string(
            "privacy.storage",
            privacy.get("storage"),
            allowed={"private DVC remote"},
        )
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
        for artifact_position, (filename, details) in enumerate(artifacts.items()):
            if not isinstance(details, dict):
                problems.append(
                    f"Sarvam artifact {artifact_position} must be an object"
                )
                continue
            if set(details) - allowed_artifact_fields:
                problems.append(
                    f"Sarvam artifact {artifact_position} has unknown metadata keys"
                )
            problems += _check_sha256(
                f"artifacts[{artifact_position}].sha256", details.get("sha256")
            )
            problems += _check_closed_string(
                f"artifacts[{artifact_position}].role",
                details.get("role"),
                allowed={_SARVAM_ARTIFACT_ROLES.get(filename)},
            )
            for key in {
                "audit_events",
                "distinct_documents",
                "distinct_tickets",
            } & details.keys():
                problems += _check_nonnegative_int(
                    f"artifacts[{artifact_position}].{key}", details.get(key)
                )
    problems += _check_allowlisted_list(
        "limitations",
        payload.get("limitations"),
        allowed={
            "The original 300-page sample manifest and benchmark log were not recovered.",
            "The paid run ended before it wrote a complete scorecard.",
            "No hand transcription exists, so paired text divergence is not OCR accuracy.",
            "Latency distributions and actual provider billing records were not recovered.",
        },
    )
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
    if schema == _CATEGORIZATION_SCHEMA:
        return _check_categorization_sample(payload)
    if schema == _PII_REDERIVED_SCHEMA:
        return _check_pii_rederived(payload)
    if schema == _SARVAM_SCHEMA:
        return _check_sarvam_snapshots(payload)
    if schema == _SUMMARY_SCHEMA:
        return _check_summary_development(payload)
    return ["unrecognized provenance schema_version"]


def check_document(path: Path, payload: Any) -> list[str]:
    """Check an already-parsed sidecar, given the path it is stored at.

    Split out of :func:`check_file` so a caller holding the bytes -- the
    pre-commit path in `check_no_terraform_plans.py` reads staged blobs, which
    are not on disk -- gets the same verdict rather than a second, laxer one.
    The location matters to the verdict, which is why the path is a parameter:
    the legacy root sidecar is allowed to carry no `schema_version`.
    """
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index : index + 2] != ("data", "external"):
            continue
        relative = parts[index + 2 :]
        is_legacy_root = relative == ("provenance.json",)
        if is_legacy_root and isinstance(payload, dict) and "schema_version" not in payload:
            return _check_pii_rederived(
                {"schema_version": _PII_REDERIVED_SCHEMA, **payload}
            )
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") not in _RECOGNIZED_SCHEMAS
        ):
            return [
                "nested provenance sidecar must declare a recognized schema_version"
            ]
        break
    return check_payload(payload)


def check_file(path: Path) -> list[str]:
    size = path.stat().st_size
    if size > MAX_BYTES:
        return [f"{size} bytes exceeds the {MAX_BYTES}-byte metadata cap"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"not valid JSON ({exc})"]
    return check_document(path, payload)


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

    print(
        f"Checked {len(args.paths)} provenance sidecar(s) against the metadata schema."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
