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

Live processing works. A grievance submitted through the web interface comes back with its redacted text, category, summary and routing. The interface is built; wiring it to the live models rather than to test responses is still in progress. Routing uses fixed rules; the learned version is later work.

Deployment automation is written and reviewed but not yet run. The system is not switched on at our AWS server.

Manual privacy correction of eighty-five pages finishes this week. That is the step that tells us how often redaction misses something. It gates the privacy scorecard, and the privacy half of the Sarvam comparison.

The document-reading half of that comparison is gated by something else entirely: a hand-transcribed sample of scanned pages, which nobody has been asked to produce yet. Two different bottlenecks, two different people.

## What we deliver on 14 August

**Table 1. The five components**

| Component | On the day | Commitment | If it slips |
|---|---|---|---|
| The DSI pipeline, rebuilt | A scanned grievance processed live, start to finish, with a table of how often redaction misses personal information, by data type and by language | **Committed** | Demonstrate from a laptop rather than the server. Report redaction results for English only |
| Spam and duplicates | Repeat submissions linked and mass campaigns grouped as one issue, across a defined portion of the backlog, with a count of how much of it is duplicated work. Complaints written in Odia script and in Roman letters are matched separately, not against each other | **Bounded** | Duplicates without spam scoring. A smaller portion of the backlog |
| The intelligence layer | Duplicate-adjusted workload. One worked example of a spike separated into filings, distinct problems and distinct citizens. Local issue themes for one category. The closure finding | **Bounded** | Themes drop first. If duplicate detection slips, only the closure finding survives, since it is the one item needing no new processing |
| A/B testing of the automation | The experiment design, the size of effect we could detect, and where the AI already agrees with officers today | **Framework only** | None needed. Nothing here depends on new engineering |
| Sarvam benchmark | Sarvam Vision against our pipeline on a paired sample of a few hundred pages. **Categorisation reports accuracy against the recorded category; document reading reports only how the two differ**, since no transcription owner was named | **Bounded** | Categorisation is now in scope, because its reference is administrative data we already hold. Transcription accuracy is out, and reported comparatively |

**Committed** means we will demonstrate it. **Bounded** means we will demonstrate it on a defined slice rather than the full 1.37 million. **Framework only** means a design and a calculation, not running software.

The portion of the backlog is not yet fixed. We will settle it after a brainstorming session and name it here, whether that is one district, one year or one category. Vague now is honest; vague on 14 August would not be.

### Benchmark record

**Table 2. Historical reference and current measurement**

| Stage | Earlier figure, and what it was measured on | 14 August |
|---|---|---|
| Personal information removal | 80.6% of items found, on a 106-sentence English validation split | **49.6% measured**, on our corrected 89-page set, by data type. English only: no Odia labelled set exists. Separately, a scan of all 55,544 redacted complaints in the demo slice found **no** mobile number, Aadhaar, PAN or non-government email left in clear text |
| Text extraction from scans | 77.9% of pages passed three plausibility checks, on 96,469 English pages. Not transcription accuracy: there was no ground truth | **No accuracy figure.** No transcription sample was commissioned, so there is still no ground truth. Reported as divergence from Sarvam Vision, handwritten and printed separately, with no verdict on which is right |
| Duplicate detection | Not attempted | Recall against the 34,000 duplicates officers have already identified |
| Page type | 67% accurate, on the earlier team's own 1,500-page sample | Historical context only. No labelled set exists for August |
| Category assignment | 71% accurate, on the earlier team's train/test split | Historical context only, unless a held-out labelled set is identified in week 1 |
| Summarisation | 1.9 of 3 for usefulness, scored by one reviewer over 500 pages | Historical context only. Re-scoring needs a blinded human review we have not scheduled |

The first three rows are measurements we will produce. The last three are the earlier team's numbers, reported so the record is complete, and we are not claiming to have re-measured them. Committing otherwise would be promising evidence the plan contains no step to produce.

The earlier figures come from different samples, different splits and almost entirely English text. They are historical reference, not a target, and several are not like-for-like with anything we will measure. Where we come in lower we will say so and say why.

**We come in lower on personal information, and this is the honest account of it.** 49.6% against a historical 80.6% is not a regression: the two numbers are not measuring the same thing. The old figure counted an untyped model finding *something* in 106 sentences. Ours counts typed spans against 529 hand-corrected labels over 89 real scanned pages, and a span only counts as found if it lands on the right characters.

The gap is almost entirely names. Phone numbers score 0.83, Aadhaar 0.86, email 0.75. Names score 0.44, and names are 404 of the 529 labels, so they set the headline. spaCy's English model was not built for Odia personal names and it shows.

Two things about that number a reader should have.

