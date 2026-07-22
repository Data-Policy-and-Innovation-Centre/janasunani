# janasunani Roadmap

## Context

`janasunani` is becoming "Janasunani 2.0," the unified, AI-powered grievance redressal system for
Odisha. End goal: a **full automation prototype** — take a **raw grievance** (typed text or an uploaded
scanned document), **extract** text, **redact PII**, **classify** (category / subcategory / department),
**summarize**, and **route** it to the responsible office — quickly — with a polished **Next.js demo**.

The system is built **phase by phase** (Phases 0–15; see the phase list and detail below). The earlier
phases lay the **foundation** — consolidate work from two earlier repos into one `janasunani/` package and
load the data: the cold-start SQL migration into a swappable OLTP store, the downstream Parquet
materialization, document ingestion → S3, the six-stage document-processing/ML pipeline, and model tracking.
The middle phases add the **automation prototype** — real-time single-grievance inference, a hybrid
(rules + learned) routing engine, FastAPI serving that writes live grievances into the **same** OLTP DB, and
the Next.js demo UI. The later phases are built **API-contract-first** so the demo (the visible deliverable)
never sits at the tail of a serial chain.

**Part III — post-demo maturity (Phases 13–15, planned).** After the status-quo demo, the plan
matures the models along a spine — *status-quo → improved (Odia-first, Indic) models → human-in-the-loop
online learning* — on a **modularity foundation** (a stage registry with configurable order, a model
registry, and MLflow-backed hot-switching) and adds a modern **governance-intelligence layer** (on-box
embeddings + spike/theme detection) robust to native **and romanized Odia** and broken English. This
extends — does not replace — the tabled Phase 5 (MuRIL retrain / PII-NER upgrade), Phase 6 (MLflow
registry, whose adoption trigger this reaches), and Phase 9 (empirical crosswalk / learned scorer) goals.
See the two new cross-cutting sections and Phases 13.0/13/14/15 below.

This plan is the source of truth, **mirrored into the repo at `docs/ROADMAP.md`** (kept in sync).

## Project snapshot — state as of 2026-07-10 (for a fresh reviewer)

> This section exists so a reviewer with **no prior context** can understand where
> the project actually stands, reproduce it, and give input on the plan below.
> The week-by-week and per-Phase items below carry the ✅/🔄/⬜ status.

**Foundation (Phases 0–5): built, tested, merged to `main`, and running in the
cloud.** The full cold-start migration, Parquet lake, S3 ingestion, and the
six-stage document pipeline (incl. the Presidio PII rebuild) are done; production
Postgres holds the migrated data on the always-on CPU box (nightly `pg_dump` →
S3), and the on-demand GPU box was shaken down for real (DeepSeek OCR) on
2026-07-04. **The automation-prototype phases (8–12) are in progress**, built
API-contract-first: the Phase 10 serving skeleton, live persistence/history,
Phase 9 rules router, and Phase 8 warm inference processor are built, while the
MLflow registry and a first-cut DPIC-branded Next.js frontend remain in
progress (see the per-Phase status below). The default API deliberately remains
mocked; the opt-in `janasunani-api-live` command runs local real models behind
the same frozen contract.

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
  **⚠ DSI team disbanded (2026-07-03) and their Box access is gone.** Consequences:
  the PII CRF weights + its 20k labeled training CSV are **unrecoverable** (Box-only);
  everything else was salvaged — the page-type ViT and the MuRIL categorizer were
  still public on HF (an orphaned org + a student's personal account) and are now
  **mirrored into our DVC remote** (`models/page_type_classifier/`, `models/categorizer/`,
  incl. the label encoder), alongside the format-classifier pickle. The PII training
  loop survives in the DSI repo's git history (commit `db4885f`). Runtime must depend
  only on our mirrors, never on their accounts. Their final technical report (PDF, user-held)
  preserves the only measured baselines: format classifier 75.71% avg acc; DeepSeek OCR 77.89%
  pass-rate on quality heuristics (watch its repetition-collapse failure mode); page-type ViT
  test acc 0.67 (plain ViT beat ViT+BERT/Longformer; 1.5K labeled pages); MuRIL categorizer
  0.7104 acc — fine-tuned on only 6,598 grievances, strengthening the retrain-on-our-1.37M
  upgrade; PII coverage 80.56% overlap / 50% exact. Summarizer usefulness by page type
  (qualitative 0–3): Text-Only 1.9, Letter 1.3, Forms 0.85, Bills/IDs ~0 — the measured basis
  for page-type gating.
- **Cold-start data** — the SQL dump `data/raw/Dump20250730.sql` (3.2 GB), a
  `mysqldump` of MySQL DB `sociomatics_ticket` (only the two ETL tables). The dump
  — not any branch — is the authoritative schema.
- **Explicitly out of scope:** the ORTPS analysis pipeline on `grievance`'s
  `refactor_cleaning` / `feb12_presentation` branches — a specific ORTPS
  application we are not using. Don't confuse it with the in-scope
  `document_pipeline`.

**What's built and verified (local + cloud)**
- **Cold-start migration `dump → OLTP`** — ran the full 3.2 GB `mysqldump` end to
  end → **1,371,288 complaints + 6,556,171 action-history rows**, both locally
  (SQLite at `data/oltp/janasunani.db`) and on **cloud Postgres** (Week-1 run,
  exact same counts). Load is deterministic + idempotent (byte-reproducible OLTP
  DB across runs).
- **OLTP is engine-swappable** via `OLTP_DB_URL` (SQLite locally / Postgres on
  AWS). Schema managed by **Alembic**; upgrade/downgrade verified on both engines.
  Conflict-inserts are dialect-portable (`_dialect_insert`).
- **Materialization `OLTP → Parquet`** (DuckDB scanner) — 1.37M + 6.56M rows →
  ~481M + ~475M Parquet in ~26 s. This is the **one DVC-tracked transform**.
- **Document ingestion → S3** — `s3service` + ingestion `client` (`with_retry`) +
  `DocumentService` (download → S3/local, status written back to OLTP).
- **84 pytest tests green on the real code path** (async engine, moto S3, respx
  HTTP, serving TestClient); `ruff` clean; **CI green** (Postgres service container).

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

**Immediate real-world plan (single maintainer, week-by-week; re-sequenced 2026-07-02
after the Week-1 cloud run — see "Reviewer input" below for the original rationale)**

*Week 1 — cloud foundation. ✅ DONE 2026-07-02.* Terraform CPU box (t3.xlarge for the
migration, downsized to t3.large after) + IAM instance role; dump via `dvc pull`; full
migration → cloud Postgres with **exact counts 1,371,288 / 6,556,171**; materialize via
the DuckDB postgres scanner (same counts, ~24 s on-box); nightly `pg_dump` → S3 cron
(first 623 MB backup verified); pytest gate green on the box. Three cloud-only kinks
found + fixed with regression tests: the MySQL first-boot auth race in `migrate.sh`,
NUL bytes in dump text (Postgres rejects; now stripped in the schema layer), and the
dedup index exceeding Postgres's ~2.7 KB btree entry cap (remark now md5-digested on
Postgres via a dialect-compiled expression + Alembic revision). *Still pending:*
ingestion smoke — blocked on the Janasunani API credentials.

