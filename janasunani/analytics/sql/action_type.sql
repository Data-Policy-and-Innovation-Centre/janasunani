-- Action-type lookup over high-frequency action_taken_remark templates (#75)
--
-- Phase 15 S3 — the third derived table. Every management view that needs to
-- say what the officer *did* groups by this. The two coarser classifications
-- (disposal ladder in closure.sql, eight discard reasons in the ROADMAP) are
-- subsets of this seven-class taxonomy plus admin noise.
--
-- Exact-match lookup only, over the top ~60 high-frequency templates for
-- August (Sprint 2 cut, ED 6 Aug). Top-500 plus tail classifier is Post-demo.
-- Per status, not corpus-wide: 301 of the top 500 span >1 status, one spanning
-- 12 of the 15. Status #3 is dropdown-driven (1.18M rows, 15,390 distinct
-- remarks), status #2 is near free text.
--
-- Normalisation mirrors janasunani.analytics.action_type.normalize_remark:
--   LOWER, collapse internal whitespace to one space, TRIM, strip trailing "."
-- and janasunani.analytics.action_type.normalize_status for the status column.
-- The Python module is the source of truth; this SQL is the hand-over
-- artifact. Keep them in sync.
--
-- Portable across DuckDB (the lake) and PostgreSQL (their DB). Reads
-- action_history only. It never touches complaints.grievance — so like the
-- closure mart it needs no redaction pass and no slice decision.
--
-- Aggregates only: action_history_typed emits one row per action_history row
-- with its class, but no finding should print a free-text remark. The only
-- view that groups by raw remark is the drift diagnostic, gated to
-- high-volume (>= 1000) dropdown strings.

