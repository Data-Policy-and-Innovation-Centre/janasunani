"""Offline research experiments. Not imported by serving or the pipeline.

Nothing under `janasunani/experiments/` is on a production code path. These
modules read the DuckDB lake and the DVC-tracked mapping CSVs directly, write
aggregate artifacts under `outputs/experiments/`, and are excluded from the
gold-metric gates.
"""
