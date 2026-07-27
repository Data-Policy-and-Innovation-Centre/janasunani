"""Tests for scripts/verify_pii_gold.py.

Synthetic text only. Real gold files hold citizen PII and never enter git.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_pii_gold.py"
_spec = importlib.util.spec_from_file_location("verify_pii_gold", _SCRIPT)
assert _spec and _spec.loader
verify = importlib.util.module_from_spec(_spec)
# @dataclass resolves annotations via sys.modules, so register before exec.
sys.modules["verify_pii_gold"] = verify
_spec.loader.exec_module(verify)


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


# "Ravi Patra called 9876543210" -> NAME [0,10), PHONE [18,28)
TEXT = "Ravi Patra called 9876543210"


def test_valid_file_passes(tmp_path):
    path = write_jsonl(
        tmp_path / "gold.jsonl",
        [
            {
                "id": "t1_p1",
                "text": TEXT,
                "entities": [
                    {"start": 0, "end": 10, "entity": "NAME"},
                    {"start": 18, "end": 28, "entity": "PHONE"},
                ],
            }
        ],
    )
    report = verify.check(verify.load(path))
    assert report.errors == []
    assert report.records == 1
    assert report.spans == 2
    assert report.by_entity["NAME"] == 1


def test_span_outside_text_is_an_error(tmp_path):
    path = write_jsonl(
        tmp_path / "g.jsonl",
        [{"id": "a", "text": "short", "entities": [{"start": 0, "end": 99, "entity": "NAME"}]}],
    )
    report = verify.check(verify.load(path))
    assert any("outside text" in e for e in report.errors)


def test_overlapping_spans_are_an_error(tmp_path):
    path = write_jsonl(
        tmp_path / "g.jsonl",
        [
            {
                "id": "a",
                "text": TEXT,
                "entities": [
                    {"start": 0, "end": 10, "entity": "NAME"},
                    {"start": 5, "end": 12, "entity": "NAME"},
                ],
            }
        ],
    )
    report = verify.check(verify.load(path))
    assert any("overlapping" in e for e in report.errors)


def test_unknown_entity_is_an_error(tmp_path):
    path = write_jsonl(
        tmp_path / "g.jsonl",
        [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 4, "entity": "PASSPORT"}]}],
    )
    report = verify.check(verify.load(path))
    assert any("unrecognised entity" in e for e in report.errors)


def test_whitespace_only_span_is_an_error(tmp_path):
    path = write_jsonl(
        tmp_path / "g.jsonl",
        [{"id": "a", "text": "a   b", "entities": [{"start": 1, "end": 4, "entity": "NAME"}]}],
    )
    report = verify.check(verify.load(path))
    assert any("whitespace only" in e for e in report.errors)


def test_loose_boundary_is_a_warning_not_an_error(tmp_path):
    path = write_jsonl(
        tmp_path / "g.jsonl",
        [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 11, "entity": "NAME"}]}],
    )
    report = verify.check(verify.load(path))
    assert report.errors == []
    assert any("loose boundaries" in w for w in report.warnings)


def test_duplicate_ids_are_an_error(tmp_path):
    row = {"id": "same", "text": TEXT, "entities": []}
    path = write_jsonl(tmp_path / "g.jsonl", [row, dict(row)])
    report = verify.check(verify.load(path))
    assert any("duplicate record id" in e for e in report.errors)


def test_label_alias_is_accepted(tmp_path):
    path = write_jsonl(
        tmp_path / "g.jsonl",
        [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 10, "label": "name"}]}],
    )
    report = verify.check(verify.load(path))
    assert report.errors == []
    assert report.by_entity["NAME"] == 1


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        verify.load(path)


def test_missing_text_raises(tmp_path):
    path = write_jsonl(tmp_path / "g.jsonl", [{"id": "a", "entities": []}])
    with pytest.raises(ValueError, match="'text' missing"):
        verify.load(path)


class TestHumanPassDiff:
    """The diff is what proves the manual labelling actually happened."""

    def _draft(self, tmp_path):
        return write_jsonl(
            tmp_path / "draft.jsonl",
            [
                {
                    "id": "a",
                    "text": TEXT,
                    "entities": [{"start": 0, "end": 10, "entity": "NAME"}],
                }
            ],
        )

    def test_unedited_draft_shows_no_change(self, tmp_path):
        draft = self._draft(tmp_path)
        gold = write_jsonl(tmp_path / "gold.jsonl", json_lines(draft))
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["spans_added"] == 0
        assert result["spans_removed"] == 0
        assert result["spans_rebounded"] == 0
        assert result["spans_unchanged"] == 1
        assert result["records_touched"] == 0

    def test_added_span_is_counted(self, tmp_path):
        draft = self._draft(tmp_path)
        gold = write_jsonl(
            tmp_path / "gold.jsonl",
            [
                {
                    "id": "a",
                    "text": TEXT,
                    "entities": [
                        {"start": 0, "end": 10, "entity": "NAME"},
                        {"start": 18, "end": 28, "entity": "PHONE"},
                    ],
                }
            ],
        )
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["spans_added"] == 1
        assert result["added_by_entity"] == {"PHONE": 1}
        assert result["records_touched"] == 1

    def test_deleted_span_is_counted(self, tmp_path):
        draft = self._draft(tmp_path)
        gold = write_jsonl(tmp_path / "gold.jsonl", [{"id": "a", "text": TEXT, "entities": []}])
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["spans_removed"] == 1
        assert result["removed_by_entity"] == {"NAME": 1}

    def test_boundary_fix_counts_as_rebounded_not_add_plus_delete(self, tmp_path):
        draft = self._draft(tmp_path)
        gold = write_jsonl(
            tmp_path / "gold.jsonl",
            [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 4, "entity": "NAME"}]}],
        )
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["spans_rebounded"] == 1
        assert result["spans_added"] == 0
        assert result["spans_removed"] == 0

    def test_relabel_counts_as_relabelled(self, tmp_path):
        draft = self._draft(tmp_path)
        gold = write_jsonl(
            tmp_path / "gold.jsonl",
            [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 10, "entity": "PAN"}]}],
        )
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["spans_relabelled"] == 1
        assert result["spans_added"] == 0
        assert result["spans_removed"] == 0

    def test_records_missing_from_gold_are_reported(self, tmp_path):
        draft = write_jsonl(
            tmp_path / "draft.jsonl",
            [
                {"id": "a", "text": TEXT, "entities": []},
                {"id": "b", "text": TEXT, "entities": []},
            ],
        )
        gold = write_jsonl(tmp_path / "gold.jsonl", [{"id": "a", "text": TEXT, "entities": []}])
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["records_only_in_draft"] == ["b"]
        assert result["records_compared"] == 1


def test_samples_are_truncated_and_capped(tmp_path):
    path = write_jsonl(
        tmp_path / "g.jsonl",
        [
            {
                "id": f"r{i}",
                "text": TEXT,
                "entities": [{"start": 0, "end": 10, "entity": "NAME"}],
            }
            for i in range(10)
        ],
    )
    samples = verify._samples(verify.load(path), limit=3, width=4)
    assert len(samples) == 3
    assert all(len(s.split(": ", 1)[1]) <= 6 for s in samples)  # 4 chars + quotes


def json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
