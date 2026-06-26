"""Integration tests for the MySQL→local migration, without MySQL/Docker.

`run_migration` reflects the source tables by name, so a SQLite database with the
MySQL table names is a faithful stand-in. These tests exercise the REAL code
path: the async target engine (so the greenlet/async stack actually runs — the
bug a sync-only check missed), the streaming reader, executemany inserts,
on-conflict dedup, tracking-map resolution, and idempotency.
"""

import sqlite3

import pytest

from janasunani.migration.from_mysql import run_migration

COMPLAINTS = [
    # ticketNumber, trackingId, grievanceSubject, districtName, govtTicket, CreatedOn, StatusName, category, officeNAme
    ("T1", "TR1", "need caste cert", "Cuttack", "Yes", "2021-01-01 10:00:00", "Disposed", "Certificates", "Collector"),
    ("T2", "TR2", "water issue", "Puri", "No", "2021-02-01 09:00:00", "Disposed", "Water Supply", "Collector"),
    ("T3", None, "no tracking id", "Khordha", "No", "2021-03-01 08:00:00", "Pending", "Other", "Collector"),
]

HISTORY = [
    # trackingId, action_taken_by, action_taken_date, action_status, action_taken_remark, complaint_status_with_authority
    ("TR1", "Officer A", "2021-01-02 10:00:00", "Forwarded", "r1", "pending"),
    ("TR1", "Officer A", "2021-01-02 10:00:00", "Forwarded", "r1", "pending"),  # exact dup -> collapsed
    ("TR2", "Officer B", "2021-02-02 09:00:00", "Resolved", "done", "closed"),
    ("UNKNOWN", "Officer C", "2021-02-03 09:00:00", "Noted", "x", "open"),  # no complaint -> skipped
]


def _build_source(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE t_janasunani_etl_pre_data ("
        "ticketNumber TEXT, trackingId TEXT, grievanceSubject TEXT, districtName TEXT, "
        "govtTicket TEXT, CreatedOn TEXT, StatusName TEXT, category TEXT, officeNAme TEXT)"
    )
    con.executemany(
        "INSERT INTO t_janasunani_etl_pre_data VALUES (?,?,?,?,?,?,?,?,?)", COMPLAINTS
    )
    con.execute(
        "CREATE TABLE t_janasunani_etl_history_pre_data ("
        "trackingId TEXT, action_taken_by TEXT, action_taken_date TEXT, action_status TEXT, "
        "action_taken_remark TEXT, complaint_status_with_authority TEXT)"
    )
    con.executemany(
        "INSERT INTO t_janasunani_etl_history_pre_data VALUES (?,?,?,?,?,?)", HISTORY
    )
    con.commit()
    con.close()


@pytest.fixture
def urls(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "grievance.db"
    _build_source(source)
    return (
        f"sqlite:///{source}",
        f"sqlite+aiosqlite:///{target}",
        target,
    )


def _counts(target):
    con = sqlite3.connect(target)
    try:
        complaints = con.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
        history = con.execute("SELECT COUNT(*) FROM action_history").fetchone()[0]
        rows = con.execute(
            "SELECT a.tracking_id, a.ticket_no FROM action_history a"
        ).fetchall()
        orphans = con.execute(
            "SELECT COUNT(*) FROM action_history a "
            "WHERE NOT EXISTS (SELECT 1 FROM complaints c WHERE c.ticket_no = a.ticket_no)"
        ).fetchone()[0]
        return complaints, history, dict(rows), orphans
    finally:
        con.close()


async def test_migration_counts_skip_dedup_and_join(urls):
    source_url, target_url, target = urls
    await run_migration(source_url, target_url)

    complaints, history, by_tracking, orphans = _counts(target)
    assert complaints == 3
    # 4 source history rows -> 1 dup collapsed, 1 unmatched skipped -> 2 inserted
    assert history == 2
    # every action_history row resolved to the right complaint via the tracking map
    assert by_tracking == {"TR1": "T1", "TR2": "T2"}
    assert orphans == 0


async def test_migration_is_idempotent(urls):
    source_url, target_url, target = urls
    await run_migration(source_url, target_url)
    first = _counts(target)
    await run_migration(source_url, target_url)  # re-run must not duplicate
    second = _counts(target)
    assert first[:2] == second[:2] == (3, 2)


async def test_govt_ticket_and_datetime_persisted(urls):
    source_url, target_url, target = urls
    await run_migration(source_url, target_url)
    con = sqlite3.connect(target)
    try:
        gt = con.execute("SELECT govt_ticket FROM complaints WHERE ticket_no='T1'").fetchone()[0]
        created = con.execute("SELECT created_on FROM complaints WHERE ticket_no='T1'").fetchone()[0]
    finally:
        con.close()
    assert gt == 1  # 'Yes' -> True -> 1
    assert str(created).startswith("2021-01-01 10:00:00")
