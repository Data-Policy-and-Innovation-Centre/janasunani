# Janasunani 2.0 — technical briefing

Speaker notes exported from the deck. Every figure traces to a named artifact.

## Slide 1 — Janasunani 2.0

_No notes._

## Slide 2 — 1.37 million grievances, one question asked of them

Sources: docs/ARCHITECTURE.md, docs/ROADMAP.md. Canonical counts verified on both local SQLite and cloud Postgres and must match after any migration change.

Caveat: the Parquet lake reads 6,548,820 action rows against the canonical 6,556,171, a 0.11% shortfall tracked as issue #241. Use the canonical figure.

DERIVED FIGURE: 162,144 is arithmetic, 1,371,288 minus the 1,209,144 that carry a closing remark. It is not separately measured. Those cases are either still open or were closed without a remark, and the record cannot distinguish the two. If pressed, say that.

Two structural facts behind everything that follows: the portal has never read the grievance text (median 19 words, 61% unique), and there is no citizen key, so every row is an island.

## Slide 3 — One grievance, or all of them at once

The asymmetry is the whole slide. The live path processes one grievance and can never know it duplicates another. Only the batch path compares records against each other.

Duplicate matching is genuinely unavailable live, not merely unbuilt. DuplicateReview defaults to `not_indexed` with the reason 'the live submission path is not connected to a completed index'; serving/triage.py states 'duplicate matching remains slice-scoped and unavailable live'; docs/ARCHITECTURE.md records 'per-request live matching is not wired'. Do not imply a per-request duplicate check exists.

Dedup figures, docs/PERFORMANCE.md section 4, Sambalpur 2024: 55,544 filings, 10,963 distinct problems, 8,560 distinct signatories, ~57 min on 2 vCPU, 16,138,623 comparison pairs. The decomposition is the argument for the index: group GOV2024999640 is 26,203 filings from ONE signatory, while DM2024854026 is 1,291 filings from 1,155. On filing counts alone those two are indistinguishable.

Materialisation ~26 s at full scale, via olap/materialize.py using DuckDB.

The freshness gap is by design: GET /grievance/{id} reads the transactional store and is instant; GET /history and /supervisor read the Parquet lake and lag until the next re-materialisation. Analytics never touch the transactional store.

The diagram is architecture.svg in this directory, embedded as vector with a PNG fallback. Edit the SVG, re-run rsvg-convert, re-run this script.

## Slide 4 — How fast, and how good

SPEEDS. outputs/benchmark/latency.json, run 2026-08-10T23:14:58Z at git sha 24ab193, is_fake_timing false. Document path, n=20 over 10 clusters. Means: OCR 5.833, summarise 6.550, categorise 0.778, redact 0.055, detect PII 0.021, route 0.00048, triage 0.00026. Summarise plus OCR is 12.383 s of the 13.244 s mean run, 93.5%. End to end: typed p50 0.133 s (n=40), PDF p50 13.661 s (n=20). Live API warm POST median 4.44 s (n=8), cold start 19.4 s. Measured on an arm64 laptop, not the deployment box.

Four stages (format classifier, page type, pii, spam) were never separately instrumented and carry n=0. They are omitted rather than shown as zero.

QUALITY, with intervals.
Redaction: overlap recall 0.779, coverage 0.783, exact 0.550 on 480 hand-marked gold spans across 89 pages and 50 documents. Per entity: Aadhaar 0.857 (n=7), phone 0.828 (n=29), name 0.777 (n=404), email 0.750 (n=40). Corpus scan: 0 of 55,544.
Triage: n=57 held-out, accuracy 94.74% (85.63-98.19), review recall 13/13 = 100% (77.19-100), false-flag rate 3/44 = 6.82% (2.35-18.23). TF-IDF word+char beat a frozen MuRIL probe 13/13 against 9/13.
Category: top-3 90.89%, top-1 46.55%, n=3,160, chronological 2024 split, exact-text-group-disjoint, macro-F1 36.5%, ECE 26.4%.
  AGAINST A TRIVIAL BASELINE, computed from the per-class supports in outputs/evaluation/categorization_historical_v1.json. The test set is lopsided: Social Welfare 1,179, Housing 743, Miscellaneous 724, together 2,646 of 3,160. Always guessing the single biggest scores 37.3% against the model's 46.6%, a lift of 9.2 points. Always guessing the biggest three scores 83.7% against the model's 90.9%, a lift of 7.2 points. Say this if anyone treats 91% as the headline.
  Per class the spread is wide. Best: Land Matters F1 62.4%, Energy 62.3%, Social Welfare 62.1%. Worst: General 4.6%, Public Utility 11.1%, Financial Assistance 12.1%. Miscellaneous has 724 cases and 11.7% recall, because a catch-all class has no signature to learn. No class sits at F1 zero.
  The DSI reference of 71.04% for MuRIL is NOT a comparison. It was measured on typed subject lines with a different split and issue #127 warns against putting the two side by side. We have not re-run that model on this split, so no head-to-head exists.
