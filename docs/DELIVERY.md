---
title: Janasunani
subtitle: Delivery plan for the 14 August demonstration
author: Yashaswi Mohanty
date: 27 July 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and the University of Chicago Trust
status: Internal
---

## What is built

The full grievance history is loaded and verified: 1,371,288 complaints and 6,556,171 action records.

All six pipeline stages are implemented: page classification, text extraction, removal of personal information, page-type filtering, summarisation, categorisation. End-to-end integration is unverified, since it has not yet been run start to finish in one pass.

The live API path works in controlled tests and the interface is built, but a
full scanned grievance has not yet been verified start to finish through the
interface with every intended local model. Wiring the interface to those model
responses rather than test responses is still in progress. Routing now runs on
the empirical crosswalk learned from case history
(issue #33, closed 7 August). The older 60.9/67.5/72.8% figures were measured on
the same history used to fit that crosswalk and are not held-out accuracy. The
new chronological benchmark (2021–23 train, 2024 validation, 2025 test) reports
45.15% top-1 and 69.05% top-3 historical-destination agreement for the live
category+district features on 208,267 eligible test cases; because the 2025
cohort was inspected during harness development, this is **developmental
held-out evidence**, not a release gate. It measures where cases were sent, not
jurisdictional correctness or where they resolved best. A learned outcome-based
scorer on disposal time and officer-recorded benefit (issue #106) is a separate,
harder problem and stays later work.

Deployment automation is written and reviewed but not yet run. The system is not switched on at our AWS server.

Manual privacy correction produced an 89-page current set. It measures redaction
by data type, but the gold has no language field, so it cannot support a
by-language claim.

The document-reading half of that comparison is gated by something else entirely: a hand-transcribed sample of scanned pages, which nobody has been asked to produce yet. Two different bottlenecks, two different people.

## What we deliver on 14 August

**Table 1. The five components**

| Component | On the day | Commitment | If it slips |
|---|---|---|---|
| The DSI pipeline, rebuilt | A scanned grievance processed live, start to finish, with a table of how often redaction misses personal information by data type. No by-language result is claimed because the current gold lacks language labels | **Committed** | Demonstrate from a laptop rather than the server; retain the same evidence caveats |
| Actionability and duplicates | Repeat submissions linked and mass campaigns grouped as one issue, across a defined portion of the backlog. The five-way taxonomy and exact-template weak-label audit are separated from a 60-case frontier-adjudicated developmental test; that test is not officer-confirmed, has no outside-purview support and is not a release gate | **Bounded** | Duplicates plus the tested bounded low-signal regression behavior; if the developmental score is shown, its 60-case denominator and release limitations stay on the same slide |
| The intelligence layer | Duplicate-adjusted workload. One worked example of a spike separated into filings, distinct problems and distinct citizens. Local issue themes for one category. The closure finding | **Bounded** | Themes drop first. If duplicate detection slips, only the closure finding survives, since it is the one item needing no new processing |
| A/B testing of the automation | A **draft**, not-yet-locked stepped-wedge design, its measurement contract, and the inputs needed for a power calculation. No workflow or citizen effect is claimed before a governed pilot | **Framework only** | The reviewed draft and impact-metric registry; illustrative power values are not presented as measured MDEs |
| Sarvam benchmark | Cached Sarvam Vision evidence from completed/interrupted runs: 56 paired successful pages in the larger run, provider failures/completions, divergence, list-price cost estimates and adapter wiring. The cached aggregate contains no reportable latency distribution. No new paid calls and no accuracy claim without transcription/adjudication | **Bounded** | Cached aggregate and dry-run wiring only; source artifacts remain untracked, so this is not an independently reconstructable scorecard |

**Committed** means we will demonstrate it. **Bounded** means we will demonstrate it on a defined slice rather than the full 1.37 million. **Framework only** means a draft design and illustrative calculation, not a locked plan, instrumentation or a running trial.

The portion of the backlog is fixed via #64 (07 Aug 2026, pre-committed highest-volume district×year, no ED override): **Sambalpur 2024 — 55,544 complaints with grievance text** (Ganjam 2024 46,678; Balangir 2024 38,248). The dedup backfill over this slice completed 07 Aug 14:14 on the CPU box (`55,544 of 55,544 indexed, 10,963 duplicate groups`, comparison_pairs=16,138,623). All overnight jobs now read this constant (`janasunani/config.py:DEMO_SLICE_LABEL`).

### Benchmark record

**Table 2. Historical reference and current measurement**

| Stage | Earlier figure, and what it was measured on | 14 August |
|---|---|---|
| Personal information removal | 80.6% of items found, on a 106-sentence English validation split | **77.9% of items found** (any-overlap), **55.0% on exact characters**, re-measured 10 August on our corrected 89-page set, by data type. By data type only: the gold carries no language field, so the by-language half of this row cannot be produced. Separately, a scan of all 55,544 redacted complaints in the demo slice found **no** mobile number, Aadhaar, PAN or non-government email left in clear text |
| Text extraction from scans | 77.9% of pages passed three plausibility checks, on 96,469 English pages. Not transcription accuracy: there was no ground truth | **No accuracy figure.** No transcription sample was commissioned, so there is still no ground truth. Reported as divergence from Sarvam Vision, handwritten and printed separately, with no verdict on which is right |
| Duplicate detection | Not attempted | The 37,299 duplicate action rows are an officer-confirmed baseline. Held-out recall and adjudicated candidate precision remain to be measured; automation's additional reviewable increment is not yet claimed |
| Actionability / low-signal | Discard reasons mixed incomplete, irrelevant, duplicate, policy and routing cases | **Developmental, not a release gate:** the weak-label audit retained 106,683 eligible single-label tickets after 67 conflicts and failed the 0.25 office-pooling gate (max total variation 0.522). Separately, on a 60-case frontier-adjudicated test, the local TF-IDF review candidate caught 14/14 review cases and flagged 5/46 actionable cases. The set is not officer-confirmed, has no outside-purview support and has a viewed test; no production threshold is approved. The screenshot failure remains regression evidence for the named case only |
| Historical routing destination | 60.9/67.5/72.8% in-sample crosswalk resubstitution | **Developmental held-out:** category+district top-1 45.15% (95% Wilson CI 44.94–45.36), top-3 69.05%, n=208,267. Informative-category top-1 54.96%, n=142,181. This is historical agreement, not correctness or benefit; freeze a future test slice before promotion |
| Page type | 67% accurate, on the earlier team's own 1,500-page sample | Historical context only. No labelled set exists for August |
| Category assignment | 71% accurate, on the earlier team's train/test split | Historical context only. The new harness is wired, but no governed redacted-text gold set is frozen, so no new category number is reportable |
| Summarisation | 1.9 of 3 for usefulness, scored by one reviewer over 500 pages | Historical context only. There is no current summary-quality benchmark; factuality, critical-fact recall, unsupported facts, PII leakage, usefulness, edit burden and correct abstention need paired blinded review |

The evidence status in each row is part of the result. Developmental held-out
numbers may compare candidates but cannot promote one; weak labels support
training feasibility only; a reproduced regression supports only the named
defect; and historical context is not a current measurement. The complete
status definitions and denominators are in `docs/QUALITY_BENCHMARKS.md`.

The earlier figures come from different samples, different splits and almost entirely English text. They are historical reference, not a target, and several are not like-for-like with anything we will measure. Where we come in lower we will say so and say why.

**We come in slightly lower on personal information, and this is the honest account of it.** 77.9% against a historical 80.6% is close, but the two numbers are not measuring the same thing. The old figure counted an untyped model finding *something* in 106 sentences. Ours counts typed spans against hand-corrected labels over 89 real scanned pages: 77.9% where the redaction lands anywhere on the item, 55.0% where it must land on the exact characters.

The gap was almost entirely names, and most of it has been closed. Phone numbers score 0.83, Aadhaar 0.86, email 0.75, and names now score 0.78 on any-overlap against 0.44 before the surname gazetteer and ALL-CAPS recogniser landed on 7 August. Names are 404 of the 480 scored spans, so they still set the headline.

Two honest qualifications on that number. First, recall rose partly because the name recogniser now fires far more freely: 824 spans predicted against 480 in the gold, and 730 name spans against 404. The gold gives us no way to separate a genuine name the labeller missed from an over-redaction, so we report no precision figure and we do not claim one. Over-redaction has a real cost — it removes what the officer needs to act on. Second, names score 0.78 on any-overlap but only 0.51 on exact characters, so a name is usually touched and often not fully covered.

**The gold-metric gate does not currently pass**, and that is deliberate rather than hidden: `janasunani-evaluate-pii` exits non-zero because coverage of 78.3% sits below the legacy 80.56% figure it is wired to compare against. That threshold is the DSI number, which every other document in this repository labels reference-only and explicitly not a target. Gate and reference should not be the same constant. Fixing that is a code change, not a documentation change, and it is filed rather than quietly relaxed before a demo.

Two things about that number a reader should have.

It is measured on a **50-document sample**, which is small enough that a few pages move it. And it cannot see the identifier classes we added this week: bank account and government scheme numbers have no labels in the gold, so they score nothing either way. The evidence for those is the corpus scan in the row above, not this figure.

What the corpus scan supports and the sample does not: across every complaint in the demonstration slice, **no mobile number, Aadhaar number, PAN or non-government email address survives redaction**. That is a weaker claim than an accuracy figure, because it checks shapes we know how to look for rather than everything a human would catch. It is also the claim that holds at 55,544 rather than 89.

The historical category figure also hides a wide spread (about 0.85 for police
cases and 0.51 for social welfare). We retain that as historical context, not
as evidence for the current live path. The present categorization harness must
be run on a frozen governed gold set before it yields a reportable number.

### On the intelligence layer

The existing dashboards read the complaint record: district, department, category, dates, status. They count and compare anything in it, and they do that well. Two things they cannot do are read what the citizen actually wrote, and recognise that two records describe the same problem. Everything below comes from one of those two abilities.

We hold to one rule. If an analyst with database access could produce a number in a day, we do not present it as something the system enables.

**1. Duplicate-adjusted workload.** How much of the backlog is one problem arriving more than once. The portal counts filings. This counts problems. It requires matching complaints with the same or near-identical wording across a corpus of 1.37 million, which no query does. Matching two complaints that describe the same problem in genuinely different words is a harder task and is not part of August.

**2. Local issue themes.** Which problems are growing in one place rather than everywhere. We group complaints by what they are about rather than by the category assigned at intake, then look for themes that are both concentrated in one area and rising. Concentrated and new is the alert worth acting on.

**3. Spikes with the cause attached.** A rise in complaints can mean four hundred citizens about one road, or two hundred unrelated problems. Those need opposite responses and are identical in a count. Every spike carries three numbers: filings, distinct problems, distinct citizens. Detecting the spike is ordinary. Telling the two cases apart is not.

**4. How cases are closed.** Officers close using a graded set of standard phrases: disposed, disposed with appropriate action, or disposed with the beneficiary benefited. Of the 776,922 complaints closed with one of those standard phrases, **61% use the wording that claims no action**, while the more specific wording was available and used 304,140 times.

The denominator matters. That 61% is of complaints closed on a standard disposal phrase, which is about two thirds of all resolved complaints. Measured against every resolved complaint the figure is 39%, because a third close on some other wording entirely.

One qualifier goes with the number wherever it travels. The share rises with the amount of work recorded on the case: 58% where three to five steps were taken, 65% where six or more were. The cases with the most movement on file are the most likely to close on the bare phrase, not the least. Whatever explains the choice of wording, it is not that nothing happened.

This one needs care for a second reason too. Sometimes no action is the correct outcome: an information request answered, an ineligible claim properly refused, a matter already settled elsewhere. A correct closure and a premature one look identical in the record, so the figure is a description and not a verdict.

Whether the citizen returns is a useful signal here, and it needs the same-issue matching from point 1, so it is a real capability. But it identifies cases worth reviewing rather than cases decided wrongly. Citizens sometimes return after a correct refusal, and often do not return after a bad one. It narrows where to look. It does not settle what happened.

Establishing what share of no-action closures were genuinely premature needs a few hundred cases read and adjudicated by hand. That is not August work, and we will not present the figure as though it were.

The 61% itself needs no machine learning. We hand it over as a query the department can run for itself.

The same field yields 37,299 duplicate action rows officers have already identified. We use those to measure ourselves. The number that matters is how many more we find, not how many we find in total.

None of this waits on document scanning.

**On the citizen text itself.** All four items read what people wrote, and what people write contains names, phone numbers and addresses. Historical complaint text is passed through the same redaction step the scanned documents use, before any matching or grouping runs over it. That runs first, not afterwards.

Redaction is not the whole answer. Dedup signatures, groups and embeddings are
still built from citizen writing, so those matching artifacts stay on our own
machines and sit under the same access rules as the original records. Separately,
a shape-screened redacted sample was sent to hosted Codex for one-time
development adjudication; it contained no ticket IDs, but exact provider
retention evidence was unavailable and that route is not a production precedent.

### On the last two

The fourth is a design, not a running trial. No department has yet agreed which of its offices would go first.

The fifth is a benchmark harness plus cached provider evidence. The available
evidence can show which calls completed or failed, measured divergence,
character-length ratios, list-price cost estimates and whether the adapter
resumes safely. The cached aggregate has no reportable latency distribution or
actual billing record. It cannot answer whether Sarvam reads Odia better: that still requires
hand transcription and observed language/handwriting strata. No additional
paid call is required to finish the wiring.

Three Sarvam models are relevant.

- **Sarvam Vision** reads scanned documents in 22 Indian languages including Odia. Two modes were costed at the then-published list prices: reading a page as text at 50 paise, or pulling named fields out of it at ₹1. Actual billing was unavailable. The second is what a grievance officer would actually use, and our pipeline has no equivalent.
- **Sarvam-105B** is a candidate for categorisation and summarisation. Note
  that the model originally costed, Sarvam-30B, has since been withdrawn. No
  current category or summary quality result exists for 105B in this project.
- **Transliteration**, converting Odia written in Roman letters into Odia script. Our pipeline does not solve this at all today.

The durable cached aggregate combines a completed five-page run with the
successful pages from an interrupted larger run. The larger run contains 56
paired successful pages. Every paired normalized output differed and Sarvam
produced 1.3345 times as many normalized characters on that aggregate; neither
fact is an accuracy result. New spend is paused, so the report uses these cached
results and does not extrapolate a full-run quality conclusion.

One caveat, and it is not the one we first wrote down. Sarvam state that Vision is trained on handwritten text across all 22 Indian languages, so handwriting is a supported case rather than an unmentioned one. What they do not publish is a handwriting-specific number: the headline benchmarks are general document scores, and they note accuracy is lower on highly stylised handwriting without saying by how much. Much of our corpus is handwritten. We will report handwritten and printed pages separately, because the split is the part nobody has measured.

A naming note, since the two are easy to conflate. Sarvam Akshar is the document digitisation platform; Sarvam Vision is the model underneath it. We benchmark the model, and the scorecard will say which surface was called.

## Schedule

**Table 3. Three weeks**

| Week | Planned work and current status | Verification or remaining gate |
|---|---|---|
| 27 to 31 July | Manual privacy labelling completed. AWS activation and one-pass end-to-end verification remain outstanding | The current gold set exists; a scanned grievance must still complete the real browser/model path with reconciled stage counts before either deployment claim is made |
| 3 to 7 August | Privacy scorecard. Historical complaint text redacted. Actionability weak-label audit and duplicate detection. Backlog duplication count. Management metrics, local issue themes, and spike view | Redaction pass completes over the chosen portion before matching. Actionability is reported as weak-label/confounding evidence only; known repeats and candidate matches keep separate denominators. Each published metric reconciles against source data |
| 10 to 14 August | Cached Sarvam aggregate, frontier-adjudicated actionability development benchmark, draft experiment design and full evidence-status table | Sarvam is reported without latency or accuracy claims; routing and actionability are labelled developmental; categorization and summary remain unmeasured until governed gold exists. The experiment MDE remains illustrative until the pre-period extract is frozen |

Work stops Thursday 13 August. Friday is rehearsal, no code changes.

Three dependencies set this order.

Privacy labelling gates the privacy scorecard, which is why it finishes this week rather than running alongside.

The hand-transcribed sample gates the Sarvam document-reading comparison. It is the one item on the final week's path with no owner.

Duplicate detection must precede the spike view. A campaign of four hundred citizens about one road is a genuine surge and should be reported as one. What duplicate detection adds is the ability to say whether a surge is four hundred people on one issue or four hundred separate problems, which are different facts requiring different responses.

Labelling finishing this week lets the privacy scorecard move to week two, which takes the final week down to two items. That matters, because the last week also carries rehearsal.

## How the three weeks are worked

One engineer is accountable for the whole of it. The engineering is heavily assisted by AI agents, which write code, draft analysis and review each other's work. That is what makes three weeks plausible for this much scope.

It does not move responsibility. Integration, checking results against real data, and final sign-off on anything touching citizen data, security or the live server are done by the engineer. The assistance speeds up how fast work is produced, not who answers for it.

Below that, three kinds of work, and they scale differently.

**Building runs in parallel.** Most of the code is self-contained enough to be written alongside itself rather than one piece after another: the duplicate-matching logic, the spam checks, the closure query, the metric definitions, the supervisor screen, the Sarvam connector. Each is tested on its own before it touches real data. This is where being one engineer is least binding.

**Processing the full history runs overnight.** Redacting the complaint text, building the duplicate index and computing the themes are hours-long jobs over more than a million records. They run in that order, and they are scheduled early and left running rather than squeezed into the final days.

**Judgement cannot be parallelised, and that is what sets the schedule.** Four things need a person:

| Work | Why it takes the time it takes |
|---|---|
| Correcting the privacy pages | Reading and judging each one; current scorecard covers 89 pages |
| Transcribing a sample of scanned pages by hand | The only way to know whether Sarvam reads Odia better than we do is to have a correct transcription to compare both against. No such record exists |
| Choosing which portion of the backlog to demonstrate on | A judgement about what is defensible. It is also the starting gun for the overnight processing, which cannot begin until the portion is fixed |
| Fixing the A/B analysis plan | The estimator and the power calculation need statistical judgement |

**Resolved, and not in the direction we wanted.** No owner was named for the transcription sample, so on 7 August we took the written fallback rather than let the decision block the final week. The document-reading comparison reports how the two systems differ, with no verdict on which is right.

Administrative categories can be joined without transcription, but they record
historical labels rather than policy correctness and still require a frozen,
group-disjoint sample. No governed paired category/summary benchmark was
completed for the cached Sarvam run, so it does not produce a category accuracy
result. That comparison remains wiring plus an evaluation schema until the
paired adjudicated reference exists.

The transcription sample is still worth commissioning after 14 August. Sarvam say their model is trained on handwritten text in all 22 languages but publish no handwriting-specific figure, and much of this corpus is handwritten. An independent split result on printed versus handwritten pages would be a measurement nobody has published, on precisely the case this corpus consists of.

## Not in scope for August

Running Sarvam's large model on our own hardware. Natural-language querying. Comparing offices against each other, which needs case-mix adjustment first. Replacing our models with Odia ones, which follows the benchmark.

## Decisions needed

**Table 4. From you**

| Decision | By |
|---|---|
| Whether the demonstration audience may see real citizen data, and under whose login | 7 August |
| Whether the closed-without-action finding is shown on 14 August or brought to you first. It reflects on the redressal process, not the software. We would report it at state level only, never office by office | 7 August |
| Whether any department has agreed in principle to an A/B trial later, which would let us present the design with a named partner | 14 August |
| Who can hand-transcribe roughly fifty scanned pages in Odia and English. Without it the Sarvam comparison has no correct answer to measure against. **The one item on the final week's path with nobody assigned** | Next meeting, and no later than 31 July |
