# Janasunani 2.0 — Architecture

> The **what and why** of the codebase, for someone landing here cold.
> The **plan and status** live in [ROADMAP.md](ROADMAP.md) (source of truth for
> sequencing); per-package detail lives in the READMEs linked throughout.

## What this is

An AI-powered grievance redressal prototype for Odisha. A raw grievance — typed
text or a scanned document — is **extracted** (OCR), **redacted** (PII),
**classified** (category), **summarized**, and **routed** to the responsible
office, ending in a Next.js demo UI.

The repo consolidates two earlier projects (a grievance backend and the DSI
document-processing pipeline) into one `janasunani/` package, in two parts:

- **Part I — Foundation** *(built)*: data migration into an OLTP store, Parquet
  materialization, document ingestion → S3, the document pipeline, cloud infra.
- **Part II — Automation prototype** *(in progress)*: single-grievance inference,
  routing, FastAPI serving, Next.js UI.

## The data flow, end to end

```
                     COLD START (one-off)                LIVE (per grievance)
                     ────────────────────                ────────────────────
mysqldump (3.2 GB)                                       Janasunani API / demo UI
      │  scripts/migrate.sh                                     │
      ▼                                                         ▼
throwaway MySQL ──▶ janasunani/migration ──▶ ┌──────────────────────────┐
                    (validate via schemas)   │   OLTP store             │◀── janasunani/ingestion
                                             │   SQLite (dev) or       │    (documents → S3,
                                             │   Postgres (deploy),    │     status → OLTP)
                                             │   via OLTP_DB_URL       │
   scanned documents (S3)                    └──────────┬───────────────┘
      │                                                 │
      ▼                                                 │ janasunani/olap
janasunani/pipeline ──▶ artifact SQLite ──▶ exporter ───┤ (materialize)
 format → OCR → PII →   (pages/documents)   (upsert     ▼
 page-type → summarize →                     into OLTP) Parquet lake (data/interim/)
 categorize                                             = analytics / ML / demo history
```

Three storage layers, deliberately distinct
([db/README](../janasunani/db/README.md), [olap/README](../janasunani/olap/README.md),
[pipeline/README](../janasunani/pipeline/README.md)):

| Layer | Engine | Role |
|---|---|---|
| **OLTP store** | SQLite / Postgres via `OLTP_DB_URL` (async SQLAlchemy, Alembic) | System of record: 1.37M complaints, 6.56M action-history rows, plus exported pipeline outputs. Live writes land here. |
| **Parquet lake** | Files in `data/interim/`, DuckDB/Polars readers | Read-optimized downstream copy, produced by `janasunani-materialize`. The demo's history browse and all ML/analytics read this, never OLTP. |
| **Pipeline artifact DB** | Standalone SQLite per run | The document pipeline's own working state (`pages`/`documents`/`unreadable_pages`), resumable by design. Reaches OLTP only through the exporter. |

Verified full-scale counts (local **and** cloud Postgres, must match after any
migration change): **1,371,288 complaints / 6,556,171 action-history rows**.

## Package map

| Path | What lives there |
|---|---|
| [`janasunani/config.py`](../janasunani/config.py) | Paths (`directories`), settings (`settings`, pydantic-settings from env/`.env`), loguru helpers. The two things everything else imports. |
| [`janasunani/db/`](../janasunani/db/README.md) | ORM models, async session, CRUD, Alembic migrations. Engine-portable (SQLite + Postgres). |
| [`janasunani/migration/`](../janasunani/migration/README.md) | Cold-start dump loader + live-MySQL sync, converging on one validated insert routine. |
| [`janasunani/ingestion/`](../janasunani/ingestion/README.md) | Janasunani API client, S3 service, document downloader, and the Pydantic schemas that are the **single raw→ORM column map**. |
| [`janasunani/olap/`](../janasunani/olap/README.md) | `materialize` (OLTP → Parquet via DuckDB scanners) and `lake` (read helpers). |
| [`janasunani/pipeline/`](../janasunani/pipeline/README.md) | The six-stage document pipeline (DSI refold), its artifact DB, the OLTP exporter, OCR quality guards, PII evaluator. |
| [`janasunani/tracking/`](../janasunani/tracking/__init__.py) | MLflow + DVC dual tracking (slim; being built). |
| [`deploy/`](../deploy/README.md) | docker-compose for the CPU box; [Terraform](../deploy/terraform/README.md) for both EC2 boxes. |
| [`scripts/`](../scripts/README.md) | Operational one-offs: `migrate.sh` (cold-start), `gpu_smoke.sh` (DeepSeek smoke), `sample_english_complaints.py` (evaluation bundles), `setup.sh`. |
| [`tests/`](../tests/README.md) | Real-code-path pytest suite. Read the README before running tests anywhere near production. |

