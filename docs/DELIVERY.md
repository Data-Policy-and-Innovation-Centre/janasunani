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

Manual privacy correction of eighty-five pages finishes this week. That is the step that tells us how often redaction misses something, and it gates both the privacy scorecard and the Sarvam benchmark.

## What we deliver on 14 August

**Table 1. The five components**

| Component | On the day | Commitment | If it slips |
|---|---|---|---|
| The DSI pipeline, rebuilt | A scanned grievance processed live, start to finish, with a table of how often redaction misses personal information, by data type and by language | **Committed** | Demonstrate from a laptop rather than the server. Report redaction results for English only |
| Spam and duplicates | Repeat submissions linked and mass campaigns grouped as one issue, across a defined portion of the backlog, with a count of how much of it is duplicated work. Complaints written in Odia script and in Roman letters are matched separately, not against each other | **Bounded** | Duplicates without spam scoring. A smaller portion of the backlog |
| The intelligence layer | Duplicate-adjusted workload. One worked example of a spike separated into filings, distinct problems and distinct citizens. Local issue themes for one category. The closure finding | **Bounded** | Themes drop first. If duplicate detection slips, only the closure finding survives, since it is the one item needing no new processing |
| A/B testing of the automation | The experiment design, the size of effect we could detect, and where the AI already agrees with officers today | **Framework only** | None needed. Nothing here depends on new engineering |
| Sarvam benchmark | Sarvam Vision against our text extraction on a transcribed sample, with handwritten and printed pages reported separately | **Bounded** | Document reading only. Comparing Sarvam on categorisation and summarisation, and the switch between providers, are stretch |

**Committed** means we will demonstrate it. **Bounded** means we will demonstrate it on a defined slice rather than the full 1.37 million. **Framework only** means a design and a calculation, not running software.

The portion of the backlog is not yet fixed. We will settle it after a brainstorming session and name it here, whether that is one district, one year or one category. Vague now is honest; vague on 14 August would not be.

### Benchmark record

**Table 2. Historical reference and current measurement**

| Stage | Earlier figure, and what it was measured on | 14 August |
|---|---|---|
| Personal information removal | 80.6% of items found, on a 106-sentence English validation split | Re-measured on our corrected 85-page set, by data type and by language |
| Text extraction from scans | 77.9% of pages passed three plausibility checks, on 96,469 English pages. Not transcription accuracy: there was no ground truth | Measured against a deliberately transcribed sample, handwritten and printed separately, against Sarvam Vision |
| Duplicate detection | Not attempted | Recall against the 34,000 duplicates officers have already identified |
| Page type | 67% accurate, on the earlier team's own 1,500-page sample | Historical context only. No labelled set exists for August |
| Category assignment | 71% accurate, on the earlier team's train/test split | Historical context only, unless a held-out labelled set is identified in week 1 |
| Summarisation | 1.9 of 3 for usefulness, scored by one reviewer over 500 pages | Historical context only. Re-scoring needs a blinded human review we have not scheduled |

The first three rows are measurements we will produce. The last three are the earlier team's numbers, reported so the record is complete, and we are not claiming to have re-measured them. Committing otherwise would be promising evidence the plan contains no step to produce.

The earlier figures come from different samples, different splits and almost entirely English text. They are historical reference, not a target, and several are not like-for-like with anything we will measure. Where we come in lower we will say so and say why.

Per-class results matter more than the averages. Category accuracy of 71% spans 0.85 for police cases and 0.51 for social welfare. We report the spread, not the headline.

### On the intelligence layer

The existing dashboards read the complaint record: district, department, category, dates, status. They count and compare anything in it, and they do that well. Two things they cannot do are read what the citizen actually wrote, and recognise that two records describe the same problem. Everything below comes from one of those two abilities.

We hold to one rule. If an analyst with database access could produce a number in a day, we do not present it as something the system enables.

**1. Duplicate-adjusted workload.** How much of the backlog is one problem arriving more than once. The portal counts filings. This counts problems. It requires matching complaints with the same or near-identical wording across a corpus of 1.37 million, which no query does. Matching two complaints that describe the same problem in genuinely different words is a harder task and is not part of August.

**2. Local issue themes.** Which problems are growing in one place rather than everywhere. We group complaints by what they are about rather than by the category assigned at intake, then look for themes that are both concentrated in one area and rising. Concentrated and new is the alert worth acting on.

**3. Spikes with the cause attached.** A rise in complaints can mean four hundred citizens about one road, or two hundred unrelated problems. Those need opposite responses and are identical in a count. Every spike carries three numbers: filings, distinct problems, distinct citizens. Detecting the spike is ordinary. Telling the two cases apart is not.

**4. How cases are closed.** Officers close using a graded set of standard phrases: disposed, disposed with appropriate action, or disposed with the beneficiary benefited. Of the roughly 792,000 complaints closed with one of those standard phrases, **61% use the wording that claims no action**, while the more specific wording was available and used 311,000 times.

The denominator matters. That 61% is of complaints closed on a standard disposal phrase, which is about two thirds of all resolved complaints. Measured against every resolved complaint the figure is 39%, because a third close on some other wording entirely.

