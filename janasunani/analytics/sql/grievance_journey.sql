-- The journey a grievance takes: how it was filed, how it ended, and which
-- offices touched it on the way.
--
-- Extracted from notebooks/aggregate_vs_ssepd.ipynb so that the OAP notebook
-- (and anything after it) reads one definition rather than a copy. The
-- conventions are the CA&GR Analytics Note's, not ours: the online/offline
-- grouping of filing modes (§2.2), the four-way resolution status rule (§2.3),
-- and disposed-only timing (§3.4.2). `tests/test_grievance_journey_mart.py`
-- pins the note's published figures so a redefinition here cannot silently
-- disagree with a published document.
--
-- Reads complaints + action_history only. Never reads `complaints.grievance`
-- (citizen prose) or `action_history.action_taken_by` (officer names).
--
-- Portable DuckDB + PostgreSQL: no MACRO, no DuckDB-only function. The fiscal
-- year is spelled out inline for that reason -- `CREATE MACRO` is DuckDB-only,
-- and this mart is handed to the department to run on their own PostgreSQL.

-- Years run July to June. `fy_start` is the calendar year the July-June year
-- opens in, so 2024 means July 2024 to June 2025 and `fy_label` renders it
-- '2024-25'.
CREATE OR REPLACE VIEW grievance_year AS
SELECT *,
    CASE WHEN EXTRACT(MONTH FROM created_on) >= 7
         THEN CAST(EXTRACT(YEAR FROM created_on) AS INTEGER)
         ELSE CAST(EXTRACT(YEAR FROM created_on) AS INTEGER) - 1
    END AS fy_start
FROM complaints;

-- One row per grievance with everything the analysis classifies on.
CREATE OR REPLACE VIEW grievance_base AS
SELECT
    ticket_no, created_on, resolved_on, status, mode, mode_id, dept, dept_id,
    category, subcategory, district, transfer_status, office, fy_start,
    CAST(fy_start AS VARCHAR) || '-' || RIGHT(CAST(fy_start + 1 AS VARCHAR), 2)
        AS year,

    -- Note §2.2. Online is Email, Website, WhatsApp, Facebook, Twitter and the
    -- mobile app; everything else -- physical, letter, the district visits,
    -- weekly and joint hearings, urgent -- is a walk-in or a piece of paper.
    CASE WHEN mode_id IN (1, 5, 6, 7, 8, 9) THEN 'Online' ELSE 'Offline' END
        AS channel,

    -- Note §2.3. Disposed splits on whether a benefit actually reached the
    -- citizen; everything not disposed or discarded is still open.
    CASE WHEN status = 'Disposed' AND TRIM(LOWER(benefitted)) = 'yes'
              THEN 'Disposed with benefit'
         WHEN status = 'Disposed' THEN 'Disposed'
         WHEN status = 'Discard'  THEN 'Discarded'
         ELSE 'Open'
    END AS outcome,

    -- A department is stamped on a grievance only when it is routed to one, so
    -- a discarded grievance -- closed before routing, note §2.4 -- carries
    -- none. Any comparison against a single department must therefore use the
    -- routed base, or the department will appear to discard nothing.
    (dept_id IS NOT NULL AND dept_id <> 0) AS is_routed,
    (status = 'Disposed')                  AS is_disposed,

    -- Note §3.4.2: timing is disposed-only, and only where the record's own
    -- dates run forwards.
    CASE WHEN status = 'Disposed' AND resolved_on IS NOT NULL
              AND resolved_on >= created_on
         THEN CAST(resolved_on AS DATE) - CAST(created_on AS DATE)
    END AS days_to_close,

    -- Where the grievance entered the system, in words. `office` holds one of
    -- seven codes (janasunani/ingestion/__init__.py::OFFICE) naming the desk
    -- that first received it, so it separates a grievance addressed straight to
    -- the department from one inherited from the Chief Minister's Cell or a
    -- Collector. A quarter of them carry nothing at all, which is a category in
    -- its own right and not a row to drop.
    CASE
        WHEN office = 'Departments'              THEN 'Received directly by the department'
        WHEN office = 'Office of Chief Minister' THEN 'Forwarded from the Chief Minister''s Cell'
        WHEN office = 'Collector'                THEN 'Forwarded from a District Collector'
        WHEN office = 'Chief Secretary'          THEN 'Forwarded from the Chief Secretary'
        WHEN office = 'Governor'                 THEN 'Forwarded from the Governor''s office'
        WHEN office IN ('Superintendent of Police', 'DG & IG Police')
                                                 THEN 'Forwarded from the police'
        ELSE 'Entry point not recorded'
    END AS entry_route,
    -- COALESCE, not a bare comparison: `office` is NULL for a quarter of
    -- grievances, and NULL here would drop every one of them from a
    -- `NOT received_directly` filter instead of counting it as not direct.
    COALESCE(office = 'Departments', FALSE) AS received_directly
FROM grievance_year;

-- The office that acted, recovered from `complaint_status_with_authority`.
-- The column is "<action_status> - <authority>", so the authority is what
-- follows the status and its separator. Rows whose prefix does not match the
-- row's own status are left NULL rather than sliced at a guessed offset.
CREATE OR REPLACE VIEW acting_office AS
SELECT id, ticket_no, action_taken_date, action_status,
    CASE WHEN complaint_status_with_authority LIKE action_status || ' - %'
         THEN SUBSTR(complaint_status_with_authority,
                     LENGTH(action_status) + 4)
    END AS code
