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

    def test_rebound_only_record_counts_as_touched(self, tmp_path):
        """A record fixed solely by moving a boundary was still worked on."""
        draft = self._draft(tmp_path)
        gold = write_jsonl(
            tmp_path / "gold.jsonl",
            [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 4, "entity": "NAME"}]}],
        )
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["spans_rebounded"] == 1
        assert result["spans_added"] == 0 and result["spans_removed"] == 0
        assert result["records_touched"] == 1

    def test_relabel_only_record_counts_as_touched(self, tmp_path):
        draft = self._draft(tmp_path)
        gold = write_jsonl(
            tmp_path / "gold.jsonl",
            [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 10, "entity": "PAN"}]}],
        )
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["spans_relabelled"] == 1
        assert result["records_touched"] == 1

    def test_edited_text_is_detected(self, tmp_path):
        """Offsets index into the text, so an edit invalidates every span."""
        draft = self._draft(tmp_path)
        gold = write_jsonl(
            tmp_path / "gold.jsonl",
            [
                {
                    "id": "a",
                    "text": "Ravi  Patra called 9876543210",  # extra space shifts offsets
                    "entities": [{"start": 0, "end": 10, "entity": "NAME"}],
                }
            ],
        )
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["text_mismatches"] == ["a"]

    def test_identical_text_reports_no_mismatch(self, tmp_path):
        draft = self._draft(tmp_path)
        gold = write_jsonl(tmp_path / "gold.jsonl", json_lines(draft))
        result = verify.diff(verify.load(draft), verify.load(gold))
        assert result["text_mismatches"] == []

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


class TestCLIExitStatus:
    """Reporting a completeness problem is not enough; the command must fail so a
    pre-merge gate catches it."""

    def _run(self, monkeypatch, gold: Path, draft: Path) -> int:
        monkeypatch.setattr(
            sys, "argv", ["verify_pii_gold.py", "--gold", str(gold), "--draft", str(draft)]
        )
        try:
            verify.main()
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0

    def _pair(self, tmp_path, gold_rows, draft_rows):
        return (
            write_jsonl(tmp_path / "gold.jsonl", gold_rows),
            write_jsonl(tmp_path / "draft.jsonl", draft_rows),
        )

    def test_clean_pair_exits_zero(self, tmp_path, monkeypatch):
        rows = [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 10, "entity": "NAME"}]}]
        gold, draft = self._pair(tmp_path, rows, [{"id": "a", "text": TEXT, "entities": []}])
        assert self._run(monkeypatch, gold, draft) == 0

    def test_page_dropped_from_gold_fails(self, tmp_path, monkeypatch):
        gold, draft = self._pair(
            tmp_path,
            [{"id": "a", "text": TEXT, "entities": []}],
            [{"id": "a", "text": TEXT, "entities": []}, {"id": "b", "text": TEXT, "entities": []}],
        )
        assert self._run(monkeypatch, gold, draft) == 1

    def test_extra_gold_record_fails(self, tmp_path, monkeypatch):
        gold, draft = self._pair(
            tmp_path,
            [{"id": "a", "text": TEXT, "entities": []}, {"id": "b", "text": TEXT, "entities": []}],
            [{"id": "a", "text": TEXT, "entities": []}],
        )
        assert self._run(monkeypatch, gold, draft) == 1

    def test_edited_text_fails(self, tmp_path, monkeypatch):
        gold, draft = self._pair(
            tmp_path,
            [{"id": "a", "text": "Ravi  Patra called 9876543210", "entities": []}],
            [{"id": "a", "text": TEXT, "entities": []}],
        )
        assert self._run(monkeypatch, gold, draft) == 1


def json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestMalformedGoldIsRejected:
    """Each of these once verified clean. A gold file that passes the gate but is
    structurally wrong produces a plausible privacy number, which is worse than a
    crash: nothing downstream can tell the difference."""

    def test_empty_file_is_rejected(self, tmp_path):
        path = tmp_path / "gold.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="no records"):
            verify.load(path)

    def test_blank_lines_only_is_rejected(self, tmp_path):
        path = tmp_path / "gold.jsonl"
        path.write_text("\n\n   \n", encoding="utf-8")
        with pytest.raises(ValueError, match="no records"):
            verify.load(path)

    def test_missing_entities_field_is_rejected(self, tmp_path):
        """pii_eval's loader requires the field, so accepting it here would pass an
        artifact the scorecard then refuses to load."""
        path = write_jsonl(tmp_path / "gold.jsonl", [{"id": "a", "text": TEXT}])
        with pytest.raises(ValueError, match="'entities' missing"):
            verify.load(path)

    def test_explicitly_empty_entities_is_accepted(self, tmp_path):
        path = write_jsonl(tmp_path / "gold.jsonl", [{"id": "a", "text": TEXT, "entities": []}])
        assert verify.check(verify.load(path)).errors == []

    @pytest.mark.parametrize("bad", [0.9, True, "0", None])
    def test_non_integer_offsets_are_rejected(self, tmp_path, bad):
        """int() would turn 0.9 into 0 and True into 1, verifying clean against
        boundaries the gold does not contain."""
        path = write_jsonl(
            tmp_path / "gold.jsonl",
            [{"id": "a", "text": TEXT, "entities": [{"start": bad, "end": 10, "entity": "NAME"}]}],
        )
        with pytest.raises(ValueError, match="must be an integer"):
            verify.load(path)


