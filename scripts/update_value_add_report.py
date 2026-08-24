"""Emit the long value-add report as DPIC Markdown.

This replaces a script that patched an existing .docx in place: it applied
corrections idempotently to a tracked binary, regenerated six embedded charts
and preserved a layout nobody could review in a diff. That made the Word file
the source of truth, which meant the actual source of truth was unreadable in
version control and every correction had to be expressed as a search-and-replace
against prose it could not see.

The report is now written here and rendered by `dpic-build-brief`:

    dpic-build-brief docs/value-add-report/value-add-report.md \\
                     docs/value-add-report/Janasunani_2.0_Value_Add_Report_August_2026.docx

Figures are referenced by bare filename and resolved against the sibling
`Exhibits/` directory, which is a symlink to `figures/` so the PNGs are not
duplicated in the repository.

This is the evidence record. Where the two short briefs summarise, this one
carries the denominators, the split policy, the intervals and the reasons a
number may not be used for something adjacent to what it measures. If the three
documents ever disagree, this one is right and the others are stale.

The fail-closed contract is unchanged: `load_benchmark_facts` raises when the
bundle lacks a required artifact.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from janasunani.evaluation.value_add_benchmark_facts import (
    DEFAULT_BUNDLE,
    BenchmarkFacts,
    load_benchmark_facts,
)

DEFAULT_OUTPUT = Path("docs/value-add-report/value-add-report.md")


def _percent(value: float, places: int = 2) -> str:
    return f"{value * 100:.{places}f}%"


def _frontmatter() -> str:
    return (
        "---\n"
        "title: Janasunani 2.0 — value-add report\n"
        "subtitle: The evidence record\n"
        "author: Data, Policy and Innovation Centre\n"
        "date: 23 August 2026\n"
        "organisation: Data, Policy and Innovation Centre\n"
        "partnership: Government of Odisha and the University of Chicago Trust\n"
        "status: Working draft. Not publication-ready; impact is not measured.\n"
        "---\n"
    )


def _how_to_read(facts: BenchmarkFacts) -> str:
    return f"""# How to read this document

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
`{facts.bundle_id[:16]}`, `publication_ready={str(facts.publication_ready).lower()}`,
with {facts.impact_available_required} of {facts.impact_required} required impact
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
"""


def _measured(facts: BenchmarkFacts) -> str:
    pii = facts.pii["overall"]
    act = facts.actionability
    conf = act["confusion"]
    summary = facts.summary
    latency = facts.latency
    return f"""# Removing personal information

Historical complaint text is passed through redaction before any matching,
grouping or model work runs over it. That order is not negotiable: dedup
signatures, groups and embeddings are all built from citizen writing.

**Table 2. Redaction, measured on 89 hand-corrected pages**

| Measure | Result |
|---|---:|
| Items found anywhere on the span | {_percent(pii['overlap_recall'])} ({pii['overlap_hits']}/{pii['gold']}) |
| Items found on exact characters | {_percent(pii['exact_recall'])} ({pii['exact_hits']}/{pii['gold']}) |
| Spans predicted | {pii['predicted']} |

![Redaction recall by data type, against the 89-page corrected set.](fig_pii.png)

*Note: {pii['predicted']} spans predicted against {pii['gold']} labelled. The set cannot separate a name the labeller missed from an over-redaction, so there is no precision figure and none should be inferred.*

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
| Cases needing officer review | {conf['true_review']}/{act['actual_review']} caught; {conf['false_review']}/{conf['true_actionable'] + conf['false_review']} ordinary cases also flagged | n={act['n']}, frontier-adjudicated, test viewed. `release_eligible={str(act['release_eligible']).lower()}` |
| Category, top-3 | {_percent(facts.categorization['top_k_accuracy']['3'])} | n={facts.categorization['n']:,}, 2024 chronological, exact-text-group-disjoint, test viewed |
| Category, top-1 | {_percent(facts.categorization['accuracy'])} | macro-F1 {_percent(facts.categorization['macro_f1'])} |
| Summary drafts usable unedited | {summary['usable_without_edit_rate']['successes']}/{summary['generated_n']} | Single frontier judge, 30 cases. Not officer validation |
| Summary critical facts retained | {summary['critical_fact_recall']['successes']}/{summary['critical_fact_recall']['n']} | Same set |

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
| Attempts completed | {latency['completed_attempts']}/{latency['attempts']} |
| Failures | {latency['failed_attempts']} |

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
"""


def _routing(facts: BenchmarkFacts) -> str:
    routing = facts.routing_outcome
    val = routing["validation_2024"]
    test = routing["test_2025"]
    robustness = routing["robustness_ladder_2024"]["rungs"]
    return f"""<!-- pagebreak -->

