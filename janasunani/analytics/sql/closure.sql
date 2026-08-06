-- The closure finding (#76) as a set of views. Insight, not a capability.
--
-- What it answers: of the complaints an officer closed using one of the six
-- standard disposal templates, what share used the rung that claims no action?
--
-- HOW TO READ THE OUTPUT. The share moves by half depending on the denominator,
-- so `closure_finding_summary` reports both and neither is optional:
--   * share of LADDER closures  -- complaints closed on one of the six templates
--   * share of ALL RESOLVED     -- every resolved complaint, including the third
--                                  that closes on neither template
-- Never quote one without the other.
--
-- A bare disposal does NOT mean the case was mishandled. Correct closure and
-- premature closure are identical in this record: an information request
-- answered, an ineligible claim properly refused, and a case dropped without
-- work all close on the same string. This is DESCRIPTIVE. It is not a failure
-- rate, and turning it into one needs a few hundred closures adjudicated by
-- hand. Read `closure_by_trajectory` before quoting the headline: a case that
-- went created -> forwarded -> ATR -> disposed had work done whatever the
-- closing phrase says.
--
-- Portable across DuckDB (the Parquet lake) and PostgreSQL (the OLTP store).
-- Reads `complaints` and `action_history` only. It never touches
-- `complaints.grievance`, so it needs no redaction pass. Every reportable view
-- below is an aggregate. The one view that emits remark text at all,
-- `closure_off_ladder_templates`, is a drift diagnostic for the engineer rather
-- than part of the handover, and is written to a separate directory.

-- ---------------------------------------------------------------------------
-- 1. The disposal ladder.
--
-- Six templates, three rungs. Officers pick from this dropdown, so the field is
-- a structured signal wearing a text costume and exact matching is the right
-- tool. Templates are stored NORMALIZED: lowercased, whitespace collapsed,
-- trailing full stops removed -- matching what `closure_closing_action` does to
-- the remark. Correct a string here and every view below follows.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_disposal_ladder AS
SELECT * FROM (
    VALUES
        ('the grievance has been disposed',                          'bare',        1),
        ('the grievance has been resolved',                          'bare',        1),
        ('the grievance has been disposed with appropriate action',  'with_action', 2),
        ('the grievance has been resolved with appropriate action',  'with_action', 2),
        ('the grievance has been disposed & beneficiary benefited',  'benefit',     3),
        ('the grievance has been resolved & beneficiary benefited',  'benefit',     3)
) AS t(template, rung, ladder_position);

-- ---------------------------------------------------------------------------
-- 2. One row per resolved complaint: its closing remark plus its trajectory.
--
-- "Resolved" is `resolved_on IS NOT NULL`. The closing remark is the latest
-- action row **at or before the resolution date**, ties broken by `id`
-- (insertion order), so a complaint with no action history still appears, with
-- a NULL remark and zero steps.
--
-- Trajectory is carried here rather than bolted on later because it is a
-- required control, not an optional cut.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_closing_action AS
WITH resolved AS (
    SELECT ticket_no, created_on, resolved_on, benefitted, category, district, dept
    FROM complaints
    WHERE resolved_on IS NOT NULL
),
-- Actions up to the recorded resolution, and only those. A reopen, audit or
-- follow-up row filed after `resolved_on` is not what closed the case: taking
-- the latest row unconditionally would pick it as the "closing" remark and
-- knock a complaint off the ladder whose actual disposal matched a template,
-- while also counting post-closure activity as work done before closure.
-- Compared at date granularity, not timestamp, because the disposal row and
-- `resolved_on` are written by different code paths on the same day and an
-- intraday ordering difference is not a signal.
actions AS (
    SELECT
        a.ticket_no,
        a.action_taken_remark,
        a.action_status,
        ROW_NUMBER() OVER (
            PARTITION BY a.ticket_no
            ORDER BY a.action_taken_date DESC NULLS LAST, a.id DESC
        ) AS recency_rank,
        COUNT(*) OVER (PARTITION BY a.ticket_no) AS action_steps
    FROM action_history a
    JOIN resolved r ON r.ticket_no = a.ticket_no
    WHERE a.action_taken_date IS NULL
       OR CAST(a.action_taken_date AS DATE) <= CAST(r.resolved_on AS DATE)
),
closing AS (
    SELECT ticket_no, action_taken_remark, action_status, action_steps
    FROM actions
    WHERE recency_rank = 1
)
SELECT
    r.ticket_no,
    r.created_on,
    r.resolved_on,
    r.benefitted,
    r.category,
    r.district,
    r.dept,
    c.action_status AS closing_action_status,
    -- lowercase, collapse internal whitespace, drop trailing full stops
    REGEXP_REPLACE(
        TRIM(REGEXP_REPLACE(LOWER(c.action_taken_remark), '\s+', ' ', 'g')),
        '\.+$', '', 'g'
    ) AS closing_remark,
    COALESCE(c.action_steps, 0) AS action_steps,
    CAST(r.resolved_on AS DATE) - CAST(r.created_on AS DATE) AS elapsed_days
FROM resolved r
LEFT JOIN closing c ON c.ticket_no = r.ticket_no;

-- ---------------------------------------------------------------------------
-- 3. Each resolved complaint assigned a rung.
--
-- `off_ladder` is every resolved complaint that closed on some other wording
-- (or on no remark at all). It is roughly a third of them, and it is the whole
-- reason the two denominators differ.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_rung AS
SELECT
    c.*,
    COALESCE(l.rung, 'off_ladder') AS rung,
    l.ladder_position,
    CASE WHEN l.rung IS NOT NULL THEN 1 ELSE 0 END AS on_ladder,
    -- Zero actions is its own bucket, not folded into '1 step'. A complaint
    -- with no action history at all is a different object from one an officer
    -- touched once, and folding them together silently pads the denominator of
    -- the shortest-trajectory cut.
    CASE
        WHEN c.action_steps = 0 THEN '0 steps'
        WHEN c.action_steps = 1 THEN '1 step'
        WHEN c.action_steps = 2 THEN '2 steps'
        WHEN c.action_steps <= 5 THEN '3-5 steps'
        ELSE '6+ steps'
    END AS steps_bucket,
    -- `resolved_on` before `created_on` is possible: the two timestamps are
    -- parsed independently at ingest and nothing enforces an order. Such a row
    -- has a negative duration, and without this branch it would land in
    -- '0-2 days' and inflate the fast-closure subset with bad data rather than
    -- fast work.
    CASE
        WHEN c.elapsed_days IS NULL THEN 'unknown'
        WHEN c.elapsed_days < 0 THEN 'invalid'
        WHEN c.elapsed_days <= 2 THEN '0-2 days'
        WHEN c.elapsed_days <= 7 THEN '3-7 days'
        WHEN c.elapsed_days <= 30 THEN '8-30 days'
        ELSE '31+ days'
    END AS elapsed_bucket
FROM closure_closing_action c
LEFT JOIN closure_disposal_ladder l ON l.template = c.closing_remark;

-- ---------------------------------------------------------------------------
-- 4. The headline, on both denominators. One row.
--
-- Quote `bare_share_of_ladder_pct` and `ladder_closures` in the same breath,
-- always, and say what the other 35-ish percent did.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_finding_summary AS
SELECT
    COUNT(*)                                                   AS resolved_complaints,
    SUM(on_ladder)                                             AS ladder_closures,
    COUNT(*) FILTER (WHERE rung = 'bare')                      AS bare,
    COUNT(*) FILTER (WHERE rung = 'with_action')               AS with_action,
    COUNT(*) FILTER (WHERE rung = 'benefit')                   AS benefit,
    COUNT(*) FILTER (WHERE rung IN ('with_action', 'benefit')) AS claims_action,
    COUNT(*) FILTER (WHERE rung = 'off_ladder')                AS off_ladder,
    -- the 61% figure: bare rungs as a share of templated closures
    100.0 * COUNT(*) FILTER (WHERE rung = 'bare')
          / NULLIF(SUM(on_ladder), 0)                          AS bare_share_of_ladder_pct,
    -- the same complaints against every resolved complaint
    100.0 * COUNT(*) FILTER (WHERE rung = 'bare')
          / NULLIF(COUNT(*), 0)                                AS bare_share_of_resolved_pct,
    -- how much of the resolved corpus the ladder covers at all
    100.0 * SUM(on_ladder) / NULLIF(COUNT(*), 0)               AS ladder_coverage_pct,
    100.0 * COUNT(*) FILTER (WHERE rung = 'off_ladder')
          / NULLIF(COUNT(*), 0)                                AS off_ladder_share_pct
FROM closure_rung;

-- ---------------------------------------------------------------------------
-- 5. The headline conditioned on trajectory. THIS IS NOT OPTIONAL.
--
-- created -> forwarded -> ATR -> disposed is not the same case as
-- created -> disposed in two days, and the closing phrase cannot tell them
-- apart. If the bare share is flat across these cells there is nothing here.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_by_trajectory AS
SELECT
    steps_bucket,
    elapsed_bucket,
    COUNT(*)                                     AS resolved_complaints,
    SUM(on_ladder)                               AS ladder_closures,
    COUNT(*) FILTER (WHERE rung = 'bare')        AS bare,
    100.0 * COUNT(*) FILTER (WHERE rung = 'bare')
          / NULLIF(SUM(on_ladder), 0)            AS bare_share_of_ladder_pct
FROM closure_rung
GROUP BY steps_bucket, elapsed_bucket;

-- ---------------------------------------------------------------------------
-- 6. The sub-finding: created and closed within two days on a bare disposal.
--
-- This is the useful half. It names a specific set of cases instead of
-- indicting the redressal system as a whole -- a finding an official can act
-- on rather than one they have to defend against. Same caveat still applies:
-- two days is fast, not wrong. An information request answered on the spot
-- belongs here too.
--
-- `two_day_bare_min_trajectory` is the floor case: closed in two days on the
-- shortest trajectory that reaches a disposal at all. THREE action rows, not
-- one. The portal writes a create and an assign row before an officer can
-- dispose, so a single-step disposal is not a thing that exists in this record
-- (22 resolved complaints corpus-wide carry one action row, and none of them
-- close on the ladder). Anyone reaching for "closed in one step" is reaching
-- for this.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_two_day_bare AS
SELECT
    COUNT(*) FILTER (WHERE rung = 'bare' AND elapsed_days BETWEEN 0 AND 2)
        AS two_day_bare,
    COUNT(*) FILTER (WHERE rung = 'bare' AND elapsed_days BETWEEN 0 AND 2 AND action_steps <= 3)
        AS two_day_bare_min_trajectory,
    COUNT(*) FILTER (WHERE rung = 'bare')
        AS bare,
    SUM(on_ladder)
        AS ladder_closures,
    COUNT(*)
        AS resolved_complaints,
    100.0 * COUNT(*) FILTER (WHERE rung = 'bare' AND elapsed_days BETWEEN 0 AND 2)
          / NULLIF(COUNT(*) FILTER (WHERE rung = 'bare'), 0)
        AS share_of_bare_pct,
    100.0 * COUNT(*) FILTER (WHERE rung = 'bare' AND elapsed_days BETWEEN 0 AND 2)
          / NULLIF(SUM(on_ladder), 0)
        AS share_of_ladder_pct,
    100.0 * COUNT(*) FILTER (WHERE rung = 'bare' AND elapsed_days BETWEEN 0 AND 2)
          / NULLIF(COUNT(*), 0)
        AS share_of_resolved_pct
FROM closure_rung;

-- ---------------------------------------------------------------------------
-- 7. Overlap with the existing `complaints.benefitted` column.
--
-- Checked before claiming the third rung is novel. If `benefitted` already
-- marks the same complaints the 'benefit' rung does, the rung is a restatement
-- of a column the dashboards already have and must not be presented as new.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_benefitted_overlap AS
SELECT
    rung,
    COALESCE(TRIM(LOWER(benefitted)), '(null)') AS benefitted_value,
    COUNT(*) AS resolved_complaints
FROM closure_rung
GROUP BY rung, COALESCE(TRIM(LOWER(benefitted)), '(null)');

-- ---------------------------------------------------------------------------
-- 8. Coverage check for the ladder itself.
--
-- The top closing remarks that did NOT match a template, most frequent first.
-- Run this first on any new corpus: if the ladder strings have drifted, the
-- headline silently collapses into `off_ladder` rather than failing. High-
-- frequency templates only (used 1,000+ times) -- the free-text tail is a
-- privacy boundary as well as a scope one, and no row of citizen writing is
-- a template.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW closure_off_ladder_templates AS
SELECT
    closing_remark,
    COUNT(*) AS resolved_complaints
FROM closure_rung
WHERE rung = 'off_ladder' AND closing_remark IS NOT NULL
GROUP BY closing_remark
HAVING COUNT(*) >= 1000;