Summary: critical-fact recall 65.48% over 84 facts, 8 of 26 usable unedited, residual PII in output 4/26 = 15.38%. One judge, not an officer. All four coherent Odia cases were skipped by an English-only gate.
Department: top-3 79.68% / top-1 54.96% on informative categories (n=142,181); 69.04% / 45.14% across all eligible (n=208,267). Untouched 2025 test year. This measures agreement with where cases were historically sent, not jurisdictional correctness.

WITHDRAWN, do not use: the in-sample crosswalk figures 60.9 / 67.5 / 72.8; PII coverage 49.6% and name 0.44; MuRIL 71.04% as a current number.

If asked about routing time savings: an estimated 11 to 23 day gain held on validation 2024 and failed on the untouched 2025 test year at -2.35 days against a standard error of 3.50. It was withdrawn on 23 August, commits 879c24c and 365e3b4, and four artifacts are archived as do-not-cite. The direct and doubly-robust estimators disagree by 33 days on the test year, which is the diagnosis: no overlap to estimate on.

## Slide 5 — Three rungs, tried in order

The ladder is janasunani/routing/provider.py. Three modes: ROUTER_DEFAULT 'crosswalk' is the shipped path and runs crosswalk, then mapping tables, then generic fallback; 'rules' skips the crosswalk and reproduces pre-#33 behaviour, useful to isolate the crosswalk in a comparison; ROUTER_INCIDENCE 'incidence' serves a checksummed empirical-Bayes artifact with the same ladder underneath it.

RUNG 1, the crosswalk. Artifact janasunani/routing/reference/routing_crosswalk.json: 34 by-category keys, 257 by-subcategory, 971 by-category-district, 5,084 on the full key. Held-out performance, outputs/evaluation/routing_historical_{informative,all}.json: top-3 79.68% and top-1 54.96% on informative categories (n=142,181); 69.04% and 45.14% across all eligible (n=208,267). Train 2021-23, validate 2024, final refit on train plus validation, test on an untouched 2025. Selected alpha=100 with a one-year history window.

RUNG 2, why a fallback is needed at all. The ORTPSA masters carry no category-to-department foreign key: intCategoryGrp is NULL on all 62 categories (janasunani/routing/crosswalk.py:3, mappings.py:36). MappingRouter can only bridge by exact name, which covers a handful of the 62. That absence is the whole reason the crosswalk had to be learned from history instead of read off a table.

RUNG 3, the trained model. Opt-in via JANASUNANI_ROUTER=incidence, artifact checksummed, and IncidenceRoutingProvider falls through to the crosswalk and rules when a lookup fails, logging rather than failing silently.

THE CAVEAT THAT MATTERS, and it applies to all three rungs. Every one of them measures agreement with the historical destination, not jurisdictional correctness. A correct-authority adjudication does not exist. Roughly 300 closed cases read by hand would settle it.

Macro-F1 is weak, 25.2% informative and 19.8% all eligible, and about a dozen departments sit at F1 zero. The top-three framing is doing real work here.

WITHDRAWN, do not use: the in-sample crosswalk figures 60.9 / 67.5 / 72.8, which are resubstitution. And any routing time saving: an estimated 11 to 23 day gain held on validation 2024 and failed on the untouched 2025 test year at -2.35 days against a standard error of 3.50. Withdrawn 23 August, commits 879c24c and 365e3b4.

## Slide 6 — What we cannot measure

Do not cut this slide for time. It is the one that makes the rest credible.

1. OCR ground truth was never commissioned and has no owner, issue #53. It cannot be produced by an agent, because it is the answer key an agent would be scored against.

2. 824 predicted spans against 480 marked. The recogniser over-fires, and the gold set cannot separate a missed label from an over-redaction. Filed as open.

3. The officer-hours figure that circulates, 201,000 to 302,000 across 1,209,144 resolved cases, is a denominator and not a saving. It is the size of the prize, not anything realised.

