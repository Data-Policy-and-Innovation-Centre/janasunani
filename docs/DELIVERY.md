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

Eighty-five pages are prepared for manual privacy correction. That is the step that tells us how often redaction misses something.

## What we deliver on 14 August

**Table 1. The five components**

| Component | On the day |
|---|---|
| The DSI pipeline, rebuilt | A scanned grievance processed live, start to finish, with a table of how often redaction misses personal information, by data type and by language |
| Spam and duplicates | Junk flagged, repeat submissions linked, mass campaigns grouped as one issue, and a count of how much of the 1.37 million backlog is duplicated work |
| The intelligence layer | A supervisor screen with two measures the existing dashboards cannot produce: how much of the workload is the same problem reported repeatedly, and which local issues are new and growing. Plus a spike alert by district, and one finding on how cases are closed |
| A/B testing of the automation | The experiment design, the size of effect we could detect, and where the AI already agrees with officers today |
| Sarvam benchmark | Sarvam against our models on Odia, romanised Odia and English, with a switch between them |

### On the intelligence layer

We hold to one rule: if an analyst with database access could produce a number in a day, we do not present it as something the system enables.

Both measures clear that line. No query can tell you that two differently worded complaints describe the same problem, or that a hundred scattered complaints are one emerging issue.

One further item is a finding, not a capability, and we will say so. Officers close cases using a graded set of standard phrases: disposed, disposed with appropriate action, or disposed with the beneficiary benefited. **Roughly 60% of closures use the wording that claims no action.** The more specific wording was available and used half a million times. Two caveats: choosing the shorter phrase is evidence, not proof, that nothing was done; and this needs no machine learning, so we hand it over as a query the department can run itself.

The same field yields 34,000 duplicates officers have already identified. We use them to measure ourselves. The number that matters is how many more we find, not how many we find.

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

**Table 2. Three weeks**

| Week | Delivered | Verified by |
|---|---|---|
| 27 to 31 July | Manual privacy labelling begins. System live on AWS. Pipeline run end to end | A grievance submitted in a browser returns a result. Counts reconcile at every pipeline step |
| 3 to 7 August | Spam and duplicate detection. Backlog duplication count. Management metrics, local issue themes, and spike view | Known repeat submissions found in a held-out sample. Each metric reconciles against source data. One real spike found and explained |
| 10 to 14 August | Privacy scorecard. Sarvam scorecard. Experiment design and power calculation | Missed-PII rate by data type and language. Sarvam and our models compared on the same test data |

Work stops Thursday 13 August. Friday is rehearsal, no code changes.

Two dependencies set this order. Privacy labelling gates both the privacy scorecard and the Sarvam benchmark, and cannot be compressed later. Duplicate detection must precede the spike alerts, or a campaign of four hundred citizens about one road registers as a surge in road complaints.

If an item slips: demonstrate from a laptop rather than the server, report privacy results for English only, ship duplicate detection without spam scoring, show the Sarvam comparison for document reading alone.

## Not in scope for August

Running Sarvam's large model on our own hardware. Natural-language querying. Comparing offices against each other, which needs case-mix adjustment first. Replacing our models with Odia ones, which follows the benchmark.

## Decisions needed

**Table 3. From you**

| Decision | By |
|---|---|
| Whether the demonstration audience may see real citizen data, and under whose login | 7 August |
| Whether the closed-without-action finding is shown on 14 August or brought to you first. It reflects on the redressal process, not the software. We would report it at state level only, never office by office | 7 August |
| Whether any department has agreed in principle to an A/B trial later, which would let us present the design with a named partner | 14 August |
