# janasunani Roadmap — Part I: Foundation/Migration · Part II: Automation Prototype

## Context

`janasunani` is becoming "Janasunani 2.0," the unified, AI-powered grievance redressal system for
Odisha. End goal: a **full automation prototype** — take a **raw grievance** (typed text or an uploaded
scanned document), **extract** text, **redact PII**, **classify** (category / subcategory / department),
**summarize**, and **route** it to the responsible office — quickly — with a polished **Next.js demo**.

- **Part I — Foundation** *(in progress)*: consolidate work from two other repos into one `janasunani/`
  package and load the data: the cold-start SQL migration into the OLTP store, the downstream Parquet
  materialization, document ingestion → S3, the document-processing/ML pipeline, and tracking.
- **Part II — Automation Prototype & Demo**: a real-time single-grievance inference service, a hybrid
  (rules + learned) routing engine, FastAPI serving that writes live grievances into the **same** OLTP DB,
  and the Next.js UI.

This plan is the source of truth, **mirrored into the repo at `docs/ROADMAP.md`** (kept in sync).

## Handoff snapshot — state as of 2026-07-02 (for a fresh reviewer)

> This section exists so a reviewer with **no prior context** can understand where
> the project actually stands, reproduce it, and give input on the plan below.

**Branch:** `feat/migration-foundation` (clean). Foundation Phases 0–4 are built,
tested, and committed; Phase 5 is next. **Nothing has run on AWS yet** — every
result below was verified on a local dev machine (macOS, `uv` + Docker).

**Source provenance — this repo consolidates code from two earlier projects**
- **DB/ORM layer + document ingestion → S3 + config** — from `../grievance`,
  branch **`dev`** (`backend/app/db/*`, `backend/app/ingestion/*`,
  `backend/app/s3service.py`, `backend/app/config.py`). Refolded into
  `janasunani/db/`, `janasunani/ingestion/`, `janasunani/config.py` (dropped the
  FastAPI "backend" framing).
- **SQL migration logic** — from `../grievance`,
  `backend/app/db/migration_from_mysql.py` (live MySQL → local DB; NOT on `dev`,
  but byte-identical across the `documents` / `alembic-migration` /
  `migration-temp` / `feb12_presentation` branches). Now `janasunani/migration/`.
- **Document-processing / ML pipeline** (OCR / PII redaction / page-type /
  summarize / categorize) — the DSI (Data Science Institute) work, from the local
  clone **`../2025-autumn-dpic`** (GitHub remote
  `Data-Policy-and-Innovation-Centre/grievance-pipeline`), branch **`pipeline`**,
  code at `src/document_pipeline/`. Lands in `janasunani/pipeline/` in **Phase 5**
  (not yet migrated).
- **Cold-start data** — the SQL dump `data/raw/Dump20250730.sql` (3.2 GB), a
  `mysqldump` of MySQL DB `sociomatics_ticket` (only the two ETL tables). The dump
  — not any branch — is the authoritative schema.
- **Explicitly out of scope:** the ORTPS analysis pipeline on `grievance`'s
  `refactor_cleaning` / `feb12_presentation` branches — a specific ORTPS
  application we are not using. Don't confuse it with the in-scope
  `document_pipeline`.

**What's built and verified locally**
- **Cold-start migration `dump → OLTP`** — ran the full 3.2 GB `mysqldump` end to
  end → **1,371,288 complaints + 6,556,171 action-history rows** in the SQLite
  OLTP DB at `data/oltp/janasunani.db`. Load is deterministic + idempotent
  (byte-reproducible OLTP DB across runs).
- **OLTP is engine-swappable** via `OLTP_DB_URL` (SQLite locally / Postgres on
  AWS). Schema managed by **Alembic**; upgrade/downgrade verified on both engines.
  Conflict-inserts are dialect-portable (`_dialect_insert`).
- **Materialization `OLTP → Parquet`** (DuckDB scanner) — 1.37M + 6.56M rows →
  ~481M + ~475M Parquet in ~26 s. This is the **one DVC-tracked transform**.
- **Document ingestion → S3** — `s3service` + ingestion `client` (`with_retry`) +
  `DocumentService` (download → S3/local, status written back to OLTP).
