"""Publish the supervisor dashboard release: the open queue and disposal times.

Reads ``complaints`` and ``action_history`` from the lake and writes four files
to ``outputs/dashboard/`` for ``janasunani/serving/dashboard.py`` to serve:

- ``meta.json``: the snapshot date, the entry offices and the filing years;
- ``status_counts.parquet``: filings by entry office, year and outcome;
- ``open_cases.parquet``: one row per open case;
- ``open_actions.parquet``: every recorded action on those open cases;
- ``disposed_phases.parquet``: one row per disposed case whose five phase
  spans tile its time to close (``journey.install_phases``).

Every row carries ``fy``, the July-June year it was filed in, so the dashboard
can show one year or all of them.

The files are row-level (ticket numbers and office codes) and stay out of git.
The site that serves them is restricted to people cleared to see raw
complaints. No citizen prose, contact detail or officer name is read.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from janasunani.analytics import journey
from janasunani.config import directories
from janasunani.olap import lake

_SQL = Path(__file__).with_name("sql") / "grievance_journey.sql"

# The office a case entered through, from `grievance_base.entry_route`. Police
# has two source codes and one entry route, so it is one office here too.
ENTRY_OFFICES = {
    "Forwarded from the Chief Minister's Cell": ("cm-office", "CM Office"),
    "Forwarded from a District Collector": ("collector", "Collector"),
    "Received directly by the department": ("department", "Department (direct)"),
    "Forwarded from the Chief Secretary": ("chief-secretary", "Chief Secretary"),
    "Forwarded from the Governor's office": ("governor", "Governor"),
    "Forwarded from the police": ("police", "Police"),
    "Entry point not recorded": ("not-recorded", "Entry office not recorded"),
}


def _office_case() -> str:
    return "CASE entry_route " + " ".join(
        f"WHEN {journey._sql_str(route)} THEN '{oid}'"
        for route, (oid, _label) in ENTRY_OFFICES.items()) + " END"


def open_lake(lake_dir: Path | None = None) -> duckdb.DuckDBPyConnection:
    """The two lake tables and the journey mart, with the engine bounded.

    Uncapped, DuckDB sizes its buffer pool from system RAM and never spills;
    an earlier notebook peaked at 55 GB.
    """
    con = lake.connect(lake_dir, tables=["complaints", "action_history"])
    con.execute("SET memory_limit = '8GB'")
    con.execute(f"SET temp_directory = '{tempfile.gettempdir()}/duckdb_spill'")
    con.execute("SET preserve_insertion_order = false")
    con.execute(_SQL.read_text())
    return con


def build(con: duckdb.DuckDBPyConnection, out: Path) -> dict:
    """Write the release into ``out`` and return its metadata."""
    out.mkdir(parents=True, exist_ok=True)
    # The extract cannot hold a filing, action or resolution later than the
    # day it was taken; any of them may be the last thing it recorded.
    as_of = con.execute("""
        SELECT CAST(GREATEST((SELECT MAX(created_on) FROM complaints),
                             (SELECT MAX(resolved_on) FROM complaints),
                             (SELECT MAX(action_taken_date) FROM action_history))
                    AS DATE)""").fetchone()[0]
    office = _office_case()

    # Every filing by where it entered, when, and how it stands: the tree the
    # page opens on. The outcome rule is the note's (grievance_base.outcome),
    # so its Open count is exactly the open_cases table below.
    con.execute(f"""
        CREATE OR REPLACE TABLE status_counts AS
        SELECT {office} AS entry_office, fy_start AS fy, outcome, COUNT(*) AS n
        FROM grievance_base WHERE created_on IS NOT NULL
        GROUP BY ALL
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE open_base AS
        SELECT ticket_no, fy_start AS fy, {office} AS entry_office, dept, category, subcategory,
               CAST(created_on AS DATE) AS created_on,
               DATE '{as_of}' - CAST(created_on AS DATE) AS days_open
        FROM grievance_base WHERE outcome = 'Open' AND created_on IS NOT NULL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE open_actions AS
        SELECT a.ticket_no,
               ROW_NUMBER() OVER (PARTITION BY a.ticket_no
                                  ORDER BY a.action_taken_date, a.id) AS seq,
               CAST(a.action_taken_date AS DATE) AS action_date,
               a.action_status AS status, a.code AS office
        FROM acting_office a JOIN open_base USING (ticket_no)
        WHERE a.action_taken_date IS NOT NULL
    """)
    # Awaiting assignment: no second office has ever held the case, so it is
    # still with the desk it entered at. The record does not name who a case
    # was forwarded to, only who acted, so the current office is the last one
    # that acted.
    con.execute(f"""
        CREATE OR REPLACE TABLE open_cases AS
        WITH held AS (
          SELECT ticket_no, COUNT(DISTINCT office) AS offices,
                 arg_max(office, seq) FILTER (WHERE office IS NOT NULL) AS current_office,
                 MAX(action_date) AS last_action
          FROM open_actions GROUP BY ticket_no)
        SELECT b.*, COALESCE(h.offices, 0) <= 1 AS awaiting_assignment,
               h.current_office,
               DATE '{as_of}' - GREATEST(COALESCE(h.last_action, b.created_on), b.created_on) AS days_at_current
        FROM open_base b LEFT JOIN held h USING (ticket_no)
    """)

    con.execute("""
        CREATE OR REPLACE TABLE g AS
        SELECT * FROM grievance_base WHERE is_disposed
    """)
    journey.install_steps(con)
    journey.install_phase_bounds(con)
    journey.install_phases(con)
    journey.install_route(con)
    con.execute(f"""
        CREATE OR REPLACE TABLE disposed_phases AS
        SELECT p.ticket_no, g.fy_start AS fy, {office} AS entry_office, g.dept, g.category,
               LIST_REDUCE(LIST_TRANSFORM(r.roles, x -> {journey.ROLE_CASE}),
                           (a, b) -> a || ' > ' || b) AS route,
               {', '.join(f'p.{name}' for name in journey.PHASES)}, p.days_to_close
        FROM phases_clean p JOIN g USING (ticket_no) JOIN route r USING (ticket_no)
    """)

    meta = {
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offices": [{"id": oid, "label": label} for oid, label in ENTRY_OFFICES.values()],
        "years": _years(con, as_of),
    }
    # Every file is written aside first and swapped in only once all of them
    # exist, so a failed run leaves the previous release whole. meta.json goes
    # last: the API reloads when it changes.
    tables = ("status_counts", "open_cases", "open_actions", "disposed_phases")
    for table in tables:
        con.execute(f"COPY {table} TO '{(out / table).as_posix()}.parquet.tmp' (FORMAT parquet)")
    (out / "meta.json.tmp").write_text(json.dumps(meta, indent=2) + "\n")
    for name in [f"{t}.parquet" for t in tables] + ["meta.json"]:
        os.replace(out / f"{name}.tmp", out / name)
    return meta


def fy_id(fy_start: int) -> str:
    """The id and label stem of a July-June year: 2024 -> "2024-25"."""
    return f"{fy_start}-{str(fy_start + 1)[2:]}"


def _years(con: duckdb.DuckDBPyConnection, as_of) -> list[dict]:
    """Every July-June year with a filing, newest first. A year the extract
    does not cover end to end is labelled a part year, so its smaller counts
    are not read as a quieter year."""
    rows = con.execute("""
        SELECT fy_start, CAST(MIN(created_on) AS DATE), CAST(MAX(created_on) AS DATE)
        FROM grievance_base WHERE fy_start IS NOT NULL GROUP BY 1 ORDER BY 1 DESC
    """).fetchall()
    years = []
    for fy, first, last in rows:
        start, end = date(fy, 7, 1), date(fy + 1, 6, 30)
        # A month's slack either side: a quiet first or last week is not a gap.
        part = (first - start).days > 31 or (min(end, as_of) - last).days > 31 or as_of < end
        label = f"FY {fy_id(fy)}" + (" (part year)" if part else "")
        years.append({"id": fy_id(fy), "label": label, "fy_start": fy})
    return years


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lake-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=directories.ROOT_DIR / "outputs" / "dashboard")
    args = parser.parse_args()
    con = open_lake(args.lake_dir)
    try:
        meta = build(con, args.out)
        for table in ("open_cases", "disposed_phases"):
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {n:,} rows")
    finally:
        con.close()
    print(f"as of {meta['as_of']} -> {args.out}")


if __name__ == "__main__":
    main()
