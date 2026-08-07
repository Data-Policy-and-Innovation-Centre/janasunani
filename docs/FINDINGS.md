---
title: What the grievance record says
subtitle: Five reproducible findings for the 14 August demonstration
author: Data, Policy and Innovation Centre
date: 7 August 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and University of Chicago Trust
status: Draft for ED clearance
---

# Reading this brief

This brief reports what can be established from structured complaint and action-history records without reading complaint narratives. Each finding is reproduced from exact, governed queries over the current snapshot. Counts describe the administrative record; they do not, by themselves, establish whether an individual decision was correct.

The sequence is deliberate: reproducible database insights come first. Two additional analytical capabilities—finding duplicate complaints missed by the manual process and decomposing a worked complaint surge—remain gated on their own acceptance evidence and are not claimed here.

# Insight 1 — What closure records say

**Number.** Of 776,922 complaints closed on one of six governed disposal templates, 472,782—**60.9%**—were closed on the rung that records no action. The same 472,782 complaints are **39.1%** of all 1,209,144 resolved complaints.

**Denominator.** The primary denominator is 776,922 exact matches to the six closure templates. The all-resolved denominator is reported separately so the 60.9% share is not mistaken for a share of every resolved complaint.

**Caveat.** This is descriptive, not a failure rate. A correct closure and a premature closure can look identical in this record. Establishing decision quality would require a separately governed hand review of 300–500 closures. The result should not be used as an office league table.

**Implication.** Closure language is useful for defining a bounded review population and improving how outcomes are recorded. It is not sufficient evidence for judging offices or individual decisions.

# Insight 2 — A bounded rapid-closure review set

**Number.** **8,974 complaints** were created and closed within two days on the bare-disposal rung. Of these, 1,020 followed the shortest recorded trajectory that reaches a disposal.

**Denominator.** The 8,974 complaints are **1.9%** of all 472,782 bare disposals and **0.7%** of all 1,209,144 resolved complaints.

**Caveat.** Two days is fast; it is not proof that a closure was wrong. The administrative history does not reveal whether the grievance was ineligible, informational, already settled, or inadequately handled.

**Implication.** The query creates a precise, manageable set for qualitative review. A sample can test whether faster closures need workflow changes without treating speed as misconduct.

# Insight 3 — Why complaints are discarded

**Number.** Eight exact, high-frequency officer templates account for the following action rows in the current snapshot:

| Reason family | Current action rows | Earlier reference | Change |
|---|---:|---:|---:|
| Details inadequate | 39,964 | 39,943 | +21 |
| Documents not attached | 29,029 | 29,029 | 0 |
| Case already taken up or taken up earlier | 21,117 | 19,904 | +1,213 |
| No specific grievance | 16,375 | 16,340 | +35 |
| Duplicate copy | 16,182 | 14,767 | +1,415 |
| Needs a policy decision first | 9,125 | 9,090 | +35 |
| Outside the grievance cell's purview | 8,472 | 8,455 | +17 |
| Address not given | 4,114 | 4,110 | +4 |

**Denominator.** These are action rows matching the eight governed template families exactly. They are not a partition of all complaints or all actions.

**Caveat.** The labels record administrative decisions, not ground truth about citizens or the merits of their grievances. Office practices may differ and must be audited before the labels are used for training or comparison.

**Implication.** The families separate distinct operational problems: missing information, repeat handling, policy dependence, and routing. That decomposition is more useful than treating every discard as spam.

# Insight 4 — The manual duplicate baseline

**Number.** The two exact duplicate-related discard families contain **37,299 officer-confirmed duplicate action rows**, up 2,628 from the earlier reference of 34,671.

**Denominator.** The count is over action rows matching the two governed duplicate families. It is a baseline for what the manual process already catches, not a count of unique duplicate clusters.

**Caveat.** Officer labels are incomplete and reflect local practice. They are validation evidence, not comprehensive ground truth.

**Implication.** Any automated duplicate capability should be assessed on the additional, reviewable matches it finds beyond this baseline. The increment must be reported separately and has not yet passed acceptance.

# Insight 5 — A routing baseline

**Number.** **8,472 action rows** use the exact template for a complaint judged outside the grievance cell's purview, 17 more than the earlier reference of 8,455.

**Denominator.** The count covers exact matches to the governed out-of-purview template; it is not a count of every potentially misrouted complaint.

**Caveat.** The record says where a complaint was judged not to belong. It does not identify the destination that handled it well, and it is not evidence that the grievance lacked merit.

**Implication.** This baseline can anchor a routing crosswalk that learns where complaints are sent. Outcome claims require separate evidence about what happened after transfer.

# Capabilities awaiting acceptance evidence

## Capability — Additional duplicate discovery

The planned MinHash analysis will report only the reviewable increment beyond the 37,299 officer-confirmed duplicate action rows. That acceptance run is not yet complete, so no increment is claimed here.

## Capability — Worked surge decomposition

The planned case study will separate volume, repeated submissions, routing changes, and recorded outcomes for one governed period. No result is claimed until its denominators and checks are fixed.

# Method and release controls

The five insights come from a scoped DVC stage over `complaints.parquet` and `action_history.parquet`. Queries open only declared tables, do not read complaint narratives, and publish aggregate counts only. Independent formulations reconcile headline totals and fail closed on disagreement or unexpected high-volume templates. This draft is for ED clearance; capability claims remain withheld pending acceptance evidence.