- **32 pytest tests green on the real code path** (async engine, moto S3, respx
  HTTP); `ruff` clean.

**How to reproduce (local)**
```bash
uv sync
bash scripts/migrate.sh          # ephemeral MySQL in Docker → restore dump → load OLTP
dvc repro materialize            # or: uv run janasunani-materialize  (OLTP → Parquet)
uv run pytest && uv run ruff check .   # gate
```

**Recent structural decisions (these post-date parts of the phase plan below)**
- The cold-start migration is **not** a DVC stage. DVC tracks only file artifacts
  (the Parquet lake + models); operational writes (OLTP DB, S3) run as jobs/CLIs.
  `dvc.yaml` now has a **single `materialize` stage**.
- **DVC + Postgres caveat:** the `materialize` stage deps on the SQLite file
  (`data/oltp/janasunani.db`), so `dvc repro` only works with the local SQLite OLTP.
  When OLTP is Postgres (cloud), run `janasunani-materialize` directly and
  `dvc commit` the Parquet outs.
- `action_history` dedup uses a **functional unique index** that coalesces NULLs
  (NULL-keyed duplicate rows now collapse). This rebuild is why the count is
  **6,556,171** (down from a pre-dedup 6,565,323 seen in earlier notes).

**Immediate real-world plan (reviewed 2026-07-02; single maintainer, week-by-week)**

*Week 1 — cloud foundation (CPU only; no GPU needed — only DeepSeek OCR requires CUDA).*
1. Pre-flight fixes: `config.py` AWS settings → `Optional[str] = None`; document the
   Postgres materialize workaround (see DVC note below). `pytest` + `ruff` green.
2. Terraform (`deploy/terraform/`): t3.large-class CPU box + ~150 GB gp3 EBS, IAM
   instance role (documents/data buckets + `dpic-dvc-cache`), SG (22 from own IP, 80/443).
3. Bootstrap the box (Docker, uv, clone); `aws s3 cp` the 3.2 GB dump up.
4. Postgres container (volume on EBS) → `alembic upgrade head` → run
   `scripts/migrate.sh` **on the box** (never across the internet) → verify
   **1,371,288 / 6,556,171**. The full-scale load over asyncpg is the least-tested
   path — watch batch sizes + total time.
5. Materialize on-box → verify Parquet counts → `dvc commit` + push.
6. Ingestion smoke against real S3 (small batch); nightly `pg_dump` → S3 cron.

*Week 2 — Phase 5 pipeline + GPU.*
7. Refold `document_pipeline` → `janasunani/pipeline/` (decisions in Phase 5 below);
   per-stage tests + 2-file pytesseract smoke locally.
8. GPU box: g6.xlarge from a Deep Learning AMI (skips driver pain) +
   nvidia-container-toolkit; build the `ocr-deepseek` image; DeepSeek smoke on the
   2-file sample. Watch for `trust_remote_code` importing `flash_attn` unconditionally.
9. **Sample backfill only** (~200 curated docs), not the full corpus → OLTP → lake.
10. MLflow slim (Phase 6 folded in): local backend on the CPU box, artifacts to S3.

*Week 3 — inference, routing, serving (Phases 8–10).* Rules router **before** the
learned router so the demo never blocks on training.

*Week 4 — frontend + deploy + demo (Phases 11–12).* Demo runbook: start GPU box →
health check → demo → stop GPU box.

## Storage architecture — OLTP front, Parquet/OLAP downstream

```
  MySQL dump ──(migrate: from_sql_dump → from_mysql)──►  OLTP DB  ──(materialize: DuckDB/Polars)──►  Parquet lake
                                                          (SQLAlchemy,                                 (data/interim,
  live demo app (Part II, seeded w/ fake data) ─────────► swappable)                                   DVC-tracked)
                                                              │                                              │
                                                     point reads/writes                              analytics + ML
                                                      (FastAPI/demo)                                (DuckDB / Polars)
```

