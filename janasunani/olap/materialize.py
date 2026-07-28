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

# Tables exported from the OLTP store to the lake. pages/documents are the
# document pipeline's outputs (empty until the exporter has run).
LAKE_TABLES = ("complaints", "action_history", "pages", "documents")

# Columns held back from the lake.
#
# ``pages.extracted_text`` is the raw OCR of a scanned document, un-redacted by
# construction: ``pii_tagger`` reads it and writes its output to
# ``redacted_text``, leaving this column untouched. It stays in the pipeline's
# artifact DB and in the access-controlled OLTP store, but it must not reach
# Parquet, which is the input to every downstream analytical consumer and is
# not access-controlled per-column. Nothing downstream reads it: the summarizer
# and categorizer take ``redacted_text`` off the artifact DB, and the OCR
# benchmark reads the artifact DB directly.
#
# This is the page-text half of the guarantee. The other half — the historical
# ``complaints.grievance``, which has never met ``pii_tagger`` — is controlled
# by access rather than redaction, and gets its own redaction pass. See
# ROADMAP §3.2.
LAKE_COLUMN_DENYLIST: dict[str, frozenset[str]] = {
    "pages": frozenset({"extracted_text"}),
}


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


def _select_list(con: duckdb.DuckDBPyConnection, table: str, alias: str = "oltp") -> str:
    """Column list for ``table``, minus anything on the denylist.

    Returns ``*`` when nothing is held back, so the emitted SQL stays readable
    and the common tables keep their existing behaviour exactly.
    """
    denied = LAKE_COLUMN_DENYLIST.get(table)
    if not denied:
        return "*"
    cursor = con.execute(f"SELECT * FROM {alias}.{table} LIMIT 0")  # noqa: S608
    columns = [c[0] for c in cursor.description]
    kept = [c for c in columns if c not in denied]
    if not kept:
        raise ValueError(f"denylist would drop every column of {table!r}")
    dropped = sorted(set(columns) & denied)
    if dropped:
        logger.info(f"{table}: holding back {', '.join(dropped)} from the lake")
    return ", ".join(f'"{c}"' for c in kept)


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
            columns = _select_list(con, table)
            con.execute(
                f"COPY (SELECT {columns} FROM oltp.{table}) TO '{path}' (FORMAT parquet)"  # noqa: S608
            )
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
