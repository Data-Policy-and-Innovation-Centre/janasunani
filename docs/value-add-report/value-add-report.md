---
title: Janasunani 2.0 — value-add report
subtitle: The evidence record
author: Data, Policy and Innovation Centre
date: 23 August 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and the University of Chicago Trust
status: Working draft. Not publication-ready; impact is not measured.
---

# How to read this document

This is the evidence record. The two short briefs summarise it; where they
disagree with this document, this one is right and they are stale.

Every figure here carries its denominator, what it was measured on, and its
status. The status is part of the result. A number without it is not a shorter
version of the same claim — it is a different and unsupported one.

**Table 1. The evidence ladder**

| Status | What it means | What may be claimed from it |
|---|---|---|
| Release gate | Frozen, governed test set, viewed once | A model-quality claim for the stated slice |
| Developmental held-out | Honest split, but the test was viewed during development | Candidate comparison. Freeze a future slice before release |
| Weak-label audit | Administrative dispositions, not independent adjudication | Training feasibility and confounding risk only |
| Regression evidence | An exact reproduced failure and the tested behaviour | That the named defect is fixed. Not general quality |
| Cached provider evidence | Paid calls already made, no new spend | Coverage, failures, cost and measured divergence only |

**Nothing in this report is at release-gate status.** The bundle behind it is
`f64a999f47bf3240`, `publication_ready=false`,
with 0 of 2 required impact
artifacts available.

Three distinctions run through everything below, and most misreadings collapse
one of them.

*Model quality is not officer behaviour.* An accuracy figure says a model would
have agreed with a historical label. It does not say an officer saw the
suggestion, or accepted it.

*Officer behaviour is not citizen outcome.* An accepted suggestion may change
nothing a petitioner experiences.

*Technical latency is not time saved.* The pipeline's processing time is not the
officer's time, and neither is the citizen's wait.

# The measurement gap

Before any model, the arithmetic. The grievance cell reports disposal percentage
and pendency. Both are counts.

Its pendency report carries no median age and no time to resolve anywhere on the
screen. Its district report prints its own formula — disposal rate = resolved
over total — in which the denominator is filings rather than problems or
citizens, so four hundred copies of one complaint count four hundred times.
Whether a petitioner benefited is recorded in the database and no reported
metric depends on it.

*Note: verified for the CM Grievance Cell, whose screens were reviewed on 11 August 2026. Believed general, not checked against a department or Collector login.*

This matters for reading the rest of the document. Several contributions below
are not improvements to an existing number. They are numbers the organisation
does not currently have.

# Removing personal information

Historical complaint text is passed through redaction before any matching,
grouping or model work runs over it. That order is not negotiable: dedup
signatures, groups and embeddings are all built from citizen writing.

**Table 2. Redaction, measured on 89 hand-corrected pages**

| Measure | Result |
|---|---:|
| Items found anywhere on the span | 77.92% (374/480) |
| Items found on exact characters | 55.00% (264/480) |
| Spans predicted | 824 |

![Redaction recall by data type, against the 89-page corrected set.](fig_pii.png)

*Note: 824 spans predicted against 480 labelled. The set cannot separate a name the labeller missed from an over-redaction, so there is no precision figure and none should be inferred.*

Three qualifications travel with this number wherever it goes.

There is no precision figure, and over-redaction has a real cost: it removes
what the officer needs to act on. Recall alone is a one-sided view.

There is no by-language split. The corrected set carries no language field, so
every record scores as unknown. "English only" describes how the set was
assembled, not something the scorecard verifies.

The sample is small enough that a few pages move it, and it cannot see the
identifier classes added most recently — bank account and scheme numbers have no
labels in it and score nothing either way.

What the sample does not support but a corpus scan does: across every complaint
in the demonstration slice, no mobile number, Aadhaar number, PAN or
non-government email survives redaction. That is a weaker claim than an accuracy
figure, because it checks shapes we know how to look for rather than everything
a human would catch. It is also the claim that holds at 55,544 records rather
than 89.

<!-- pagebreak -->

# Reading and triaging what was filed

**Table 3. Model results, all developmental**

| Task | Result | Denominator and status |
|---|---|---|
| Cases needing officer review | 13/13 caught; 3/44 ordinary cases also flagged | n=57, frontier-adjudicated, test viewed. `release_eligible=false` |
| Category, top-3 | 90.89% | n=3,160, 2024 chronological, exact-text-group-disjoint, test viewed |
| Category, top-1 | 46.55% | macro-F1 36.49% |
| Summary drafts usable unedited | 8/26 | Single frontier judge, 30 cases. Not officer validation |
| Summary critical facts retained | 55/84 | Same set |

The actionability taxonomy separates *underspecified*, *irrelevant*,
*out of scope* and *policy blocked* rather than calling all four spam. The
serving contract is advisory throughout: a flag never blocks a filing. The
checksummed artifact can serve the binary review decision only; it does not
assign the four reasons and is not release-eligible.

Two limitations on the category figure that change how it should be read. It
measures agreement with the label an officer recorded, not whether that label
was right. And officers report that the category field does not meaningfully
affect routing, so they do not invest in getting it right — meaning the target
is noisy partly because the field carries little operational weight. A ranked
shortlist is what this supports; automatic assignment is not.