- **OLTP = system of record** (the existing `db/` ORM layer — **kept**). Holds migrated history **and**
  live grievances. **Swappable DBMS** via `OLTP_DB_URL`: SQLite locally (the `grievance.db` we already
  built), PostgreSQL on AWS (container; RDS later) — just change the URL. SQLAlchemy (async) keeps it
  dialect-portable; **Alembic** for schema migrations; the `db/crud.py` repository is the engine-agnostic
  access layer.
- **OLAP = derived Parquet, no ORM.** A downstream **materialization** step (DuckDB reads the OLTP DB via
  its `sqlite`/`postgres` scanner → `COPY … TO` Parquet in `data/interim/`) feeds analytics, ML training,
  dashboards, and the demo's history browse/search. Query with DuckDB SQL / Polars.
- **Why this shape:** OLTP gives transactional row ops + one front door for history+live; Parquet gives
  fast columnar scans for ML/analytics without hammering the transactional DB. Reuses everything built.

### Confirmed decisions
- Topology: **dump → OLTP → (downstream) → Parquet**; live app later feeds the **same** OLTP DB (fake data
  for the demo). **Keep the existing `db/` ORM + `crud.py` + migration** — they are the OLTP layer.
- OLTP is **swappable** (SQLAlchemy + `OLTP_DB_URL`, Alembic, asyncpg/aiosqlite). OLAP Parquet is derived,
  no ORM.
- SQL migration supports **both** a raw-dump cold start and a live-MySQL sync.
- Doc-processing/ML pipeline source = the `grievance-pipeline` `document_pipeline` (NOT ORTPS).
- MLflow: local file-based tracking + registry; artifacts to S3/DVC remote.
- **Part II**: Next.js/React frontend; hybrid (rules + learned) routing; text **and** document input;
  heavy/GPU inference.
- **Testing policy**: every feature ships with real-code-path pytest tests, run before a phase is "done".
- DVC-track the raw dump + derived Parquet; the OLTP DB is operational (not DVC-tracked) — back it up to S3.

### Implementation status
- ✅ **Phase 0** — scaffolding & dependencies.
- ✅ **Phase 1** — OLTP layer: `db/models.py` (`Complaint` = full 56-col dump set + `tracking_id` + 4
  ingestion cols; `ActionHistory`; tracking tables), `db/session.py`, `db/crud.py`, `ingestion/schemas.py`
  (source→field map), merged `config.py`. 19 tests passing.
- ✅ **Phase 2** — cold-start migration `dump → OLTP` (`from_sql_dump` restore + `from_mysql` streaming
  load). Ran the FULL dataset: **1,371,288 complaints + 6,556,171 action history** (after the functional
  unique-index dedup; pre-dedup was 6,565,323) into `data/oltp/janasunani.db` (~10 min). Real-data +
  integration tests passing.
- ✅ **Phase 2b** — OLTP store **swappable**: `OLTP_DB_URL`, `asyncpg`, **Alembic** baseline
  (upgrade/downgrade verified on SQLite + Postgres), dialect-portable conflict-inserts. DB relocated to
  `data/oltp/janasunani.db`.
- ✅ **Phase 3** — **OLTP → Parquet materialization** (`olap/materialize.py` via DuckDB) + `olap/lake.py`
  read helpers + `materialize` DVC stage. Verified live: 1.37M + 6.56M rows → 481M + 475M Parquet in ~26s.
- ✅ **Phase 4** — document ingestion → S3: `s3service`, ingestion `client` (`with_retry` +
  `JanasunaniAPIClient`), and `DocumentService` (download → S3/local, status into OLTP). Console script
  `janasunani-ingest-documents`. 30 tests (moto + respx).
- 🔄 **Phase 5 (next)** — document processing pipeline (refold `document_pipeline`, DVC-track models).
- ⬜ **Phases 6–7** — MLflow+DVC tracking, CI/docs.
- ⬜ **Phases 8–12** (Part II) — inference, routing, serving (live → OLTP), frontend, deploy.

## Package structure

