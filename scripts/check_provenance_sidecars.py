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
# "note" is the one deliberately long field: a fixed caveat written by the
# script, never derived from a record.
MAX_NOTE = 1000

_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
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
            problems.append(f"{key} entry {position} is not allowlisted; value withheld")
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
        for key in sample_design_keys - {"production_prevalence_representative"}:
            problems += _check_scalar(
                f"sample_design.{key}", sample_design.get(key), MAX_STRING
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
    for key in {"group_policy", "label_interpretation", "split_policy"}:
        problems += _check_scalar(key, payload[key], MAX_STRING)

    eligible = payload["eligible_categories"]
    excluded = payload["excluded_categories"]
    problems += _check_category_list("eligible_categories", eligible)
    problems += _check_category_list("excluded_categories", excluded)
    if isinstance(eligible, list) and isinstance(excluded, list) and set(eligible) & set(excluded):
        problems.append("eligible and excluded categories must be disjoint")
    problems += _check_count_map(
        "category_counts", payload["category_counts"], allowed_keys=_ADMIN_CATEGORIES
    )
    problems += _check_count_map(
        "split_counts", payload["split_counts"], allowed_keys={"train", "validation", "test"}
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
        problems += _check_scalar("privacy.source_column", privacy.get("source_column"), MAX_STRING)
        for key in privacy_keys - {"source_column"}:
            problems += _check_bool(f"privacy.{key}", privacy.get(key))

    split_counts = payload["split_counts"]
    if (
        isinstance(split_counts, dict)
        and all(isinstance(value, int) and not isinstance(value, bool) for value in split_counts.values())
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
    problems += _check_scalar("evidence_status", payload["evidence_status"], MAX_STRING)
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
    problems += _check_exact_keys("summary adjudication", adjudication, adjudication_keys)
    if isinstance(adjudication, dict):
        for key in {
            "independent_judges",
            "officer_validated",
            "one_time_redacted_egress_authorized",
            "structured_judgments_only_in_governed_artifacts",
        }:
            problems += _check_bool(f"adjudication.{key}", adjudication.get(key))
        problems += _check_sha256("adjudication.rubric_sha256", adjudication.get("rubric_sha256"))
        for key in adjudication_keys - {
            "independent_judges",
            "officer_validated",
            "one_time_redacted_egress_authorized",
            "rubric_sha256",
            "structured_judgments_only_in_governed_artifacts",
        }:
            problems += _check_scalar(f"adjudication.{key}", adjudication.get(key), MAX_STRING)

    environment = payload["environment"]
    environment_keys = {"device", "python", "torch", "transformers"}
    problems += _check_exact_keys("summary environment", environment, environment_keys)
    if isinstance(environment, dict):
        for key in environment_keys:
            problems += _check_scalar(f"environment.{key}", environment.get(key), MAX_STRING)

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
        problems += _check_sha256("model.weights_sha256", model.get("weights_sha256"))
        for key in {"max_input_tokens", "max_output_tokens", "min_output_tokens", "num_beams"}:
            problems += _check_nonnegative_int(f"model.{key}", model.get(key))
        for key in {"family", "revision"}:
            problems += _check_scalar(f"model.{key}", model.get(key), MAX_STRING)

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
        problems += _check_scalar("selection.policy", selection.get("policy"), MAX_STRING)
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
        if all(isinstance(selection.get(key), int) for key in {"generated", "sample_size", "skipped"}):
            if selection["generated"] + selection["skipped"] != selection["sample_size"]:
                problems.append("generated and skipped must sum to sample_size")

    source = payload["source"]
    source_keys = {"path", "redacted_only", "sha256", "split"}
    problems += _check_exact_keys("summary source", source, source_keys)
    if isinstance(source, dict):
        problems += _check_bool("source.redacted_only", source.get("redacted_only"))
        problems += _check_sha256("source.sha256", source.get("sha256"))
        for key in {"path", "split"}:
            problems += _check_scalar(f"source.{key}", source.get(key), MAX_STRING)
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
        problems = _check_exact_keys("PII re-derived sidecar", payload, ALLOWED_TOP)
        if problems:
            return problems
    if schema == _SARVAM_SCHEMA:
        return _check_sarvam_snapshots(payload)
    if schema == _SUMMARY_SCHEMA:
        return _check_summary_development(payload)
    if schema is not None and schema != _PII_REDERIVED_SCHEMA:
        return ["unrecognized provenance schema_version"]

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
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index : index + 2] != ("data", "external"):
            continue
        relative = parts[index + 2 :]
        is_legacy_root = relative == ("provenance.json",)
        if not is_legacy_root and (
            not isinstance(payload, dict)
            or payload.get("schema_version") not in _RECOGNIZED_SCHEMAS
        ):
            return ["nested provenance sidecar must declare a recognized schema_version"]
        break
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
