# janasunani.migration — cold-start and live sync

Loads the grievance history into the OLTP store. Two entry points, **one
validated insert routine** (`from_mysql.run_migration`):

- `janasunani-migrate-dump` (`from_sql_dump.py`) — cold start: restore the raw
  `mysqldump` (`data/raw/Dump20250730.sql`, 3.2 GB, DVC-tracked) into a
  throwaway MySQL container, then hand off to `run_migration`. Wrapped end to
  end by [`scripts/migrate.sh`](../../scripts/migrate.sh).
- `janasunani-migrate-mysql` (`from_mysql.py`) — live sync against a running
  MySQL, same routine.

## How the load works

Each source table is read **once** through a server-side streaming cursor; rows
validate through the Pydantic schemas (the single raw→ORM map in
`janasunani/ingestion/schemas.py`) and bulk-insert with driver `executemany` +
on-conflict-do-nothing. Action-history rows resolve `ticket_no` from an
in-memory `{tracking_id: ticket_no}` map — no giant `IN (...)`, no `OFFSET`
re-scans. The load is deterministic and idempotent: re-running produces the
same OLTP content.

**Ground truth after a full run: 1,371,288 complaints / 6,556,171
action-history rows.** Any change here must reproduce those exact counts (the
dedup story behind the second number is in [`../db/README.md`](../db/README.md)).

## Operational notes

- Run the full-scale migration **on the box next to the database** (dump pulled
  via `dvc pull`), never across the internet.
- `scripts/migrate.sh` probes MySQL readiness with an **authenticated**
  `SELECT 1` — a bare ping answers during MySQL's first-boot temp server and
  then hits "Access denied" (bit us on EC2).
- The dump text contains NUL bytes (~600k rows); they're stripped in the schema
  layer *before* the tracking-map lookup. Postgres would reject them at insert.
- URLs in logs are rendered with `hide_password=True`.