```
janasunani/
  config.py              # paths/logging + Settings (OLTP_DB_URL, MYSQL_URL, AWS/S3, API)        [DONE]
  db/                    # OLTP layer — SQLAlchemy (KEEP). Swappable via OLTP_DB_URL + Alembic.
    models.py session.py crud.py  (alembic/)                                                      [DONE]
  ingestion/
    schemas.py           # Pydantic source→field map + API DTOs                                   [DONE]
    __init__.py          # OFFICE/STATUS maps                                                      [DONE]
    client.py document_ingestion.py s3service.py orchestrator.py   # legacy API + docs→S3 (Phase 4)
  migration/             # cold-start dump + live MySQL sync -> OLTP
    from_mysql.py from_sql_dump.py                                                                 [DONE]
  olap/                  # downstream analytics — DuckDB + Parquet (NO ORM)
    materialize.py       #   OLTP DB -> Parquet (DuckDB sqlite/postgres scanner -> COPY)
    lake.py              #   read/query helpers over the Parquet lake (DuckDB/Polars)
  pipeline/              # document processing (OCR/PII/page-type/summarize/categorize)
  inference/ routing/ serving/ tracking/                          # Part II
data/
  raw/                   # Dump20250730.sql (raw input, DVC-trackable)
  oltp/                  # OLTP DB file in dev (SQLite); Postgres volume in deploy   [relocate grievance.db here]
  interim/               # OLAP Parquet (complaints.parquet, action_history.parquet) — DVC-tracked
models/                  # ML artifacts (DVC-tracked)
docs/ROADMAP.md          # this plan, mirrored
```

## Authoritative schema = the SQL dump  *(reference)*

3.2 GB `mysqldump` ([data/raw/Dump20250730.sql](data/raw/Dump20250730.sql)) of `sociomatics_ticket`
(5.7.44): `t_janasunani_etl_pre_data` (complaints, 56 cols) + `t_janasunani_etl_history_pre_data` (action
history, 6 cols). Messy source names → clean snake_case via the **one source→field map** (Pydantic aliases
in [ingestion/schemas.py](janasunani/ingestion/schemas.py)); `trackingId`↔`tracking_id` joins the tables.
This is the OLTP `complaints`/`action_history` schema; the Parquet lake is a faithful columnar copy.

## Testing policy (EVERY phase)
Pytest on the **real code path**, run + green before "done". Async paths use a real async engine; OLTP
repository tests run against **both SQLite and Postgres**; the materialization is tested by reading back the
Parquet. Cover counts, idempotency, malformed inputs, the source→field mapping, skip/dedup. Gate on
`uv run pytest` + `uv run ruff check .`.

---

# PART I — Foundation

## Phase 2b — Make OLTP swappable  🔄 next (small)
- `config.py`: rename `DB_URL` → `OLTP_DB_URL` (default `sqlite+aiosqlite:///data/oltp/janasunani.db`;
  deploy `postgresql+asyncpg://…`). Relocate the dev DB from `data/raw/grievance.db` → `data/oltp/`.
- Add `asyncpg`; add **Alembic** (`db/alembic/`) with a baseline migration generated from the ORM, so
  schema creation is engine-portable (replaces ad-hoc `create_all`).
- Keep `from_mysql.run_migration(target_db_url=…)` (already parameterized) — it now targets any engine.
- **Tests**: `db/crud.py` CRUD + the migration integration test run against **SQLite and Postgres** (test
  container); Alembic upgrade/downgrade on both.

## Phase 3 — OLTP → Parquet materialization (downstream)  ⬜
- `olap/materialize.py`: connect DuckDB, `INSTALL/LOAD sqlite` (or `postgres`), `ATTACH` the OLTP DB,
  `COPY (SELECT * FROM complaints) TO data/interim/complaints.parquet (FORMAT parquet)` and likewise for
  `action_history`. Engine-agnostic (works off `OLTP_DB_URL`). Console script `janasunani-materialize`.
- `olap/lake.py`: helpers to query the Parquet via DuckDB/Polars (the analytics + history-browse read path).
- `dvc.yaml`: a single `materialize` stage (OLTP DB → Parquet, DVC-tracked outs). The cold-start
  migration is **not** a DVC stage — it seeds the external OLTP store (run via `scripts/migrate.sh`); DVC
  only tracks file artifacts (the Parquet lake + models). Update README.
- **Tests**: seed a tiny OLTP DB, materialize, read the Parquet back → row counts + a couple values match;
  `lake.py` query helper returns expected rows.

