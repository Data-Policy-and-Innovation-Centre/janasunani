import re
import polars as pl
import duckdb
from janasunani.analytics.marts import mart_sql


def _lake(tmp_path):
    # 20 weeks of steady 10 per week, then a spike week with 40 (4x)
    rows = []
    import datetime
    base = datetime.date(2023, 1, 2)  # Monday
    for w in range(0, 13):
        d = base + datetime.timedelta(weeks=w)
        for i in range(10):
            rows.append((f"T{w}_{i}", d.isoformat(), "Water", "Puri"))
    spike_d = base + datetime.timedelta(weeks=13)
    for i in range(40):
        rows.append((f"TS_{i}", spike_d.isoformat(), "Water", "Puri"))
    complaints = pl.DataFrame(rows, schema=["ticket_no","created_on","category","district"], orient="row")
    # string dates -> cast in SQL via CAST(... AS DATE), so keep as string
    complaints = complaints.with_columns(pl.col("created_on").str.strptime(pl.Datetime, "%Y-%m-%d"))
    lake = tmp_path / "lake"
    lake.mkdir()
    complaints.write_parquet(lake / "complaints.parquet")
    # empty action_history to satisfy mart that may not need it but keep for open_lake
    pl.DataFrame({"ticket_no":[], "action_taken_remark":[], "action_taken_date":[], "action_status":[], "id":[]},
                 schema=[("ticket_no",pl.Utf8),("action_taken_remark",pl.Utf8),("action_taken_date",pl.Datetime),("action_status",pl.Utf8),("id",pl.Int64)]).write_parquet(lake / "action_history.parquet")
    return lake


def test_spike_mart_never_reads_grievance():
    sql = mart_sql("spike")
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"'[^']*'", "''", sql)
    assert "grievance" not in sql.lower()


def test_spike_candidate_detection(tmp_path):
    lake = _lake(tmp_path)
    con = duckdb.connect()
    for p in lake.glob("*.parquet"):
        con.execute(f"CREATE VIEW {p.stem} AS SELECT * FROM read_parquet('{p.as_posix()}')")
    con.execute(mart_sql("spike"))
    cands = con.execute("SELECT * FROM spike_candidates").pl()
    # should find at least the spike week
    assert cands.height >= 1
    # lift should be >=2
    assert (cands["lift_vs_trailing"] >= 2).all()
    con.close()


def test_spike_compute_and_render(tmp_path):
    lake = _lake(tmp_path)
    from janasunani.analytics.findings.spike import compute, render_markdown
    tables = compute(lake_dir=lake)
    assert "spike_candidates" in tables
    md = render_markdown(tables)
    assert "decomposed" in md.lower() or "decomposition" in md.lower()
    assert "campaign is not a false spike" in md.lower()
