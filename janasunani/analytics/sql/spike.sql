-- Spike decomposition (#78, component b spike): one worked example, decomposed.
-- Reads complaints + action_history + (when built) dedup groups. Never reads
-- complaints.grievance. Portable DuckDB + PostgreSQL.
--
-- Bare spike detection is not a capability: EWMA over (category × district × week)
-- is a day's work. The capability is the decomposition into three numbers:
-- filings, distinct problems (dedup clusters), distinct citizens (signatories).
-- A campaign is not a false spike — spikes are labelled, never suppressed.
-- Baselined against same period last year, not last month (monsoon/summer seasonality).

-- Weekly filing counts by (category, district)
CREATE OR REPLACE VIEW spike_weekly_counts AS
SELECT
    COALESCE(category, 'unknown') AS category,
    COALESCE(district, 'unknown') AS district,
    date_trunc('week', CAST(created_on AS DATE)) AS week,
    EXTRACT(YEAR FROM CAST(created_on AS DATE)) AS y,
    EXTRACT(WEEK FROM CAST(created_on AS DATE)) AS wk,
    COUNT(*) AS filings,
    COUNT(DISTINCT ticket_no) AS distinct_tickets
FROM complaints
WHERE created_on IS NOT NULL
GROUP BY 1,2,3,4,5;

-- EWMA baseline (alpha=0.3) over weekly filings per (category, district), plus
-- year-over-year same-week prior year count for seasonality check
CREATE OR REPLACE VIEW spike_ewma AS
SELECT category, district, week, filings,
       AVG(filings) OVER (
           PARTITION BY category, district
           ORDER BY week ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
       ) AS trailing_8wk_mean,
       -- EWMA approximated as trailing mean here for portability; Python wrapper
       -- computes true EWMA when needed and flags candidates where filings >
       -- 2 * trailing mean AND > yoy same-week + 2*sigma.
       LAG(filings, 52) OVER (PARTITION BY category, district, wk ORDER BY y) AS yoy_same_week
FROM spike_weekly_counts;

-- Candidate spikes: filings at least 2x trailing mean and at least 1.5x yoy
CREATE OR REPLACE VIEW spike_candidates AS
SELECT category, district, week, filings, trailing_8wk_mean, yoy_same_week,
       filings * 1.0 / NULLIF(trailing_8wk_mean, 0) AS lift_vs_trailing,
       filings * 1.0 / NULLIF(yoy_same_week, 0) AS lift_vs_yoy
FROM spike_ewma
WHERE trailing_8wk_mean IS NOT NULL
  AND trailing_8wk_mean > 0
  AND filings >= 2 * trailing_8wk_mean
  AND (yoy_same_week IS NULL OR filings >= 1.5 * yoy_same_week)
ORDER BY lift_vs_trailing DESC, filings DESC;

-- Decomposition placeholders: when dedup groups exist, these join to show
-- distinct problems and distinct citizens per spike week. Until then they
-- report filings only and flag the other two as pending.
CREATE OR REPLACE VIEW spike_decomposition AS
SELECT c.category, c.district, c.week, c.filings,
       NULL::INTEGER AS distinct_clusters,
       NULL::INTEGER AS distinct_signatories,
       'pending dedup index' AS decomposition_status
FROM spike_candidates c;

-- One worked example selector: top candidate by lift, with caveat that
-- decomposition needs dedup index
CREATE OR REPLACE VIEW spike_worked_example AS
SELECT * FROM spike_candidates ORDER BY lift_vs_trailing DESC LIMIT 1;