## Phase 4 — Document ingestion → S3  ⬜
- Port `s3service.py`, `ingestion/client.py`, `document_ingestion.py`, `orchestrator.py`. Document-download
  status updates write to **OLTP** (row updates via `crud.py` — natural fit now). Bucket via config; dev-vs-
  prod on `settings.ENV`; console entry points.
- **Tests**: `moto`-mocked S3 — `upload_fileobj` called; OLTP status columns updated; URL edge cases.

## Phase 5 — Document processing pipeline  ⬜
- Refold `src/document_pipeline/**` → `janasunani/pipeline/**`; keep CLI, `PipelineConfig`, `STAGE_ORDER` +
  **lazy per-stage imports** (transformers-conflict fix). Populate `pipeline-core`/`ocr-deepseek`/
  `categorizer` optional dep groups **with hard pins** (DSI repo pins `transformers>=4.57,<5`, `numpy<2`,
  `opencv-python<4.12`); one Docker image per group. DVC-track `models/`.
- **Keep the pipeline's internal artifact DB** (`db.py`, `pages`/`documents` tables) — do NOT rewrite it
  onto the ORM. Add a small **exporter** that upserts final page/document outputs into OLTP (which then
  materializes to Parquet). Categorizer reads complaints from OLTP.
- **PII tagger is the most fragile port**: it expects legacy artifacts at
  `models/pii_tagger/outputs/pii_crf_model.pt` via a `sys.path` alias — DVC-track `models/pii_tagger/**`
  preserving that layout.
- GPU note: only DeepSeek OCR hard-requires CUDA (fails fast); summarizer/categorizer/PII fall back to
  CPU. DeepSeek's `trust_remote_code` may import `flash_attn` unconditionally — pin or force eager attn.
- **Tests**: per-stage unit tests on fixtures; format+pytesseract smoke run on the 2-file sample.

## Phase 6 — MLflow + DVC dual tracking  ⬜
- `tracking/mlflow_utils.py`: local backend, S3 artifacts; register models + tag each version with its DVC
  path + content hash. `dvc.yaml` stages mirror the flow.
- **Tests**: a logged run + registered version resolves to a real DVC artifact; `dvc dag` renders.

## Phase 7 — CI + docs  ⬜
- Wire `uv run pytest` + `uv run ruff` into the GitHub workflows; expand coverage; update `README`/`AGENTS`.

---

# PART II — Automation Prototype & Demo  ⬜

**Goal:** live single-grievance path — *raw input → extract → redact → classify → summarize → route →
persist to OLTP → view* — behind FastAPI + a Next.js UI. The live app writes into the **same** OLTP DB
(seeded with fake data for the demo); analytics/history read the Parquet lake.

```
janasunani/inference/service.py   # warm GrievanceProcessor (load models once; extract→redact→classify→summarize)
janasunani/routing/{rules,model,router}.py   # hybrid: rule backbone + learned scorer + confidence/fallback
janasunani/serving/api.py         # FastAPI: POST /grievance, GET /grievance/{id}, GET /history (lake)
frontend/                         # Next.js + React + Tailwind
deploy/                           # docker-compose (api, frontend, mlflow, oltp-postgres, proxy) + terraform/
```

## Phase 8 — Real-time inference core  ⬜
- Each pipeline stage gets a single-item `process(text|image_bytes)->dict` beside its batch path. Warm
  `GrievanceProcessor` loads models once from the MLflow registry; text skips OCR. **OCR engine is
  configurable** (the existing `ocr_engine` switch): DeepSeek when CUDA is present (GPU box up during
  demo windows), pytesseract fallback otherwise — the doc path never hard-fails without a GPU.
- **Tests**: one sample text + one PDF → structured result; heavy model-load mocked.

## Phase 9 — Routing engine (hybrid)  ⬜
- `routing/rules.py`: deterministic mapping from the `janasunani-mappings` master tables (category/
  subcategory + district → dept → office/designation + escalation). *(AGENTS.md gate: confirm read access.)*
- `routing/model.py`: learned router trained on the OLAP history (features → handling office); MLflow-
  registered. `routing/router.py`: combine + confidence/fallback.
