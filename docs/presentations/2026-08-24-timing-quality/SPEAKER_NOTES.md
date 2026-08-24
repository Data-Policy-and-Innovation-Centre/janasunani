# Janasunani 2.0 — Timing and Quality

Speaker notes exported from the deck. Every figure traces to a named artifact.

## Slide 1 — Janasunani 2.0

_No notes._

## Slide 2 — 1.37 million grievances, one question asked of them

Sources: docs/ARCHITECTURE.md, docs/ROADMAP.md. Canonical counts verified on both local SQLite and cloud Postgres and must match after any migration change.

Caveat: the Parquet lake reads 6,548,820 action rows against the canonical 6,556,171, a 0.11% shortfall tracked as issue #241. Use the canonical figure.

DERIVED FIGURE: 162,144 is arithmetic, 1,371,288 minus the 1,209,144 that carry a closing remark. It is not separately measured. Those cases are either still open or were closed without a remark, and the record cannot distinguish the two. If pressed, say that.

Two structural facts behind everything that follows: the portal has never read the grievance text (median 19 words, 61% unique), and there is no citizen key, so every row is an island.

## Slide 3 — Two axes, and both halves of each

The three-label scheme is the evidence-chip primitive carried over from the 17 August deck.

Governing rule, from janasunani/evaluation/__init__.py: pipeline.pii_eval is a GATE and can fail a release; everything in evaluation/ REPORTS, and a bad result is an answer, not a failure.

Two slides in this deck do nothing but list the open items: slide 7 for timing, slide 11 for quality. Do not cut them for time. A timing and quality briefing showing only the good half of each axis is exactly what this room is trained to distrust.

