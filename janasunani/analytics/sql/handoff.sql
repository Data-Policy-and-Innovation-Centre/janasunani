-- Elapsed time between recorded handling steps (phase 1: descriptive only).
--
-- What it answers: once an officer records an action on a ticket, how long is
-- it until the NEXT recorded action on that same ticket? This is the gap
-- between two *recorded events*, not a measurement of idle time and not a
-- measurement of routing quality.
--
-- THIS IS NOT THE WITHDRAWN ROUTING-SAVINGS CLAIM. That claim ("better
-- routing saves 11-23 days per case") was withdrawn on 23 Aug (#879c24c,
-- #365e3b4) after failing temporal replication; its archived artifacts live
-- under docs/experiments/superseded/ and must not be quoted. That work was
-- causal and counterfactual, and it read the de jure route
-- (dept_id + vchAllEscUser). This mart reads action_history only -- the
-- record of what actually happened, not what was assigned -- and computes no
-- counterfactual. NEVER use "delay", "time lost" or "saving" language for the
-- numbers this mart produces. Say "elapsed time between recorded steps".
--
-- Caveats that must travel with every quoted number from this mart:
--   1. De facto handling, not the routing decision. This measures the
--      realised event stream, not the assigned route.
--   2. Not causal, not a saving. No counterfactual is computed.
--   3. Dedup collapse (see §6 below): gap COUNTS are a LOWER bound (some
--      genuinely distinct hand-offs collapsed into one recorded row), gap
--      DURATIONS are an UPPER bound (a collapsed row's gap spans what were
--      possibly two or more shorter real gaps).
--   4. Undated rows are dropped; `handoff_coverage_summary.dropped_undated_rows`
--      reports how many.
--   5. Inverted timestamps are bucketed `invalid`, not clamped or silently
--      reordered; `handoff_coverage_summary.invalid_order_intervals` reports
--      how many.
--   6. Hops cannot be labelled by role: `action_taken_by` is free text
--      (models.py) and is never joined to a user-role table anywhere in this
--      repo.
--   7. Chain labels are unusable as strata: `dept_id` / `vchAllEscUser`
--      provenance is `not_identified_from_current_snapshot` (the routing
--      work's own finding). This mart does not read `complaints` at all, so
--      it cannot stratify by department, and does not try to.
--   8. A gap is not idle time. It includes field enquiry, statutory waiting
--      periods, and citizen response time. It is elapsed time between
--      recorded steps, nothing more specific than that.
--
-- Phase 1 scope only: a descriptive distribution over COMPLETED gaps (both
-- endpoints observed, so no censoring correction is needed for them). No
-- IPCW, no RMST, no survival correction -- that machinery (censoring.py)
-- lives on an unmerged worktree and only matters for a per-ticket *total*
-- elapsed-time estimate, which this phase does not produce. The per-ticket
-- reducer (§5) is shaped so a later phase can plug IPCW in without a rewrite,
-- but it is not wired here.
--
-- Depends on `action_history_typed` from action_type.sql (#75) -- install
-- action_type.sql before this file (janasunani.analytics.marts.install
-- accepts multiple mart names in order: install(con, "action_type", "handoff")).
-- Portable across DuckDB (the lake) and PostgreSQL (their DB), same as the
-- marts it builds on. Reads action_history only, via action_history_typed;
-- never touches complaints or complaints.grievance, so it needs no redaction
-- pass and no slice decision.
--
-- Aggregates only for the reportable/handover views: no free-text emission,
-- following the rule at action_type.sql:24-27 and closure.sql:26-30.
-- `handoff_intervals`, `handoff_ordered` and `handoff_ticket_summary` are
-- per-ticket intermediates (ticket_no, no remark text) -- like
-- closure_closing_action / closure_rung, they are not part of the handover
-- and are not written to outputs/findings/ by the Python wrapper. They exist
-- so the reportable aggregates below can be built, and so a later IPCW layer
-- has a per-ticket table to build on without re-deriving the interval logic.

-- ---------------------------------------------------------------------------
-- 1. One row per action_history row that has a usable date, with its
--    neighbours in ticket order.
--
-- Ordering convention -- `ORDER BY action_taken_date, id` -- matches
-- closure.sql:79-81 exactly (date primary, id as the deterministic tiebreak
-- for same-date rows). Undated rows are dropped here (`WHERE
-- action_taken_date IS NOT NULL`), never given a fallback insertion-order
-- position, for the same reason closure.sql:85-89 drops them: an undated
-- action cannot establish its place in a verified sequence, and guessing one
-- from `id` would misrepresent insertion order as chronology.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_ordered AS
SELECT
    t.id,
    t.ticket_no,
    t.action_taken_date,
    t.action_type,
    t.is_known_template,
    LAG(t.action_taken_date) OVER w AS prev_action_taken_date,
    LAG(t.action_type)       OVER w AS from_action_type,
    LAG(t.id)                OVER w AS prev_id,
    LEAD(t.action_taken_date) OVER w AS next_action_taken_date,
    ROW_NUMBER()              OVER w AS step_index
FROM action_history_typed t
WHERE t.action_taken_date IS NOT NULL
WINDOW w AS (PARTITION BY t.ticket_no ORDER BY t.action_taken_date, t.id);

-- ---------------------------------------------------------------------------
-- 2. Count of rows dropped for a NULL date. Reported, never silently absorbed.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_dropped_undated AS
SELECT COUNT(*) AS dropped_undated_rows
FROM action_history_typed
WHERE action_taken_date IS NULL;

-- ---------------------------------------------------------------------------
-- 3. Closed intervals: one row per consecutive pair of recorded events on the
--    same ticket. A ticket's first dated row opens no interval (no
--    predecessor) and contributes no row here -- a single-event ticket has no
--    gap and correctly produces zero rows in this view.
--
-- `is_trailing_open` marks the interval whose `to` event is the ticket's last
-- recorded event (`next_action_taken_date IS NULL`): after it, there is an
-- open interval of unknown duration this mart does not estimate (phase 2).
--
-- `is_invalid_order`: nothing enforces that `action_taken_date` increases
-- with `id` (nothing enforces ordering on action dates, same as closure.sql's
-- `resolved_on` / `created_on`). Sorting by `(action_taken_date, id)` makes
-- `gap_days` itself non-negative by construction, but it can still silently
-- reorder around a row whose claimed date contradicts when it was actually
-- recorded: if a row's `id` is LOWER than the row immediately before it in
-- date order, that row was logged before an event the dates say it followed.
-- That is the actual "nothing enforces ordering" failure mode here, and it is
-- bucketed `invalid` (§4) rather than trusted as a real elapsed-time
-- observation or silently clamped/reordered away.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_intervals AS
SELECT
    ticket_no,
    step_index,
    from_action_type,
    action_type AS to_action_type,
    is_known_template AS to_is_known_template,
    CAST(action_taken_date AS DATE) - CAST(prev_action_taken_date AS DATE) AS gap_days,
    next_action_taken_date IS NULL AS is_trailing_open,
    (prev_id IS NOT NULL AND id < prev_id) AS is_invalid_order
FROM handoff_ordered
WHERE prev_action_taken_date IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 4. Per-ticket year proxy. This mart reads action_history only (#not the
--    de jure route), so there is no `complaints.created_on` here -- the year
--    of a ticket's earliest RECORDED action stands in for it. State this
--    whenever "ticket creation year" is quoted: it is the year of the first
--    action_history row, not the portal's created_on.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_ticket_year AS
SELECT
    ticket_no,
    EXTRACT(YEAR FROM MIN(action_taken_date)) AS ticket_creation_year_proxy
FROM action_history_typed
WHERE action_taken_date IS NOT NULL
GROUP BY ticket_no;

-- ---------------------------------------------------------------------------
-- 5. Per-ticket reducer -- scalars a later IPCW layer can consume without
--    re-deriving the interval logic. NOT wired to any survival correction
--    here; phase 2 is explicitly out of scope.
--
-- `total_gap_days` sums CLOSED intervals only (both endpoints observed). It
-- deliberately excludes the open tail after the last recorded event, whose
-- duration is unknown -- estimating that needs IPCW/RMST (phase 2), not a
-- plain sum. `largest_gap_share_pct` is therefore a share of the OBSERVED
-- elapsed span, not of the ticket's true end-to-end time.
-- `forwarded_delegated_open_days` is the total time sitting in intervals that
-- OPENED on a forwarded/delegated action -- how long the record shows the
-- case sitting after being handed off, not a claim about what happened during
-- that time (caveat 8: it is not idle time).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_ticket_summary AS
WITH valid AS (
    SELECT * FROM handoff_intervals WHERE NOT is_invalid_order
),
per_ticket AS (
    SELECT
        ticket_no,
        COUNT(*) AS n_closed_intervals,
        CAST(SUM(gap_days) AS BIGINT) AS total_gap_days,
        CAST(MAX(gap_days) AS BIGINT) AS max_gap_days,
        CAST(
            SUM(gap_days) FILTER (WHERE from_action_type = 'forwarded_delegated')
            AS BIGINT
        ) AS forwarded_delegated_open_days,
        BOOL_OR(is_trailing_open) AS has_trailing_open_interval
    FROM valid
    GROUP BY ticket_no
)
SELECT
    ticket_no,
    n_closed_intervals,
    total_gap_days,
    max_gap_days,
    COALESCE(forwarded_delegated_open_days, 0) AS forwarded_delegated_open_days,
    CASE
        WHEN total_gap_days > 0 THEN 100.0 * max_gap_days / total_gap_days
        ELSE NULL
    END AS largest_gap_share_pct,
    has_trailing_open_interval
FROM per_ticket;

-- ---------------------------------------------------------------------------
-- 6. Dedup-sensitivity check.
--
-- `action_history_uniq` (janasunani/db/models.py:199-214, Alembic
-- 09f36c201e97) is a hard UNIQUE INDEX on
-- (ticket_no, action_taken_by, action_status, md5(remark),
-- complaint_status_with_authority), enforced at insert with
-- ON CONFLICT DO NOTHING -- it DELIBERATELY excludes action_taken_date. Two
-- genuinely distinct hand-offs by the same officer, same status, same
-- templated remark collapse into whichever row was inserted first; the
-- second insert is silently dropped and never reaches the OLTP table, the
-- lake, or this mart. There is no pre-dedup artifact left to recover: the
-- collapsed rows are gone, not merely hidden, so "recompute with the
-- collapse-prone rows restored" is not something this mart can do.
--
-- What it CAN do: bound the distortion by comparing the population most
-- exposed to this mechanism against the population that is not. A row whose
-- remark matched a known high-frequency template (`to_is_known_template`) is
-- exactly the kind of reused, byte-identical string this index collapses --
-- two officers typing free text are very unlikely to collide, but two
-- officers picking the same dropdown entry under the same status collide
-- constantly. Comparing "all intervals" against "excluding templated `to`
-- events" shows how much the headline would move if every collapse-exposed
-- interval were removed outright -- an upper bound on how much the true
-- (uncollapsed) picture could differ from what is reported, not a corrected
-- estimate of it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_dedup_sensitivity AS
SELECT
    'all_intervals' AS population,
    COUNT(*) AS intervals,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap_days) AS median_gap_days,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY gap_days) AS q1_gap_days,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY gap_days) AS q3_gap_days