The summary baseline has a failure pattern that matters more than its headline.
Every case the judge said should be skipped received a draft anyway, and every
coherent Odia case was skipped by an English-only gate. Residual identifying
detail appeared in some generated output, so a post-summary privacy gate is
required before any promotion.

<!-- pagebreak -->

# Speed

**Table 4. Development timing**

| Measure | Result |
|---|---:|
| Attempts completed | 90/90 |
| Failures | 0 |

![Processing time by input type, development run.](fig_time_comparison.png)

*Note: deterministic synthetic filings on a laptop, sequential single-process execution. This is not a server benchmark, and it is not officer time.*

The only officer-time figure anyone has is self-reported: officers describe
roughly 10 to 15 minutes to turn a submitted document into a registered
complaint, the same in either language. Any future claim of time saved must be
stated against that denominator, and it is an estimate rather than an
observation.

One assumption this corrects. Handwritten Odia has been treated as the
bottleneck. Officers fluent in Odia do not describe it that way: reading the
document is not what takes the time. The constraint is comprehension and typing.

<!-- pagebreak -->

# Routing, part one: where cases go

**Table 5. Historical destination agreement, chronological split**

| Features | Test n | Top-1 | Top-3 |
|---|---:|---:|---:|
| Category + district, all eligible | 208,267 | 45.14% | 69.04% |
| Category + district, informative categories | 142,181 | 54.96% | 79.68% |

*Note: 2021–23 train, 2024 validation, 2025 test. The 2025 cohort was inspected while developing the harness, so this is developmental held-out evidence, not a release gate.*

This measures agreement with the department a case was historically sent to. It
is not jurisdictional correctness and not a claim about where cases resolved
best. The older 60.9 / 67.5 / 72.8% crosswalk figures were in-sample
resubstitution over the same history the crosswalk was fitted on and are not
comparable to these.

<!-- pagebreak -->

# Is outcome-based routing the right question?

The production rule sends a grievance wherever cases of its type have gone
before. Its blind spot is specific: it learns where cases *were* sent, never
where they were *handled well*. A department that receives every land dispute in
a district and resolves none of them is, to that rule, exactly the right
destination.

## Why routing for speed fails

Closure is an action the officer controls directly. A grievance can be closed at
any moment by recording that it has been disposed. An objective that rewards
short durations without qualification is therefore maximised by closing
everything immediately and doing nothing.

**Table 6. How grievances close, over 1,209,144 resolved cases**

| Closing wording | Cases | Share | Median days | Flagged benefited |
|---|---:|---:|---:|---:|
| Disposed, no action claimed | 472,782 | 39.1% | 46 | 9.2% |
| Non-standard text | 432,222 | 35.7% | 25 | 3.7% |
| Disposed with appropriate action | 280,887 | 23.2% | 54 | 6.2% |
| Disposed, beneficiary benefited | 23,253 | 1.9% | 44 | 70.5% |

Read the last column before the others. The benefit flag does not track the
ladder: closures claiming *no* action carry it more often (9.2%) than closures
claiming appropriate action (6.2%). The two signals disagree, and in the wrong
direction, which is why the flag is not used to define whether action was taken.

Read the median column next. Closures claiming action are **slower** than those
claiming none, by 18 days. The fastest way to close a grievance is not to work
on it. An unconstrained speed objective would learn exactly the wrong policy.

So the question must be posed as a constrained one: can grievances be disposed
faster *without* losing whether action is taken. Not "how fast are the cases
that came out correct", which compares a different population under each routing
choice and rewards a route for abandoning its hard cases.

<!-- pagebreak -->

## That distinction is measurable, not merely theoretical

Selecting the analysis sample on whether action was taken makes the joint
department-and-chain assignment appear to predict duration when it does not.

**Table 7. Joint-action ablation under the corrected population sequence**

| Population and target | Validation n | Δ RMSE from adding the joint action | Evaluation SE |
|---|---:|---:|---:|
| Cases selected as correct completers | 166,628 | +0.0305 | 0.0138 |
| Closure-proxy actionable completers | 454,232 | +0.0002 | 0.0059 |
| Closure-proxy actionable, restricted outcome | 454,232 | +0.0002 | 0.0059 |
| Closure-proxy actionable, restricted outcome with IPCW | rerun required | — | — |

*Note: positive means lower prediction error after adding the joint action. The SE resamples district-year validation clusters with fitted models held fixed; no fit-bootstrap uncertainty was run.*

