# janasunani

**Janasunani 2.0** — Odisha's unified, AI-powered grievance redressal system.
A raw grievance (typed text or a scanned document) is **extracted** (OCR),
**redacted** (PII, in-process by default), **triaged** with advisory low-signal
and corpus-level duplicate evidence, **classified** (category/department), **summarized**,
and **routed** to a suggested office, ending in a Next.js demo UI. The system
never auto-rejects a grievance: officers retain the consequential decisions.

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
- **The demo — in progress:** single-grievance inference, hybrid routing,
  FastAPI serving, and the Next.js demo. The pipeline-quality trunk adds the
  bounded spam advisory, an optional checksummed actionability scorer, a
  duplicate index, chronological category and routing evaluation, an opt-in
  empirical-Bayes incidence router, a summary scorecard, cached Sarvam evidence
  import, and immutable local model releases. `make up` brings the API and the
  frontend up together. MLflow is a pre-deploy control plane for evaluation and
  alias resolution; serving never contacts MLflow or a public model hub.

  Per-phase status is in [docs/ROADMAP.md](docs/ROADMAP.md) §2, which is the
  only place it is recorded — code being on `main` is not the same as a phase
  being closed.

  Re-scoped 2026-07-27 to five components: pipeline replication, spam & duplicate
  detection, the intelligence layer, A/B testing of AI automation, and a Sarvam
  benchmark. See [docs/ROADMAP.md](docs/ROADMAP.md) §1.1.

New here? Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first;
[docs/ROADMAP.md](docs/ROADMAP.md) is the plan and current status.
[docs/DELIVERY.md](docs/DELIVERY.md) is the dated plan for the 14 August demo,
in plain language.

## Pipeline-quality snapshot

The new evaluation system deliberately separates a useful development result
from a production claim. Every governed report records its input fingerprint,
split policy, parameters and evidence status; the bundle stays
`publication_ready=false` until every required release-quality and impact gate
exists.

| Capability | Current measured evidence | Boundary |
|---|---|---|
| Actionability review | 94.74% accuracy; 13/13 non-actionable cases sent to review; 3/44 actionable cases also reviewed (viewed test, n=57) | Binary, frontier-adjudicated development evidence; no `out_of_scope` support; advisory and not release-eligible |
| Categorization | 46.55% top-1 and 90.89% top-3 agreement (chronological 2024 test, n=3,160) | Agreement with historical labels, not policy correctness; no promoted release artifact |
| Routing | 45.14% top-1 and 69.04% top-3 agreement (chronological 2025 test, n=208,267) | Agreement with historical destination, not correct authority or citizen outcome |
| Summary | 55/84 critical facts retained; 8/26 generated drafts usable without edit; 4/26 had residual PII (enriched n=30) | Single-frontier-judge development diagnostic, not officer-confirmed quality |
| Sarvam OCR | 56 cached paired successful pages; all normalized outputs differed; Sarvam emitted 1.3345× as many characters | Coverage/divergence evidence only; no hand-transcribed OCR accuracy or new paid calls |

The numbers, denominators and limitations are maintained in
[QUALITY_BENCHMARKS.md](docs/QUALITY_BENCHMARKS.md). The client-facing evidence
reports are indexed in
[docs/value-add-report/README.md](docs/value-add-report/README.md).

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

### 2b · Findings over the lake (marts → presentable numbers)

```bash
uv run janasunani-closure-finding              # → outputs/findings/
uv run janasunani-closure-finding --print-sql  # the view definitions, for handover
```

Governed SQL marts plus the findings built on them, with the caveats each number
must be quoted with: [janasunani/analytics/README.md](janasunani/analytics/README.md).

### 2c · Intelligence layer (workload, spikes, themes)

```bash
uv run janasunani-publish-workload      # filings vs distinct problems (dedup-adjusted)
uv run janasunani-publish-intelligence  # EWMA spike decomposition over category × district × week
uv run janasunani-publish-themes        # concentrated-and-rising themes within one category
```

Aggregates only, computed from redacted text and digest-guarded dedup groups —
never raw grievance text. The workload and spike outputs need the dedup index
(§4b) to exist first.

