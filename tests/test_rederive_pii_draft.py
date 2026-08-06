"""Tests for scripts/rederive_pii_draft.py.

The claim the script rests on: a bootstrap draft's spans are a pure function of
its text plus the pinned analyzer, so a lost draft can be rebuilt from the gold.
`test_rederivation_is_stable` and the round-trip test exercise that through the
real analyzer. The rest guards the two ways this could destroy data: overwriting
an original draft, and producing a file nobody can tell apart from one.

Loaded via importlib (scripts/ is not a package).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("presidio_analyzer")

from janasunani.pipeline.pii_eval import load_gold_jsonl  # noqa: E402
from janasunani.pipeline.stages.pii_tagger import PIISpan  # noqa: E402

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rederive_pii_draft.py"
_spec = importlib.util.spec_from_file_location("rederive_pii_draft", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)

_TEXT = "Ramesh Kumar, mobile 9876543210, ward 7 water supply broken"


def _gold(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "gold.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


def _fake_predict(text: str):
    return (PIISpan(entity="NAME", start=0, end=12, score=0.9),)


def test_load_records_keeps_id_and_text(tmp_path):
    gold = _gold(tmp_path, {"id": "CMO1_p1", "text": _TEXT, "entities": []})

    [record] = mod.load_records(gold)

    assert record["id"] == "CMO1_p1"
    assert record["text"] == _TEXT


def test_load_records_rejects_a_record_without_text(tmp_path):
    gold = _gold(tmp_path, {"id": "CMO1_p1", "entities": []})

    with pytest.raises(ValueError, match="missing 'text'"):
        mod.load_records(gold)


def test_load_records_rejects_duplicate_ids(tmp_path):
    # pii_eval refuses duplicate ids, so a draft carrying them is unusable.
    gold = _gold(
        tmp_path,
        {"id": "CMO1_p1", "text": _TEXT},
        {"id": "CMO1_p1", "text": _TEXT},
    )

    with pytest.raises(ValueError, match="duplicate record ids"):
        mod.load_records(gold)


def test_rederive_replaces_gold_spans_with_predicted_ones(tmp_path):
    # The gold's own entities must not leak into the draft: the draft is what the
    # analyzer said, which is the baseline the human pass is measured against.
    records = [{"id": "CMO1_p1", "text": _TEXT, "entities": [{"start": 40, "end": 44, "entity": "NAME"}]}]

    lines, by_entity = mod.rederive(records, _fake_predict)

    assert by_entity == {"NAME": 1}
    assert json.loads(lines[0])["entities"] == [{"start": 0, "end": 12, "entity": "NAME"}]


def test_rederived_lines_round_trip_through_the_evaluator_loader(tmp_path):
    from janasunani.pipeline.stages.pii_tagger import detect_pii_spans

    records = [{"id": "CMO1_p1", "text": _TEXT}]

    lines, by_entity = mod.rederive(records, detect_pii_spans)
    draft = tmp_path / "draft.jsonl"
    draft.write_text("\n".join(lines) + "\n", encoding="utf-8")

    [example] = load_gold_jsonl(draft)
    assert example.id == "CMO1_p1"
    assert example.text == _TEXT
    assert sum(by_entity.values()) == len(example.entities) > 0


def test_rederivation_is_stable(tmp_path):
    """Same text plus same analyzer gives byte-identical spans, which is the
    premise that makes rebuilding a lost draft legitimate."""
    from janasunani.pipeline.stages.pii_tagger import detect_pii_spans

    records = [{"id": "CMO1_p1", "text": _TEXT}]

    first, _ = mod.rederive(records, detect_pii_spans)
    second, _ = mod.rederive(records, detect_pii_spans)

    assert first == second


def test_provenance_records_the_gold_checksum_and_flags_the_kind(tmp_path):
    gold = _gold(tmp_path, {"id": "CMO1_p1", "text": _TEXT})
    expected_md5 = mod._file_md5(gold)

    meta = mod.provenance(gold, tmp_path / "draft.jsonl", 1, mod.Counter({"NAME": 1}))

    assert meta["kind"] == "rederived_draft"
    assert meta["source_gold_md5"] == expected_md5
    assert meta["records"] == 1 and meta["spans"] == 1
    # Without the versions a re-derived draft cannot be matched to the analyzer
    # that produced it, which is the whole point of the sidecar.
    assert set(meta["analyzer"]) >= {"presidio_analyzer", "spacy", "en_core_web_sm"}


def test_cli_refuses_to_overwrite_an_existing_draft(tmp_path, monkeypatch, capsys):
    # The original bootstrap draft cannot be regenerated (#57), so clobbering one
    # is unrecoverable data loss.
    gold = _gold(tmp_path, {"id": "CMO1_p1", "text": _TEXT})
    out = tmp_path / "draft.jsonl"
    out.write_text("do not clobber\n", encoding="utf-8")

    monkeypatch.setattr(
        sys, "argv", ["rederive_pii_draft.py", "--gold", str(gold), "--out", str(out)]
    )
    with pytest.raises(SystemExit) as excinfo:
        mod.main()

    assert excinfo.value.code != 0
    assert out.read_text(encoding="utf-8") == "do not clobber\n"


def test_cli_writes_the_draft_and_a_provenance_sidecar(tmp_path, monkeypatch, capsys):
    gold = _gold(tmp_path, {"id": "CMO1_p1", "text": _TEXT})
    out = tmp_path / "draft.jsonl"

    monkeypatch.setattr(
        sys, "argv", ["rederive_pii_draft.py", "--gold", str(gold), "--out", str(out)]
    )
    mod.main()

    [example] = load_gold_jsonl(out)
    assert example.id == "CMO1_p1"

    sidecar = tmp_path / "draft.jsonl.provenance.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["source_gold_md5"] == mod._file_md5(gold)
    assert meta["spans"] == len(example.entities)

    # No citizen text on stdout, ever.
    assert _TEXT not in capsys.readouterr().out


def test_cli_refuses_to_write_over_the_gold_even_with_force(tmp_path, monkeypatch):
    """--force exists to replace a stale draft. Aimed at the gold it would destroy
    irreplaceable human labelling and then hash the wreckage as the source
    checksum, so it is refused regardless of the flag."""
    gold = _gold(tmp_path, {"id": "CMO1_p1", "text": _TEXT})
    before = gold.read_text(encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["rederive_pii_draft.py", "--gold", str(gold), "--out", str(gold), "--force"],
    )
    with pytest.raises(SystemExit) as excinfo:
        mod.main()

    assert excinfo.value.code != 0
    assert gold.read_text(encoding="utf-8") == before


def test_cli_refuses_an_aliased_path_to_the_gold(tmp_path, monkeypatch):
    """Same file reached by a different path string is the same destruction."""
    gold = _gold(tmp_path, {"id": "CMO1_p1", "text": _TEXT})
    before = gold.read_text(encoding="utf-8")
    aliased = tmp_path / "sub" / ".." / gold.name
    (tmp_path / "sub").mkdir()

    monkeypatch.setattr(
        sys,
        "argv",
        ["rederive_pii_draft.py", "--gold", str(gold), "--out", str(aliased), "--force"],
    )
    with pytest.raises(SystemExit) as excinfo:
        mod.main()

    assert excinfo.value.code != 0
    assert gold.read_text(encoding="utf-8") == before


def test_git_commit_notices_an_uncommitted_analyzer(tmp_path, monkeypatch):
    """Pathspecs resolve against the working directory, so running from scripts/
    silently matched nothing and every draft was recorded as clean -- attributing
    output to committed code that may not have produced it."""
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if "--show-toplevel" in cmd:
            return _Result("/repo\n")
        if "rev-parse" in cmd:
            return _Result("abc1234\n")
        return _Result(" M janasunani/pipeline/stages/pii_tagger.py\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert mod._git_commit() == "abc1234-dirty"
    status = next(c for c in calls if "status" in c)
    # The pathspecs must be interpreted from the repository root, not scripts/.
    assert status[: status.index("status")] == ["git", "-C", "/repo"]