- **Sequencing:** rules router ships **first** and is demo-sufficient on its own (confidence stubbed);
  the learned router lands only after the E2E demo path works, so the demo never blocks on training.
- **Tests**: rule lookups; learned-router top-k on held-out; combiner fallback.

## Phase 10 — Serving API + live wiring  ⬜
- `serving/api.py`: `POST /grievance` (text or file) → inference + routing → **persist a new grievance into
  OLTP** (via `crud.py`) → return result; `GET /grievance/{id}` + status update; `GET /history` browse/
  search via `olap/lake.py`. Models warm from MLflow; `/health`; CORS. **Seed fake live grievances**.
- **Tests**: API tests (TestClient) with mocked processor against a temp OLTP DB — submit→persist→fetch;
  history endpoint returns lake rows.

## Phase 11 — Demo frontend (Next.js)  ⬜
- Submit (text + upload) → staged view (extracted/redacted text, category/subcategory/dept, summary,
  routing + escalation + confidence) + a history browse/search view. Env-configurable API URL
  (`NEXT_PUBLIC_API_URL`); client-side fetch only — no auth, no SSR data plumbing.
- **Tests**: manual verification against the demo checklist (Playwright deferred post-demo; the
  pytest policy for the Python side is unchanged).

## Phase 12 — Demo integration & deployment  ⬜
- `deploy/docker-compose.yml` on the **CPU box**: `api` + `frontend` + `mlflow` + **`oltp` (Postgres)** +
  `proxy`; seed data; `make demo`. The GPU box runs the OCR container separately during demo windows
  (runbook: start GPU box → health check → demo → stop). Materialize Parquet on a schedule/one-off.
  Latency pass.
- **Verify**: fresh bring-up → submit a grievance in the browser → pipeline + routing renders and persists
  to OLTP; history view shows historical tickets.

---

# Cross-cutting — Infrastructure (Docker + S3, minimal managed services)

Self-host on **Docker**; **S3** is the only stateful AWS dependency. OLTP DB is **swappable** — Postgres
container for the demo (no RDS needed), repointable to RDS via `OLTP_DB_URL`.

- **Compute — two boxes** *(decided 2026-07-02; week 1 needs no GPU at all)*:
  - **CPU box (always on)** — t3.large-class, runs `docker-compose` (api + frontend + mlflow +
    oltp-postgres + proxy) + the migration/materialization one-offs. Postgres data on a named volume on
    EBS; nightly `pg_dump` → S3; never `docker compose down -v`.
  - **GPU box (on demand)** — g6.xlarge-class from a **Deep Learning AMI**, started only for batch OCR
    runs and demo windows, stopped otherwise (~$0.80–1.00/hr while up). Runs the `ocr-deepseek` one-off
    container (plain `docker run` — no compose needed there). Spot is fine for backfill (pipeline is
    per-file resumable); no batch queue — overkill for one maintainer.
  - S3 via EC2 IAM instance roles (no static keys) — `s3service` already uses the default boto3 chain.
- **Storage**: DVC remote `s3://dpic-dvc-cache/janasunani` (raw dump + Parquet lake + model artifacts);
  `s3://janasunani-documents-*` (documents); `s3://…/mlflow-artifacts`. OLTP DB → Postgres volume (SQLite
  file in dev), snapshotted to S3. **Don't DVC-track the big OLTP DB** — track dump + derived Parquet.
- **Images/CI**: GitHub Actions → GHCR; one image per dep group (core / ocr-deepseek / categorizer). GPU
  base for api/pipeline; slim Node for frontend.
- **IaC**: minimal Terraform (`deploy/terraform/`): EC2 + IAM role + security group + S3. App-level is compose.
- **Config/secrets**: per-service `.env` (+ `.env.example`) + IAM role for S3. No managed secret store.

## Key reuse / do-not-reinvent notes
- The existing `db/` ORM + `crud.py` + `from_mysql`/`from_sql_dump` ARE the OLTP layer — keep them; only
  generalize the target URL and add Alembic for the swap.
