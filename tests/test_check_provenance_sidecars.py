"""Tests for scripts/check_provenance_sidecars.py.

Synthetic sidecars only. The point of the gate is that citizen text never
reaches git, so nothing here uses a real one.

Loaded via importlib (scripts/ is not a package).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("check_provenance_sidecars")
verify = _load("verify_pii_gold")

# A structurally valid sidecar, matching what rederive_pii_draft.py writes.
VALID = {
    "schema_version": "janasunani.pii-rederived-draft-provenance/v1",
    "kind": "rederived_draft",
    "note": (
        "Analyzer output on the gold's own text, NOT the original bootstrap draft. "
        "Cannot prove the human pass happened, cannot detect an edited text, cannot "
        "detect pages dropped from the drafted sample. See "
        "scripts/rederive_pii_draft.py."
    ),
    "created_utc": "2026-08-07T09:00:00+00:00",
    "out": "pii_draft_n50.jsonl",
    "source_gold": "pii_gold_draft_n50.jsonl",
    "source_gold_md5": "c4862fcc95548934cfd5bf004e77542d",
    "records": 89,
    "spans": 618,
    "spans_by_entity": {"AADHAAR": 14, "EMAIL": 34, "NAME": 497, "PHONE": 73},
    "analyzer": {
        "git_commit": "abc1234",
        "presidio_analyzer": "2.2.355",
        "spacy": "3.7.5",
        "en_core_web_sm": "3.7.1",
    },
    "environment": {"python": "3.12.4", "system": "Darwin", "machine": "arm64"},
}

# What must never be committable. Used as a key, a value and a label below.
CITIZEN_TEXT = "Ramesh Chandra Sahoo, At/Po Bhubaneswar"


def sidecar(tmp_path: Path, payload: dict, name: str = "x.provenance.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_real_shape_passes():
    assert check.check_payload(VALID) == []


@pytest.mark.parametrize(
    "field",
    ["kind", "note", "created_utc", "out", "source_gold"],
)
def test_pii_sidecar_rejects_free_text_scalar_values_without_echoing(field):
    payload = dict(VALID, **{field: CITIZEN_TEXT})

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("analyzer", "git_commit"),
        ("analyzer", "presidio_analyzer"),
        ("environment", "python"),
        ("environment", "system"),
        ("environment", "machine"),
    ],
)
def test_pii_sidecar_rejects_free_text_nested_scalars_without_echoing(section, field):
    payload = dict(VALID)
    payload[section] = dict(payload[section], **{field: CITIZEN_TEXT})

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


def test_pii_schema_requires_the_complete_allowlisted_shape():
    payload = dict(VALID)
    payload.pop("environment")

    assert check.check_payload(payload) == [
        "PII re-derived sidecar does not have the exact allowlisted metadata keys"
    ]


def test_unknown_schema_is_rejected():
    assert check.check_payload({**VALID, "schema_version": "unknown/v1"}) == [
        "unrecognized provenance schema_version"
    ]


def _actionability_payload():
    return {
        "schema_version": "actionability-adjudication-sample-v1",
        "dataset_fingerprint": "sha256:" + "a" * 64,
        "counts": {"train/s1": 5, "validation/s5": 40, "test/s3": 5},
        "forbidden_fields": [
            "ticket_no",
            "raw grievance",
            "officer remark",
            "petitioner identifiers",
            "office",
        ],
        "parameters": {
            "adjudicator_blinding": "sampling strata are opaque s1-s5",
            "per_weak_stratum_split": 5,
            "seed": "actionability-gold-v1",
            "shaped_pii_excluded": 47,
            "split_policy": "single_snapshot_hash_60_20_20_development_only",
            "ticket_identifier": "salted_sha256_not_reversible",
            "unlabeled_per_split": 40,
        },
        "sample_design": {
            "sampling_scheme": "fixed quotas across opaque sampling strata",
            "production_prevalence_representative": False,
            "metric_interpretation": "composition-specific development metrics",
            "intended_use": "development model comparison and error analysis",
        },
        "records": 180,
        "selected_fields": [
            "salted item/group id",
            "grievance_redacted",
            "created_year",
            "split",
            "opaque sampling stratum",
        ],
    }


def test_actionability_sample_sidecar_is_metadata_only():
    assert check.check_payload(_actionability_payload()) == []


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("parameters", "seed"),
        ("parameters", "split_policy"),
        ("sample_design", "intended_use"),
    ],
)
def test_actionability_sample_rejects_free_text_scalars_without_echoing(
    section, field
):
    payload = _actionability_payload()
    payload[section][field] = CITIZEN_TEXT

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


@pytest.mark.parametrize("field", ["forbidden_fields", "selected_fields"])
def test_actionability_sample_rejects_free_text_lists_without_echoing(field):
    payload = {
        "schema_version": "actionability-adjudication-sample-v1",
        "dataset_fingerprint": "sha256:" + "a" * 64,
        "counts": {"train/s1": 5},
        "forbidden_fields": [
            "ticket_no",
            "raw grievance",
            "officer remark",
            "petitioner identifiers",
            "office",
        ],
        "parameters": {},
        "records": 5,
        "selected_fields": [
            "salted item/group id",
            "grievance_redacted",
            "created_year",
            "split",
            "opaque sampling stratum",
        ],
        "sample_design": {},
    }
    payload[field] = [CITIZEN_TEXT]

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


def _categorization_payload():
    return {
        "schema_version": "categorization-benchmark-sample-v1",
        "dataset_fingerprint": "sha256:" + "a" * 64,
        "records": 1,
        "year": 2024,
        "split_policy": "chronological_months_1_6_train_7_9_validation_10_12_test",
        "group_policy": "one earliest row per exact normalized-redacted-text group",
        "min_support_per_split": 1,
        "label_interpretation": (
            "historical administrative agreement, not policy correctness"
        ),
        "privacy": {
            "source_column": "grievance_redactions.grievance_redacted",
            "raw_grievance_read": False,
            "ticket_identifiers_salted": True,
            "narrative_output_private_dvc_only": True,
        },
        "input_rows": 1,
        "exact_text_groups": 1,
        "conflicting_label_groups_excluded": 0,
        "shaped_pii_rows_excluded": 0,
        "eligible_categories": ["Housing"],
        "excluded_categories": ["Tourism"],
        "split_counts": {"test": 1},
        "category_counts": {"Housing": 1},
    }


def test_categorization_sample_sidecar_is_metadata_only():
    assert check.check_payload(_categorization_payload()) == []


@pytest.mark.parametrize(
    ("section", "field"),
    [(None, "split_policy"), ("privacy", "source_column")],
)
def test_categorization_sidecar_rejects_free_text_scalars_without_echoing(
    section, field
):
    payload = _categorization_payload()
    target = payload if section is None else payload[section]
    target[field] = CITIZEN_TEXT

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


def test_categorization_sidecar_rejects_malformed_counts_without_crashing():
    payload = _categorization_payload()
    payload["category_counts"]["General"] = "not-a-count"

    problems = check.check_payload(payload)

    assert problems
    assert all("not-a-count" not in problem for problem in problems)


def _summary_payload():
    return {
        "schema_version": "summary-development-provenance/v1",
        "evidence_status": "single-frontier-judge-development-only",
        "publication_ready": False,
        "limitations": [
            "typed redacted inputs only",
            "language labels not adjudicated",
            "single frontier-agent judge",
            "development test viewed",
            "edit time is adjudicator time, not officer time saved",
        ],
        "adjudication": {
            "independent_judges": False,
            "judge_type": "single-frontier-agent-context",
            "narrative_review_storage": "private-temporary-only",
            "officer_validated": False,
            "one_time_redacted_egress_authorized": True,
            "provider": "OpenAI Codex",
            "exact_served_model_revision": "unavailable",
            "prompt_and_sampling_metadata": "unavailable-beyond-committed-rubric",
            "edit_seconds_source": "frontier-judge estimate, not observed officer time",
            "rubric": "summary-scorecard-v1",
            "rubric_sha256": "b" * 64,
            "structured_judgments_only_in_governed_artifacts": True,
        },
        "environment": {
            "device": "cpu",
            "python": "3.13",
            "torch": "2.12",
            "transformers": "4.57",
        },
        "model": {
            "family": "facebook/bart-large-cnn",
            "local_files_only": True,
            "revision": "c" * 40,
            "max_input_tokens": 1024,
            "max_output_tokens": 100,
            "min_output_tokens": 20,
            "num_beams": 4,
            "weights_sha256": "d" * 64,
        },
        "selection": {
            "cohort_counts": {"category:Housing": 1},
            "generated": 1,
            "not_prevalence_representative": True,
            "policy": "deterministic-enriched-category-short-long-language-v1",
            "private_review_sha256": "e" * 64,
            "sample_size": 1,
            "skipped": 0,
        },
        "source": {
            "path": "data/external/categorization_historical_v1/benchmark.jsonl",
            "redacted_only": True,
            "sha256": "f" * 64,
            "split": "test",
        },
    }


def test_summary_development_sidecar_is_metadata_only():
    assert check.check_payload(_summary_payload()) == []


def test_summary_sidecar_rejects_free_text_limitations_without_echoing():
    payload = {
        "schema_version": "summary-development-provenance/v1",
        "evidence_status": "single-frontier-judge-development-only",
        "publication_ready": False,
        "limitations": [CITIZEN_TEXT],
        "adjudication": {},
        "environment": {},
        "model": {},
        "selection": {},
        "source": {},
    }

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


@pytest.mark.parametrize(
    "field_path",
    [
        ("evidence_status",),
        ("adjudication", "provider"),
        ("environment", "device"),
        ("model", "family"),
        ("model", "revision"),
        ("selection", "policy"),
        ("source", "path"),
        ("source", "split"),
    ],
)
def test_summary_sidecar_rejects_free_text_scalars_without_echoing(field_path):
    payload = _summary_payload()
    target = payload
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = CITIZEN_TEXT

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


@pytest.mark.parametrize("revision", ["summarizer", "bart-large-cnn", "a1b2c3d"])
def test_summary_sidecar_accepts_closed_model_revisions(revision):
    payload = _summary_payload()
    payload["model"]["revision"] = revision

    assert check.check_payload(payload) == []


def test_allowlisted_list_rejects_non_string_items_without_crashing():
    assert check._check_allowlisted_list("metadata", [{}], allowed={"fixed"})


def test_allowlisted_list_rejects_empty_required_metadata():
    assert check._check_allowlisted_list(
        "metadata", [], allowed={"fixed"}, require_all=False
    )


@pytest.mark.parametrize(
    "schema",
    ["categorization-benchmark-sample-v1", "summary-development-provenance/v1"],
)
def test_benchmark_sidecars_reject_unexpected_fields_without_echoing(schema):
    payload = {"schema_version": schema, CITIZEN_TEXT: CITIZEN_TEXT}
    problems = check.check_payload(payload)
    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


def _frontier_payload():
    return {
        "schema_version": "janasunani.actionability-frontier-artifacts/v1",
        "claim_status": "development evidence only",
        "privacy": {
            "source": "PII-redacted sample",
            "contains_redacted_narratives": True,
            "residual_pii_risk": True,
            "git_contains_row_level_bytes": False,
            "storage": "private DVC remote",
        },
        "sample": {
            "records": 3,
            "split_counts": {"train": 1, "validation": 1, "test": 1},
            "sampling": "fixed sample",
            "split_policy": "fixed hash split",
            "sha256": "a" * 64,
            "tracking_mode": "direct DVC input",
            "tracking_reason": "private salt is not versioned",
        },
        "direct_inputs": {
            name: {"role": "model output", "sha256": "b" * 64}
            for name in [
                "judge_a.jsonl",
                "judge_b.jsonl",
                "resolver.jsonl",
                "resolver_backup.jsonl",
            ]
        },
        "deterministic_stages": {
            "actionability-adjudication-prepare": [
                "consensus.jsonl",
                "resolver_input.jsonl",
                "adjudication_report.json",
            ],
            "actionability-adjudication-finalize": ["gold.jsonl", "gold.manifest.json"],
            "actionability-local-candidate-benchmark": [
                "outputs/evaluation/actionability_candidates_muril_high_catch.json"
            ],
        },
        "canonical_reproducible_gold": {
            "records": 3,
            "sha256": "c" * 64,
            "label_counts": {
                "actionable": 2,
                "underspecified": 1,
                "irrelevant": 0,
                "policy_blocked": 0,
                "out_of_scope": 0,
            },
            "policy": "exclude uncertain rows",
            "excluded_uncertain_resolver_rows": 1,
        },
        "preserved_historical_gold": {
            "artifact": "historical.jsonl",
            "manifest": "historical.manifest.json",
            "records": 4,
            "sha256": "d" * 64,
            "status": "audit only",
        },
        "preserved_nonreproducible_reports": {
            "historical_candidates_strict.json": "e" * 64,
            "historical_candidates_sensitivity.json": "f" * 64,
            "historical_candidates_high_catch.json": "1" * 64,
            "historical_candidates_muril_minilm_high_catch.json": "2" * 64,
        },
        "limitations": [
            "The adjudicators were separate Codex contexts, not independent model families or providers.",
            "Exact hidden prompts, sampling configuration and provider retention evidence were unavailable.",
            "The sample contains no defensible out_of_scope example and cannot validate the five-class serving contract.",
            "The four historical candidate reports used the preserved 180-row historical gold; the reproducible benchmark uses the stricter 174-row canonical gold.",
            "The historical MiniLM comparisons depend on an untracked local Hugging Face cache and are preserved as direct evidence rather than represented as reproducible stages.",
        ],
    }


def test_actionability_frontier_sidecar_is_metadata_only():
    assert check.check_payload(_frontier_payload()) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("claim_status", CITIZEN_TEXT),
        lambda payload: payload["privacy"].__setitem__("source", CITIZEN_TEXT),
        lambda payload: payload["sample"].__setitem__("sampling", CITIZEN_TEXT),
        lambda payload: payload["direct_inputs"]["judge_a.jsonl"].__setitem__(
            "role", CITIZEN_TEXT
        ),
        lambda payload: payload["canonical_reproducible_gold"].__setitem__(
            "policy", CITIZEN_TEXT
        ),
        lambda payload: payload["preserved_historical_gold"].__setitem__(
            "status", CITIZEN_TEXT
        ),
    ],
    ids=[
        "claim-status",
        "privacy-source",
        "sampling",
        "direct-input-role",
        "canonical-policy",
        "historical-status",
    ],
)
def test_actionability_frontier_rejects_free_text_scalars_without_echoing(mutate):
    payload = _frontier_payload()
    mutate(payload)

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


@pytest.mark.parametrize(
    "role",
    [
        "ramesh sahoo lives at 42 lane third input",
        "primary judge model response extra",
    ],
)
def test_actionability_frontier_roles_require_closed_values(role):
    payload = _frontier_payload()
    payload["direct_inputs"]["judge_a.jsonl"]["role"] = role

    problems = check.check_payload(payload)

    assert problems
    assert all(role not in problem for problem in problems)


def test_actionability_frontier_rejects_unexpected_nested_fields_without_echoing():
    payload = _frontier_payload()
    payload["privacy"][CITIZEN_TEXT] = CITIZEN_TEXT
    problems = check.check_payload(payload)
    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


@pytest.mark.parametrize(
    "payload_factory",
    [
        lambda: {
            **_frontier_payload(),
            "limitations": [CITIZEN_TEXT],
        },
        lambda: {
            **_frontier_payload(),
            "deterministic_stages": {
                **_frontier_payload()["deterministic_stages"],
                "actionability-adjudication-prepare": [CITIZEN_TEXT],
            },
        },
    ],
)
def test_actionability_frontier_rejects_free_text_lists_without_echoing(
    payload_factory,
):
    problems = check.check_payload(payload_factory())

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


def _sarvam_payload():
    return {
        "schema_version": "janasunani.sarvam-source-snapshots/v1",
        "claim_status": "cached provider evidence; not OCR accuracy",
        "privacy": {
            "contains_operational_ticket_and_document_identifiers": True,
            "contains_provider_response_metadata": True,
            "git_contains_row_level_bytes": False,
            "storage": "private DVC remote",
        },
        "artifacts": {
            "validation_5_page_scorecard.json": {
                "sha256": "a" * 64,
                "role": "machine-readable aggregate scorecard",
            }
        },
        "limitations": [
            "The original 300-page sample manifest and benchmark log were not recovered.",
            "The paid run ended before it wrote a complete scorecard.",
            "No hand transcription exists, so paired text divergence is not OCR accuracy.",
            "Latency distributions and actual provider billing records were not recovered.",
        ],
    }


def test_sarvam_source_snapshot_sidecar_is_metadata_only():
    assert check.check_payload(_sarvam_payload()) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("claim_status", CITIZEN_TEXT),
        lambda payload: payload["privacy"].__setitem__("storage", CITIZEN_TEXT),
        lambda payload: payload["artifacts"][
            "validation_5_page_scorecard.json"
        ].__setitem__("role", CITIZEN_TEXT),
    ],
    ids=["claim-status", "privacy-storage", "artifact-role"],
)
def test_sarvam_sidecar_rejects_free_text_scalars_without_echoing(mutate):
    payload = _sarvam_payload()
    mutate(payload)

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


@pytest.mark.parametrize(
    "role",
    [
        "ramesh sahoo lives at 42 lane human readable",
        "machine-readable aggregate scorecard extra",
    ],
)
def test_sarvam_artifact_roles_require_closed_values(role):
    payload = _sarvam_payload()
    payload["artifacts"]["validation_5_page_scorecard.json"]["role"] = role

    problems = check.check_payload(payload)

    assert problems
    assert all(role not in problem for problem in problems)


def test_sarvam_sidecar_rejects_free_text_limitations_without_echoing():
    payload = _sarvam_payload()
    payload["limitations"] = [CITIZEN_TEXT]

    problems = check.check_payload(payload)

    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


def test_nested_sidecar_schema_rejects_unexpected_fields():
    payload = {
        "schema_version": "actionability-adjudication-sample-v1",
        "dataset_fingerprint": "sha256:" + "a" * 64,
        "counts": {"train/s1": 5},
        "forbidden_fields": [],
        "parameters": {},
        "records": 5,
        "selected_fields": [],
        "raw_text": CITIZEN_TEXT,
    }
    problems = check.check_payload(payload)
    assert problems
    assert all(CITIZEN_TEXT not in problem for problem in problems)


class TestCounterKeysAreConstrained:
    """#95. The counter's keys were exempt from every rule because they are
    entity labels rather than a fixed key set, so a label-to-count map from a
    tool whose labels are surface forms would have been committed."""

    def test_a_surface_form_as_a_counter_key_is_rejected(self):
        payload = dict(VALID, spans_by_entity={CITIZEN_TEXT: 1})
        assert check.check_payload(payload)

    def test_the_rejected_key_is_never_echoed(self):
        """CI logs are public. Publishing what you refuse to publish defeats the gate."""
        payload = dict(VALID, spans_by_entity={CITIZEN_TEXT: 1})
        problems = check.check_payload(payload)
        assert all(CITIZEN_TEXT not in problem for problem in problems)
        assert any("withheld" in problem for problem in problems)

    def test_a_valid_label_with_a_bad_count_is_still_rejected(self):
        assert check.check_payload(dict(VALID, spans_by_entity={"NAME": CITIZEN_TEXT}))

    def test_a_bool_is_not_an_integer_count(self):
        assert check.check_payload(dict(VALID, spans_by_entity={"NAME": True}))

    def test_canonical_labels_pass(self):
        payload = dict(VALID, spans=3, spans_by_entity={"NAME": 1, "PAN": 2})
        assert check.check_payload(payload) == []

    def test_label_set_matches_the_verifier(self):
        """The two drifting apart is how this gap reopens: a label the verifier
        accepts but the gate rejects, or worse, the reverse."""
        assert check.ENTITY_LABELS == verify.KNOWN_ENTITIES


class TestUnknownKeysAreRejectedWithoutEchoing:
    @pytest.mark.parametrize("key", ["content", "excerpt", "raw", "body", "entities"])
    def test_plausible_content_keys_are_rejected(self, key):
        assert check.check_payload(dict(VALID, **{key: CITIZEN_TEXT}))

    def test_unknown_top_level_key_name_is_withheld(self):
        problems = check.check_payload({**VALID, CITIZEN_TEXT: 1})
        assert problems
        assert all(CITIZEN_TEXT not in problem for problem in problems)

    def test_unknown_nested_key_name_is_withheld(self):
        payload = dict(VALID, analyzer={**VALID["analyzer"], CITIZEN_TEXT: "x"})
        problems = check.check_payload(payload)
        assert problems
        assert all(CITIZEN_TEXT not in problem for problem in problems)


class TestValueRules:
    def test_prose_over_the_cap_is_rejected(self):
        assert check.check_payload(dict(VALID, source_gold="x" * 500))

    def test_note_is_the_fixed_generator_caveat(self):
        assert check.check_payload(dict(VALID, note="x" * 500))

    def test_note_still_has_a_ceiling(self):
        assert check.check_payload(dict(VALID, note="x" * 2000))

    def test_a_list_of_records_is_rejected(self):
        assert check.check_payload(
            dict(VALID, records=[{"id": "a", "text": CITIZEN_TEXT}])
        )

    def test_a_non_digest_checksum_is_rejected(self):
        assert check.check_payload(dict(VALID, source_gold_md5=CITIZEN_TEXT))

    @pytest.mark.parametrize("value", [123, None, True, 1.5, ["a" * 32]])
    def test_a_non_string_checksum_is_rejected(self, value):
        # A trusted checksum field must reject every non-string value.
        assert check.check_payload(dict(VALID, source_gold_md5=value))

    def test_a_digest_with_a_trailing_newline_is_rejected(self):
        # `$` matches before a trailing newline; only fullmatch anchors the end.
        assert check.check_payload(dict(VALID, source_gold_md5="a" * 32 + "\n"))

    def test_a_well_formed_digest_still_passes(self):
        assert check.check_payload(dict(VALID, source_gold_md5="0" * 31 + "f")) == []

    def test_top_level_must_be_an_object(self):
        assert check.check_payload([VALID])


class TestFileLevelChecks:
    def test_oversized_file_is_rejected(self, tmp_path):
        path = sidecar(tmp_path, dict(VALID, note="x" * 40_000))
        assert check.check_file(path)

    def test_malformed_json_is_rejected(self, tmp_path):
        path = tmp_path / "broken.provenance.json"
        path.write_text("not json", encoding="utf-8")
        assert check.check_file(path)

    def test_valid_file_passes(self, tmp_path):
        assert check.check_file(sidecar(tmp_path, VALID)) == []

    def test_nested_sidecar_requires_a_recognized_schema_without_echoing(
        self, tmp_path
    ):
        path = tmp_path / "data" / "external" / "candidate" / "provenance.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"note": CITIZEN_TEXT}), encoding="utf-8")

        problems = check.check_file(path)

        assert problems
        assert all(CITIZEN_TEXT not in problem for problem in problems)

    def test_legacy_root_sidecar_uses_closed_pii_schema(self, tmp_path):
        path = tmp_path / "data" / "external" / "provenance.json"
        path.parent.mkdir(parents=True)
        payload = {key: value for key, value in VALID.items() if key != "schema_version"}
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert check.check_file(path) == []

        payload["note"] = CITIZEN_TEXT
        path.write_text(json.dumps(payload), encoding="utf-8")
        problems = check.check_file(path)
        assert problems
        assert all(CITIZEN_TEXT not in problem for problem in problems)


class TestCLI:
    def _run(self, monkeypatch, *paths: Path) -> int:
        monkeypatch.setattr(
            sys, "argv", ["check_provenance_sidecars.py", *[str(p) for p in paths]]
        )
        return check.main()

    def test_no_paths_is_not_a_failure(self, monkeypatch):
        assert self._run(monkeypatch) == 0

    def test_clean_sidecar_exits_zero(self, tmp_path, monkeypatch):
        assert self._run(monkeypatch, sidecar(tmp_path, VALID)) == 0

    def test_bad_sidecar_exits_one(self, tmp_path, monkeypatch):
        payload = dict(VALID, spans_by_entity={CITIZEN_TEXT: 1})
        assert self._run(monkeypatch, sidecar(tmp_path, payload)) == 1

    def test_bad_sidecar_output_withholds_the_value(
        self, tmp_path, monkeypatch, capsys
    ):
        payload = dict(VALID, spans_by_entity={CITIZEN_TEXT: 1})
        self._run(monkeypatch, sidecar(tmp_path, payload))
        assert CITIZEN_TEXT not in capsys.readouterr().out