It is measured on a **50-document sample**, which is small enough that a few pages move it. And it cannot see the identifier classes we added this week: bank account and government scheme numbers have no labels in the gold, so they score nothing either way. The evidence for those is the corpus scan in the row above, not this figure.

What the corpus scan supports and the sample does not: across every complaint in the demonstration slice, **no mobile number, Aadhaar number, PAN or non-government email address survives redaction**. That is a weaker claim than an accuracy figure, because it checks shapes we know how to look for rather than everything a human would catch. It is also the claim that holds at 55,544 rather than 89.

Per-class results matter more than the averages. Category accuracy of 71% spans 0.85 for police cases and 0.51 for social welfare. We report the spread, not the headline.

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

The same field yields 34,000 duplicates officers have already identified. We use those to measure ourselves. The number that matters is how many more we find, not how many we find in total.

None of this waits on document scanning.

**On the citizen text itself.** All four items read what people wrote, and what people write contains names, phone numbers and addresses. Historical complaint text is passed through the same redaction step the scanned documents use, before any matching or grouping runs over it. That runs first, not afterwards.

Redaction is not the whole answer. What the matching produces is still built from citizen writing, so those files stay on our own machines, are never sent to an outside provider, and sit under the same access rules as the original records.

### On the last two

The fourth is a design, not a running trial. No department has yet agreed which of its offices would go first.

The fifth is a measurement: does Sarvam read Odia better than we do? The scorecard is the deliverable and adoption follows the result. It runs on real grievances.

Three Sarvam models are relevant.

- **Sarvam Vision** reads scanned documents in 22 Indian languages including Odia. Two modes: reading a page as text at 50 paise, or pulling named fields out of it at ₹1. The second is what a grievance officer would actually use, and our pipeline has no equivalent.
- **Sarvam-105B** for categorisation and summarisation. Note that the model we originally costed, Sarvam-30B, has since been withdrawn, and its replacement is roughly twelve times the price. The whole comparison is still a few thousand rupees.
- **Transliteration**, converting Odia written in Roman letters into Odia script. Our pipeline does not solve this at all today.

Cost is a few hundred rupees for the whole comparison.

One caveat, and it is not the one we first wrote down. Sarvam state that Vision is trained on handwritten text across all 22 Indian languages, so handwriting is a supported case rather than an unmentioned one. What they do not publish is a handwriting-specific number: the headline benchmarks are general document scores, and they note accuracy is lower on highly stylised handwriting without saying by how much. Much of our corpus is handwritten. We will report handwritten and printed pages separately, because the split is the part nobody has measured.

A naming note, since the two are easy to conflate. Sarvam Akshar is the document digitisation platform; Sarvam Vision is the model underneath it. We benchmark the model, and the scorecard will say which surface was called.

## Schedule

**Table 3. Three weeks**

| Week | Delivered | Verified by |
|---|---|---|
| 27 to 31 July | Manual privacy labelling complete. System live on AWS. Pipeline run end to end | A grievance submitted in a browser returns a result. Counts reconcile at every pipeline step |
| 3 to 7 August | Privacy scorecard. Historical complaint text redacted. Spam and duplicate detection. Backlog duplication count. Management metrics, local issue themes, and spike view | Missed-PII rate by data type and language. Redaction pass completes over the chosen portion before any matching runs. Known repeat submissions found in a held-out sample. Each metric reconciles against source data. One real spike found and explained |
| 10 to 14 August | Sarvam scorecard. Experiment design and power calculation. Full benchmark table | Sarvam and our models compared on the same pages, as a paired sample with stated uncertainty. Every stage in Table 2 has a number **except text extraction, which has no reference to score against** |

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
| Correcting the 85 privacy pages | Reading and judging each one. Finishing this week |
| Transcribing a sample of scanned pages by hand | The only way to know whether Sarvam reads Odia better than we do is to have a correct transcription to compare both against. No such record exists |
| Choosing which portion of the backlog to demonstrate on | A judgement about what is defensible. It is also the starting gun for the overnight processing, which cannot begin until the portion is fixed |
| Fixing the A/B analysis plan | The estimator and the power calculation need statistical judgement |

**Resolved, and not in the direction we wanted.** No owner was named for the transcription sample, so on 7 August we took the written fallback rather than let the decision block the final week. The document-reading comparison reports how the two systems differ, with no verdict on which is right.

What that costs is narrower than it first appears. Only one of the three comparisons needed a transcriber. **Categorisation is measured against the category already recorded on each ticket**, and that reference costs nothing to assemble, so the benchmark still produces a real accuracy result — arguably the more decision-relevant one, since it is the number that would change how a grievance is routed.

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