If asked how the intervals are computed: an audit of evaluation/stats.py (PR #237) found a fixed z=1.96 used for every confidence level and a missing small-cluster correction. Both were fixed, and every clustered interval in this deck is 4 to 10% wider as a result.

## Slide 4 — Seconds, not minutes

Source: outputs/benchmark/latency.json, run 2026-08-10T23:14:58Z at git sha 24ab193, with is_fake_timing false. 30 synthetic grievances (20 typed, 10 PDF) x 3 repeats, first discarded. Host is an arm64 laptop, 10 logical cores, Python 3.13.

Means: 0.109 s typed (clustered SE 0.0115), 13.244 s PDF (SE 0.425). p95: 0.150 s and 15.26 s. Processor startup 6.47 s, one-off.

Live API from docs/PERFORMANCE.md section 1, baseline 2026-08-07 at ca58f31: warm POST median 4.44 s over n=8, first call after boot 9.5 s, cold start to health 19.4 s.

DO NOT cite the latency section of outputs/benchmark/table2.md. It was generated 3h38m before latency.json and wrongly says no harness output exists.

## Slide 5 — Two stages are 94% of the wall clock

Source: outputs/benchmark/latency.json, document path, n=20 over 10 clusters.

Full per-stage means: summarise 6.550, OCR 5.833, categorise 0.778, redact 0.055, detect PII 0.021, detect language 0.006, route 0.00048, triage 0.00026. Summarise plus OCR is 12.383 s of the 13.244 s mean run, 93.5%.

'Everything else' is 0.861 s, the sum of the remaining six instrumented stages.

Four stages (format classifier, page type, pii, spam) were never separately instrumented and carry n=0. They are omitted rather than shown as zero. On the typed path summarise is a no-op at 5e-06 s.

Implication if asked: optimisation effort belongs in the summariser and the OCR engine. Nowhere else on this list is worth touching.

## Slide 6 — Batch work is minutes, not days

Sources: docs/PERFORMANCE.md sections 1 and 4. Measured against production Postgres on the deployment box, not the laptop used for slide 4.

Dedup ran over the frozen demo slice, Sambalpur 2024. Provenance complete: 55,544 of 55,544 on both the group and signature tables. 310 large buckets.

Materialisation via janasunani/olap/materialize.py using DuckDB. History reads the Parquet lake, never the transactional store; a live submission therefore appears in history only after the next re-materialisation, and that freshness gap is by design.

Cost, if asked: marginal cost per call is zero. Open weights on hardware we control. A commercial vision API is Rs 0.50 to 1.50 per page; roughly Rs 8,050 to push 1.37M subjects through a 105B model. Source janasunani/evaluation/pricing.py, verified 2026-08-07.

## Slide 7 — What we cannot claim about time

Do not cut this slide for time.

The officer-hours range covers the registration time embedded in 1,209,144 resolved cases. It is the size of the prize, not anything realised. It must never be presented as a benefit.

A fourth gap, if asked: routing step timing. The figure that previously sat on this slide in the 18 August deck, 11 to 23 days lost between routing steps, was WITHDRAWN on 23 August (commits 879c24c, 365e3b4). See slide 12. A descriptive replacement measuring elapsed time between recorded handling steps is under recomputation and is deliberately not previewed here. Do not reintroduce any day-saving number.

## Slide 8 — Nothing shaped like an identifier survives

Sources: outputs/evaluation/pii_release.json (re-measured 2026-08-10) and docs/FINDINGS.md.

Overall typed overlap recall 0.7792, coverage 0.7833, exact 0.5500. Per entity, overlap: Aadhaar 0.857 (n=7), phone 0.828 (n=29), name 0.777 (n=404), email 0.750 (n=40). Names are 404 of 480 spans so they set the headline; name exact recall is only 0.507.

Gold set: 529 hand-corrected spans, 480 scored after excluding 49 government email addresses by policy. 50 documents, 89 pages.

Stack: Presidio in-process, custom Indian recognisers (mobile/Aadhaar/PAN), spaCy NER, an Indian-surname gazetteer and an ALL-CAPS recogniser.

WITHDRAWN, do not use: the 49.6% coverage figure and the 0.44 name figure. Both predate the ALL-CAPS and gazetteer fixes.

The gate currently FAILS: janasunani-evaluate-pii exits 1 because 0.7833 < 0.8056. That 0.8056 is the DSI reference constant wired as a threshold by mistake, issue #239. It was filed rather than relaxed on the eve of a demo.

## Slide 9 — Shortlists, not decisions

Sources: outputs/evaluation/categorization_historical_v1.json and outputs/evaluation/routing_historical_{informative,all}.json.

TOP-1, if asked, and say it unprompted if the room is quantitative: category 46.6%, department 55.0% clear / 45.1% all. Neither model can name the single right answer and neither is asked to. Macro-F1 is weak: 36.5% category, 25.2% / 19.8% department, with about a dozen departments at F1 zero.

Category split: chronological 2024, exact-text-group-disjoint, 18 categories, ECE 26.4%, release_eligible false.

Department split: train 2021-23, validate 2024, final refit on train+validation, test on an untouched 2025. alpha=100, one-year history window. Intervals suppressed as not cluster-robust for weighted route cells.

CRITICAL CAVEAT: this measures agreement with where cases were historically sent, NOT jurisdictional correctness. A correct-authority adjudication does not exist and is one of the eight publication blockers.

WITHDRAWN, do not use: the in-sample crosswalk figures 60.9 / 67.5 / 72.8%. They are resubstitution and are not comparable held-out results.

Subcategory would give 86.5% top-3, but it is not reliably supplied at live intake, so it is an upper bound and not a live number.

THE WITHDRAWN ROUTING-OUTCOME CLAIM, if asked. The claim held on validation 2024 (augmented +26.77 days, SE 4.04) and collapsed on the untouched 2025 test year (-2.35, SE 3.50). Three separable causes:
(a) The population was selected on a post-treatment variable. The old 'correct' label was read off the closing remark, which routing can itself affect. Robustness rung R0 gives a delta RMSE of +0.0305 (SE 0.0138, t=2.21); rung R1, changing nothing but the population, gives +0.0002 (SE 0.0059, t=0.04). A 125x collapse. The old restriction discarded 63% of cases and kept the ones selected on outcome.
(b) Overlap collapsed on the test year: match rate 0.536 to 0.156, median propensity 0.412 to 0.108. For 84% of test cases the recommended route essentially never occurred in that cell. The direct estimator extrapolates anyway and returns its largest figure ever, 30.53 days; the doubly-robust estimator returns -2.35. A 33-day gap between estimators is the diagnosis.
(c) Censoring tripled: 0.031 train, 0.092 validation, 0.344 test. A third of 2025 cases were still open at the snapshot.

Also worth saying: '11 to 23' was never a confidence interval. It was two estimators disagreeing by a factor of two on identical data, which was the warning sign from the start.

ITEM 4: an audit of evaluation/stats.py found a fixed z=1.96 used for every confidence level and a missing small-cluster t correction. Both fixed; every clustered interval in the repo widened 4 to 10%.

Sources: outputs/experiments/routing_outcome/{robustness,ope_val_ridge,ope_test_ridge,outputs/experiments/routing_outcome/{robustness,ope_val_ridge,ope_test_ridge,censoring}.json; commits 879c24c and 365e3b4; docs/experiments/superseded/README.md.

## Slide 10 — Triage works. Drafting does not.

Sources: models/actionability/benchmark.json and outputs/evaluation/summary_development_v1.json.

TRIAGE, n=57 held-out: accuracy 94.74% (95% Wilson 85.63-98.19), review recall 13/13 = 100% (77.19-100), actionable-review rate 3/44 = 6.82% (2.35-18.23), F1 89.66%, ROC-AUC 99.13%. Selected model is TF-IDF word+char at threshold 0.435. The frozen MuRIL probe got 9/13 recall and 85.96% accuracy. The cheap method won, and that is the procurement point.

Gold set: 180 PII-redacted cases labelled in two independent contexts plus a resolver, 174 canonical after excluding 6 uncertain judgments. Raw agreement 99.44%, Cohen's kappa 0.985.

Fifty-seven cases is a small test. Say so. The intervals are wide and they are on the slide in the notes for a reason.

DRAFTING, n=26 scored: critical-fact recall 65.48% (54.83-74.76) over 84 facts, usable unedited 8/26 = 30.77% (16.50-49.99), residual PII in output 4/26 = 15.38% (6.15-33.53). Zero unsupported claims and zero contradictions, but the upper bound on each is 12.87%. Mean usefulness 1.5 against the DSI reference of 1.9. One judge, not an officer.

The English-only gate skipped all 4 coherent Odia cases, a 100% miss on that slice. The summary_release artifact is missing and is one of the eight publication blockers.

Local BART, bart-large-cnn rev 37f520fa.

## Slide 11 — What we cannot claim about quality

Sources: outputs/evaluation/pii_release.json, docs/PERFORMANCE.md section 6, outputs/findings/confirmed_duplicates.md, outputs/benchmark/full_benchmark.json.

37,299 = 21,117 'already taken up' plus 16,182 'duplicate copy', re-run 8 August. NOTE: outputs/findings/duplicate_recall.md still holds a buggy 18,432 from a template-matching defect, and 34,671 and 39,937 are older superseded totals. Do not read those files.

On text extraction: a commercial vision model returns 1.3345x as many characters as ours on 56 paired pages. Neither fact says which is right. The accuracy row was dropped from the delivery table because no owner was ever named for a hand-transcription sample, issue #53.

The eight publication blockers: pipeline_latency_release, pii_officer_release, actionability_officer_release, categorization_release, summary_release, routing_correct_authority_release, pilot_operational_effects, pilot_citizen_outcomes.

If pushed on why so little is claimed: because the alternative is claiming things we cannot defend, and this deck has to survive the room checking it.

## Slide 12 — Data, Policy and Innovation Centre

Close on the limits, not a summary.

What we are not claiming: no officer minutes saved, no faster resolution, no satisfaction improvement, no accuracy figure confirmed by an officer, no redaction precision, no deduplication increment, no routing gain.

Nearly every effect worth proving is blocked on a log line or a timer, not on a model.