### 2d · Routing crosswalk (history → category/district → department)

```bash
uv run janasunani-build-crosswalk       # → janasunani/routing/reference/routing_crosswalk.json
```

The ORTPSA master tables carry no category-to-department link, so the crosswalk
is learned from where complaints were actually sent. It is the first rung of
the live router, ahead of the mapping tables and the generic fallback. It
records incidence, not outcome quality: see the module docstring in
[janasunani/routing/crosswalk.py](janasunani/routing/crosswalk.py) before
quoting it as a recommendation.

### 3 · Document pipeline (scanned docs → text/redaction/summary/category)

Heavy deps live in four extras (`pipeline-core`, `pii`, `ocr-deepseek`,
`categorizer`), alongside the light `serving` extra and `demo`, the
conflict-free set the live API runs on. `pii` is a small, compiler-free
redaction/evaluation environment, intentionally separate from the inherited
`numpy<2` pipeline environment. Run the stages that need each extra in
sequential invocations against the same artifact DB:

```bash
uv run --extra pipeline-core janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --ocr-engine pytesseract \
  --stages format_classifier ocr_extraction  # deepseek needs CUDA (the GPU box)

uv run --extra pii janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --stages pii_tagger

uv run --extra pipeline-core janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --stages page_type_classifier summarizer

uv run --extra categorizer janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --stages categorizer

dvc repro pipeline-sample           # the 2-doc end-to-end regression stage
bash scripts/gpu_smoke.sh           # on the GPU box: DeepSeek OCR smoke
```

Needs the `tesseract` binary (+ `tesseract-lang` for Odia). Stage details,
flags, and the artifact-DB design:
[janasunani/pipeline/README.md](janasunani/pipeline/README.md).

### 4 · Export pipeline outputs and evaluate PII

```bash
uv run janasunani-export-pipeline --db data/processed/pipeline.sqlite  # → OLTP (idempotent upserts)
uv run --extra pii janasunani-evaluate-pii --gold <gold.jsonl> # gate: coverage ≥ 0.8056
```

### 4b · Triage over a corpus slice (redact → dedup → spam)

```bash
uv run --extra pii janasunani-redact-grievance --district Sambalpur --year 2024
uv run janasunani-dedup-index --slice Sambalpur/2024   # near-duplicate groups → OLTP
uv run janasunani-spam-score --slice Sambalpur/2024    # prevalence CSV + Markdown
uv run janasunani-spam-scorecard --slice Sambalpur/2024
```

Slice-at-a-time by design: everything downstream reads the redacted text, not
the raw grievance. The dedup index is what the workload and spike findings
(§2c) count distinct problems with.

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

### 7 · Demo API and frontend (default mock or opt-in live processor)

```bash
make up                                       # API + Next.js UI together; Ctrl-C stops both
make down                                     # tear down API, frontend, throwaway Postgres
make rehearsal                                # the 13 Aug freeze gate (static + stack + artifacts)
```

Or one piece at a time:

```bash
uv run --extra serving janasunani-api        # mock; http://127.0.0.1:8000, docs at /docs

make models                                  # legacy category/page-type mirrors only
# Provision local BART via an approved release (see §9) before live startup.
uv run --extra demo janasunani-demo-preflight # check local models, release + OCR binaries
uv run --extra demo janasunani-api-live       # real models behind the same contract
make frontend                                 # Next.js UI on :3000, pointed at the API
```

The full endpoint surface the frontend builds against (`POST /grievance`,
`GET /grievance/{id}`, `GET /history`, `/health`) with a mocked processor
returning the real response shapes — no models load. The live command (the
conflict-free `demo` extra) resolves each model from an explicit operator
override, then an active immutable release, then its local DVC mirror. It makes
no serving-time MLflow or public-hub call. It runs pytesseract, Presidio,
MuRIL, BART, bounded advisory triage, and the crosswalk → mappings → fallback
router behind the same contract, persisting each submission to `live_grievances`
when `OLTP_DB_URL` is set. `/health` reports `{"processor":"pipeline"}` once
warm-up finishes; detailed model-release, router and triage readiness is exposed
by preflight. The mock returns `routing.method: "mock"` and the UI badges it as
such.

