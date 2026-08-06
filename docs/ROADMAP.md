# Janasunani 2.0 — Roadmap

> Source of truth for **sequencing and status**. Architecture detail lives in
> [ARCHITECTURE.md](ARCHITECTURE.md), operations in [DEPLOY.md](DEPLOY.md),
> per-package detail in the package READMEs.
>
> **For the dated 14 August delivery plan, owners, and fallbacks, read
> [DELIVERY.md](DELIVERY.md) first.** This document is the engineering source of
> truth; DELIVERY.md is the management view and is where the demo scope is
> actually committed.
>
> Phase status lives in exactly one place: the table in §2.
>

## 1. What we're building

An AI grievance-redressal system for Odisha. A raw grievance, typed text or a
scanned document, is **extracted** (OCR), **redacted** (PII), **triaged** (spam /
duplicate), **classified** (category / subcategory / department), **summarized**,
and **routed** to the responsible office, ending in a Next.js demo.

Three parts:

- **Part I, Foundation** *(built)*. Two earlier repos consolidated into one
  `janasunani/` package, and the data loaded: cold-start migration into a
  swappable OLTP store, the Parquet lake, document ingestion to S3, the document
  pipeline.
- **Part II, The demo** *(in progress)*. Real-time inference, routing, FastAPI
  serving, the Next.js demo, plus the five components in §1.1. Built
  API-contract-first so the visible deliverable never sits at the tail of a serial
  chain.
- **Part III, Post-demo maturity** *(planned)*. Odia as a first-class citizen,
  the corpus turned into governance intelligence, the system made portable enough
  to interest other governments. Sequenced evaluation-first (§6).

### 1.1 The five demo components

Set by the Executive Director, 2026-07-27. The demo is defined by these, not by
the phase numbering.

| # | Component | Phase | Demo deliverable | Maturity |
|---|---|---|---|---|
| a | DSI pipeline replication | 13 | End-to-end run, document in to routed grievance out, with a per-entity PII scorecard | Ships working |
| b | Spam & duplicate detection | 14 | Triage stage and UI treatment: spam flag, duplicate link, campaign cluster. Plus corpus prevalence | Ships working |
| c | Intelligence layer | 15 | Metrics layer, supervisor dashboards, deterministic spike detection, and three headline metrics no SQL dashboard can produce | Ships working |
| d | A/B testing of AI automation | 16 | Assignment / exposure / shadow instrumentation, a locked analysis plan, a power calculation, retrospective evidence | Ships as framework + evidence |
| e | Sarvam benchmark | 17 | Head-to-head scorecard against our local models on Odia, romanized Odia, English. Provider-switchable via the registry | Ships as benchmark + switch |

Three ship as working surfaces (a, b, c). Two ship as a framework plus measured
evidence (d, e). Both of those make claims that need data we do not have yet, and
neither is honest as a live feature on the demo date.

**The August cut is narrower than the full phase scope below.** The demo is
**14 August 2026**, built by **one accountable engineer with agent augmentation**
(§5.6 states the operating model), so §5 describes each phase in full
while [DELIVERY.md](DELIVERY.md) states what is actually promised for the demo and
what the fallback is if it slips. Where the two differ, DELIVERY.md governs the
demo and this document governs the eventual shape.

Two change the system's shape rather than its feature list:

- **(e) retires the no-egress invariant.** The Government of Odisha has authorized
  Sarvam for this data including PII. "Citizen text never leaves the box" becomes
  a declared, audited, revocable channel. See §3.1.
- **(d) puts a measurement obligation on every automated decision.** Offline
  accuracy on a gold set is not evidence that the tool helps an officer or a
  citizen. §5.4 separates the two claims.

## 2. Status: canonical phase list

The only place phase status is recorded.

| Phase | Part | Scope | Status |
|---|---|---|---|
| 0 | I | Scaffolding & dependencies | ✅ |
| 1 | I | OLTP layer (ORM models, session, CRUD, ingestion schemas) | ✅ |
| 2 | I | Cold-start migration dump → OLTP (1.37M / 6.56M rows) | ✅ |
| 2b | I | OLTP engine-swappable (`OLTP_DB_URL`, asyncpg, Alembic) | ✅ |
| 3 | I | OLTP → Parquet materialization + lake read helpers | ✅ |
| 4 | I | Document ingestion → S3 | ✅ |
| 5 | II | Document pipeline (6 stages, Presidio PII rebuild, GPU shakedown) | ✅ |
| 6 | II | Model tracking (DVC is the tracker; MLflow helpers merged, unused) | 🔄 |
| 7 | II | CI (ruff + pytest on a Postgres service container) | ✅ *(docs pending)* |
| 8 | II | Real-time inference core (warm processor, live CLI) | ✅ |
| 9 | II | Routing (rules built; crosswalk + learned scorer deferred; demo = `fallback`) | 🔄 |
| 10 | II | Serving API (default mock + opt-in live wiring) | ✅ |
| 11 | II | Demo frontend (Next.js, DPIC-branded; first cut) | 🔄 |
| 12 | II | Demo integration & cloud deployment | 🔄 |
| 13 | II | **Pipeline completion**: PII gold set, Presidio tuning, end-to-end run *(a)* | 🔄 *(gold set underway, #15)* |
| 14 | II | **Spam & duplicate detection** *(b)* | ⬜ |
| 15 | II | **Structured analytics I**: metrics layer, dashboards, spikes *(c)* | ⬜ |
| 16 | II | **A/B instrumentation + retrospective impact evidence** *(d)* | ⬜ |
| 17 | II | **Sarvam benchmark + provider registry + egress control** *(e)* | ⬜ |
| 18 | III | Evaluation harness & operational safety (RBAC, restore, observability) | ⬜ |
| 19 | III | Model & pipeline platform (one recipe, release manifest, API v1) | ⬜ |
| 20 | III | Structured analytics II (adjusted comparisons, NL query) | ⬜ |
| 21 | III | Odia-first models | ⬜ |
| 22 | III | Semantic / unstructured intelligence (retrieval, emergent themes) | ⬜ |
| 23 | III | Governed feedback loop | ⬜ |
| 24 | III | Jurisdiction pack (DPI portability) | ⬜ |

Anchor facts:

- Verified corpus, local SQLite **and** cloud Postgres, which must match after any
  migration change: **1,371,288 complaints / 6,556,171 action-history rows**.
- The demo ships with routing on **`method:"fallback"`**. Smarter routing is Part
  III; the demo does not block on it.

### Phase renumbering (2026-07-27)

Phases 0–12 are unchanged: merged PRs reference them. The old 13–19 were entirely
unstarted, so they were renumbered to make room for demo scope and to put the list
in execution order.

| Old | New | Note |
|---|---|---|
| 13 Evaluation, gold sets, operational safety | **18**, minus two slices | PII gold set → 13. Egress enforcement → 17 |
| 14 Model & pipeline platform | **19**, minus one slice | Model registry + MLflow aliases → 17 |
| 15 Intelligence I (S1–S4) | **split** | S1 metrics + S2 spikes → 15. S3 adjusted comparisons + S4 NL query → 20 |
| 16 Odia-first models | **21** | |
| 17 Intelligence II (semantic) | **22** | |
| 18 Governed feedback loop | **23**, minus one slice | Shadow-mode mechanism → 16 |
| 19 Jurisdiction pack | **24** | |

### Tracking issues

| Phase | Issue | Phase | Issue |
|---|---|---|---|
| 13 Pipeline completion | #49 (+ #15 gold set) | 19 Model & pipeline platform | #42 |
| 14 Spam & duplicate | #50 | 20 Structured analytics II | #43 |
| 15 Structured analytics I | #51 | 21 Odia-first models | #44 |
| 16 A/B evaluation | #52 | 22 Semantic intelligence | #45 |
| 17 Sarvam & egress | #53 | 23 Governed feedback loop | #46 |
| 18 Evaluation & op-safety | #41 | 24 Jurisdiction pack | #47 |

Demo-scope issues carry the `demo` label; Part III keeps `deferred` + `part-iii`.
Issues #41–47 were the old Phases 13–19 and were renumbered in place, so their
numbers no longer track their phase numbers.

## 3. Architecture in brief

Three storage layers, deliberately distinct (detail in
[ARCHITECTURE.md](ARCHITECTURE.md)):

- **OLTP store.** Postgres in deploy, SQLite in dev, via `OLTP_DB_URL`. Async
  SQLAlchemy + Alembic. System of record: migrated history plus live grievances.
- **Parquet lake.** `data/interim/`, read via DuckDB/Polars. Read-optimized
  downstream copy produced by `janasunani-materialize`. All analytics, ML, and the
  demo's history browse read this, never OLTP.
- **Pipeline artifact DB.** A per-run SQLite database, the document pipeline's
  resumable working state. Reaches OLTP only through the exporter.
- **Dedup index** *(Phase 14)*. Corpus-level MinHash/LSH state built from the
  lake. The first thing a pipeline run needs beyond its own artifact DB.

Live flow: raw input → extract → redact → triage → classify → summarize → route →
persist to OLTP → view.

`GET /grievance/{id}` reads OLTP. `GET /history` reads the lake, so a live
grievance appears in history only after the next re-materialization. That
freshness gap is by design.

### 3.1 The trust boundary

The old invariant was **citizen text never leaves the box**. Two things broke it.

- The Government of Odisha authorized Sarvam for this data, including PII.
- More basically, **"the box" was never the real boundary.** Scanned documents
  already live in S3. The GPU box is a second machine. A self-hosted Sarvam model
  on the GPU box is not "on-box" in any literal sense, and calling it that hides a
  real network hop behind a reassuring word.

So the boundary is defined by **who controls the destination**, not by which
process the code runs in. Three tiers:

| Tier | Meaning | Examples |
|---|---|---|
| `same-host` | Same machine as the calling process | Presidio, spaCy, MuRIL, BART, pytesseract |
| `dpic-infra` | A different machine, still under DPIC control | S3 documents, Postgres on the CPU box, GPU-box DeepSeek OCR, self-hosted Sarvam weights |
| `authorized-external` | A third party, under a specific written authorization | Sarvam hosted API |

New invariant: **every route that carries citizen data declares its tier, and no
route to `authorized-external` exists without a recorded authorization.**

Each declared route records: data class, destination, approval reference,
retention terms, encryption in transit and at rest, audit policy, and fallback.
Tier alone is not the control; it is the index into those fields.

- Exactly one module may make an `authorized-external` call. Any other path doing
  so is a bug, and CI should be able to catch it.
- Every such call is logged: ticket, stage, provider, model ID, bytes, timestamp,
  and the authorization record it relies on.
- A kill switch reverts every `authorized-external` entry to a maintained
  counterpart at a lower tier.
- Sarvam-30B and 105B are Apache 2.0, so a `dpic-infra` deployment on the GPU box
  is the standing exit ramp. The decision to use the third party rests on
  something better than trust.

This is stricter than the old rule in the place that matters and more honest in
two places it was quietly wrong. `dpic-infra` traffic was never covered by
"never leaves the box" but was happening anyway.

Two honest notes:

- **This tightens enforcement.** Today no-egress is policy, not network: the boxes
  allow general outbound and BART downloads from a public hub at startup. Building
  an allowlist that permits Sarvam and nothing else is the egress enforcement
  Phase 18 wanted, delivered earlier because there is finally a reason to build it.
- **The authorization is settled, and it is not ours to revisit.** The Government
  of Odisha holds an MoU with Sarvam, and Principal / Additional Chief Secretary
  Vishal Dev (IT Department) has signed off on its use for this data. No further
  permission, review, or sign-off is required before we call Sarvam on real
  grievances. Record the reference in-repo so a future reader knows why the
  boundary is drawn where it is; that is documentation, not a gate.

Unchanged: PII detection and redaction run in-process by default, and PII
`start`/`end` offsets stay defined over the original text.

### 3.2 What is actually PII-free, and where

**The lake is not PII-free, and it holds raw citizen prose, not only contact
fields.** `olap/materialize.py` runs `COPY (SELECT * FROM oltp.<table>)`, so every
`complaints` column lands in Parquet, including **`grievance`, which is the
citizen's own account of their problem** (§5.3: median 19 words, p75 458
characters). Phase 15 S4 then reads that field over the full corpus by design.

| Data | Contains PII? | Notes |
|---|---|---|
| `complaints` structured columns, OLTP **and** lake | **Yes** | `petitioner_name`, `petitioner_mobile`, `petitioner_email`, `address`. Faithful to the dump, by design |
| **`complaints.grievance`, OLTP and lake** | **Yes, raw prose** | Citizen-authored, never passed through `pii_tagger`. Materialized verbatim by `SELECT *` |
| `pages.extracted_text` (raw OCR) | **Yes** | Artifact DB **and OLTP** — the exporter copies every column. Held back from the lake by `LAKE_COLUMN_DENYLIST` (`olap/materialize.py`); access-controlled, not redacted |
| `pages.redacted_text` | No, by construction | Typed tokens. This is what reaches the lake |
| Parquet lake, *pipeline-derived* text fields | No | Only redacted page text. Enforced by the denylist above and asserted in `tests/test_e2e_synthetic.py`, not merely intended |
| Dedup index, MinHash signatures | **Yes, derived** | Salted contact hashes, plus signatures over raw `grievance`. A hash is not anonymous |
| Embeddings over `grievance` (S4) | **Yes, derived** | A vector is not anonymous merely because it is unreadable |
| API responses, summaries | No | Built only from redacted text |