## Environments and the dependency split

Python ≥ 3.13, managed by **uv**. Base deps are light; heavy ML stacks live in
three **optional extras** (`pyproject.toml`):

- `pipeline-core` — format classifier, pytesseract OCR, Presidio PII, page-type
  ViT, summarizer (`transformers>=4.57,<5`)
- `ocr-deepseek` — DeepSeek OCR only (`transformers==4.46.3` — **conflicts** with
  the others; declared in `[tool.uv].conflicts`)
- `categorizer` — MuRIL categorizer

The conflict is load-bearing: one environment can never hold both transformers
pins, so **stages import their deps lazily** and deploy runs one env per extra
(`uv run --extra X`) against the same artifact DB. `scripts/gpu_smoke.sh` is the
canonical demonstration. `scikit-learn>=1.8,<1.9` is pinned to the pickle era of
the inherited estimators.

## Infrastructure (two boxes)

Managed by [Terraform](../deploy/terraform/README.md), state local, region
ap-south-1, IAM instance roles only (no static keys):

- **CPU box** (always on, t3.large, Elastic IP 52.66.116.80): Postgres OLTP,
  `api`, `frontend`, and `proxy` (Caddy) in Docker
  ([compose](../deploy/README.md)), migration/materialization one-offs,
  nightly `pg_dump` → S3. `mlflow` is intentionally still absent (see
  [ROADMAP.md](ROADMAP.md) Phase 12).
- **GPU box** (on demand, g6.xlarge/L4, `gpu_box_count = 0/1` toggle, ~$1/hr
  while up): DeepSeek OCR batch runs and demo windows. Built from the Deep
  Learning Base AMI; created and destroyed per use — nothing stateful on it.

## DVC: what it tracks and what it doesn't

DVC (S3 remote `dpic-dvc-cache/janasunani`) versions **file artifacts** — the raw
dump, the Parquet lake, mirrored models under `models/`, the document sample —
and **file-in/file-out transforms** (`dvc.yaml`: `pipeline-sample`,
`materialize`). Operational jobs whose effects live in external systems
(migration → OLTP, ingestion → S3, backfills) are **not** DVC stages; they run
as CLIs/cron and reach the lake via `materialize`. With Postgres OLTP, run
`janasunani-materialize` directly then `dvc commit` (the `materialize` stage
deps on the SQLite path).

## Model provenance (hard rule)

The DSI team disbanded (2026-07-03) and their Box data is gone. Runtime loads
models **only** from our DVC mirrors under `models/` or from large public repos
(`facebook/bart-large-cnn`, `deepseek-ai/DeepSeek-OCR`) — never from
DSI-controlled accounts. The PII model was the one unrecoverable artifact; its
replacement is the Presidio-based stage (see
[pipeline/README](../janasunani/pipeline/README.md)). The DSI technical report's
measured baselines (the only surviving eval record) are recorded in
[ROADMAP.md](ROADMAP.md) — headline: legacy PII coverage **80.56%** any-overlap,
the number the rebuilt stage must beat.

## Security invariants

- **Citizen text never leaves the box**: PII detection/redaction is fully
  in-process (Presidio + local spaCy). No external redaction APIs, ever.
- Postgres password only in the box's chmod-600 `.env` / gitignored
  `deploy/.env`. Terraform state/tfvars, SSH keys: local only (CI + pre-commit
  guards enforce; `no-raw-data-in-git` blocks data files).
- **Never `docker compose down -v`** on the CPU box — the OLTP volume holds the
  migrated production data.
- **Never run pytest on the CPU box against the prod container** — fixtures drop
  tables. See [tests/README](../tests/README.md).
- The boxes hold no GitHub credential; clone/`uv sync` via SSH agent forwarding.

## Gates

Every feature ships with real-code-path pytest tests, run before "done":
`uv run --extra pipeline-core pytest && uv run ruff check .`. CI
(`.github/workflows/`) runs ruff, the test suite against a service-container
Postgres plus `dvc status` validation, and the raw-data-in-git guard. CI installs
**no heavy extras** — anything imported by tests must live in a light module
(see [pipeline/README](../janasunani/pipeline/README.md) for the pattern).
