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
> **Re-scoped 2026-07-27** to the Executive Director's five demo components.
> §1.1 maps them, §5 details them, §8 records the decisions. Phases 13–19 were
> renumbered to 18–24; see the crosswalk in §2.

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
| c | Intelligence layer | 15 | Metrics layer, supervisor dashboards, deterministic spike detection | Ships working |
| d | A/B testing of AI automation | 16 | Assignment / exposure / shadow instrumentation, a locked analysis plan, a power calculation, retrospective evidence | Ships as framework + evidence |
| e | Sarvam benchmark | 17 | Head-to-head scorecard against our local models on Odia, romanized Odia, English. Provider-switchable via the registry | Ships as benchmark + switch |

Three ship as working surfaces (a, b, c). Two ship as a framework plus measured
evidence (d, e). Both of those make claims that need data we do not have yet, and
neither is honest as a live feature on the demo date.

**The August cut is narrower than the full phase scope below.** The demo is
**14 August 2026**, built by **one engineer**, so §5 describes each phase in full
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

Retitled and re-scoped 2026-07-27 to match this document.

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

### 3.1 The trust boundary (rewritten 2026-07-27)

The old invariant was **citizen text never leaves the box**. Two things broke it.

- The Government of Odisha authorized Sarvam for this data, including PII.
- More basically, **"the box" was never the real boundary.** Scanned documents
  already live in S3. The GPU box is a second machine. A self-hosted Sarvam model
  on the GPU box is not "on-box" in any literal sense, and calling it that (as an
  earlier draft did) hides a real network hop behind a reassuring word.

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

An earlier draft claimed "no un-redacted PII reaches the lake". That is **false as
written**, and the Phase 14 dedup design proves it: the index keys off
`petitioner_mobile` and `petitioner_email`, which it reads from the lake.

The precise position:

| Data | Contains PII? | Notes |
|---|---|---|
| `complaints` structured columns, OLTP **and** lake | **Yes** | `petitioner_name`, `petitioner_mobile`, `petitioner_email`, `address`. Faithful to the dump, by design |
| `pages.extracted_text` (raw OCR) | **Yes** | Never leaves the pipeline artifact DB |
| `pages.redacted_text` | No, by construction | Typed tokens. This is what the exporter carries onward |
| Parquet lake, text fields | No | Only redacted text is exported |
| Dedup index | **Yes, derived** | Salted hashes of contact fields. A hash of a mobile number is still personal data under DPDP |
| API responses, summaries, analytics | No | Built only from redacted text |