- DuckDB reads SQLite/Postgres directly (scanners) — the materialization needs no ORM and no hand-rolled export.
- Preserve the `pipeline.py` lazy-import-per-stage pattern; reuse stage code for batch + single-item inference.
- `s3service.S3Service` — reuse for ingestion **and** MLflow artifacts.
- Routing consumes the `janasunani-mappings` tables + OLAP history.

## Open items to confirm during implementation (non-blocking)
- ~~Exact S3 bucket name~~ → resolved: `janasunani-documents-main` (exists; the one `s3service` uses).
  `AWS_S3_BUCKET_NAME`/`janasunani-data-main` was vestigial — defined in config since the grievance
  backend but read by no code, and the bucket never existed — dropped from `config.py`. Nightly
  `pg_dump`s go to the existing `grievance-database-backups-main`; DVC artifacts (incl. the raw dump)
  to `dpic-dvc-cache`.
- AGENTS.md read access to `data/raw/janasunani-mappings/*` for routing rules.
- Whether live demo grievances reuse the `complaints` table (+ AI/routing columns) or a sibling table.
- Whether to DVC-track an OLTP "seed" snapshot for reproducible demos (vs regenerate from dump).
- **History freshness** *(decided)*: live grievances land in OLTP but only appear in `GET /history`
  (which reads the lake) after re-materialization. `GET /grievance/{id}` reads OLTP live; `/history` is
  historical — re-materialize nightly/one-off. Revisit a union view only if stakeholders ask.

## Reviewer input — RESOLVED 2026-07-02 (Fable review, pre-cloud-push)

The five open questions were answered after verifying the roadmap's claims against this repo and the
DSI pipeline source (`../2025-autumn-dpic`, branch `pipeline`). Decisions are folded into the sections
above; the short answers:

1. **AWS deployment.** Compose + S3 + Postgres-container is right — with a **two-box split** (CPU
   always-on, GPU on-demand; see Infrastructure). What breaks first, in likely order: the **full-scale
   6.5M-row migration over asyncpg** (only tested on tiny data — run `migrate.sh` on the box, never
   across the internet), asyncpg datetime/type strictness, DuckDB `postgres` scanner needing same-box
   locality + extension egress, compose hostname/.env drift.
2. **GPU pipeline.** On-demand g6.xlarge from a DLAMI; spot for backfill; **no batch queue**. Only
   DeepSeek OCR hard-requires CUDA (verified in source — everything else falls back to CPU). Risks:
   torch cu12x/driver match, `flash_attn` imports in `trust_remote_code`, and the currently-empty
   optional dep groups needing hard `transformers` pins (see Phase 5).
3. **Frontend.** 3 endpoints + `/health` is thin enough; sync `POST` with a generous timeout;
   `create-next-app` + Tailwind + shadcn/ui, two routes, client-side fetch only (see Phase 11).
4. **Scope.** Deferred: full historical OCR backfill (~200-doc curated sample instead) and Playwright.
   Kept: learned router + MLflow registry, sequenced so the demo never blocks on them (rules router
   first; MLflow wired in week 2 when models first need serving).
5. **Architecture.** The split is sound. Sharp edges now documented: the history-freshness gap (Open
   items), keep-the-pipeline's-internal-DB + OLTP exporter (Phase 5), PII-tagger legacy artifact
   layout (Phase 5), the `dvc repro`-is-SQLite-only caveat (structural decisions), and `config.py`'s
   `"None"`-string AWS defaults (fixed).

## End-to-end verification (whole system)
1. `uv sync`; `uv run pytest` + `uv run ruff check .` green.
2. Migration: dump → OLTP DB with expected counts; OLTP CRUD/migration tests pass on **SQLite + Postgres**.
3. Materialize: OLTP → `data/interim/*.parquet`; DuckDB/Polars read back matches OLTP counts.
4. Ingestion (`ENV=dev`): documents fetched; OLTP status columns updated.
5. Pipeline: format + OCR on the 2-file sample → page rows with `extracted_text`.
6. Tracking: MLflow run + registered model tagged with DVC hash; `dvc repro materialize` reproduces the lake.
7. Serving: `POST /grievance` (text + PDF) → result + routing **persisted to OLTP**; `GET /history` reads Parquet.
8. Frontend: browser demo runs raw-grievance → routed-result and shows history.
