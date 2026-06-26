# janasunani Roadmap — Part I: Migration · Part II: Automation Prototype

## Context

`janasunani` is becoming "Janasunani 2.0," the unified, AI-powered grievance redressal system for
Odisha. The end goal is a **full automation prototype**: take a **raw grievance** (typed text or an
uploaded scanned document), **extract** its text, **redact PII**, **classify** it (category / subcategory
/ department), **summarize** it, and **route** it to the responsible office — quickly — with a polished
**Next.js demo frontend** to show it end to end.

Getting there has two parts:

- **Part I — Foundation / Migration** *(in progress)*: consolidate existing work scattered across two
  other repos into one `janasunani/` package on one stack (uv, DVC, SQLAlchemy, DuckDB, MLflow): the SQL
  migration, the document ingestion → S3, and the document-processing/ML pipeline
  (OCR / PII / page-type / summarize / categorize). This gives us data + models + a batch pipeline.
- **Part II — Automation Prototype & Demo** *(future)*: wrap those pieces into a real-time,
  single-grievance inference service, add a **hybrid (rules + learned) routing engine**, expose a FastAPI
  API, and build a **Next.js/React** demo UI. Heavy/GPU model profile for best quality.

This plan is the source of truth and is **mirrored into the repo at `docs/ROADMAP.md`** (kept in sync).

### Implementation status (progress tracker — keep in sync)
- ✅ **Phase 0** — scaffolding & dependencies (package skeleton, uv deps, optional ML groups)
- ✅ **Phase 1** — DB / ORM layer (`Complaint` expanded to the dump's full 56-col set; schemas = source→ORM
  map; session, crud, merged config, `.env.example`)
- ✅ **Phase 2** — SQL migration: `from_mysql.py` (config-driven) + `from_sql_dump.py` converge on one
  validated insert routine; console scripts; package made installable (hatchling). *Real-data row mapping
  verified; the full 3.2 GB live restore is deferred until a MySQL server (Docker/creds) is available.*
- ⬜ **Phase 3** — document ingestion → S3 *(next)*
- ⬜ **Phase 4** — document processing pipeline (refold `document_pipeline`, DVC-track model artifacts)
- ⬜ **Phase 5** — MLflow + DVC dual tracking + `dvc.yaml` stages
- ⬜ **Phase 6** — tests, CI, docs
- ⬜ **Part II — Phase 7** — real-time inference core (warm `GrievanceProcessor`)
- ⬜ **Part II — Phase 8** — hybrid routing engine (rules + learned)
- ⬜ **Part II — Phase 9** — FastAPI serving API
- ⬜ **Part II — Phase 10** — Next.js/React demo frontend
- ⬜ **Part II — Phase 11** — demo integration & deploy (docker-compose + Terraform, S3, `make demo`)

### Confirmed decisions
- Source of the doc-processing/ML pipeline: the `grievance-pipeline` `document_pipeline` (NOT the ORTPS
  analysis pipeline, which is out of scope).
- SQL migration: **both** a raw-dump loader and a live-MySQL sync.
- Layout: refold into the `janasunani/` package (drop the old FastAPI "backend" framing — a fresh serving
  layer is reintroduced cleanly in Part II).
- MLflow: local file-based tracking + registry; artifacts to the existing S3/DVC remote.
- **Part II**: frontend = **Next.js/React (polished)**; routing = **hybrid (rules + learned)**; demo input
  = **both typed text and document upload**; inference profile = **heavy/GPU (best quality)**.

### Testing policy (applies to EVERY phase)

Every feature ships with **robust automated tests (pytest), run and shown passing before the phase is
called done.** This is not optional and not deferred to Phase 6 — Phase 6 only adds CI wiring and broader
coverage. The rule exists because several migration bugs reached "done" on shortcut verification (sync
engine + tiny samples): the async path's missing `greenlet` and an O(n²) action-history loop both only
surfaced on the live run.

Concretely:
- **Exercise the real code path**, not a simplified stand-in. Async DB tests use `create_async_engine`
  (aiosqlite) so the greenlet/async path actually runs.
- **Integration-test the migration without MySQL/Docker:** build a SQLite "source" with the MySQL table
  names (`t_janasunani_etl_pre_data`, `t_janasunani_etl_history_pre_data`) — `run_migration` reflects
  tables by name, so it runs against any SQLAlchemy engine.
