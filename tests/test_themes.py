"""Tests for janasunani.analytics.findings.themes.

compute_themes has three early-exit shapes below the full theme schema (no
redacted rows at all, too few rows in the largest category, too little TF-IDF
vocabulary) plus the full clustering path. render_markdown must handle all of
them without crashing: found live, running janasunani-publish-themes against a
slice with only a handful of locally-staged redacted records raised
ColumnNotFoundError("filings") because the "too few rows" and "insufficient
vocabulary" branches return a {category, note} frame, not the {theme_id,
filings, ...} frame the renderer assumed.
"""

from __future__ import annotations

import datetime

import polars as pl

from janasunani.analytics.findings.themes import (
    MIN_THEME_SIZE,
    compute_themes,
    render_markdown,
)


_COMPLAINTS_SCHEMA = {
    "ticket_no": pl.Utf8,
    "district": pl.Utf8,
    "category": pl.Utf8,
    "created_on": pl.Datetime,
    "created_year": pl.Int64,
}
_REDACTIONS_SCHEMA = {"ticket_no": pl.Utf8, "grievance_redacted": pl.Utf8}


def _write_lake(tmp_path, rows: list[dict]) -> None:
    lake = tmp_path / "lake"
    lake.mkdir()
    complaints = pl.DataFrame(
        [
            {
                "ticket_no": r["ticket_no"],
                "district": r["district"],
                "category": r["category"],
                "created_on": r["created_on"],
                "created_year": r["created_on"].year,
            }
            for r in rows
        ],
        schema=_COMPLAINTS_SCHEMA,
    )
    complaints.write_parquet(lake / "complaints.parquet")
    redactions = pl.DataFrame(
        [{"ticket_no": r["ticket_no"], "grievance_redacted": r["text"]} for r in rows],
        schema=_REDACTIONS_SCHEMA,
    )
    redactions.write_parquet(lake / "grievance_redactions.parquet")
    return lake


def test_render_markdown_handles_too_few_rows_in_category(tmp_path):
    """Fewer than MIN_THEME_SIZE*2 rows in the only category must not crash."""
    base = datetime.date(2024, 1, 1)
    rows = [
        {
            "ticket_no": f"T{i}",
            "district": "Sambalpur",
            "category": "Water",
            "created_on": datetime.datetime.combine(base, datetime.time()),
            "text": f"pipe leaking near ward {i}",
        }
        for i in range(MIN_THEME_SIZE)  # below the MIN_THEME_SIZE*2 floor
    ]
    lake = _write_lake(tmp_path, rows)
    tables = compute_themes(lake_dir=lake, district="Sambalpur", year=2024)
    assert tables["themes"].height == 1
    assert "note" in tables["themes"].columns
    assert "filings" not in tables["themes"].columns

    md = render_markdown(tables)  # must not raise
    assert "insufficient data for themes" in md
    assert "No themes computed" in md
    assert "Water" in md.splitlines()[0]  # attempted category, not "unknown"


def test_render_markdown_handles_no_redacted_rows(tmp_path):
    lake = _write_lake(tmp_path, [])
    tables = compute_themes(lake_dir=lake, district="Sambalpur", year=2024)
    md = render_markdown(tables)  # must not raise
    assert "No themes computed (insufficient data)" in md


def test_render_markdown_renders_full_theme_table(tmp_path):
    """With enough rows the full {theme_id, filings, ...} schema still renders."""
    early = datetime.datetime(2024, 1, 1)
    late = datetime.datetime(2024, 6, 1)
    rows = []
    # One concentrated, rising theme in Sambalpur (mostly late).
    for i in range(3):
        rows.append(
            {
                "ticket_no": f"E{i}",
                "district": "Sambalpur",
                "category": "Water",
                "created_on": early,
                "text": "road pothole outside market repeated complaint pothole",
            }
        )
    for i in range(9):
        rows.append(
            {
                "ticket_no": f"L{i}",
                "district": "Sambalpur",
                "category": "Water",
                "created_on": late,
                "text": "road pothole outside market repeated complaint pothole",
            }
        )
    lake = _write_lake(tmp_path, rows)
    tables = compute_themes(lake_dir=lake, district="Sambalpur", year=2024, category="Water")
    md = render_markdown(tables)  # must not raise
    assert "Themes found" in md
