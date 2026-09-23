"""Scoped journey tables: steps, phase boundaries, phases, loop-backs.

The static definitions -- the office decode, the rung ladder, step compression
-- live in ``sql/grievance_journey.sql``, because they are the same for every
caller and are handed to the department as SQL. These four are different: each
one carries a window over a grievance's whole action history, so it has to be
**materialised over a bounded scope**. Left as a view over all 6.5M action
rows, the window cannot be pushed through and one uncapped run of an earlier
notebook peaked at 55 GB.

So the scope is a parameter and the result is a TABLE. Both notebooks call
these rather than each keeping a copy of the SQL.

Every function takes ``scope``: a SQL predicate over ``g``, the caller's own
base view (itself built on ``grievance_base``). Callers are expected to have
created ``g`` before calling.
"""

from __future__ import annotations

import duckdb

__all__ = [
    "install_steps", "install_phase_bounds", "install_phases", "install_returns",
    "install_route", "install_office_wait", "install_transferred",
    "PHASES", "PHASE_LABEL", "RUNG_NAME", "SHORT_LEVEL", "SHORT_ROLE",
    "UNNAMED_ROLE",
]

# The rung ladder in words. `acting_rung` assigns the number; these name it.
# SHORT_LEVEL is for chart labels only -- tables keep the full wording, and
# the brand font has no arrow glyph, so routes are joined with ">".
RUNG_NAME = {
    1: "State apex", 2: "State department", 3: "District office",
    4: "Sub-district", 5: "Block or field office",
}
SHORT_LEVEL = {
    "State apex": "Apex", "State department": "State dept",
    "District office": "District", "Sub-district": "Sub-district",
    "Block or field office": "Block/field",
}

# Roles, short enough to sit in a route string. The mart's `role_name` is the
# full form and stays the label in any table about one role; these are for a
# sequence, where "District Collector > Block Development Officer > District
# Collector" is a paragraph rather than a route. Abbreviations only where the
# short form is the ordinary word for the office, never an internal code.
SHORT_ROLE = {
    "District Collector": "Collector",
    "Block Development Officer": "BDO",
    "Department Secretary": "Secretary",
    "DRDA Project Director": "DRDA director",
    "District social security officer": "Social security officer",
    "District social welfare officer": "Social welfare officer",
    "District education officer": "Education officer",
    "District medical officer": "Medical officer",
    "Chief Minister's Grievance Cell": "CM Cell",
    "Superintendent of Police": "SP",
    "Police station": "Police station",
    "Sub-Collector": "Sub-Collector",
    "Tahasildar": "Tahasildar",
}

# What a route calls an office whose code the mart recognises well enough to
# place on the rung ladder but not well enough to name. Naming it by its level
# keeps the claim in the route instead of dropping it for want of a label.
UNNAMED_ROLE = {
    1: "Other state apex office", 2: "Other state office",
    3: "Other district office", 4: "Other sub-district office",
    5: "Other block or field office",
}


def install_steps(con: duckdb.DuckDBPyConnection, scope: str = "TRUE") -> None:
    """One row per action, with the grievance's journey summarised alongside.

    ``prev_code`` is what makes "the first time it was routed" answerable: a
    step whose office differs from the step before it is a hand-off, and the
    first such step is the first assignment. ``deepest_rung`` and ``n_steps``
    are partition-wide, which is why this must be materialised per scope.

    Reads ``acting_office_named`` rather than ``acting_rung`` so the role
    travels with the step: a route wants to say who held the claim, not only
    at what level of government they sat.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE steps AS
        SELECT r.ticket_no, r.id, r.action_taken_date, r.action_status, r.rung, r.code,
               r.role_name,
               ROW_NUMBER() OVER w AS step_index,
               LAG(r.rung)  OVER w AS prev_rung,
               LAG(r.code)  OVER w AS prev_code,
               MAX(r.rung)  OVER (PARTITION BY r.ticket_no) AS deepest_rung,
               COUNT(*)     OVER (PARTITION BY r.ticket_no) AS n_steps,
               BOOL_OR(r.rung IS NULL) OVER (PARTITION BY r.ticket_no) AS has_unranked
        FROM acting_office_named r
        JOIN (SELECT ticket_no FROM g WHERE {scope}) y USING (ticket_no)
        WHERE r.ticket_no IS NOT NULL AND r.action_taken_date IS NOT NULL
        WINDOW w AS (PARTITION BY r.ticket_no ORDER BY r.action_taken_date, r.id)
    """)


