# Janasunani 2.0 — Roadmap

> Source of truth for **sequencing and status**. Architecture detail lives in
> [ARCHITECTURE.md](ARCHITECTURE.md), operations in [DEPLOY.md](DEPLOY.md), and
> per-package detail in the package READMEs. Phase status lives in exactly one
> place: the table in §2 — nowhere else in this document.

## 1. What we're building

An AI grievance-redressal system for Odisha. A raw grievance, either typed text
or an uploaded scanned document, is **extracted** (OCR), **redacted** (PII),
**classified** (category / subcategory / department), **summarized**, and
**routed** to the responsible office, ending in a Next.js demo.

The work is in three parts:

- **Part I — Foundation** *(built)*: consolidate two earlier repos into one
  `janasunani/` package and load the data — cold-start migration into a swappable
  OLTP store, the Parquet lake, document ingestion to S3, and the six-stage
  document pipeline.
- **Part II — Demo automation** *(in progress)*: real-time single-grievance
  inference, routing, FastAPI serving, and the Next.js demo, built
  API-contract-first so the visible deliverable never sits at the tail of a
  serial chain.
- **Part III — Post-demo maturity** *(planned)*: make Odia (including romanized
  Odia) a first-class citizen, turn the grievance history into governance
  intelligence, and make the system portable enough to interest other
  governments. Sequenced **evaluation-first** (§5).

## 2. Status — canonical phase list

This table is the only place phase status is recorded.

| Phase | Scope | Status |
|---|---|---|
| 0 | Scaffolding & dependencies | ✅ |
| 1 | OLTP layer (ORM models, session, CRUD, ingestion schemas) | ✅ |
| 2 | Cold-start migration dump → OLTP (1.37M / 6.56M rows) | ✅ |
| 2b | OLTP engine-swappable (`OLTP_DB_URL`, asyncpg, Alembic) | ✅ |
| 3 | OLTP → Parquet materialization + lake read helpers | ✅ |
| 4 | Document ingestion → S3 | ✅ |
| 5 | Document pipeline (6 stages, Presidio PII rebuild, GPU shakedown) | ✅ |
| 6 | Model tracking (DVC now; MLflow slim registry on branch) | 🔄 |
| 7 | CI (ruff + pytest on a Postgres service container) | ✅ *(docs pending)* |
| 8 | Real-time inference core (warm processor, live CLI) | ✅ |
| 9 | Routing (rules built; crosswalk + learned scorer deferred; demo = `fallback`) | 🔄 |
| 10 | Serving API (default mock + opt-in live wiring) | ✅ |
| 11 | Demo frontend (Next.js, DPIC-branded; first cut) | 🔄 |
| 12 | Demo integration & cloud deployment | 🔄 |
| 13 | Evaluation, gold sets & operational-safety foundation | ⬜ |
| 14 | Model & pipeline platform (fixed recipe + model registry + MLflow control-plane) | ⬜ |
| 15 | Governance intelligence I — spike & anomaly detection | ⬜ |
| 16 | Odia-first models (gated on Phase 13) | ⬜ |
| 17 | Governance intelligence II — semantic index, case retrieval, themes | ⬜ |
| 18 | Governed feedback loop | ⬜ |
| 19 | Jurisdiction pack (DPI portability) | ⬜ |

Anchor facts:

- Verified corpus, local SQLite **and** cloud Postgres, which must match after any
  migration change: **1,371,288 complaints / 6,556,171 action-history rows**.
- The demo ships with routing on **`method:"fallback"`**. Smarter routing is Part
  III; the end-to-end demo does not block on it.

## 3. Architecture in brief

Three storage layers, deliberately distinct (full detail in
[ARCHITECTURE.md](ARCHITECTURE.md)):

- **OLTP store** — Postgres in deploy, SQLite in dev, via `OLTP_DB_URL`; async
  SQLAlchemy + Alembic. System of record: the migrated history plus live
  grievances.
- **Parquet lake** — `data/interim/`, read via DuckDB/Polars. A read-optimized
  downstream copy produced by `janasunani-materialize`; all analytics, ML, and
  the demo's history browse read this, never OLTP.
