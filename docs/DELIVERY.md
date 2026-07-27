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

The full grievance history is loaded and verified: 1,371,288 complaints and 6,556,171 action records, in a database and in an analytics copy that the demonstration reads from.

The document pipeline is complete. All six stages work: page classification, text extraction from scanned documents, removal of personal information, page-type filtering, summarisation, and categorisation. It has not yet been run start to finish in a single pass.

Live processing works. A grievance submitted through the web interface is processed by the real models and returned with its redacted text, category, summary and routing. The web interface is built and connected. Routing uses fixed rules for now; the learned version is later work.

The deployment automation is written and reviewed but has not been run. The system is not yet switched on at our AWS server.

Privacy measurement is in progress. Eighty-five pages are prepared for manual correction, which is the step that tells us how often redaction misses something.

## What we deliver on 14 August

**Table 1. The five components**

| Component | On the day |
|---|---|
| The DSI pipeline, rebuilt | A scanned grievance processed live, start to finish, with a table of how often redaction misses personal information, by data type and by language |
| Spam and duplicates | Junk flagged, repeat submissions linked, mass campaigns grouped as one issue, and a count of how much of the 1.37 million backlog is duplicated work |
| The intelligence layer | A supervisor screen with three to five management metrics, and an alert when complaints spike in a district |
| A/B testing of the automation | The experiment design, the size of effect we could detect, and where the AI already agrees with officers today |
| Sarvam benchmark | Sarvam against our models on Odia, romanised Odia and English, with a switch between them |

The fourth is a design rather than a running trial, since no department has yet agreed which of its offices would go first.

The fifth is a measurement. The question is whether Sarvam reads Odia documents better than we do. The scorecard is the deliverable and adoption follows the result. The benchmark runs on real grievances.

Three of their models are relevant. Sarvam Vision reads scanned documents in all 22 Indian languages including Odia, at 50 paise per page, which is the direct comparison against our current text extraction. Sarvam-30B is a general model we would test on categorisation and summarisation. Their transliteration service converts Odia written in Roman letters into Odia script, which is a problem our current pipeline does not solve at all. Cost is negligible at benchmark scale: the entire comparison is a few hundred rupees.

One caveat worth stating now. Sarvam's documentation describes their document model as strong on printed text, tables and layout, and does not mention handwriting. Much of our corpus is handwritten. The benchmark will report handwritten and printed pages separately, because a result that only holds for printed forms is a much smaller result than it first appears.

## Schedule

**Table 2. Three weeks**

| Week | Delivered | Verified by |
|---|---|---|
| 27 to 31 July | Manual privacy labelling begins. System live on AWS. Pipeline run end to end | A grievance submitted in a browser returns a result. Counts reconcile at every pipeline step |
| 3 to 7 August | Spam and duplicate detection. Backlog duplication count. Management metrics and spike view | Known repeat submissions found in a held-out sample. Each metric reconciles against source data. One real spike found and explained |
| 10 to 14 August | Privacy scorecard. Sarvam scorecard. Experiment design and power calculation | Missed-PII rate by data type and language. Sarvam and our models compared on the same test data |

Work stops Thursday 13 August. Friday is rehearsal, with no code changes.

Two dependencies set this order. Manual privacy labelling gates both the privacy scorecard and the Sarvam benchmark and cannot be compressed later, so it starts this week. Duplicate detection has to precede the spike alerts, because a campaign of four hundred citizens writing about the same road would otherwise register as a surge in road complaints.

If an item slips, the fallbacks are: demonstrate from a laptop rather than the server, report privacy results for English only, ship duplicate detection without spam scoring, and show the Sarvam comparison for document reading alone.

## Not in scope for August

Running Sarvam's large model on our own hardware. Natural-language querying of the data. Comparing offices against each other, which needs case-mix adjustment first. Replacing our models with Odia ones, which follows the benchmark.

## Decisions needed

**Table 3. From you**

| Decision | By |
|---|---|
| Whether the demonstration audience may see real citizen data, and under whose login | 7 August |
| Whether any department has agreed in principle to an A/B trial later, which would let us present the design with a named partner | 14 August |
