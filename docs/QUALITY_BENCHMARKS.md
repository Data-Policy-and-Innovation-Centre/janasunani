# Pipeline quality benchmark register

**Snapshot:** 11 August 2026. **Purpose:** separate measured quality from
developmental estimates, weak supervision, regression evidence, and proposed
experiments. A number can enter the value-add report only with its denominator,
split policy, uncertainty, and limitations on the same page.

## Evidence ladder

| Status | Meaning | Permitted claim |
|---|---|---|
| Release gate | Frozen, governed test set; test viewed once | Model-quality claim for the stated slice |
| Developmental held-out | Honest split, but test has been viewed during development | Candidate comparison; freeze a future slice before release |
| Weak-label audit | Administrative dispositions, not independent adjudication | Training feasibility and confounding risk only |
| Regression evidence | Exact reproduced failure and tested behavior | The named defect is fixed; not general quality |
| Cached provider evidence | Paid calls already made, no new spend | Coverage, failures, latency/cost and measured divergence only |

## Routing — historical destination agreement

The new harness uses year-disjoint chronological cohorts over weighted
structured feature cells: 2021–23 training, 2024 validation, and 2025 test.
Hyperparameters and the eligible history window are selected on validation;
the final model refits on training plus validation. Full-corpus campaign/dedup
grouping is unavailable, so the split does not claim campaign isolation. The
2025 cohort was inspected while developing the harness, so these are
**developmental held-out** numbers, not a release gate.

| Live features | Test n | Top-1 agreement (95% Wilson CI) | Macro-F1 | Top-3 | ECE |
|---|---:|---:|---:|---:|---:|
| Category + district, all eligible | 208,267 | 45.14% (44.94–45.36) | 19.79% | 69.04% | 11.37% |
| Category + district, informative categories | 142,181 | 54.96% (54.70–55.22) | 25.17% | 79.68% | 13.15% |
| Subcategory upper bound, all eligible | 208,267 | 55.05% (54.83–55.26) | 30.37% | 74.56% | 5.33% |
| Subcategory upper bound, informative categories | 142,181 | 69.15% (68.91–69.39) | 36.50% | 86.53% | 3.87% |

The selected live model used empirical-Bayes smoothing `alpha=100` and a
one-year history window. It modestly exceeded the hard-backoff baseline on the
same cohort (44.87% top-1; 67.86% top-3). Subcategory is an upper bound because
it is not reliably supplied at live intake. All figures measure agreement with
the historical department, not jurisdictional correctness or improved citizen
outcomes. The older 60.9/67.5/72.8% crosswalk figures are in-sample
resubstitution and are not comparable held-out results.

## Actionability and low-signal review

The taxonomy is `actionable`, `underspecified`, `irrelevant`, `out_of_scope`,
and `policy_blocked`. All non-actionable outputs are advisory review/abstention;
the system never auto-rejects. An exact-template administrative audit found
106,683 eligible single-label tickets after excluding 67 conflicting tickets:

| Weak label | Tickets |
|---|---:|
| Underspecified | 72,934 |
| Irrelevant / no specific grievance | 16,282 |
| Policy-blocked | 9,033 |
| Outside purview | 8,434 |

These are train-only weak labels. Across eight sufficiently supported current
offices, maximum total-variation distance from the global label mix was 0.522
(median 0.130), failing the predeclared 0.25 pooling gate. They therefore do
not establish PPV, recall, or a production threshold.

A separate privacy-screened development sample has 180 PII-redacted cases
(60 train / 60 validation / 60 test) labelled in two separate Codex agent
contexts, with another context resolving disagreements or uncertainty. These
are independent passes, not independent model families or providers; the exact
serving model version, hidden prompts, sampling settings and provider-retention
evidence were unavailable. Raw agreement was 99.44% and Cohen's kappa was
0.985. Of 27 resolver judgments, 6 were explicitly marked uncertain. The
committed finalization policy excludes them, leaving canonical development gold
of 174 cases (58 train / 59 validation / 57 test): 140 actionable, 13
underspecified, 7 irrelevant and 14 policy-blocked, with no defensible
`out_of_scope` example. This is **frontier-adjudicated development gold**, not
officer-confirmed truth and not a complete five-class release set.

The sample is deliberately enriched: each split contains five records from
each of four administrative weak-label strata plus 40 previously unlabelled
records. Source-stratum prevalence and inclusion probabilities were not
retained, so accuracy and review precision describe this sample composition;
they are not estimates of production prevalence or PPV.

On the 57-case held-out development test, the validation-selected local
word+character TF-IDF review model produced the following result under the
high-catch advisory policy selected on validation (minimum review precision
60%; maximum actionable-review rate 10%):

| Enriched-sample development metric | Held-out result (95% item-level Wilson CI) |
|---|---:|
| Accuracy | 94.74% (85.63–98.19) |
| Non-actionable review recall | 100% (77.19–100) — 13/13 caught |
| Review precision | 81.25% (56.99–93.41) — 13/16 flagged |
| Actionable cases sent to review | 6.82% (2.35–18.23) — 3/44 |
| ROC-AUC / average precision | 99.13% / 97.46% |

