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
    "kind": "rederived_draft",
    "note": "Analyzer output on the gold's own text, NOT the original bootstrap draft.",
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
        payload = dict(VALID, spans_by_entity={"NAME": 1, "PAN": 2})
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

    def test_note_may_be_longer_than_other_scalars(self):
        assert check.check_payload(dict(VALID, note="x" * 500)) == []

    def test_note_still_has_a_ceiling(self):
        assert check.check_payload(dict(VALID, note="x" * 2000))

    def test_a_list_of_records_is_rejected(self):
        assert check.check_payload(dict(VALID, records=[{"id": "a", "text": CITIZEN_TEXT}]))

    def test_a_non_digest_checksum_is_rejected(self):
        assert check.check_payload(dict(VALID, source_gold_md5=CITIZEN_TEXT))

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

    def test_bad_sidecar_output_withholds_the_value(self, tmp_path, monkeypatch, capsys):
        payload = dict(VALID, spans_by_entity={CITIZEN_TEXT: 1})
        self._run(monkeypatch, sidecar(tmp_path, payload))
        assert CITIZEN_TEXT not in capsys.readouterr().out