# Routing, part one: where cases go

**Table 5. Historical destination agreement, chronological split**

| Features | Test n | Top-1 | Top-3 |
|---|---:|---:|---:|
| Category + district, all eligible | {facts.routing_all['n']:,} | {_percent(facts.routing_all['accuracy'])} | {_percent(facts.routing_all['top_k_accuracy']['3'])} |
| Category + district, informative categories | {facts.routing_informative['n']:,} | {_percent(facts.routing_informative['accuracy'])} | {_percent(facts.routing_informative['top_k_accuracy']['3'])} |

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
| Cases selected as correct completers | {robustness['R0_binary_completers']['n_validation']:,} | {robustness['R0_binary_completers']['delta']:+.4f} | {robustness['R0_binary_completers']['delta_evaluation_se']:.4f} |
| Closure-proxy actionable completers | {robustness['R1_proxy_actionable_completers']['n_validation']:,} | {robustness['R1_proxy_actionable_completers']['delta']:+.4f} | {robustness['R1_proxy_actionable_completers']['delta_evaluation_se']:.4f} |
| Closure-proxy actionable, restricted outcome | {robustness['R2_proxy_actionable_restricted']['n_validation']:,} | {robustness['R2_proxy_actionable_restricted']['delta']:+.4f} | {robustness['R2_proxy_actionable_restricted']['delta_evaluation_se']:.4f} |
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

**Table 8. Validation 2024 (common-support n={val['support']['n_evaluated']:,})**

| Outcome model | Direct Δ | Augmented Δ (SE) | ESS / n |
|---|---:|---:|---:|
| Ridge, top-three actions | {val['tau_0']['ridge_top_three']['delta_dm']:.2f} | {val['tau_0']['ridge_top_three']['delta_aipw']:.2f} ({val['tau_0']['ridge_top_three']['aipw_se']:.2f}) | {val['tau_0']['ridge_top_three']['ess_over_n']:.3f} |
| Boosting, top-three actions | {val['tau_0']['gbm_top_three']['delta_dm']:.2f} | {val['tau_0']['gbm_top_three']['delta_aipw']:.2f} ({val['tau_0']['gbm_top_three']['aipw_se']:.2f}) | {val['tau_0']['gbm_top_three']['ess_over_n']:.3f} |

**Table 9. Test 2025 (common-support n={test['support']['n_evaluated']:,})**

| Outcome model | Direct Δ | Augmented Δ (SE) | ESS / n |
|---|---:|---:|---:|
| Ridge, top-three actions | {test['tau_0']['ridge_top_three']['delta_dm']:.2f} | {test['tau_0']['ridge_top_three']['delta_aipw']:.2f} ({test['tau_0']['ridge_top_three']['aipw_se']:.2f}) | {test['tau_0']['ridge_top_three']['ess_over_n']:.3f} |
| Boosting, top-three actions | {test['tau_0']['gbm_top_three']['delta_dm']:.2f} | {test['tau_0']['gbm_top_three']['delta_aipw']:.2f} ({test['tau_0']['gbm_top_three']['aipw_se']:.2f}) | {test['tau_0']['gbm_top_three']['ess_over_n']:.3f} |

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
"""


def _intelligence_and_impact(facts: BenchmarkFacts) -> str:
    return f"""<!-- pagebreak -->

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

{facts.impact_available_required} of {facts.impact_required} required impact
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

*Source: bundle {facts.bundle_id[:16]}, publication_ready={str(facts.publication_ready).lower()}.*
"""


def create_report(destination: Path, *, benchmark_bundle: Path = DEFAULT_BUNDLE) -> None:
    facts = load_benchmark_facts(benchmark_bundle)
    document = "\n".join(
        [
            _frontmatter(),
            _how_to_read(facts),
            _measured(facts),
            _routing(facts),
            _intelligence_and_impact(facts),
        ]
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--benchmark-bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    create_report(args.output, benchmark_bundle=args.benchmark_bundle)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
