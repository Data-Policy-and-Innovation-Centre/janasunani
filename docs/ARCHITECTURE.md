# Janasunani 2.0 — Architecture

> The **what and why** of the codebase, for someone landing here cold.
> The **plan and status** live in [ROADMAP.md](ROADMAP.md) (source of truth for
> sequencing); per-package detail lives in the READMEs linked throughout.

## What this is

An AI-powered grievance redressal prototype for Odisha. A raw grievance — typed
text or a scanned document — is **extracted** (OCR), **redacted** (PII),
**triaged** with advisory low-signal and duplicate evidence, **classified**
(category), **summarized**, and **routed** to a suggested office, ending in a
Next.js demo UI. Triage does not auto-reject or dispose of a grievance.

The repo consolidates two earlier projects (a grievance backend and the DSI
document-processing pipeline) into one `janasunani/` package, in three parts:

- **Part I — Foundation** *(built)*: data migration into an OLTP store, Parquet
  materialization, document ingestion → S3, the document pipeline, and the
  Terraform-provisioned boxes. The *demo stack* on those boxes is Phase 12 and has
  not had its first live bring-up yet (issue #30).
- **Part II — The demo** *(in progress)*: single-grievance inference, routing,
  FastAPI serving, Next.js UI, plus the five components below.
- **Part III — Post-demo maturity** *(planned, with evaluation/release
  down-payments built)*: operational safety, Odia-first models, governance
  intelligence, and a jurisdiction pack for portability — see
  [ROADMAP.md](ROADMAP.md) §6 and the direction summary below.

The demo is scoped by five components (set 2026-07-27, full detail in
[ROADMAP.md](ROADMAP.md) §1.1 and §5):

| # | Component | Architectural impact |
|---|---|---|
| a | DSI pipeline replication | None. Exercises the existing six stages end to end |
| b | Spam & duplicate detection | Post-redaction live advisory plus the first corpus-level dependency (the dedup index) |
| c | Intelligence layer | New `serving/intelligence.py` router and a semantic layer over the lake |
| d | A/B testing of AI automation | New `experiments/` package; shadow-mode execution path |
| e | Sarvam benchmark | Provider-backed model registry, a single egress module, and a redrawn trust boundary |

*(Status is not recorded here. It lives in [ROADMAP.md](ROADMAP.md) §2, in one
table, so the two documents cannot drift.)*

Two of them change the architecture rather than extend it: (b) adds a
post-redaction advisory and the first corpus-level dependency, and (e)
**redraws the trust boundary** (see Security invariants).

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
 page-type → summarize                       into OLTP) Parquet lake (data/interim/)
 → categorize                                            = analytics / ML / demo history
        │                                                        │
        └────────── dedup index ◀─────────────────────────────────┘
```

The live processor adds bounded post-redaction triage, category, summary and
routing suggestions. Models resolve locally from an operator override, then an
active immutable release manifest, then a DVC mirror. The only implemented
third-party model route is the governed Sarvam egress path:

```
pipeline stage ──▶ janasunani/egress (the single outbound module)
                        │  audit log: ticket, stage, provider, model ID,
                        │             bytes, authorization ref
                        ▼
                   Sarvam hosted API        (trust_tier: authorized-external)
                   or GPU box, Sarvam       (trust_tier: dpic-infra)
                   Apache-2.0 weights