- **Pipeline artifact DB** — a per-run SQLite database that is the document
  pipeline's resumable working state; reaches OLTP only through the exporter.

Live flow: raw input → extract → redact → classify → summarize → route → persist
to OLTP → view. `GET /grievance/{id}` reads OLTP; `GET /history` reads the lake,
so a live grievance appears in history only after the next re-materialization.

Hard invariant: **citizen text never leaves the box.** PII detection and
redaction are fully in-process (Presidio + local spaCy); no external redaction,
LLM, or embedding APIs. Part III (Phase 13) notes where this is still enforced by
policy rather than by the network.

## 4. Foundation & demo (Phases 0–12)

What each phase is. Status is in §2.

### Data & storage (0–4)

- **0 Scaffolding** — the `janasunani/` package, `uv`, `config.py`.
- **1 OLTP layer** — `db/models.py` (`Complaint` = the 56 dump columns plus
  tracking/ingestion columns; `ActionHistory`; tracking tables), `db/session.py`,
  `db/crud.py`, and `ingestion/schemas.py`, the single raw→field map.
- **2 Cold-start migration** — `from_sql_dump` restore + `from_mysql` streaming
  load; ran the full 3.2 GB `Dump20250730.sql` to 1.37M / 6.56M rows,
  deterministic and idempotent. `action_history` dedup uses a NULL-coalescing
  functional unique index, which is why the count settled at 6,556,171.
- **2b OLTP swappable** — `OLTP_DB_URL`, asyncpg, an Alembic baseline
  (upgrade/downgrade verified on SQLite and Postgres), dialect-portable
  conflict-inserts.
- **3 Materialization** — `olap/materialize.py` (DuckDB sqlite/postgres scanner →
  Parquet) plus `olap/lake.py` read helpers; the one DVC-tracked transform (~26 s
  at full scale). `dvc repro` works only on the SQLite path; against Postgres, run
  `janasunani-materialize` then `dvc commit` the outputs.
- **4 Document ingestion → S3** — `s3service`, the ingestion `client`
  (`with_retry`), and `DocumentService` (download → S3/local, status back to
  OLTP).

### Document pipeline (5)

Six stages in a fixed order:
`format_classifier → ocr_extraction → pii_tagger → page_type_classifier →
summarizer → categorizer`. Each imports its heavy dependencies lazily to work
around a hard conflict (`ocr-deepseek` pins `transformers==4.46.3`, everything
else needs `≥4.57`); the uv extras `pipeline-core` / `ocr-deepseek` /
`categorizer` are mutually exclusive, one Docker image per group. The pipeline
keeps its own resumable SQLite artifact DB and reaches OLTP through the exporter.

- PII was rebuilt on **Presidio** after the DSI CRF weights were lost: in-process,
  with custom Indian recognizers (mobile / Aadhaar / PAN), spaCy NER for names,
  and typed tokens. No citizen text leaves the box.
- Page-type is the signal/noise gate: the summarizer only consumes target page
  types (letters/forms in, IDs/covers out).
- OCR uses pytesseract with the `ori` data for Odia. DeepSeek OCR is English-only
  in practice (Odia comes out script-confused) and GPU-only; a repetition-collapse
  guard (repeated-trigram share > 0.5) catches its failure mode.

### Automation & demo (6–12)

- **6 Model tracking** — DVC is the tracker through the demo. The MLflow slim
  registry (branch `feat/mlflow-slim-registry`) is deferred until there are
  retrain candidates to compare and promote; that trigger is Part III (Phase 14).
  Eval metrics land in a DVC-tracked `eval_results.jsonl`.
- **7 CI** — GitHub Actions runs ruff + pytest against a Postgres service
  container plus `dvc status` and the raw-data-in-git guard; it installs no heavy
  extras, so anything a test imports must live in an import-light module.
- **8 Inference core** — `PipelineGrievanceProcessor` warms the models once
  (page-type, MuRIL, BART, Presidio); typed text skips OCR, documents run
  pytesseract with page-type gating. `janasunani-api-live` loads local DVC
  artifacts and fails closed. Non-English text is currently downgraded to
  `Uncategorized`; Phase 16 fixes that.
