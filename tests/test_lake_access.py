"""Exact-table access controls for protected Parquet lake jobs."""

import polars as pl
import pytest

from janasunani.olap import lake


def test_exact_table_connection_does_not_enumerate_unrelated_parquet(tmp_path):
    pl.DataFrame({"id": [1, 2]}).write_parquet(tmp_path / "authorized.parquet")
    # If the exact-table path accidentally falls back to the directory glob,
    # DuckDB will try to read this and the test will fail as invalid Parquet.
    (tmp_path / "unrelated.parquet").write_bytes(b"not parquet")

    con = lake.connect(tmp_path, tables=("authorized",))
    try:
        assert con.execute("SELECT count(*) FROM authorized").fetchone()[0] == 2
        views = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.views"
            ).fetchall()
        }
        assert "authorized" in views
        assert "unrelated" not in views
    finally:
        con.close()


def test_exact_table_connection_rejects_missing_or_unsafe_names(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing.parquet"):
        lake.connect(tmp_path, tables=("missing",))
    with pytest.raises(ValueError, match="invalid lake table"):
        lake.connect(tmp_path, tables=("bad-name",))