**The guarantee, stated precisely.** No un-redacted *page* text (the OCR output of
scanned documents) reaches any downstream output. That guarantee does **not** extend
to `complaints.grievance`, which is raw personal data sitting in the lake, and it
does not extend to artifacts derived from it.

Three consequences bind Phases 14 and 15. The first is a scheduled build step, not a
principle.

- **A `grievance` redaction pass is step 0 of Phase 14, and nothing indexes raw
  prose.** `janasunani-redact-grievance` runs the same Presidio analyzer `pii_tagger`
  uses over the `grievance` column for the chosen backlog slice, and writes
  `grievance_redacted` to its own governed table. MinHash signatures and S4 embeddings
  read that column only. The job is CPU-only over short text (median 19 words), so it
  contends with neither GPU backfill and can start the moment the slice is chosen. The
  live path is unaffected: `pii_tagger` already covers page text there.
- **Redaction lowers exposure; it does not declassify what is derived.** A signature or
  vector built from citizen prose inherits its classification even after contact
  details are stripped, because distinctive phrasing re-identifies where a phone number
  no longer does. So the dedup index, the MinHash signatures and the S4 embeddings are
  **`dpic-infra` artifacts**: DPIC-controlled machines only, never an
  `authorized-external` route, and inside Phase 18's RBAC and audit scope alongside the
  raw lake. Neither Phase 14 nor Phase 15 may call them downstream-safe by
  construction.
- **The raw column stays off the analytics surface.** The semantic layer, the metrics
  and the supervisor screen read `grievance_redacted`. Dropping
  `complaints.grievance` from the analytics-facing Parquet altogether is the cleaner
  end state and belongs to Phase 18; for August the control is that no query in the
  metrics path selects it, plus access control on the lake itself.

## 4. Built: Phases 0–12

What each phase is. Status is in §2.

### Data & storage (0–4)

- **0 Scaffolding.** The `janasunani/` package, `uv`, `config.py`.
- **1 OLTP layer.** `db/models.py` (`Complaint` = the 56 dump columns plus
  tracking/ingestion columns; `ActionHistory`; tracking tables), `db/session.py`,
  `db/crud.py`, and `ingestion/schemas.py`, the single raw→field map.
- **2 Cold-start migration.** `from_sql_dump` restore + `from_mysql` streaming
  load; ran the full 3.2 GB `Dump20250730.sql` to 1.37M / 6.56M rows,
  deterministic and idempotent. `action_history` dedup uses a NULL-coalescing
  functional unique index, which is why the count settled at 6,556,171.
- **2b OLTP swappable.** `OLTP_DB_URL`, asyncpg, an Alembic baseline
  (upgrade/downgrade verified on SQLite and Postgres), dialect-portable
  conflict-inserts.
- **3 Materialization.** `olap/materialize.py` (DuckDB sqlite/postgres scanner →
  Parquet) plus `olap/lake.py` read helpers. The one DVC-tracked transform, ~26 s
  at full scale. `dvc repro` works only on the SQLite path; against Postgres, run
  `janasunani-materialize` then `dvc commit` the outputs.
- **4 Document ingestion → S3.** `s3service`, the ingestion `client`
  (`with_retry`), and `DocumentService` (download → S3/local, status back to OLTP).

### Document pipeline (5)

Six stages in a fixed order:
`format_classifier → ocr_extraction → pii_tagger → page_type_classifier →
summarizer → categorizer`. Phase 14 inserts a seventh, `spam_duplicate`, after
`pii_tagger`.

Each stage imports its heavy dependencies lazily to work around a hard conflict:
`ocr-deepseek` pins `transformers==4.46.3`, everything else needs `>=4.57`. The uv
extras `pipeline-core` / `ocr-deepseek` / `categorizer` are mutually exclusive, one
Docker image per group. The pipeline keeps its own resumable SQLite artifact DB and
reaches OLTP through the exporter.

- PII was rebuilt on **Presidio** after the DSI CRF weights were lost: in-process,
  custom Indian recognizers (mobile / Aadhaar / PAN), spaCy NER for names, typed
  tokens. It has **never been scored against gold data**. Phase 13 fixes that.
- Page-type is the signal/noise gate: the summarizer only consumes target page
  types (letters and forms in, IDs and covers out).
- OCR uses pytesseract with the `ori` data for Odia. DeepSeek OCR is English-only
  in practice (Odia comes out script-confused) and GPU-only. A repetition-collapse
  guard (repeated-trigram share > 0.5) catches its failure mode.

### Automation & demo (6–12)

- **6 Model tracking.** DVC is the tracker. The slim MLflow helpers
  (`janasunani/tracking/mlflow_utils.py`, `configure_tracking`,
  `ensure_experiment`, `log_model_artifact`) **merged to `main` in PR #20 on
  2026-07-08**, with `tests/test_mlflow_utils.py`. Nothing calls them yet: no stage
  resolves a model through MLflow. Phase 17 wires them up, which is a better
  starting position than "deferred on a branch". Eval metrics land in a DVC-tracked
  `eval_results.jsonl`.
- **7 CI.** GitHub Actions runs ruff + pytest against a Postgres service container,
  plus `dvc status` and the raw-data-in-git guard. It installs no heavy extras, so
  anything a test imports must live in an import-light module.
- **8 Inference core.** `PipelineGrievanceProcessor` warms the models once
  (page-type, MuRIL, BART, Presidio). Typed text skips OCR; documents run
  pytesseract with page-type gating. `janasunani-api-live` loads local DVC
  artifacts and fails closed. Non-English text is downgraded to `Uncategorized`;
  Phase 21 fixes that.