FROM action_history;

-- Level of government, as an ordinal rung: 1 is the state apex, 5 the field.
-- Codes matching no pattern get NULL, and callers count them rather than
-- dropping them silently.
CREATE OR REPLACE VIEW acting_rung AS
SELECT *,
    CASE
        WHEN code LIKE 'CM Grievance Cell%' OR code LIKE 'Chief Minister Office%'
          OR code LIKE 'Office of The Governor%' OR code LIKE 'Chief Secretary%'
          OR code LIKE 'Governor%'                                    THEN 1
        WHEN code LIKE 'Secretary %' OR code LIKE 'Director%'
          OR code LIKE 'Directorate%' OR code LIKE 'Engineer-in-Chief%'
          OR code LIKE 'Labour Commissioner%' OR code LIKE 'DGP%'
          OR code LIKE 'President, BSE%' OR code LIKE 'CEO,%'         THEN 2
        WHEN code LIKE 'Collector,%' OR code LIKE 'SP,%' OR code LIKE 'DCP%'
          OR code LIKE 'DEO,%' OR code LIKE 'CDMO%' OR code LIKE 'DLO%'
          OR code LIKE 'DSWO%' OR code LIKE 'CDAO%' OR code LIKE 'DSSO%'
          OR code LIKE 'CSO%' OR code LIKE 'DWO%' OR code LIKE 'DFO%'
          OR code LIKE 'DRCS%' OR code LIKE 'DRDA%' OR code LIKE 'PDDRDA%'
          OR code LIKE 'DPO%' OR code LIKE 'DIC%' OR code LIKE 'Commissioner, %'
          OR code LIKE 'R.W Division%' OR code LIKE 'R&B Division%'   THEN 3
        WHEN code LIKE 'Sub-Collector%' OR code LIKE 'Sub-Divisional%'
          OR code LIKE 'SDPO%'                                        THEN 4
        WHEN code LIKE 'BDO%' OR code LIKE 'ABDO%' OR code LIKE 'Tehsildar%'
          OR code LIKE 'IIC%' OR code LIKE 'Executive Officer%'
          OR code LIKE 'Sarpanch%'                                    THEN 5
    END AS rung
FROM acting_office;

-- The same office split into a readable role and the place it serves, so no
-- output ever shows a stakeholder a string like 'PDDRDA,Subarnapur'.
-- `role_name` is NULL for codes outside the named roles; callers filter.
--
-- The role is matched on `role_code` -- the trimmed token before the comma --
-- and NOT on the raw `code` with a comma glued to it. Those are not the same
-- test: 95,957 rows are spelled 'DSSO ,Puri' with a space before the comma,
-- so `code LIKE 'DSSO,%'` matched none of them and left the busiest acting
-- role in the SSEPD pension caseload unnamed. Every caller that filters on
-- `role_name IS NOT NULL` -- the office-wait table among them -- dropped it.
-- The same spelling accounts for 7,323 BDO rows and almost every Secretary
-- row, which only escaped because its pattern ended in a space rather than a
-- comma.
CREATE OR REPLACE VIEW acting_office_named AS
SELECT *,
    TRIM(SPLIT_PART(code, ',', 1)) AS role_code,
    TRIM(SPLIT_PART(code, ',', 2)) AS place,
    CASE
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'BDO'
            THEN 'Block Development Officer'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'Tehsildar'    THEN 'Tahasildar'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'Collector'
            THEN 'District Collector'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'Sub-Collector' THEN 'Sub-Collector'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'SP'
            THEN 'Superintendent of Police'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'IIC'          THEN 'Police station'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'DEO'
            THEN 'District education officer'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'CDMO'
            THEN 'District medical officer'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'DSSO'
            THEN 'District social security officer'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'DSWO'
            THEN 'District social welfare officer'
        -- Panchayati Raj's district arm and the apex intake desk. Both clear
        -- the 1,000-use gate many times over (214,535 and 243,726) and both
        -- were unnamed, which left a Panchayati Raj route unable to say who
        -- held the claim at its district step.
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'PDDRDA'
            THEN 'DRDA Project Director'
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'CM Grievance Cell'
            THEN 'Chief Minister''s Grievance Cell'
        -- Secretary keeps a prefix match: the department name follows it in
        -- the same token ('Secretary Panchayati Raj ,Bhubaneswar').
        WHEN TRIM(SPLIT_PART(code, ',', 1)) = 'Secretary'
          OR TRIM(SPLIT_PART(code, ',', 1)) LIKE 'Secretary %'
            THEN 'Department Secretary'
    END AS role_name
FROM acting_rung;

-- Consecutive actions by one office collapsed to a single step, so a "step" is
-- an office spell rather than a keystroke. This is the DSI progress report's
-- "raw compressed steps" and is what its routing-gap benchmark is measured on.
CREATE OR REPLACE VIEW compressed_steps AS
SELECT ticket_no, action_taken_date, id, code, prev_code
FROM (
    SELECT ticket_no, action_taken_date, id, code,
           LAG(code) OVER (PARTITION BY ticket_no
                           ORDER BY action_taken_date, id) AS prev_code
    FROM acting_office
    WHERE ticket_no IS NOT NULL AND action_taken_date IS NOT NULL
) s
WHERE prev_code IS NULL OR prev_code IS DISTINCT FROM code;
