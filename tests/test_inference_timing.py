"""Codex finding on #234: emit recorded timings when a stage fails.

`StageTimer.stage()` records elapsed time in a `finally` even when the stage
raises -- deliberate, because a slow failure is exactly what a latency
profile should show. But the old `process()` called `timer.emit(...)` only
after its final `return`, so a raised stage discarded exactly the
measurement that design existed for. These tests exercise the real
`PipelineGrievanceProcessor.process` code path (not just `StageTimer` in
isolation) so the fix is checked where the bug actually lived: the outer
`finally` in `process()`, not just the timer primitive.

Two properties are checked throughout:
- Timings never touch `GrievanceResult` -- the frozen serving contract.
- A sink that raises must not break a submission, on either the success or
  the failure path.
"""

from __future__ import annotations

import pytest

from janasunani.inference.service import PipelineGrievanceProcessor
from janasunani.inference.timing import NullTimer, StageTimer
from janasunani.serving.schemas import RoutingResult


def _make_processor(*, router, categorizer=None, timing_sink):
    return PipelineGrievanceProcessor(
        ocr=lambda b, n: None,
        redact=lambda t: t,
        detect_pii=lambda t: [],
        categorizer=categorizer
        or type("C", (), {"predict": lambda self, t: "Housing"})(),
        summarizer=type("S", (), {"summarize": lambda self, t: "summary"})(),
        router=router,
        is_english_compatible=lambda t: True,
        detect_language=lambda t: "en",
        timing_sink=timing_sink,
    )


class _WorkingRouter:
    def route(self, *, category, subcategory=None, district=None) -> RoutingResult:
        return RoutingResult(
            dept="Test Department", office="Test Office", confidence=0.5, method="fallback"
        )


class _RaisingRouter:
    """Stands in for "a model, or routing raises" from the finding."""

    def route(self, *, category, subcategory=None, district=None) -> RoutingResult:
        raise ValueError("routing exploded")


class _RaisingCategorizer:
    def predict(self, text: str) -> str:
        raise RuntimeError("categorizer exploded")


# --- StageTimer.emit / NullTimer.emit signature -----------------------------


def test_stage_timer_emit_marks_ok_true_by_default():
    timer = StageTimer()
    with timer.stage("route"):
        pass
    captured = []
    timer.emit(captured.append)
    assert captured[0]["ok"] == 1.0


def test_stage_timer_emit_marks_ok_false_when_told():
    timer = StageTimer()
    with timer.stage("route"):
        pass
    captured = []
    timer.emit(captured.append, ok=False)
    assert captured[0]["ok"] == 0.0
    # The stage that ran before the caller decided to mark failure is still
    # in the payload -- ok is metadata alongside the timings, not a filter.
    assert "route" in captured[0]


def test_null_timer_emit_accepts_ok_and_stays_a_noop():
    """NullTimer is only ever used with sink=None; the kwarg must not break it."""
    NullTimer().emit(None, ok=False)
    NullTimer().emit(None, ok=True)


# --- the real process() code path --------------------------------------------


def test_process_emits_on_success_with_ok_true():
    captured: list[dict] = []
    processor = _make_processor(router=_WorkingRouter(), timing_sink=captured.append)

    result = processor.process(
        grievance_id="g1",
        ticket_no="T1",
        text="the drain outside my house has been blocked for a month",
        district="Sambalpur",
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload["ok"] == 1.0
    assert "e2e" in payload
    assert "route" in payload
    # The frozen serving contract carries no timing data.
    assert "timing" not in result.model_dump()


def test_process_emits_when_the_final_stage_raises():
    """The finding's exact scenario: routing raises, and the raise happens in
    the last stage before the return, which is where the old code's single
    end-of-happy-path `emit()` call was most obviously never reached."""
    captured: list[dict] = []
    processor = _make_processor(router=_RaisingRouter(), timing_sink=captured.append)

    with pytest.raises(ValueError, match="routing exploded"):
        processor.process(
            grievance_id="g1",
            ticket_no="T1",
            text="the drain outside my house has been blocked for a month",
            district="Sambalpur",
        )

    assert len(captured) == 1, "the finally-emit must fire exactly once, not be skipped"
    payload = captured[0]
    assert payload["ok"] == 0.0
    # The raising stage's own `finally` (StageTimer.stage) still recorded it.
    assert "route" in payload
    assert "e2e" in payload


def test_process_emits_a_partial_timing_set_when_a_mid_pipeline_stage_raises():
    """A stage that never ran must not appear -- the failed set is a real
    prefix of the pipeline, not padded or faked."""
    captured: list[dict] = []
    processor = _make_processor(
        router=_WorkingRouter(),
        categorizer=_RaisingCategorizer(),
        timing_sink=captured.append,
    )

    with pytest.raises(RuntimeError, match="categorizer exploded"):
        processor.process(
            grievance_id="g1",
            ticket_no="T1",
            text="the drain outside my house has been blocked for a month",
            district="Sambalpur",
        )

    payload = captured[0]
    assert payload["ok"] == 0.0
    # Stages before the raise ran and were recorded...
    assert "redact" in payload
    assert "detect_pii" in payload
    assert "triage" in payload
    assert "detect_language" in payload
    assert "categorize" in payload  # recorded even though it's the one that raised
    # ...but routing never ran, because the exception stopped the pipeline
    # before that stage started.
    assert "route" not in payload
    assert "summarize" not in payload


def test_a_raising_sink_does_not_mask_the_original_processing_exception():
    """Measurement must not be worth more than the citizen's submission, in
    either direction: a broken sink cannot manufacture a 500, and it cannot
    swallow a real one either."""

    def bad_sink(payload: dict) -> None:
        raise RuntimeError("sink exploded")

    processor = _make_processor(router=_RaisingRouter(), timing_sink=bad_sink)

    with pytest.raises(ValueError, match="routing exploded"):
        processor.process(
            grievance_id="g1",
            ticket_no="T1",
            text="the drain outside my house has been blocked for a month",
            district="Sambalpur",
        )


def test_a_raising_sink_does_not_break_a_successful_submission():
    def bad_sink(payload: dict) -> None:
        raise RuntimeError("sink exploded")

    processor = _make_processor(router=_WorkingRouter(), timing_sink=bad_sink)

    result = processor.process(
        grievance_id="g1",
        ticket_no="T1",
        text="the drain outside my house has been blocked for a month",
        district="Sambalpur",
    )
    assert result.routing.dept == "Test Department"
