"""Tests for run_pipeline's stage-dispatch loop in janasunani.pipeline.pipeline.

Each stage module is faked via sys.modules so this exercises the dispatch
logic (canonical ordering, stage selection, db init) without importing any
stage's real (and possibly heavy: cv2/sklearn/torch) dependencies."""

import sys
import types

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.pipeline import STAGE_ORDER, run_pipeline


def _fake_stages(monkeypatch):
    calls = []
    run_attr = {
        "format_classifier": "run_format_classifier",
        "ocr_extraction": "run_ocr_extraction",
        "pii_tagger": "run_pii_tagger",
        "page_type_classifier": "run_page_type_classifier",
        "summarizer": "run_summarizer",
        "categorizer": "run_categorizer",
    }
    for stage_name, attr in run_attr.items():
        module = types.ModuleType(f"janasunani.pipeline.stages.{stage_name}")
        setattr(module, attr, lambda config, name=stage_name: calls.append(name))
        monkeypatch.setitem(
            sys.modules, f"janasunani.pipeline.stages.{stage_name}", module
        )
    return calls


def test_run_pipeline_runs_all_stages_in_canonical_order(tmp_path, monkeypatch):
    calls = _fake_stages(monkeypatch)
    config = PipelineConfig(
        input_dir=tmp_path, db_path=tmp_path / "p.sqlite", models_dir=tmp_path
    )
    run_pipeline(config)
    assert calls == STAGE_ORDER
    assert (tmp_path / "p.sqlite").exists()


def test_run_pipeline_runs_only_selected_stages_in_canonical_order(tmp_path, monkeypatch):
    calls = _fake_stages(monkeypatch)
    config = PipelineConfig(
        input_dir=tmp_path,
        db_path=tmp_path / "p.sqlite",
        models_dir=tmp_path,
        # Given out of canonical order; the loop must still run them in
        # STAGE_ORDER (OCR depends on format_classifier's output).
        stages=("categorizer", "format_classifier"),
    )
    run_pipeline(config)
    assert calls == ["format_classifier", "categorizer"]