- Cover happy-path counts, **idempotency** (re-run = no dups), malformed/edge inputs, the schema
  source→ORM alias mapping (catches drift), and skip/dedup behavior.
- Gate each phase on `uv run pytest` + `uv run ruff check .` (dev dep: `pytest-asyncio`).

Each phase below has a **Tests** bullet listing the cases that guard it.

---

# PART I — Foundation / Migration

## Sources (exact paths / branches)

| Source | Repo (local path) | Branch | What we take |
|---|---|---|---|
| DB/ORM layer | `/Users/ymohanty/Documents/GitHub/grievance` | `dev` | `backend/app/db/{models,session,crud}.py`, `backend/app/ingestion/schemas.py`, `backend/app/config.py` |
| Document ingestion → S3 | `.../grievance` | `dev` | `backend/app/ingestion/{document_ingestion,client,orchestrator}.py`, `backend/app/s3service.py` |
| SQL migration | `.../grievance` | any (`documents`) | `backend/app/db/migration_from_mysql.py` (live MySQL→SQLite) — the *logic*, rebound to the `dev` ORM |
| Document processing + ML pipeline | `/Users/ymohanty/Documents/GitHub/2025-autumn-dpic` (remote: `grievance-pipeline`) | `pipeline` | `src/document_pipeline/**`, `models/**` (format_classifier `.pkl`, pii_tagger code, summarizer) |

Inspect source files non-destructively with `git -C <repo> show <branch>:<path>`.

## Target package structure

```
janasunani/
  config.py            # paths/logging + Settings (DB, AWS/S3, API, MYSQL_URL) + directories shim   [DONE]
  db/                  # async SQLAlchemy over grievance.db (complaints/action_history)             [DONE]
    models.py  session.py  crud.py
  ingestion/           # API client, schemas (source→ORM map), document ingestion → S3
    schemas.py [DONE]  client.py  document_ingestion.py  s3service.py  orchestrator.py
  migration/           # SQL migration (both paths)
    from_mysql.py      # ported migration_from_mysql.py (live MySQL → SQLite)
    from_sql_dump.py   # NEW: load the raw mysqldump → DB
  pipeline/            # refold of document_pipeline (OCR/PII/page-type/summarize/categorize)
    cli.py  config.py  db.py  pipeline.py  stages/{...}/...
  tracking/            # MLflow + DVC dual-tracking helpers
    mlflow_utils.py
models/                # ML artifacts (DVC-tracked)
docs/ROADMAP.md        # this plan, mirrored into the repo
```

Two SQLite DBs stay **separate**, joined on `ticket_no`/`ticket_number`:
- `data/raw/grievance.db` — complaints + action history (async SQLAlchemy ORM).
- `data/output/pipeline.sqlite` — `pages` / `documents` / `unreadable_pages` (raw `sqlite3`).

Import rewrites: `app.*` → `janasunani.*`; `document_pipeline.*` → `janasunani.pipeline.*`.

## Authoritative schema = the SQL dump  *(reference)*

The cold-start data source is a 3.2 GB `mysqldump` ([data/raw/Dump20250730.sql](data/raw/Dump20250730.sql))
of MySQL DB `sociomatics_ticket` (5.7.44), with only **2 tables**:
- `t_janasunani_etl_pre_data` — complaint table, **56 columns** → `Complaint` ORM.
- `t_janasunani_etl_history_pre_data` — action history, **6 columns** → `ActionHistory` ORM.

The dump (not any grievance branch) defines the schema. Source names are a messy mix (camelCase,
PascalCase, `int*`/`vch*` prefixes, real typos like `officeNAme`, `RecievedBy`, `intCompliantStatusId`);
they map to clean snake_case ORM fields via **one source→ORM map** — the Pydantic `Field` aliases in
`janasunani/ingestion/schemas.py`. The final `Complaint` = dump's 56 columns ∪ 4 ingestion-populated
document columns (`local_document_path`, `document_downloaded`, …). Paired `(id, name)` source columns
become both `*_id` and the name column (e.g. `intDistId`→`district_id`, `districtName`→`district`).
`tracking_id` (from `trackingId`) is the join key between complaints and action history.

*(Branch nuance: `migration_from_mysql.py` is ~identical across branches; its ORM/schema deps differ
between `dev` and `documents`. We adopt `dev`'s ORM style/plumbing and expand it to the dump's full
column set, so ingestion and migration share one schema in one DB.)*

