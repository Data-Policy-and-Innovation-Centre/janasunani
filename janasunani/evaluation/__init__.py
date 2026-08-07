"""Harnesses that measure a stage against ground truth and report.

The boundary against :mod:`janasunani.pipeline` is what the code does when it
finds a bad number, not what it computes:

* ``pipeline.pii_eval`` is a **gate**. The pipeline runs it, and it can fail a
  release. It stays in ``pipeline/`` with the thing it gates.
* Modules here **report**. Nothing in the pipeline imports them, each has its
  own CLI, and a bad result is an answer rather than a failure.

Both scorecards landed on 2026-08-07 from separate Sprint 3 branches, one in
``pipeline/`` and one here, because that rule had never been written down.
"""
