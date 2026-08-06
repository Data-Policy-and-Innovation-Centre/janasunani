# janasunani

**Janasunani 2.0** — Odisha's unified, AI-powered grievance redressal system.
A raw grievance (typed text or a scanned document) is **extracted** (OCR),
**redacted** (PII, in-process by default), **triaged** (spam / duplicate),
**classified** (category/department), **summarized**, and **routed** to the
responsible office, ending in a Next.js demo UI.

Citizen text leaves the box only through a declared, audited, revocable channel
(see [ARCHITECTURE.md](docs/ARCHITECTURE.md) "Security invariants"). That replaced
a strict no-egress rule on 2026-07-27, when the Government of Odisha authorized
Sarvam for this data.

The repo is one Python package (`janasunani/`) built **phase by phase** (see
[docs/ROADMAP.md](docs/ROADMAP.md)):

- **Foundation — built:** the historical data load (1.37M complaints,
  6.56M action-history rows) into a swappable OLTP store, Parquet
  materialization for analytics, document ingestion → S3, the six-stage
  document-processing pipeline, and the AWS infrastructure (an always-on CPU
  box + an on-demand GPU box).
- **The demo — in progress:** single-grievance inference, hybrid
  routing, FastAPI serving, and the Next.js demo. The serving API skeleton is on
  `main`; the routing engine, live persistence, lake-backed history, MLflow
  registry, and a first-cut DPIC-branded Next.js frontend are ongoing on feature
  branches. Phase 8 now provides an opt-in real local-model server behind the
  same contract; the default API remains mocked for frontend development.

  Re-scoped 2026-07-27 to five components: pipeline replication, spam & duplicate
  detection, the intelligence layer, A/B testing of AI automation, and a Sarvam
  benchmark. See [docs/ROADMAP.md](docs/ROADMAP.md) §1.1.

New here? Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first;
[docs/ROADMAP.md](docs/ROADMAP.md) is the plan and current status.
[docs/DELIVERY.md](docs/DELIVERY.md) is the dated plan for the 14 August demo,
in plain language.

## Running the components (today)

### Setup

```bash
make setup    # installs uv, rclone, AWS CLI v2 into ~/.local/bin (never sudo)
uv sync       # base environment; heavy ML stacks are opt-in extras (see below)
```