## Phase 2 — SQL migration (both paths)  ⬜ next
- Port `migration_from_mysql.py` → `janasunani/migration/from_mysql.py`; `MYSQL_URL` config-driven (env)
  not hard-coded; reuse the chunked async insert + Pydantic validation + on-conflict-do-nothing dedup.
- NEW `janasunani/migration/from_sql_dump.py`: load the raw `mysqldump` (`sociomatics_ticket`, MySQL 5.7,
  utf8mb4) by restoring it into a **local/throwaway MySQL 5.7**, then funnel through the *same*
  `from_mysql` table-copy + validation path so dump (cold start) and live MySQL (incremental) **converge
  on one validated insert routine**. (Pure-SQLite translation of a 1GB+ utf8mb4 dump is brittle — prefer
  the real MySQL restore.)
- Reuse the existing `janasunani/db/crud.py` bulk-load helpers and `ingestion/schemas.py` validators.
- Verify: run the loader on a small sample `.sql`; assert `grievance.db` row counts; round-trip real dump
  rows through `Complaint`/`ActionHistory` schemas (catches any column the old migration ignored).

## Phase 3 — Document ingestion → S3  ⬜
- Port `s3service.py`, `ingestion/client.py`, `document_ingestion.py`, `orchestrator.py`; rewrite imports.
- Bucket via config (`AWS_S3_DOCUMENTS`); dev-vs-prod (local FS vs S3) on `settings.ENV`.
- Console entry points in `[project.scripts]` (e.g. `janasunani-ingest-documents`).
- Verify: `ENV=dev` ingestion writes to `LOCAL_STORAGE_PATH`; with `moto`, assert `upload_fileobj` and the
  `Complaint` document-status columns update.

## Phase 4 — Document processing pipeline  ⬜
- Refold `src/document_pipeline/**` → `janasunani/pipeline/**`. Preserve the CLI, `PipelineConfig`,
  `STAGE_ORDER` + **lazy per-stage imports** (the existing fix for the transformers-version conflict
  between DeepSeek-OCR/categorizer and the rest), and the `pages`/`documents`/`unreadable_pages` schema.
- Populate the `pipeline-core` / `ocr-deepseek` / `categorizer` optional dependency groups (heavy ML deps)
  now. **Python 3.13 risk**: attempt 3.13; isolate any stage that breaks behind its group / separate env.
- Move `models/` artifacts in and **DVC-track** them; document manually-placed weights (PII CRF, MuRIL).
- Wire the categorizer's complaints-JSON to an export from `grievance.db`.
- Verify: `init-db` + `run --stages format_classifier ocr_extraction --ocr-engine pytesseract` on the
  2-file sample → `pages` rows with `extracted_text`.

## Phase 5 — MLflow + DVC dual tracking  ⬜
- `janasunani/tracking/mlflow_utils.py`: local backend store (`mlflow.db`/`./mlruns`), artifacts to the S3
  remote. Helpers to start runs, log params/metrics, register models, and tag each registered version with
  its **DVC path + content hash** (MLflow owns run/registry metadata; DVC owns the bytes).
- `dvc.yaml` stages mirroring the pipeline (migrate → ingest → format → ocr → pii → page-type → summarize
  → categorize) so `dvc repro` reproduces and CI `dvc dag`/`dvc repro` pass.

## Phase 6 — Tests, CI, docs  ⬜
- Port/adapt ingestion tests (`moto`) and pipeline smoke tests into `tests/`.
- Update `README.md` / `AGENTS.md` (package map, env-group install matrix, run commands).
- `uv run ruff check .`, `uv run pytest`, and the three GitHub workflows pass.

---

# PART II — Automation Prototype & Demo  ⬜ (future)

**Goal:** a live, single-grievance path — *raw input → extract → redact → classify → summarize → route →
result* — behind a FastAPI service and a polished Next.js UI. Heavy/GPU models, loaded once and reused.

### New package structure (added to Part I)
```
janasunani/
  inference/     # single-grievance, warm-model inference (wraps pipeline stages for one document/text)
    service.py   #   load-once model handles; extract→redact→classify→summarize for one grievance
  routing/       # hybrid (rules + learned) routing engine
    rules.py     #   deterministic lookup from janasunani-mappings master tables
    model.py     #   learned router trained on dump history; MLflow-registered
    router.py    #   combine: rules backbone + learned scorer + confidence/fallback
  serving/       # FastAPI app
    api.py       #   POST /grievance (text or file) → full structured result; loads models from MLflow registry
frontend/        # Next.js + React + Tailwind demo app
deploy/          # docker-compose (api + frontend + mlflow + proxy), demo seed data, make demo
  terraform/     #   minimal IaC: EC2 + IAM role + security group + S3 buckets
```

