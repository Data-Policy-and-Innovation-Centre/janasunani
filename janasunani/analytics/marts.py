"""Marts: the governed derived tables under ``analytics/sql/``.

A mart is one ``.sql`` file of ``CREATE OR REPLACE VIEW`` statements over the
lake's base tables. The SQL is the deliverable, not an implementation detail.
Several of these are handed to the department to run against their own
PostgreSQL, so this module only locates and installs it. It never rewrites it,
and no mart's logic is duplicated in Python.

Portability is on the SQL author: keep to constructs DuckDB and PostgreSQL both
accept, and let ``tests/`` prove the numbers on a fixture lake.
"""

from pathlib import Path
from typing import Optional, Sequence

import duckdb

from janasunani.olap.lake import connect

SQL_DIR = Path(__file__).resolve().parent / "sql"


def mart_path(name: str) -> Path:
    """Path to a mart's SQL file."""
    path = SQL_DIR / f"{name}.sql"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in SQL_DIR.glob("*.sql"))) or "none"
        raise FileNotFoundError(f"No mart named {name!r}. Available: {available}")
    return path


def mart_sql(name: str) -> str:
    """A mart's view definitions, verbatim. This is what gets handed over."""
    return mart_path(name).read_text()


def install(con: duckdb.DuckDBPyConnection, *names: str) -> None:
    """Create a mart's views on ``con``.

    ``con`` must already expose the base tables the mart reads; over the lake
    that is what :func:`janasunani.olap.lake.connect` gives you.
    """
    for name in names:
        con.execute(mart_sql(name))


def open_lake(
    *names: str,
    lake_dir: Optional[Path] = None,
    tables: Optional[Sequence[str]] = None,
) -> duckdb.DuckDBPyConnection:
    """A lake connection with the named marts installed. Caller closes it."""
    con = connect(lake_dir, tables=tables)
    try:
        install(con, *names)
    except Exception:
        con.close()
        raise
    return con
