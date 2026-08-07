import re
import duckdb
import polars as pl
from janasunani.analytics.marts import mart_sql


def _lake_with_fixture(tmp_path):
    # Minimal lake: complaints + action_history
    complaints = pl.DataFrame({
        "ticket_no": ["T1","T2","T3","T4","T5"],
        "created_on": ["2024-01-01"]*5,
        "resolved_on": ["2024-01-02"]*5,
        "district": ["Khordha","Khordha","Ganjam","Ganjam","Khordha"],
        "category": ["Water"]*5,
        "mode": ["Online"]*5,
        "dept": ["Dept"]*5,
    })
    actions = pl.DataFrame({
        "ticket_no": ["T1","T2","T3","T4","T5"],
        "action_taken_remark": ["Case Already Taken Up.", "duplicate copy", "duplicate copy   ", "irrelevant remark", None],
        "action_taken_date": ["2024-01-02"]*5,
        "action_status": [2]*5,
        "id": [1,2,3,4,5],
    })
    lake = tmp_path / "lake"
    lake.mkdir()
    complaints.write_parquet(lake / "complaints.parquet")
    actions.write_parquet(lake / "action_history.parquet")
    return lake


def test_duplicate_recall_mart_counts(tmp_path):
    lake = _lake_with_fixture(tmp_path)
    con = duckdb.connect()
    for p in (lake).glob("*.parquet"):
        con.execute(f"CREATE VIEW {p.stem} AS SELECT * FROM read_parquet('{p.as_posix()}')")
    con.execute(mart_sql("duplicate_recall"))
    s = con.execute("SELECT * FROM duplicate_baseline_summary").pl().row(0, named=True)
    # T1 normalized -> taken_up, T2,T3 -> duplicate_copy, T4/T5 not counted
    assert s["officer_confirmed_total"] == 3
    assert s["taken_up"] == 1
    assert s["duplicate_copy"] == 2
    # prevalence
    by_dist = con.execute("SELECT * FROM duplicate_prevalence_by_district").pl()
    assert by_dist.height >= 1
    con.close()


def test_render_never_reads_grievance():
    sql = mart_sql("duplicate_recall")
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"'[^']*'", "''", sql)
    assert "grievance" not in sql.lower()


def test_compute_and_render(tmp_path):
    lake = _lake_with_fixture(tmp_path)
    from janasunani.analytics.findings.duplicate_recall import compute, render_markdown
    tables = compute(lake_dir=lake)
    assert "duplicate_baseline_summary" in tables
    md = render_markdown(tables)
    assert "Officer-confirmed" in md
    assert "34,671" in md or "34671" in md