def install_phase_bounds(con: duckdb.DuckDBPyConnection) -> None:
    """The five dates that bound a grievance's phases.

    Grievances containing an office the rung ladder does not recognise are
    excluded here rather than given a wrong deepest rung; callers count them.
    """
    con.execute("""
        CREATE OR REPLACE TABLE phase_bounds AS
        WITH s AS (SELECT * FROM steps WHERE NOT has_unranked AND rung IS NOT NULL),
        marks AS (
          SELECT ticket_no,
            MIN(action_taken_date) AS first_action,
            MAX(action_taken_date) AS last_action,
            MIN(action_taken_date) FILTER (WHERE rung = deepest_rung) AS reached_deepest,
            -- the first time the grievance passes to a different office
            MIN(action_taken_date) FILTER (
                WHERE prev_code IS NOT NULL AND prev_code IS DISTINCT FROM code
            ) AS first_routed,
            MIN(action_taken_date) FILTER (
                WHERE rung < deepest_rung
                  AND action_taken_date >= (SELECT MIN(x.action_taken_date) FROM s x
                                            WHERE x.ticket_no = s.ticket_no
                                              AND x.rung = x.deepest_rung)
            ) AS returned_up,
            ANY_VALUE(deepest_rung) AS deepest_rung,
            COUNT(*) AS n_steps
          FROM s GROUP BY ticket_no
        )
        SELECT * FROM marks
    """)


PHASES = ["registration", "first_assignment", "field_action", "review", "closure"]
PHASE_LABEL = {
    "registration": "Registration",
    "first_assignment": "First assignment",
    "field_action": "Field action",
    "review": "Review",
    "closure": "Closure",
}


def install_phases(con: duckdb.DuckDBPyConnection, scope: str = "TRUE") -> dict:
    """Split each disposed grievance's elapsed time into five spans that tile.

    First assignment ends at the **first hand-off to a different office**, not
    at the deepest office reached, so a grievance routed CM cell -> Collector
    -> BDO does not count both hops as assignment. Field action begins there
    and absorbs the rest of the descent.

    Review is zero, not missing, for a grievance never sent back up: that is a
    real value and dropping those grievances would change the denominator
    under the reader without saying so.

    Returns ``{"all_rows", "tiling_rows"}``. Spans are clamped forward, so
    where the record's own dates run backwards they cannot sum to the total;
    those grievances are excluded from ``phases_clean`` and counted here
    rather than quietly distorting an average.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE phases AS
        WITH b AS (
          SELECT g.ticket_no, g.year, g.days_to_close,
                 b.deepest_rung, b.returned_up IS NOT NULL AS went_to_review,
                 b.first_routed IS NOT NULL                AS has_handoff,
                 CAST(g.created_on AS DATE)                            AS d0,
                 CAST(b.first_action AS DATE)                          AS d1,
                 CAST(COALESCE(b.first_routed, b.last_action) AS DATE) AS d2,
                 CAST(COALESCE(b.returned_up,  b.last_action) AS DATE) AS d3,
                 CAST(b.last_action AS DATE)                           AS d4,
                 CAST(g.resolved_on AS DATE)                           AS d5
          FROM g JOIN phase_bounds b USING (ticket_no)
          WHERE g.is_disposed AND g.days_to_close IS NOT NULL AND {scope}),
        -- Force the boundaries to run forwards. Nothing in the source
        -- guarantees an action date is later than the one before it, and a
        -- boundary that goes backwards hands one phase a negative span and
        -- another an inflated one.
        m1 AS (SELECT *, GREATEST(d1, d0) AS p1 FROM b),
        m2 AS (SELECT *, GREATEST(d2, p1) AS p2 FROM m1),
        m3 AS (SELECT *, GREATEST(d3, p2) AS p3 FROM m2),
        m4 AS (SELECT *, GREATEST(d4, p3) AS p4 FROM m3),
        m5 AS (SELECT *, GREATEST(d5, p4) AS p5 FROM m4)
        SELECT ticket_no, year, days_to_close, deepest_rung, went_to_review, has_handoff,
               p1 - d0 AS registration,
               p2 - p1 AS first_assignment,
               p3 - p2 AS field_action,
               p4 - p3 AS review,
               p5 - p4 AS closure
        FROM m5
    """)
    total = " + ".join(PHASES)
    con.execute(f"""
        CREATE OR REPLACE TABLE phases_clean AS
        SELECT *, {total} AS span_total FROM phases WHERE {total} = days_to_close
    """)
    bad = con.execute(
        "SELECT COUNT(*) FROM phases_clean WHERE span_total <> days_to_close"
    ).fetchone()[0]
    if bad:
        raise AssertionError(f"{bad} phase rows do not tile their total")
    return con.execute("""
        SELECT (SELECT COUNT(*) FROM phases)       AS all_rows,
               (SELECT COUNT(*) FROM phases_clean) AS tiling_rows
    """).pl().row(0, named=True)


