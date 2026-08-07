"""Tests for the action-type lookup (#75).

Every assertion runs against the real code paths (Python + SQL mart) over a
hand-built fixture lake, never against the live lake. Fixtures are arranged so
each taxonomy class has an independently countable answer and so per-status
vs corpus-wide lookup is exercised rather than assumed.

Also asserts the privacy and consistency invariants the issue requires:
free-text tail stays unclassified, admin noise is isolated, and the six-string
closure ladder is a subset of this lookup.
"""

import re
from datetime import datetime

import polars as pl
import pytest

from janasunani.analytics import marts
from janasunani.analytics import action_type as at

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_ACTIONS = [
    # (id, ticket, date, taken_by, status, remark)
    (1, "T1", datetime(2024, 1, 2), "Officer A", "Disposed", "The grievance has been disposed."),
    (2, "T2", datetime(2024, 1, 3), "Officer A", "Resolved", "the grievance has been resolved"),
    (3, "T3", datetime(2024, 1, 4), "Officer A", "Disposed", "The grievance has been disposed with appropriate action."),
    (4, "T4", datetime(2024, 1, 5), "Officer B", "Disposed", "The grievance has been disposed & beneficiary benefited."),
    (5, "T5", datetime(2024, 1, 6), "Officer A", "Forwarded", "Forwarded to concerned officer for necessary action"),
    (6, "T6", datetime(2024, 1, 7), "Officer B", "ATR Received", "ATR received from concerned officer"),
    (7, "T7", datetime(2024, 1, 8), "Officer A", "Disposed", "Complaint details inadequate"),
    (8, "T8", datetime(2024, 1, 9), "Officer A", "Disposed", "Duplicate copy of grievance"),
    (9, "T9", datetime(2024, 1, 10), "Officer A", "Pending", "Case already taken up earlier"),
    (10, "T10", datetime(2024, 1, 11), "Officer A", "Disposed", "Not within the purview of this grievance cell"),
    (11, "T11", datetime(2024, 1, 12), "Officer A", "Reopened", "Grievance reopened as per direction"),
    (12, "T12", datetime(2024, 1, 13), "Officer A", "Forwarded", "Escalated to higher authority"),
    (13, "T13", datetime(2024, 1, 14), "Officer A", "Pending", "."),
    (14, "T14", datetime(2024, 1, 15), "Officer A", "Pending", "ok"),
    (15, "T15", datetime(2024, 1, 16), "Officer A", "Forwarded", "ok"),  # per-status: ok + Forwarded -> admin_noise
    (16, "T16", datetime(2024, 1, 17), "Officer A", "ATR Received", "ok"),  # per-status override -> reported_back
    (17, "T17", datetime(2024, 1, 18), "Officer A", "Pending", "pmay"),
    (18, "T18", datetime(2024, 1, 19), "Officer A", "Pending", "This is free text about a hand pump that is broken in our village, please help"),
    (19, "T19", datetime(2024, 1, 20), "Officer A", "Disposed", "The grievance has been disposed."),
    (20, "T20", datetime(2024, 1, 21), "Officer A", "Forwarded", "The grievance has been disposed."),
    (21, "T21", datetime(2024, 1, 22), "Officer A", "Pending", "   The  grievance   has been disposed...   "),
    (22, "T22", datetime(2024, 1, 23), "Officer A", "Pending", "Not within purview of this grievance cell"),
]

_COMPLAINTS = [(f"T{i}", datetime(2024, 1, 1), None, None, "Water", "Puri", "RWSS") for i in range(1, 23)]


def _write_lake(path):
    path.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        _COMPLAINTS,
        schema=[("ticket_no", pl.Utf8), ("created_on", pl.Datetime), ("resolved_on", pl.Datetime),
                ("benefitted", pl.Utf8), ("category", pl.Utf8), ("district", pl.Utf8), ("dept", pl.Utf8)],
        orient="row",
    ).write_parquet(path / "complaints.parquet")
    pl.DataFrame(
        _ACTIONS,
        schema=[("id", pl.Int64), ("ticket_no", pl.Utf8), ("action_taken_date", pl.Datetime),
                ("action_taken_by", pl.Utf8), ("action_status", pl.Utf8), ("action_taken_remark", pl.Utf8)],
        orient="row",
    ).write_parquet(path / "action_history.parquet")
    return path


