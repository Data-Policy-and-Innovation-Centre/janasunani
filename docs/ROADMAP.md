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
  load). Ran the FULL dataset: **1,371,288 complaints + 6,565,323 action history** into `grievance.db`
  (~10 min). Real-data + integration tests passing.
- ✅ **Phase 2b** — OLTP store **swappable**: `OLTP_DB_URL`, `asyncpg`, **Alembic** baseline
  (upgrade/downgrade verified on SQLite + Postgres), dialect-portable conflict-inserts. DB relocated to
  `data/oltp/janasunani.db`.
- ✅ **Phase 3** — **OLTP → Parquet materialization** (`olap/materialize.py` via DuckDB) + `olap/lake.py`
  read helpers + `materialize` DVC stage. Verified live: 1.37M + 6.57M rows → 481M + 475M Parquet in ~26s.
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
- `dvc.yaml`: `migrate` (dump → OLTP) → `materialize` (OLTP → Parquet, DVC-tracked outs). Update README.
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
  `categorizer` optional dep groups; DVC-track `models/`. Outputs (`pages`/`documents`) land in OLTP and/or
  get materialized to Parquet. Categorizer reads complaints from OLTP.
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
  `GrievanceProcessor` loads models once from the MLflow registry; text skips OCR, documents run GPU OCR.
- **Tests**: one sample text + one PDF → structured result; heavy model-load mocked.

## Phase 9 — Routing engine (hybrid)  ⬜
- `routing/rules.py`: deterministic mapping from the `janasunani-mappings` master tables (category/
  subcategory + district → dept → office/designation + escalation). *(AGENTS.md gate: confirm read access.)*
- `routing/model.py`: learned router trained on the OLAP history (features → handling office); MLflow-
  registered. `routing/router.py`: combine + confidence/fallback.
- **Tests**: rule lookups; learned-router top-k on held-out; combiner fallback.

## Phase 10 — Serving API + live wiring  ⬜
- `serving/api.py`: `POST /grievance` (text or file) → inference + routing → **persist a new grievance into
  OLTP** (via `crud.py`) → return result; `GET /grievance/{id}` + status update; `GET /history` browse/
  search via `olap/lake.py`. Models warm from MLflow; `/health`; CORS. **Seed fake live grievances**.
- **Tests**: API tests (TestClient) with mocked processor against a temp OLTP DB — submit→persist→fetch;
  history endpoint returns lake rows.

## Phase 11 — Demo frontend (Next.js)  ⬜
- Submit (text + upload) → staged view (extracted/redacted text, category/subcategory/dept, summary,
  routing + escalation + confidence) + a history browse/search view. Env-configurable API URL.
- **Tests**: a couple of Playwright happy-path checks.

## Phase 12 — Demo integration & deployment  ⬜
- `deploy/docker-compose.yml`: `api` (GPU) + `frontend` + `mlflow` + **`oltp` (Postgres)** + `proxy`; seed
  data; `make demo`. Materialize Parquet on a schedule/one-off. Latency pass.
- **Verify**: fresh bring-up → submit a grievance in the browser → pipeline + routing renders and persists
  to OLTP; history view shows historical tickets.

---

# Cross-cutting — Infrastructure (Docker + S3, minimal managed services)

Self-host on **Docker**; **S3** is the only stateful AWS dependency. OLTP DB is **swappable** — Postgres
container for the demo (no RDS needed), repointable to RDS via `OLTP_DB_URL`.

- **Compute**: one GPU EC2 box runs `docker-compose` (api + frontend + mlflow + oltp-postgres + proxy);
  batch/pipeline + materialization run as one-off containers. S3 via an EC2 IAM instance role (no static keys).
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
- Exact S3 bucket name (`janasunani-documents` vs `…-main`).
- AGENTS.md read access to `data/raw/janasunani-mappings/*` for routing rules.
- Whether live demo grievances reuse the `complaints` table (+ AI/routing columns) or a sibling table.
- Whether to DVC-track an OLTP "seed" snapshot for reproducible demos (vs regenerate from dump).

## End-to-end verification (whole system)
1. `uv sync`; `uv run pytest` + `uv run ruff check .` green.
2. Migration: dump → OLTP DB with expected counts; OLTP CRUD/migration tests pass on **SQLite + Postgres**.
3. Materialize: OLTP → `data/interim/*.parquet`; DuckDB/Polars read back matches OLTP counts.
4. Ingestion (`ENV=dev`): documents fetched; OLTP status columns updated.
5. Pipeline: format + OCR on the 2-file sample → page rows with `extracted_text`.
6. Tracking: MLflow run + registered model tagged with DVC hash; `dvc repro` (migrate → materialize → …).
7. Serving: `POST /grievance` (text + PDF) → result + routing **persisted to OLTP**; `GET /history` reads Parquet.
8. Frontend: browser demo runs raw-grievance → routed-result and shows history.
