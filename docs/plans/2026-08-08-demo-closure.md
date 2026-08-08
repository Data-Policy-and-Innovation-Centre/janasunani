# Demo-closure plan — 14 August 2026 (Phases 13, 14, 15, 17)

*Date: 2026-08-08 (T-6 days) · Branch: `main` @ `0ba59a8` · Owner: one accountable engineer + agents*
*Source of truth: [ROADMAP.md](../ROADMAP.md) §2 (phase table) + [DELIVERY.md](../DELIVERY.md) Tables 1–3 · Architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)*

## Goal

Close the **six** demo-blocking components so the 14 August rehearsal has a committable artifact for every row of DELIVERY Table 1 **plus** the two you just promoted:

* **13 Pipeline completion (a, Committed)** — end-to-end `document in → routed grievance out` + per-entity PII scorecard
* **14 Spam & duplicates (b, Bounded)** — duplicate linking + campaign grouping + corpus prevalence over a named backlog slice, **plus a bounded `spam_score` / `spam_reason` scorer** (no longer advisory-only)
* **9 Routing (re-promoted to Bounded for the demo)** — from `method:"fallback"` to **empirical crosswalk + mappings + fallback** with confidence and evidence, built off the 1.37M history
* **15 Intelligence layer (c, Bounded)** — duplicate-adjusted workload + spike-with-cause + themes + closure finding, via supervisor dashboards
* **17 Sarvam benchmark (e, Bounded)** — paired Vision-vs-pytesseract divergence and categorizer accuracy scorecards, provider-switchable