The guarantee we can actually make: **no un-redacted grievance or page text
reaches any downstream output.** The structured contact columns are a different
matter. They are personal data, they are in the lake, and the control on them is
access, not redaction. Phase 18 owns the role and audit rules for reading them.

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
  browser E2E (issue #30). Three hardening items before Phase 12 is done: a
  **CPU-only Torch** API image (issue #48; the current 8–12 GB image bundles CUDA
  Torch though the box is CPU-only), and two rollout gaps (issue #32): the workflow
  can time out and kill the SSH session before the box script finishes its
  health-wait/rollback, and a rollback run can ship the current compose/proxy
  beside an old image SHA.

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
- Asserts row counts at each hop, and that no un-redacted PII reaches the lake or
  any API response.
- A **non-PII synthetic variant** that runs in CI. This doubles as the readiness
  canary: it proves real history is queryable, the DB result store is selected,
  routing mappings loaded rather than silently `fallback`, and submit→persist→fetch
  succeeds.

**Exit criteria.** Per-entity, per-language PII scorecard published. E2E green on
real data and synthetic in CI. DSI baselines reproduced or the gap explained.

### 5.2 Phase 14 — Spam & duplicate detection (component b)

Two different problems. Conflating them is the main design risk.

**Spam / non-actionable.** Content that is not a workable grievance: test entries,
abuse, blank or garbage OCR, out-of-jurisdiction requests.

- Weak labels are minable: rejected / invalid dispositions in `status` and
  `complaint_status_id`, plus `action_history.action_taken_remark`.
- Those labels are **not ground truth**. A rejection conflates spam with "resolved
  elsewhere" and with officer discretion, and inherits whatever bias the old
  process had. Audit a gold sample before training on them.
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
3. **Semantic duplicate.** Same issue, different words. Needs embeddings, gates on
   Phase 22, out of demo scope. Say so rather than implying the demo covers it.

**Technique for 1 and 2.**

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

**Two placement decisions that matter.**

- The stage runs **after `pii_tagger`, on redacted text**, so the
  redaction-first safety edge stays intact. Note this is a trade, not a free win:
  see the placeholder caveat above.
- It runs **before `summarizer` and `categorizer`** and gates them, the way
  page-type already gates the summarizer. Obvious spam and exact duplicates should
  not consume the expensive stages.

New stage order:
`format_classifier → ocr_extraction → pii_tagger → spam_duplicate →
page_type_classifier → summarizer → categorizer`.

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

**Corpus study.** Run the detector over the 1.37M history; report prevalence by
district, category, mode, year. What share of officer load is duplicate handling is
the number that makes the case. It feeds Phase 15 and is a candidate outcome for
Phase 16.

**UI.** A triage banner on the result screen: "possible duplicate of ticket NNN"
with a link, "part of a campaign, N related filings", "flagged low-signal, review".
All advisory, none blocking.

### 5.3 Phase 15 — Structured analytics I (component c)

A governed analytics surface over the corpus. It reads only **structured lake
fields** (category, subcategory, district, dates, disposal times from
`action_history`), so it does not depend on text-language processing and can run in
parallel with the rest of the demo work.

"Does not read text" is not "clean". The structured fields still carry missingness,
historic policy choices, and language-related classification error. Profile and
reconcile them before exposing any comparison.

- **S1, metric definitions + freshness + dashboards.** The **semantic layer**: a
  thin governed definition of allowed dimensions and measures over the lake, a
  small YAML compiled to DuckDB SQL (dbt-Semantic-Layer / Cube / Malloy lineage),
  tested metric by metric, with data-quality and lake-freshness reporting, feeding
  fixed supervisor dashboards. This is the contract everything else builds on:
  downstream queries reference only defined fields, and policy constraints (RBAC
  scope, redaction, small-cell suppression) are enforced in the layer, not left to
  model discretion.
- **S2, deterministic spikes + contribution analysis.** Per
  `(category × district × week)` counts, flagged by EWMA / STL residual / Poisson
  surprise ("water complaints in District X up 300% this week"), with key-driver
  analysis decomposing a spike by dimension. No model. The cheapest slice.

Phase 14 adds inputs, and forces a decision about what a count means.

- Input: spam and duplicate prevalence as governed measures.
- **A campaign is not a false spike.** An earlier draft said spike detection must
  run on de-duplicated counts, which is wrong: 500 citizens filing about the same
  thing is a real and important signal, and collapsing them to 1 destroys it.
- The fix is to stop pretending one number answers the question. The metrics layer
  defines **three** counts, and every spike view carries all three:

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
human-subjects research, and no ethics review applies. Settled; do not re-open it.
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

Tiers: Starter is pay-as-you-go at 60 req/min, Pro ₹10,000/month at 200 req/min,
Business ₹50,000/month at 1,000 req/min.

**Language coverage is not the constraint.** Sarvam Vision lists all 22 scheduled
languages plus English, Odia among them explicitly, which is more than our current
OCR can honestly claim.

**Cost is not the constraint either.** A 500-page benchmark is **₹250**. Running
Sarvam-30B over all 1.37M grievance subjects is roughly 275M input tokens, on the
order of **₹700**. These numbers are small enough that cost should not shape the
benchmark design; latency, rate limits, and quality should.

Four things to check before relying on any of it:

- ⚠️ **Handwriting is not mentioned anywhere in the Vision documentation.** Tables,
  layout, and printed Indic script are the advertised strengths. A large share of
  our corpus is handwritten grievance letters, which is exactly the hardest case
  and the one we most need solved. **Put handwritten pages in the benchmark
  sample deliberately, stratified, and report them separately.** If Sarvam Vision
  only wins on printed forms, that is a much smaller result than the headline
  suggests.
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

## 6. Part III: post-demo maturity (Phases 18–24)

After the demo, the system matures toward two goals: Odia (native and romanized) as
a genuine first-class citizen, and the corpus turned into governance intelligence an
official can query on demand, while becoming portable enough to interest other
governments.

Part III is a set of **gated investments, not an implementation commitment**. See
the gates at the end.

### Standing decisions

Dated 2026-07-22, amended 2026-07-27.

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
- **Natural-language querying, last.** An agent loop over the semantic layer: plan,
  emit semantic-layer-constrained SQL, run on DuckDB, inspect, self-correct, narrate
  with a chart. Raw text-to-SQL is still unreliable on real schemas (BIRD /
  Spider 2.0 execution accuracy sits well below human), so generation uses
  **structured/constrained decoding** and a **local** model, capacity-gated on the
  CPU box. If it is not reliable enough it degrades to guided/templated queries or
  on-demand GPU. A hypothesis to benchmark and promote only if it wins.

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

Retrieval and emergent-theme discovery over grievance **text**. Unlike Phase 15 this
reads citizen text, so it gates on Phase 21 normalization (embedding raw romanized
or out-of-distribution text is what sank the earlier BERTopic attempt) and on a
capacity benchmark before any index is committed.

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
  (§1.1). Old Phases 13–19 renumbered to 18–24 to put the list in execution order
  and remove the overlap between demo scope and Part III. Five consequences:
  - **The no-egress invariant is retired and replaced** (§3.1). Sarvam is authorized
    by the Government of Odisha for this data including PII, so the absolute rule
    becomes a declared, audited, revocable channel with a kill switch. Enforcement
    improves: an allowlist permitting Sarvam and nothing else is stricter than
    today's policy-only posture.
  - **Sarvam is a provider with two backends, not an external dependency.**
    Sarvam-30B and 105B are Apache 2.0, so self-hosting on the GPU box is the
    standing exit ramp behind the same registry entry. Egress stays reversible.
  - **Spam and duplicates are separate problems, and campaigns are neither.**
    Duplicate detection runs on redacted text, after `pii_tagger` and before the
    expensive stages, keeping the redaction-first edge intact. Spam never
    auto-rejects. Spike detection must run on de-duplicated counts.
  - **Offline accuracy is not impact evidence.** Phase 16 builds assignment,
    exposure, and shadow instrumentation and an office-level staggered rollout with a
    locked analysis plan. The demo ships a power calculation and a retrospective
    agreement study, labelled suggestive rather than causal, because the models were
    trained on the same human labels they are compared against. This is a program
    evaluation, not human-subjects research: no IRB applies.
  - **Correction to the record.** Earlier versions said the MLflow slim registry was
    "on branch `feat/mlflow-slim-registry`". It merged to `main` in PR #20 on
    2026-07-08 and the branch was deleted. The helpers exist and are tested; nothing
    calls them.
- **2026-07-27 (Codex CTO review, second pass).** Five corrections to the re-scope
  draft, four of them factual errors rather than refinements:
  - **"On the box" was never the boundary.** Documents already live in S3 and the
    GPU box is a second machine, so classifying self-hosted Sarvam as `on-box` was
    wrong. Replaced by three trust tiers plus per-route declarations (§3.1).
  - **The lake is not PII-free.** The claim contradicted our own dedup design,
    which keys off `petitioner_mobile` / `petitioner_email` read from the lake.
    §3.2 now states exactly what holds PII and what the real guarantee is.
  - **A campaign is not a false spike.** Requiring de-duplicated counts would have
    destroyed the signal we said we wanted to surface. Replaced by three counts
    (filings, clusters, signatories) with spikes labelled, not suppressed.
  - **Redacted text is not strictly better for near-duplicate matching.** Uniform
    `[PHONE]` / `[NAME]` placeholders inflate similarity between unrelated
    documents. Placeholders are stripped or down-weighted before hashing.
  - **"No IRB applies" is a determination, not an assumption**, and "no arm may
    leave a citizen worse off" needed mechanisms (retained service path, monitored
    harm indicators, named escalation owner, predetermined pause conditions).