Live triage runs after redaction. `JANASUNANI_TRIAGE=bounded` is the default;
`model` adds the checksummed binary actionability advisory when available and
falls back safely; `off` disables triage explicitly. `JANASUNANI_ROUTER=incidence`
opts into the checksummed historical-incidence router. Neither model changes the
never-auto-reject policy, and historical routing evidence is not a jurisdiction
decision.
Full step-by-step bring-up (preflight → Postgres/migrations → launch → health →
submit): **[docs/DEMO.md](docs/DEMO.md)**.
Contract details: [janasunani/serving/README.md](janasunani/serving/README.md).
Frontend: [frontend/README.md](frontend/README.md).

### 8 · Governed quality benchmarks and the Sarvam comparison

```bash
uv run python scripts/benchmark_pipeline.py          # per-stage latency, ticket-clustered SE
uv run janasunani-evaluate-sarvam --input <dir> --out outputs/sarvam --dry-run
uv run janasunani-evaluate-benchmark                 # → outputs/benchmark/ (Table 2)
uv run janasunani-evaluate-benchmark --check         # validate existing outputs, regenerate nothing

uv run dvc repro --single-item actionability-local-candidate-benchmark
uv run dvc repro --single-item categorization-historical-benchmark
uv run dvc repro --single-item routing-historical-benchmark
uv run dvc repro --single-item summary-development-benchmark
uv run dvc repro --single-item full-benchmark-bundle
```

`--dry-run` renders and runs pytesseract only: no Sarvam call, no spend. Dropping
it sends citizen text off the box through the audited egress channel in
[janasunani/egress/sarvam.py](janasunani/egress/sarvam.py), which logs every call
to an audit DB and enforces the run's rate and spend limits. Read
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) "Security invariants" before the
first real run.

The final DVC stage writes `outputs/benchmark/full_benchmark.json` and `.md`.
It is a governance bundle, not a leaderboard: missing officer-confirmed release
sets and measured workflow/citizen outcomes intentionally keep publication
readiness false. See [QUALITY_BENCHMARKS.md](docs/QUALITY_BENCHMARKS.md) for the
exact stage commands, schemas and claim boundaries. Existing Sarvam evidence
can be imported to MLflow with `janasunani-import-sarvam-evidence` without an
API call; importing evidence does not upgrade it to OCR-accuracy evidence.

### 9 · Materialize and switch an immutable model release

Copy [deploy/model-release.example.json](deploy/model-release.example.json) and
replace **every** review placeholder. Never run the example unchanged.

```bash
uv run janasunani-model-release materialize \
  --spec <approved-release.json> \
  --release-root models/releases \
  --activate

uv run --extra demo janasunani-demo-preflight --strict

# rollback: activate a previously materialized, checksum-valid manifest
uv run janasunani-model-release activate \
  models/releases/<old-release>/release-manifest.json
```

MLflow resolves reviewed aliases only during materialization. The immutable
manifest and local artifact hashes are the runtime contract. Full provenance,
override precedence and rollback semantics: [MODELS.md](docs/MODELS.md).

### Tests (the gate for every change)

```bash
uv run --extra serving --extra pipeline-core pytest
uv run --extra pii pytest tests/test_pii_extra_contract.py tests/test_pii_redaction.py tests/test_redact_grievance.py tests/test_rederive_pii_draft.py tests/test_bootstrap_pii_gold.py
uv run ruff check .
```

