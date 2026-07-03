"""Integration tests for the MySQL→local migration, without MySQL/Docker.

`run_migration` reflects the source tables by name, so a SQLite database with the
MySQL table names is a faithful stand-in. These tests exercise the REAL code
path: the async target engine (so the greenlet/async stack actually runs — the
bug a sync-only check missed), the streaming reader, executemany inserts,
on-conflict dedup, tracking-map resolution, and idempotency.
"""

import hashlib
import sqlite3
from pathlib import Path

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


async def test_nul_tracking_id_still_joins_history(tmp_path):
    """A NUL-carrying trackingId on both sides must still join.

    Complaints are NUL-stripped by the schema layer, so the tracking map's
    keys are clean — the history pass must sanitize the RAW key before its
    lookup or the row is silently skipped (Codex review catch on PR #2)."""
    source = tmp_path / "source.db"
    con = sqlite3.connect(source)
    con.execute(
        "CREATE TABLE t_janasunani_etl_pre_data ("
        "ticketNumber TEXT, trackingId TEXT, grievanceSubject TEXT)"
    )
    con.execute(
        "INSERT INTO t_janasunani_etl_pre_data VALUES (?, ?, ?)",
        ("T9", "TR\x009", "nul in tracking id"),
    )
    con.execute(
        "CREATE TABLE t_janasunani_etl_history_pre_data ("
        "trackingId TEXT, action_taken_by TEXT, action_taken_date TEXT, action_status TEXT, "
        "action_taken_remark TEXT, complaint_status_with_authority TEXT)"
    )
    con.execute(
        "INSERT INTO t_janasunani_etl_history_pre_data VALUES (?, ?, ?, ?, ?, ?)",
        ("TR\x009", "Officer N", "2021-05-01 10:00:00", "Noted", "r", "open"),
    )
    con.commit()
    con.close()

    target = tmp_path / "grievance.db"
    await run_migration(f"sqlite:///{source}", f"sqlite+aiosqlite:///{target}")

    out = sqlite3.connect(target)
    row = out.execute(
        "SELECT tracking_id, ticket_no FROM action_history"
    ).fetchone()
    out.close()
    assert row == ("TR9", "T9")  # joined, and stored sanitized


async def test_migration_is_idempotent(urls):
    source_url, target_url, target = urls
    await run_migration(source_url, target_url)
    first = _counts(target)
    await run_migration(source_url, target_url)  # re-run must not duplicate
    second = _counts(target)
    assert first[:2] == second[:2] == (3, 2)


def _build_nullable_history_duplicate_source(path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE t_janasunani_etl_pre_data "
        "(ticketNumber TEXT, trackingId TEXT, grievanceSubject TEXT)"
    )
    con.execute(
        "INSERT INTO t_janasunani_etl_pre_data VALUES (?,?,?)",
        ("T1", "TR1", "nullable duplicate"),
    )
    con.execute(
        "CREATE TABLE t_janasunani_etl_history_pre_data "
        "(trackingId TEXT, action_taken_by TEXT, action_taken_date TEXT, "
        "action_status TEXT, action_taken_remark TEXT, complaint_status_with_authority TEXT)"
    )
    con.executemany(
        "INSERT INTO t_janasunani_etl_history_pre_data VALUES (?,?,?,?,?,?)",
        [
            ("TR1", "Officer A", "2021-01-02 10:00:00", "Forwarded", None, "pending"),
            ("TR1", "Officer A", "2021-01-02 10:00:00", "Forwarded", None, "pending"),
        ],
    )
    con.commit()
    con.close()


async def test_migration_dedupes_nullable_action_history_key(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "grievance.db"
    _build_nullable_history_duplicate_source(source)

    await run_migration(f"sqlite:///{source}", f"sqlite+aiosqlite:///{target}")
    first = _counts(target)
    await run_migration(f"sqlite:///{source}", f"sqlite+aiosqlite:///{target}")
    second = _counts(target)

    assert first[:2] == second[:2] == (1, 1)


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


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _build_ambiguous_source(path) -> None:
    """Source with an ambiguous trackingId (TRX -> A and B) and duplicate history
    rows differing only by date — the cases that made the load non-deterministic."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE t_janasunani_etl_pre_data "
        "(ticketNumber TEXT, trackingId TEXT, grievanceSubject TEXT)"
    )
    con.executemany(
        "INSERT INTO t_janasunani_etl_pre_data VALUES (?,?,?)",
        [("B", "TRX", "b"), ("A", "TRX", "a"), ("C", "TRC", "c")],
    )
    con.execute(
        "CREATE TABLE t_janasunani_etl_history_pre_data "
        "(trackingId TEXT, action_taken_by TEXT, action_taken_date TEXT, "
        "action_status TEXT, action_taken_remark TEXT, complaint_status_with_authority TEXT)"
    )
    con.executemany(
        "INSERT INTO t_janasunani_etl_history_pre_data VALUES (?,?,?,?,?,?)",
        [
            ("TRX", "O", "2021-01-02 00:00:00", "S", "r", "p"),
            ("TRX", "O", "2021-01-01 00:00:00", "S", "r", "p"),  # same key, earlier date
            ("TRC", "O2", "2021-02-01 00:00:00", "S2", "r2", "p2"),
        ],
    )
    con.commit()
    con.close()


async def test_migration_is_byte_deterministic(tmp_path):
    """Two runs from the same source produce byte-identical OLTP DBs — so DVC sees
    the materialize input as unchanged (no spurious re-runs)."""
    src = tmp_path / "source.db"
    _build_ambiguous_source(src)
    t1, t2 = tmp_path / "o1.db", tmp_path / "o2.db"
    await run_migration(f"sqlite:///{src}", f"sqlite+aiosqlite:///{t1}")
    await run_migration(f"sqlite:///{src}", f"sqlite+aiosqlite:///{t2}")

    assert _sha256(t1) == _sha256(t2), "OLTP DB is not byte-reproducible"

    con = sqlite3.connect(t1)
    try:
        # ambiguous TRX resolved deterministically to min(ticket_no) = 'A'
        ah = con.execute(
            "SELECT ticket_no FROM action_history WHERE ticket_no='A'"
        ).fetchall()
    finally:
        con.close()
    # the two TRX rows share a natural key -> deduped to one; the byte-identical
    # assertion above proves which row is kept is reproducible.
    assert len(ah) == 1