Correcting the population collapses the effect. The last three rows are
identical in the historical aggregate because the closure-derived proxy is
observed only for resolved cases. A post-review audit found that the weighted
R3 validation risk and bootstrap had dropped the IPCW magnitudes. That rung is
withdrawn pending a corrected rerun (#291); the as-run equality must not be
interpreted.

<!-- pagebreak -->

## What the corrected analysis supports

Durations are restricted at a 365-day horizon. The developmental OPE compares
the jointly selected department and complete intended chain on common support.
Positive Δ means the candidate rule has a lower fitted restricted duration than
historical assignment.

**Table 8. Validation 2024 (common-support n=450,567)**

| Outcome model | Direct Δ | Augmented Δ (SE) | ESS / n |
|---|---:|---:|---:|
| Ridge, top-three actions | 24.50 | 26.77 (4.04) | 0.194 |
| Boosting, top-three actions | 23.90 | 12.40 (4.12) | 0.168 |

**Table 9. Test 2025 (common-support n=113,535)**

| Outcome model | Direct Δ | Augmented Δ (SE) | ESS / n |
|---|---:|---:|---:|
| Ridge, top-three actions | 30.53 | -2.35 (3.50) | 0.073 |
| Boosting, top-three actions | 31.32 | 0.15 (4.41) | 0.081 |

*Note: 3,665 validation and 2,037 test rows lie outside the declared common support. The 2025 augmented estimates do not reproduce the validation gain.*

**Table 10. The constraint, and why this is not yet a recommendation**

| Question | Answer |
|---|---|
| Has historical routing been shown to be slower? | No. Direct estimates favour the candidate rule, but the augmented 2025 estimates are −2.35 and 0.15 days |
| Does a correctness-constrained threshold exist? | **Not recomputed.** The prior frontier used the wrong normalization population and is withdrawn pending a corrected labelled-row rerun (#284); no `τ*` is published |
| Is this a recommendation? | No. No causal routing gain has been established |

The positive validation result does not survive the later-period augmented
check. Reporting only the direct method or only 2024 would choose the convenient
answer. **No claim that alternative routing saves time or preserves action is
supported by this work.**

Four further limits. Assignment provenance is unresolved: the current snapshot
does not establish that department and chain preserve the initial intention to
route. The officer's view of destination workload is absent from the
conditioning set. Actionability is read from closing remarks, so it is a
post-resolution proxy rather than an intake-time population definition. And an
earlier internal comparison of two PMAY routes, 23 days against 48, was a raw
average by route with no adjustment for how cases differ; it was never a saving.

<!-- pagebreak -->

# Duplicate-adjusted workload

The portal counts filings. This counts problems.

![Filings, distinct problems and distinct citizens in the demonstration slice.](fig_dedup.png)

![A volume spike separated into filings, problems and citizens.](fig_spike.png)

*Note: 37,299 duplicate action rows are an officer-confirmed baseline. The additional reviewable increment found by automation is not yet claimed, because held-out recall and adjudicated candidate precision have not been measured.*

The distinction is operational, not cosmetic. A rise in complaints can mean four
hundred citizens about one road, or two hundred unrelated problems. Those need
opposite responses and are identical in a count. Every spike therefore carries
three numbers: filings, distinct problems, distinct citizens.

# How cases are closed

Of complaints closed on one of the standard disposal phrases, 61% use the
wording that claims no action, while the more specific wording was available and
used 304,140 times. Measured against every resolved complaint the figure is 39%,
because a third close on some other wording entirely. Neither denominator is
optional.

One qualifier travels with it. The share rises with the amount of work recorded:
58% where three to five steps were taken, 65% where six or more were. The cases
with the most movement on file are the most likely to close on the bare phrase.
Whatever explains the choice of wording, it is not that nothing happened.

And a correct closure and a premature one look identical in this record. An
information request answered, an ineligible claim properly refused and a case
dropped without work all close on the same string. This is a description, not a
verdict, and establishing what share was genuinely premature needs a few hundred
cases adjudicated by hand.

![Distribution of filings by gender across the corpus.](fig_gender.png)

# What is not claimed

0 of 2 required impact
artifacts exist. Impact is therefore reported as **not measured**, which is
different from measured and found to be zero.

Specifically unclaimed: officer minutes saved; correct legal authority; faster
resolution; fewer repeat contacts; improved citizen satisfaction. Each requires
instrumentation that does not exist — an append-only record of what the system
showed and what the officer then did, adjudication against something other than
historical labels, and a governed rollout with a comparison group.

A routing suggestion already ships in the live portal, labelled as AI-suggested.
It has no measured accuracy and no override log. That is the ordinary situation
and it is why the exposure-and-decision record is a precondition rather than a
refinement.

The full definitions of every officer and citizen outcome, and the conditions
under which each becomes measurable, are in `docs/IMPACT_METRICS.md`. The
governed status of every model number is in `docs/QUALITY_BENCHMARKS.md`.

# Reproduction

Pull the governed evidence with `dvc pull dvc.yaml:full-benchmark-bundle`.
Regenerate the Markdown with `uv run python scripts/update_value_add_report.py`.
Render the Word document with `dpic-build-brief` using the generated Markdown as
the source and the tracked `.docx` path as the destination.

The generator fails closed when the bundle lacks the tracked timing, the
selected actionability test, the weak-label audit, the PII scorecard or either
routing scorecard or routing-outcome aggregate, so no figure here can outlive
the evidence behind it. The routing-outcome evidence is reproduced by the
commands in `docs/QUALITY_BENCHMARKS.md`.

*Source: bundle f64a999f47bf3240, publication_ready=false.*