The repo and its private `dpic` dependency need GitHub SSH access. Full setup
notes (WSL, hooks, Box remotes) are under [Contributor reference](#contributor-reference).

### 1 · Cold-start migration (dump → OLTP store)

Builds the OLTP store from the raw MySQL dump (`data/raw/Dump20250730.sql`,
3.2 GB — `uv run dvc pull data/raw/Dump20250730.sql.dvc`). One command: brings
up an ephemeral MySQL, restores the dump, validates and loads both tables
through one shared insert routine:

```bash
uv run alembic upgrade head   # create/upgrade the OLTP schema
bash scripts/migrate.sh       # tunables: MYSQL_PORT, KEEP_MYSQL=1, DUMP, ...
```

Writes to `OLTP_DB_URL` — default local SQLite at `data/oltp/janasunani.db`;
set `postgresql+asyncpg://…` to target Postgres (what the CPU box runs). The
load is idempotent and deterministic; a full run yields **1,371,288 complaints
/ 6,556,171 action-history rows**. Variants (existing MySQL, live sync):
[janasunani/migration/README.md](janasunani/migration/README.md).

### 2 · Materialize the Parquet lake (OLTP → analytics)

```bash
dvc repro materialize         # local SQLite OLTP
uv run janasunani-materialize # any engine (required path when OLTP is Postgres),
                              # then `dvc commit` the Parquet outs
```

Query it:

```python
from janasunani.olap import lake
lake.query("SELECT category, count(*) AS n FROM complaints GROUP BY 1 ORDER BY n DESC")
lake.read("complaints")       # whole table as a Polars DataFrame
```

### 3 · Document pipeline (scanned docs → text/redaction/summary/category)

Heavy deps live in three mutually-conflicting extras (`pipeline-core`,
`ocr-deepseek`, `categorizer`) — run stage subsets per env:

```bash
uv run --extra pipeline-core janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --ocr-engine pytesseract          # deepseek needs CUDA (the GPU box)

dvc repro pipeline-sample           # the 2-doc end-to-end regression stage
bash scripts/gpu_smoke.sh           # on the GPU box: DeepSeek OCR smoke
```

Needs the `tesseract` binary (+ `tesseract-lang` for Odia). Stage details,
flags, and the artifact-DB design:
[janasunani/pipeline/README.md](janasunani/pipeline/README.md).

### 4 · Export pipeline outputs and evaluate PII

```bash
uv run janasunani-export-pipeline --db data/processed/pipeline.sqlite  # → OLTP (idempotent upserts)
uv run --extra pipeline-core janasunani-evaluate-pii --gold <gold.jsonl> # gate: coverage ≥ 0.8056
```

### 5 · Sample English complaints + documents (evaluation bundles)

```bash
uv run --extra pipeline-core python scripts/sample_english_complaints.py   # --n/--seed/--out
```

Picks N complaints that are English on both sides — the grievance subject (not
Odia, not romanized Odia) *and* the scanned document (judged by the pipeline's
format classifier + page-type ViT; documents that are pure PII like an Aadhaar
are dropped) — downloads the S3 documents (STANDARD storage class only), and
writes one zip: the documents + a `complaints.parquet` of their metadata and
gate evidence. Selection logic: [scripts/README.md](scripts/README.md).

### 6 · Document ingestion (complaint files → S3)

```bash
uv run janasunani-ingest-documents
```

Downloads each complaint's document to S3 (or local disk in dev) and records
status back into OLTP. *Currently parked: live Janasunani API credentials are
unavailable.*

### 7 · Demo API (default mock or opt-in live processor)

```bash
uv run --extra serving janasunani-api        # mock; http://127.0.0.1:8000, docs at /docs

uv run --extra demo janasunani-demo-preflight # check models + OCR binaries are ready
uv run --extra demo janasunani-api-live       # real models behind the same contract
```

The full endpoint surface the frontend builds against (`POST /grievance`,
`GET /grievance/{id}`, `GET /history`, `/health`) with a mocked processor
returning the real response shapes — no models load. The live command (the
conflict-free `demo` extra) strictly loads local DVC model artifacts and runs
pytesseract, Presidio, MuRIL, BART, and rules routing behind the same contract,
persisting each submission to `live_grievances` when `OLTP_DB_URL` is set.
Full step-by-step bring-up (preflight → Postgres/migrations → launch → health →
submit): **[docs/DEMO.md](docs/DEMO.md)**.
Contract details: [janasunani/serving/README.md](janasunani/serving/README.md).

### Tests (the gate for every change)

```bash
uv run --extra pipeline-core pytest && uv run ruff check .
```

**Never against the production Postgres container** — see
[tests/README.md](tests/README.md).

### Cloud

Terraform creates both EC2 boxes ([deploy/terraform/README.md](deploy/terraform/README.md));
docker-compose runs the stack on the CPU box ([deploy/README.md](deploy/README.md)).
The GPU box is a `gpu_box_count = 0/1` toggle (~$1/hr while up).

## Documentation

- [docs/ROADMAP.md](docs/ROADMAP.md) — the plan and current status (source of
  truth for sequencing), including a project snapshot for a fresh reviewer.
  **New agent or contributor? Start here.**
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system overview: data flow,
  storage layers, environments, infrastructure, invariants.
- [docs/DEMO.md](docs/DEMO.md) — live-inference demo runbook (preflight →
  Postgres/migrations → `janasunani-api-live` → health → submit).
- [docs/DEPLOY.md](docs/DEPLOY.md) — end-to-end cloud deployment runbook
  (provision → migrate → run → back up; the two boxes and hard rules).
- Per-package detail: [db](janasunani/db/README.md) ·
  [migration](janasunani/migration/README.md) ·
  [ingestion](janasunani/ingestion/README.md) ·
  [olap](janasunani/olap/README.md) ·
  [pipeline](janasunani/pipeline/README.md) ·
  [inference](janasunani/inference/README.md) ·
  [serving](janasunani/serving/README.md) ·
  [deploy](deploy/README.md) ·
  [terraform](deploy/terraform/README.md) ·
  [scripts](scripts/README.md) ·
  [tests](tests/README.md)

## Contributor reference

### Setup notes

Do not run `make setup` with `sudo`, including on WSL. Setup installs missing
user-level tools such as `uv`, `rclone`, and Linux/WSL AWS CLI v2 into
`~/.local/bin`, and adds that directory to `~/.bashrc` and `~/.profile` if it is
not already on your shell PATH. Git remains a system prerequisite.

On WSL, clone this repository inside the Linux filesystem, such as
`~/Documents/GitHub/janasunani`, not under `/mnt/c/...`. Creating the
Python virtual environment on the Windows-mounted filesystem can fail with
permission errors.

If your rclone Box remote should use a non-default name, run:

```bash
make setup BOX_REMOTE=<remote-name>
```

To enable only the repository Git hooks in an existing checkout, run:

```bash
make install-hooks
```

The pre-commit hook blocks local-only state and secret files such as Terraform
state, tfvars, `.env`, PEM files, and private SSH keys before they enter Git
history.

### Box paths and data ops

You can create an optional local `.env` file in the repo root to configure
machine-specific Box/rclone paths:

```dotenv
BOX_REMOTE=box
BOX_PROJECT_ROOT=2. Projects/21. Governance/
```

`.env` is ignored by Git and should be used only for local path or remote-name
settings, not credentials or data files. It uses the same dotenv syntax
`Settings` reads (`janasunani/config.py`) — plain `KEY=value` per line, an
optional `export ` prefix, and optional quotes around the value — not Make
syntax: the Makefile parses it with python-dotenv rather than including it as
Makefile text, specifically so a value containing `$`, `#`, or a quote
character is read literally instead of being misread as Make syntax.

Most users only need to set `BOX_REMOTE` and `BOX_PROJECT_ROOT`; the Makefile
derives the full remotes from those values. Collaborators may see the same
shared Box folder under different path prefixes — print the resolved paths with
`make box-paths`, and override per command
(`make deliver BOX_PROJECT_ROOT="DPIC/janasunani"`) or persistently in `.env`.
If a derived path doesn't match your Box layout, override the full endpoint
(`INCOMING_REMOTE` / `EXHIBITS_REMOTE`) in `.env` — write the bare value, with
no manual shell quoting around spaces (`INCOMING_REMOTE=box:My Custom Path/`,
not `box:'My Custom Path/'`): the `ingest`/`publish-raw`/`deliver` recipes
quote it for you at the point they hand it to `rclone`, so a value with
spaces (the default `BOX_PROJECT_ROOT` above has one) or an embedded `'`
reaches `rclone` as a single argument either way.

Common data operations:

```bash
make ingest DATA=survey_dump.csv    # import a stakeholder original from Box incoming
make push DATA=survey_dump.csv      # record an approved version through DVC
make publish-raw DATA=api_dump.csv  # publish a local raw file to Box incoming
make pull                           # restore approved project data from DVC
make run                            # run the dvc.yaml stages
make deliver                        # publish figures/tables/reports to Box
```

### Contributing

Keep pull requests small enough for a reviewer to understand in one sitting.
Separate unrelated changes into separate PRs, especially when data, analysis
logic, and report formatting change independently.

Before opening a PR, run:

```bash
uv run ruff check .
uv run pytest
```

Use Ruff for Python linting. Prefer small, explicit functions and project-local
helpers over one-off notebook-only logic when code will be reused.

If you work with notebooks, install the output-stripping hook once
(`uv run nbstripout --install`) and commit notebooks only after outputs have
been stripped. Do not commit large rendered notebook outputs, temporary
exports, or local execution artifacts.

Data files under `data/` are proprietary by default. Do not commit raw,
interim, processed, or output data directly to Git. Use DVC for approved data
versions and `make deliver` for stakeholder-facing Box delivery.

Use the [pull request template](.github/PULL_REQUEST_TEMPLATE.md) when opening
a PR. CI expects repository secret `DPIC_GITHUB_SSH_KEY` when private `dpic`
dependency resolution is required.