This one needs care for a second reason too. Sometimes no action is the correct outcome: an information request answered, an ineligible claim properly refused, a matter already settled elsewhere. A correct closure and a premature one look identical in the record, so the figure is a description and not a verdict.

Whether the citizen returns is a useful signal here, and it needs the same-issue matching from point 1, so it is a real capability. But it identifies cases worth reviewing rather than cases decided wrongly. Citizens sometimes return after a correct refusal, and often do not return after a bad one. It narrows where to look. It does not settle what happened.

Establishing what share of no-action closures were genuinely premature needs a few hundred cases read and adjudicated by hand. That is not August work, and we will not present the figure as though it were.

The 61% itself needs no machine learning. We hand it over as a query the department can run for itself.

The same field yields 34,000 duplicates officers have already identified. We use those to measure ourselves. The number that matters is how many more we find, not how many we find in total.

None of this waits on document scanning.

### On the last two

The fourth is a design, not a running trial. No department has yet agreed which of its offices would go first.

The fifth is a measurement: does Sarvam read Odia better than we do? The scorecard is the deliverable and adoption follows the result. It runs on real grievances.

Three Sarvam models are relevant.

- **Sarvam Vision** reads scanned documents in 22 Indian languages including Odia, at 50 paise per page. Direct comparison against our text extraction.
- **Sarvam-30B**, tested on categorisation and summarisation.
- **Transliteration**, converting Odia written in Roman letters into Odia script. Our pipeline does not solve this at all today.

Cost is a few hundred rupees for the whole comparison.

One caveat. Sarvam documents their vision model as strong on printed text, tables and layout, with no mention of handwriting. Much of our corpus is handwritten. We will report handwritten and printed pages separately, because a result that holds only for printed forms is a much smaller result than it appears.

## Schedule

**Table 3. Three weeks**

| Week | Delivered | Verified by |
|---|---|---|
| 27 to 31 July | Manual privacy labelling complete. System live on AWS. Pipeline run end to end | A grievance submitted in a browser returns a result. Counts reconcile at every pipeline step |
| 3 to 7 August | Privacy scorecard. Spam and duplicate detection. Backlog duplication count. Management metrics, local issue themes, and spike view | Missed-PII rate by data type and language. Known repeat submissions found in a held-out sample. Each metric reconciles against source data. One real spike found and explained |
| 10 to 14 August | Sarvam scorecard. Experiment design and power calculation. Full benchmark table | Sarvam and our models compared on the same test data. Every stage in Table 2 has a number |

Work stops Thursday 13 August. Friday is rehearsal, no code changes.

Two dependencies set this order. Privacy labelling gates both the privacy scorecard and the Sarvam benchmark, which is why it finishes this week rather than running alongside. Duplicate detection must precede the spike view, because a campaign of four hundred citizens about one road is a genuine surge and should be reported as one. What duplicate detection adds is the ability to say whether a surge is four hundred people on one issue or four hundred separate problems, which are different facts requiring different responses.

Labelling finishing this week lets the privacy scorecard move to week two, which takes the final week down to two items. That matters, because the last week also carries rehearsal.

## How the three weeks are worked

Three kinds of work, and they scale differently.

**Building runs in parallel.** Most of the code is self-contained enough to be written alongside itself rather than one piece after another: the duplicate-matching logic, the spam checks, the closure query, the metric definitions, the supervisor screen, the Sarvam connector. Each is tested on its own before it touches real data. This is where the single-engineer constraint is least binding.

**Processing the full history runs overnight.** Building the duplicate index and computing the themes are hours-long jobs over more than a million records. They are scheduled early and left to run rather than squeezed into the final days.

**Judgement cannot be parallelised, and that is what sets the schedule.** Four things need a person:

| Work | Why it takes the time it takes |
|---|---|
| Correcting the 85 privacy pages | Reading and judging each one. Finishing this week |
| Transcribing a sample of scanned pages by hand | The only way to know whether Sarvam reads Odia better than we do is to have a correct transcription to compare both against. No such record exists |
| Choosing which portion of the backlog to demonstrate on | A judgement about what is defensible, not a technical choice |
| Fixing the A/B analysis plan | The estimator and the power calculation need statistical judgement |

**One request.** The transcription sample has no owner yet, and it sits on the final week's path. It needs perhaps fifty pages, printed and handwritten, transcribed by someone who reads Odia. If we can identify that person in the coming week, the Sarvam comparison reports accuracy. If not, it reports a narrower result: how the two systems differ from each other, without a verdict on which is right.

## Not in scope for August

Running Sarvam's large model on our own hardware. Natural-language querying. Comparing offices against each other, which needs case-mix adjustment first. Replacing our models with Odia ones, which follows the benchmark.

## Decisions needed

**Table 4. From you**

| Decision | By |
|---|---|
| Whether the demonstration audience may see real citizen data, and under whose login | 7 August |
| Whether the closed-without-action finding is shown on 14 August or brought to you first. It reflects on the redressal process, not the software. We would report it at state level only, never office by office | 7 August |
| Whether any department has agreed in principle to an A/B trial later, which would let us present the design with a named partner | 14 August |
| Who can hand-transcribe roughly fifty scanned pages in Odia and English. Without it the Sarvam comparison has no correct answer to measure against | 31 July |