**Never against the production Postgres container** — see
[tests/README.md](tests/README.md). The complete pre-PR list (lockfile,
standards sync, DVC dag, nbstripout) is in
[CONTRIBUTING.md](CONTRIBUTING.md#required-checks).

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
- [docs/DELIVERY.md](docs/DELIVERY.md) — the dated commitment for 14 August:
  what is promised, who owns it, what the fallback is. Governs demo scope.
- [docs/DEPLOY.md](docs/DEPLOY.md) — end-to-end cloud deployment runbook
  (provision → migrate → run → back up; the two boxes and hard rules).
- [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) — the timed 43-minute client
  walkthrough on the laptop stack.
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — measured numbers, with the
  commit and date each was measured against.
- [docs/QUALITY_BENCHMARKS.md](docs/QUALITY_BENCHMARKS.md) — governed
  actionability, category, routing, summary, PII and Sarvam evidence, including
  development/release boundaries.
- [docs/MODELS.md](docs/MODELS.md) — model inventory, parameterizations,
  immutable releases, local resolution and rollback.
- [docs/IMPACT_METRICS.md](docs/IMPACT_METRICS.md) — the metric registry linking
  model evidence to officer workflow and citizen outcomes.
- [docs/value-add-report/README.md](docs/value-add-report/README.md) — long
  evidence report, short IAS brief and prospective capability brief.
- [docs/FINDINGS.md](docs/FINDINGS.md) — the five reproducible findings for the
  demonstration.
- [docs/PII_GOLD_ENSEMBLE.md](docs/PII_GOLD_ENSEMBLE.md) — the ensemble-gold
  protocol standing in for the human annotation pass.
- [docs/AB_PLAN.md](docs/AB_PLAN.md) — the A/B estimator, power calculation and
  assignment design. Locked before any outcome data is viewed.
- [docs/CPU_BOX_RUNBOOK.md](docs/CPU_BOX_RUNBOOK.md) — operating the CPU box.
- [CONTRIBUTING.md](CONTRIBUTING.md) — required checks, data and PII policy,
  gold-metric gates, PR size, and the review protocol.
  [AGENTS.md](AGENTS.md) covers where things live and how to run them.
- Per-package detail: [db](janasunani/db/README.md) ·
  [migration](janasunani/migration/README.md) ·
  [ingestion](janasunani/ingestion/README.md) ·
  [olap](janasunani/olap/README.md) ·
  [analytics](janasunani/analytics/README.md) ·
  [pipeline](janasunani/pipeline/README.md) ·
  [inference](janasunani/inference/README.md) ·
  [serving](janasunani/serving/README.md) ·
  [frontend](frontend/README.md) ·
  [deploy](deploy/README.md) ·
  [terraform](deploy/terraform/README.md) ·
  [scripts](scripts/README.md) ·
  [tests](tests/README.md)

`routing`, `egress`, `evaluation`, `tracking`, and `pii` have no package README
yet; their module docstrings carry the design notes.

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
reaches `rclone` as a single argument either way. If your `.env` still has
the old hand-quoted form from before this recipe-boundary quoting existed
(`INCOMING_REMOTE=box:'Some/Path/'`), the Makefile now refuses to run with a
loud error instead of silently misreading it — remove the quote marks.

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

**[CONTRIBUTING.md](CONTRIBUTING.md) is authoritative** for the full pre-PR
check list, the data and PII policy, the gold-metric gates, PR size, and the
review protocol. `uv run pytest` on its own is not the gate — the suite needs
extras, and the PII suite runs in its own environment. Org-wide commit, branch
and review conventions live in
[.dpic/standards/agent-conventions.md](.dpic/standards/agent-conventions.md),
synced from `dpic-org` and not edited here.

Keep pull requests small enough for a reviewer to understand in one sitting.
Separate unrelated changes into separate PRs, especially when data, analysis
logic, and report formatting change independently.

Prefer small, explicit functions and project-local helpers over one-off
notebook-only logic when code will be reused. If you work with notebooks,
install the output-stripping hook once (`uv run nbstripout --install`) and
commit notebooks only after outputs have been stripped. Do not commit large
rendered notebook outputs, temporary exports, or local execution artifacts.

Data files under `data/` are proprietary by default. Do not commit raw,
interim, processed, or output data directly to Git. Use DVC for approved data
versions and `make deliver` for stakeholder-facing Box delivery.

Use the [pull request template](.github/PULL_REQUEST_TEMPLATE.md) when opening
a PR. CI expects repository secret `DPIC_GITHUB_SSH_KEY` when private `dpic`
dependency resolution is required.