def install_returns(con: duckdb.DuckDBPyConnection) -> None:
    """How many times a grievance arrived at its deepest office.

    More than once is consistent with the first round of action being judged
    insufficient, but the record states no reason, so this counts the pattern
    and claims nothing about the cause.
    """
    con.execute("""
        CREATE OR REPLACE TABLE returns AS
        SELECT ticket_no,
               COUNT(*) FILTER (WHERE rung = deepest_rung
                                AND (prev_rung IS NULL OR prev_rung < deepest_rung))
                   AS arrivals
        FROM steps WHERE NOT has_unranked AND rung IS NOT NULL
        GROUP BY ticket_no
    """)


def install_route(con: duckdb.DuckDBPyConnection) -> None:
    """The sequence of offices a grievance actually passed through.

    Two sequences, collapsed independently, because they answer different
    questions. ``rungs`` collapses consecutive steps at the same *level*, so
    a Collector handing to a district social security officer is one district
    step. ``roles`` collapses consecutive steps in the same *role*, so that
    same hand-off is two steps and the route can say which office held the
    claim. A claim worked twice by one Collector is one step in both.

    Reads `steps`, so the caller's scope already applies.
    """
    unnamed = "CASE " + " ".join(
        "WHEN rung = %d THEN '%s'" % (k, v.replace("'", "''"))
        for k, v in UNNAMED_ROLE.items()) + " END"
    con.execute(f"""
        CREATE OR REPLACE TABLE route AS
        WITH base AS (
          SELECT ticket_no, rung, step_index,
                 COALESCE(role_name, {unnamed}) AS role
          FROM steps WHERE NOT has_unranked AND rung IS NOT NULL),
        by_rung AS (
          SELECT *, LAG(rung) OVER (PARTITION BY ticket_no ORDER BY step_index) AS prev
          FROM base),
        rung_kept AS (SELECT * FROM by_rung WHERE prev IS NULL OR prev <> rung),
        by_role AS (
          SELECT *, LAG(role) OVER (PARTITION BY ticket_no ORDER BY step_index) AS prev
          FROM base),
        role_kept AS (SELECT * FROM by_role WHERE prev IS NULL OR prev <> role),
        r AS (SELECT ticket_no, LIST(rung ORDER BY step_index) AS rungs,
                     COUNT(DISTINCT rung) AS levels_touched
              FROM rung_kept GROUP BY ticket_no),
        o AS (SELECT ticket_no, LIST(role ORDER BY step_index) AS roles,
                     COUNT(DISTINCT role) AS offices_touched
              FROM role_kept GROUP BY ticket_no)
        SELECT * FROM r JOIN o USING (ticket_no)
    """)


def install_office_wait(con: duckdb.DuckDBPyConnection) -> None:
    """How long a grievance waits after each office acts before anything else.

    One row per hand-off, not per grievance: the unit changes here, and callers
    reporting from it must say so. `role_name` and `place` come from the mart,
    so no output carries an internal office code.

    This is **not** a measure of effort. Offices differ in what reaches them,
    and a long wait may be field enquiry, a statutory waiting period, or time
    spent waiting on the citizen.
    """
    con.execute("""
        CREATE OR REPLACE TABLE office_wait AS
        SELECT o.ticket_no, o.role_name, o.place,
               CAST(nxt AS DATE) - CAST(o.action_taken_date AS DATE) AS wait_days
        FROM (SELECT *, LEAD(action_taken_date) OVER
                (PARTITION BY ticket_no ORDER BY action_taken_date, id) AS nxt
              FROM acting_office_named
              WHERE ticket_no IS NOT NULL AND action_taken_date IS NOT NULL) o
        WHERE o.nxt IS NOT NULL AND o.role_name IS NOT NULL AND o.place <> ''
    """)


def install_transferred(con: duckdb.DuckDBPyConnection) -> None:
    """Tickets whose record contains a transfer between offices.

    The `complaints.transfer_status` flag cannot answer this: it is collinear
    with status 'Not Assigned', and none of those are ever disposed, so there is
    no resolution time to compare. The action-history event is used instead.
    """
    con.execute("""
        CREATE OR REPLACE TABLE transferred AS
        SELECT DISTINCT ticket_no FROM action_history
        WHERE action_status = 'Complaint Transfer'
    """)