- **9 Routing** — the deterministic `RuleRouter` / `MappingRouter` are built,
  producing the frozen `RoutingResult`. The master tables carry no
  category→department link (`intCategoryGrp` is NULL on all 62 categories), so the
  real crosswalk has to be learned from history:
  `(category, subcategory, district) → argmax(dept, office)`, measured at 60.9 /
  67.5 / 72.8%. That crosswalk and the learned scorer are deferred past the demo,
  which ships on `fallback`.
- **10 Serving** — three endpoints plus `/health` and CORS behind the frozen
  `serving/schemas.py` contract. The default app is mocked (`janasunani-api`);
  `janasunani-api-live` mounts the real processor. Live submissions persist to a
  sibling `live_grievances` OLTP table.
- **11 Frontend** — Next.js 16 + Tailwind, DPIC-branded (maroon, Calibri), with
  two routes: submit (text/upload → staged result cards showing extracted and
  redacted text with typed PII tags, classification, summary, routing with the
  escalation chain and a confidence figure) and history browse/search. Types
  mirror `serving/schemas.py`. First cut built; live-API wiring in progress.
- **12 Demo integration & deploy** — the full deploy pipeline is **built and
  heavily reviewed** (Codex rounds 2–5, PRs #27/#29, plus a Fable pass) on branch
  `deploy/cpu-box`, not yet merged to `main`. A `workflow_dispatch` job
  (`.github/workflows/deploy.yml`) builds both images to GHCR and deploys over SSH
  using a temporary, OIDC-scoped CI IAM role (`deploy/terraform/ci.tf`) that opens
  port 22 only for the run; `deploy/deploy.sh` is the sole sanctioned box-side
  path, health-gating on `/health` and auto-rolling-back to the prior
  digest-pinned images on failure; compose runs `oltp` + `api` + `frontend` +
  `proxy` (Caddy with site-wide `basic_auth`, so production data isn't openly
  public); `tests/test_deploy_stack.py` (~1k lines) covers it. Local live bring-up
  is validated (`docs/DEMO.md`). What remains: `terraform apply` of `ci.tf`, the
  one-time box setup (GHCR login, `deploy/.env`), the first real amd64 build + a live
  `workflow_dispatch` run, and an on-box browser E2E (`docs/DEPLOY.md §4`). Three
  hardening items flagged by the deploy/cpu-box addendum before Phase 12 is "done":
  build a **CPU-only Torch** API image (the current 8–12 GB image bundles CUDA Torch
  though the box is CPU-only and DeepSeek is excluded), and close two rollout gaps —
  the workflow can time out and kill the SSH session before the box script finishes
  its health-wait/rollback, and a rollback run can ship the current compose/proxy
  beside an old image SHA (ship artifacts from the requested image's commit).

### Model provenance & baselines (hard rule)

Runtime loads models **only** from our DVC mirrors under `models/` or from large
public repos (`facebook/bart-large-cnn`, `deepseek-ai/DeepSeek-OCR`), never from
DSI-controlled accounts (the DSI team disbanded 2026-07-03 and their Box is gone).
The mirrored artifacts are the page-type ViT, the MuRIL categorizer and its label
encoder, and the format-classifier pickle. The PII CRF weights were the one
unrecoverable artifact and were rebuilt on Presidio; the training loop survives at
DSI-repo commit `db4885f`.

The DSI clinic technical report ([`docs/Full Technical Report DPIC.pdf`](Full%20Technical%20Report%20DPIC.pdf))
is the only surviving eval record — the **DSI clinic reference baselines**. These
are the prior team's numbers on their own splits, not re-measured on our pipeline,
and (crucially) **English-centric**: the OCR benchmark is English-only by
construction, and the PII / summarizer gold is English. Phase 13 re-establishes
each on *our* data, **per language**, and turns these into before-numbers rather
than thresholds.

| Stage | Model as evaluated | Sample / split | Metric → result |
|---|---|---|---|
| Format classifier | XGBoost + OpenCV + SMOTE | 1,000 hand-labelled pages | avg acc across classifiers **75.71%**; best-model acc 81.97% / macro-precision 72.93% |
| OCR (text extraction) | DeepSeek-OCR, **English only** | 96,469 English pages, heuristic quality gates (no transcription ground truth) | pass word-count≥20 85.52% / alpha≥0.5 84.04% / trigram≤0.25 91.14% / **all three 77.89%** |
| PII tagger | RoBERTa BIO | 106-sentence / 2,126-token val split | B-PII F1 0.929 · I-PII F1 0.730 · O F1 0.995; **coverage 80.56% any-overlap / 50.00% exact-span** |
| Page-type classifier | ViT (beat ViT+BERT / ViT+Longformer) | 1,500 pages, 70/30 | accuracy **0.67** / macro-F1 0.62; per-class F1 Letter 0.79 … Misc 0.44 |
| Categorizer | MuRIL, fine-tuned on 6,598 | 65,999-grievance test | accuracy **0.7104** / macro-F1 0.6853 / weighted-F1 0.6947; per-class F1 Police-Case 0.85 … Social-Welfare 0.51 |
| Summarizer | BART | 500-page qualitative, 0–3 usefulness | ROUGE deemed uninformative; usefulness Text-Only 1.9 · Letter 1.3 · Forms 0.85 · Identification 0.45 · Bills 0.40 |

Efficiency (clinic, per 500 tokens): format 4.53s · OCR 18.86s · PII 4.67s ·
page-type 2.49s · summarizer 1.84s · categorizer 2.82s (NVIDIA A100). The
wide **per-class** spread on categorization and page-type is why the Phase 13
scorecard reports per-category / per-entity, not just aggregates.

## 5. Part III — post-demo maturity (Phases 13–19)

After the demo, the system matures toward two goals: Odia (native **and**
romanized) as a genuine first-class citizen, and the 1.37M / 6.56M corpus turned
into governance intelligence — while becoming portable enough to interest other
governments (the DPI-export goal). It is sequenced **evaluation-first,
workflow-first, platform-light**: the biggest risk is not whether DuckDB scales or
another model can be registered, it is whether we can prove multilingual outputs
are safe and useful, operate safely around real citizen data, and turn officer
feedback into trustworthy labels. Part III is a set of **gated investments, not an
implementation commitment** (see the go/no-go gates below). It extends, and does
not replace, the tabled Phase 5/6/9 goals.

### Decisions (2026-07-22)

- **Local Indic LLM — phased hybrid.** Task-specific Indic models now; a local
  Indic LLM only later, as an on-demand GPU batch job (cluster narration, hardest
  cases), never a serving-path dependency and never always-on.
- **Multilingual — Indic-native, not cloud-translate.** Respects the no-egress
  boundary. IndicTrans2 is kept only as an optional, deferred fallback.
- **Modularity — minimal.** The pipeline runs a **fixed canonical stage order
  shared by batch and live**; only **models** are swappable, via a registry. There
  is no configurable/reorderable stage graph — some orderings are policy
  invariants (PII-safe text must feed anything summarized or presented;
  original-text offsets stay authoritative), not free choices.
- **MLflow — control-plane, not a runtime dependency.** Resolve an approved alias
  at deploy/startup, pin the artifact in a release manifest, cache locally, expose
  it in health/telemetry, keep one-command rollback. No unreviewed automatic
  production switching.
- **Intelligence — spike detection first.** Cheap, explainable, immediately
  useful; embeddings and themes only after normalization and a capacity benchmark.
- **Feedback — governed capture, not "online learning".** Officer edits are
  signal, not automatic ground truth; adaptation runs in shadow mode first.

### Execution order

1. **Finish and harden the demo** — auth/RBAC on real-data endpoints, egress
   enforcement, a tested restore, baseline observability — before any *additional*
   real-data exposure or new Part III endpoint (the system already stores and
   processes real citizen data).
2. **Phase 13** — evaluation, gold sets, operational safety.
3. **Phase 14** — model & pipeline platform (minimal).
4. **Phase 15** — spike/anomaly detection (first government-visible value). It uses
   only structured lake fields, so it can run **alongside Phase 14** once Phase 13
   lands — don't serialize it behind the model platform.
5. **Phase 16** — Odia-first models, gated on Phase 13.
6. **Phase 17** — semantic index, case retrieval, themes (needs Phase 16
   normalization; capacity-gated; local-LLM narration last).
7. **Phase 18** — governed feedback loop (shadow-first; needs the Phase 17 index).
8. **Phase 19** — jurisdiction pack, running alongside as the export throughline.

### Phase 13 — Evaluation, gold sets & operational-safety foundation

The first post-demo investment, because the top risk is being unable to tell a
better multilingual model from a more confident unsafe one.

- **Per-task, per-language eval harness + scorecard**, built on
  `pipeline/pii_eval.py`. One `eval_results.jsonl` row per
  `(task, model_name, model_version, gold_version, language)`. The scorecard
  covers: PII precision/recall and false-negative rate by entity, language,
  document type, and source; OCR quality **and** downstream task success;
  categorization and **routing** top-k, calibration, and abstention/override rate;
  summary faithfulness, omission, harmful disclosure, and officer acceptance;
  latency and cost by path/model/language.
- **Status-quo baseline.** Run the current English-centric models against the
  Odia / romanized-Odia / English gold slices to get the before-numbers,
  reproducing the **DSI clinic reference baselines** (§4 table, from
  [`docs/Full Technical Report DPIC.pdf`](Full%20Technical%20Report%20DPIC.pdf))
  as the English comparison point per task: format-classifier accuracy (75.71%
  avg / 81.97% best), DeepSeek OCR quality-gate pass-rate (77.89% all-three, on
  the English-only slice the clinic could measure), PII coverage (80.56%
  any-overlap / 50.00% exact, plus B/I/O F1), page-type ViT (0.67 acc / 0.62
  macro-F1), MuRIL categorization (0.7104 acc / 0.6853 macro-F1, **per-category**
  given the 0.85→0.51 F1 spread), and summarizer usefulness by page type. These
  are a reference and an English anchor, **not** a release threshold — the point
  is the Odia / romanized-Odia deltas, which the clinic never measured.
- **Gold-set governance.** Immutable versions, annotation guidance, adjudication,
  controlled access. Routing gets its own held-out gold, not only PII /
  categorization / summarization.
- **Operational safety** (before any real-data endpoint): identity/RBAC on the
  real history / correction / intelligence endpoints; tighten the permissive demo
  CORS; **enforce network egress** (today the no-egress rule is policy, not a
  technical boundary — the boxes allow general outbound and some models are public
  runtime downloads); mirrored, checksummed runtime artifacts; a **tested restore**
  of the Postgres backup; and stage-level metrics/traces (latency, failure,
  abstention, language mix, redaction counts, model/recipe version).

  *Partly down-paid by the Phase 12 deploy work (branch `deploy/cpu-box`), which
  aligns with this foundation and with Phase 14's release-manifest/rollback intent,
  at the stack/image level:* site-wide Caddy `basic_auth` (the demo isn't openly
  public), health-gated deploys with **auto-rollback** to digest-pinned images,
  atomic image tagging, and a temporary OIDC-scoped CI IAM role that opens port 22
  only for the run. No conflict with the Codex advice — same direction, partial
  installment. *Not yet covered, still owned here:* per-user **RBAC** (basic_auth
  is coarse, not identity — Phase 18/17 need SSO/OIDC identity, roles, and
  jurisdiction scope), **egress enforcement**, a **tested restore** drill,
  **audit logging** and authz/redaction on real-data read endpoints, and
  model-*level* release/rollback (Phase 14). Three specifics from the addendum:
  (a) **recovery is the largest infra risk** — the prod DB, lake, model cache,
  images, and backups share one host/root-volume failure domain, and the backup
  cron is box-only; codify the timer + retention + encryption + a restore test from
  code (the single-instance Postgres is a known prototype limit). (b) The no-egress
  rule is undercut by the box now holding a read-scoped **GHCR PAT** (which also
  makes ARCHITECTURE's "boxes hold no GitHub credential" line inaccurate) and by
  BART downloading from a public hub at startup — mirror/checksum every runtime
  model and rotate/replace the PAT. (c) Add a **non-PII synthetic readiness canary**
  that proves real history is queryable, the DB result store is selected, routing
  mappings loaded (not silently `fallback`), and submit→persist→fetch succeeds —
  and reports the release manifest.

### Phase 14 — Model & pipeline platform (minimal)

Make model swaps and retrains safe and reproducible without over-building.

- **One validated processing recipe shared by batch and live.** Converge
  `pipeline/pipeline.py` and `inference/service.py::process` on a single recipe with
  declared inputs/outputs and startup validation, so the two paths cannot drift. It
  is a **fixed typed dataflow with explicit branches, not a literal linear chain**:
  typed grievances skip format-classification / OCR / page-type; document
  submissions include the page-level path and gating. The order is fixed (not
  configurable), and the validator enforces the **mandatory safety edges** — PII
  redaction before summarization/presentation, and original-text offsets kept
  authoritative (text-based `language_id` / `transliteration`, added in Phase 16,
  run after OCR and write a *derived* field, never the redaction span-of-record).
- **Establish the jurisdiction config contract now** (not just in Phase 19). Define
  the minimal seam — taxonomy, routing mappings, languages, retention, RBAC, eval
  thresholds — as configuration during this phase, so Phases 15–18 don't bake in
  Odisha-specific assumptions that are expensive to extract later. The second,
  synthetic-jurisdiction *validation* stays in Phase 19.
- **API evolution policy.** "New schemas, frozen contract untouched" won't hold as
  correction and intelligence endpoints accumulate. Pin the current response
  contract as **v1** and specify: additive compatibility, versioned routes,
  idempotency, structured errors, upload/request-size limits, and async job/status
  semantics for long OCR/GPU operations.
- **Model registry.** Resolve every model by name/alias from a DVC path **or** an
  MLflow alias, not a hardcoded constant (`summarizer.py`, `categorizer/stage.py`,
  `pii_tagger.py`, `page_type_classifier.py`, `deepseek_backend.py`);
  `config.py` / `PipelineConfig` carry the references.
- **MLflow as control-plane.** Adopt the slim registry: register versions, resolve
  an **alias** (`@champion` / `@production`, not the deprecated stages) at
  deploy/startup, pin it in an immutable release manifest with one-command
  rollback, and record recipe + model + data versions on every output. Add the
  `mlflow` service to compose. This is the Phase 6 trigger, fulfilled.
- **The release manifest is the highest-value piece here** (Codex, deploy/cpu-box
  addendum). The Phase 12 deploy already ships immutable commit-SHA *images*, but an
  image SHA does **not** pin the independently bind-mounted artifacts that also
  determine a result: the DVC model hashes, the routing mappings, the Parquet
  snapshot, the Alembic schema revision, and the public Hugging Face model
  revisions. The manifest must join **all** of these so an inference release is
  reproducible and roll-back-able as a unit — more valuable than any generic
  reorderable-pipeline framework.

### Phase 15 — Governance intelligence I: spike & anomaly detection

The first government-visible outcome and the cheapest. Per
`(category × district × week)` counts over the lake, flagged by EWMA / STL
residual / Poisson surprise ("water complaints in District X up 300% this week").
No ML and no embeddings, only structured fields already in the lake. Served
read-only through a new `serving/intelligence.py` router (new schemas; the frozen
contract is untouched) behind auth, with a supervisor screen in the frontend. It
is descriptive: it reports what, where, and how fast, and does **not** rank
offices on performance (that comparison carries the same omitted-variable bias as
routing).

### Phase 16 — Odia-first models (gated on Phase 13)

Today non-English is downgraded to `Uncategorized` / `fallback`. Make language a
property that follows the grievance. Ships only when it clears the Phase 13 gates.

- **Language ID, split by where text exists.** Pre-OCR, the image-based
  format-classifier signal picks the OCR model (`eng` / `ori` / `eng+ori`).
  Post-OCR, and immediately on the typed-text path, a text-based **IndicLID** stage
  *refines* `pages.language`. IndicLID refines the value; it does not replace the
  pre-OCR image signal (which scanned documents need before any text exists).
- **Romanized normalization.** An **IndicXlit** step transliterates romanized Odia
  into Odia script, on text (after OCR, or on typed text). It is **not
  length-preserving**, so it writes a **separate derived field** used only for
  language-ID, classification, and embedding — never the string that redaction
  offsets are computed against. PII `start`/`end` stay defined over the original
  text; redacting on transliterated text would misalign spans, which is a privacy
  hazard, not a cosmetic one.
- **Multilingual models** (via the Phase 14 registry): summarizer BART →
  IndicBART / mT5-Indic; an IndicNER PERSON recognizer added to Presidio (which
  also lifts Indian-name recall on English pages); MuRIL retrained on our corpus
  including native and romanized Odia (the tabled Phase 5 retrain).
- **Relax the English-only gates** — the crux, without which the swaps do nothing:
  `_is_english` in `categorizer/stage.py`, the English branch in `service.py`, and
  `WHERE language LIKE '%English%'` in `pii_tagger.py`. Route by detected language
  instead.
- **Land the empirical crosswalk** (Phase 9's tabled first step) as the improved
  routing, gated by the routing gold set.

### Phase 17 — Governance intelligence II: semantic index, retrieval & themes

Needs Phase 16's normalized text (embedding raw romanized/out-of-distribution text
is what sank the earlier BERTopic attempt) and a capacity benchmark before any
index is committed.

- **On-box embeddings.** `olap/embed.py` writes `data/interim/embeddings.parquet`
  with schema `ticket_no, vector, model_version, source, kind, correction_id`, so a
  raw-grievance vector is distinguishable from a correction-derived one (edited
  summary / label / route). The embedder is chosen from **benchmark candidates**
  (e.g. BGE-M3, e5-mistral, gte-Qwen2) by multilingual task quality, licensing,
  artifact size, build cost, and measured CPU/GPU performance — not a predetermined
  floor/ceiling.
- **Capacity gate before VSS/HNSW.** The dense index is the first genuinely new
  scaling regime on the downsized CPU box. Benchmark artifact size, build time,
  filtered-query p95, memory, restart/rebuild, and incremental updates before
  choosing. Brute-force `array_distance` may be fine for offline analysis;
  interactive retrieval is not assumed acceptable without the benchmark. HNSW needs
  a persisted DuckDB table with fixed-size `FLOAT[n]` vectors, not a Parquet view.
- **Case-based retrieval** (similar past grievances with their actual resolutions
  and disposal times) and **emergent-theme** clustering (clusters that do not fit
  the 62 categories are new issues). **Local-LLM narration comes last**: a
  deferred, on-demand GPU batch job that labels clusters and writes a plain brief,
  never a serving dependency.

### Phase 18 — Governed feedback loop

Officer corrections to classification, summary, and route are valuable but **not
automatic ground truth** (they can encode local practice, workload pressure,
policy disagreement, or error). Three stages:

- **Capture.** Authenticated, append-only corrections (actor, reason code,
  before/after values, timestamp, model/release version, audit history) through a
  new `POST /grievance/{id}/correction` endpoint (new schemas; the frozen contract
  is untouched) and the officer **correction UI** on the result screen — the edit
  controls that turn Phase 11's read-only screen into where an officer works.
  Corrections are materialized to the lake; training reads the lake, never OLTP
  directly.
- **Curate.** Quality checks, deduplication, adjudication/approval,
  taxonomy-version compatibility, and explicit inclusion in a versioned training
  set.
- **Learn.** Retrieval-based adaptation in **shadow/suggestion mode first** (which
  needs the Phase 17 index); periodic batch retraining only after a minimum
  clean-label volume clears a held-out eval; promotion via controlled rollout and
  rollback. Routing stays **decision-support, not autonomous** — the
  disposal-time/benefit objective's omitted-variable bias (harder cases run longer
  regardless of office, and office assignment reflects the old policy) is a release
  constraint, not a footnote.

### Phase 19 — Jurisdiction pack (DPI portability)

The abstraction that turns "an Odisha deployment with reusable code" into
something another government can adopt. Taxonomy, routing mappings, languages,
model release, eval thresholds, retention rules, and RBAC policy all become
**configuration and data**, with a portability test against a second, synthetic
jurisdiction. The minimal config *contract* is established early (Phase 14) so
Phases 15–18 don't embed Odisha assumptions; this phase is the second-jurisdiction
**validation**. It runs alongside the other phases as the export throughline.

### Go / no-go gates

Part III is a direction to fund, not an implementation commitment. Do not present
it as committed until four gates exist:

1. a reproducible, authenticated, observable demo baseline with a tested restore;
2. per-language and per-task release thresholds with versioned gold sets;
3. a normalized, lake-backed data path for embeddings and corrections;
4. governed correction curation with shadow evaluation before any adaptive
   behavior.

Explicitly deferred for the next milestone: local-LLM narration, autonomous fast
adaptation from individual corrections, learned routing optimized on disposal
time, any fully-generic reorderable pipeline, and HNSW unless a measured
interactive-retrieval need appears.

## 6. Cross-cutting

### Infrastructure (two boxes)

Self-host on Docker; S3 is the only stateful AWS dependency; Terraform
(`deploy/terraform/`), IAM instance roles only, region ap-south-1.

- **CPU box** (always on, t3.large, Elastic IP 52.66.116.80): Postgres OLTP, api,
  frontend, and proxy (Caddy) in compose, plus the migration/materialization
  one-offs and a nightly `pg_dump` → S3. Never `docker compose down -v` (the OLTP
  volume holds production data).
- **GPU box** (on demand, g6.xlarge / L4, `gpu_box_count` toggle, ~$1/hr while up):
  DeepSeek OCR, and in Part III the embedding/LLM batch jobs. Created and destroyed
  per use; nothing stateful survives.
- DVC remote `s3://dpic-dvc-cache/janasunani` (dump + lake + models); documents in
  `s3://janasunani-documents-main`; backups in `grievance-database-backups-main`.
  Don't DVC-track the OLTP DB.

### Testing policy (every phase)

Real-code-path pytest, green before "done":
`uv run --extra pipeline-core pytest && uv run ruff check .`. OLTP tests run on
**both** SQLite and Postgres. Never run pytest against the production container
(fixtures drop tables), and never read or recurse into `data/` (real citizen PII).

### Data & schema

The authoritative schema is the dump `data/raw/Dump20250730.sql` (a `mysqldump` of
`sociomatics_ticket`: complaints, 56 columns, plus action history), mapped to clean
snake_case by the single source→field map in `ingestion/schemas.py`. Out of scope:
the ORTPS analysis pipeline, a different application not used here.

## 7. Decisions log

Terse and dated; the reasoning for choices that aren't obvious from the code.

- **2026-07-02** — Two-box split (CPU always-on, GPU on-demand); compose + S3 +
  Postgres-container, no RDS. The biggest full-scale risk is the 6.5M-row asyncpg
  migration, so run `migrate.sh` on the box, never across the internet.
- **2026-07-03** — DSI team disbanded and their Box was lost; the PII stage was
  rebuilt on Presidio. Live demo submissions use a sibling `live_grievances` table,
  keeping the historical `complaints` schema faithful to the dump.
- **2026-07-08** — Learn category→department from history, not the master tables
  (`intCategoryGrp` is NULL on all 62 categories, so name-matching covers almost
  nothing).
- **2026-07-10** — The demo ships on `fallback` routing; the empirical crosswalk
  and the learned scorer are deferred past the demo so it never blocks on routing
  modeling.
- **2026-07-22** — Part III restructured evaluation-first (this document). Minimal
  modularity (fixed stage order, swappable models only). MLflow as a control-plane
  with a release manifest, not a runtime dependency. Feedback governed and
  shadow-first, not autonomous online learning. Jurisdiction pack as the
  DPI-export throughline.

History freshness is by design: live grievances hit OLTP immediately but appear in
`GET /history` (which reads the lake) only after the next re-materialization.
