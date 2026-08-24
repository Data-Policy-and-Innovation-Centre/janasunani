"""Aggregate audit of assignment timing against complaint-transfer history.

The complaints lake is a current-state snapshot: it contains one department,
one ``assigned_on`` timestamp and one ``vchAllEscUser`` chain per ticket.  The
action-history lake records events, but not old and new values of those routing
fields.  This stage therefore answers the timing questions the two tables can
answer and says explicitly what they cannot establish.  It never emits a
ticket number or any other row-level value.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from . import paths

TRANSFER_ACTION = "Complaint Transfer"
ROUTE_SNAPSHOT_COLUMNS = frozenset(
    {"dept_id", "departmentid", "all_esc_user", "vchallescuser"}
)


def _register_parquet(
    con: duckdb.DuckDBPyConnection,
    *,
    complaints_path: Path,
    action_history_path: Path,
) -> None:
    con.from_parquet(str(complaints_path)).create_view(
        "assignment_audit_complaints", replace=True
    )
    con.from_parquet(str(action_history_path)).create_view(
        "assignment_audit_history", replace=True
    )


def assignment_provenance_audit(
    complaints_path: Path = paths.COMPLAINTS_PARQUET,
    action_history_path: Path = paths.ACTION_HISTORY_PARQUET,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
) -> dict:
    """Return a PII-free aggregate assignment/transfer provenance report.

    ``pre_assignment_only`` means every explicit complaint-transfer event for
    the ticket precedes its recorded assignment.  ``at_or_after_assignment``
    is the conservative bucket: equal timestamps do not establish event order.
    Neither bucket proves whether the complaint row was ever rewritten, because
    action history does not store a department or full-chain snapshot.
    """

    con = con or duckdb.connect()
    _register_parquet(
        con,
        complaints_path=complaints_path,
        action_history_path=action_history_path,
    )

    snapshot = con.execute(
        """
        SELECT count(*) AS complaint_rows,
               count(DISTINCT ticket_no) AS complaint_tickets,
               min(created_on) AS first_created_on,
               max(created_on) AS last_created_on,
               max(coalesce(resolved_on, last_updated_on, created_on)) AS latest_observed_on
        FROM assignment_audit_complaints
        """
    ).fetchone()
    history = con.execute(
        """
        SELECT count(*) AS action_events,
               count(DISTINCT ticket_no) AS action_tickets
        FROM assignment_audit_history
        """
    ).fetchone()

    transfer = con.execute(
        """
        WITH transfers AS (
            SELECT ticket_no,
                   count(*) AS transfer_events,
                   min(action_taken_date) AS first_transfer,
                   max(action_taken_date) AS last_transfer
            FROM assignment_audit_history
            WHERE action_status = ?
            GROUP BY ticket_no
        ), joined AS (
            SELECT c.*,
                   coalesce(t.transfer_events, 0) AS transfer_events,
                   t.first_transfer,
                   t.last_transfer,
                   lower(trim(coalesce(c.transfer_status, ''))) = 'yes' AS flag_yes,
                   CASE
                       WHEN t.ticket_no IS NULL THEN 'none'
                       WHEN c.assigned_on IS NULL THEN 'unassigned'
                       WHEN t.last_transfer < c.assigned_on THEN 'pre_assignment_only'
                       ELSE 'at_or_after_assignment'
                   END AS timing
            FROM assignment_audit_complaints c
            LEFT JOIN transfers t USING (ticket_no)
        )
        SELECT count(*) FILTER (WHERE transfer_events > 0) AS explicit_transfer_tickets,
               sum(transfer_events) AS explicit_transfer_events,
               count(*) FILTER (WHERE timing = 'pre_assignment_only') AS pre_assignment_only,
               count(*) FILTER (
                   WHERE timing = 'pre_assignment_only'
                     AND CAST(first_transfer AS DATE) = CAST(created_on AS DATE)
               ) AS pre_assignment_on_creation_day,
               count(*) FILTER (WHERE timing = 'at_or_after_assignment') AS at_or_after_assignment,
               count(*) FILTER (WHERE timing = 'unassigned') AS unassigned_after_transfer,
               count(*) FILTER (WHERE flag_yes) AS flag_yes,
               count(*) FILTER (WHERE transfer_events > 0 AND NOT flag_yes) AS history_but_flag_no,
               count(*) FILTER (WHERE flag_yes AND transfer_events = 0) AS flag_yes_without_history,
               count(*) FILTER (WHERE flag_yes AND status <> 'Not Assigned') AS flag_yes_other_status,
               count(*) FILTER (WHERE flag_yes AND assigned_on IS NOT NULL) AS flag_yes_with_assignment,
               count(*) FILTER (
                   WHERE flag_yes AND all_esc_user IS NOT NULL AND trim(all_esc_user) <> ''
               ) AS flag_yes_with_chain,
               count(*) FILTER (
                   WHERE flag_yes AND pending_with_id IS NOT NULL
                     AND pending_with_id NOT IN (-1, 0)
               ) AS flag_yes_with_pending_holder,
               count(*) FILTER (WHERE flag_yes AND resolved_on IS NOT NULL) AS flag_yes_resolved
        FROM joined
        """,
        [TRANSFER_ACTION],
    ).fetchone()

    history_columns = {
        str(row[0]).lower()
        for row in con.execute("DESCRIBE assignment_audit_history").fetchall()
    }
    route_snapshot_columns = sorted(history_columns & ROUTE_SNAPSHOT_COLUMNS)
    one_row_per_ticket = snapshot[0] == snapshot[1]

    return {
        "sources": {
            "complaints": str(complaints_path),
            "action_history": str(action_history_path),
        },
        "snapshot": {
            "complaint_rows": int(snapshot[0]),
            "complaint_tickets": int(snapshot[1]),
            "one_complaint_row_per_ticket": bool(one_row_per_ticket),
            "action_events": int(history[0]),
            "action_tickets": int(history[1]),
            "first_created_on": snapshot[2].isoformat() if snapshot[2] else None,
            "last_created_on": snapshot[3].isoformat() if snapshot[3] else None,
            "latest_observed_on": snapshot[4].isoformat() if snapshot[4] else None,
        },
        "explicit_transfer_history": {
            "tickets": int(transfer[0]),
            "events": int(transfer[1]),
            "pre_assignment_only": int(transfer[2]),
            "pre_assignment_on_creation_day": int(transfer[3]),
            "at_or_after_assignment": int(transfer[4]),
            "unassigned": int(transfer[5]),
        },
        "current_transfer_state": {
            "flag_yes": int(transfer[6]),
            "history_but_flag_no": int(transfer[7]),
            "flag_yes_without_history": int(transfer[8]),
            "flag_yes_other_status": int(transfer[9]),
            "flag_yes_with_assignment": int(transfer[10]),
            "flag_yes_with_chain": int(transfer[11]),
            "flag_yes_with_pending_holder": int(transfer[12]),
            "flag_yes_resolved": int(transfer[13]),
        },
        "assignment_field_provenance": {
            "status": "not_identified_from_current_snapshot",
            "action_history_route_snapshot_columns": route_snapshot_columns,
            "reason": (
                "The complaints extract has one current row per ticket and action history "
                "does not store department or full-chain snapshots. The tables can order "
                "transfer events around assigned_on, but cannot reveal an earlier value of "
                "dept_id or vchAllEscUser."
            ),
        },
    }


def main() -> int:
    report = assignment_provenance_audit()
    destination = paths.out("assignment_provenance.json")
    with destination.open("w") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
