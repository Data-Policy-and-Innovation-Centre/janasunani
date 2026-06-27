"""Read helpers over the OLAP Parquet lake (DuckDB / Polars).

The lake is just Parquet files in ``data/interim/`` (one per table). Query them
with DuckDB SQL via :func:`query`, or pull a whole table as a Polars DataFrame via
:func:`read`. This is the analytics + ML + demo-history read path.
"""

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
