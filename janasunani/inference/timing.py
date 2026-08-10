"""Per-stage wall-clock timing for one live grievance.

There has never been per-stage instrumentation in this pipeline. The benchmark
harness knew it: it recorded only end-to-end and explicitly refused to
synthesise a per-stage split rather than invent one. That refusal was right,
and this module is what makes it unnecessary.

Two design constraints, both deliberate:

**Timings never touch ``GrievanceResult``.** That is the frozen serving
contract the frontend and the result store are built on. Timing is a side
channel: the processor takes an optional sink and calls it once per
submission. A benchmark passes a sink, production passes nothing, and the wire
shape is identical either way.

**A broken sink cannot break a submission.** Measurement is not worth a 500.
``StageTimer`` swallows sink errors, and a stage that raises still records the
time it burned before raising, because a slow failure is exactly the thing
worth seeing in a latency profile. The caller (``PipelineGrievanceProcessor.
process``) emits from an outer ``finally`` rather than only at the end of the
happy path, so that recorded time actually reaches the sink instead of being
discarded along with the exception -- and it marks those timings ``"ok": 0.0``
so a report cannot silently mix a partial submission's numbers with a
completed one's.

Wall clock, via ``time.perf_counter``, not CPU time. The question being asked
is "how long does a citizen wait", and on a 2 vCPU box that includes time lost
to contention.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Iterator

from loguru import logger

#: The stages of one live submission, in execution order. Named here so a
#: report can distinguish "this stage took no time" from "this stage did not
#: run", which a bare dict cannot express.
LIVE_STAGES = (
    "ocr",
    "redact",
    "detect_pii",
    "triage",
    "detect_language",
    "categorize",
    "summarize",
    "route",
)

TimingSink = Callable[[dict[str, float]], None]


class StageTimer:
    """Accumulate per-stage seconds for a single submission.

    Not thread-safe and not meant to be: one instance measures one request.
    The processor creates one per ``process`` call.
    """

    def __init__(self) -> None:
        self._stages: dict[str, float] = {}
        self._started = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time one stage, recording elapsed time even when the stage raises."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            # Accumulate: the document path redacts once per page, and the
            # honest total for `redact` is the sum of those calls rather than
            # whichever one happened to run last.
            self._stages[name] = self._stages.get(name, 0.0) + elapsed

    def as_dict(self) -> dict[str, float]:
        """Per-stage seconds plus the wall-clock total under ``e2e``.

        ``e2e`` is measured independently rather than summed, so the gap
        between it and the sum of stages is visible. That gap is real work
        nobody has instrumented, and hiding it by construction would defeat
        the point.
        """
        timings = dict(self._stages)
        timings["e2e"] = time.perf_counter() - self._started
        return timings

    def emit(self, sink: TimingSink | None, *, ok: bool = True) -> None:
        """Hand the timings to *sink*, swallowing anything it does about it.

        ``ok`` distinguishes a submission that reached its normal return from
        one whose caller is emitting from a `finally` after a stage raised.
        Both cases must reach the sink -- a slow failure is exactly what a
        latency profile should show -- but a report that cannot tell them
        apart would silently average partial timings in with complete ones.
        Carried as a value inside the emitted dict (``"ok": 1.0`` / ``0.0``)
        rather than a second argument to *sink*, so the sink's own signature
        -- a single ``dict[str, float]`` -- does not change; a sink such as
        ``dict.update`` still works unmodified.
        """
        if sink is None:
            return
        payload = self.as_dict()
        payload["ok"] = 1.0 if ok else 0.0
        try:
            sink(payload)
        except Exception:  # pragma: no cover - measurement must not break serving
            logger.warning("timing sink raised; discarding the measurement")


class NullTimer:
    """A timer that measures nothing, for the default production path.

    Exists so ``process`` has no ``if self._timer is not None`` branches
    around every stage: the no-op costs one attribute lookup and a generator,
    and it keeps the measured and unmeasured code paths textually identical.
    """

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        del name
        yield

    def as_dict(self) -> dict[str, float]:
        return {}

    def emit(self, sink: TimingSink | None, *, ok: bool = True) -> None:
        del sink, ok
