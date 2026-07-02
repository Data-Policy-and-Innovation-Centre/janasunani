"""Execution strategies for the format classifier.

An Executor is anything with a `map(fn, items)` method that yields
results in arbitrary order. This is intentionally similar to the
`concurrent.futures.Executor` interface so callers can drop in
ThreadPoolExecutor / ProcessPoolExecutor / a custom Slurm executor
if they want.

This module ships two executors:
  * SerialExecutor: runs in-process, one item at a time. Easy to debug,
    good for small batches and unit tests.
  * MultiprocessExecutor: uses multiprocessing.Pool to fan out across
    CPU cores. Good for batches of more than ~20 files on a single
    machine.

The `auto_executor()` helper picks one based on workload size.
"""
from __future__ import annotations

import os
from multiprocessing import Pool, cpu_count
from typing import Any, Callable, Iterable, Iterator, Protocol

# Threshold below which serial execution is faster than spinning up workers.
# Multiprocessing startup costs ~1s per worker on most systems; below this
# count the overhead dominates the actual work.
_SERIAL_THRESHOLD = 20


class Executor(Protocol):
    """Anything that can map a function over a list of items."""

    def map(
        self, fn: Callable[[Any], Any], items: Iterable[Any]
    ) -> Iterator[Any]: ...

    def close(self) -> None: ...


class SerialExecutor:
    """Run the function inline, one item at a time. No subprocesses."""

    def __init__(self, initializer: Callable | None = None, initargs: tuple = ()) -> None:
        if initializer is not None:
            initializer(*initargs)

    def map(
        self, fn: Callable[[Any], Any], items: Iterable[Any]
    ) -> Iterator[Any]:
        for item in items:
            yield fn(item)

    def close(self) -> None:
        pass


class MultiprocessExecutor:
    """Fan out across N worker processes using multiprocessing.Pool."""

    def __init__(
        self,
        n_workers: int,
        initializer: Callable | None = None,
        initargs: tuple = (),
    ) -> None:
        self._pool = Pool(
            processes=n_workers, initializer=initializer, initargs=initargs
        )

    def map(
        self, fn: Callable[[Any], Any], items: Iterable[Any]
    ) -> Iterator[Any]:
        # imap_unordered preserves no ordering but lets us start consuming
        # results as soon as the first worker finishes — important for
        # progress reporting and DB batching.
        yield from self._pool.imap_unordered(fn, items)

    def close(self) -> None:
        self._pool.close()
        self._pool.join()


def default_worker_count() -> int:
    """How many workers to use by default.

    Respects SLURM_CPUS_PER_TASK if running under Slurm, otherwise uses
    all available cores.
    """
    slurm = os.environ.get("SLURM_CPUS_PER_TASK")
    if slurm and slurm.isdigit():
        return int(slurm)
    return cpu_count()


def auto_executor(
    n_items: int,
    n_workers: int | None = None,
    initializer: Callable | None = None,
    initargs: tuple = (),
) -> Executor:
    """Pick an executor based on workload size.

    Small batches (< 20 items) run serially — multiprocessing startup
    is a wash at that scale. Larger batches use multiprocessing.

    Pass `n_workers=1` to force serial. Pass `n_workers=N > 1` to force
    multiprocessing with N workers regardless of batch size.
    """
    if n_workers == 1:
        return SerialExecutor(initializer=initializer, initargs=initargs)
    if n_workers is None and n_items < _SERIAL_THRESHOLD:
        return SerialExecutor(initializer=initializer, initargs=initargs)
    workers = n_workers if n_workers is not None else default_worker_count()
    return MultiprocessExecutor(
        n_workers=workers, initializer=initializer, initargs=initargs
    )
