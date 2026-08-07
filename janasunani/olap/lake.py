"""Read helpers over the OLAP Parquet lake (DuckDB / Polars).

The lake is just Parquet files in ``data/interim/`` (one per table). Query them
with DuckDB SQL via :func:`query`, or pull a whole table as a Polars DataFrame via
:func:`read`. This is the analytics + ML + demo-history read path.
"""

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Optional, Sequence

import duckdb
import polars as pl

from janasunani.config import directories

_TABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def lake_path(table: str, lake_dir: Optional[Path] = None) -> Path:
    """Path to a table's Parquet file in the lake."""
    base = Path(lake_dir) if lake_dir else directories.INTERIM
    return base / f"{table}.parquet"


def connect(
    lake_dir: Optional[Path] = None, *, tables: Optional[Sequence[str]] = None
) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection with each lake Parquet registered as a view named after
    the file stem (e.g. ``complaints``).

    ``tables`` narrows the connection to exact named files without enumerating
    the rest of the directory. Analytics jobs over protected data should always
    use it so their runtime access matches their declared DVC dependencies.
    """
    con = duckdb.connect()
    base = Path(lake_dir) if lake_dir else directories.INTERIM
    if tables is None:
        paths = sorted(base.glob("*.parquet"))
    else:
        invalid = [table for table in tables if not _TABLE_NAME_RE.fullmatch(table)]
        if invalid:
            con.close()
            raise ValueError(f"invalid lake table names: {invalid}")
        paths = [base / f"{table}.parquet" for table in tables]
        missing = [path for path in paths if not path.is_file()]
        if missing:
            con.close()
            raise FileNotFoundError(
                "Missing lake tables: " + ", ".join(path.name for path in missing)
            )
    for path in paths:
        con.execute(
            f"CREATE VIEW {path.stem} AS SELECT * FROM read_parquet('{path.as_posix()}')"
        )
    return con


def query(
    sql: str,
    lake_dir: Optional[Path] = None,
    *,
    tables: Optional[Sequence[str]] = None,
) -> pl.DataFrame:
    """Run a DuckDB SQL query against the lake views; return a Polars DataFrame."""
    con = connect(lake_dir, tables=tables)
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