The intervals remain wide. Frozen local MuRIL did not beat TF-IDF, reaching
85.96% accuracy and 69.23% review recall. The canonical aggregate is
[`evidence/actionability_frontier_benchmark_reproducible.json`](evidence/actionability_frontier_benchmark_reproducible.json).
The original 180-row TF-IDF/MuRIL/MiniLM comparison is retained separately as
[`evidence/actionability_frontier_benchmark.json`](evidence/actionability_frontier_benchmark.json),
but it admitted six uncertain resolver labels and is historical evidence only.
The benchmark now exports a checksummed `artifact_format=2` binary model under
`models/actionability`. Serving can load that artifact for the
`actionable_vs_officer_review` objective, so it can emit an advisory review
request while preserving every filing. It does not predict the four
non-actionable reasons and has not been activated in a reviewed release. No
candidate is release-eligible. The next set needs officer review, explicit
outside-purview sampling, duplicate-group isolation, language/source strata and
a newly frozen test split.

The screenshot case `i am an idiot` is **regression evidence**. Version
`spam-v1.1-bounded` now records `low_signal_no_grievance`, requests officer
review, skips category/summary generation, and reports language `unknown`
instead of the previous false `cy` detection. Generic short inputs retain the
bounded short-text advisory.

## Categorization and summary

The categorization harness now has a privacy-screened 2024 development
benchmark over redacted typed grievance text. Exact normalized text groups are
kept in one split, so repetitions cannot leak across the chronological cohorts:
January–June training (n=13,030), July–September validation (n=4,309), and
October–December test (n=3,160). Eighteen historically recorded categories had
at least five examples in every split. The 2024 test was viewed while improving
the harness, so this is **developmental held-out** evidence, not a release gate.

Validation selected the local word+character hashing SGD candidate with
`alpha=1e-5`. Its later-period test results were:

| Historical category agreement metric | Viewed development test |
|---|---:|
| Top-1 | 46.55% (44.82–48.29) |
| Top-3 | 90.89% |
| Top-5 | 95.19% |
| Macro-F1 | 36.49% |
| Expected calibration error | 26.41% |

The validation-selected abstention rule did not meet its selective constraints;
it covered 41.52% of the test with 61.36% accuracy. The full result therefore
supports a ranked top-three decision aid, not automatic categorization. It
measures agreement with historical administrative labels, not whether those
labels were correct policy classifications. There is no promoted serving
artifact, the test is already viewed, and language remains unadjudicated. A
future officer-confirmed, newly frozen and source/language-stratified set is
required for a release claim. The current MuRIL artifact remains an incumbent
to compare on that same governed set; its 71.04% typed-subject figure is a
historical reference, not a like-for-like result.

The first current summary baseline uses local-only BART revision `37f520fa…`
on a deterministic, enriched 30-case sample from the same redacted typed-text
test pool. One Codex agent context applied the committed rubric to private
source/candidate pairs; only opaque structured judgments and aggregate
provenance are governed. It is single-judge **development evidence**, not an
officer-confirmed release scorecard or a prevalence estimate.

| Summary metric | Enriched development result |
|---|---:|
| Critical-fact recall | 55/84 = 65.48% (54.83–74.76) |
| Unsupported-claim cases | 0/26 (upper 95% bound 12.87%) |
| Contradiction cases | 0/26 (upper 95% bound 12.87%) |
| Usable without edit | 8/26 = 30.77% (16.50–49.99) |
| Residual-PII cases in generated output | 4/26 = 15.38% (6.15–33.53) |
| Correct skip behavior | 20/30 = 66.67% (48.78–80.77) |

The failure pattern is operationally important: all six cases the judge said
should be skipped received a draft, while all four coherent Odia cases were
skipped by the English-only gate. Estimated frontier-judge edit time is not
officer time. Before promotion, the pipeline needs a post-summary privacy gate,
better evidence-based abstention, and a newly frozen paired officer review by
language and typed/scan source. The required `summary_release` artifact remains
missing and continues to block publication.

## Sarvam Vision — cached evidence, no new calls

The durable aggregate is [`evidence/sarvam_cached_benchmark.json`](evidence/sarvam_cached_benchmark.json).
The completed five-page run and interrupted 300-page run together provide 56
paired successful pages from the larger run, provider job completion/failure
evidence, character-length comparisons, and cost estimates. Every paired
normalized text differed; Sarvam produced 1.3345 times as many normalized
characters on the 56-page aggregate. Neither fact is an accuracy result.

No more paid calls are required to wire the adapter, checkpointing, release
manifest, or evaluation schema. A quality conclusion still requires a
hand-transcribed set, adjudicated categories/summaries, handwriting and observed
language strata, and failure-inclusive reporting.

## Release gates

Before an alias can be promoted:

1. Freeze dataset and group/split fingerprints before viewing test outcomes.
2. Record code SHA, dependency lock, full parameters, artifact digest, input and
   output schema versions, benchmark run ID, denominator, slices and CIs.
3. Compare the incumbent and candidate on the identical frozen examples.
4. Pass safety guardrails: zero auto-rejection, PII leakage, harmful actionable
   review, unseen-class/department, coverage, provider failure and p90 latency.
5. Resolve the reviewed MLflow alias before deploy into the immutable local
   release manifest; serving never follows a moving alias or calls MLflow.