Two more we do not claim, if asked: no gain from duplicate detection beyond the 37,299 repeats officers already confirmed, and no accuracy result for the outside option on slide 6.

If someone asks why so little is claimed: because the alternative is claiming things we cannot defend, and this deck has to survive the room checking it.

## Slide 7 — It does more. We have not shown it does better.

The provider is Sarvam. Two endpoints, billed separately: digitise at ₹0.50 a page returns text and layout; extract at ₹1.00 a page returns schema-driven fields. Both is ₹1.50. Source: janasunani/evaluation/pricing.py, checked against the Sarvam dashboard 2026-08-07. Our local pipeline is ₹0.00 a page.

THE FOUR EXTRACT FIELDS, which is the 'does more' claim: grievance_category, summary, district, grievance_text. That is our OCR, categoriser and summariser in one call. janasunani/evaluation/sarvam_grievance_schema.py, schema v1, pinned so a later edit cannot silently move a headline number. docs/DELIVERY.md:163: 'our pipeline has no equivalent'.

LANGUAGE. Sarvam Vision lists all 22 scheduled languages plus English, Odia among them. There is also a separate transliteration API for romanized Odia (od-IN), which we do not solve at all today. Our own summariser skipped all four coherent Odia cases through an English-only gate, and non-English text is downgraded to Uncategorized.

WHAT WE ACTUALLY RAN. Two runs, docs/evidence/sarvam_cached_benchmark.json. A 5-page validation (₹7.50) and a 300-page run that died at 65 pages on credit exhaustion, 3 HTTP 402s, 7 job failures. 61 pages paired and scored in total.

WHAT WE FOUND, AND WHAT IT COVERS. Normalised exact-text divergence 1.000 on both runs: the two systems differed on every page. Sarvam returns more characters, ratio 1.2433 then 1.3345. That figure is TRANSCRIPTION ONLY. It compares OCR text and says nothing about the category or summary fields. Divergence says they disagree, never who is right.

THE EXTRACT FIELDS WERE NEVER GRADED. Sarvam did return them: 61 extract jobs completed in the 300-page run. Nothing was compared against them.

  Category was the DECLARED PRIMARY OUTCOME and came back null. Reason, verbatim from outputs/sarvam_validation/sarvam_scorecard.md: 'Not measured — no gold labels (gold_category) in sample; run with --join-metadata from the lake slice.' This is the cheap gap. The recorded category already sits in our own database; the sample was simply not joined to it. Unlike OCR, no new ground truth has to be created.

  Summary was only ever scoped as divergence against BART with no gold referee, and summary_divergence is the same function as divergence_rate under another name (sarvam_scorecard.py:234). Even fully run it could only have said the two summaries differ, never which was better. It was not run: no paired sarvam_summary / pipeline_summary in the sample.

  A schema bug returned HTTP 400 on every extract submission until 2026-08-09, which is why the 5-page validation run has no extract output at all. Every test mocked the transport, so no test could see the 400.

NOT MEASURED, do not claim: OCR accuracy, category accuracy, summary quality, latency (stated in four places), actual billed cost (every rupee figure is list price), observed language split, handwritten versus printed split.

GOVERNANCE. Trust tier authorized-external. Authorisation is a GoO-Sarvam MoU with sign-off from the Additional Chief Secretary, Electronics & IT, accepted 2026-08-07, on the basis that no state statute currently governs the transfer. All three provider controls remain UNVERIFIED: retention terms, encryption in transit, encryption at rest. Authorisation and verification are recorded separately on purpose. One module may make the call, janasunani/egress/, every attempt is audit-logged, and a kill switch falls back to local pytesseract.

COST AT SCALE, projected list price: ₹48,000 to digitise the 96,469-page English corpus, ₹145,000 for both endpoints, ₹8,050 to push 1.37M subjects through the 105B text model. At 10 requests a minute, which does not rise with the plan tier, the full corpus is roughly ten days of continuous calling. It is a measurement instrument, not a backfill path. Do not quote ₹700; that was priced on the withdrawn 30B model.

## Slide 8 — Data, Policy and Innovation Centre

Close on the limits, not a summary.

What we are not claiming: no officer minutes saved, no faster resolution, no satisfaction improvement, no accuracy figure confirmed by an officer, no redaction precision, no deduplication increment, no routing gain.

Nearly every effect worth proving is blocked on a log line or a timer, not on a model.