- **9 Routing.** The deterministic `RuleRouter` / `MappingRouter` are built,
  producing the frozen `RoutingResult`. The master tables carry no
  category→department link (`intCategoryGrp` is NULL on all 62 categories), so the
  crosswalk has to be learned from history:
  `(category, subcategory, district) → argmax(dept, office)`, measured at
  60.9 / 67.5 / 72.8%. Crosswalk and learned scorer are deferred past the demo
  (issue #33), which ships on `fallback`.
- **10 Serving.** Three endpoints plus `/health` and CORS behind the frozen
  `serving/schemas.py` contract. The default app is mocked (`janasunani-api`);
  `janasunani-api-live` mounts the real processor. Live submissions persist to a
  sibling `live_grievances` OLTP table.
- **11 Frontend.** Next.js 16 + Tailwind, DPIC-branded (maroon, Calibri), two
  routes: submit (text/upload → staged result cards showing extracted and redacted
  text with typed PII tags, classification, summary, routing with escalation chain
  and confidence) and history browse/search. Types mirror `serving/schemas.py`.
  First cut built; live-API wiring in progress.
- **12 Demo integration & deploy.** The deploy pipeline is **built and heavily
  reviewed** (Codex rounds 2–5, PRs #27/#29, plus a Fable pass) on branch
  `deploy/cpu-box` (PR #29 open, 24 commits ahead of `main`). A
  `workflow_dispatch` job builds both images to GHCR and deploys over SSH using a
  temporary OIDC-scoped CI IAM role (`deploy/terraform/ci.tf`) that opens port 22
  only for the run. `deploy/deploy.sh` is the sole sanctioned box-side path,
  health-gating on `/health` and auto-rolling-back to the prior digest-pinned
  images. Compose runs `oltp` + `api` + `frontend` + `proxy` (Caddy with site-wide
  `basic_auth`, so production data is not openly public).
  `tests/test_deploy_stack.py` (~1k lines) covers it. Local live bring-up is
  validated ([DEMO.md](DEMO.md)).

  Remaining: `terraform apply` of `ci.tf`, one-time box setup (GHCR login,
  `deploy/.env`), first real amd64 build + a live `workflow_dispatch` run, on-box
  browser E2E (issue #30).

  ✅ **CPU-only Torch image** (issue #48). The `demo` extra now resolves torch
  from PyTorch's CPU index, dropping ~2.5 GB of wheels: torch's whole
  cuda-toolkit / cuda-bindings / nvidia-\* / triton payload, plus the torch
  wheel itself going 532 MB → 192 MB. The box is CPU-only and DeepSeek is
  excluded from `demo`, so none of it was ever reachable. Scoped to `demo`
  alone — `categorizer` and `ocr-deepseek` keep CUDA wheels for the GPU-box
  batch jobs. The size drop is only observable in the amd64 CI build; on darwin
  `demo` still resolves the ordinary PyPI wheel.

  One CUDA wheel survives, and the torch source cannot remove it: `xgboost`
  declares `nvidia-nccl-cu12` unconditionally on linux, and `demo` pulls
  xgboost through `pipeline-core`. The live processor never loads the format
  classifier, so it is ~300 MB of dead weight in the api image (issue #63).

  Remaining hardening before Phase 12 is done: two rollout gaps (issue #32).
  The workflow can time out and kill the SSH session before the box script
  finishes its health-wait/rollback, and a rollback run can ship the current
  compose/proxy beside an old image SHA.

### Model provenance & DSI baselines (hard rule)

Runtime loads models **only** from our DVC mirrors under `models/` or from large
public repos (`facebook/bart-large-cnn`, `deepseek-ai/DeepSeek-OCR`), never from
DSI-controlled accounts (the DSI team disbanded 2026-07-03 and their Box is gone).
Mirrored: the page-type ViT, the MuRIL categorizer and its label encoder, the
format-classifier pickle. The PII CRF weights were the one unrecoverable artifact
and were rebuilt on Presidio; the training loop survives at DSI-repo commit
`db4885f`.

The DSI clinic technical report
([`Full Technical Report DPIC.pdf`](Full%20Technical%20Report%20DPIC.pdf)) is the
only surviving eval record. These are the prior team's numbers on their own splits,
not re-measured on our pipeline, and **English-centric**: the OCR benchmark is
English-only by construction, the PII and summarizer gold are English. Treat them
as before-numbers, not thresholds.

| Stage | Model as evaluated | Sample / split | Metric → result |
|---|---|---|---|
| Format classifier | XGBoost + OpenCV + SMOTE | 1,000 hand-labelled pages | avg acc across classifiers **75.71%**; best-model acc 81.97% / macro-precision 72.93% |
| OCR | DeepSeek-OCR, **English only** | 96,469 English pages, heuristic quality gates (no transcription ground truth) | word-count≥20 85.52% / alpha≥0.5 84.04% / trigram≤0.25 91.14% / **all three 77.89%** |
| PII tagger | RoBERTa BIO | 106-sentence / 2,126-token val split | B-PII F1 0.929 · I-PII F1 0.730 · O F1 0.995; **coverage 80.56% any-overlap / 50.00% exact-span** |
| Page-type classifier | ViT (beat ViT+BERT / ViT+Longformer) | 1,500 pages, 70/30 | accuracy **0.67** / macro-F1 0.62; per-class F1 Letter 0.79 … Misc 0.44 |
| Categorizer | MuRIL, fine-tuned on 6,598 | 65,999-grievance test | accuracy **0.7104** / macro-F1 0.6853 / weighted-F1 0.6947; per-class F1 Police-Case 0.85 … Social-Welfare 0.51 |
| Summarizer | BART | 500-page qualitative, 0–3 usefulness | ROUGE uninformative; usefulness Text-Only 1.9 · Letter 1.3 · Forms 0.85 · Identification 0.45 · Bills 0.40 |

Efficiency (clinic, per 500 tokens, A100): format 4.53s · OCR 18.86s · PII 4.67s ·
page-type 2.49s · summarizer 1.84s · categorizer 2.82s.

The wide **per-class** spread on categorization and page-type is why every
scorecard in this roadmap reports per-category and per-entity, not just aggregates.

## 5. The demo: Phases 13–17

> **Scope note.** This section describes each phase in full. The **14 August 2026**
> demo ships a narrower cut, committed with dates and fallbacks in
> [DELIVERY.md](DELIVERY.md).
>
> **There is one engineer.** Nothing here runs in parallel with anything else here,
> whatever the dependency graph permits. The real order is: gold-set labelling
> starts first and runs throughout (it gates 13 and 17 and cannot be compressed),
> then Phase 12's live bring-up, then 13 → 14 → 15 → 17 → 16.

### 5.1 Phase 13 — Pipeline completion (component a)

Phases 5 and 8 built the pipeline. Two things stop it being demonstrable.

- Presidio is untuned. It was rebuilt from scratch after the CRF weights were lost
  and has never been scored against gold data.
- The pipeline has never run end to end. Stages are individually exercised;
  document in to routed grievance out is not.

**PII gold set** (issue #15, in progress). Treat it as the most sensitive artifact
in the repo: real citizen PII, annotated and concentrated.

- Immutable versions, written annotation guidance, an adjudication step,
  controlled access. This is the gold-set governance machinery, built once here and
  reused by Phase 18 for the other tasks.
- **Per language from the start.** The DSI gold was English-only. Odia and
  romanized-Odia PII recall is unmeasured, and spaCy NER has no Odia name model, so
  the honest prior is that it is bad. Measuring it is what makes the Sarvam
  benchmark (§5.5) decidable.
- Slice by entity type, language, document type, source.

**Presidio tuning.** Score per entity, not aggregate.

- The release-critical metric is the **false-negative rate**, leaked PII. F1 hides
  it. A recall-favouring threshold with human review beats a balanced one.
- Tune recognizer confidence thresholds, context enhancers, the custom Indian
  recognizers.
- Compare to the DSI reference: 80.56% any-overlap, 50.00% exact-span. Reference,
  not threshold, and English-only.

**End-to-end run.** The three extras are mutually exclusive, so this is not one
pytest.

- `scripts/e2e_pipeline.sh`: a scripted multi-environment run over a fixed sample.
  Document → S3 → artifact DB → exporter → OLTP → materialize → lake → API
  read-back.
- Asserts row counts at each hop, and that no un-redacted *page* text reaches the
  lake. **Not** "no un-redacted PII reaches the lake": §3.2 explains why that is
  false and cannot be an acceptance criterion.
- A **non-PII synthetic variant** that runs in CI. This doubles as the readiness
  canary: it proves real history is queryable, the DB result store is selected,
  routing mappings loaded rather than silently `fallback`, and submit→persist→fetch
  succeeds.

✅ **The synthetic variant is built** (`tests/test_e2e_synthetic.py`, PR #59).
Artifact DB → OLTP → lake → API over invented fixtures, row counts asserted at
each hop, with only the models replaced by a canned stand-in. History and store
selection are asserted through `inference.serve.create_live_app`, so a
deployment that silently keeps `MockHistory` or `InMemoryResultStore` fails the
canary. Two caveats it records honestly: routing is exercised via `MappingRouter`
directly, because the router is wired inside `build_processor`, which needs the
models; and the pipeline's own stages are not run. Both belong to the real-data
variant, which is still outstanding.

Building it found the leak §3.2 now describes: `materialize.py` was copying
`pages.extracted_text` into Parquet verbatim. The lake was re-materialized and
`dvc.lock` updated, because the code fix alone left the pre-fix artifact in
circulation through `dvc pull`.

**Exit criteria.** Per-entity, per-language PII scorecard published. E2E green on
real data and synthetic in CI. DSI baselines reproduced or the gap explained.

### 5.2 Phase 14 — Spam & duplicate detection (component b)

Two different problems. Conflating them is the main design risk.

**Spam / non-actionable.** Content that is not a workable grievance: test entries,
abuse, blank or garbage OCR, out-of-jurisdiction requests.

- **A bare "discarded" flag is not a usable label, but the discard *reasons* are.**
  Training on the disposition repeats the routing OVB trap: officers discard for at
  least six different reasons, and acting on the prediction is self-fulfilling. The
  fix is decomposition, and the reasons are already written into
  `action_taken_remark` at volume:

  | Discard reason (template family) | Rows | Reads as |
  |---|---|---|
  | details inadequate | 39,943 | low-signal, closest to spam |
  | documents not attached | 29,029 | incomplete filing, not spam |
  | case already taken up / taken up earlier | 19,904 | **duplicate** |
  | no specific grievance | 16,340 | low-signal, closest to spam |
  | duplicate copy | 14,767 | **duplicate** |
  | needs a policy decision first | 9,090 | out of scope, valid grievance |
  | not within purview of this grievance cell | 8,455 | **routing failure**, not junk |
  | address not given | 4,110 | incomplete filing |

  Roughly 161,000 reasoned discards in the top 100 templates alone. One noisy binary
  becomes several clean labels, and only two of the eight families resemble spam.
  "Not within purview" is a *routing* failure and must never be scored as junk.
- Those labels are still **not ground truth**. They inherit whatever bias the old
  process had, and discard rates should be checked for variance by office before
  training, or the model learns office identity rather than content. Audit a gold
  sample first.
- ⚠️ **Extracting these reasons is itself a string lookup, not a capability.** Anyone
  with SQL access can write the `CASE WHEN`. Treat the reason breakdown as an
  *insight* (§5.3), useful for training labels and for the demo narrative, but not as
  evidence the pipeline is needed.
- **Free validation set, and the right way to state the value.** The two
  duplicate-reason families give roughly **34,700 officer-confirmed duplicates**.
  These are the **baseline, not the deliverable**: they are exactly the duplicates
  the existing manual process already caught, and they are queryable today. The
  capability claim is the *increment* — duplicates MinHash finds that carry no such
  remark — and the confirmed set is what makes that increment measurable rather than
  asserted. Report recall against it and report the increment separately.
- Start with cheap calibrated features, not a model: text length, the existing OCR
  quality gates (word count, alpha ratio, repeated-trigram share, already a garbage
  detector), language ID, category-confidence entropy, repeat-filer rate.
- **Never auto-reject.** Spam is a flag for officer triage. A false positive is a
  citizen's grievance discarded, a different class of harm from a
  mis-categorization. Abstain by default and log the abstention.

**Duplicates.** Three distinct relations, deliberately separated.

1. **Resubmission.** Same citizen, same issue, filed again. Merge and link.
2. **Campaign.** Same or near-same text from many filers. **Not spam.** It is a
   collective grievance and should surface in Phase 15 as one issue with N
   signatories. Treating campaigns as spam would suppress exactly the signal
   government most needs.
3. **Semantic duplicate.** Same issue, different words. **Not handled here, and
   deliberately not treated as deduplication at all.** It surfaces in Phase 15 as
   thematic clustering, which groups without collapsing counts. See "Why dedup
   stays lexical" below.

**Technique for 1 and 2.**

- ⚠️ **Script normalization is a Phase 17 dependency, and Phase 17 runs after this
  one.** Transliterating romanized Odia to Odia script before hashing is what lets
  one grievance filed twice in two scripts land in one index. The transliteration
  provider is Phase 17 (§5.5), and the execution order is `13 → 14 → 15 → 17 → 16`,
  so that capability does not exist when this phase is built.
  - **August contract: ship script-specific matching.** Odia-script and
    romanized-Odia filings are indexed and matched separately, not against each
    other. Cross-script recall is **explicitly unsupported** and reported as such,
    not quietly assumed.
  - Cross-script matching becomes available once Phase 17 lands, as a re-index rather
    than a redesign. Nothing else in the design changes.
  - Do not silently rely on character n-grams to bridge scripts. They will not.
- MinHash / LSH over **character** n-grams, not word tokens. Character grams
  survive OCR noise and script variation; word tokenization does not work across
  Odia, romanized Odia, and English in one index.
- Blocked by district and time window to keep candidate generation cheap.
- Union-find over candidate pairs to form groups.
- Deterministic, CPU-only, no model, no GPU. It works on Odia script today, which
  almost nothing else in the pipeline does.
- ⚠️ **Strip or down-weight the typed PII placeholders before hashing.** Every
  phone number becomes the same `[PHONE]` token, so two unrelated grievances that
  each contain a name and a phone share those tokens and score as more similar
  than they are. Redaction helps similarity for *matched* text and hurts it here.
  Same-citizen resubmission is detected by the separately salted identity keys,
  not by placeholder overlap.

**Why dedup stays lexical, and clustering stays semantic.**

The two run on the same normalized text and then branch. They are not the same
operation and must not share one similarity function.

| | Dedup (Phase 14) | Thematic clustering (Phase 15) |
|---|---|---|
| Similarity | Lexical, MinHash over char n-grams | Semantic, MuRIL embeddings |
| Question | Is this the same filing? | Is this the same kind of problem? |
| Effect on counts | Collapses them | Adds a grouping column, removes nothing |

- **Deduplication is destructive to the numbers management sees, so it carries a
  higher evidence bar.** Merging on semantic similarity would fold two pothole
  complaints from different villages into one, undercounting real citizen demand
  while reporting it as efficiency. That error would be invisible in the output.
- **The embedding we have is the wrong tool for this by construction.** It is a
  MuRIL encoder fine-tuned to predict `category` (§5.3), so it deliberately
  collapses within-category variation, which is exactly what dedup must preserve.
  Two unrelated water complaints sit close together in that space on purpose.
- Clustering consumes dedup output: one vector per near-identical group, not one
  per filing. Otherwise a 400-signature campaign becomes its own "theme" and the
  finding is circular.

**Three placement decisions that matter.**

- The stage runs **after `pii_tagger`, on redacted text**, so the
  redaction-first safety edge stays intact. Note this is a trade, not a free win:
  see the placeholder caveat above.
- It runs **before `summarizer` and `categorizer`** and gates them, the way
  page-type already gates the summarizer. Obvious spam and exact duplicates should
  not consume the expensive stages.
- **The historical corpus does not enter through this stage at all**, and that is the
  placement decision most easily missed. The 1.37M records already sit in the lake;
  they were never scanned documents and never met `pii_tagger`. Their redaction edge
  is the step-0 pass in §3.2, and the index is built from `grievance_redacted`. Two
  entry points, one index.

New stage order for documents:
`format_classifier → ocr_extraction → pii_tagger → spam_duplicate →
page_type_classifier → summarizer → categorizer`.

Backfill order for history:
`janasunani-redact-grievance → dedup index build → spam_duplicate scoring`.

**Three firsts this stage introduces**, worth flagging:

- The **first stage with a corpus-level dependency**. It needs a dedup index built
  from the lake, not just the per-run artifact DB. That affects resumability and
  the Phase 19 batch/live recipe convergence.
- Dedup keys derived from `petitioner_mobile` / `petitioner_email` must be
  **salted hashes**, held outside the redacted text path. A raw mobile number in a
  dedup index is a PII store by another name.
- Deduplication is document-level. The artifact DB is page-level.

**Outputs:** `spam_score`, `spam_reason`, `duplicate_group_id`, `duplicate_kind`
(`resubmission` | `campaign` | `none`).

**Corpus study.** Run the detector over the chosen slice of the 1.37M history, on
`grievance_redacted`; report prevalence by district, category, mode, year. What share
of officer load is duplicate handling is the number that makes the case. It feeds
Phase 15 and is a candidate outcome for Phase 16.

**UI.** A triage banner on the result screen: "possible duplicate of ticket NNN"
with a link, "part of a campaign, N related filings", "flagged low-signal, review".
All advisory, none blocking.

### 5.3 Phase 15 — Structured analytics I (component c)

A governed analytics surface over the corpus, plus a scoped slice of text-derived
intelligence.

**The value-add test.** Basic dashboards already exist on the portal, and they are
SQL over `complaints` and `action_history`. So every headline metric must pass one
check: **could a competent analyst with SQL access and knowledge of the data build
this in a day?** If yes, it is not our contribution. Pendency by district, disposal
time by department, reopen rate and volume trend all fail on existing columns.

Note what the test has to catch. Exact-match string lookup over a text field is a
`CASE WHEN`, so a metric can depend on a column that does not exist and still be a
view definition rather than a capability.

**Insight versus capability.** An *insight* is something they could have computed and
did not. A *capability* is something they could not compute at all. Both are worth
shipping and they are different products. An insight is cheap, one-time, and hands
over as a SQL view; it does not justify a pipeline. A capability is what a pipeline,
a GPU and an ML workstream are for. Label every deliverable as one or the other, out
loud. If someone asks "couldn't our DBA have found that?", the answer for an insight
is yes, and that is a fine answer given first and a bad one heard as a correction.

Two qualifications. It applies to *headline* metrics, not to supports: structured
measures are still built as denominators and reconciliation checks, and S1 below
deliberately re-implements them with tested definitions and freshness reporting.
And it is a filter, not a target. A metric can pass and still be useless. Novel and
ignored is worse than duplicative and used.

**What the portal structurally cannot do.** Two things, and everything here follows
from them.

1. **It has never read a grievance.** `grievance` carries the citizen's own account
   (median 19 words, p75 458 characters, 61% of rows unique), `document_url` points
   at the scanned attachment, and `action_taken_remark` holds 6.5M free-text records
   of what officers actually did. All three are opaque strings to SQL. The portal
   describes the metadata envelope of 1.37M grievances and nothing about their
   contents. Note that `grievance` is normally full complaint text despite the
   dump's `grievanceSubject` column name, so **the intelligence layer has no OCR
   dependency**.
2. **Every row is an island.** There is no citizen key. `petitioner_mobile` is the
   only near-identifier, nullable and unnormalized. SQL can group on it and get
   naive repeat filers; it cannot do same-issue-different-person (the campaign
   case) or same-person-different-contact. Converting *filings* into *distinct
   problems* needs Phase 14.

**The base layer is three derived tables, not three charts.** Every management view
is a `GROUP BY` over these. The value add is not the chart, it is that the chart has
columns to group by that the portal does not have. This also lets our output feed
their existing dashboard rather than argue for replacing it.

| Derived table | Produced by | New grouping columns |
|---|---|---|
| Per-complaint record | Phases 13, 17 | model category, summary, language, handwritten vs printed |
| Cluster id | Phase 14 | `duplicate_group_id`, `duplicate_kind`, theme id |
| Action type | Phase 15 S3 | what the remark says the officer actually did |

"Does not read text" is not "clean" either. The structured fields still carry
missingness, historic policy choices, and language-related classification error.
Profile and reconcile them before exposing any comparison.

- **S1, metric definitions + freshness + dashboards.** The **semantic layer**: a
  thin governed definition of allowed dimensions and measures over the lake, a
  small YAML compiled to DuckDB SQL (dbt-Semantic-Layer / Cube / Malloy lineage),
  tested metric by metric, with data-quality and lake-freshness reporting, feeding
  fixed supervisor dashboards. This is the contract everything else builds on:
  downstream queries reference only defined fields, and policy constraints (RBAC
  scope, redaction, small-cell suppression) are enforced in the layer, not left to
  model discretion.
  - **Write the definitions to stand alone as a query target, not as backing for
    three fixed dashboards.** Phase 20's natural-language querying parses *into* this
    schema, so every measure needs a name, a description and declared legal
    dimension pairings whether or not a August dashboard uses them. This adds no
    August scope, it constrains how S1 is written. Retrofitting it later is
    expensive; doing it now is nearly free.
- **S2, deterministic spikes + contribution analysis.** Per
  `(category × district × week)` counts, flagged by EWMA / STL residual / Poisson
  surprise ("water complaints in District X up 300% this week"), with key-driver
  analysis decomposing a spike by dimension. No model. The cheapest slice.

- **S3, action type from officer remarks.** Classify `action_taken_remark` into what
  the officer actually did. Pendency and disposal time are computed off
  `resolved_on`; if a meaningful share of "resolved" rows carry remarks amounting to
  "forwarded" or "no action possible", then disposal time measures closure speed,
  not resolution. This splits "resolved" into resolved-with-action and
  closed-without, and re-reports the existing headline metric on an honest
  denominator. It audits the dashboard rather than competing with it.
  - Cheapest item here by a wide margin: no OCR, no document ingest, no GPU.
  - **Field profile.** Populated on 99.87% of the 6,556,171 action rows, with
    1,395,867 distinct normalized values.

    | Measure | Value |
    |---|---|
    | Rows covered by top 1 / 10 / 100 / 500 distinct values | 10.9% / 45.1% / 56.8% / 62.4% |
    | Rows in templates used 1000+ times | 59.7% |
    | Distinct values used exactly once | 1,164,410 (83% of distinct, 17.8% of rows) |
    | Length p50 / p90 / p99 / max (chars) | 46 / 282 / 1,000 / 50,980 |

  - **Templating is the easy case, not the failure case.** Ten distinct strings cover
    45% of 6.5M action records, so hand-labelling them yields action type for nearly
    half the corpus deterministically, with no model and no training set. A dropdown
    is a structured signal wearing a text costume.
  - **Method follows the shape of the field.** A lookup table over the top few
    hundred templates (top 500 buys 62% of rows for roughly a day of labelling, and
    the curve flattens hard after top 10, so there is a natural stopping point),
    plus a classifier for the free-text tail. The tail is where a model earns its
    keep, and at p90 = 282 characters there is real content in it.
  - **Not redundant with `action_status`.** Status has 15
    values; the largest (25% of rows) contains 430,253 distinct remarks and the
    second (20% of rows) contains 839,480. Within the largest status the remarks
    stay spread (top 1 = 7.6%, top 20 = 28.3%). Decisively, **301 of the top 500
    templates appear under more than one status**, one spanning 12 of the 15. The
    two fields are crossing classifications, not a hierarchy, so the remark encodes
    a dimension the status column does not. S3 passes the value-add test.
  - Statuses differ in kind: some are dropdown-driven (status #3, 1.18M rows but
    only 15,390 distinct remarks), others near free text (status #2). Build the
    lookup per status rather than corpus-wide.
  - **Officers pick from a graded disposal ladder** that encodes exactly the
    distinction this metric needs:

    | Template | Rows |
    |---|---|
    | "the grievance has been disposed." | 634,235 |
    | "the grievance has been resolved." | 222,326 |
    | "…disposed with appropriate action." | 335,630 |
    | "…resolved with appropriate action." | 178,819 |
    | "…disposed & beneficiary benefited." | 22,886 |
    | "…resolved & beneficiary benefited." | 20,681 |

    **About 61% of explicit disposals use a rung that claims no action**, while the
    more specific rung was available and chosen 514,449 times. Caveat to carry into
    the reporting: a bare "disposed" does not *prove* inaction, an officer may have
    acted and picked the shorter phrase. Declining a more specific template that sits
    right beside it is evidence, not proof. Check overlap with the existing
    `complaints.benefitted` column before claiming the third rung is novel.
  - Working taxonomy, seven classes, all reachable by lookup: forwarded/delegated,
    reported back (the large ATR / compliance-report vocabulary), disposed-no-claim,
    disposed-with-action, benefit-delivered, discarded-with-reason, and
    reopened/escalated. Plus an administrative-noise bucket (".", "ok", "other", and
    scheme names like "pmay" typed into the remark field, which look like category
    tags in the wrong box and are a data-quality finding in their own right).
  - The tail classifier must be multilingual: at least one high-volume template is
    entirely in Odia script. The lookup table is unaffected, since it matches exactly.

- **S4, thematic clustering in location and time.** The scoped semantic slice; the
  rest of the semantic track stays in Phase 22.
  - **Reuse the existing encoder.** The categorizer is a MuRIL sequence classifier
    fine-tuned on `grievance_and_docs` text to `category`
    (`pipeline/stages/categorizer/model.py`), and its pooled representation is a
    category-aware embedding. **No new model** and no new entry into the
    `transformers` version conflict, which is what makes the slice affordable.
  - ⚠️ **It is not free.** "A forward pass we already make" is true only for
    grievances flowing through the live pipeline. The
    1.37M historical records have never been through the categorizer, so this is a
    **new corpus-scale batch inference job** plus a clustering job, on the GPU box.
    Hours, not minutes, and it needs scheduling like any other backfill.
  - **Bound it for August.** Run over one priority category and time window, with the
    result precomputed as a demo artifact, rather than the full corpus live.
    Full-corpus S4 is a stretch goal. DELIVERY Table 1 marks the component *Bounded*
    for this reason.
  - **Embed `grievance_redacted` only** for the demo, so this carries no OCR
    dependency. It reads the output of the Phase 14 step-0 pass (§3.2), never the raw
    column, and the resulting vectors are `dpic-infra` artifacts regardless.
  - **Cluster within category, never globally.** Compute stays tractable, every
    cluster is nested inside a label officers already use, and it avoids presenting
    "people complain about water" as a discovery. Global clustering over 1.37M
    grievances mostly recovers the categories we already have.
  - **Space × time is the product.** Concentration by block crossed with recency
    gives four cells: diffuse-persistent is background, concentrated-persistent is a
    chronic local failure, diffuse-new is a policy or seasonal shift, and
    **concentrated-new is the alert**. That last cell is the one thing here no
    existing dashboard can produce.
  - **Stable cluster identity is the hard part.** "Emerging" requires a cluster to be
    the same cluster next month, and re-clustering monthly makes ids drift and kills
    the time series. Fit once on history, assign new grievances to nearest centroid,
    and keep a residual bucket past a distance threshold. The residual bucket *is*
    the novelty detector, which is cheaper and more honest than online discovery.
  - **Baseline against the same period last year**, not last month. Monsoon drainage
    and summer water will fire a naive spike alert every year on schedule. The
    multi-year history is an asset most systems doing this do not have.
  - **The field supports this.** `grievance` is populated on 99.98% of rows, 915,639
    distinct values, 61.3% of rows unique, median 19 words and p75 of 458 characters,
    with top-100 distinct values covering only 6.7% of rows. Effectively untemplated,
    unlike the remarks.
    - It is **bimodal**: 27.0% of rows are five words or fewer while 53.8% run to
      fifteen or more. Treat the short tail separately rather than assuming a uniform
      field.
  - **Concentration works at district level, with block on drill-down.**

    | Grouping | Cells | Median | Cells ≥30 | Rows in cells ≥30 |
    |---|---|---|---|---|
    | district × year × category | 4,452 | 29 | 49.6% | 98.3% |
    | block × year × category | 32,776 | 4 | 14.5% | 81.9% |

    District is well powered. Block is sparse per cell but the mass concentrates:
    half the cells are tiny and hold only 18% of rows, so the S1 small-cell
    suppression threshold handles it. Corpus shape: 30 districts, 427 blocks, 35
    categories, 5 years.
    - ⚠️ `block` is null on 17.3% of rows and `category` on 16.9%. Both need a stated
      missingness treatment before they anchor a published metric.
    - ⚠️ Only **35 distinct categories appear in the data** against the 62 in the
      masters. Resolve before clustering within category; it changes what "within
      category" means.
    - Five years of history is enough for same-period-last-year baselines, but not
      deep. Do not over-claim seasonal confidence.
  - ⚠️ **The live risk is script.** If the encoder does not align romanized Odia with
    Odia script, clusters split by *script* rather than theme and it will look like a
    finding rather than a bug. This is what sank the earlier BERTopic attempt
    (Phase 22), so it is a known failure mode here, not a hypothetical one.
    Transliterate first, then embed, and verify on transliterated pairs before
    trusting any cluster. Transliteration is Phase 17, which runs after this, so the
    August contract matches Phase 14's: **cluster within one script**, and treat the
    cross-script alignment check as work that follows Phase 17.

Phase 14 adds inputs, and forces a decision about what a count means.

- Input: spam and duplicate prevalence as governed measures.
- **A campaign is not a false spike.** Spike detection must *not* run on
  de-duplicated counts: 500 citizens filing about the same thing is a real and
  important signal, and collapsing them to 1 destroys it.
- So one number cannot answer the question. The metrics layer defines **three**
  counts, and every spike view carries all three:

  | Measure | Answers |
  |---|---|
  | Total filings | How much work arrived |
  | Unique grievance clusters | How many distinct issues |
  | Unique citizens / signatories | How many people, via the salted identity keys |

- Spikes are **labelled** by which measure drove them, not suppressed. "Up 300%,
  campaign-driven, 1 cluster, 480 signatories" and "up 300%, 260 distinct clusters"
  are different facts, and an official needs to tell them apart.
- Deduplication still matters for the *workload* reading. It just cannot be the
  only reading.

**The headline metrics, each labelled.**

| Metric | Needs | Source | Kind |
|---|---|---|---|
| Distinct problems vs total filings | cluster id | Phase 14 | **Capability** |
| A theme concentrated in one block and rising | theme id + block + time | S4 | **Capability** |
| Spike decomposed into filings / clusters / signatories | cluster id + identity keys | S2 + Phase 14 | **Capability** |
| Return rate after a no-action closure | action type + cluster id | S3 + Phase 14 | **Capability** |
| Share of closures recording no action | action type | S3 | **Insight**. Ships as a SQL view |

Note on the third: EWMA over `(category × district × week)` counts is something an
analyst builds in a day, so bare spike detection is *not* a capability. Decomposing
the spike into three counts is, because two of them need dedup. Ship the
decomposition, not the alert.

**The closure metric splits into an insight and a capability**, and the split is the
whole point.

- **The insight.** 86.65% of resolved complaints close on a templated remark, so
  "share of closures recording no action" is an exact string match for roughly seven
  cases in eight. Deliverable is the view definition, handed over.
- **State the denominator explicitly; it moves the number by half.** Of the 792,038
  complaints whose closing remark is one of the disposal templates, **60.8%** are on
  the bare rung (481,268 bare vs 310,770 claiming action). Measured against *all*
  1,209,138 resolved complaints it is **39.1%**, because 35.8% close on neither
  template. Quote the 792,038 base whenever the 61% figure is used.
- ⚠️ **A bare disposal does not mean the case was mishandled.** Sometimes no action
  is correct: an information request answered, an ineligible claim properly refused,
  a matter already settled elsewhere. Correct closure and premature closure are
  identical in the record. **The 61% is descriptive and must never be reported as a
  failure rate.**
- **The capability is the return signal: does the citizen come back?** Reopening is
  already a column, but refiling requires recognising a new grievance as the same
  issue as a closed one, which is exactly Phase 14. This is the number that leads.
- ⚠️ **Return identifies cases worth reviewing. It does not determine that a closure
  was wrong.** It errs in both directions: citizens return after correct refusals
  they disagree with, and fail to return after bad ones because they gave up.
  Non-return in particular conflates satisfaction with abandonment, and the two
  almost certainly differ by literacy, connection and district. Report it as a
  review-triage signal, never as a rate of wrongful closure.
- **Trajectory is a required control.** A case that goes created → forwarded → ATR →
  disposed had work done whatever the closing phrase says; one that goes created →
  disposed in two days did not. Median resolution at two action steps is 2 days,
  which is where the suspicious mass likely sits. Condition on step count and elapsed
  time before reporting anything.
- **Validate by variance decomposition, not by ranking.** If bare-disposal rates are
  fully explained by case mix (category, request type, district, year), there is
  nothing here. A large surviving residual is the finding. This tests practice
  against composition without ever publishing an office league table. Usual omitted
  variables caveat: case mix is never fully observed.
- Supporting but not decisive: officers already have non-disposal vocabulary for "no
  action warranted" ("not within purview", "can be considered only after a policy
  decision", "will be considered as per rule in due course"), all discard templates
  at volume. Correct closures have somewhere else to go, so they are unlikely to be
  the bulk of bare disposals.
- **Calibration needs humans.** 300 to 500 closures hand-adjudicated, stratified by
  template, request type and trajectory, is the only thing that turns 61% into a
  claim. Not August work; name it so nobody treats the number as settled.

There is no capability hiding in the free-text tail, on the hypothesis that an
officer typing something original signals a non-standard case. The raw split looks
strong (reopen 8.42% vs 4.38%, median resolution 45 days vs 29) but is confounded:
longer cases mechanically accumulate rare remarks *and* take longer. Stratified by
action-step count the gap shrinks sharply and turns non-monotonic (median days 17 vs
33 at three steps, 37 vs 44 at four, 35 vs 39 at five; reopen rates swing from 0.24%
to 5.5% across strata for reasons not currently understood). Direction is consistent,
magnitude is modest, none of it is load-bearing. Do not build on it.

**Everything capability-side routes through Phase 14.** Dedup is not one of four
deliverables, it is the dependency under three of them. Slipping it takes the
intelligence layer down to a SQL view.

⚠️ **The first one carries political risk, not technical risk.** It does not expose
our flaws, it exposes the redressal process closing cases without fixing them. That
is either the finding that proves the system's worth or the finding that gets it
shut down, depending on framing. Report it **at state level as an observation about
the closure workflow, never as an office league table**, and lead with the point
that no existing dashboard could see it at all. It goes to the Executive Director
before it goes anywhere near the government side.

**Deliberately tabled, and why.** Both pass the value-add test and are still wrong
for this audience. Demo metrics should be about the grievances and how they are
handled; anything about how well *our models* perform belongs in the evaluation
appendix. Different audiences, different documents.

- **Citizen-vs-model category disagreement.** Audits the clerks and the model at
  once, which is the worst of both. Retained as an internal diagnostic: it is a free
  validation signal for the categorizer and cheap candidate generation for the
  Phase 20 routing work.
- **PII exposure rate as a supervisor metric.** Not to be confused with the Phase 13
  privacy scorecard, which is a different artifact with a different purpose and
  **stays**. The scorecard is a safety credential, the thing that makes running this
  on real citizen data defensible, and DELIVERY.md commits to it under component
  (a). A PII exposure number on a management screen is the meta-analytical one, and
  it goes.

**Serving + UI.** A `serving/intelligence.py` router with new schemas, the frozen
contract untouched, behind auth, plus a supervisor screen in the frontend.

Deliberately **not** in this phase: office-vs-office ranking and natural-language
querying. Both are Phase 20, for reasons given there.

### 5.4 Phase 16 — A/B evaluation of AI automation (component d)

The question is not "is the model accurate". It is "does the automation help".
Those need different evidence, and the roadmap has so far only planned for the
first.

**Three claims, kept separate.**

| Claim | Evidence | Where |
|---|---|---|
| The model is accurate | Gold sets, offline scorecard | Phases 13, 18 |
| The officer decides better with it | Exposure logs, override and agreement rates | Phase 16 instrumentation |
| Citizens are better off | Disposal time, transfers, reopens, benefit | Phase 16 experiment |

**The corpus already carries the outcome variables.** The strongest asset here: the
primary outcomes need no new instrumentation, because they are the government's own
measurements, recorded before we existed.

- Time to disposal: `resolved_on − created_on`.
- Mis-routing: transfer count via `transfer_status`; escalations via
  `escalation_date`, `review_authority`.
- Rework: `reopened_by`.
- Citizen outcome: `benefitted`.
- Officer effort proxy: action count per ticket in `action_history`.
- Randomization and clustering unit: `office_id`. Also `received_by_id`.

**Design: office-level staggered rollout (stepped wedge).** Chosen for reasons as
practical as statistical.

- Grievance-level randomization has the most power but contaminates: an officer
  seeing both arms learns from the treated one.
- A parallel office-level trial denies half the offices a tool indefinitely, which
  government partners rarely accept.
- Staggered rollout treats every office eventually, matches how a rollout would
  actually happen, and still identifies an effect from variation in timing.
- **Interference is why the unit is the office, not the grievance.** Better routing
  mechanically changes the composition of another office's inbox. That violates
  SUTVA at grievance level.
- Analysis needs a staggered-adoption-robust estimator (Callaway–Sant'Anna,
  Sun–Abraham, or similar). Two-way fixed effects is biased under staggered timing
  with heterogeneous effects. Cluster at the office.

**Instrumentation to build.** Useful whether or not the trial is ever run.

- `janasunani/experiments/`: an **assignment service**, a deterministic seeded hash
  of `(unit_id, experiment_id)` to arm, so assignment is reproducible and
  stateless. Stratified on pre-period volume and district.
- **Exposure log**, append-only: model output, what the officer was shown, what
  they did, timestamps, model and release version.
- **Assigned and exposed are separate fields.** Without that distinction you can
  compute neither a clean ITT nor a defensible treatment-on-treated.
- **Shadow mode**: the model runs on control units too, output hidden. This yields
  counterfactual predictions for controls, which sharpens the analysis
  considerably, and it is the same mechanism Phase 23 needs later.
- **Locked analysis plan**: a versioned in-repo document declaring hypotheses,
  primary and secondary outcomes, MDE, and the analysis specification, frozen
  before launch under the same governance as the gold sets. Not a research
  pre-registration and not required by anyone. Worth doing anyway: it is what stops
  the readout to government becoming a search for the outcome that looks best.
- **Analysis harness**: a reproducible CLI computing ITT with clustered standard
  errors, plus the retrospective analyses below.

**What ships for the demo.**

- **Power calculation on pre-period data.** Given the observed variance of disposal
  time and the real number of offices, what is the minimum detectable effect.
  Honest, legible to a non-technical audience, and it determines whether a trial is
  worth running at all.
- **Counterfactual agreement study.** Run the pipeline over a historical sample.
  Compare AI category and route against the eventual human ones. Estimate how often
  the AI would have avoided a transfer.
- **Time-motion estimate.** Model latency against observed human handling intervals
  in `action_history`.

**The caveat belongs on the slide, not in a footnote.** The agreement study is
suggestive, not causal. The models were trained on those same human labels, so
agreement partly measures imitation. It is the same omitted-variable trap already
flagged for learned routing: harder cases run longer regardless of office, and
office assignment reflects the old policy. Retrospective agreement cannot separate
those. Only the experiment can.

**Governance.** This is a **program evaluation of a government service**, not
human-subjects research, and no ethics review applies.

> **Decision record.** Determination made by the Executive Director, DPIC,
> 2026-07-27, covering the 14 August demonstration and the Phase 16 evaluation
> design. It rests on this being deployment of a government service rather than
> research for publication. **What would invalidate it:** publishing results as
> research, extending to interventions that alter a citizen's service entitlement,
> or any partner institution requiring its own review. Engineers should treat it as
> settled and route changes of scope back to the decision owner rather than
> reinterpreting it.
The one thing worth remembering is that the exemption rests on this not being
research for publication, so if that ever changes, the question has to be asked
before data collection rather than after.

What governs the trial:

- **Departmental sign-off** on the rollout design and the order of offices. The
  sequence is an administrative decision, not ours.
- **DPDP obligations** apply regardless of research status, since the evaluation
  reads real citizen records.
- **Enforceable protections, not a principle.** "No arm may leave a citizen worse
  off" is a sentiment until it has mechanisms:

  | Protection | Mechanism |
  |---|---|
  | Existing service path retained | Control offices keep the current process unchanged. Treatment adds decision support, never removes a route |
  | Harm indicators monitored | Disposal time, reopen rate, and escalation rate tracked per arm, on the same cadence as the primary outcome |
  | Escalation defined | A named owner reviews any arm-level degradation, with a stated response time |
  | Pause conditions predetermined | Written before launch, in the locked analysis plan. Crossing one halts the rollout without needing a new judgement call |

- Spam auto-reject stays off during any trial.
- Analysis reads the lake, so it sees redacted text and the same scope rules as
  everything else.

### 5.5 Phase 17 — Sarvam benchmark, provider registry, egress control (component e)

Sarvam is the most promising single quality lever available, because our weakest
stage and their strongest capability are the same thing: Odia document
understanding.

**What Sarvam offers** (checked 2026-07-27 against their docs; re-check before
building, this moves fast).

| Model / API | ID | What it does | Price | Limits |
|---|---|---|---|---|
| Sarvam Vision | `sarvam-vision` | 3B vision-language model for document digitisation. Text extraction, **table structure**, layout preservation. Out: HTML / Markdown / JSON. In: PDF, PNG, JPG, ZIP | **₹0.50 / page** | 10 pages per PDF, 200 MB, **10 req/min** |
| Sarvam-30B | `sarvam-30b` | MoE, 30B total / 2.4B active, 128 experts, **64K context**. Native tool calling. OpenAI-compatible | ₹2.5 / ₹1.5 cached / ₹10 per 1M tokens (in / cached / out) | Tiered, below |
| Sarvam-105B | `sarvam-105b` | Flagship, ~9B active, 128K context | ₹4 / ₹2.5 / ₹16 per 1M tokens | Tiered, below |
| Transliteration | text API | Roman ↔ native script, bidirectional. **Odia is `od-IN`** | Text pricing | 1,000 chars per request |
| Sarvam Translate / Mayura | translate API | 22 scheduled languages (Translate); colloquial and code-mixed (Mayura) | Text pricing | Tiered |
| Saaras v3 / Saarika v2.5 | speech APIs | ASR with transcribe / translate / **transliterate** / codemix output modes | ₹30 per hour (₹45 diarised) | Tiered |

Tiers: Starter is pay-as-you-go, Pro ₹10,000/month, Business ₹50,000/month. Rate
limits differ by model class, and **the limit that binds us is the lower one**:

| Model class | Starter | Pro | Business |
|---|---|---|---|
| Default chat models | 60 | 200 | 1,000 |
| **Sarvam-30B / 105B** (our candidates) | **40** | **60** | **120** |
| Vision, document intelligence | 10 | 10 | 10 |

Vision limits do not rise with the plan. Verified against Sarvam's published rate
limits, 2026-07-28.

**Language coverage is not the constraint.** Sarvam Vision lists all 22 scheduled
languages plus English, Odia among them explicitly, which is more than our current
OCR can honestly claim.

**Cost is not the constraint either.** A 500-page benchmark is **₹250**. Running
Sarvam-30B over all 1.37M grievance subjects is roughly 275M input tokens, on the
order of **₹700**. These numbers are small enough that cost should not shape the
benchmark design; latency, rate limits, and quality should.

Four things to check before relying on any of it:

- ⚠️ **Handwriting is claimed but not benchmarked.** Corrected 2026-08-07: an
  earlier revision of this section said handwriting was unmentioned in the Vision
  documentation. It is mentioned, and directly — Sarvam state the model is trained
  on handwritten text across all 22 Indian languages, and that it beats
  general-purpose OCR on Indian-language handwriting. What they publish no number
  for is handwriting itself: the headline scores are general document benchmarks,
  and the only handwriting statement is qualitative, that accuracy is lower on
  highly stylised hands. A large share of our corpus is handwritten grievance
  letters. **Put handwritten pages in the benchmark sample deliberately,
  stratified, and report them separately** — not as a hedge against an
  unsupported case, but because the printed/handwritten split is the number
  nobody has published and the one our corpus turns on.
- ⚠️ **Sarvam-30B runs with reasoning enabled by default, and reasoning tokens bill
  as completion tokens** at 4x the input rate. Disable it for classification or
  the cost model above is wrong.
- **Vision is capped at 10 pages per PDF and 10 requests per minute.** Our pipeline
  is already page-level so the page cap is harmless, but 10 req/min is the binding
  constraint on any full-corpus run: at 10 pages per request that is 6,000 pages an
  hour on the starter tier.
- **Transliteration does not do Indic to Indic** and caps at 1,000 characters per
  request, so it chunks. It is a direct candidate to replace IndicXlit for
  romanized Odia, and the same derived-field rule applies: it never becomes the
  string redaction offsets are computed against.

**The open weights are the architectural point.** Sarvam is a *provider with two
backends*, not an external dependency:

- `sarvam-hosted`: the API under the Odisha authorization. Cheap, no GPU, fastest
  path to a benchmark.
- `sarvam-selfhosted`: Apache-2.0 weights on the GPU box. No egress at all.

Both sit behind one registry entry. That is what makes the egress decision
reversible, and why §3.1 replaces the invariant rather than abandoning it.
Sarvam-30B at 4-bit is roughly at the limit of the current 24 GB L4; 105B is beyond
it and would need a larger instance. **Measure before assuming the exit ramp is
usable.**

**Registry generalization.** Today a model is a local DVC path. It becomes:

```
{name, alias, provider: local | sarvam-hosted | sarvam-selfhosted,
 artifact_or_endpoint, version, trust_tier}
```

`trust_tier` is one of `same-host` / `dpic-infra` / `authorized-external` (§3.1).
Self-hosted Sarvam on the GPU box is `dpic-infra`, not `same-host`: it is a real
network hop to a second machine, and the route declaration says so.

- The merged MLflow helpers (Phase 6) finally get a caller. Register versions,
  resolve an **alias** (`@champion` / `@production`, not the deprecated stages) at
  deploy/startup, cache locally, expose in health/telemetry, keep one-command
  rollback. Add the `mlflow` service to compose.
- **A hosted endpoint is not reproducible the way a pinned artifact is.** The
  vendor can change the model behind a stable name. Mitigate: record the returned
  model ID and response metadata on every call, put both in the release manifest,
  re-run the benchmark on a schedule to detect drift. Do not claim reproducibility
  we do not have.

**The egress client.** One module, and only one, may send citizen text outbound.

- Per-call audit: ticket, stage, provider, model ID, bytes, timestamp,
  authorization reference, response metadata.
- Network allowlist to Sarvam endpoints only.
- Config kill switch reverting every `authorized-external` entry to a counterpart
  at a lower tier. Those counterparts stay maintained so the switch works.
- Timeouts, retries, circuit breaker, local fallback. A remote model in the live
  path adds an availability dependency the CPU box has never had.

**What to benchmark**, against the Phase 13 gold sets, per language, on Odia,
romanized Odia, English.

| Stage | Sarvam candidate | Incumbent | Why it might win |
|---|---|---|---|
| OCR | Sarvam Vision | pytesseract `ori`, DeepSeek-OCR | Largest expected gain. DeepSeek is English-only in practice; Odia comes out script-confused |
| Transliteration | Sarvam Translate / Mayura | IndicXlit (Phase 21) | Code-mixed romanized Odia is the designed-for case |
| Categorization | Sarvam-30B, structured output | MuRIL, 0.7104 acc | MuRIL is English-gated today; the 0.51 worst-class F1 has headroom |
| Summarization | Sarvam-30B | BART | BART is English-only; DSI usefulness 1.9 at best, 0.45 on IDs |
| PII detection | Sarvam-30B NER | Presidio + spaCy | spaCy has no Odia name model. The biggest measured gap |

Two cautions on the PII row. Spans must be returned over the **original** text or
they are unusable for redaction. And using an external model to *find* PII means
sending un-redacted text out, which the authorization permits but which is the
single highest-sensitivity call in the system. Adopt it last, not first.

**Benchmark on more than accuracy.** Latency, cost per 1,000 grievances, rate
limits, failure modes, drift, privacy cost. Same posture as every technology bet
here: a hypothesis to measure and promote only if it wins.

**Authorization: already in place.** The Government of Odisha holds an MoU with
Sarvam, and Principal / Additional Chief Secretary Vishal Dev (IT Department) has
approved its use on this data including PII. Nothing further is needed to start.
The benchmark can run on real grievances from day one, which is why component (e)
is one of the less risky items in the August plan rather than one of the more
risky ones.

**Engineering gates before wiring Sarvam into the live path.** These are not
permission checks. They are the controls that keep the choice reversible:

- Audit logging live and verified.
- The kill switch tested, not just implemented.
- A maintained lower-tier counterpart for every `authorized-external` entry.
- The MoU and sign-off reference recorded alongside the route declaration, so the
  basis for the egress is legible in the repo rather than in someone's memory.

### 5.6 Execution plan to 14 August

Fourteen working days. What follows separates the three kinds of work, because they
scale differently and conflating them is how the schedule quietly fails.

**The operating model.** One accountable engineer, augmented across implementation,
testing, analysis, documentation and independent review by frontier-model agents, plus
an intern on labelling. That augmentation changes throughput, not accountability. The
engineer owns integration, validation against real data, and final acceptance on
anything touching PII, the trust boundary, or deployment. Agent review runs before
that acceptance and strengthens it; it never substitutes for it. Where this plan says
work is parallel, it means authoring is parallel.

#### A. Human bottlenecks

These consume calendar time and cannot be parallelized, delegated to an agent, or
compressed by working harder. They are the schedule.

| Work | Who | Why it cannot be compressed | Status |
|---|---|---|---|
| Adjudicate 85 PII pages | Intern | Reading citizen text and judging span boundaries. Gates the privacy scorecard *and* the Sarvam PII comparison | Finishing wk 1 |
| **Transcribe an OCR ground-truth sample** | **Unassigned** | Someone must hand-transcribe scanned Odia and English pages, printed and handwritten. The earlier 77.9% is a plausibility rate, not accuracy, so there is no existing ground truth to reuse | ⚠️ **Owner to be named at the ED meeting** |
| Label the disposal templates into the 7-class action taxonomy | Engineer | ~500 strings. LLM-assisted drafting, human adjudication. Half a day, but it gates the closure view | Not started |
| Choose the backlog slice for Phase 14 | ED + engineer | A judgement call about what is defensible to demonstrate. Brainstorming session. Also a technical gate: the redaction pass and both backfills cannot start until it is fixed | Not scheduled |
| Lock the A/B analysis plan | Engineer, statistical judgement | Estimator choice and the power calculation are not mechanical | Not started |
| Rehearsal | Engineer | 14 Aug, code frozen 13 Aug | Fixed |

⚠️ **The transcription set is the largest unmanaged risk in the plan.** It sits on
the week-3 critical path, has no owner, and cannot be produced by an agent because it
is the ground truth an agent would be measured against. Fifty pages is probably the
floor for reporting handwritten and printed separately. It goes to the ED meeting as a
named decision; if no owner comes out of it, drop the OCR accuracy row and report
Sarvam Vision comparatively only.

#### B. The serial chain

Ordering forced by real dependencies, not preference.

```
deploy to AWS ──► end-to-end pipeline run ──► live demo path

PII gold complete ──► privacy scorecard ──► Sarvam PII comparison
transcription set ──► OCR ground truth ──► Sarvam Vision scorecard

backlog slice chosen ──► grievance redaction pass ──► dedup index backfill ──┬──► duplicate-adjusted workload
                                                                            ├──► spike three-count
                                                                            └──► S4 clustering (on dedup'd units)

semantic layer definitions ──► metrics ──► supervisor screen
```

Note what the third chain does *not* contain. The historical corpus never passes
through `pii_tagger`, so the redaction pass of §3.2 is its safety edge and the
gate on everything downstream of it. Nothing indexes `complaints.grievance`
directly.

- **The two backfills are long-running and belong on the calendar, not in a sprint
  list.** The dedup index over the chosen slice and the MuRIL embedding pass are
  multi-hour GPU/CPU jobs. Write the stage early, kick the backfill, work on
  something else while it runs. Starting either late in week 3 is a plan to fail.
- **The redaction pass is short but strictly first.** CPU-only over short text, so it
  is cheap, but it gates both backfills. It cannot start until the backlog slice is
  chosen, which makes that ED decision a technical dependency rather than a
  presentational one.
- **S4 cannot precede dedup.** Clustering over un-deduplicated filings makes a
  400-signature campaign its own theme, which is circular.
- Everything capability-side converges on the dedup index. It is the single point of
  failure for the intelligence layer (§5.3).

#### C. Parallel with subagents

Agent-suitable work is code that is self-contained, testable against synthetic or
fixture data, and touching a disjoint file set. Each of these can run while the
serial chain proceeds.

| Workstream | Agent | Why it is independent | Merges into |
|---|---|---|---|
| `janasunani-redact-grievance` batch job + tests | `executor-sonnet` | Wraps the existing Presidio analyzer over one column. Testable on synthetic strings; the real run needs the slice | Phase 14 step 0 |
| MinHash/LSH implementation + tests | `executor-sonnet` | Pure algorithm, testable on synthetic strings before any real index exists | Phase 14 stage |
| Spam feature extraction | `executor-sonnet` | Reuses existing OCR quality gates (word count, alpha ratio, trigram share). No new data | Phase 14 stage |
| Closure SQL view + action-type lookup | `executor-sonnet` | Depends only on the template labels, not on dedup | Phase 15 S3 |
| Semantic layer YAML, compiler, per-metric tests | `executor-sonnet` | Definitions and compilation are testable against fixtures | Phase 15 S1 |
| Supervisor screen | `frontend-dpic` | Builds against the frozen API contract with mocked responses | Phase 15 UI |
| Sarvam provider adapter + registry entry | `executor-sonnet` | Testable with recorded responses, no live calls needed | Phase 17 |
| Power calculation + analysis plan draft | `planner-opus` | Analytical, no code dependency | Phase 16 |
| Per-entity / per-language scorecard harness | `executor-sonnet` | Extends the existing verifier, runs on the gold set when it lands | Phase 13 |

Review the security-sensitive merges (dedup index, anything touching `grievance`)
with `reviewer-fable`; routine merges with `reviewer-opus`. Agent review is a gate
before the engineer's acceptance, not the acceptance itself: the redaction pass, the
dedup index and the deploy are signed off by a person who has read the diff.

#### D. What actually limits parallelism

Not ideas. **Shared mutable state.**

- One CPU box, one GPU box, one Postgres, one lake. Two agents cannot both run a
  backfill, and pytest against the production Postgres drops tables.
- Agents editing overlapping files conflict. Use `isolation: worktree` and keep
  file sets disjoint, or serialize the merge.
- **Agents parallelize writing code. They do not parallelize validating it against
  real data**, which needs the box and is therefore serial. Budget for that
  separately: the validation queue is the hidden constraint, not the authoring.

## 6. Part III: post-demo maturity (Phases 18–24)

After the demo, the system matures toward two goals: Odia (native and romanized) as
a genuine first-class citizen, and the corpus turned into governance intelligence an
official can query on demand, while becoming portable enough to interest other
governments.

Part III is a set of **gated investments, not an implementation commitment**. See
the gates at the end.

### Standing decisions

- **Local Indic LLM, phased hybrid.** Task-specific Indic models now; a local Indic
  LLM later. Heavy uses (theme induction, cluster narration, hardest cases) run as
  on-demand GPU batch jobs, never in the serving path and never always-on.
- **Multilingual approach is now empirical, not principled.** The original rule was
  Indic-native rather than cloud-translate, justified by no-egress. With Sarvam
  authorized, Phase 17 benchmarks and Phase 21 adopts whichever wins per stage.
  IndicTrans2 stays an optional deferred fallback.
- **Modularity stays minimal.** A fixed canonical stage order shared by batch and
  live; only **models** are swappable, via the registry. There is no
  configurable/reorderable stage graph. Some orderings are policy invariants: PII-safe
  text must feed anything summarized or presented, and original-text offsets stay
  authoritative.
- **MLflow is a control-plane, not a runtime dependency.** Resolve an approved alias
  at deploy/startup, pin the artifact in a release manifest, cache locally, expose in
  health/telemetry, keep one-command rollback. No unreviewed automatic production
  switching.
- **Feedback is governed capture, not online learning.** Officer edits are signal,
  not automatic ground truth; adaptation runs in shadow mode first.
- **Analytics models stay on-box.** The Sarvam authorization covers grievance
  processing. An official's free-text query is a different data class, and sending it
  out would need its own authorization, which we have not sought.

### Execution order

1. **Finish and harden the demo.** Auth/RBAC on real-data endpoints, a tested
   restore, baseline observability, before any *additional* real-data exposure or
   new Part III endpoint. The system already stores and processes real citizen data.
2. **Phase 18**, evaluation harness and operational safety.
3. **Phase 19**, model and pipeline platform.
4. **Phase 20**, analytics II. Language-agnostic, so it can run alongside 19 and 21.
   Do not serialize it behind the model platform or the language work.
5. **Phase 21**, Odia-first models, gated on Phase 18.
6. **Phase 22**, semantic/unstructured intelligence. Needs Phase 21 normalization,
   capacity-gated.
7. **Phase 23**, governed feedback loop. Needs the Phase 22 index.
8. **Phase 24**, jurisdiction pack, running alongside as the export throughline.

### Phase 18 — Evaluation harness & operational safety

Phase 13 built the gold-set governance machinery and the PII slice. This extends it
to every other task, and closes the operational gaps.

- **Per-task, per-language eval harness + scorecard**, built on
  `pipeline/pii_eval.py`. One `eval_results.jsonl` row per
  `(task, model_name, model_version, gold_version, language)`. Covers: OCR quality
  **and** downstream task success; categorization and **routing** top-k,
  calibration, abstention/override rate; summary faithfulness, omission, harmful
  disclosure, officer acceptance; latency and cost by path, model, language.
- **Status-quo baseline.** Run the current English-centric models against Odia,
  romanized-Odia, and English gold slices, reproducing the §4 DSI baselines as the
  English anchor per task. The point is the Odia deltas, which the clinic never
  measured.
- **Routing gets its own held-out gold**, not only PII, categorization, and
  summarization.
- **Operational safety.** Identity/RBAC on real history, correction, and
  intelligence endpoints; tighten the permissive demo CORS; mirrored, checksummed
  runtime artifacts; a **tested restore** of the Postgres backup; stage-level
  metrics and traces (latency, failure, abstention, language mix, redaction counts,
  model/recipe version).

  *Partly down-paid by Phase 12* at the stack/image level: site-wide Caddy
  `basic_auth`, health-gated deploys with auto-rollback, atomic image tagging, a
  temporary OIDC-scoped CI IAM role. *Still owned here:* per-user **RBAC**
  (basic_auth is coarse, not identity), a **tested restore** drill, **audit
  logging** and authz/redaction on real-data reads, and model-*level* rollback
  (Phase 19). Egress enforcement moved to Phase 17.

  **Recovery is the largest infra risk.** The prod DB, lake, model cache, images,
  and backups share one host and root-volume failure domain, and the backup cron is
  box-only. Codify the timer, retention, encryption, and a restore test from code
  (issue #31). The single-instance Postgres is a known prototype limit.

  The **GHCR PAT** on the box is the one standing credential; rotate or replace it,
  and mirror/checksum every runtime model (BART currently downloads from a public
  hub at startup).

### Phase 19 — Model & pipeline platform

Make model swaps and retrains safe and reproducible without over-building.

- **One validated processing recipe shared by batch and live.** Converge
  `pipeline/pipeline.py` and `inference/service.py::process` on a single recipe with
  declared inputs/outputs and startup validation, so the paths cannot drift. It is a
  **fixed typed dataflow with explicit branches**, not a literal linear chain: typed
  grievances skip format-classification, OCR, and page-type; document submissions
  include the page-level path and gating. The validator enforces the mandatory
  safety edges, including PII redaction before summarization or presentation, and
  original-text offsets kept authoritative. It also enforces which stages may use
  which `trust_tier`.
- **Establish the jurisdiction config contract now**, not just in Phase 24. Define
  the minimal seam (taxonomy, routing mappings, languages, retention, RBAC, eval
  thresholds) as configuration during this phase, so Phases 20–23 do not bake in
  Odisha-specific assumptions that are expensive to extract later.
- **API evolution policy.** Pin the current response contract as **v1** and specify
  additive compatibility, versioned routes, idempotency, structured errors,
  upload/request-size limits, and async job/status semantics for long OCR/GPU
  operations.
- **The release manifest is the highest-value piece here.** Phase 12 ships immutable
  commit-SHA *images*, but an image SHA does not pin the independently bind-mounted
  artifacts that also determine a result: DVC model hashes, routing mappings, the
  Parquet snapshot, the Alembic revision, public Hugging Face model revisions, and
  (from Phase 17) the resolved remote model IDs. The manifest joins all of these so
  an inference release is reproducible and roll-back-able as a unit. More valuable
  than any generic reorderable-pipeline framework.

### Phase 20 — Structured analytics II

The two increments Phase 15 deliberately deferred.

- **Adjusted comparisons, only after statistical and policy review.** Raw
  office-vs-office ranking carries the same omitted-variable bias as routing: harder
  cases run longer regardless of office, and office assignment reflects the old
  policy. So it is not an exposable measure. Do it properly instead: case-mix-adjusted
  disposal times, risk-adjusted SLAs, and event-study / difference-in-differences on
  interventions, gated on the Phase 15 field reconciliation and a documented review.
  Adjusted comparison is the answer to the comparative questions government users
  will ask; whether a given adjustment is trustworthy is an empirical question
  decided by that review, not asserted here.
- **Natural-language querying, last.** **Explicitly not a 14 August deliverable.**
  DELIVERY.md lists it as out of scope and it stays there. What follows is the
  build spec for when it is taken up.

#### Architecture: semantic parsing, not text-to-SQL

The model never writes SQL. It fills in a structured form, and a hand-written,
tested compiler turns that form into SQL. The form's schema **is** the semantic
layer, which is why S1 has to exist first: you cannot parse into a representation
that does not exist.

```json
{
  "intent":     "ranking",
  "measure":    "filings",
  "dimensions": ["district"],
  "filters":    [{"dim": "category", "op": "in", "values": ["Water Supply"]}],
  "time":       {"field": "created_on", "from": "2026-04-28", "to": "2026-07-28"},
  "order":      {"by": "measure", "dir": "desc"},
  "limit":      5
}
```

Two predictions produce it, from one shared encoder:

| | Predicts | Granularity | Head |
|---|---|---|---|
| **Intent** | Which of ~20 request shapes (count, ranking, trend, comparison, share, breakdown) | One per question | Sequence classification, as in the categorizer |
| **Slots** | Which words name a dimension, a filter value, a time range, a direction | One per token, BIO tagged | Token classification, as in the PII tagger |

Both architectures already exist in this repo with different label sets. MuRIL is
the encoder: multilingual, handles Odia, already fine-tuned here, CPU-viable at
inference.

⚠️ **Do not implement this as embedding similarity between the question and a bank
of canned queries.** Two reasons it fails. The query space is combinatorial, not
enumerable, once filters over 30 districts and 35 categories are allowed. And
sentence embeddings encode topic, so "water complaints rising in Puri" and "water
complaints falling in Puri" are near-identical in that space while requiring
opposite answers. Direction, negation and comparison are exactly what pooled
embeddings blur, and they are exactly what determines the result. Similarity *is*
the right tool for one sub-problem only: resolving a mention like "water" against
the closed list of category names.

#### Build order

Three milestones. Each is independently useful and each covers the failures of the
one below it.

1. **Guided query builder.** Dropdowns over the semantic layer. No language model,
   no hallucination surface, answers most real questions. **Build this first, not as
   a fallback**, for a reason beyond risk: it is the instrument that produces the
   training distribution. Every assembled query is logged as a form, so after a few
   weeks there is a real record of what people ask, which is otherwise unobtainable
   before a system exists.
2. **Intent + slot parser.** Trained on generated data (below) plus the logged
   demand from milestone 1.
3. **LLM fallback for the compositional tail.** Multi-clause comparisons with
   exclusions, follow-up questions carrying dialogue state. Emits the same IR under
   schema-constrained decoding. Degrades to "could not parse, here is the builder"
   without taking anything else down.

#### Training data by inversion

Do not annotate questions. Generate them backwards: sample a legal IR, render it to
a sentence through a template, and the labels are correct by construction because
the slot values were inserted rather than found. Templates crossed with legal
combinations give tens of thousands of examples cheaply.

- Add phrasing variety by hand-writing several renderings per intent, or with an LLM
  **offline at build time**, checking that slot values survive verbatim so alignment
  holds. The served model stays small and local.
- Add 200-300 real hand-labelled questions. This is the only manual annotation. Its
  job is idiom, not grammar: synthetic data teaches the template, real data teaches
  how people actually type.
- Odia-language questions roughly double the generation work. MuRIL covers the
  language; the generator needs the renderings.

#### Gaps in the earlier spec, all required

- **Value linking**, which is where these systems fail in production more often than
  on SQL syntax. "Puri" to a district id, "water" to some subset of 35 categories,
  "PMAY" to either a category or a scheme name in free text. Needs a dimension-value
  index with fuzzy and multilingual matching. Small closed vocabularies, so a lookup
  table plus curated synonyms beats a model here.
- **Mandatory disambiguation.** "Resolved" has at least three defensible readings in
  this schema (the `status` field, `resolved_on` non-null, the §5.3 disposal ladder)
  and "complaints" means filings or distinct problems. §5.3 established that these
  give different numbers. A parser that silently picks one is dangerous *because*
  the choice is known to matter. It must ask.
- **Echo the interpretation before executing.** "Showing filings, by district, where
  category is Water Supply, 28 Apr to 28 Jul, top 5." This converts the dangerous
  failure mode, a valid-but-wrong query returning a plausible number, into a visible
  one, at near-zero cost.
- **Evaluation set.** 100-200 question-to-expected-result pairs graded on
  **execution match**, not string match, against hand-written correct SQL. Held-out
  real questions only, never synthetic. Without this there is no way to know it
  works. Writing it is domain work, not engineering.
- ⚠️ Most interesting questions need window functions over `action_history` (latest
  action per ticket, elapsed time between steps) over 6.5M rows. Generated SQL is
  weakest exactly there, which is another argument for a compiler rather than a
  model emitting SQL.

#### Egress, and a constraint that is now stale

The earlier spec required a **local** model capacity-gated on the CPU box. That
predates the Sarvam authorization and is stricter than necessary here, because
**the model never sees citizen data**. It receives the question and the metric
catalogue, and returns a form. Rows are fetched and rendered locally. That is a
completely different egress profile from OCR or summarization, where grievance text
itself leaves.

⚠️ One real exposure: questions can themselves contain PII ("what happened to
X's complaint, mobile 98765..."). Run the existing PII tagger over the question and
redact or refuse before it goes anywhere.

Local remains the standing exit ramp, as elsewhere, since the candidate weights are
Apache 2.0.

#### Effort

One engineer, after Phase 15 S1 lands.

| Item | Estimate |
|---|---|
| S1 built to queryable standard rather than dashboard-minimum | +1-2 weeks |
| Guided builder + query logging | 2 weeks |
| Value-linking index | 1 week |
| Generation pipeline, training, disambiguation | 2 weeks |
| Evaluation set and iteration | 2 weeks |
| Query & disclosure controls (below) | 1-2 weeks |

Roughly 9-11 weeks. The guided builder alone is about a third of that and delivers
most of the practical value, which is why it leads.

**Query & disclosure controls**, a release gate for the query path and for any
exposed comparison. Constrained SQL and RBAC are necessary but not sufficient
around citizen records:

- an **allowlisted query plan / AST**, not arbitrary SQL, validated against the
  semantic layer;
- a **read-only, isolated execution role** with time, memory, and result-size
  limits, so a valid query cannot exhaust the shared CPU box;
- **minimum-group-size suppression** and restricted drill-down fields, so an
  aggregate cannot re-identify an individual;
- prompt, query, and result **audit logging**;
- visible **data-source and freshness provenance** on every answer.

### Phase 21 — Odia-first models

Today non-English is downgraded to `Uncategorized` / `fallback`. Make language a
property that follows the grievance. Ships only when it clears the Phase 18 gates.

- **Language ID, split by where text exists.** Pre-OCR, the image-based
  format-classifier signal picks the OCR model (`eng` / `ori` / `eng+ori`).
  Post-OCR, and immediately on the typed-text path, a text-based **IndicLID** stage
  *refines* `pages.language`. It refines the value; it does not replace the pre-OCR
  image signal, which scanned documents need before any text exists.
- **Romanized normalization.** An **IndicXlit** step transliterates romanized Odia
  into Odia script, after OCR or on typed text. It is **not length-preserving**, so
  it writes a **separate derived field** used only for language-ID, classification,
  and embedding, never the string redaction offsets are computed against. Redacting
  on transliterated text would misalign spans. That is a privacy hazard, not a
  cosmetic one.
- **Multilingual models** via the Phase 17 registry: summarizer BART → IndicBART /
  mT5-Indic; an IndicNER PERSON recognizer added to Presidio, which also lifts
  Indian-name recall on English pages; MuRIL retrained on our corpus including
  native and romanized Odia (issue #34).

  Phase 17 benchmarks Sarvam against each of these on the same gold sets. Adopt per
  stage whichever wins: Indic-native model, Sarvam hosted, or self-hosted Sarvam
  weights. The registry makes that a per-stage decision, not all-or-nothing. **If
  Sarvam Vision wins on OCR, the pre-OCR language routing above becomes far less
  load-bearing**, which is the single largest possible change to this phase.
- **Relax the English-only gates**, the crux without which the swaps do nothing:
  `_is_english` in `categorizer/stage.py`, the English branch in `service.py`, and
  `WHERE language LIKE '%English%'` in `pii_tagger.py`. Route by detected language
  instead.
- **Land the empirical crosswalk** (issue #33) as the improved routing, gated by the
  routing gold set.

### Phase 22 — Semantic / unstructured intelligence

Retrieval and emergent-theme discovery over grievance **text**. Gates on Phase 21
normalization (embedding raw romanized or out-of-distribution text is what sank the
earlier BERTopic attempt) and on a capacity benchmark before any index is committed.

**Boundary with Phase 15 S4.** The demo ships the cheap slice: subject-line
embeddings from the existing fine-tuned categorizer encoder, clustered within
category, for location-and-time themes. It needs no new model, no OCR and no dense
index. This phase is everything that does need those: a purpose-built multilingual
embedder, the vector index and its capacity gate, retrieval over full document text,
and LLM-as-operator taxonomy induction. S4's results tell us how much of the rest is
worth building.

- **On-box embeddings.** `olap/embed.py` writes `data/interim/embeddings.parquet`
  with schema `ticket_no, vector, model_version, source, kind, correction_id`, so a
  raw-grievance vector is distinguishable from a correction-derived one. The embedder
  is chosen from benchmark candidates (BGE-M3, e5-mistral, gte-Qwen2, and Sarvam if
  it offers one) on multilingual task quality, licensing, artifact size, build cost,
  and measured performance.
- **Capacity gate before VSS/HNSW.** The dense index is the first genuinely new
  scaling regime on the downsized CPU box. Benchmark artifact size, build time,
  filtered-query p95, memory, restart/rebuild, and incremental updates before
  choosing. Brute-force `array_distance` may be fine for offline analysis;
  interactive retrieval is not assumed acceptable without the benchmark. HNSW needs a
  persisted DuckDB table with fixed-size `FLOAT[n]` vectors, not a Parquet view.
- **Case-based retrieval.** Similar past grievances with their actual resolutions and
  disposal times. Feeds Phase 23 shadow adaptation and the Phase 20 query agent: a
  hybrid question like "what are people saying about water in Puri, and is it
  rising?" joins semantic retrieval to structured aggregation.
- **Emergent themes via semantic operators.** A candidate route to topic modeling
  without an API: treat a **local** LLM as a batch relational operator over the
  corpus (semantic aggregate / filter / top-k, in the LOTUS / TAG / DocETL lineage)
  to induce a taxonomy and label grievances, as an on-demand GPU batch job. The
  hypothesis is that an LLM *reads* broken-English and romanized-Odia better than an
  embedder *clusters* it. Benchmark against the embedding track and promote only if
  it wins. Embedding clustering (UMAP + HDBSCAN + c-TF-IDF over normalized text) is
  the cheap first pass; clusters that do not fit the 62 categories are candidate new
  issues. Narration into a plain governance brief is the last, deferred step, never a
  serving dependency.

  Phase 14's campaign clusters are a deterministic, zero-model preview of this. If
  they prove sufficient for the governance questions actually asked, that is evidence
  about how much of this phase is needed.

### Phase 23 — Governed feedback loop

Officer corrections are valuable but **not automatic ground truth**: they can encode
local practice, workload pressure, policy disagreement, or error. Three stages.

- **Capture.** Authenticated, append-only corrections (actor, reason code,
  before/after values, timestamp, model/release version, audit history) through
  `POST /grievance/{id}/correction`, plus the officer **correction UI** on the result
  screen, which turns Phase 11's read-only screen into where an officer works.
  Corrections materialize to the lake; training reads the lake, never OLTP directly.
- **Curate.** Quality checks, deduplication, adjudication, taxonomy-version
  compatibility, explicit inclusion in a versioned training set.
- **Learn.** Retrieval-based adaptation in **shadow/suggestion mode first**, reusing
  the Phase 16 shadow mechanism and the Phase 22 index. Periodic batch retraining
  only after a minimum clean-label volume clears a held-out eval; promotion via
  controlled rollout and rollback. Routing stays **decision-support, not autonomous**:
  the omitted-variable bias in a disposal-time objective is a release constraint, not
  a footnote.

### Phase 24 — Jurisdiction pack (DPI portability)

What turns "an Odisha deployment with reusable code" into something another
government can adopt. Taxonomy, routing mappings, languages, model release, eval
thresholds, retention rules, and RBAC policy all become **configuration and data**,
with a portability test against a second, synthetic jurisdiction. The minimal config
*contract* is established early (Phase 19); this phase is the second-jurisdiction
**validation**. It runs alongside the other phases as the export throughline.

### Go / no-go gates

Part III is a direction to fund, not an implementation commitment. Do not present it
as committed until five gates exist:

1. a reproducible, authenticated, observable demo baseline with a tested restore;
2. per-language and per-task release thresholds with versioned gold sets;
3. a normalized, lake-backed data path for embeddings and corrections;
4. governed correction curation with shadow evaluation before any adaptive behavior;
5. a working, tested egress kill switch with a maintained on-box counterpart for
   every `authorized-external` model, so no capability becomes unrevocably dependent
   on a vendor.

Explicitly deferred: local-LLM narration, semantic-operator theme induction,
autonomous adaptation from individual corrections, learned routing optimized on
disposal time, any fully-generic reorderable pipeline, HNSW unless a measured
interactive-retrieval need appears, and a CPU-box-resident interactive NL-query model
unless it clears a capacity benchmark.

## 7. Cross-cutting

### Infrastructure (two boxes)

**Whose machines these are.** Both boxes are **DPIC's own AWS account**,
ap-south-1. Nothing runs on Government of Odisha infrastructure, and nothing
will unless the demo is approved, at which point a vendor takes over and builds
the production system. Everything in this document is a prototype on DPIC
hardware. That is what `dpic-infra` means in the §3.1 trust tiers, and it is why
the tier exists as a category separate from `same-host`.

Self-host on Docker. S3 is the only stateful AWS dependency. Terraform
(`deploy/terraform/`), IAM instance roles only, region ap-south-1.

- **CPU box** (always on, t3.large, Elastic IP 52.66.116.80): Postgres OLTP, api,
  frontend, proxy (Caddy) in compose, plus migration/materialization one-offs and a
  nightly `pg_dump` → S3. Never `docker compose down -v`: the OLTP volume holds
  production data.
- **GPU box** (on demand, g6.xlarge / L4, `gpu_box_count` toggle, ~$1/hr while up):
  DeepSeek OCR, the Part III embedding/LLM batch jobs, and from Phase 17 self-hosted
  Sarvam weights as the no-egress alternative. Created and destroyed per use; nothing
  stateful survives. Sarvam-30B at 4-bit is at the limit of a 24 GB L4 and 105B is
  beyond it, so the exit ramp may need a larger instance type.
- DVC remote `s3://dpic-dvc-cache/janasunani` (dump, lake, models); documents in
  `s3://janasunani-documents-main`; backups in `grievance-database-backups-main`.
  Do not DVC-track the OLTP DB.

### Testing policy (every phase)

Real-code-path pytest, green before "done":
`uv run --extra pipeline-core pytest && uv run ruff check .`. OLTP tests run on
**both** SQLite and Postgres. Never run pytest against the production container
(fixtures drop tables), and never read or recurse into `data/` (real citizen PII).

### Data & schema

The authoritative schema is the dump `data/raw/Dump20250730.sql`, a `mysqldump` of
`sociomatics_ticket` (complaints, 56 columns, plus action history), mapped to clean
snake_case by the single source→field map in `ingestion/schemas.py`. Out of scope:
the ORTPS analysis pipeline, a different application not used here.

## 8. Decisions log

Terse and dated. The reasoning for choices that are not obvious from the code.

- **2026-07-02.** Two-box split (CPU always-on, GPU on-demand); compose + S3 +
  Postgres-container, no RDS. The biggest full-scale risk is the 6.5M-row asyncpg
  migration, so run `migrate.sh` on the box, never across the internet.
- **2026-07-03.** DSI team disbanded and their Box was lost; the PII stage was
  rebuilt on Presidio. Live demo submissions use a sibling `live_grievances` table,
  keeping the historical `complaints` schema faithful to the dump.
- **2026-07-08.** Learn category→department from history, not the master tables
  (`intCategoryGrp` is NULL on all 62 categories, so name-matching covers almost
  nothing).
- **2026-07-10.** The demo ships on `fallback` routing; the empirical crosswalk and
  the learned scorer are deferred so the demo never blocks on routing modeling.
- **2026-07-22.** Part III restructured evaluation-first. Minimal modularity (fixed
  stage order, swappable models only). MLflow as a control-plane with a release
  manifest, not a runtime dependency. Feedback governed and shadow-first, not
  autonomous online learning. Jurisdiction pack as the DPI-export throughline.
- **2026-07-23.** Analytics re-scoped for ambition and split into structured
  (semantic layer, NL query, spikes, key-driver, case-mix-adjusted comparisons) and
  semantic/unstructured (retrieval, emergent themes). The structured track is
  language-agnostic, so it decoupled from the Odia work. The blanket "never rank
  offices" rule became an adjusted-analytics requirement. Analytics ships as trusted
  increments so the query agent is off the critical path, with explicit disclosure
  controls.
- **2026-07-27.** Demo re-scoped by the Executive Director to five components
  (§1.1). Old Phases 13-19 renumbered to 18-24 to put the list in execution order and
  remove the overlap between demo scope and Part III.
  - **The no-egress invariant is retired and replaced** (§3.1). Sarvam is authorized
    by the Government of Odisha for this data including PII, so the absolute rule
    becomes a declared, audited, revocable channel with a kill switch. An allowlist
    permitting Sarvam and nothing else is stricter than today's policy-only posture.
    "On the box" was never a coherent boundary anyway: documents already live in S3
    and the GPU box is a second machine. Hence three trust tiers, not two.
  - **Sarvam is a provider with two backends, not an external dependency.**
    Sarvam-30B and 105B are Apache 2.0, so self-hosting on the GPU box is the
    standing exit ramp behind the same registry entry. Egress stays reversible.
  - **The lake is not PII-free**, and claiming otherwise contradicted our own dedup
    design, which keys off `petitioner_mobile` / `petitioner_email` read from the
    lake. §3.2 states what actually holds PII and what the real guarantee is.
  - **Spam and duplicates are separate problems, and campaigns are neither.** Dedup
    runs on redacted text, after `pii_tagger` and before the expensive stages. Spam
    never auto-rejects. Placeholders are stripped before hashing, since uniform
    `[PHONE]` / `[NAME]` tokens inflate similarity between unrelated documents.
  - **Offline accuracy is not impact evidence.** Phase 16 builds assignment, exposure
    and shadow instrumentation plus an office-level staggered rollout with a locked
    analysis plan. The demo ships a power calculation and a retrospective agreement
    study, labelled suggestive rather than causal, because the models were trained on
    the same human labels they are compared against. This is program evaluation, not
    human-subjects research: no IRB applies. "No arm may leave a citizen worse off"
    is backed by mechanisms (retained service path, monitored harm indicators, named
    escalation owner, predetermined pause conditions), not by intent.
- **2026-07-27 (intelligence layer).** Phase 15 grounded on what the existing portal
  dashboards structurally cannot do, since they are SQL over `complaints` and
  `action_history`.
  - **Value-add test:** could a competent analyst with SQL access build this in a
    day? If yes it is not our contribution. Exact-match string lookup over a text
    field counts as yes, so a metric can depend on a column that does not exist and
    still be only a view definition.
  - **Insight vs capability must be labelled explicitly** (§5.3). Both ship. An
    insight hands over as a SQL view and does not justify a pipeline.
  - **The base layer is three derived tables, not three charts** (per-complaint
    record, cluster id, action type). Management views are `GROUP BY`s over them, and
    they can feed the existing dashboard rather than replace it.
  - **Semantic work moved forward, narrowly**, as Phase 15 S4: the existing
    fine-tuned categorizer encoder, subject lines only, clustered within category.
    Fit once and assign forward so cluster ids stay stable; the residual bucket is
    the novelty detector. Seasonal baselines are same-period-last-year.
  - **Dedup and clustering stay separate operations.** Dedup is lexical and collapses
    counts; clustering is semantic and only adds a column, so dedup carries the
    higher evidence bar. Merging on semantic similarity would fold two pothole
    complaints from different villages into one and undercount real citizen demand
    while reporting it as efficiency. Independently, the encoder is fine-tuned on
    `category` and so collapses exactly the within-category variation dedup needs.
    Cross-script duplicates are handled by transliterating before hashing.
  - **"Discarded" is not a usable spam label, but its reasons are.** The bare
    disposition conflates at least six things and repeats the routing OVB trap. The
    reasons are written into the remarks at volume (~161,000 in the top 100
    templates), giving eight families of which only two resemble spam. "Not within
    purview" is a routing failure and must never be scored as junk.
  - **Two metrics excluded as meta-analytical.** Citizen-vs-model category
    disagreement and PII exposure rate audit our own models rather than the grievance
    process. Both retained as internal diagnostics. The Phase 13 privacy scorecard is
    a distinct artifact and is unaffected.
  - **The closure metric splits in two.** "Share of closures recording no action" is
    an insight: 86.65% of resolved complaints close on a templated remark, so it is a
    string match for seven cases in eight and ships as a view definition. It is
    descriptive only, because sometimes no action is the correct outcome and a
    correct closure is identical in the record to a premature one. The capability is
    the disambiguator: **does the citizen return?** Reopening is a column, but
    refiling needs same-issue matching, so it depends on Phase 14. Report it as a
    lower bound, since non-return conflates satisfaction with giving up. Condition on
    trajectory (step count, elapsed time) and validate by variance decomposition
    against case mix rather than by ranking offices. Calibrating the 61% properly
    needs 300-500 hand-adjudicated closures, which is post-August.
  - **Political rather than technical risk** on this metric: it is reported at state
    level as a closure-workflow observation, never as an office league table, and
    goes to the ED first.
  - **Bare spike detection is not a capability.** EWMA over `(category × district ×
    week)` counts is a day of analyst work. The three-count decomposition is a
    capability because two of the counts need dedup. Ship the decomposition, not the
    alert.
  - **No capability hides in the free-text tail.** The bespoke-vs-template gap is
    confounded by case length and does not survive stratification by step count.
  - **The ~34,700 officer-confirmed duplicates are a baseline, not a deliverable.**
    They are what the manual process already catches. The claim is the increment
    MinHash finds beyond them, which this makes measurable rather than asserted.
  - **Natural-language querying specced properly in Phase 20, and still not August
    work.** Semantic parsing into the semantic layer's schema (intent classification
    plus slot tagging, both architectures already in this repo), never text-to-SQL
    and never embedding retrieval over canned queries. Guided dropdown builder leads
    rather than acting as a fallback, because it generates the training distribution.
    Training data comes from inverting the problem: sample a legal query form, render
    it to a sentence, labels are correct by construction. One consequence lands in
    August: S1's definitions must be written as a standalone query target.
  - **The lake holds raw citizen prose, not only structured contact fields.**
    `materialize.py` copies `SELECT *`, so `complaints.grievance` is in Parquet
    verbatim and never passed through `pii_tagger`. §3.2 rewritten.
  - **The remedy is a scheduled step, not a stated principle.**
    `janasunani-redact-grievance` is step 0 of Phase 14: it runs the existing
    Presidio analyzer over the `grievance` column for the chosen slice and writes
    `grievance_redacted`, which is the only thing MinHash and S4 read. It gates both
    backfills, so the ED's choice of backlog slice is a technical dependency and not
    just a presentational one. Redaction does not declassify what is derived: the
    signatures and vectors are `dpic-infra` artifacts under Phase 18's access and
    audit rules, and neither phase may call them downstream-safe by construction.
  - **Cross-script duplicate matching is out of August.** Transliteration belongs to
    Phase 17, which runs after Phases 14 and 15, so August ships script-specific
    matching with cross-script recall reported as unsupported. It becomes a re-index
    once Phase 17 lands, not a redesign.
  - **S4 is a new corpus-scale batch job, not a free forward pass.** The 1.37M
    historical records have never been through the categorizer. Bounded to one
    category and time window for August, precomputed.
  - **Benchmark table restated as historical reference plus current measurement.**
    Only PII, OCR/Sarvam Vision and duplicate recall have labelled sets for August.
    Page type, categorization and summarization are reported as the earlier team's
    numbers, not re-measured, because no evidence-production step exists for them.
  - **The two Sarvam comparisons have different gates, and conflating them hid a
    risk.** The 85-page PII set gates the privacy comparison. The document-reading
    comparison is gated by the hand-transcribed sample, which has no owner. Naming
    them separately is what makes the unassigned one visible.
  - **The operating model is one accountable engineer with frontier-model agents,
    stated explicitly.** Agents parallelize authoring, testing, analysis and review.
    They do not parallelize integration, validation against real data, or acceptance
    of PII-, security- and deployment-sensitive changes, all of which stay with the
    engineer. §5.6 says so rather than leaving "plus subagents" to be read either way.
  - **Net: everything capability-side routes through Phase 14.** Dedup is not one
    deliverable among several, it is the dependency under the duplicate-adjusted
    workload, the spike decomposition and the return-rate metric. If it slips, the
    intelligence layer degrades to a SQL view.