```

Three storage layers, deliberately distinct
([db/README](../janasunani/db/README.md), [olap/README](../janasunani/olap/README.md),
[pipeline/README](../janasunani/pipeline/README.md)):

| Layer | Engine | Role |
|---|---|---|
| **OLTP store** | SQLite / Postgres via `OLTP_DB_URL` (async SQLAlchemy, Alembic) | System of record: 1.37M complaints, 6.56M action-history rows, plus exported pipeline outputs. Live writes land here. |
| **Parquet lake** | Files in `data/interim/`, DuckDB/Polars readers | Read-optimized downstream copy, produced by `janasunani-materialize`. The demo's history browse and all ML/analytics read this, never OLTP. |
| **Pipeline artifact DB** | Standalone SQLite per run | The document pipeline's own working state (`pages`/`documents`/`unreadable_pages`), resumable by design. Reaches OLTP only through the exporter. |
| **Dedup index** *(built for the governed Sambalpur-2024 slice)* | MinHash / LSH signatures over redacted grievance records | Corpus-level state used by duplicate-adjusted analytics. Keys derived from `petitioner_mobile` / `petitioner_email` are **salted hashes**, never raw values — though a salted hash of a mobile number is still personal data under DPDP, so it is access-controlled, not treated as anonymous. Per-request live matching is not wired. |

Verified full-scale counts (local **and** cloud Postgres, must match after any
migration change): **1,371,288 complaints / 6,556,171 action-history rows**.

## Package map

| Path | What lives there |
|---|---|
| [`janasunani/config.py`](../janasunani/config.py) | Paths (`directories`), settings (`settings`, pydantic-settings from env/`.env`), loguru helpers. The two things everything else imports. |
| [`janasunani/samples.py`](../janasunani/samples.py) | The registry of named corpora and `require_sample()`, the guard that refuses a random draw for a population-level measurement. Stdlib-only so anything can consult it. **The source of truth for which sample a measurement may run on** — do not restate its entries here or anywhere else. |
| [`janasunani/db/`](../janasunani/db/README.md) | ORM models, async session, CRUD, Alembic migrations. Engine-portable (SQLite + Postgres). |
| [`janasunani/migration/`](../janasunani/migration/README.md) | Cold-start dump loader + live-MySQL sync, converging on one validated insert routine. |
| [`janasunani/ingestion/`](../janasunani/ingestion/README.md) | Janasunani API client, S3 service, document downloader, and the Pydantic schemas that are the **single raw→ORM column map**. |
| [`janasunani/olap/`](../janasunani/olap/README.md) | `materialize` (OLTP → Parquet via DuckDB scanners) and `lake` (read helpers). |
| [`janasunani/pipeline/`](../janasunani/pipeline/README.md) | The six-stage batch document pipeline (DSI refold), its artifact DB, the OLTP exporter, OCR quality guards, and `pii_eval` — the release gate the pipeline itself runs. |
| [`janasunani/inference/`](../janasunani/inference/README.md) | Single-item inference for the live warm processor. Same models as `pipeline/`, one item at a time instead of a batch. |
| [`janasunani/serving/`](../janasunani/serving/README.md) | The demo API: endpoints, response schemas (the frozen frontend contract), triage advisory, supervisor aggregates, history. |
| [`janasunani/routing/`](../janasunani/routing/__init__.py) | Rules-first grievance routing: mappings, the empirical category→department crosswalk, and an opt-in checksummed empirical-Bayes incidence provider (`JANASUNANI_ROUTER=incidence`). Both learn historical destination, not correct authority or outcomes, and fall through safely. |
| [`janasunani/analytics/`](../janasunani/analytics/README.md) | The analytical layer over the Parquet lake: governed marts, the findings scripts, and the action-type lookup. |
| [`janasunani/evaluation/`](../janasunani/evaluation/__init__.py) | Governed, report-only harnesses for PII, actionability/weak labels, categorization, summary, historical routing and Sarvam, plus the full evidence bundle. They measure evidence; release gates remain explicit. |
| [`janasunani/pii/`](../janasunani/pii/__init__.py) | PII gold-set construction: the ensemble labelling helpers, agreement report and adjudication queue (#15). |
| [`janasunani/egress/`](../janasunani/egress/__init__.py) | The **only** package permitted to send citizen data to an `authorized-external` destination. Provider route registry, per-call audit log, rate limiting, kill switch, and the governance gate. |
| [`janasunani/tracking/`](../janasunani/tracking/__init__.py) | DVC owns artifact bytes; MLflow records runs/versions and resolves reviewed aliases only in the pre-deploy control plane. A checksummed immutable release manifest is activated atomically, and runtime resolution remains local-only. See [`MODELS.md`](MODELS.md). |
| [`janasunani/experiments/`](../janasunani/experiments/__init__.py) | Research-only experiment implementations. The routing-outcome package builds local aggregate diagnostics and does not feed serving or egress. Phase 16 assignment, exposure, and shadow-mode instrumentation remain unbuilt. |
| [`deploy/`](../deploy/README.md) | docker-compose for the CPU box; [Terraform](../deploy/terraform/README.md) for both EC2 boxes. |
| [`scripts/`](../scripts/README.md) | Operational one-offs: `migrate.sh` (cold-start), `gpu_smoke.sh` (DeepSeek smoke), `sample_english_complaints.py` (evaluation bundles), `setup.sh`. |
| [`tests/`](../tests/README.md) | Real-code-path pytest suite. Read the README before running tests anywhere near production. |

## Environments and the dependency split

Python ≥ 3.13, managed by **uv**. Base deps are light; heavy ML stacks live in
four **optional extras** (`pyproject.toml`):

- `pipeline-core` — format classifier, pytesseract OCR, page-type ViT,
  summarizer (`transformers>=4.57,<5`); its inherited `numpy<2` pin remains
  isolated from the standalone `pii` extra
- `pii` — Presidio, spaCy, and the English model; a numpy 2.x floor gives the
  standalone redaction/evaluation environment CPython 3.13 wheels
- `ocr-deepseek` — DeepSeek OCR only (`transformers==4.46.3` — **conflicts** with
  the others; declared in `[tool.uv].conflicts`)
- `categorizer` — MuRIL categorizer

The conflict is load-bearing: one environment can never hold both transformers
pins, so **stages import their deps lazily** and deploy runs one env per extra
(`uv run --extra X`) against the same artifact DB. `scripts/gpu_smoke.sh` is the
canonical demonstration. `scikit-learn>=1.8,<1.9` is pinned to the pickle era of
the inherited estimators.

## Infrastructure (two boxes)

Both boxes live in **DPIC's own AWS account**, not on Government of Odisha
infrastructure. Production deployment is a post-approval vendor handover, not
something this repo targets. Managed by
[Terraform](../deploy/terraform/README.md), state local, region ap-south-1, IAM
instance roles only (no static keys):

- **CPU box** (always on, t3.large, Elastic IP 52.66.116.80): Postgres OLTP in
  Docker ([compose](../deploy/README.md)), migration/materialization one-offs,
  nightly `pg_dump` → S3. The compose stack also defines the `api` / `frontend` /
  `proxy` (Caddy) services with CI → GHCR → box deploy automation
  (`.github/workflows/deploy.yml`); the stack is not yet brought up live on the
  box (first live bring-up tracked in #30). MLflow is available to the
  evaluation and pre-deploy release control plane, never the serving process.
- **GPU box** (on demand, g6.xlarge/L4, `gpu_box_count = 0/1` toggle, ~$1/hr
  while up): DeepSeek OCR batch runs and demo windows. Built from the Deep
  Learning Base AMI; created and destroyed per use — nothing stateful on it.

## DVC: what it tracks and what it doesn't

DVC (S3 remote `dpic-dvc-cache/janasunani`) versions **file artifacts** — the raw
dump, the Parquet lake, mirrored models under `models/`, the document sample —
and **file-in/file-out transforms**. In addition to `pipeline-sample` and
`materialize`, `dvc.yaml` governs actionability adjudication/candidates,
weak-label audit, chronological categorization and routing, summary development,
PII development, the full benchmark bundle and presentation tables. Operational
jobs whose effects live in external systems
(migration → OLTP, ingestion → S3, backfills) are **not** DVC stages; they run
as CLIs/cron and reach the lake via `materialize`. With Postgres OLTP, run
`janasunani-materialize` directly then `dvc commit` (the `materialize` stage
deps on the SQLite path).

## Model provenance (hard rule)

The DSI team disbanded (2026-07-03) and their Box data is gone. Production
runtime loads models **only** from an explicit operator override, an active
checksum-validated local release manifest, or our DVC mirrors under `models/`.
Remote public IDs are explicit development opt-ins, never an implicit startup
download and never a serving-time MLflow lookup. The PII model was the one
unrecoverable artifact; its
replacement is the Presidio-based stage (see
[pipeline/README](../janasunani/pipeline/README.md)). The DSI technical report's
measured baselines (the only surviving eval record) are recorded in
[ROADMAP.md](ROADMAP.md) — headline: legacy PII coverage **80.56%** any-overlap.
Treat these as **reference baselines, not release thresholds**: Phase 13 (PII) and Phase 18 (everything else)
re-measure the current models on our own data, per language, and sets per-task
release gates.

## Security invariants

- **Every route carrying citizen data declares its trust tier, and no route to a
  third party exists without a recorded authorization** *(changed 2026-07-27;
  replaces "citizen text never leaves the box")*. Two things broke the old rule:
  the Odisha government authorized Sarvam for this data including PII, and
  "the box" was never the real boundary anyway — documents already live in S3 and
  the GPU box is a second machine. What replaces it:
  - every registry entry declares a `trust_tier`: `same-host`, `dpic-infra`
    (a different machine, still DPIC-controlled: S3, the GPU box), or
    `authorized-external`. "On the box" was never the real boundary, since
    documents already live in S3 and the GPU box is a second machine;
  - exactly one module (`janasunani/egress/`) may make the outbound call, and CI
    should be able to prove no other path does;
  - every call is audit-logged with the authorization record it relies on;
  - each route records data class, destination, approval reference, retention
    terms, encryption, audit policy, and fallback. The tier is the index into
    those fields, not the control by itself;
  - a kill switch reverts every `authorized-external` entry to a **maintained**
    lower-tier counterpart. The GPU-box deployment was the exit ramp on the
    argument that Sarvam-30B is Apache 2.0 and fits the box; 30B has since been
    withdrawn and 105B exceeds the current 24 GB L4, so **that ramp is
    unconfirmed** until the licence and sizing are re-checked (#125). The
    same-host counterparts remain the working fallback;
  - a production network allowlist should permit only approved destinations.
    BART and the other mandatory live models are now required locally by
    default; an explicit development opt-in is required for a remote model ID.

  Unchanged: PII detection and redaction run in-process (Presidio + local spaCy)
  by default, and PII `start`/`end` offsets stay defined over the original text.

- **The lake is not PII-free, and it holds raw citizen prose.** The `complaints`
  structured columns (`petitioner_name`, `petitioner_mobile`, `petitioner_email`,
  `address`) are carried faithfully from the dump into both OLTP and the lake, and
  the Phase 14 dedup index keys off them. So is `complaints.grievance`, the
  citizen's own account of the problem: `materialize.py` copies it verbatim and it
  never meets `pii_tagger`. The guarantee that holds is narrower than it sounds:
  **no un-redacted *page* text reaches any downstream output.** Raw OCR text stays
  in the pipeline artifact DB; only `redacted_text` is exported. Historical
  `grievance` is redacted by its own batch pass before Phase 14 or 15 index it, and
  the resulting signatures and vectors stay `dpic-infra`. Control on the structured
  contact columns is access, not redaction. Full breakdown in
  [ROADMAP.md](ROADMAP.md) §3.2.
  Sending un-redacted text to an external PII detector is permitted by the
  authorization but is the highest-sensitivity call in the system; it is the last
  candidate to adopt, not the first. See [ROADMAP.md](ROADMAP.md) §3.1, §3.2, and §5.5.
- Postgres password only in the box's chmod-600 `.env` / gitignored
  `deploy/.env`. Terraform state/tfvars, SSH keys: local only (CI + pre-commit
  guards enforce; `no-raw-data-in-git` blocks data files).
- **Never `docker compose down -v`** on the CPU box — the OLTP volume holds the
  migrated production data.
- **Never run pytest on the CPU box against the prod container** — fixtures drop
  tables. See [tests/README](../tests/README.md).
- Source access uses SSH agent forwarding (no long-lived Git SSH key on the boxes).
  The CPU box does hold a **read-scoped GHCR PAT** for image pulls (Phase 12 deploy)
  — rotate/replace it (or move pulls to an instance-role-native registry); it is the
  one standing credential on the box.

## Roadmap direction (Part III — planned)

Post-demo work is sequenced **evaluation-first, workflow-first, platform-light**
(full detail + status in [ROADMAP.md](ROADMAP.md) Phases 18–24). The 2026-07-27
re-scope pulled four slices into the demo: the PII gold set (Phase 13), egress
enforcement and the model registry (Phase 17), the first analytics increments
(Phase 15), and shadow mode (Phase 16). The old Phases 13–19 were renumbered to
18–24; the crosswalk is in ROADMAP.md §2. What remains:

- **Evaluation and safety come first.** Before touching the models, build a
  per-task, per-language eval harness + governed gold sets, re-measure the current
  models as a baseline, and close the operational-safety gaps (RBAC, tested
  restore, audit). Egress enforcement moved earlier, into Phase 17, because Sarvam
  gave it a concrete reason to exist.
- **Language-first invariant.** `pages.language` is the spine every stage keys
  off. Today the pipeline degrades non-English to `Uncategorized`/`fallback` via
  English-only gates; Part III makes language first-class — an image-based signal
  before OCR to pick the OCR model, a text-based **IndicLID** refine once text
  exists (native **and romanized** Odia), romanized → script via **IndicXlit** as
  a *separate derived field* (transliteration isn't length-preserving, so PII
  offsets stay on the original text), Indic models the whole way down, and
  per-language measurement. Phase 17 benchmarks Sarvam against each of these
  choices, so which model fills each slot is decided per stage on gold-set
  evidence rather than fixed in advance.
- **Minimal modularity + model registry.** The pipeline keeps a **fixed canonical
  stage order shared by batch and live** (some orderings are policy invariants, not
  free choices); only **models** become swappable. MLflow aliases are resolved
  before deployment into a checksum-validated immutable local manifest. Serving
  resolves operator override → active manifest → DVC mirror, without importing
  MLflow or using the network. This is a **control-plane**, not a runtime
  dependency, and supports explicit activation and one-command rollback. A model
  reference becomes
  `{name, alias, provider, artifact_or_endpoint, version, trust_tier}`, so a
  local artifact, the Sarvam hosted API, and self-hosted Sarvam weights are three
  backends behind one entry. Caveat recorded in the roadmap: a hosted endpoint is
  not reproducible the way a pinned artifact is, so every call records the returned
  model ID and the benchmark re-runs on a schedule to catch drift.
- **Governance intelligence, on-demand and two-track.** The corpus becomes a
  governed natural-language analytics surface. A **structured** track ships first
  and is language-agnostic: a semantic/metrics layer over the lake, an agentic
  natural-language-to-SQL query loop on DuckDB (a local, structured-decoding model,
  no external API), spike/anomaly detection, and **case-mix-adjusted** comparisons
  rather than naive office rankings. It ships as trusted increments (metrics and
  dashboards, then spikes, then adjusted comparisons, NL query last) and the query
  path is guarded (allowlisted query plans, small-cell suppression, isolated
  read-only execution, audit). A **semantic/unstructured** track (on-box
  embeddings, case retrieval, emergent themes via local-LLM semantic operators;
  DuckDB VSS, capacity-gated) comes later, gated on the language normalization
  above. The analytics models stay **on-box**: the authorization covers grievance
  processing, and an official's free-text query is a different data class from the
  grievance itself. Sending queries out would need its own authorization, which we
  do not have and have not asked for.
- **Governed feedback + jurisdiction pack.** Officer corrections are captured,
  curated, and learned from in shadow mode first (not autonomous online learning);
  and the taxonomy/mappings/languages/thresholds/RBAC become portable config+data
  so the system can target a second jurisdiction, which is what makes it a DPI
  product rather than one deployment. Shadow mode arrives early, in Phase 16, since
  the A/B design needs the same mechanism.
- **Measured impact, not assumed impact.** Phase 16 adds an `experiments/` package
  and treats "the model is accurate" and "the automation helps" as separate claims
  needing separate evidence. Offline gold-set scores support the first only. The
  corpus already carries the outcome variables for the second (disposal time,
  transfers, reopens, `benefitted`), with `office_id` as the randomization and
  clustering unit.

## Gates

Every feature ships with real-code-path pytest tests, run before "done":

```bash
uv run --extra serving --extra pipeline-core pytest
uv run --extra pii pytest tests/test_pii_extra_contract.py tests/test_pii_redaction.py tests/test_redact_grievance.py tests/test_rederive_pii_draft.py tests/test_bootstrap_pii_gold.py
uv run ruff check .
```

CI
(`.github/workflows/`) runs ruff, the test suite against a service-container
Postgres plus `dvc status` validation, and the raw-data-in-git guard. CI installs
**no heavy extras** — anything imported by tests must live in a light module
(see [pipeline/README](../janasunani/pipeline/README.md) for the pattern).