FROM handoff_intervals
WHERE NOT is_invalid_order
UNION ALL
SELECT
    'excluding_templated_to_events' AS population,
    COUNT(*) AS intervals,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap_days) AS median_gap_days,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY gap_days) AS q1_gap_days,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY gap_days) AS q3_gap_days
FROM handoff_intervals
WHERE NOT is_invalid_order AND NOT to_is_known_template;

-- ---------------------------------------------------------------------------
-- 7. Coverage / data-quality summary. One row. Read this before quoting
--    anything below -- it is the honesty check for the whole mart.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_coverage_summary AS
SELECT
    (SELECT COUNT(*) FROM action_history_typed)                     AS action_rows_total,
    (SELECT dropped_undated_rows FROM handoff_dropped_undated)      AS dropped_undated_rows,
    (SELECT COUNT(*) FROM handoff_intervals)                        AS emitted_intervals,
    (SELECT COUNT(*) FROM handoff_intervals WHERE is_invalid_order) AS invalid_order_intervals,
    (SELECT COUNT(*) FROM handoff_intervals WHERE is_trailing_open) AS trailing_open_intervals,
    (SELECT COUNT(DISTINCT ticket_no) FROM handoff_intervals)       AS tickets_with_intervals;

-- ---------------------------------------------------------------------------
-- 8. Headline aggregate: median and IQR of gap_days by from_action_type.
--    Invalid-order intervals are excluded (bucketed, not blended in) --
--    see handoff_coverage_summary.invalid_order_intervals for the count.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_gap_by_from_type AS
SELECT
    COALESCE(from_action_type, 'unclassified_tail') AS from_action_type,
    COUNT(*) AS intervals,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap_days) AS median_gap_days,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY gap_days) AS q1_gap_days,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY gap_days) AS q3_gap_days
FROM handoff_intervals
WHERE NOT is_invalid_order
GROUP BY COALESCE(from_action_type, 'unclassified_tail');

-- ---------------------------------------------------------------------------
-- 9. Headline aggregate, second cut: intervals that OPENED on a
--    forwarded_delegated action, median/IQR of gap_days split by the
--    ticket-creation-year proxy (§4). This is the closest this mart gets to
--    "how long does a hand-off sit", still descriptive, still not a saving.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW handoff_forwarded_delegated_by_year AS
SELECT
    y.ticket_creation_year_proxy,
    COUNT(*) AS intervals,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY i.gap_days) AS median_gap_days,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY i.gap_days) AS q1_gap_days,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY i.gap_days) AS q3_gap_days
FROM handoff_intervals i
JOIN handoff_ticket_year y ON y.ticket_no = i.ticket_no
WHERE NOT i.is_invalid_order
  AND i.from_action_type = 'forwarded_delegated'
GROUP BY y.ticket_creation_year_proxy
ORDER BY y.ticket_creation_year_proxy;
