# CPU-box runbook — Sprint 3 operationalization (dedup + pipeline)

Box: `ubuntu@52.66.116.80`, reached with `ssh -A ubuntu@52.66.116.80` (agent
forwarding is required — the box pulls from GitHub over SSH).

The box holds the **production** OLTP Postgres in a Docker container
(`janasunani-oltp`). Everything below writes to it. Read
[docs/DEPLOY.md](DEPLOY.md) and the data rules in `AGENTS.md` first.

## 0. State as of 2026-08-07

Verified on the box, not assumed:

| Thing | State |
|---|---|
| Repo checkout | `~/janasunani`, clean, pulled to `07be1e8` |
| `uv` | `0.11.26`, at `~/.local/bin/uv` — **not on the non-interactive PATH**, use `bash -lc` |
| `psql` | **not installed**; query through `.venv/bin/python` + SQLAlchemy instead |
| `.env` keys | `DEDUP_SALT` (set), `OLTP_DB_URL`, `ENV`. **No `SARVAM_API_KEY`** |
| Alembic | at `e59fb4410dd6`; head is `f3a91c0d54e7` (**one migration behind**) |
| Complaints | 1,371,288 |
| `grievance_redactions` | 55,544 |
| `dedup_groups` / `dedup_signatures` | 55,544 / 55,544 — from an **earlier run with no provenance** |
| Backups | nightly 21:30 UTC → `s3://grievance-database-backups-main/janasunani/`, current, ~654 MB/day |
| Running services | none (no API, no frontend) — the venv can be resynced safely |

The nightly backup writes to `~/janasunani/logs/backup.log`, which is empty;
the cron is nonetheless working. Verify with `aws s3 ls`, not the log.

## 1. Prerequisites

```bash
ssh -A ubuntu@52.66.116.80
cd ~/janasunani
git pull origin main
uv sync            # REQUIRED: a stale venv has no janasunani-dedup-index
ls .venv/bin | grep dedup
```

`uv sync` without extras is enough for `janasunani-dedup-index`: it reads
`grievance_redactions` and never loads Presidio. Presidio is only needed to
*produce* redactions (§3).

## 2. Schema: provenance columns

`f3a91c0d54e7` adds three nullable columns (`dedup_signatures.source_record_digest`,
`dedup_groups.source_name`, `dedup_groups.source_snapshot_id`). It is additive
and has a clean `downgrade()`.

```bash
uv run alembic current      # expect e59fb4410dd6
uv run alembic upgrade head # -> f3a91c0d54e7
```

The migration **cannot** backfill the 55,544 existing groups — the migration's
own docstring says so. Those rows keep `source_snapshot_id IS NULL` until
re-indexed, and downstream duplicate analytics must reject legacy NULL
provenance rather than treat it as fresh.

## 3. Dedup index

```bash
# smoke (20 complaints)
uv run janasunani-dedup-index --district Sambalpur --year 2024 --limit 20

# one full slice
uv run janasunani-dedup-index --district Sambalpur --year 2024

# backfill per district-year
for district in Sambalpur Bargarh Balangir Nayagarh Khordha; do
  for year in 2021 2022 2023 2024 2025; do
    uv run janasunani-dedup-index --district "$district" --year "$year"
  done
done
```

`--salt` defaults to `DEDUP_SALT` from `.env`. Do not pass a different salt:
the salt is part of `index_version`, so changing it silently re-partitions the
index and makes old and new groups incomparable.

### Verifying (no psql on the box)

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
url=[l.split("=",1)[1].strip() for l in open(".env") if l.startswith("OLTP_DB_URL=")][0]
async def main():
    e=create_async_engine(url)
    async with e.connect() as c:
        for q in ("select count(*) from dedup_groups",
                  "select count(*) from dedup_groups where source_snapshot_id is null",
                  "select count(distinct duplicate_group_id) from dedup_groups"):
            print(q, "->", (await c.execute(text(q))).scalar())
    await e.dispose()
asyncio.run(main())
PY
```

Note the real column names: `duplicate_group_id`, `group_size`,
`index_version`, `grouped_at`. There is no `group_id`.

## 4. Redaction (only if extending beyond the current 55,544)

```bash
uv run --extra pii janasunani-redact-grievance
```

Presidio conflicts with `pipeline-core` in the same environment; run it as its
own `--extra pii` invocation.

## 5. Findings

Run these where the Parquet lake is (`data/interim`), which is the **local**
machine, not the box:

```bash
uv run python -m janasunani.analytics.findings.duplicate_recall
uv run python -m janasunani.analytics.findings.spike
```

The closure finding has no `if __name__ == "__main__"` guard, so `-m` exits
silently. Call it directly:

```bash
uv run python -c "from janasunani.analytics.findings import closure; closure.main()"
```

As of 2026-08-07 it **refuses to publish**: 22 high-volume off-ladder closing
templates covering 268,885 resolved complaints trip `check_ladder_coverage`.
That is the guard working, not a bug — the templates need validation against
the private source system before the headline can be quoted. Until then
`outputs/findings/closure_finding_summary.csv` does not exist.

## 6. Sarvam

`SARVAM_API_KEY` is not on the box. It does not matter yet: all three
provider-held-data controls (`retention_terms`, `encryption_in_transit`,
`encryption_at_rest`) are `verified=False`, so `route.live_use_ready is False`
and `SarvamVisionAdapter` falls back to pytesseract **without making a network
call**, auditing `reason="SarvamGovernanceError"`. This holds with
`enabled=True` and a real key. The scorecard stays divergence-only per #84.

## 7. Supervisor surface

`GET /supervisor` reads published aggregate artifacts from
`JANASUNANI_SUPERVISOR_FINDINGS_DIR`; unset, every panel is `unavailable`.

Two separate reasons it currently returns no `recorded` panel:

- **closure** — needs `closure_finding_summary.csv`, which §5 refuses to write.
- **workload and spike** — `RecordedWorkloadPanel` and `RecordedSpikePanel`
  exist in `janasunani/serving/schemas.py` but are **constructed nowhere**.
  `_dashboard()` in `janasunani/serving/intelligence.py` always emits the
  unavailable variants. Completing the dedup backfill will not flip these
  panels; that needs code.

The frontend builds fine (`npm run build` in `frontend/`, Turbopack, Next 16.2.10)
and the `/supervisor` route is on `feat/supervisor-screen`, not `main`.