class TestUntrustedLabelsAreNotEchoed:
    """The entity field is annotator input and this report is written to be pasted
    into a PR, so a bad label is counted, never repeated."""

    LEAKED = "Ravi Patra, At/Po Balipatna"

    def _report(self, tmp_path):
        path = write_jsonl(
            tmp_path / "gold.jsonl",
            [
                {
                    "id": "a",
                    "text": TEXT,
                    "entities": [{"start": 0, "end": 10, "entity": self.LEAKED}],
                }
            ],
        )
        return verify.check(verify.load(path))

    def test_bad_label_still_fails(self, tmp_path):
        report = self._report(tmp_path)
        assert any("unrecognised entity label" in e for e in report.errors)

    def test_bad_label_value_never_appears_in_errors(self, tmp_path):
        report = self._report(tmp_path)
        assert all(self.LEAKED.upper() not in e.upper() for e in report.errors)

    def test_bad_label_value_never_appears_in_counts(self, tmp_path):
        report = self._report(tmp_path)
        assert self.LEAKED.upper() not in report.by_entity
        assert report.by_entity[verify.UNRECOGNISED_ENTITY] == 1


class TestVoidMeasurementsFail:
    def _run(self, monkeypatch, gold: Path, draft: Path) -> int:
        monkeypatch.setattr(
            sys, "argv", ["verify_pii_gold.py", "--gold", str(gold), "--draft", str(draft)]
        )
        try:
            verify.main()
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0

    def test_gold_identical_to_draft_fails(self, tmp_path, monkeypatch):
        """Analyzer output graded against itself scores ~100% recall. That is a void
        measurement, not a perfect one."""
        rows = [{"id": "a", "text": TEXT, "entities": [{"start": 0, "end": 10, "entity": "NAME"}]}]
        gold = write_jsonl(tmp_path / "gold.jsonl", rows)
        draft = write_jsonl(tmp_path / "draft.jsonl", rows)
        assert self._run(monkeypatch, gold, draft) == 1

    def test_duplicate_draft_ids_are_rejected(self, tmp_path):
        """dict() would silently keep the last record, and the completeness check is
        set-based, so the id sets would still match while a record was dropped."""
        draft = verify.load(
            write_jsonl(
                tmp_path / "draft.jsonl",
                [
                    {"id": "a", "text": TEXT, "entities": []},
                    {"id": "a", "text": TEXT, "entities": []},
                ],
            )
        )
        gold = verify.load(
            write_jsonl(tmp_path / "gold.jsonl", [{"id": "a", "text": TEXT, "entities": []}])
        )
        with pytest.raises(ValueError, match="duplicate record ids"):
            verify.diff(draft, gold)


class TestRecordIdsAreNotPublishedByDefault:
    """#96. Record ids are `{ticket}_p{page}` — not citizen text, but the join
    key to it. This report exists to be pasted into a PR, and the failure mode
    is exactly the one where someone pastes it: the arrays fill, the tool exits
    1, and the natural next step is to ask for help in public."""

    TICKET = "CMOFF-D-2021-01956_p2"

    def _run(self, monkeypatch, capsys, gold: Path, draft: Path, *extra: str):
        monkeypatch.setattr(
            sys,
            "argv",
            ["verify_pii_gold.py", "--gold", str(gold), "--draft", str(draft), *extra],
        )
        try:
            verify.main()
        except SystemExit:
            pass
        return capsys.readouterr().out

    def _mismatched_pair(self, tmp_path):
        gold = write_jsonl(
            tmp_path / "gold.jsonl",
            [{"id": self.TICKET, "text": TEXT, "entities": []}],
        )
        draft = write_jsonl(
            tmp_path / "draft.jsonl",
            [{"id": "some-other-page_p1", "text": TEXT, "entities": []}],
        )
        return gold, draft

    def test_ticket_ids_absent_from_default_output(self, tmp_path, monkeypatch, capsys):
        gold, draft = self._mismatched_pair(tmp_path)
        out = self._run(monkeypatch, capsys, gold, draft)
        assert self.TICKET not in out
        assert "--show-samples" in out

    def test_ticket_ids_absent_from_json_output(self, tmp_path, monkeypatch, capsys):
        gold, draft = self._mismatched_pair(tmp_path)
        out = self._run(monkeypatch, capsys, gold, draft, "--json")
        assert self.TICKET not in out
        assert "records_only_in_gold_count" in out

    def test_counts_are_still_reported(self, tmp_path, monkeypatch, capsys):
        gold, draft = self._mismatched_pair(tmp_path)
        out = self._run(monkeypatch, capsys, gold, draft, "--json")
        payload = json.loads(out)
        human = payload["human_pass"]
        assert human["records_only_in_gold_count"] == 1
        assert human["records_only_in_draft_count"] == 1

    def test_show_samples_still_lists_them(self, tmp_path, monkeypatch, capsys):
        """The flag that already guards citizen text guards identities too."""
        gold, draft = self._mismatched_pair(tmp_path)
        out = self._run(monkeypatch, capsys, gold, draft, "--show-samples")
        assert self.TICKET in out
