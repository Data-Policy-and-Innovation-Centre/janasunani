---
title: Janasunani
subtitle: Delivery plan for the 14 August demonstration
author: Yashaswi Mohanty
date: 27 July 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and the University of Chicago Trust
status: Internal
---

## Where we stand

All five components you asked for will be in the demonstration on 14 August. Three of them will be working software that you can click through. The other two will be a design and a measurement, and I would rather explain now than on the day why that is the right answer for those two.

There are fifteen working days left and one engineer building this. The system is also not yet switched on at our own cloud server, which was already outstanding before this conversation. Those two facts govern everything below. There is no version of this plan where all five components become polished software by the middle of August, so the useful question is which three, and what the other two should be instead.

One point of clarification, since it affects how the demonstration should be read. Everything runs on DPIC's own AWS machines in Mumbai. Nothing is deployed to government infrastructure and nothing will be unless the demonstration is approved, at which point a vendor takes it over and builds the production system. What we are showing is a working prototype and the evidence that the approach is sound, not a system waiting to be switched on for the state. That distinction matters for the two components below that are a design and a measurement rather than software, because a prototype's job is to establish whether something is worth building properly.

## What you will see

**Table 1. The five components on 14 August**

| Component | What we demonstrate |
|---|---|
| The DSI pipeline, rebuilt | A scanned grievance goes in. A redacted, categorised, routed grievance comes out. Live, start to finish, with a table showing how often the privacy step misses something |
| Spam and duplicates | The system flags likely junk, links repeat submissions from the same citizen, and groups mass campaigns as a single issue. Plus a count of how much of the 1.37 million backlog is duplicated work |
| The intelligence layer | A supervisor screen with a handful of management numbers we can defend, and an alert when complaints spike in a district |
| A/B testing of the automation | The experiment design, a calculation of how large an effect we could actually detect, and a first look at where the AI already agrees with officers |
| Sarvam benchmark | Sarvam against our current models on Odia, romanised Odia and English, side by side, with a switch that lets us move between them |

The fourth item is a design rather than a running trial for a straightforward reason. A real experiment needs officers using the system and a department that has agreed which of its offices go first. Neither can be arranged in three weeks, and a demonstration of a fake experiment would be worth less than an honest design. The design is also the part that takes judgement. Building the software to log an experiment is a week's work; deciding what we are measuring, on whom, and how we would know we were wrong is the part worth showing you.

The fifth is a measurement by its nature. Sarvam is not a feature we are adding, it is a question we are answering: is their Odia document reading better than ours, and by how much. The scorecard is the deliverable. If it says yes, adopting it is the next phase of work and not this one.

## The plan

**Table 2. Three weeks**

| Week | What lands | How we know it worked |
|---|---|---|
| 27 to 31 July | Manual privacy labelling begins. The system goes live on our AWS server. The pipeline runs end to end once | A grievance submitted in a browser, on the server, returns a result. Counts reconcile at every step of the pipeline |
| 3 to 7 August | Spam and duplicate detection. The backlog duplication count. Three to five management metrics and one spike view | Known repeat submissions are found in a held-out sample. Each metric reconciles against the source data. One real spike found and explained |
| 10 to 14 August | Privacy scorecard. Sarvam scorecard. Experiment design and power calculation | Missed-PII rate reported by data type and by language. Sarvam and our models compared on the same test data |

Work stops on Thursday 13 August. Friday is a rehearsal with no code changes. I would ask you to hold that line even if something looks nearly finished on the Thursday, because the failure mode for a demonstration is not an incomplete feature, it is a broken one.

The privacy labelling in week one is the item I want to flag. It is a person reading grievance pages and correcting what the system marked as personal information, and it cannot be automated, because automating it would mean the system grades its own homework. It gates two of the five components: we cannot report how well redaction works without it, and we cannot judge Sarvam against us without it. It is also the only task on the list that more effort in the final week cannot rescue. It starts this week.

One dependency is worth knowing about because it is not obvious. Duplicate detection has to come before the spike alerts. If it does not, a campaign of four hundred citizens writing about the same road looks identical to a genuine surge in road complaints, and the first alert we show you would be wrong.

## What we are leaving out

We are not running Sarvam's large model on our own hardware in August. We use their hosted service for the benchmark and keep self-hosting as a proven fallback, which matters because their models are openly licensed and we can therefore always walk back from sending data to a third party.

We are not building natural-language querying of the data. It is the most impressive-sounding thing on the roadmap and the least trustworthy, and it belongs after the basic numbers are ones we would defend in a meeting.

We are not comparing offices against each other. Doing that fairly requires adjusting for the fact that harder cases take longer wherever they land, and doing it unfairly would be worse than not doing it at all. It is a real capability and it needs a statistical review first.

We are also not putting Odia models through the whole pipeline. The Sarvam benchmark is what tells us which ones are worth adopting. Adoption follows the evidence rather than preceding it.

## What I need from you

**Table 3. Decisions**

| Decision | Needed by | What happens without it |
|---|---|---|
| Written terms from Sarvam on whether our data trains their models, how long they hold it, and how it is deleted | 5 August | The benchmark runs on synthetic data only, which is a much weaker result |
| Who formally records that this evaluation does not require ethics review | Before the experiment design is circulated | Almost certainly exempt, but it should be a named authority's decision rather than an engineering assumption |
| Whether the demonstration audience may see real citizen data, and under whose login | 7 August | Determines whether we demonstrate on real or synthetic grievances |
| Whether any department has agreed in principle to an A/B trial later | Useful by 14 August | A design with a willing partner is a far stronger story than a design alone |

## What could go wrong

The labelling takes longer than I expect. This is the likeliest problem and it weakens two components at once. The mitigation is to start immediately and to report partial results by language rather than waiting for a complete set, so we can say what we know about English even if Odia is still in progress.

The first deployment to our AWS server goes badly. First deployments usually do. If it comes to it we demonstrate from a laptop running exactly the same code, which is a weaker story but not a broken one.

Sarvam's terms do not arrive in time. I am preparing the benchmark on synthetic documents in parallel so that a comparison exists either way.

The Odia results come back poor. This is a real possibility and I want to say in advance that it is a finding rather than a failure. Our current models were built and measured on English by a team that has since disbanded, and nobody has ever measured how they perform on Odia. If the answer is that they perform badly, that is the argument for the next phase of investment, and it is better to learn it from a benchmark in August than from a district officer in November.

Finally, one engineer means there is no slack anywhere in this schedule. Every item above has something we would show instead if it slips. Where that trade has to be made I will make it in favour of the date, and I will tell you which one I made.