-- ---------------------------------------------------------------------------
-- 1. The taxonomy — one row per known template, per status where it matters.
--
-- status IS NULL  →  corpus-wide fallback (class is stable across statuses).
-- status = '...'  →  per-status override. Most duplicate the fallback class
--                    and exist to prove the lookup is per status; a few
--                    diverge (e.g. "ok" under ATR is reported_back, not noise).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW action_type_lookup AS
SELECT * FROM (
    VALUES
        -- disposed ladder (6) — must match closure_disposal_ladder
        ('the grievance has been disposed',                          NULL, 'disposed_no_claim'),
        ('the grievance has been resolved',                          NULL, 'disposed_no_claim'),
        ('the grievance has been disposed with appropriate action',  NULL, 'disposed_with_action'),
        ('the grievance has been resolved with appropriate action',  NULL, 'disposed_with_action'),
        ('the grievance has been disposed & beneficiary benefited',  NULL, 'benefit_delivered'),
        ('the grievance has been resolved & beneficiary benefited',  NULL, 'benefit_delivered'),
        -- forwarded / delegated (10)
        ('forwarded to concerned officer for necessary action',      NULL, 'forwarded_delegated'),
        ('forwarded to collector for necessary action',              NULL, 'forwarded_delegated'),
        ('forwarded to block development officer for necessary action', NULL, 'forwarded_delegated'),
        ('forwarded to tahasildar for necessary action',             NULL, 'forwarded_delegated'),
        ('forwarded to executive engineer for necessary action',     NULL, 'forwarded_delegated'),
        ('forwarded to district level officer',                      NULL, 'forwarded_delegated'),
        ('forwarded to concerned department',                        NULL, 'forwarded_delegated'),
        ('forwarded to superintendent of police for necessary action', NULL, 'forwarded_delegated'),
        ('delegated to concerned officer',                           NULL, 'forwarded_delegated'),
        ('transferred to concerned authority',                       NULL, 'forwarded_delegated'),
        -- reported back / ATR (10)
        ('atr received from concerned officer',                      NULL, 'reported_back'),
        ('compliance report received',                               NULL, 'reported_back'),
        ('enquiry report received',                                  NULL, 'reported_back'),
        ('field enquiry report submitted',                           NULL, 'reported_back'),
        ('action taken report furnished',                            NULL, 'reported_back'),
        ('report received from collector',                           NULL, 'reported_back'),
        ('report received from block development officer',           NULL, 'reported_back'),
        ('joint enquiry report received',                            NULL, 'reported_back'),
        ('atr submitted by concerned officer',                       NULL, 'reported_back'),
        ('reply received from concerned department',                 NULL, 'reported_back'),
        -- discarded with reason (15 — eight families)
        ('complaint details inadequate',                             NULL, 'discarded_with_reason'),
        ('grievance details inadequate',                             NULL, 'discarded_with_reason'),
        ('required documents not attached',                          NULL, 'discarded_with_reason'),
        ('documents not attached',                                   NULL, 'discarded_with_reason'),
        ('case already taken up earlier',                            NULL, 'discarded_with_reason'),
        ('grievance already taken up earlier',                       NULL, 'discarded_with_reason'),
        ('no specific grievance',                                    NULL, 'discarded_with_reason'),
        ('duplicate copy of grievance',                              NULL, 'discarded_with_reason'),
        ('duplicate grievance',                                      NULL, 'discarded_with_reason'),
        ('needs policy decision',                                    NULL, 'discarded_with_reason'),
        ('can be considered only after policy decision',             NULL, 'discarded_with_reason'),
        ('not within the purview of this grievance cell',            NULL, 'discarded_with_reason'),
        ('not within purview of this grievance cell',                NULL, 'discarded_with_reason'),
        ('address not given',                                        NULL, 'discarded_with_reason'),
        ('complete address not provided',                            NULL, 'discarded_with_reason'),
        -- reopened / escalated (6)
        ('grievance reopened as per direction',                      NULL, 'reopened_escalated'),
        ('grievance reopened for re-enquiry',                        NULL, 'reopened_escalated'),
        ('escalated to higher authority',                            NULL, 'reopened_escalated'),
        ('escalated to appellate authority',                         NULL, 'reopened_escalated'),
        ('reopened on request of petitioner',                        NULL, 'reopened_escalated'),
        ('grievance reopened',                                       NULL, 'reopened_escalated'),
        -- admin noise (11 + Odia)
        ('.',                                                        NULL, 'admin_noise'),
        ('ok',                                                       NULL, 'admin_noise'),
        ('other',                                                    NULL, 'admin_noise'),
        ('pmay',                                                     NULL, 'admin_noise'),
        ('mgnrega',                                                  NULL, 'admin_noise'),
        ('bsky',                                                     NULL, 'admin_noise'),
        ('kala',                                                     NULL, 'admin_noise'),
        ('-',                                                        NULL, 'admin_noise'),
        ('na',                                                       NULL, 'admin_noise'),
        ('nil',                                                      NULL, 'admin_noise'),
        ('noted',                                                    NULL, 'admin_noise'),
        ('ଅଭିଯୋଗଟି ସମାଧାନ ହୋଇଛି',                                      NULL, 'admin_noise'),
        -- per-status overrides (same remark, different status → explicit row)
        ('the grievance has been disposed',                          'disposed', 'disposed_no_claim'),
        ('the grievance has been disposed',                          'resolved', 'disposed_no_claim'),
        ('the grievance has been disposed',                          'closed', 'disposed_no_claim'),
        ('the grievance has been disposed',                          'forwarded', 'disposed_no_claim'),
        ('the grievance has been disposed',                          'atr received', 'disposed_no_claim'),
        ('the grievance has been disposed',                          'pending', 'disposed_no_claim'),
        ('the grievance has been resolved',                          'disposed', 'disposed_no_claim'),
        ('the grievance has been resolved',                          'resolved', 'disposed_no_claim'),
        ('the grievance has been resolved',                          'closed', 'disposed_no_claim'),
        ('the grievance has been disposed with appropriate action',  'disposed', 'disposed_with_action'),
        ('the grievance has been disposed with appropriate action',  'resolved', 'disposed_with_action'),
        ('the grievance has been resolved with appropriate action',  'disposed', 'disposed_with_action'),
        ('the grievance has been resolved with appropriate action',  'resolved', 'disposed_with_action'),
        ('noted',                                                    'forwarded', 'forwarded_delegated'),
        ('ok',                                                       'atr received', 'reported_back')
) AS t(template, status, action_type);

