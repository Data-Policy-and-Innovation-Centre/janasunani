"""Execution strategies for the OCR stage.

Differs from the format classifier's executors in two ways:

1. DeepSeek MUST run serially within a single process — each GPU model
   instance takes multi-GB of VRAM, so multiprocessing isn't viable.
   For DeepSeek, cross-machine parallelism is done via worker sharding
   (--worker-id / --num-workers), not local subprocesses.

2. Pytesseract benefits from multiprocessing (CPU-bound, no GPU state).

The `shard_work_items()` helper supports the SLURM-array pattern: split
a list of items across N workers by round-robin index. Worker M processes
indices M, M + N, M + 2N, ...
"""
from __future__ import annotations

import os
from multiprocessing import Pool, cpu_count
from typing import Any, Callable, Iterable, Iterator, Protocol

_SERIAL_THRESHOLD = 20


class Executor(Protocol):
    def map(self, fn: Callable[[Any], Any], items: Iterable[Any]) -> Iterator[Any]: ...
    def close(self) -> None: ...


class SerialExecutor:
    """Run the function inline, one item at a time."""

    def __init__(self, initializer: Callable | None = None, initargs: tuple = ()) -> None:
        if initializer is not None:
            initializer(*initargs)

    def map(self, fn: Callable[[Any], Any], items: Iterable[Any]) -> Iterator[Any]:
        for item in items:
            yield fn(item)

    def close(self) -> None:
        pass


class MultiprocessExecutor:
    """Fan out across N worker processes via multiprocessing.Pool."""

    def __init__(
        self,
        n_workers: int,
        initializer: Callable | None = None,
        initargs: tuple = (),
    ) -> None:
        self._pool = Pool(processes=n_workers, initializer=initializer, initargs=initargs)

    def map(self, fn: Callable[[Any], Any], items: Iterable[Any]) -> Iterator[Any]:
        yield from self._pool.imap_unordered(fn, items)

    def close(self) -> None:
        self._pool.close()
        self._pool.join()


def default_worker_count() -> int:
    """How many workers to use by default.

    Respects SLURM_CPUS_PER_TASK if set, otherwise uses all cores.
    """
    slurm = os.environ.get("SLURM_CPUS_PER_TASK")
    if slurm and slurm.isdigit():
        return int(slurm)
    return cpu_count()


def pick_executor(
    backend: str,
    n_items: int,
    n_workers: int | None = None,
    initializer: Callable | None = None,
    initargs: tuple = (),
) -> Executor:
    """Pick an executor based on backend and workload size.

    DeepSeek always runs serial (GPU memory limits make multiprocessing
    impractical — cross-machine parallelism is done via worker sharding).

    Pytesseract picks serial for small batches, multiprocessing otherwise.
    """
    if backend in {"deepseek", "sarvam"}:
        return SerialExecutor(initializer=initializer, initargs=initargs)

    # Pytesseract path
    if n_workers == 1:
        return SerialExecutor(initializer=initializer, initargs=initargs)
    if n_workers is None and n_items < _SERIAL_THRESHOLD:
        return SerialExecutor(initializer=initializer, initargs=initargs)
    workers = n_workers if n_workers is not None else default_worker_count()
    return MultiprocessExecutor(n_workers=workers, initializer=initializer, initargs=initargs)


def shard_work_items(items: list, worker_id: int, num_workers: int) -> list:
    """Round-robin slice for cross-machine sharding.

    Worker M of N processes indices M, M+N, M+2*N, ...

    Args:
        items: Full list of work items.
        worker_id: 0-indexed worker id.
        num_workers: Total number of workers across all machines.

    Returns:
        The subset of items this worker should process.
    """
    if num_workers <= 1:
        return items
    if not (0 <= worker_id < num_workers):
        raise ValueError(
            f"worker_id ({worker_id}) must be in [0, num_workers={num_workers})"
        )
    return [item for i, item in enumerate(items) if i % num_workers == worker_id]