@pytest.fixture
def lake(tmp_path):
    return _write_lake(tmp_path)


# ---------------------------------------------------------------------------
# Python lookup
# ---------------------------------------------------------------------------

def test_taxonomy_has_seven_plus_admin_noise():
    assert set(at.CLASSES) == {
        "forwarded_delegated", "reported_back", "disposed_no_claim",
        "disposed_with_action", "benefit_delivered", "discarded_with_reason",
        "reopened_escalated", "admin_noise",
    }


def test_normalize_remark_mirrors_sql():
    assert at.normalize_remark("  The  grievance has been disposed...  ") == "the grievance has been disposed"
    assert at.normalize_remark("OK.") == "ok"
    assert at.normalize_remark(".") == ""
    assert at.normalize_remark(None) is None
    assert at.normalize_remark("   ") == ""


def test_classify_exact_match_only():
    assert at.classify("The grievance has been disposed.") == "disposed_no_claim"
    assert at.classify("the grievance has been disposed") == "disposed_no_claim"
    # whitespace/case/dots collapsed -> still matches
    assert at.classify("  The  grievance   has been disposed... ") == "disposed_no_claim"
    # near-miss is not a match
    assert at.classify("The grievance has been disposed today") is None
    assert at.classify("This is free text about a hand pump") is None


def test_classify_per_status_not_corpus_wide():
    # Same remark under different statuses — corpus fallback is same class,
    # but per-status table proves the mechanism.
    assert at.classify("The grievance has been disposed.", "Disposed") == "disposed_no_claim"
    assert at.classify("The grievance has been disposed.", "Forwarded") == "disposed_no_claim"
    # per-status divergence: "ok" under ATR is reported_back, not admin_noise
    assert at.classify("ok", "Pending") == "admin_noise"
    assert at.classify("ok", "ATR Received") == "reported_back"
    assert at.classify("noted", "Forwarded") == "forwarded_delegated"
    assert at.classify("noted", "Pending") == "admin_noise"


def test_discarded_reasons_all_map():
    for remark in [
        "Complaint details inadequate",
        "Required documents not attached",
        "Case already taken up earlier",
        "No specific grievance",
        "Duplicate copy of grievance",
        "Needs policy decision",
        "Not within the purview of this grievance cell",
        "Address not given",
    ]:
        assert at.classify(remark) == "discarded_with_reason"


def test_admin_noise_bucket():
    for remark in [".", "ok", "pmay", "mgnrega", "bsky", "-", "na"]:
        assert at.classify(remark) == "admin_noise"


def test_benefit_and_with_action():
    assert at.classify("The grievance has been disposed with appropriate action.") == "disposed_with_action"
    assert at.classify("The grievance has been disposed & beneficiary benefited.") == "benefit_delivered"


def test_closure_ladder_subset():
    # Every closure ladder template must be in the action-type lookup
    # with the mapped class.
    assert at.classify("the grievance has been disposed") == at.DISPOSED_NO_CLAIM
    assert at.classify("the grievance has been resolved") == at.DISPOSED_NO_CLAIM
    assert at.classify("the grievance has been disposed with appropriate action") == at.DISPOSED_WITH_ACTION
    assert at.classify("the grievance has been resolved with appropriate action") == at.DISPOSED_WITH_ACTION
    assert at.classify("the grievance has been disposed & beneficiary benefited") == at.BENEFIT_DELIVERED
    assert at.classify("the grievance has been resolved & beneficiary benefited") == at.BENEFIT_DELIVERED


def test_odia_template_present():
    assert at.classify("ଅଭିଯୋଗଟି ସମାଧାନ ହୋଇଛି") is not None


# ---------------------------------------------------------------------------
# SQL mart
# ---------------------------------------------------------------------------

def test_mart_sql_is_read_verbatim(lake):
    assert marts.mart_sql("action_type") == marts.mart_path("action_type").read_text()
    assert "CREATE OR REPLACE VIEW action_type_lookup" in marts.mart_sql("action_type")
    assert "CREATE OR REPLACE VIEW action_history_typed" in marts.mart_sql("action_type")