-- ---------------------------------------------------------------------------
-- 2. One row per action_history row, with its normalised keys and its class.
--
-- Normalised exactly as the Python does, so the two agree row-for-row.
-- action_type IS NULL  →  free-text tail, for the Post-demo classifier.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW action_history_typed AS
WITH normalised AS (
    SELECT
        a.id,
        a.ticket_no,
        a.action_taken_date,
        a.action_taken_by,
        a.action_status,
        a.action_taken_remark,
        CASE
            WHEN TRIM(LOWER(a.action_taken_remark)) IN ('.', '-') THEN TRIM(LOWER(a.action_taken_remark))
            ELSE REGEXP_REPLACE(
                TRIM(REGEXP_REPLACE(LOWER(a.action_taken_remark), '\s+', ' ', 'g')),
                '\.+$', '', 'g'
            )
        END AS remark_norm,
        TRIM(REGEXP_REPLACE(LOWER(a.action_status), '\s+', ' ', 'g')) AS status_norm
    FROM action_history a
),
per_status AS (
    SELECT n.*, l.action_type AS per_status_type
    FROM normalised n
    LEFT JOIN action_type_lookup l
      ON l.template = n.remark_norm AND l.status = n.status_norm
),
corpus AS (
    SELECT n.*, c.action_type AS corpus_type
    FROM normalised n
    LEFT JOIN action_type_lookup c
      ON c.template = n.remark_norm AND c.status IS NULL
)
SELECT
    n.id,
    n.ticket_no,
    n.action_taken_date,
    n.action_taken_by,
    n.action_status,
    n.action_taken_remark,
    n.remark_norm,
    n.status_norm,
    COALESCE(ps.per_status_type, co.corpus_type) AS action_type,
    CASE WHEN COALESCE(ps.per_status_type, co.corpus_type) IS NOT NULL THEN 1 ELSE 0 END AS is_known_template
FROM normalised n
LEFT JOIN per_status ps ON ps.id = n.id
LEFT JOIN corpus co ON co.id = n.id;

-- ---------------------------------------------------------------------------
-- 3. Action-type prevalence — the headline for S3. Aggregates only.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW action_type_summary AS
SELECT
    COALESCE(action_type, 'unclassified_tail') AS action_type,
    COUNT(*) AS action_rows,
    COUNT(DISTINCT ticket_no) AS distinct_complaints,
    100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0) AS share_of_actions_pct
FROM action_history_typed
GROUP BY COALESCE(action_type, 'unclassified_tail');

-- ---------------------------------------------------------------------------
-- 4. Per-status breakdown — proves the lookup is per status.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW action_type_by_status AS
SELECT
    COALESCE(action_status, '(null)') AS action_status,
    COALESCE(action_type, 'unclassified_tail') AS action_type,
    COUNT(*) AS action_rows
FROM action_history_typed
GROUP BY COALESCE(action_status, '(null)'), COALESCE(action_type, 'unclassified_tail');

-- ---------------------------------------------------------------------------
-- 5. Drift diagnostic — high-volume unclassified remarks only.
--
-- The free-text tail is personal data and must not be emitted. So this
-- gates on >= 1000 uses, the same floor the closure diagnostic uses.
-- It emits the normalised remark and a count, not the raw citizen text.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW action_type_unclassified_templates AS
SELECT
    remark_norm AS template,
    COUNT(*) AS action_rows
FROM action_history_typed
WHERE action_type IS NULL AND remark_norm IS NOT NULL AND remark_norm <> ''
GROUP BY remark_norm
HAVING COUNT(*) >= 1000
ORDER BY action_rows DESC;
