-- The duplicate baseline (#72, component b baseline): officer-confirmed duplicates.
-- Insight, not a capability: these are the ~34,700 duplicates the manual process
-- already caught, queryable today with a CASE WHEN. The capability claim is the
-- increment MinHash finds that carries no such remark.
--
-- Reads complaints + action_history only. Never reads citizen text column.
-- Portable DuckDB + PostgreSQL. Exact normalized match over two template families:
--   'case already taken up%' (+ 'case taken up earlier%' variant) — 19,904
--   'duplicate copy%' — 14,767
-- Matching is prefix-anchored because the stored remarks carry suffixes and a
-- referenced ticket number. The ROADMAP figures above came from an ad hoc
-- exact-match query and therefore undercount those variants; the delta is
-- reported rather than smoothed.
-- Normalized: lowercased, whitespace collapsed, trailing dots stripped.
-- The increment, recall, and prevalence queries are in the Python wrapper
-- (janasunani/analytics/findings/duplicate_recall.py) and use the same
-- normalization so the baseline count cannot drift.

-- Normalized closing remark per resolved complaint (reuses closure pattern)
CREATE OR REPLACE VIEW duplicate_closing_remark AS
WITH resolved AS (
    SELECT ticket_no, created_on, resolved_on, district, category, mode, dept
    FROM complaints WHERE resolved_on IS NOT NULL
),
actions AS (
    SELECT a.ticket_no, a.action_taken_remark,
           ROW_NUMBER() OVER (PARTITION BY a.ticket_no ORDER BY a.action_taken_date DESC NULLS LAST, a.id DESC) AS rn
    FROM action_history a JOIN resolved r ON r.ticket_no = a.ticket_no
    WHERE a.action_taken_date IS NOT NULL
      AND CAST(a.action_taken_date AS DATE) <= CAST(r.resolved_on AS DATE)
),
closing AS (SELECT ticket_no, action_taken_remark FROM actions WHERE rn = 1)
SELECT r.ticket_no, r.district, r.category, r.mode, r.dept,
       r.created_on, r.resolved_on,
       REGEXP_REPLACE(TRIM(REGEXP_REPLACE(LOWER(c.action_taken_remark), '\s+', ' ', 'g')), '\.+$', '', 'g') AS closing_remark
FROM resolved r LEFT JOIN closing c ON c.ticket_no = r.ticket_no;

-- Officer-confirmed duplicate baseline (two families, normalized)
CREATE OR REPLACE VIEW duplicate_officer_confirmed AS
SELECT ticket_no, closing_remark,
       CASE
           -- Prefix-anchored, not exact. The stored templates carry a suffix
           -- ('... for examination', '... hence closed') and often a referenced
           -- ticket, so equality matched 8 rows out of ~21,500. The anchor is
           -- what keeps the deferral template out: 'the grievance has been kept
           -- in priority category and shall be taken up after due government
           -- approval' contains 'taken up' but is not a duplicate, and does not
           -- start with 'case'.
           WHEN closing_remark LIKE 'case already taken up%'
             OR closing_remark LIKE 'case taken up earlier%' THEN 'taken_up'
           WHEN closing_remark LIKE 'duplicate copy%' THEN 'duplicate_copy'
           ELSE NULL
       END AS duplicate_family
FROM duplicate_closing_remark;

CREATE OR REPLACE VIEW duplicate_baseline_summary AS
SELECT
    COUNT(*) FILTER (WHERE duplicate_family IS NOT NULL) AS officer_confirmed_total,
    COUNT(*) FILTER (WHERE duplicate_family = 'taken_up') AS taken_up,
    COUNT(*) FILTER (WHERE duplicate_family = 'duplicate_copy') AS duplicate_copy,
    (SELECT COUNT(*) FROM duplicate_closing_remark) AS resolved_with_closing,
    19904 AS roadmap_taken_up, 14767 AS roadmap_duplicate_copy, 34671 AS roadmap_total
FROM duplicate_officer_confirmed;

-- Prevalence by district / category / mode / year (officer-confirmed only)
CREATE OR REPLACE VIEW duplicate_prevalence_by_district AS
SELECT COALESCE(d.district, 'unknown') AS district,
       COUNT(*) AS confirmed_duplicates,
       COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0) AS share_pct
FROM duplicate_closing_remark d JOIN duplicate_officer_confirmed o USING (ticket_no)
WHERE o.duplicate_family IS NOT NULL
GROUP BY 1 ORDER BY confirmed_duplicates DESC;

CREATE OR REPLACE VIEW duplicate_prevalence_by_category AS
SELECT COALESCE(d.category, 'unknown') AS category,
       COUNT(*) AS confirmed_duplicates
FROM duplicate_closing_remark d JOIN duplicate_officer_confirmed o USING (ticket_no)
WHERE o.duplicate_family IS NOT NULL
GROUP BY 1 ORDER BY confirmed_duplicates DESC;

CREATE OR REPLACE VIEW duplicate_prevalence_by_mode AS
SELECT COALESCE(d.mode, 'unknown') AS mode, COUNT(*) AS confirmed_duplicates
FROM duplicate_closing_remark d JOIN duplicate_officer_confirmed o USING (ticket_no)
WHERE o.duplicate_family IS NOT NULL
GROUP BY 1 ORDER BY confirmed_duplicates DESC;

CREATE OR REPLACE VIEW duplicate_prevalence_by_year AS
SELECT EXTRACT(YEAR FROM CAST(d.created_on AS DATE)) AS filing_year, COUNT(*) AS confirmed_duplicates
FROM duplicate_closing_remark d JOIN duplicate_officer_confirmed o USING (ticket_no)
WHERE o.duplicate_family IS NOT NULL
GROUP BY 1 ORDER BY filing_year;

-- Duplicate-adjusted workload: how much of the backlog is one problem arriving more than once
CREATE OR REPLACE VIEW duplicate_workload AS
SELECT
    (SELECT COUNT(*) FROM duplicate_closing_remark) AS resolved_complaints,
    (SELECT officer_confirmed_total FROM duplicate_baseline_summary) AS officer_confirmed_duplicates,
    (SELECT officer_confirmed_total FROM duplicate_baseline_summary) * 100.0 /
        NULLIF((SELECT COUNT(*) FROM duplicate_closing_remark), 0) AS officer_confirmed_share_pct;
