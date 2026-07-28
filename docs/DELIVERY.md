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

The document pipeline is complete. All six stages work: page classification, text extraction, removal of personal information, page-type filtering, summarisation, categorisation. Not yet run start to finish in one pass.

Live processing works. A grievance submitted through the web interface comes back with its redacted text, category, summary and routing. The interface is built and connected. Routing uses fixed rules; the learned version is later work.

Deployment automation is written and reviewed but not yet run. The system is not switched on at our AWS server.

Manual privacy correction of eighty-five pages finishes this week. That is the step that tells us how often redaction misses something, and it gates both the privacy scorecard and the Sarvam benchmark.

## What we deliver on 14 August

**Table 1. The five components**

| Component | On the day |
|---|---|
| The DSI pipeline, rebuilt | A scanned grievance processed live, start to finish, with a table of how often redaction misses personal information, by data type and by language |
| Spam and duplicates | Junk flagged, repeat submissions linked, mass campaigns grouped as one issue, and a count of how much of the 1.37 million backlog is duplicated work |
| The intelligence layer | A supervisor screen with three things the existing dashboards cannot produce: how much of the workload is one problem arriving repeatedly, which local issues are new and growing, and whether a spike is one campaign or many separate problems. Plus one finding on how cases are closed |
| A/B testing of the automation | The experiment design, the size of effect we could detect, and where the AI already agrees with officers today |
| Sarvam benchmark | Sarvam against our models on Odia, romanised Odia and English, with a switch between them |

### Benchmark record

Every stage is scored against the DSI clinic's numbers, so the demonstration leaves a comparable record rather than a claim.

**Table 2. Before and after**

| Stage | DSI clinic | 14 August |
|---|---|---|
| Text extraction from scans | 77.9% of pages passed all three quality checks, English only | Same checks on Odia and English, plus Sarvam head to head |
| Personal information removal | 80.6% of items found | Re-measured on our own labelled set, by data type and by language |
| Page type | 67% accurate | Re-measured, per page type |
| Category assignment | 71% accurate | Re-measured, per category |
| Summarisation | 1.9 of 3 for usefulness on text pages | Re-measured on the same scale |
| Duplicate detection | not attempted | Recall against 34,000 duplicates officers already identified |

The DSI figures were measured on their own samples and almost entirely on English. They are a starting point, not a target. Where we come in lower we will say so and say why, since the earlier numbers do not cover Odia at all.

Per-class results matter more than the averages here. Category accuracy of 71% spans 0.85 for police cases and 0.51 for social welfare. We report the spread, not the headline.

### On the intelligence layer

The existing dashboards read the complaint record: district, department, category, dates, status. They count and compare anything in it, and they do that well. Two things they cannot do are read what the citizen actually wrote, and recognise that two records describe the same problem. Everything below comes from one of those two abilities.

We hold to one rule. If an analyst with database access could produce a number in a day, we do not present it as something the system enables.

**1. Duplicate-adjusted workload.** How much of the 1.37 million backlog is one problem arriving more than once. The portal counts filings. This counts problems. It requires matching complaints written in different words across three scripts, which no query does.

**2. Local issue themes.** Which problems are growing in one place rather than everywhere. We group complaints by what they are about rather than by the category assigned at intake, then look for themes that are both concentrated in one area and rising. Concentrated and new is the alert worth acting on.

**3. Spikes with the cause attached.** A rise in complaints can mean four hundred citizens about one road, or two hundred unrelated problems. Those need opposite responses and are identical in a count. Every spike carries three numbers: filings, distinct problems, distinct citizens. Detecting the spike is ordinary. Telling the two cases apart is not.

**4. How cases are closed.** Officers close using a graded set of standard phrases: disposed, disposed with appropriate action, or disposed with the beneficiary benefited. **Roughly 60% use the wording that claims no action**, while the more specific wording was available and used half a million times.

This one needs care, for two reasons.

Sometimes no action is the correct outcome. An information request answered, an ineligible claim properly refused, a matter already settled elsewhere. A correct closure and a premature one look identical in the record, so the 60% is a description and not a verdict.

What separates them is whether the citizen returns. If nothing was owed, they stop. If something was owed and refused, they reopen or file again. Linking a new filing to an earlier closure needs the same-issue matching from point 1, so that figure is a real capability and it is the one we will lead with. It understates the problem, because people also give up, so it should be read as a floor rather than a rate.

The 60% itself needs no machine learning. We hand it over as a query the department can run for itself.

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

Two dependencies set this order. Privacy labelling gates both the privacy scorecard and the Sarvam benchmark, which is why it finishes this week rather than running alongside. Duplicate detection must precede the spike alerts, or a campaign of four hundred citizens about one road registers as a surge in road complaints.

Labelling finishing this week lets the privacy scorecard move to week two, which takes the final week down to two items. That matters, because the last week also carries rehearsal.

If an item slips: demonstrate from a laptop rather than the server, report privacy results for English only, ship duplicate detection without spam scoring, show the Sarvam comparison for document reading alone.

## Not in scope for August

Running Sarvam's large model on our own hardware. Natural-language querying. Comparing offices against each other, which needs case-mix adjustment first. Replacing our models with Odia ones, which follows the benchmark.

## Decisions needed

**Table 4. From you**

| Decision | By |
|---|---|
| Whether the demonstration audience may see real citizen data, and under whose login | 7 August |
| Whether the closed-without-action finding is shown on 14 August or brought to you first. It reflects on the redressal process, not the software. We would report it at state level only, never office by office | 7 August |
| Whether any department has agreed in principle to an A/B trial later, which would let us present the design with a named partner | 14 August |
