"""Draft-format contract of scripts/bootstrap_pii_gold.py.

The one thing that must never drift: a line the bootstrap writes has to parse
through pii_eval's real loader with entities normalized. Loaded via importlib
(scripts/ is not a package); presidio import is lazy in the script, so only
the pii_eval dependency gates these tests.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("presidio_analyzer")

from janasunani.pipeline.pii_eval import load_gold_jsonl  # noqa: E402
from janasunani.pipeline.stages.pii_tagger import PIISpan  # noqa: E402

_SCRIPT = Path(__file__).parents[1] / "scripts" / "bootstrap_pii_gold.py"
spec = importlib.util.spec_from_file_location("bootstrap_pii_gold", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_draft_line_round_trips_through_the_evaluator_loader(tmp_path):
    text = "Ramesh Kumar, mobile 9876543210, ward 7 water supply broken"
    spans = (
        PIISpan(entity="NAME", start=0, end=12, score=0.9),
        PIISpan(entity="IN_MOBILE", start=21, end=31, score=0.6),
    )
    gold = tmp_path / "draft.jsonl"
    gold.write_text(mod.spans_to_jsonl_line("CMO123_p1", text, spans) + "\n")

    [example] = load_gold_jsonl(gold)

    assert example.id == "CMO123_p1"
    assert example.text == text
    # entity labels come back normalized by the loader (IN_MOBILE -> PHONE)
    assert example.entities == (
        PIISpan(entity="NAME", start=0, end=12),
        PIISpan(entity="PHONE", start=21, end=31),
    )


def test_draft_line_preserves_non_ascii_text():
    text = "ପାଣି problem in ward 7"
    line = mod.spans_to_jsonl_line("t_p1", text, ())
    assert "ପାଣି" in line  # ensure_ascii=False — text stays readable for labeling


def test_render_marks_spans_inline():
    text = "Ramesh Kumar, mobile 9876543210"
    spans = (
        PIISpan(entity="NAME", start=0, end=12),
        PIISpan(entity="PHONE", start=21, end=31),
    )
    rendered = mod.render_annotated(text, spans)
    assert rendered == "⟦NAME:Ramesh Kumar⟧, mobile ⟦PHONE:9876543210⟧"


def test_render_skips_overlapping_span():
    text = "Ramesh Kumar here"
    spans = (
        PIISpan(entity="NAME", start=0, end=12),
        PIISpan(entity="NAME", start=7, end=12),  # nested — first wins
    )
    rendered = mod.render_annotated(text, spans)
    assert rendered == "⟦NAME:Ramesh Kumar⟧ here"


def test_empty_spans_produce_empty_entities(tmp_path):
    gold = tmp_path / "draft.jsonl"
    gold.write_text(mod.spans_to_jsonl_line("t_p1", "no pii here at all", ()) + "\n")
    [example] = load_gold_jsonl(gold)
    assert example.entities == ()