Phase 16 (A/B framework) remains Framework-only and out of scope. Routing and spam are **now demo-committed (Bounded)**: their tracker issues now carry the `demo` label (#64, #33, #50, #74 — labeled 08 Aug, see Unit 0), and the demo ships with `method:"learned"` and `spam_score` visible, not with the old fallback/abstention placeholders. The slice is not future work — it was pre-committed on #64 and closed 07 Aug as **Sambalpur/2024**.

## Success Criteria

Demo is done when each row below can be shown live and its artifact reconciles against source data. Thresholds are release gates, not aspirations.

| Component | Must be true on 13 Aug (freeze) | Gate / how verified |
|---|---|---|
| **13 PII** | Presidio stage scored on the 89-page / 50-doc gold set; per-entity recall published (mobile/Aadhaar/email vs names) + corpus scan over the demo slice reporting zero cleartext shaped PII | `janasunani-evaluate-pii --gold <gold.jsonl>` coverage ≥0.8056 historically; current measured headline ~0.496 with name-heavy caveat per DELIVERY Table 2 — report per-class, not just headline. Corpus scan: `SELECT count` over `data/interim/complaints.parquet` redacted_text via `janasunani/evaluation/pii_scorecard.py` helpers |
| **13 E2E** | One scanned grievance processed through **7** stages in one pass (format→OCR→PII→**spam/signal**→page-type→summarize→categorize→**route via crosswalk**) with counts reconciling at every stage | `janasunani-pipeline run` over `data/raw/documents-sample` or the named slice; stage-row counts in artifact DB match exporter upserts into OLTP; `GET /grievance/{id}` returns `{redacted_text, summary, category, spam_review: {spam_score, reason}, routing: {method:"learned"|"rules"|"fallback", confidence, empirical_evidence}}` |
| **14 Dedup** | MinHash/LSH index **already built on the box 07 Aug 14:14** over `Sambalpur/2024` from OLTP `complaints` + `grievance_redactions` — **55,544 of 55,544 indexed, 10,963 duplicate groups**, deterministic digest, provenance `source='complaints+grievance_redactions'`; recall to be measured against 34k officer-confirmed duplicates (held-out) | Verify: `SELECT count(*) FROM dedup_signatures` → 55,544, `dedup_groups` → 10,963; digest asserted on every downstream join (#137); held-out recall reported (no threshold — report prevalence + recall) |
| **14 Spam** | **Bounded `spam_score` / `spam_reason`** for `Sambalpur/2024`: rule/lookup scorer over **redacted** text (repetition-collapse + length + template-family filter, never on raw PII), never scoring `not within purview` as spam; corpus prevalence reported, live triage returns a numeric score instead of unconditional abstention | `janasunani-spam-score --slice Sambalpur/2024` (Unit 2b) writes `spam_scores` sidecar; `GET /triage/preview` and live `TriageResult.spam.{score,reason_code,evidence}` are scored; held-out PPV / false-positive by language+mode reported (no blocking yet) |
| **9 Routing** | **Empirical crosswalk built and live**: `category→dept` and `category+district→dept` lookup from 83.1% history with confidence, backing `method:"learned"` with `empirical_evidence`; falls through to `MappingRouter` (rules) then `fallback`; artifact committed | `janasunani/routing/crosswalk.py:build_crosswalk` → `janasunani/routing/reference/routing_crosswalk.json` committed; `MappingRouter(enable_crosswalk=True)` in `serving/`+`inference/` wiring; `GET /grievance/{id}.routing.method != "mock"`; `tests/test_routing_crosswalk.py` green |
| **14 Slice** | **Frozen 07 Aug via #64** — `Sambalpur 2024, 55,544 complaints with grievance text` (pre-committed default, highest-volume district×year; no ED override). Every downstream job reads this constant | Recorded in `config.py` + `DELIVERY.md` by Unit 0 recording commit; issues **#64, #33, #50, #74 now carry `demo`** (labeled 08 Aug) — not future work |
| **15 Intelligence** | Supervisor API returns three panels from aggregate artifacts only (never lake): closure ladder, workload (filings / distinct problems / distinct citizens), spike-with-cause for one real spike | `GET /supervisor/dashboard` returns `SupervisorDashboard` with `closurePanel` + `workloadPanel` + `spikePanel`; each panel validates schema/arithmetic and surfaces `Unavailable*` rather than a plausible substitute when stale (`janasunani/serving/intelligence.py`) |
| **15 Themes** | One category's local-issue themes identified (concentrated + rising) — ships working; fallback is closure-only if dedup slips (per DELIVERY) | Findings CLI writes `outputs/findings/<theme>.csv` + Markdown fragment; grouping reads only redacted lake text |
| **17 Sarvam** | Paired scorecard Vision vs pipeline on same few-hundred pages, handwritten/printed split, categorized-vs-OCR reported separately; categorizer scored against recorded `grievance_category`; egress is single-module, tier-declared, audit-logged | `janasunani/evaluation/sarvam_scorecard.py` harness; every `authorized-external` call via `janasunani/egress/` with `trust_tier=authorized-external`, authorization ref, tiered fallback; `tests/test_egress_boundary.py` green |

## Context And Current Facts

* **Phase table (ROADMAP §2, 2026-08-08, updated 07 Aug):** 0–5 ✅, 6 🔄, 7 ✅, 8 ✅, 9 🔄 (crosswalk code built, wiring pending — now `demo` via #33), 10 ✅, 11 🔄, 12 🔄, **13 🔄 (gold set underway #15), 14 🔄→partial ✅ (Sambalpur/2024 55,544/55,544 indexed, 10,963 groups via #71 on 07 Aug 14:14 — recall + spam scorer + prevalence still to do), 15 🔄 (metric-registry slice done; cluster/citizen metrics unblocked — dedup artifact now exists, lake analytic still deferred), 16 ⬜, 17 ⬜**. Slice: **Sambalpur/2024 via #64 (closed 07 Aug, pre-committed highest-volume district×year)**. Verified corpus: 1,371,288 complaints / 6,556,171 action_history.
* **What is already built (as of 07 Aug 14:14):** 6 pipeline stages, Presidio rebuild, dedup primitives + **completed backfill over Sambalpur/2024** (`pipeline/dedup_index.py` — 55,544 signatures, 10,963 groups, `comparison_pairs=16138623`, `large_buckets=310`, provenance `complaints+grievance_redactions` + digest), serving API (`serving/intelligence.py` aggregate-only, `serving/triage.py` advisory shape — spam wiring pending), OLTP→lake `materialize` via DuckDB, analytics marts, findings CLIs, evaluation harnesses.
* **What is not (remaining for 08–13 Aug):** spam numeric scorer (Unit 2b — promoted to `demo` via #74), routing crosswalk artifact + wiring to `method:"learned"` (Unit 7 — `enable_crosswalk=False` → `True` + commit `routing_crosswalk.json`), no lake-based duplicate analytic (intentionally deferred — OLTP `dedup_groups` is the demo source, lake analytic stays post-demo per #137), no PII scorecard published on gold, no Sarvam paired run, no hand-transcribed OCR ground truth (fallback stands), demo not yet live on AWS (Phase 12, `deploy/` + #30).
* **Hard constraints from the repo:** `data/` is real PII — AGENTS.md forbids listing/reading/searching it; lake reads never go through OLTP; `janasunani/egress` is the only allowed `authorized-external` caller (CI-enforced); extras conflict (`pipeline-core` `transformers>=4.57` vs `ocr-deepseek==4.46.3` vs `pii` numpy floor) so stages import lazily and run per-extra.

## Constraints And Non-goals

* **Non-goals for 14 Aug (still out of scope, even after this promotion):** learned office-level scorer and outcome-optimised routing (incidence-ranked crosswalk is the demo ceiling — the disposal-time / benefit optimisation stays Part III), semantic dedup (different-words same-issue), NL query, adjusted office comparisons, Odia model replacement (follows benchmark), self-hosted Sarvam-105B bring-up, production handover. **What leaves the non-goal list:** `method:"learned"` via the empirical crosswalk now ships (Phase 9), and a bounded `spam_score` now ships (Phase 14 spam half) — both as Bounded, not as ML models, with no auto-rejection.
* **Time & people:** 6 days to freeze (13 Aug, Friday rehearsal — no code), one accountable engineer. Long jobs (redaction, dedup index, theme grouping over 1.37M rows) must be scheduled overnight, in order, and left running — not squeezed into final days.
* **Data & safety invariants (do not relax):** every citizen-data route declares `trust_tier` (`same-host` / `dpic-infra` / `authorized-external`) and an authorization record; `authorized-external` kill switch to a lower-tier fallback; dedup signatures salted (`DEDUP_SALT`) and treated as DPDP personal data; findings never emit row-level prose.
* **Dependency split is load-bearing:** do not attempt a single `uv sync --all-extras` env with both transformers pins. Each CLI invocation uses exactly one extra against the same artifact DB.

## Key Decisions

| # | Decision | Chosen | Rejected alternative | Why |
|---|---|---|---|---|
| 1 | Slice definition | **Frozen 07 Aug via #64: Sambalpur/2024, 55,544 complaints (highest-volume district×year, no ED override)** — Unit 0 is now a recording commit, not a decision | Keep slice vague until final week | Slice was pre-committed precisely so the 07 Aug redaction → dedup chain did not stall on a meeting. The box already indexed this slice (55,544/55,544, 10,963 groups at 14:14). Re-choosing now would invalidate that artifact and the digest guard (#137). |
| 2 | Dedup source | **OLTP `complaints` + `grievance_redactions` with digest — already built 07 Aug 14:14 (55,544 signatures, 10,963 groups, Sambalpur/2024)**. Remaining work is verification + recall measurement, not a rebuild | Wait for lake-based duplicate analytic or rebuild the index | Rebuilding would waste the box's 07 Aug run (16,138,623 comparisons) and risk a second snapshot racing the first. Lake analytic stays post-demo; Unit 4b joins on the box's `dedup_groups` with digest assertion (#137). |
| 3 | Lake duplicate marts | **Defer the lake `duplicate_*` marts/views to post-demo** | Block intelligence on lake marts | ROADMAP §15 already says cluster/citizen metrics remain unavailable pending dedup lake artifacts — deferring is the documented fallback, not a slip. |
| 4 | Spam scoring | **Bounded `spam_score` / `spam_reason` now ships (promoted): rule lookup over redacted text only, decomposing the 8 discard families so only the 2 spam-like ones (`details inadequate` 39,943 + `no specific grievance` 16,340) score, with `not within purview` 8,455 explicitly excluded as routing failure, never junk; reports prevalence + PPV/false-positive, no auto-rejection** | Keep advisory-only (no score, no numeric field) | Advisory-only would hide the only bounded spam signal achievable in 2 days and would keep `spam_score` invisible on demo. The full 7th `spam_duplicate` pipeline stage that gates summarizer/categorizer remains deferred — this bounded scorer is a sidecar + `serving/triage.py` wiring, not a stage gate. Campaigns (`duplicate` families 19,904+14,767) are dedup, never spam, so they score separately. |
| 4b | Spam surface vs dedup | **Spam and campaigns are scored orthogonally: campaigns are dedup groups (`duplicate_kind=campaign`), never `spam_score`; duplicates by resubmission are dedup, not spam** | Score campaign text as high spam | Would suppress the collective-grievance signal government most needs — ROADMAP §5.2 calls this the main design risk. |
| 5 | Routing (re-promoted) | **Empirical crosswalk `method:"learned"` ships Bounded**: `category→dept` 60.9% and `category+district→dept` 72.8% from lake, lifecycle is `build_crosswalk` → commit `routing_crosswalk.json` → `MappingRouter(enable_crosswalk=True)` in `inference/service.py` + `serving/` wiring; `subcategory` rungs built but not live (live classifier emits no subcategory) | Keep demo on `method:"fallback"` | Fallback was the pre-promotion position and would leave routing as the only demo component that ships manufactured data. Crosswalk is already implemented (`crosswalk.py`, `tests/test_routing_crosswalk.py`), incidence-ranked, with confidence (support+share), and degrades to mappings→fallback — it is the only Bounded routing achievable in <1 day. Learned scorer and office-level optimisation remain deferred (Part III). |
| 6 | Intelligence serving | **Keep `serving/intelligence.py` aggregate-only** (reads small published CSVs from `DATA_DIR/aggregates/`, validates schema/arithmetic, returns `Unavailable*` panels when stale) | Let supervisor API query the lake/Dedup index directly | Querying the lake from the serving path re-introduces the freshness gap as a correctness bug and violates the deliberate lake/OLTP separation. Aggregate-only makes missing/stale capability visible. |
| 7 | Sarvam benchmark | **Paired divergence for OCR (no ground-truth verdict) + accuracy for categorizer (against recorded category) + transliteration check; split handwritten/printed** | Commission hand transcription now to get OCR accuracy | No owner was named and the fallback was already taken 07 Aug (DELIVERY §Schedule). Divergence + categorizer accuracy still yields a decision-relevant routing signal and is the only path that fits in 3 days at few-hundred-page cost. |
| 8 | Egress | **Single module `janasunani/egress/sarvam.py` owns every `authorized-external` call; registry declares tier, destination, approval ref, retention, encryption, fallback; every call audit-logged (ticket, stage, provider, model, bytes)** | Ad-hoc `httpx` calls from evaluation/analytics | The invariant is tier-declared, audited, revocable channels — `tests/test_egress_boundary.py` enforces the one-module rule. Required for DPDP and for the kill-switch rehearsal. |

## Recommended Approach

Run **slice-first, then fan-out CPU work, then sequential overnight jobs, then benchmark last** — this respects the only true ordering constraint (slice → dedup → intelligence) while keeping the engineer free during long jobs.

```
08 Aug  EOD  freeze slice + label demo issues (Unit 0) ─────────┐
                                                         │
09 Aug  daytime  PII (Unit 1)  ∥  Dedup (Unit 2)  ∥  E2E wire-up (Unit 3)  ∥  Routing crosswalk build (Unit 7)
09 Aug  overnight dedup overnight  ∥  Marts/closure (Unit 4a)  ∥  Spam scorer (Unit 2b) scoring over slice
                                                         │
10 Aug  daytime  materialize slice lake (if needed)        │
10-11 Aug        Workload/spike (Unit 4b)  ∥  Sarvam paired run (Unit 5)
11-12 Aug        Spam PPV validation + hook into triage    │
13 Aug  freeze   E2E 7-stage rehearsal + routing + spam banner + dashboards + benchmark table (Unit 6)
14 Aug  demo     no code
```

If dedup slips, intelligence falls back to **closure finding only** (DELIVERY Bounded fallback — no new processing needed). If Sarvam slips, demo still has categorizer accuracy + divergence methodology with costed estimate. If spam scorer slips, demo keeps the old `abstained` triage banner — the E2E path is unaffected because the 7th stage gates nothing yet. If routing crosswalk slips, demo falls back to `method:"rules"` → `fallback` (the pre-promotion path) — also non-blocking.

### What is parallel and what is serial

Only one thing is truly serial — everything else is parallel by default. The plan makes that explicit so agents are not blocked waiting for each other.

| Must be serial (one after the other) | Why |
|---|---|
| **Slice freeze (Unit 0) → everything else** | Every downstream job — dedup, spam, lake materialize, routing artifact build, themes, Sarvam sample — hashes the same `slice_id` + input-snapshot digest. Running any of them before the slice commit is a mixed-snapshot bug (#137) that the digest guard will fail loudly. This is the single blocking gate on 08 Aug. |
| **Dedup index persisted (Unit 2) → workload/spike panels that need dedup (part of Unit 4)** | `workload = filings − dedup_groups` and `spike distinct_problems / distinct_citizens` join on `dedup_groups.digest`. Starting those joins before the index exists produces empty panels — correct but useless. The `closure` panel and `action_type` mart have no dedup dependency and are not serial here (see parallel column). |
| **Slice redacted_text ready (Unit 1) → Sarvam Vision sample (Unit 5)** | Vision vs pytesseract divergence runs on the same redacted pages. The sample list is drawn from the slice; sampling before the slice exists reseats the paired comparison. |
| **Slice frozen (Unit 0) → spam scorer (Unit 2b)** | Spam scorer reads `grievance_redacted` for the slice; scoring before the slice exists is undefined. But spam can start in parallel with dedup once slice exists — see parallel table. |
| **Crosswalk artifact committed (Unit 7) → E2E routing wire-up in Unit 6** | Unit 6 demos `method:"learned"` only if `routing_crosswalk.json` is committed. Crosswalk build itself is independent of dedup/PII, so it can run in parallel on 09 Aug and just needs to merge before the freeze. |
| **All content PRs merged → Demo integration freeze (Unit 6)** | Integration wires what the other units publish. Rehearsal is a freeze, not a feature — nothing merges after 13 Aug 12:00. |

| Can be parallel (run at the same time) | Why it is safe |
|---|---|
| **Unit 1 (PII scorecard) ∥ Unit 2 (dedup) ∥ Unit 3 (E2E skeleton) ∥ Unit 7 (routing crosswalk)** on 09 Aug | File-disjoint: Unit 1 `janasunani/evaluation/`+`pii/`; Unit 2 `janasunani/pipeline/dedup*.py`+`db/models.py` dedup tables; Unit 3 `janasunani/pipeline/pipeline.py`+`export.py`+`janasunani/inference/`+`serving/processor.py` (skeleton, no crosswalk wiring yet); Unit 7 `janasunani/routing/crosswalk.py`+`reference/routing_crosswalk.json`+`routing/mappings.py`. No shared file, no shared table write. They share only the frozen `slice_id`. |
| **Unit 4a (marts/closure) ∥ Unit 2b (spam scorer) ∥ tail of Unit 2 (dedup overnight)** | 4a touches `analytics/marts.py`+`sql/*`; 2b touches `janasunani/pipeline/spam.py` (new) + `janasunani/serving/triage.py` scoring path + `evaluation/spam_scorecard.py` (new); dedup tail touches `pipeline/dedup_index.py`+`dedup_*` tables. No file overlaps. Spam scorer reads `grievance_redacted` but does not write dedup tables, so it can run while dedup is still indexing. |
| **Unit 5 (Sarvam) ∥ Unit 4b (workload/spike)** on 10–11 Aug | Unit 5 `janasunani/egress/`+`evaluation/sarvam_scorecard.py`; Unit 4b `janasunani/serving/intelligence.py`+`DATA_DIR/aggregates/`. Both read `dedup_groups` but neither writes it. |

In wall-clock terms, the 6-day critical path is **Unit 0 (0.5d) → Unit 2 (1d setup+overnight) → Unit 4b (1d) → Unit 6 (1d) = 3.5 days**, with Units 1, 3, 4a, and 5 overlapped. Without parallelism the same work is 6–7 days and misses the freeze.

### Git worktrees and branch naming

This repo follows [.dpic/standards/agent-conventions.md](../.dpic/standards/agent-conventions.md): every branch-based task runs in a **top-level `.worktrees/` worktree**, branch names are `type/kebab-case`, and no work is committed directly to `main`. The `.gitignore` already contains `.worktrees/` so worktrees never appear as untracked dirs.

Base branch for all units is `main` at the slice-freeze commit. Each unit gets its own worktree and an explicit **do-not-touch** list so parallel agents cannot overwrite each other's files (see Work Plan per-unit).

| Unit | Branch | Worktree path | Base |
|---|---|---|---|
| 0 | `feat/freeze-demo-slice` | `.worktrees/freeze-demo-slice` | `main @ 0ba59a8` |
| 1 | `feat/pii-scorecard-per-entity` | `.worktrees/pii-scorecard-per-entity` | `main` + Unit 0 merge |
| 2 | `feat/dedup-index-slice-build` | `.worktrees/dedup-index-slice-build` | `main` + Unit 0 merge |
| 2b | `feat/spam-signal-scorer` | `.worktrees/spam-signal-scorer` | `main` + Unit 0 merge |
| 3 | `feat/pipeline-e2e-rehearsal` | `.worktrees/pipeline-e2e-rehearsal` | `main` + Unit 0 merge |
| 7 | `feat/routing-crosswalk-live` | `.worktrees/routing-crosswalk-live` | `main` + Unit 0 merge |
| 4a | `feat/intelligence-marts-closure` | `.worktrees/intelligence-marts-closure` | `main` + Unit 0 merge |
| 4b | `feat/intelligence-workload-spike` | `.worktrees/intelligence-workload-spike` | `main` + Units 0,2,4a |
| 5 | `feat/sarvam-paired-scorecard` | `.worktrees/sarvam-paired-scorecard` | `main` + Units 0,1 |
| 6 | `chore/demo-integration-freeze` | `.worktrees/demo-integration-freeze` | `main` + Units 0–5,7,2b |

Creation (canonical):

```bash
git fetch origin
git worktree add .worktrees/freeze-demo-slice -b feat/freeze-demo-slice main
# ... one per unit, same pattern — never `git checkout -b` in the main checkout
```

Each worktree runs its own `uv sync --extra <name>` env (conflicting extras never co-exist) and its own `pytest` invocation. After its PR merges, the worktree is removed (`git worktree remove .worktrees/<name>`) — the branch remains for history. No agent reuses another unit's worktree.

### Parallel-agent dispatch rules

You asked for parallel agents wherever possible — this plan enables that explicitly and stays compliant with the DPIC subagent convention ("do not spawn unless asked; when asked, analyse file disjointness first"):

* **You have now asked**, so agents are allowed for Units 1, 2, 2b, 3, 7, 4a which are file-disjoint (table above). Wave 1 is now **4 agents in parallel** (was 3) + 2b/4a overlapping the dedup tail = **6 parallelizable workstreams** before the cross-unit merge. Units 4b and 5 may also be dispatched in parallel once their serial gate clears. Units 0 and 6 are single-agent (serial gates / freeze).
* Before spawning, each agent is given: its **base commit**, its **branch + worktree path**, the **exact files it owns**, and a **do-not-touch list** naming every file owned by every other in-flight agent (per-unit lists in the Work Plan). Two agents never edit one file — this avoids the "three Sprint 3 collisions" failure mode noted in AGENTS.md. Wave-1 agents (1, 2, 3, 7) are spawned together after Unit 0 merges; 2b and 4a are spawned as soon as the dedup overnight job starts, not after it finishes.
* **Serialize mass rewrites**: a `ruff` or `rclone` sweep that touches every file is never in flight with any other agent. Each unit runs `ruff check` only on its own files.
* **Verification is per-agent, then re-run centrally**: each agent must show its own `pytest` / `ruff` / `dvc dag` green in its worktree; the accountable engineer re-runs the full required-checks matrix on `main` after each merge (see Validation Plan) and does not take agent logs at face value.
* **Agents are file-disjoint but table-aware**: Units 2 and 4b both read `dedup_groups` — the plan treats the **table** as shared state and enforces the digest guard so a parallel read of an incomplete index fails rather than silently mis-counts. Units 2b (spam) and 4b (workload) both read `grievance_redacted` but write disjoint artifacts, so they are safe to overlap.

## Work Plan

Each unit is one PR, one worktree, one `uv run --extra <name>` env, independently green. Parallelism is the default — serial only where the table above says so.

### Unit 0 — Freeze the demo slice + promote issues to `demo` (08 Aug, 0.5 day) — SERIAL gate, blocks all parallel work — **NOW CLOSED VIA #64**

* **Branch / worktree:** `feat/freeze-demo-slice` → `.worktrees/freeze-demo-slice` — **already decided; this unit is now a recording commit**
* **Files it owns:** `janasunani/config.py` (slice constant `SAMBALPUR/2024`), `janasunani/olap/materialize.py` slice predicate, `docs/DELIVERY.md` (record `Sambalpur 2024 — 55,544`), `docs/ROADMAP.md` §2 phase table (flip 9 and 14 spam half to Bounded/demo scope note), `tests/test_slice.py` (asserts `Sambalpur/2024` determinism + digest)
* **Do-not-touch:** (none yet — this is the gate; no other agent is running)
* **Agents:** **1 agent, single** — this is the one serial bottleneck. Do not parallelize; every downstream agent rebases onto its merge commit.
* **Do:** (a) **Record the pre-committed decision from #64:** absent ED override by EOD 07 Aug, slice is the highest-volume `district × year` — **Sambalpur/2024, 55,544 complaints with grievance text** (Ganjam 2024 46,678; Balangir 2024 38,248; see #64 comment 2026-08-07). Write `slice_id = "Sambalpur/2024"` + input counts + `dedup_index.Digest` (10,963 groups / 55,544 signatures) into `config.py` / `DELIVERY.md`. No new query needed — the box already built on this slice. (b) **re-label tracker issues to `demo`:** add `demo` to `#64` (slice decision), `#33` (routing crosswalk), `#50` (spam & duplicate — now includes bounded scorer #74), plus new children `#___` for Units 2b/7; remove `deferred` where it conflicts. Closes #64 formally.
* **Depends:** nothing. **Blocks:** 1, 2, 2b, 3, 7, 4a (i.e., everything). **Status 2026-08-07 14:14:** `janasunani-dedup-index` done on this slice (55,544 of 55,544 indexed) — Unit 2 is therefore box-completed and does not need a local re-run.

### Wave 1 — 09 Aug daytime (four agents in parallel after Unit 0 merges)

All four start from the same base: `main` + Unit 0. They are file-disjoint so no merge conflict by design. The accountable engineer merges them in any order once each is green — no ordering among them.

#### Unit 1 — PII scorecard + corpus scan (1 day, parallel)

* **Branch / worktree:** `feat/pii-scorecard-per-entity` → `.worktrees/pii-scorecard-per-entity`
* **Files it owns:** `janasunani/evaluation/pii_scorecard.py` (already exists — wire to gold), `janasunani/pii/` gold helpers, `tests/test_pii_extra_contract.py` + `tests/test_pii_redaction.py` (existing), new `tests/test_corpus_scan.py`, `docs/FINDINGS.md` (PII section only)
* **Do-not-touch:** `janasunani/pipeline/dedup*.py`, `janasunani/db/models.py`, `janasunani/pipeline/pipeline.py`, `janasunani/pipeline/export.py`, `janasunani/inference/**`, `janasunani/serving/processor.py` — those belong to Units 2 and 3 in this same wave.
* **Agents:** **parallel agent 1 of 3** in Wave 1.
* **Do:** run `uv run --extra pii janasunani-evaluate-pii --gold data/pii_gold/<gold.jsonl>` and publish per-entity table (phone/Aadhaar/email high, names ~0.44, per DELIVERY). Run shaped-PII corpus scan over the slice lake/OLTP redacted text (`grep` shapes for mobile/Aadhaar/PAN/non-gov email) and assert zero cleartext — this is the "55k scale" claim, distinct from the 89-page gold recall. Update `FINDINGS.md` / `outputs/findings/`.
* **Reuse:** existing Presidio recognizers (`stages/pii_tagger.py` Indian recognizers), `pii_eval.py` gate. **Not:** do not tune thresholds before measuring — measure first, gate second.

#### Unit 2 — Dedup index over slice (0.5 day setup + 8–12h overnight run, parallel)

* **Branch / worktree:** `feat/dedup-index-slice-build` → `.worktrees/dedup-index-slice-build`
* **Files it owns:** `janasunani/pipeline/dedup.py` (primitives — no change), `janasunani/pipeline/dedup_index.py` (already OLTP-sourced), `janasunani/db/models.py` (`dedup_signatures`, `dedup_groups`), `tests/test_dedup_index.py`, `tests/test_dedup.py`
* **Do-not-touch:** `janasunani/evaluation/**`, `janasunani/pii/**`, `janasunani/pipeline/pipeline.py`, `janasunani/pipeline/export.py`, `janasunani/inference/**`, `janasunani/analytics/**`, `janasunani/serving/**`, `janasunani/egress/**`
* **Agents:** **parallel agent 2 of 3** in Wave 1. Its overnight `janasunani-dedup-index` run continues while Wave 1.5 (Unit 4a) starts.
* **Do:** `uv run --extra pipeline-core janasunani-dedup-index --slice <slice> --threshold 0.8 --bands 20` (threshold/bands already pinned in `dedup_index.py`; do not invent new values without updating tests). Persist `dedup_signatures` per `grievance_redactions` row + `dedup_groups` with `source='complaints+grievance_redactions'` + digest. Measure held-out recall against officer 34k duplicates (report recall + incremental count). Salt via `DEDUP_SALT`.
* **Guard:** assert `source` + digest before any downstream join (#137). If digest mismatches, fail loudly — never silently mix snapshots.

#### Unit 3 — Pipeline E2E verification (0.5 day, parallel)

* **Branch / worktree:** `feat/pipeline-e2e-rehearsal` → `.worktrees/pipeline-e2e-rehearsal`
* **Files it owns:** `janasunani/pipeline/pipeline.py`, `janasunani/pipeline/export.py`, `janasunani/inference/`, `janasunani/serving/processor.py`, `tests/test_pipeline_e2e.py` (new, real-code-path)
* **Do-not-touch:** `janasunani/evaluation/**`, `janasunani/pii/**`, `janasunani/pipeline/dedup*.py`, `janasunani/db/models.py`, `janasunani/analytics/**`, `janasunani/egress/**`
* **Agents:** **parallel agent 3 of 3** in Wave 1.
* **Do:** one live grievance (scanned doc) through 6 stages in order, artifact DB counts reconcile at each stage, exporter upserts into OLTP, `GET /grievance/{id}` returns `{redacted_text, summary, category, routing: {method:"fallback"}}`. This is the Committed component-a demo path. No 7th stage gating yet.

### Wave 1.5 — 09 Aug overnight → 10 Aug (overlaps dedup tail)

#### Unit 2b — Spam signal scorer (1 day, starts 09 Aug overnight with dedup tail)

* **Branch / worktree:** `feat/spam-signal-scorer` → `.worktrees/spam-signal-scorer`
* **Base:** `main` + Unit 0 (no other gate — spam signal does not need dedup)
* **Files it owns:** `janasunani/pipeline/spam.py` (new — bounded `spam_score`/`spam_reason` over redacted text), `janasunani/serving/triage.py` (scoring path, replacing unconditional `abstained`), `janasunani/evaluation/spam_scorecard.py` (new — prevalence + PPV/false-positive harness), `tests/test_spam_scorer.py` (new), `tests/test_triage_spam.py` (new)
* **Do-not-touch:** `janasunani/pipeline/dedup*.py`, `janasunani/db/models.py` (dedup tables), `janasunani/routing/**`, `janasunani/analytics/marts.py`, `janasunani/analytics/sql/**`, `janasunani/serving/intelligence.py`, `janasunani/egress/**`, `janasunani/evaluation/pii_scorecard.py`, `janasunani/evaluation/sarvam_scorecard.py`
* **Agents:** **parallel agent, runs alongside overnight dedup + 4a** — triple-parallel tail of Wave 1. This hides spam work inside the dedup window.
* **Do:** (a) implement the bounded scorer: inputs are `redacted_text` + `is_repetition_collapsed` + `len(redacted_text)` + lookup against the 8 discard-reason families derived from `action_taken_remark` (ROADMAP §5.2) — only `details inadequate` (39,943) and `no specific grievance` (16,340) map to spam-like; `case already taken up` (19,904) + `duplicate copy` (14,767) are dedup, never spam; `not within purview` (8,455) + `documents not attached` (29,029) + `needs policy decision` (9,090) are incomplete/routing failures, never spam. Emit `spam_score ∈ [0,1]` + `spam_reason ∈ {low_signal_details_inadequate, low_signal_no_grievance, repetition_collapse, length_too_short, clean}` with evidence. No raw PII read, never on `grievance` column. (b) corpus prevalence over slice `grievance_redacted`; report by district/category/mode/year. (c) wire `serving/triage.py:assess()` so live `TriageResult.spam` carries the score (no blocking — `decision` remains advisory, `method` reflects scorer version, with `Unavailable` fallback if scorer absent). (d) measure held-out PPV / false-positive vs the 34k officer-confirmed spam-like discards (distinct from duplicate families) — reported, not gated. (e) `janasunani-spam-score --slice <slice>` CLI for the overnight run.
* **Guard:** never score `duplicate` or `not within purview` families as spam; never read `complaints.grievance` raw; never mutate `status` — this is an advisory score that gates nothing (Phase 15 still decides how to surface it).
* **Fallback if slips:** demo keeps the old `abstained` triage banner — non-blocking.

#### Unit 7 — Routing crosswalk live (0.75 day, parallel in Wave 1)

* **Branch / worktree:** `feat/routing-crosswalk-live` → `.worktrees/routing-crosswalk-live`
* **Base:** `main` + Unit 0 (no dedup/PII gate — crosswalk builds from the lake's history alone)
* **Files it owns:** `janasunani/routing/crosswalk.py`, `janasunani/routing/reference/routing_crosswalk.json` (built artifact, committed), `janasunani/routing/mappings.py`, `janasunani/routing/rules.py` (enable_crosswalk wiring), `janasunani/inference/service.py` (wiring `MappingRouter(enable_crosswalk=True)`), `janasunani/serving/schemas.py` (RoutingResult.learned shape already exists), `tests/test_routing_crosswalk.py` (existing, now gated on real artifact), `tests/test_routing_integration.py` (new — `method` ladder)
* **Do-not-touch:** `janasunani/pipeline/dedup*.py`, `janasunani/pipeline/spam.py`, `janasunani/db/models.py`, `janasunani/analytics/**`, `janasunani/serving/intelligence.py`, `janasunani/egress/**`, `janasunani/evaluation/**`
* **Agents:** **parallel agent 4 of 4** in Wave 1 (file-disjoint with Units 1, 2, 3). Finishes well before the freeze — artifact just needs to merge before Unit 6.
* **Do:** (a) run `uv run janasunani-build-crosswalk --lake data/interim` (or OLTP-derived lake slice) to produce `by_category`, `by_subcategory`, `by_category_district`, `by_full` tables — measured at 60.9% / 67.5% / 72.8% argmax accuracy on history; commit the JSON (aggregates only, safe to commit — no citizen prose). (b) flip live paths to `MappingRouter(enable_crosswalk=True)` in both `inference/service.py` (warm processor) and `serving/` (API), preserving the ladder: `crosswalk (method:"learned", with empirical_evidence)` → `mappings/rules (method:"rules")` → `fallback (method:"fallback")`, with `None` on missing artifact degrading gracefully. (c) add `tests/test_routing_integration.py` to assert the ladder and `confidence` computed from support+share (not asserted). (d) document that live `route()` still receives `category+district` only (no subcategory from classifier per `crosswalk.py:15`), so only `by_category` and `by_category_district` rungs are live — `by_subcategory`/`by_full` are built and tested but not consulted.
* **Guard:** never route on outcome (disposal time/benefit) — incidence only; office-level optimisation stays deferred. Never ship a new `transformers` dep for routing.
* **Fallback if slips:** demo ships `method:"rules"→"fallback"` (the pre-promotion path) — also non-blocking, but the routing demo cell will show manufactured fallback rather than history-derived routing.

#### Unit 4a — Intelligence marts + closure findings (no dedup needed, parallel with dedup tail)

* **Branch / worktree:** `feat/intelligence-marts-closure` → `.worktrees/intelligence-marts-closure`
* **Files it owns:** `janasunani/analytics/marts.py` + `janasunani/analytics/sql/closure.sql` + `janasunani/analytics/sql/action_type.sql`, `janasunani/analytics/action_type.py`, `janasunani/analytics/findings/` (closure headline, two-day bare, discard reasons, confirmed duplicates, misrouting baseline), `tests/test_marts.py`, `tests/test_action_type.py`
* **Do-not-touch:** `janasunani/pipeline/dedup*.py`, `janasunani/pipeline/spam.py`, `janasunani/routing/**`, `janasunani/db/models.py`, `janasunani/serving/intelligence.py` (that is 4b), `janasunani/egress/**`, `janasunani/evaluation/**`
* **Agents:** **parallel agent, runs alongside the overnight dedup job** — file-disjoint, so no wait is needed. This is how the 8–12h dedup run is hidden inside productive work.
* **Do:** validate closure/action_type marts over fixture lake (DuckDB + Postgres constructs); run findings CLIs over slice lake (reading only redacted text) to produce `closure_finding_summary.csv` + `outputs/findings/*.csv` + Markdown fragments. Publish the closure aggregate that 4b will later serve.

### Wave 2 — 10–12 Aug (serial gate clears, then two agents in parallel)

4b and 5 both need their serial gates met, then they are parallel.

#### Unit 4b — Intelligence workload / spike / themes (needs dedup, 1 day)

* **Branch / worktree:** `feat/intelligence-workload-spike` → `.worktrees/intelligence-workload-spike`
* **Base:** `main` + Units 0, 2, 4a (rebased after dedup + marts merge — this is the one serial edge in Wave 2)
* **Files it owns:** `janasunani/serving/intelligence.py` + `janasunani/serving/schemas.py`, `janasunani/analytics/findings/` (themes + spike detection only), `tests/test_intelligence.py`, `DATA_DIR/aggregates/` publishing
* **Do-not-touch:** `janasunani/egress/**`, `janasunani/evaluation/sarvam_scorecard.py`, `janasunani/pipeline/dedup*.py` (already merged, read-only now)
* **Agents:** **parallel agent 1 of 2** in Wave 2 (starts as soon as dedup + 4a merge).
* **Do:** publish aggregates to `DATA_DIR/aggregates/`; wire supervisor dashboard to read them with strict schema allowlist + reconciliation (lookup-join vs `CASE` query); duplicate-adjusted workload = filings minus dedup-group size>1; spike-with-cause = three numbers per spike (filings, distinct problems via dedup groups, distinct citizens). Themes = grouping by substance (redacted text) then concentrated+rising filter for one category.
* **Fallback:** if Unit 2 slips, ship `closure` panel only (needs no dedup). Themes drop first per DELIVERY. This unit would be dropped, not delayed.

#### Unit 5 — Sarvam benchmark (1.5 days, parallel with 4b)

* **Branch / worktree:** `feat/sarvam-paired-scorecard` → `.worktrees/sarvam-paired-scorecard`
* **Base:** `main` + Units 0, 1 (needs slice + redacted sample list)
* **Files it owns:** `janasunani/egress/sarvam.py` (single outbound module), `janasunani/evaluation/sarvam_scorecard.py`, `janasunani/config.py` (`Settings.sarvam_api_key` — already centralized at `0ba59a8`), `tests/test_egress_boundary.py`, `tests/test_sarvam_scorecard.py`
* **Do-not-touch:** `janasunani/serving/intelligence.py`, `janasunani/serving/schemas.py`, `janasunani/analytics/**`, `janasunani/pipeline/dedup*.py`
* **Agents:** **parallel agent 2 of 2** in Wave 2.
* **Do:** (a) register route `trust_tier=authorized-external`, record authorization ref, retention, encryption, audit fields; (b) sample few hundred pages from slice, stratified handwritten/printed, call `Sarvam Vision` (doc-read mode 50p + field-extract ₹1 note which) and pipeline pytesseract on same pages; report per-surface divergence (no accuracy verdict for OCR); (c) categorize same tickets via `sarvam-105B` vs MuRIL against recorded `grievance_category` — report accuracy spread (e.g., police 0.85 vs welfare 0.51 pattern per DELIVERY); (d) transliteration probe on romanized Odia if present. Log every call (ticket, stage, provider, model ID, bytes, auth ref). Keep kill switch to `dpic-infra` fallback.
* **Why parallel with 4b:** different output artifacts, different tests; both read `dedup_groups` but neither writes it. No file conflict.

### Unit 6 — Demo integration & freeze (13 Aug, 1 day) — SERIAL, single agent

* **Branch / worktree:** `chore/demo-integration-freeze` → `.worktrees/demo-integration-freeze`
* **Base:** `main` + Units 0–5, 7, 2b (all content including promoted routing + spam merged)
* **Files it owns:** `deploy/` (compose), `frontend/` (supervisor cards + **triage banner** + **routing badge**), `janasunani/serving/api.py`, `janasunani/inference/service.py` (freeze wiring), `docs/DEMO.md`, tagging
* **Do-not-touch:** (owns the freeze — no other agent is in flight)
* **Agents:** **1 agent, serial** — nothing merges after this branch is cut at 13 Aug 12:00.
* **Do:** (a) wire Next.js supervisor screens to `GET /supervisor/dashboard`, history browse to lake, live submission to warm processor; (b) wire **triage banner**: `possible duplicate of #NN` / `campaign N filings` / `low-signal: <reason> (spam_score)` — all advisory, never blocking, gated on `spam_score` + `duplicate_group_id` presence with fallback to `Unavailable` text; (c) wire **routing badge**: `dept / office / confidence / method:"learned" vs "rules" vs "fallback"` with `empirical_evidence` tooltip (support+share) when `learned`; (d) rehearse full 7-stage E2E `document in → routed grievance out` with spam+routing visible; (e) bring up `api`/`frontend`/`proxy` on CPU box if Phase 12 allows (`deploy/README.md`, `issues #30`) otherwise laptop per DELIVERY fallback. Record full benchmark table (Table 2 + routing accuracy + spam prevalence) with uncertainty. Tag `demo-2026-08-14-rc1`, freeze.

## Execution summary (who runs where)

```
main @ 0ba59a8
 │
 ├─ 08 Aug SERIAL ── Unit 0 (single agent) ──► merge slice freeze + label #33,#50 as demo
 │                       │
 │        ┌──────────────┼──────────────┬──────────────┐
 │        ▼              ▼              ▼              ▼
 │   Wave 1 (09 Aug, 4 agents in parallel)
 │   Unit 1 PII      Unit 2 Dedup     Unit 3 E2E      Unit 7 Routing
 │   (pii)           (pipeline-core)  (skeleton)      (routing)
 │        │              │              │              │
 │        └──────┬───────┴──────┬───────┘              │
 │               │ overnight    │                      │
 │   Wave 1.5 ──►│──► Unit 4a Marts/Closure  ∥  Unit 2b Spam scorer (both parallel with dedup tail)
 │               │         (analytics)            (pipeline/spam + triage)
 │               ▼              │                      │
 │   ── merge dedup + marts + spam ──► Wave 2 (10-12 Aug, 2 agents in parallel)
 │                                     Unit 4b Workload/Spike  ∥  Unit 5 Sarvam
 │                                     (serving)                 (egress/eval)
 │                                            │                       │
 │                                            └───────────┬───────────┘
 │                                                        ▼
 │                                     Unit 6 SERIAL (single agent, 13 Aug freeze)
 │                                     E2E 7-stage + routing badge + spam banner + dashboards
 │                                                        │
 │                                                   demo 14 Aug — no code
```

Wall-clock without parallelism: ~7.5 days → misses freeze. With waves above: **critical path stays 3.5 days** (Unit 0 → Unit 2 → Unit 4b → Unit 6) — routing (7) and spam (2b) are off the critical path by design, so the promotion costs **0 days** on the wall clock. Wave 1 is now 4-way parallel; Wave 1.5 is 3-way (dedup tail + 4a + 2b).

Worktree lifecycle (DPIC convention): `git worktree add .worktrees/<name> -b <branch> <base>` → `uv sync --extra <name>` (per-worktree venv via `UV_PROJECT_ENVIRONMENT` or `uv run --extra` isolation) → `pytest`/`ruff` in that worktree → PR → merge to `main` → `git worktree remove .worktrees/<name>` (branch kept). Main checkout is never used for feature work.

## Validation Plan

Each worktree must be green in isolation; the accountable engineer re-runs the matrix on `main` after each merge (do not take agent logs at face value). Run in the env listed — do not combine conflicting extras.

| Check | Command | Must show |
|---|---|---|
| Lint | `uv run ruff check .` | 0 errors |
| Pipe + serving tests | `uv run --extra serving --extra pipeline-core pytest` | green; real-code-path, not mocked lake |
| PII gate | `uv run --extra pii pytest tests/test_pii_extra_contract.py tests/test_pii_redaction.py tests/test_redact_grievance.py tests/test_rederive_pii_draft.py tests/test_bootstrap_pii_gold.py` | green |
| Egress boundary | `uv run --extra serving pytest tests/test_egress_boundary.py -v` | single-module invariant holds, no citizen-data import outside `janasunani/egress/` |
| DVC DAG | `uv run dvc dag` | no cycles, `pipeline-sample` deps listed explicitly |
| Slice determinism | `uv run --extra serving --extra pipeline-core pytest tests/test_slice.py` (new) | same slice_id → same row counts + digest |
| Gold PII gate (local only) | `uv run --extra pii janasunani-evaluate-pii --gold <gold.jsonl>` | per-entity table + headline; do not chase 0.8056 blindly — report per DELIVERY caveats |
| Corpus scan | `uv run --extra pii python -m janasunani.evaluation.pii_scorecard --corpus data/interim/complaints.parquet` (or OLTP slice query) | zero cleartext shaped PII in slice redacted_text |
| Dedup recall | `uv run --extra pipeline-core pytest tests/test_dedup.py tests/test_dedup_index.py` + held-out recall script | recall vs 34k officer duplicates reported, digest asserted on join |
| Spam scorer | `uv run --extra pipeline-core pytest tests/test_spam_scorer.py tests/test_triage_spam.py -v` + `janasunani-spam-score --slice <slice>` prevalence | `spam_score∈[0,1]` + `spam_reason` valid; `not within purview` never scores as spam; PPV/false-positive by language+mode reported; `TriageResult.spam` non-blocking |
| Routing | `uv run --extra serving pytest tests/test_routing_crosswalk.py tests/test_routing_integration.py -v` + `janasunani-build-crosswalk` | `by_category` 60.9% / `by_category_district` 72.8% on history; `method:"learned"` carries `empirical_evidence`; ladder `learned→rules→fallback` + graceful None on missing artifact |
| Marts | `uv run --extra serving pytest tests/test_marts.py -v` | closure/action_type SQL valid on DuckDB + Postgres constructs |
| Intelligence | `uv run --extra serving pytest tests/test_intelligence.py -v` | schema/arithmetic validation, `Unavailable*` on stale/missing, forbid extra columns |
| Sarvam | `uv run --extra serving pytest tests/test_sarvam_scorecard.py -v` + paired run log | handwritten/printed split, few-hundred-page sample, audit log row per call |

Highest-risk validation: **the dedup digest guard (#137)**. If the lake materialization does not carry the same `source+digest` as the OLTP-sourced `dedup_groups`, downstream joins must fail loudly. This is the only check that turns a silent count corruption (duplicate-adjusted workload, spike distinct-problems) into a visible error. Second-highest: **spam ↔ dedup confusion** — the 34,671 duplicate-family rows must never inflate the spam numerator; tests assert `not within purview` and `duplicate copy` families score `spam_score=0`.

## Risks / Rollback

| Risk | Likelihood | Impact | Mitigation / fallback |
|---|---|---|---|
| Slice not frozen → overnight jobs run on wrong input | High if left until 10 Aug | All downstream counts invalid, mixed-snapshot joins | **Unit 0 is a blocking PR on 08 Aug.** Every job reads the same slice constant + digest; digest mismatch fails the build, not the demo. |
| Dedup index hours-long, OOM or band/threshold mis-tune | Medium | Blocks workload + spike distinct-problems | Use pinned threshold 0.8 / bands 20 already covered by tests; run on CPU box (not laptop); keep campaign dedup distinct from spam (do not threshold campaigns as spam). Fallback: ship closure-only intelligence (no new processing). |
| Spam ↔ dedup confusion (duplicate rows counted as spam, or campaigns suppressed) | High | Inflated spam prevalence, suppressed governance signal | Spam scorer's 8-family decomposition is the guard: test that `duplicate copy` + `case already taken up` (34,671 rows) and `not within purview` (8,455) never contribute to `spam_score`. Campaigns are dedup groups, never spam. Report prevalence separately. |
| Routing crosswalk over-fits or mis-routes on demo slice | Medium | Demo shows confident wrong `dept` | Crosswalk confidence already guards this: low-support / fragmented categories fall through to `rules`/`fallback`. Demo tooltip shows `support+share` so a thin route is visibly thin. Subcategory rungs exist but are not live (no subcategory from classifier), so only the two validated rungs (`by_category`, `by_category_district`) gate the live path. |
| Lake freshness gap confuses demo ("where is my just-submitted grievance?") | High | Demo history browse looks empty | Documented by design (ARCHITECTURE: OLTP vs lake). Demo script: show `GET /grievance/{id}` (OLTP, live) then `GET /history` (lake, next materialization). Do not patch serving to read OLTP for history. |
| Sarvam cost/auth or network failure on eve of demo | Medium | No live Vision call during rehearsal | Pre-run paired sample and cache redacted inputs + Vision outputs in `outputs/sarvam/` (aggregates, not row prose). Kill switch to `dpic-infra` pytesseract path; benchmark table shows cached divergence + categorizer accuracy even if live call fails. |
| PII headline 0.496 misread as regression vs old 0.806 | High (audience) | Undermines trust | Always present per-entity table (phone 0.83/Aadhaar 0.86/email 0.75 vs names 0.44) + corpus zero-cleartext claim + DELIVERY caveat (different tasks, 404/529 labels are names, small 50-doc sample, bank/scheme classes unscored). |
| Single-engineer bottleneck on judgment calls | High | Slice choice + A/B sign-off stall | Those are the only serial items (DELIVERY). Timebox slice choice to 08 Aug EOD; A/B plan (Phase 16) is intentionally deferred — do not pull it into demo week. |
| Data/privacy incident from materialized lake on laptop | Medium | PII exposure | Lake and dedup artifacts stay on DPIC-controlled machines (`same-host` / `dpic-infra`); never `authorized-external`; enforce `DEDUP_SALT`; findings emit aggregates only. |

**Rollback:** every unit is behind a feature flag or artifact presence check (`intelligence.py` returns `Unavailable*`, dedup groups carry digest, egress has kill switch, spam scorer has `Unavailable` triage fallback, routing crosswalk `None` degrades to `rules`/`fallback`). Rollback is removing the artifact or flipping the flag — no schema migration to reverse.

## Open Questions

1. **Slice choice:** which district/year/category is defensible? Needs analyst + product input by EOD 08 Aug. Recommendation in Unit 0 is a starting point, not a decision.
2. **Gold location:** path to the 89-page adjudicated gold set (`data/pii_gold/` or DVC remote?) — confirm before Unit 1 run.
3. **Sarvam authorization record:** approval ref + retention terms to populate the egress registry entry — confirm with DPIC ops before Unit 5.
4. **GPU box window:** is `gpu_box_count=1` available 11–12 Aug for DeepSeek vs Vision comparison, or does 11 Aug need to be CPU Vision-only? Affects whether Unit 5 can also smoke-test self-hosted Vision path.
5. **AWS live bring-up (#30):** will Phase 12 be green for 13 Aug rehearsal on the box (Elastic IP 52.66.116.80), or do we stay on the DELIVERY fallback (laptop demo)? Decision by 10 Aug standup so frontend wiring targets the right host.

## What this plan does not do

* No DVC-provenance rewrite, no new top-level package (all work stays in existing `pipeline/` / `analytics/` / `evaluation/` / `egress/` / `serving/` / `routing/`), no ad-hoc egress paths, no lake queries from serving, no transcription commissioning (fallback stands). The only new module is `janasunani/pipeline/spam.py` (bounded scorer) — it reuses the `dedup.py` convention (stdlib-only where possible) and the triage seam, not a new top-level package.
* No learned office-level scorer, no disposal-time/benefit optimisation, no semantic dedup (different words, same issue) — those stay Part III even after this promotion.

---

*No code changed in this plan. Routing + spam are now demo-committed (Bounded) with `demo`-labeled issues; the next step is to approve, then create `.worktrees/freeze-demo-slice` on `feat/freeze-demo-slice` and land Unit 0 (slice freeze + label #33 + #50 as `demo`) — the gate that unlocks the 4-way Wave 1 (Units 1, 2, 3, 7) and the 3-way overnight tail (4a + 2b + dedup).*
