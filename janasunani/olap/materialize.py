"""Materialize the OLTP store to the OLAP Parquet lake (downstream of migration).

DuckDB reads the OLTP DB directly via its ``sqlite``/``postgres`` scanner and
``COPY``s each table to Parquet in ``data/interim/`` — no ORM, no hand-rolled
export. Engine-agnostic: works off ``OLTP_DB_URL`` (SQLite locally, Postgres on
deploy), so the same command runs regardless of the OLTP engine.
"""

import argparse
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import duckdb
from loguru import logger

from janasunani.config import directories, settings

# Tables exported from the OLTP store to the lake.
LAKE_TABLES = ("complaints", "action_history")


def _attach_oltp(
    con: duckdb.DuckDBPyConnection, oltp_url: str, alias: str = "oltp"
) -> None:
    """ATTACH the OLTP database into DuckDB read-only, for either engine."""
    scheme = oltp_url.split("://", 1)[0]
    if scheme.startswith("sqlite"):
        db_path = oltp_url.split(":///", 1)[1]
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{db_path}' AS {alias} (TYPE sqlite, READ_ONLY)")
    elif scheme.startswith("postgres"):
        u = urlsplit(oltp_url)
        dsn = (
            f"host={u.hostname or 'localhost'} port={u.port or 5432} "
            f"user={u.username or ''} password={u.password or ''} "
            f"dbname={(u.path or '/').lstrip('/')}"
        )
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{dsn}' AS {alias} (TYPE postgres, READ_ONLY)")
    else:
        raise ValueError(
            f"Unsupported OLTP_DB_URL scheme for materialization: {scheme!r}"
        )


def materialize(
    oltp_url: Optional[str] = None,
    out_dir: Optional[Path] = None,
    tables: tuple[str, ...] = LAKE_TABLES,
) -> dict[str, int]:
    """Export the OLTP ``tables`` to Parquet in ``out_dir``; return ``{table: rows}``."""
    oltp_url = oltp_url or settings.OLTP_DB_URL
    out = Path(out_dir) if out_dir else directories.INTERIM
    out.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    counts: dict[str, int] = {}
    try:
        _attach_oltp(con, oltp_url)
        for table in tables:
            path = out / f"{table}.parquet"
            logger.info(f"Materializing {table} -> {path}")
            con.execute(f"COPY (SELECT * FROM oltp.{table}) TO '{path}' (FORMAT parquet)")
            counts[table] = con.execute(
                f"SELECT count(*) FROM read_parquet('{path.as_posix()}')"
            ).fetchone()[0]
            logger.success(f"{table}: {counts[table]:,} rows -> {path}")
    finally:
        con.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize the OLTP store to the Parquet lake."
    )
    parser.add_argument(
        "--oltp-url", default=None, help="OLTP DB URL (default: settings.OLTP_DB_URL)."
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="Output dir (default: data/interim)."
    )
    args = parser.parse_args()
    counts = materialize(oltp_url=args.oltp_url, out_dir=args.out_dir)
    for table, n in counts.items():
        print(f"{table}: {n:,}")


if __name__ == "__main__":
    main()
