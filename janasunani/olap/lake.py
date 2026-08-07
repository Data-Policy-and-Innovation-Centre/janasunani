"""Read helpers over the OLAP Parquet lake (DuckDB / Polars).

The lake is just Parquet files in ``data/interim/`` (one per table). Query them
with DuckDB SQL via :func:`query`, or pull a whole table as a Polars DataFrame via
:func:`read`. This is the analytics + ML + demo-history read path.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import polars as pl

from janasunani.config import directories


def lake_path(table: str, lake_dir: Optional[Path] = None) -> Path:
    """Path to a table's Parquet file in the lake."""
    base = Path(lake_dir) if lake_dir else directories.INTERIM
    return base / f"{table}.parquet"


def connect(lake_dir: Optional[Path] = None) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection with each lake Parquet registered as a view named after
    the file stem (e.g. ``complaints``)."""
    con = duckdb.connect()
    base = Path(lake_dir) if lake_dir else directories.INTERIM
    for path in sorted(base.glob("*.parquet")):
        con.execute(
            f"CREATE VIEW {path.stem} AS SELECT * FROM read_parquet('{path.as_posix()}')"
        )
    return con


def query(sql: str, lake_dir: Optional[Path] = None) -> pl.DataFrame:
    """Run a DuckDB SQL query against the lake views; return a Polars DataFrame."""
    con = connect(lake_dir)
    try:
        return con.execute(sql).pl()
    finally:
        con.close()


def read(table: str, lake_dir: Optional[Path] = None) -> pl.DataFrame:
    """Read a whole lake table as a Polars DataFrame."""
    return pl.read_parquet(lake_path(table, lake_dir))


def lake_freshness(lake_dir: Optional[Path] = None) -> dict[str, datetime]:
    """When each lake table was last (re-)materialized, keyed by table name.

    This is the Parquet file's mtime, i.e. when ``janasunani-materialize`` last
    wrote it -- not a value read out of the data. ``GET /history`` and the
    metrics layer (:mod:`janasunani.olap.metrics`) both read this lake, and a
    live grievance lands in OLTP immediately but is invisible here until the
    next materialization run (#36 schedules that; this just reports the gap so
    a consumer never assumes "as of now").

    Naive UTC, matching every other timestamp this codebase hands to a caller
    (see the ``dedup_remark``/asyncpg note in ``db/models.py``): a tz-aware
    value survives SQLite in tests and breaks on the deployed Postgres.
    """
    base = Path(lake_dir) if lake_dir else directories.INTERIM
    return {
        path.stem: datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(
            tzinfo=None
        )
        for path in sorted(base.glob("*.parquet"))
    }