Infra is **Docker + S3, minimal managed services** — see "Cross-cutting — Infrastructure".

## Phase 7 — Real-time inference core  ⬜
- Refactor each pipeline stage to expose a **single-item function** (`process(text|image_bytes) -> dict`)
  alongside its existing batch DB path — reuse the same model code, no duplication. The lazy-import pattern
  stays (heavy stage deps load only when that stage runs).
- `janasunani/inference/service.py`: a warm `GrievanceProcessor` that loads models **once** (OCR, PII-CRF,
  page-type ViT, BART summarizer, MuRIL categorizer) — pulled by version from the **MLflow registry**
  (Phase 5) — and runs extract → redact → classify → summarize for one grievance. Text input skips OCR;
  document input (PDF/JPG bytes) runs DeepSeek OCR on GPU first.
- Verify: feed one sample text and one sample PDF; assert a structured result
  (`extracted_text`, `redacted_text`, `category`, `subcategory`, `dept`, `summary`) within target latency.

## Phase 8 — Routing engine (hybrid)  ⬜
- **Rule backbone** (`routing/rules.py`): deterministic mapping using the `data/raw/janasunani-mappings/`
  master tables (`m_admin_category`, `m_admin_subcategory`, `m_admin_offices`,
  `m_office_designation_mapping`, `m_admin_hierarchy_value`, escalation tables `t_forward_escalation` /
  `t_admin_escalation`) to resolve category/subcategory + district → department → office/designation, plus
  the escalation path. *(Access to these reference tables to be confirmed before reading — AGENTS.md
  restricts `data/`.)*
- **Learned router** (`routing/model.py`): train on the **migrated dump history** — features
  (category, subcategory, district, dept, mode, …) → label (the office / `pending_with` / `tagged_to` that
  actually handled it). Log/register in MLflow; evaluate top-k accuracy against held-out history.
- **Combine** (`routing/router.py`): rules give the candidate set + escalation; the learned model scores /
  disambiguates and fills gaps; emit a routed office + confidence + fallback when confidence is low.
- Verify: held-out routing accuracy reported in MLflow; a handful of sample grievances route to sensible
  offices with an escalation path.

## Phase 9 — Serving API (FastAPI)  ⬜
- `janasunani/serving/api.py`: `POST /grievance` accepting **multipart** (typed `text` or an uploaded
  `file`) → returns the full pipeline result + routing decision as JSON. Async, GPU-backed, models warm at
  startup from the MLflow registry. Add `/health` and basic timing metrics; CORS for the frontend.
- Console entry point (e.g. `janasunani-serve`) + uvicorn config.
- Verify: `curl` the endpoint with text and with a PDF → structured JSON; concurrent requests reuse warm
  models (no reload per request).

## Phase 10 — Demo frontend (Next.js/React)  ⬜
- `frontend/`: Next.js + React + Tailwind. A single-page flow: submit a grievance (text box **and** file
  upload), then show — in a clean, staged UI — extracted text, redacted text, predicted
  category/subcategory/department, the summary, and the **routing decision** (office + escalation path +
  confidence). Loading/streaming states so the "quick" story lands.
- Talks to the Phase 9 API; env-configurable API base URL.
- Verify: end-to-end in the browser — paste text or upload a scanned complaint, see the full result.

## Phase 11 — Demo integration & deployment  ⬜
- `deploy/`: docker-compose bringing up the FastAPI service (GPU) + the Next.js app; seed a set of sample
  grievances/documents; a one-command `make demo` (or script) that launches everything.
- Latency pass: warm caches, batch where possible, surface per-stage timings.
- Verify: fresh-machine bring-up → open the UI → submit a real sample grievance → extract→classify→
  summarize→route renders, with models served from the MLflow registry.

---

# Cross-cutting — Infrastructure (Docker + S3, minimal managed services)

**Principle:** self-host on **Docker** with **S3** as the only stateful AWS service. Avoid managed app
services (no SageMaker, ECS/EKS, RDS, Managed MLflow, Secrets Manager). S3 we already use everywhere, so
it stays; everything else runs in containers we control.