def test_open_lake_installs_action_type(lake):
    con = marts.open_lake("action_type", lake_dir=lake)
    try:
        n = con.execute("SELECT count(*) FROM action_type_lookup").fetchone()[0]
        assert n >= 60  # Sprint 2 cut: ~60 + admin noise + per-status rows
        m = con.execute("SELECT count(*) FROM action_history_typed").fetchone()[0]
        assert m == len(_ACTIONS)
    finally:
        con.close()


def test_python_and_sql_agree_row_for_row(lake):
    con = marts.open_lake("action_type", lake_dir=lake)
    try:
        rows = con.execute(
            "SELECT action_taken_remark, action_status, action_type, is_known_template "
            "FROM action_history_typed ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    for remark, status, sql_type, is_known in rows:
        py_type = at.classify(remark, status)
        assert sql_type == py_type, f"mismatch for {remark!r} / {status!r}: sql={sql_type!r} py={py_type!r}"
        assert bool(is_known) == (py_type is not None)


def test_action_type_summary_counts(lake):
    con = marts.open_lake("action_type", lake_dir=lake)
    try:
        summary = {r[0]: r[1] for r in con.execute("SELECT action_type, action_rows FROM action_type_summary").fetchall()}
    finally:
        con.close()
    # At least one of each major class should appear in our fixture
    for cls in ["disposed_no_claim", "disposed_with_action", "benefit_delivered",
                "forwarded_delegated", "reported_back", "discarded_with_reason",
                "reopened_escalated", "admin_noise"]:
        assert cls in summary, f"missing {cls} in summary: {summary}"
    assert "unclassified_tail" in summary  # the free-text pump row


def test_no_grievance_column_read(tmp_path):
    # The mart must never read complaints.grievance, so the lake can carry
    # that PII column and the view still succeeds without exposing it.
    path = tmp_path
    path.mkdir(parents=True, exist_ok=True)
    # Write lake with extra grievance column
    pl.DataFrame(
        [(*row, "My hand pump is broken, please help — 9876543210") for row in _COMPLAINTS],
        schema=[("ticket_no", pl.Utf8), ("created_on", pl.Datetime), ("resolved_on", pl.Datetime),
                ("benefitted", pl.Utf8), ("category", pl.Utf8), ("district", pl.Utf8),
                ("dept", pl.Utf8), ("grievance", pl.Utf8)],
        orient="row",
    ).write_parquet(path / "complaints.parquet")
    pl.DataFrame(
        _ACTIONS,
        schema=[("id", pl.Int64), ("ticket_no", pl.Utf8), ("action_taken_date", pl.Datetime),
                ("action_taken_by", pl.Utf8), ("action_status", pl.Utf8), ("action_taken_remark", pl.Utf8)],
        orient="row",
    ).write_parquet(path / "action_history.parquet")
    con = marts.open_lake("action_type", lake_dir=path)
    try:
        rows = con.execute("SELECT action_type FROM action_history_typed").pl()
        assert "grievance" not in [c.lower() for c in rows.columns]
        # No citizen text leaks into typed output
        for v in rows["action_type"].to_list():
            assert "hand pump" not in str(v)
    finally:
        con.close()
    # Static: SQL must not name the grievance column (strip comments/literals first)
    sql = re.sub(r"--[^\n]*", "", marts.mart_sql("action_type"))
    sql = re.sub(r"'[^']*'", "''", sql)
    assert "grievance" not in sql.lower()


def test_unclassified_diagnostic_gated(tmp_path):
    # Diagnostic only emits >=1000, so our small fixture yields zero rows.
    # Verify the threshold exists in the SQL.
    assert "HAVING COUNT(*) >= 1000" in marts.mart_sql("action_type")
    lake = _write_lake(tmp_path)
    con = marts.open_lake("action_type", lake_dir=lake)
    try:
        n = con.execute("SELECT count(*) FROM action_type_unclassified_templates").fetchone()[0]
        assert n == 0
    finally:
        con.close()


def test_per_status_breakdown_exists(lake):
    con = marts.open_lake("action_type", lake_dir=lake)
    try:
        rows = con.execute("SELECT count(*) FROM action_type_by_status").fetchone()[0]
        assert rows >= 5
    finally:
        con.close()