*Week 2 — Phase 5 pipeline + GPU, plus the pulled-forward plumbing.*
1. ✅ External unblocks: `janasunani-mappings` confirmed + DVC-tracked. API credentials
   parked (pulls unavailable). ~~PII weights from Box~~ → unrecoverable, re-planned
   (see Phase 5); at-risk HF models mirrored into our DVC remote 2026-07-03.
2. ✅ Minimal `deploy/docker-compose.yml`: `oltp` declared and adopted live on the box
   (zero data movement; external volume). Grows service-by-service from here.
3. ✅ Minimal CI (Phase 7 split): Postgres service container added — the Postgres-path
   tests now run in CI instead of skipping.
4. ✅ Refold `document_pipeline` → `janasunani/pipeline/`: done, incl. dep groups with
   the uv-conflicts split, the OLTP exporter (pages/documents → OLTP → lake), the
   `pipeline-sample` DVC stage over 2 real documents, and the pytesseract smoke
   (Odia extraction verified after adding the `ori` traineddata).
5. ✅ **PII stage rebuilt** on Presidio (see Phase 5) — the default full-stage run
   works again; the pipeline-sample DVC stage now includes redaction.
6. ✅ GPU box, built and shaken down (first real run 2026-07-04): count-toggled
   g6.xlarge in `deploy/terraform/gpu.tf` (Deep Learning **Base** AMI; AZ pinned to
   ap-south-1a — g6 not offered in 1c), `scripts/gpu_smoke.sh` (format via
   `pipeline-core` env, DeepSeek OCR via `ocr-deepseek` env, same SQLite), and the
   repetition-collapse guard (repeated-trigram share > 0.5 — generalizes DSI's
   top-trigram rule, which only catches single-word loops). On-box smoke: model
   loads on the L4, 3/5 sample pages extract; the guard fired on a looping Odia
   handwritten page and was **verified a true positive** (repeated share 0.55 where
   DSI's metric read 0.04). The predicted `trust_remote_code` surprise was
   **torchvision**, not flash_attn — now in the `ocr-deepseek` extra. Learned:
   DeepSeek is English-only in practice (Odia comes out script-confused) — the
   backfill routes DeepSeek at `--filter-language English`, pytesseract+`ori` at
   Odia, matching DSI. Box destroyed after the run (create/destroy per use).
7. ⬜ **PII eval before sample backfill**: label ~100-200 real pages and beat the
   legacy 80.56% any-overlap baseline before exporting real-page outputs.
   *Tooling merged (PRs #10/#11/#13) and labeling material generated
   2026-07-06: 50-doc English bundle → 85 pre-annotated pages / 573 draft
   spans (`data/output/pii_gold_draft_n50.jsonl` + `.review.txt`, local,
   regenerable via the PII-gold labeling tooling). Remaining: the intern labeling
   pass → evaluate (record the score in the DVC-tracked `eval_results.jsonl`, cf. Phase 6) →
   DVC-promote to `data/external/pii_gold.jsonl`.*
8. ⬜ **Sample backfill only** (~200 curated docs; pick STANDARD storage class — parts
   of the documents bucket are GLACIER-archived), not the full corpus → OLTP → lake.
9. ⬜ Model tracking: **DVC is the tracker through the demo**; MLflow registration is deferred to the
   retrain phase (see Phase 6). Near-term — at evaluation time — log metrics to a DVC-tracked
   `data/output/eval_results.jsonl`, seeded with the DSI baselines.

*Week 3 — API-contract-first (re-ordered: the frontend must not sit at the tail of a
serial chain with zero float).*
1. ✅ **Phase 10 skeleton first** (2026-07-06): `janasunani/serving/` — the three
   endpoints + `/health` + CORS behind a `serving` extra (`janasunani-api` CLI);
   `schemas.py` is the frozen contract (field names mirror the pipeline DB /
   `PIISpan` / lake columns so wire-up is plumbing, not renaming); processor,
   history, and result store are injectable seams (`create_app`) — mock today,
   Phase 8/9 inference + lake + `live_grievances` OLTP later. TestClient tests
   pin the contract and must pass unchanged after wire-up.
2. **Phase 11 scaffold immediately after**, built against the mocked contract — the
   demo's visible deliverable gets Weeks 3–4 of iteration instead of a cramped tail.
3. Phases 8 + 9 fill in behind the stable contract: single-item inference, then the
   rules router (learned router only after the E2E path works).
4. Phase 10 wire-up = swap the mock for the real processor + persist to OLTP.

*Week 4 — integrate + demo (Phase 12).* Full compose on the CPU box; demo runbook:
start GPU box → health check → demo → stop GPU box; latency pass; stakeholder dry run.

## Storage architecture — OLTP front, Parquet/OLAP downstream

```
  MySQL dump ──(migrate: from_sql_dump → from_mysql)──►  OLTP DB  ──(materialize: DuckDB/Polars)──►  Parquet lake
                                                          (SQLAlchemy,                                 (data/interim,
  live demo app (Phases 8–12, seeded w/ fake data) ─────► swappable)                                   DVC-tracked)
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
- **Automation (Phases 8–12)**: Next.js/React frontend; hybrid (rules + learned) routing; text **and**
  document input; heavy/GPU inference.
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
- ✅ **Phase 5** — document processing pipeline (refolded `document_pipeline`, DVC-tracked models, Presidio
  PII rebuild, GPU shakedown 2026-07-04).
- 🔄 **Phase 6** — MLflow slim: helpers on branch `feat/mlflow-slim-registry` (ongoing). ✅ **Phase 7 CI** —
  green with Postgres service container; docs stay late.
- 🔄 **Phases 8–12** — built API-contract-first; the real backend path is now available opt-in while
  frontend/deployment integration remains:
  - **Phase 8** ✅ real-time inference core — 8A warm summarizer, 8B standalone OCR, and 8C warm
    processor/live CLI complete. Local DVC model artifacts are required and startup fails closed.
  - **Phase 9** 🔄 routing — deterministic rules/mappings route the live processor now; the learned
    OLAP-history crosswalk remains follow-up work.
  - **Phase 10** ✅ serving — frozen mock API, live persistence, lake-backed history, and opt-in real
    processor wiring are built. The module-level API remains mock by design.
  - **Phase 11** 🔄 frontend — first cut on `feat/frontend-demo` (DPIC-branded, against the mock).
  - **Phase 12** ⬜ demo integration & deployment.
  - Docs: `chore/handoff-doc-links` (ongoing). *(Dead: `backend-plan-unsplit` — an ancestor of main, no diff.)*
- ⬜ **Phases 13–15 — Part III post-demo maturity (planned 2026-07-22).** The model-maturity
  spine (*status-quo → improved Odia-first models → human-in-the-loop online learning*) on a
  modularity foundation (**13.0** stage/model registry + MLflow), plus the governance-intelligence
  layer (**15**). Extends the tabled Phase 5/6/9 goals; not started. See the two new cross-cutting
  sections + Phase 13.0/13/14/15 detail below.

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
  inference/ routing/ serving/ tracking/                          # automation (Phases 8–12)
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

# Phase-by-phase detail

*Foundation phases (2b–7) first, then the automation phases (8–12). Phases 0–2/5
are done and summarized under Implementation status above; the detail below covers
the rest.*

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

## Phase 5 — Document processing pipeline  ✅ *(merged to main; Presidio PII rebuild, GPU shakedown 2026-07-04)*
- Refold `src/document_pipeline/**` → `janasunani/pipeline/**`; keep CLI, `PipelineConfig`, `STAGE_ORDER` +
  **lazy per-stage imports** (transformers-conflict fix). Populate `pipeline-core`/`ocr-deepseek`/
  `categorizer` optional dep groups **with hard pins** (DSI repo pins `transformers>=4.57,<5`, `numpy<2`,
  `opencv-python<4.12`); one Docker image per group. DVC-track `models/`.
- **Keep the pipeline's internal artifact DB** (`db.py`, `pages`/`documents` tables) — do NOT rewrite it
  onto the ORM. Add a small **exporter** that upserts final page/document outputs into OLTP (which then
  materializes to Parquet). Categorizer reads complaints from OLTP.
- **PII stage — REBUILT on Presidio ✅ (2026-07-03)**: same `extracted_text → redacted_text`
  interface, internals now Presidio analyzer/anonymizer + custom Indian recognizers (mobile,
  Aadhaar, PAN) + spaCy NER for names; typed tokens ([NAME]/[PHONE]/[AADHAAR]/[PAN]/[EMAIL]).
  Improvements over the lost CRF: no 512-token truncation window, mixed "English, Odia" pages
  covered (equality filter skipped them), SQL-paged batches, explainable hits. Legacy
  `models/pii_tagger/` deleted. Rule unchanged: never send citizen text to external APIs.
  **Baseline to beat (DSI technical report, their only surviving eval):** legacy coverage was
  **80.56% any-overlap / 50.0% exact-span** on 106 held-out sentences; I-PII recall 0.575 (it
  truncated multi-token spans), sentence-level eval (page-level 512-token loss never measured),
  untyped spans, trained on ~21.9K tokens. Structured ids (phone/Aadhaar/PAN/email) are now
  deterministic; the open question is names — spaCy `en_core_web_sm` vs their in-domain
  fine-tune. **TODO (pre-backfill): label ~100–200 real pages and measure Presidio coverage
  against the 80.56% baseline; if PERSON recall lags, upgrade the NER (trf model or
  Indian-names recognizer).**
- **Model provenance rule**: every model the pipeline loads comes from **our DVC remote** (mirrored
  ViT page-type, MuRIL categorizer + label encoder, format pickle) or a large public repo
  (facebook/bart-large-cnn, deepseek-ai/DeepSeek-OCR) — no runtime dependency on DSI-controlled
  accounts. Point stages at the local `models/` paths, not HF repo ids.
- **Page-type matters**: the summarizer feeds ONLY pages of the target page-type class into the
  summary (and the categorizer consumes that text) — it's the signal/noise gate (letters/forms in,
  IDs/covers out), a hard upstream dependency of routing quality, not an optional stage.
- **Categorizer upgrade path (post-demo)**: retrain MuRIL on our own OLTP (1.37M complaints with
  ground-truth categories) — likely better than the student model trained on a slice; the mirrored
  model carries the demo.
- GPU note: only DeepSeek OCR hard-requires CUDA (fails fast); summarizer/categorizer/PII fall back to
  CPU. DeepSeek's `trust_remote_code` may import `flash_attn` unconditionally — pin or force eager attn.
- **Index rule (Week-1 lesson, generalized):** the `pages`/`documents` tables carry unbounded text
  (`extracted_text`, summaries). On Postgres, btree entries cap at ~2.7 KB — **never put an unbounded
  text column in a btree key or unique index**; hash it (see `dedup_remark` in `db/models.py`) or key on
  ids. Applies to the OLTP exporter's Alembic revisions.
- **Tests**: per-stage unit tests on fixtures; format+pytesseract smoke run on the 2-file sample.

## Phase 6 — model tracking (DVC now; MLflow deferred to retrain)  🔄 *(slim helpers on branch `feat/mlflow-slim-registry`)*
- **Reality check (2026-07-10):** the live path (`build_processor`) resolves models straight from the
  DVC-tracked `models/` dir; nothing in `inference/`/`serving/` touches MLflow. Over DVC (versioned
  bytes, git-pinned via `dvc.lock`, S3 remote), the MLflow **registry** adds no runtime value *today* —
  the models are static, externally-sourced, and loaded by path. So model **registration is deferred to
  the retrain phase**, when there are candidate versions to compare/promote; it is **not** a demo
  prerequisite (DVC already delivers demo reproducibility).
- **Tracking starts paying off at *evaluation*, not training.** The first activity that produces
  comparable numbers is scoring models on gold/held-out data (PII eval; categorizer/ViT scoring). For
  the first handful of eval runs, record metrics in a **DVC-tracked eval-results file**
  (`data/output/eval_results.jsonl`: model version + gold-data version + metrics) — simpler than a
  tracking server, same compare-against-baseline ability. **Seed row = the DSI baselines** (MuRIL 0.71,
  page-type ViT 0.67, PII 80.56% overlap) so retrains have a versioned, machine-readable baseline rather
  than report-PDF prose.
- **Adopt MLflow when run volume demands it** — many retrain candidates → interactive comparison +
  registry stages (`Staging`/`Production`, so serving asks for "Production" instead of a hardcoded DVC
  path). The slim helpers stay as-is until then: `configure_tracking`/`ensure_experiment`/
  `log_model_artifact` with `dvc.path`/`dvc.hash` version tags, `MLFLOW_TRACKING_URI`/
  `MLFLOW_ARTIFACT_URI` config (branch `feat/mlflow-slim-registry`, reviewed/tested, not yet merged).
- **Trigger reached in Part III.** Phase 13 (Indic model swaps) and Phase 14 (online-learning
  candidates) produce exactly the retrain-candidate volume this bullet names as the adoption
  condition. **Phase 13.0 (F3)** wires these slim helpers into the model-resolution path
  (registry-backed, `Production`-by-default) — fulfilling, not contradicting, this deferral. See the
  "Modularity, switchability & MLflow" cross-cutting section below.
- **Tests**: a logged run + registered version resolves to a real DVC artifact; `dvc dag` renders.

## Phase 7 — CI + docs  ⬜ *(split 2026-07-02)*
- **CI — pulled forward to Week 2**: a GitHub Actions job running `uv run pytest` + `uv run ruff` with a
  throwaway Postgres service container (so the Postgres-path tests run, not skip). Cheap, and fixes stop
  shipping gated only by locally-run tests.
- **Docs — stays late**: expand coverage; update `README`/`AGENTS`.

## Automation phases (8–12) — demo path & build order

**Goal:** live single-grievance path — *raw input → extract → redact → classify → summarize → route →
persist to OLTP → view* — behind FastAPI + a Next.js UI. The live app writes into the **same** OLTP DB
(seeded with fake data for the demo); analytics/history read the Parquet lake.

**Build order (re-sequenced 2026-07-02): API-contract-first, not 8→9→10→11.** The phases below are
numbered by component, but they are *built* around the API contract so the frontend — the demo's visible
deliverable — never sits at the tail of a serial chain: Phase 10 **skeleton** (endpoints + mocked
processor, ~a day) → Phase 11 scaffold against it → Phases 8 + 9 fill in behind the contract → Phase 10
wire-up (swap mock for real).

```
janasunani/inference/service.py   # warm GrievanceProcessor (load models once; extract→redact→classify→summarize)
janasunani/routing/{rules,model,router}.py   # hybrid: rule backbone + learned scorer + confidence/fallback
janasunani/serving/api.py         # FastAPI: POST /grievance, GET /grievance/{id}, GET /history (lake)
frontend/                         # Next.js + React + Tailwind
deploy/                           # docker-compose (api, frontend, mlflow, oltp-postgres, proxy) + terraform/
```

## Phase 8 — Real-time inference core  ✅ *(8A + 8B + 8C complete)*
- ✅ **8A:** `Summarizer` is a warm single-item wrapper over public
  `facebook/bart-large-cnn`; short inputs avoid an unnecessary generation call.
- ✅ **8B:** `ocr_document()` renders and OCRs one uploaded document without the
  batch artifact DB, preserves per-page text/type metadata, rejects quality
  collapse, and reports truncation at the synchronous page cap.
- ✅ **8C:** `PipelineGrievanceProcessor` warms the local DVC-mirrored page-type
  and MuRIL models, BART, then Presidio once. Typed text skips OCR; documents use
  pytesseract and feed only non-empty Letter/Form/Application/Text Only pages to
  classification and summarization while preserving all accepted OCR text in
  the response. Non-English-compatible text is `Uncategorized`; routing uses
  `DEFAULT_ROUTER` (`rules`/`fallback`, never `mock`). Corrupt, unsupported,
  blank, quality-rejected, truncated, and irrelevant-only documents fail with
  a typed input error surfaced as HTTP 422.
- ✅ **Strict live server:** `janasunani-api-live` loads local models directly,
  fails startup if dependencies/artifacts are missing, serves `LakeHistory`,
  and uses OLTP persistence only when `OLTP_DB_URL` is explicitly set. The
  module-level `janasunani.serving.api:app` and `janasunani-api` stay mocked.
  DeepSeek (separate process) and MLflow runtime model resolution remain
  follow-up work.
- **Tests:** dependency-injected text/PDF processor paths, page gating,
  invalid/unsafe input rejection, warm reuse, strict builder/store selection,
  unchanged serving contract, and an opt-in real-model smoke.

## Phase 9 — Routing engine (hybrid)  🔄 *(components on branches; not yet wired into serving)*
- **Rules layer built** (`routing/rules.py`, branches `feat/rules-router` → `feat/rules-router-mappings`):
  a deterministic `RuleRouter`/`MappingRouter` producing the frozen `RoutingResult` shape. Reviewed, tested.
- **Crosswalk decision (2026-07-08): learn category→department FROM THE DATA, not the master tables.**
  The `janasunani-mappings` masters (`m_admin_category` / `m_admin_subcategory` / `m_admin_hierarchy_value`
  = departments / `t_admin_escalation`) give clean category/subcategory + department + escalation lists, and
  the dept↔escalation join is real (verified by content: dept 5 "Energy" → the four Odisha DISCOM CEOs). But
  they carry **no category→department link** — `intCategoryGrp` is NULL on all 62 categories — so only ~4/62
  categories resolve by exact name-match. That gap is not hand-authorable honestly; the source of truth is
  the OLAP history itself, where **83.1% of the 1.37M complaints carry BOTH `category` and the `dept` they
  were actually routed to**. Measured argmax accuracy of an empirical crosswalk over that history:
  **category → dept 60.9% · +subcategory 67.5% · +subcategory+district 72.8%** (≈ the legacy MuRIL 0.71).
  Build the crosswalk as `(category, subcategory, district) → argmax(dept, office)` carrying **support count
  + concentration as real confidence** (not a stubbed number), with a **fallback ladder**
  (cat+subcat+district → cat+subcat → cat → generic) that naturally covers the ~27/62 master categories with
  little/no history. This **replaces the name-match hack** and becomes the rules layer.
- `routing/model.py`: learned router trained on the OLAP history (features → handling office); MLflow-
  registered — layered **above** the empirical crosswalk. `routing/router.py`: combine + confidence/fallback.
- **Live wiring complete for the deterministic layer** — `janasunani-api-live`
  routes through `DEFAULT_ROUTER`; only the default mock command returns
  `method:"mock"`.
- **Sequencing:** the empirical crosswalk ships **first** (demo-sufficient, real data-derived confidence);
  the learned router lands only after the E2E demo path works, so the demo never blocks on training.
- **Tests**: crosswalk lookups + fallback ladder; learned-router top-k on held-out; combiner fallback.
- **Part III forward-pointer:** the empirical crosswalk above is landed/completed as **Phase 13.5**
  (the "improved routing" step); the learned `routing/model.py` is **generalized in Phase 14** from a
  routing-only scorer into a correction-fed **online-learning loop across all three decision surfaces**
  (classification / summary / routing), reusing the Phase-15 embedding index as its fast-adaptation
  layer. The OVB caveat on the disposal-time/benefit objective (Phase 14) applies to routing only.

## Phase 10 — Serving API + live wiring  ✅ *(default mock + opt-in live)*
- ✅ **Skeleton (merged to main, PR #12):** all endpoints + `/health` + CORS with a **mocked processor**
  returning the real response shapes — the stable `serving/schemas.py` contract Phases 8/9/11 build against.
- ✅ **Live persistence:** the
  `live_grievances` sibling OLTP table + Alembic revision + injectable `ResultStore`
  (`InMemory`/`Database`); `POST /grievance` persists, `GET /grievance/{id}` reads it back.
- ✅ **Real history:** `LakeHistory`
  serves `GET /history` from the real 1.37M-row Parquet lake (`olap/lake.py`) instead of `MockHistory`.
  The module-level app uses it with the mock processor; the live CLI uses it
  with the real processor.
- ✅ **Opt-in wire-up:** `janasunani-api-live` mounts real inference/routing.
  Local DVC artifacts are loaded directly for Phase 8C; MLflow runtime
  resolution and seeded fake live grievances remain follow-up work.
- **Tests**: API tests (TestClient) with mocked processor against a temp OLTP DB — submit→persist→fetch;
  history endpoint returns lake rows.

## Phase 11 — Demo frontend (Next.js)  🔄 *(first cut built 2026-07-08 — ONGOING on branch `feat/frontend-demo`)*
- 🔄 **First cut built** (branch `feat/frontend-demo`, DPIC-branded): Next.js 16 App Router + TypeScript +
  Tailwind v4. Submit route (text/upload → staged result cards: extracted/redacted text + typed PII token
  badges, classification, summary, routing w/ escalation + confidence) + History browse/search route. Types
  mirror `serving/schemas.py`; `NEXT_PUBLIC_API_URL`; client-side fetch only. Brand tokens (maroon `#8B1524`,
  Calibri) read from the installed `dpic` package. `npm run lint`/`build` green; endpoints verified live
  against the mock API. Not merged. **Still mock end-to-end** (mock processor + `MockHistory` on `main`) —
  the UI shape is real; classification/redaction/history become real at the Phase 8/10 wire-up.
- Scaffolded right after the Phase 10 skeleton and built against the mocked contract, so it gets
  Weeks 3–4 of iteration instead of a cramped tail. Submit (text + upload) → staged view
  (extracted/redacted text, category/subcategory/dept, summary, routing + escalation + confidence) +
  a history browse/search view. Env-configurable API URL (`NEXT_PUBLIC_API_URL`); client-side fetch
  only — no auth, no SSR data plumbing.
- **Tests**: manual verification against the demo checklist (Playwright deferred post-demo; the
  pytest policy for the Python side is unchanged).

## Phase 12 — Demo integration & deployment  ⬜ *(integration only — compose grows incrementally)*
- `deploy/docker-compose.yml` starts in **Week 2** with just `oltp` (replacing the ad-hoc `docker run`)
  and gains each service as it's born (`api` → `frontend` → `proxy`), so this phase is
  integration + runbook + latency — not the first time the runtime composition exists in the repo.
  **`mlflow` is intentionally NOT part of the demo compose** — it lands in **Part III / Phase 13.0
  (F3)** when there are retrain candidates to register/promote (see Phase 6 trigger).
- On the **CPU box**: full stack + seed data; `make demo`. The GPU box runs the OCR container separately
  during demo windows (runbook: start GPU box → health check → demo → stop). Materialize Parquet on a
  schedule/one-off. Latency pass.
- **Verify**: fresh bring-up → submit a grievance in the browser → pipeline + routing renders and persists
  to OLTP; history view shows historical tickets.

---

# Part III — post-demo maturity (Phases 13–15)  ⬜ *(planned 2026-07-22)*

After the status-quo demo, two parallel workstreams mature the system and open it to
third-party-government interest (the DPI-export framing). **Workstream A** — a model-maturity
spine: *status-quo → improved (Odia-first, Indic) models → human-in-the-loop online learning*,
built on a **modularity foundation** (Phase 13.0). **Workstream B** — a modern
**governance-intelligence layer** (Phase 15), on-box, robust to native **and romanized Odia**
and broken English. This **extends** the tabled Phase 5 (MuRIL retrain / PII-NER upgrade),
Phase 6 (MLflow adoption trigger) and Phase 9 (empirical crosswalk / learned scorer) goals —
no deletions, no conflicts.

**Motivating gaps.** (1) *Odia is a second-class path* — Odia (native + romanized) and broken
English are OCR'd but then degraded by English-only gates downstream (`categorizer/stage.py`
`_is_english`, `pii_tagger.py` `WHERE language LIKE '%English%'`, `inference/service.py`
English branch), so Odia grievances collapse to `Uncategorized` + `fallback`. (2) *No
governance-intelligence layer* — the 1.37M/6.56M corpus (the real moat) is not aggregated into
emergent-issue / spike intelligence. A prior BERTopic attempt failed on
Odia/romanized/broken-English; diagnosed as an **embedder-quality + out-of-distribution**
problem (not a clustering-algorithm one), and solvable **on-box** — the "citizen text never
leaves the box" invariant holds throughout Part III (no external APIs).

**Decisions locked with the maintainer (2026-07-22):**
- **Local Indic LLM: phased hybrid.** Demo + improved-models tiers use *task-specific* Indic
  models only. A local Indic LLM is added **later, as a batch job on the on-demand GPU box**
  (the existing `gpu_box_count` create/destroy pattern — `deploy/terraform/gpu.tf`) to power
  intelligence-layer narration + the hardest multilingual cases. **No always-on GPU billing, no
  resident inference service.**
- **Multilingual: Indic-native + relax gates**, not translate-to-English. IndicTrans2 kept only
  as an optional low-confidence fallback / intelligence English-pivot (deferred).
- **Intelligence layer: parallel post-demo track** — not a demo blocker (the demo still ships on
  `fallback`/crosswalk routing per Phase 9).

## Cross-cutting — Language-first invariant (Part III)

`pages.language` is the spine every downstream stage already keys off. The invariant: **detect
language early, normalize romanized → script, run multilingual models the whole way down, and
measure per-language** — language-awareness as a pipeline property, not an "Odia mode" bolted
on. Concretely: a first-class IndicLID language-ID stage (13.1) replaces the coarse
format-classifier `Language` output; an IndicXlit transliteration step (13.2) canonicalizes
romanized Odia before model calls; the English-only gates are relaxed (13.4); and every model
change is gated by a **per-language eval harness** (13.6) with Odia / romanized-Odia / English
slices, so parity is *provable* to a third-party government rather than assumed. **Ordering
caveat (Codex, PR #40):** a *text-based* language ID can't precede OCR on scanned docs — the
pre-OCR signal stays **image-based** (for OCR-engine choice) and text-based IndicLID/transliteration
run only once text exists (post-OCR, or immediately on the typed-text path). See Phase 13.1.

## Cross-cutting — Modularity, switchability & MLflow (Phase 13.0)  ⬜ *(prerequisite for 13–14)*

The model-maturity work is only safe if swapping a model or reordering the pipeline is a
config/registry change, **not** a code edit. Built first:

- **F1 — Stage registry + configurable order.** Replace the hardcoded `STAGE_ORDER` tuple +
  if/elif dispatch (`pipeline/pipeline.py`) with a **stage registry**: each stage registers
  `name → (run_callable, declared inputs/outputs)`, preserving the lazy-per-stage import
  (register a thunk, import inside). The run sequence is **config-driven**
  (`PipelineConfig.stages` becomes an ordered plan) and **dep-validated** by topological check.
  Note the dep graph must match 13.1's corrected ordering: the **pre-OCR** language signal is the
  *image-based* one inherent in `format_classifier` (runs first), while the **text-based**
  `language_id`/`transliteration` stages depend on `ocr` and run *after* it — so the validator
  encodes `format_classifier` → `ocr` → `language_id`/`transliteration`, then `pii` before
  `categorizer`, `page_type` before `summarizer`. Adding a stage becomes *registering* it.
  The **warm processor** (`inference/service.py::process`) is a parallel hardcoded sequence —
  bring it onto the same abstraction so batch + live stay in lockstep (the larger refactor here).
- **F2 — Model registry.** Move all model ids/paths out of code constants (`summarizer.py`,
  `categorizer/stage.py`, `pii_tagger.py`, `page_type_classifier.py`, `deepseek_backend.py`)
  into one resolver returning a handle from either a **local DVC path** or an **MLflow registry
  alias**. `PipelineConfig` + `Settings` (`config.py` — already has `MLFLOW_TRACKING_URI`/
  `MLFLOW_ARTIFACT_URI`) carry the per-stage references. Model swaps (13.3, B.1) become config.
- **F3 — MLflow adoption.** Wire the built-but-unmerged slim helpers (`tracking/mlflow_utils.py`,
  branch `feat/mlflow-slim-registry`) into the resolve path: register versions and resolve them by
  **alias** (`@champion`/`@production`) + DVC path/hash provenance tags; F2 resolves `@production`
  by default. *(Codex round 2: use MLflow **aliases**, not the legacy `Staging`/`Production`
  **stages** — those are deprecated; this also updates the Phase 6 wording.)*
  **Infra:** the `mlflow` service lands in `deploy/docker-compose.yml` now (Phase 12 reserves
  `mlflow → api → frontend → proxy`; ARCHITECTURE's "intentionally still absent" note updates) —
  file backend + S3 artifacts via `MLFLOW_ARTIFACT_URI`. This is the Phase 6 trigger, fulfilled.
- **F4 — `eval_results.jsonl` gates promotion.** Per-task, per-language eval metrics (13.6) become
  the gate for promoting a candidate (moving its `@production` alias): retrain → eval → compare in
  MLflow → promote → serving resolves the new `@production` model with **zero code change**. The
  gate matches candidates **by task** (see 13.6 key) so it never compares a router against a
  summarizer.
- **Tests**: reordering/removing a stage via config changes the run and the dep-validator rejects
  an illegal order; a registry model swap changes serving output with no code edit; MLflow shows
  registered versions with aliases + DVC tags and serving resolves `@production`.

## Phase 13 — Multilingual / Odia-first pipeline (Tier 2 "improved models")  ⬜

Built on the 13.0 foundation (new stages are *registered*; model swaps are *registry/config*).

- **13.1 Language ID — split by where text exists** *(corrects an earlier pre-OCR placement;
  Codex review of PR #40).* A **text-based** IndicLID cannot run before OCR on scanned documents
  (no `extracted_text` yet — only the format classifier's coarse image-derived `pages.language`).
  So language handling splits by path rather than being a single pre-OCR stage:
  - **Pre-OCR (scanned):** OCR-engine selection uses an **image-based** language signal — the
    format classifier already predicts `Language` from the page *image* (`format_classifier/`),
    which is exactly what picks the tesseract model (`eng`/`ori`/`eng+ori`). Improve this signal
    if needed, but it stays image-based (a text LID here is impossible).
  - **Post-OCR (scanned) + immediately (typed text):** a text-based **IndicLID** stage runs on
    the actual text to *refine* `pages.language` (native + romanized Odia included) before the
    downstream Indic stages. Warm path: replace `_detect_language` + the `is_english_compatible`
    injection (`inference/service.py`) — text exists at once on the typed-text branch (flips
    immediately); the document branch refines after OCR.

  Net: `pages.language` is written coarsely (image) pre-OCR to choose the OCR model, then refined
  (text) post-OCR to drive the Indic stages. The stage registry (13.0/F1) dep-validates this order.
- **13.2 Romanized → script normalization (new).** An **IndicXlit** transliteration step
  canonicalizes romanized Odia → Odia script. It operates **on text**, so it runs *after* the
  post-OCR IndicLID refine (13.1) — in `ocr_extraction/stage.py::_process_page` after OCR (batch)
  and `service.py::process` (warm). Note romanized Odia is overwhelmingly a **typed-text**
  phenomenon (typed text is never OCR'd), so this mostly fires on the typed-text path; it also
  covers any OCR output that comes back romanized.
  - **⚠ Offset constraint (Codex round 2 — do NOT do this in place).** Transliteration is **not
    length-preserving** (unlike the 1:1 Indic-digit normalizer in `pii_tagger.py` — that analogy
    does *not* hold here). The frozen serving contract and `pii_tagger.detect_pii_spans` define
    PII `start`/`end` over the **original** extracted text, so the transliterated form must be a
    **separate derived field** consumed only by language-ID / classification / embedding —
    **never** the string that redaction offsets are computed against. Redacting on transliterated
    text (or applying its spans to the original) would misalign PII badges — a **privacy hazard**,
    not a cosmetic bug. If a stage ever needs to map between the two, it carries an explicit
    offset map, not an in-place rewrite.
- **13.3 Multilingual model swaps (via the F2 registry).** Summarizer `facebook/bart-large-cnn`
  → **IndicBART**/mT5-Indic (loader is already generic `AutoModelForSeq2SeqLM`); add an
  **IndicNER**-backed `PERSON` recognizer to Presidio's registry (admit an Odia language code in
  `supported_languages` + the `analyze(language=…)` calls; new token in
  `ENTITY_TOKENS`/`ENTITY_ALIASES`) — this also lifts weak Indian-name recall on English pages;
  **retrain MuRIL** on the OLTP corpus **including native + romanized Odia** (transliteration-
  augment) — MuRIL already covers Odia, the blocker is the training slice (this is the Phase 5
  tabled retrain).
- **13.4 Relax the English-only gates (the crux).** Without this, the swaps don't fire. Relax
  `_is_english` (`categorizer/stage.py`), the English branch + `UNSUPPORTED_LANGUAGE_SUMMARY`
  (`service.py`), and `WHERE language LIKE '%English%'` (`pii_tagger.py`) — replace "English vs
  not" with "route by detected language."
- **13.5 Improved routing = the empirical crosswalk (lands Phase 9's tabled first step).**
  `(category, subcategory, district) → argmax(dept, office)` from the lake `complaints` table
  with **support + concentration as real confidence** and the fallback ladder, behind the
  `_Router` Protocol, `method="rules"`. Ceilings: cat→dept 60.9% / +subcat 67.5% /
  +subcat+district 72.8%.
- **13.6 Per-language eval harness (new; gates 13.1–13.5 and feeds 13.0/F4).** No
  `eval_results.jsonl` exists yet — build it modeled on `pipeline/pii_eval.py`
  (`EvaluationReport.to_dict()`, `LEGACY_OVERLAP_BASELINE=0.8056`). One row per
  `(task, model_name, model_version, gold_version, language)` — the **`task`/stage** key is
  load-bearing (Codex round 2): PII, categorization, summarization and routing are independent
  surfaces with their own candidates and metrics, so the F4 promotion gate must not compare across
  them. Small gold slices for PII / categorization / summarization in Odia, romanized-Odia, English.
- **Verify:** Odia + romanized-Odia grievances to `janasunani-api-live` → non-`Uncategorized`
  category, non-empty Odia summary, Odia names redacted, routing no longer forced to `fallback`;
  English PII overlap still ≥ 0.8056.

## Phase 14 — Human-in-the-loop online learning (classification, summarization, routing)  ⬜

Officers correct **all three** AI decision surfaces — category/subcategory, summary, route — so
the feedback loop is a general AI-HI flywheel, not routing-only. One shared correction substrate
feeds three per-model signals.

- **14.1 Shared correction-capture surface (new, non-breaking).** A new
  `POST /grievance/{id}/correction` endpoint records `corrected_category`/`_subcategory`/
  `_summary`/`_route` (+ who/when) alongside the AI output; **new schemas in a new module —
  `serving/schemas.py` (frozen contract) untouched**. Persist via a `grievance_corrections`
  sibling table (or extend `live_grievances` + `crud.create_or_update_live_grievance`). Needs
  auth (officer identity).
- **14.2 Two-speed learning (the honest "online").** Per-example gradient updates are unstable
  for these transformers, so every correction feeds: a **fast layer** (embed the correction into
  the Phase-15 index → the *next similar grievance* benefits at once via case-based retrieval:
  nearest-neighbor category vote, retrieved officer-edited summaries as few-shot exemplars,
  neighbor routes) and a **slow layer** (periodic batch retrain — batch, not real-time, per the
  freshness model). *(Codex round 2: `materialize` currently exports only complaints /
  action_history / pages / documents, so the new `grievance_corrections` table must be added to
  the materialize/DVC outputs, or the retrain reads it straight from OLTP — otherwise the
  captured corrections never reach the training job.)*
- **14.3 Per-model signals.** *Classification* — corrected labels → MuRIL retrain (historical
  `complaints.category` is already human labels, so the signal exists day one). *Summarization* —
  AI-vs-edited summary → SFT/preference pairs for IndicBART (**forward-only**: no historical gold
  summaries). *Routing* — a learned scorer (`routing/model.py`/`router.py`, `method="learned"`
  already reserved) ranking on incidence + **disposal time** + citizen benefit, features joined
  from `action_history` → `complaints`; override labels = routed dept/office vs the acting office.
- **Confounding asymmetry (bake in):** classification + summarization corrections are clean
  supervised signals; **routing's disposal-time/benefit objective carries the OVB caveat** (harder
  cases run longer regardless of office; office selection isn't random) — keep scored routing
  descriptive-assisted, not autonomous, until causal controls are in.

## Phase 15 — Governance-intelligence layer (Workstream B)  ⬜ *(parallel post-demo)*

Embeddings-first, unsupervised, on-box. Reuses the DuckDB/Parquet lake + the on-demand GPU box;
**no new datastore, no external API**.

- **B.1 On-box semantic index — inside DuckDB.** New `olap/embed.py` batch producer writes
  `data/interim/embeddings.parquet`. Schema (Codex round 2): `ticket_no`, `vector`,
  `model_version`, plus **`source`/`kind`** and a **`correction_id`** so a raw-grievance vector is
  distinguishable from a correction-derived one (edited summary / label / route, per 14.2) —
  otherwise the fast-adaptation layer can't retrieve the right evidence. `lake.connect()` auto-globs
  the parquet as a queryable **view** (fine for scans; no registration needed there). New `dvc.yaml`
  stage after `materialize`. **Search:** at ~1.37M vectors a brute-force `array_distance` scan is
  viable to start; if latency demands an HNSW index, that needs a **persisted DuckDB table** +
  `CREATE INDEX … USING HNSW` (VSS does not index a parquet view) — a build-time choice, not a
  requirement. **This fixes the prior BERTopic failure**: normalize-before-embed
  (13.1/13.2 move romanized Odia in-distribution — the step the prior attempt lacked) + a modern
  multilingual embedder (**BGE-M3** floor; LLM-scale **e5-mistral-7B / gte-Qwen2-7B** on the GPU
  box as ceiling — a generation past mBERT/LaBSE, cross-lingual). Also powers **case-based
  retrieval** (similar past grievances + their actual resolutions/disposal times) and
  **duplicate/campaign detection**.
- **B.2 Spike/anomaly detection (do first — cheapest win).** `(category × district × week)`
  counts → EWMA / STL-residual / Poisson surprise ("water complaints in District X up 300% this
  week"). No ML; DuckDB queries on a schedule.
- **B.3 Emergent-theme topic modeling (two-track).** Embedding track (BERTopic-style with the
  *good* embedder + normalization; clusters not matching the 62 categories = emergent issues) and
  an **LLM-driven track** (once the phased-hybrid local Indic LLM lands as a GPU batch job:
  TopicGPT-style taxonomy induction + zero-shot labeling — a local Indic LLM *reads*
  broken-English/romanized-Odia better than any embedder *clusters* it: "SOTA topic modeling
  without an API"). Local LLM also **narrates** clusters/spikes into a governance brief.
- **B.4 Serving + frontend (non-breaking).** New `serving/intelligence.py` `APIRouter` + an
  `AnalyticsProvider` Protocol mirroring `HistoryProvider`, reading aggregates via `olap/lake.py`
  — **new schemas in the new module; `schemas.py` untouched**; **add auth** (read paths are
  currently unauthenticated and this is real lake data). Frontend: `app/intelligence/page.tsx` +
  `components/IntelligenceView.tsx` + nav link + `lib/api.ts`/`lib/types.ts`; reuse `ui.tsx` +
  the `--dpic-stat-tile` token; a light charting lib (none exists today).
- **Guardrail (bake in):** keep this layer **descriptive** (what/where/how-fast-growing).
  Comparative *office-performance* claims hit the same OVB problem as Phase 14 — spikes + themes
  are safe and shippable; performance rankings need causal care.

## Part III — new config / deps / infra surface

- `config.py` `Settings`: model refs for IndicLID, IndicXlit, IndicBART, IndicNER, the embedder,
  and (later) the local LLM — following the `MODELS_DIR` + `Settings` pattern; F2 resolves them.
- New uv extras per the transformers-conflict rule (a local Indic LLM likely needs its **own**
  extra + Docker image, like `ocr-deepseek`); VSS + the embedder are new deps.
- `deploy/docker-compose.yml` gains `mlflow` (13.0/F3); the GPU box gains batch jobs (embeddings,
  later the LLM) under the existing create/destroy toggle — no resident service.

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
- ~~AGENTS.md read access to `data/raw/janasunani-mappings/*`~~ → resolved: confirmed readable and
  DVC-tracked to the remote (2026-07-02).
- ~~PII weights recovery~~ → closed 2026-07-03: DSI team disbanded, Box gone; stage will be rebuilt on
  Presidio (see Phase 5). Training loop preserved at DSI-repo commit `db4885f` for reference.
- ~~Whether live demo grievances reuse the `complaints` table (+ AI/routing columns)
  or a sibling table~~ → decided 2026-07-03: use a sibling `live_grievances`
  OLTP table for Week 3 API/demo submissions. Keep the historical `complaints`
  schema faithful to the dump; `GET /grievance/{id}` reads live OLTP, and
  `/history` remains Parquet-backed historical data.
- Whether to DVC-track an OLTP "seed" snapshot for reproducible demos (vs regenerate from dump).
- **Backfill hardening (before any FULL-corpus run; fine at the ~200-doc demo scale)** — from the Codex
  review of PR #4: the summarizer/categorizer/PII stages materialize their whole pending workload in
  memory before batching (fetchall/pandas/prebuilt work lists); page through SQL in bounded chunks.
  The PII `max_len` zip-truncation (drops text past the first token window) dies with the legacy
  module in the Presidio rebuild. The pii_tagger hard-fail on missing artifacts is intentional
  (privacy gate — silently skipping redaction would be worse) and is also resolved by the rebuild.
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
   first; MLflow registration deferred to the retrain phase — the demo serves DVC-tracked models by
   path, so MLflow adds no runtime value until there are candidate versions to compare/promote).
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