### Compute
- A **single GPU EC2 instance** (e.g. `g5.xlarge` / `g4dn`) runs Docker + `docker-compose`. One box hosts
  the whole demo (API + frontend + MLflow + reverse proxy). Batch/pipeline jobs run as **one-off
  containers** (`docker compose run`), not long-lived services. Scale out later only if needed.
- AWS access via an **EC2 IAM instance role** (S3 read/write) — no static keys baked into images. This is
  the one piece of AWS "wiring," and it's the standard keyless way to reach S3.

### Storage — all on S3 (no RDS)
- `s3://dpic-dvc-cache/janasunani` — **DVC remote**: versions data + model artifacts (already configured).
- `s3://janasunani-documents-*` — raw complaint **documents** (already).
- `s3://…/mlflow-artifacts` — **MLflow artifact store**.
- SQLite DBs (`grievance.db`, `pipeline.sqlite`) live on the instance's EBS volume and are **DVC-tracked →
  S3**. SQLite (a file) instead of RDS keeps us managed-service-free; revisit only if concurrency demands.

### Services (one `docker-compose.yml` in `deploy/`)
- `api` — FastAPI serving (GPU; `--gpus all`), models warm-loaded from the MLflow registry.
- `frontend` — Next.js app.
- `mlflow` — self-hosted MLflow **tracking server** container: backend store = SQLite file (or a small
  `postgres` container only if concurrent writes need it), artifact store = S3. For local dev, the
  file-based MLflow from Phase 5 is enough; the container is for the shared/demo box.
- `proxy` — Caddy/Nginx container for TLS + routing `frontend` and `api` on one host.
- Pipeline stages run as profile-gated one-off containers (`docker compose --profile pipeline run …`),
  honoring the per-stage dependency groups so the heavy DeepSeek/categorizer image stays separate.

### Images & CI/CD
- Build images in **GitHub Actions** (existing CI), push to **GHCR** (GitHub Container Registry) to stay
  off AWS-managed registries; the EC2 box pulls by tag. (ECR is a fine alternative if you'd rather keep it
  in AWS.) Pin base images; one image per dependency group (core / ocr-deepseek / categorizer) to respect
  the transformers conflict.
- A GPU base image (CUDA + torch) for `api`/pipeline; a slim Node image for `frontend`.

### IaC (keep it light)
- Minimal **Terraform** under `deploy/terraform/` for just: the EC2 instance + IAM role/profile, security
  group, and the S3 buckets/prefixes. Everything app-level is `docker-compose`. (The `grievance` repo's
  `terraform/` dir is a starting reference.) Alternative: a documented setup script if Terraform is
  overkill for a prototype.

### Config / secrets
- `.env` files per service (not committed; `.env.example` documents them) + IAM role for S3. No managed
  secret store.

This section is realized incrementally: Phase 5 stands up MLflow (file-based locally, container on the
box); Phase 11 assembles the full `deploy/` compose + Terraform and the `make demo` bring-up.

## Key reuse / do-not-reinvent notes
- Preserve the `pipeline.py` lazy-import-per-stage pattern (the existing transformers-conflict fix) and
  reuse the same stage model code for both batch and single-item inference (Phase 7).
- `migration_from_mysql.py` chunked async insert + validation + dedup — the dump loader funnels into it.
- `s3service.S3Service` (upload/list/exists/presign) — reuse for ingestion **and** MLflow artifacts.
- Routing should consume the existing `janasunani-mappings` master tables and the migrated dump history
  rather than inventing new reference data.

## Open items to confirm during implementation (non-blocking)
- Exact S3 bucket name (`janasunani-documents` vs `janasunani-documents-main`).
- Whether to later unify the two SQLite DBs (separate for now).
- Permission to read `data/raw/janasunani-mappings/*` for the routing rule layer (AGENTS.md gate).

## End-to-end verification (whole system, once Part II lands)
1. `uv sync` (with optional groups) resolves; `import janasunani` works. *(Part I ✅)*
2. Migration: sample `.sql` dump → `grievance.db` populated.
3. Ingestion (`ENV=dev`): documents fetched; status columns updated.
4. Pipeline: `init-db` + format/OCR on the 2-file sample → `pages` rows with `extracted_text`.
5. Tracking: an MLflow run + a registered model version tagged with its DVC hash; `dvc repro` succeeds.
6. Inference/serving: `POST /grievance` (text and PDF) → structured result + routing, from warm
   registry-loaded models.
7. Frontend: browser demo runs the full raw-grievance → routed-result flow.
8. `uv run ruff check .` and `uv run pytest` green.
