---
title: Janasunani
subtitle: Pilot plan, SSEPD and Labour & ESI
author: Yashaswi Mohanty
date: 1 September 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and the University of Chicago Trust
status: Internal
---

# Pilot plan: SSEPD and Labour & ESI

**This plan covers work due by December 2026.** The randomised design in
[AB_PLAN.md](AB_PLAN.md) §14 is deferred. That section remains the record of
what integration could make possible. This document describes the work the team
can do in the next four months.

**Executive summary for the ED:** [PILOT_ED_BRIEF.md](PILOT_ED_BRIEF.md).

---

## 1. What this pilot is

Two departments have approved a limited pilot: Social Security & Empowerment of
Persons with Disabilities (SSEPD) and Labour & Employees' State Insurance
(Labour & ESI). **The deliverable is evidence for integration, due in December
2026.**

Four constraints set that scope.

1. **OCAC has not approved integration.** Officers read our output on a
   separate screen and type whatever they choose back into the legacy portal.
2. **There is one grievance officer per department.** There are two officers in
   total, and we do not know how many cases reach them personally.
3. **There is no dedicated engineer.** The principal will build part time while
   also handling design and government relations.
4. **No API access.** The read-only grievance API is not running and we hold
   no credentials for it. The extract ends 2025-07-30, so it is thirteen months
   stale over a period when volume roughly doubled year on year.

Constraints 3 and 4 mean that the app does not control the schedule. They do not
stop us from building it. **The analysis and process mapping will happen while
the app is being prepared.** These tracks do not block or depend on each other.
The December deliverables come from the analysis and field work. The app will
continue toward a 2027 pilot as part-time capacity allows.

**One measured number sets the limit.** Without access to the workflow, we can
only take a case directly into our system when it both starts at the department
and arrives on paper. In that situation the officer already has the document
and is entering it. This is **1.8% of SSEPD and 2.9% of Labour & ESI**, or about
five cases a week across both departments. The share is falling. Section 2
shows the year-by-year breakdown.

Without API access, we cannot reach most of the caseload. The December memo will
explain why that matters.

**December's deliverables do not depend on the app.** They are two department
briefs, hand-delivered in November; an integration memo, written in December;
and measured results for our own pipeline. The results will show what we have
tested before any 2027 pilot.

We will not estimate effects on citizen outcomes or officer workload. AB_PLAN
§14.1 explains why, and §11 describes what would be needed later.

---

## 2. What the extract already tells us

These are findings, not tasks. The rest of the plan uses them, and the limit in
section 1 is calculated here.

**Measured on 2026-09-02 using `data/interim/complaints.parquet`.** Arrival mode
divides the caseload into two groups. Combining mode with the case's origin
shows the share we can reach without API access.

*Document-borne* means Physical, Letter, Joint Hearing, CM Weekly, or CMO
district visits. *Dept origin* means `office == 'Departments'`. *Both* means a
case is both document-borne and from the department. These are the cases an
officer registers from paper in hand, and therefore the only cases we can take
directly into our system.

**SSEPD**

| Jul-Jun year | all tickets | dept origin | document-borne | both | both % |
|---|---|---|---|---|---|
| Apr-Jun 2021 (stub) | 389 | 2.6% | 0.0% | 0 | 0.0% |
| 2021/22 | 2,079 | 10.7% | 23.8% | 3 | 0.1% |
| 2022/23 | 5,832 | 13.9% | 38.6% | 137 | 2.3% |
| 2023/24 | 6,932 | 16.4% | 41.1% | 168 | 2.4% |
| 2024/25 | 25,283 | 13.7% | 35.7% | 457 | 1.8% |
| Jul 2025 (stub) | 4,824 | 7.6% | 39.1% | 52 | 1.1% |
| **4 full years** | **40,126** | **14.0%** | **36.4%** | **765** | **1.9%** |
| whole sample | 45,339 | 13.3% | 36.4% | 817 | 1.8% |

**Labour & ESI**

| Jul-Jun year | all tickets | dept origin | document-borne | both | both % |
|---|---|---|---|---|---|
| Apr-Jun 2021 (stub) | 34 | 61.8% | 0.0% | 0 | 0.0% |
| 2021/22 | 459 | 45.8% | 27.9% | 36 | 7.8% |
| 2022/23 | 1,341 | 44.4% | 36.3% | 165 | 12.3% |
| 2023/24 | 2,534 | 46.9% | 19.1% | 35 | 1.4% |
| 2024/25 | 5,651 | 42.8% | 19.9% | 62 | 1.1% |
| Jul 2025 (stub) | 440 | 40.7% | 18.4% | 3 | 0.7% |
| **4 full years** | **9,985** | **44.2%** | **22.3%** | **298** | **3.0%** |
| whole sample | 10,459 | 44.1% | 22.1% | 301 | 2.9% |

The tables show three things.

**Over the four full years, reach without API access is 1.9% for SSEPD and 3.0%
for Labour & ESI.** Together, this is about five cases a week.

**The share is falling.** Labour & ESI went from 12.3% in 2022/23 to 1.1% in
2024/25. Over the same period, its document-borne share fell from 36.3% to
19.9%. SSEPD's decline is smaller but moves in the same direction. A plan based
on paper arriving at the department is therefore working against the trend.

**Origin and arrival mode are not independent.** If they were independent,
SSEPD's 13.3% department-origin share and 36.4% document-borne share would give
4.8% reach; the observed figure is 1.8%. For Labour & ESI, the independent
figure would be 9.7%, compared with an observed 2.9%. Paper usually enters the
system elsewhere in the chain, not at the department. This matches the 14
August meeting note that physical grievances travel through a long manual route
before reaching the grievance officer.

The regimes differ in what the record actually contains. In the citizen-typed
modes the text field holds the grievance: median length 217 characters on
Website, 271 on Twitter, 283 on Mobile. In the document-borne modes it holds a
stub an officer typed: median 31 characters on Physical, 22 on Joint Hearing, 18
on a CMO district visit, 58 on Letter. Those modes carry a scanned document
98-100% of the time. Twitter carries none at all.

The Odia shares confirm the split rather than contradicting it. The text field is
41% Odia on Twitter and 38% on Website, against 2.7% on Physical and 2.4% on
Joint Hearing. The paper cases are not more English; their text field is not the
grievance.

Median days to resolution also varies four-fold by mode: 16 for Joint Hearing,
60 for Twitter, 30 for Website, and 43 for Physical. **Any future experiment
must account for arrival mode before treatment** (AB_PLAN §14.4).

---

## 3. Workstream A: data analysis

This work uses the existing extract and code. It needs no department input, app,
or travel. It starts now.

**Ghazal owns this workstream** and works from Patna. It does not depend on her
travelling or on officer availability. **Milinda joins in October** for A3 and
A7, after completing the process maps.

Each item supports a specific December deliverable. Work that only supported the
randomised design is deferred.

| | Serves | Priority |
|---|---|---|
| A1 volume, case mix, mode | Briefs, and A9 | Done in part |
| A2 power | Nothing in 2026 | **Deferred** |
| A3 turnaround | Briefs, memo | After the B2 process maps (§4) |
| A4 repeat filers **and lookup** | Briefs, **the question service** | High, wanted in October |
| A5 forwarding patterns | Memo only | May slip to December |
| A6 promised time against actual | Briefs | Medium |
| A7 field semantics | Quality control on A1, A3, A4 | Scoped to that |
| A8 department briefs | **The November deliverable** | November |
| A9 reach without API access | **The memo's headline** | High |
| A10 officer throughput | Memo, and the 2027 decision | High |
| A11 OCR sample selection | Workstream C2 | September |
| A12 what staleness costs | The staleness caveat, and ask 1 | Low, quick |

A3 waits for the B2 process maps (§4). We cannot interpret gaps between recorded
steps until we know what happens in each step.

**A1. Department volume and case mix.** Filter the lake to `dept` in {SSEPD,
Labour & Employees' State Insurance}. Report monthly filings from 2021 to 2025
by district, category, subcategory, and `mode`. This supplies the briefs and A9.
**Partly done: section 2 already has the mode and origin split.**

The committed crosswalk suggests
(`janasunani/routing/reference/routing_crosswalk.json`) that SSEPD's largest
entry is `financial assistance` with support 7,020. Labour & ESI's largest is
`social welfare|identity card matter` with support 1,451. That entry does not
appear at either the category or category-district level. These are hypotheses
for A1 to test, not findings.

**A2. Power calculation. Deferred to 2027.** This supported a randomised
comparison that is no longer in scope. When it resumes, use officer throughput
as the denominator rather than department volume: SSEPD showed 27 cases
pending with the department node against 1,471 pending in the department. Reuse
`janasunani/evaluation/stats.py` (Wilson intervals, cluster-robust sandwich with
the small-cluster t correction). Do not add scipy.

**A3. Turnaround baseline.** Run the existing marts for the two departments:
`janasunani/analytics/sql/closure.sql` for `elapsed_days`, the disposal ladder,
and `closure_two_day_bare`; and `handoff.sql` for `handoff_intervals` and
`gap_days` by action type. Keep both caveats in the report. A gap is not
necessarily idle time: it can include a field enquiry, a statutory waiting
period, or a citizen response. Also, the action-history dedup index
(`janasunani/db/models.py:199-214`) excludes `action_taken_date`, so each
inter-step duration has an unknown bias.

**A4. Repeat filers and the lookup behind the question service.** This produces
two outputs.

For the briefs, run `janasunani-dedup-index` for the two departments. Officers
said in the 12 August field record that they do not know their repeat-filing
rate.

**The lookup, for answering officer questions** (§6): given a petitioner's name,
mobile number, or ticket number, return that person's filing history and the
outcome of each case. Ghazal will run it by hand when officers ask from
October. It needs to be queryable, but it does not need to be a finished
statistic. It is needed in October, before the briefs.

**A5. Where the officer sends the case next.** The portal represents this in two
ways. Only one requires analysis.

**The route.** The portal has a dropdown of fixed chains: DLO to Commissioner to
Secretary, BDO to Collector to Secretary, DSSO to Collector to Secretary, Self
Assign, and others. These are ranks, not people. The chains are stored in the
portal settings and shown on the escalation admin screens. **This is a lookup,
not a prediction problem.**

**The actual office.** If the chain says DLO, which DLO should receive the case?
The dropdown contains about thirty district Labour Officers, but the specific
choice is not in the settings. It appears only in the history of past cases, in
`action_history.action_taken_by`.

For the first forward on each past case, count which office received it by
district and case type. If a Ganjam case nearly always goes to the same office,
the suggestion is a simple lookup. If it varies, find out why before suggesting
anything.

Important limitation: `action_taken_by` is free text and is not joined to a list
of valid offices (`janasunani/analytics/sql/handoff.sql:31`). The same office
may appear with different spellings, so the values must be cleaned before we
count them.

**A6. Ageing and the deadline it uses.** There is no statutory deadline here.
When assigning a case, the officer enters a resolution time for that case. The
allowed time is therefore a field in the record, not a fixed value. First,
measure its distribution in the two departments. Any ageing feature must count
down to that value, not to a fixed 30 days.

`escalation_date` is ingested and documented as an overdue date
(`janasunani/db/models.py:161`), but no downstream code reads it. Check its
meaning against `created_on`, `resolved_on`, and the resolution time entered by
the officer.

**Also reconcile the results with the dashboard.** The department dashboard has
an overdue panel with buckets for within 7, more than 7, more than 15, and more
than 30 days (SSEPD, 19 August). Our per-case ageing counts must agree with what
the officer already sees. Compare against that panel, not only against the raw
fields.

**A7. Field-semantics audit.** Check only the fields needed to support the
briefs, rather than doing the full audit requested for the experiment in
AB_PLAN §6. Check the action taxonomy, whether `dept` records the assignment or
the final snapshot, whether `all_esc_user` is overwritten, and censoring. The
action history has no chain snapshots, so overwritten values cannot be recovered.
Censoring was 34.4% in 2025
(`janasunani/experiments/routing_outcome/dataset.py:28-36`). Any completion rate
in a brief must say what it excludes.

**A8. Retrospective department report.** Combine A1, A3, A4, and A6 into a
short brief for each department. The officer dashboard shows totals, current
holders, and overdue counts in 7/15/30-day buckets. It does not show change
over time, compare the department with its own past, or report repeat filings.
The briefs will add those views.

Use aggregates only: no citizen text and no portal screenshots
(`docs/presentations/README.md`). Ghazal and Milinda will draft the briefs in
late October. Utkarsh will hand-deliver them in mid-November. **This is the
buy-in deliverable, requires no engineering, and is one of December's three
deliverables.**

**A9. Reach without API access.** Maintain the origin-by-mode calculation from
A1. It is the memo's headline number and currently exists only as an ad hoc
calculation. Extend it by district, since paper arriving at a central office is
different from paper arriving at a block office. Also project where the number
will be in 2027 if the document-borne share keeps falling at its 2022-2025 rate.

**A10. Officer throughput.** Estimate how many cases reach the department
grievance officer each week. B1 asks the officers directly, and the action
history can provide an estimate. Count cases where the department node appears
as an action step, by week, and compare the result with the SSEPD dashboard's
snapshot of 27 pending cases.

This number will tell us whether a randomised comparison is possible and whether
the question service will receive five requests a week or fifty. Do it early.

**A11. OCR sample selection, for C2.** Choose the scanned grievances that
Milinda and Aparupa will transcribe. Stratify them by department, mode, and
script. Include illegible documents, not just clean ones; an easy sample would
make the accuracy look better than it is. Set the sample size according to what
two people can transcribe alongside their other work. Complete this in
September so C2 can start.

**A12. What staleness costs.** Estimate how many repeat filers the lookup will
miss because the extract ends on 2025-07-30 and volume roughly doubled year on
year. This turns the warning about stale data into a number and gives request 1
a clear justification.

---

## 4. Workstream B: process mapping and field work

Our workflow knowledge comes from two logins: the CM Grievance Cell and Labour &
ESI. They are different types of office, so neither login can stand for the
other. Annex B of the 12 August field record is only a lower bound for a
dropdown's contents; it is not the schema. **SSEPD still needs its own Annex B,
and Labour & ESI's needs completing.** Work from
`outputs/Janasunani_Canonical_Questions_14Aug_Demo.docx`, the PII-free twin.
The 9.3 MB copy in `docs/` carries real citizen data in its Annex B.1 figures
and must not be quoted from or circulated. Both are gitignored (`docs/*.docx`),
so they exist in a working checkout and not in a fresh clone.

**B1. Department login walkthrough. Partly done; finish it in the first two
weeks of September.** Yashaswi and Milinda will attend both calls. Milinda will
write the maps, so she needs to see the workflow herself rather than rely on
second-hand notes.

Source: Labour & ESI session 18 August 2026 and SSEPD dashboard 19 August 2026,
in Box under `2. Projects/21. Governance/Grievance Redressal/Department
Summaries and Photos`, plus the Labour & ESI meeting summary of 14 August.
Screenshots stay in Box and are not copied into this repo.

**What we have established:**

- The grievance officer's account carries admin rights: workflow config, the
  escalation settings screens under `/Admin/Setting/Escalation`, and Janasunani
  Reports.
- It carries the **registration path** (`/Admin/Eabhijog/Register/regNext/...`):
  citizen details, then Assign ATA with department, `Define Workflow`, the named
  office at each level, category, subcategory, and remarks. The officer therefore
  both receives and registers cases. **This confirms the trigger described in
  AB_PLAN §14.2** and is now a gate-1 input.
- `Define Workflow` is a fixed list of escalation chains, and the admin
  escalation table lists them for each office. The route part of A5 is therefore
  a lookup.
- **Forward To**: designation, named assignee, a seven-item canned remark
  dropdown plus Other, free remark (500 characters on the subordinate path, 2000
  at assignment), and a jpg/png/pdf upload capped at 5 MB.
- **Action History** is on screen per case: action date, description (Assigned,
  Forwarded To Subordinate, Replied, Forward), sender, and who currently holds
  the case. Accept, Revert, and Forward-to-subordinate are the reply actions.
- The dashboard carries mode counts, pendings split by holder, and an **overdue
  panel bucketed at 7, 15 and 30 days**. The "no time metric on any screen"
  finding came from the CM Grievance Cell login and does not hold here.
- Record-level reports exist, the Joint Hearing data report among them.
- Physical grievances arrive through a manual chain and OSWAS or e-Dispatch.
  There is no OSWAS integration for grievance handling.

**Still open. The September calls will answer these questions.** The first two
are most important because they affect the 2027 plan.

- **How often the registration path is actually used**, as against receiving
  already-routed cases. AB_PLAN §14.2 turns on this.
- **The arrival rate at the officer.** SSEPD showed 27 cases pending with the
  department node against 1,471 pending in the department. Throughput at the
  officer, not department volume, is the power denominator.
- Whether "Department * (Suggested by AI)" appears on their screen, and whether
  they ever act on it.
- **Whether any record-level report exports in bulk**, and in what format. This
  decides which rung of AB_PLAN §14.5 the pilot lands on.
- Closure templates, discard reasons and the revert modal at this login.

**B2. Department-level process map. Mostly complete. September, weeks 2 to 3.
Milinda and Yashaswi, remote.** The August walkthroughs cover the department
node in detail: how a case arrives, the Forward To screen, the fixed escalation
chains, the action history, the dashboard, and the overdue buckets. September's
work is to put this into one map per department. List every step from arrival to
closure, name the person holding the case, and record how long officers say it
takes and what the portal captures. The steps include reading the case, choosing
the downstream authority, entering the resolution time, writing the remark,
forwarding, waiting, reviewing the ATR, and reverting or closing. Close the
open B1 questions on the same calls.

SSEPD's version is thinner than Labour & ESI's, since 19 August was a dashboard
session rather than a full walkthrough. Budget an extra call there.

Send both maps back for correction before making any expensive request. The
corrected map is also the first useful thing we can give a department that its
portal does not provide.

The only officer-time denominator anywhere in this project is "10 to 15 minutes
to turn a raw document into a registered complaint, irrespective of language"
(`scripts/create_officer_brief.py:81-84`). That is an intake number from a
different office. **It must not be reused as our baseline.** Measure our own.

**B3. Field-level process mapping. This is the main gap and is back in scope for
2026.** The department map ends when the officer forwards the case. We have not
seen what happens next at the Collector's grievance cell, the BDO office, or the
department's district officer: DLO for Labour & ESI and DSSO for SSEPD.

Three parts of the plan depend on this work.

- **A3 mostly measures downstream time.** The gaps between recorded steps occur
  in the field, not at the department node. Without observing the work, an
  11-day gap has no clear meaning.
- **A5's named-office question is decided in the field.** The portal cannot show
  why a particular DLO receives a Ganjam case.
- **The memo needs evidence about where time goes.** We cannot make that claim
  without seeing the field process.

**Visit two districts, one per department, chosen by A1's filing volumes.** At
each site, record how a forwarded case arrives, who reads it, what enquiry it
triggers, what an ATR means in practice, how long each step takes, and what is
entered in the portal versus what remains on paper.

**Yashaswi will visit the first district on the October trip**, along with the
department verification and B4. **Utkarsh will visit the second from November.**
If only one district is completed by December, it will still be the project's
first field evidence.

**B4. Stopwatch baseline. October, on the one trip, Yashaswi.** Spend two to
three days per department. Use a stopwatch and a structured form. For each
grievance, record reading time, decision time, data-entry time, screens touched,
and how often the officer leaves and returns to the case. Observe 60 to 80 cases
per department, **stratified by mode**. A typed 217-character Website complaint
and a scanned Odia letter are different tasks; an average that mixes them is not
useful (A1).

This gives us the officer-time denominator that the project currently lacks. It
also tells us what a future console must beat. Entering the same information in
a separate screen is estimated to take two to four minutes per case. If total
handling takes eight minutes, the console must save at least one-third of that
time to break even.

**B5. Data requests, in order of cost. September, principal-owned.** There are
three requests, from cheapest to most expensive. The first is the easiest and
unlocks the most analysis.

1. **A fresh extract to the current date.** This is a one-time data request, not
   a service revival. It refreshes every analysis in Workstream A and is needed
   for a reliable repeat-filer lookup. **Submit this first.**
2. **A record-level export the officer can download.** The officer could
   download and we could upload it weekly. This is the only realistic way to
   keep current-state information updated without a system change. Confirm on
   the September calls whether one exists (B1).
3. **The read-only API revived, then scoped to these two departments**
   (`getGrievanceDetails`, `getGrievanceHistory`). The endpoint is not running,
   so this requires both revival and credentials. It is still much smaller than
   the integration OCAC refused.

Alongside these:

- Written department sign-off naming both departments for a bounded engagement.
- **District-level access for B3**, to a Collector's grievance cell and a block
  office in the two chosen districts. A separate permission running through a
  different chain, so it is asked for in September rather than assumed in
  October.
- Permission to log what officers ask us and what we return, which is what the
  question log records (§6).
- **Re-confirmation of the 2026-07-27 research-exemption determination** is no
  longer urgent, because the citizen survey that prompted it is cut from 2026.
  Ask again before any citizen contact resumes. AB_PLAN §14.7.

---

## 5. Workstream C: prepare the app in parallel

This work runs alongside A and B. It blocks neither. Its only December
deliverable is a set of measured results.

**We have not run the Sarvam pipeline at scale or on Odia.** This project has no
OCR accuracy figure yet. The December goal is an honest measurement, not a
finished product.

About one-third of SSEPD's cases arrive as documents. OCR and summarisation are
the only useful tools for these cases, and this is the least measured part of
the system.

**C1. Run the Sarvam pipeline at scale** on a slice from the two departments,
including Odia. Record the run and everything that breaks. Owner: Yashaswi with
agents.

**C2. The OCR reference sample.** Hand-transcribe a limited sample of scanned
grievances so that we can calculate accuracy. Issue #53 has been unowned since
7 August 2026. **Owners: Milinda and Aparupa.** Include both Odia and English.

**C3. Measure the summariser against C2** in Odia and English. Current
development evidence is 55/84 critical facts retained, 8/26 summaries usable
without editing, 4/26 with residual personal information, and all four coherent
Odia cases skipped. Start with Odia.

**C4. Separate the PII gate from the DSI reference constant.**
`janasunani-evaluate-pii` exits non-zero because 78.3% coverage is below the
80.56% constant at `janasunani/pipeline/pii_eval.py:31`. Every other document
describes that constant as a reference, not a target. Set a real threshold.
**Do not lower it just to make the number pass**, and do not show model output
to an officer until the threshold is met.

**C5. Record what is safe to show.** Write one page for the December memo that
gives, for each stage, the measured result, the sample, and whether it passes a
standard we would defend in front of an officer.

Sarvam spending resumed on 25 August 2026. Set a budget for C1 and C3 before
running them.

**C6. The app.** Complete the cloud stack (#30, open since July, with two
rollout gaps in #32), replace site-wide Caddy `basic_auth` with authentication
and role-based access control, and then build the officer-facing views. C6
depends on C1 to C4. **There is no December deadline.** Assess readiness at the
section 11 decision.

Do not build the assignment service or event tables in 2026. They support only
the randomised design and should return with that design in AB_PLAN §14.

---

## 6. Answering officer questions by hand

Before building a repeat-filer panel, first find out whether officers want this
information.

Officers send a ticket number or a petitioner detail. We return the filing
history within a day, by hand, using the extract. **Log every request:** what the
officer asked, what we returned, and whether it changed the officer's action.

**Divide the work so neither part requires everyone to be in the same place.**
Utkarsh will manage the officer relationship and the question channel. Ghazal
will run the lookup from Patna using her A4 output and send back the answer. The
request comes from the field; the answer comes from the analysis.

Why do this before building a console?

- There is no build or deployment work.
- Two officers using the answer and asking for it in their workflow is stronger
  evidence than a memo.
- If they never ask, we learn that before building anything.
- The log will show whether there is demand and what the tool needs to do.

Start this on the October trip, when we agree the channel with the officers in
person. Utkarsh takes it over in November. Continue it through December.

**Tell officers about the stale data.** Until request 1 is complete, the lookup
cannot see thirteen months of filings. A petitioner who filed in March 2026
could appear to be a first-time filer. Explain this when the channel is agreed.

---

## 7. What we would build, and what each needs

Workstream C6 will build toward these features as part-time capacity allows.
None will be delivered to an officer in 2026. This analysis supports the
December memo, and the question log will help refine the requirements.

**The section 1 limit applies to every feature.** A case must be in our system
before a feature can act on it. Without API access, only cases registered from
paper get there. Every feature is therefore limited to 1.8% of SSEPD and 2.9% of
Labour & ESI. Features then differ in two ways: whether they are still useful at
that coverage, and whether the thirteen-month-old extract gives the right
answer. Request 3 changes coverage; request 1 fixes stale data.

| Feature | What must cross into our system | Meaningful at 2% reach | Correct on data ending 2025-07-30 |
|---|---|---|---|
| Intra-department forwarding suggestion | District and subcategory, two fields | **Yes.** Acts per case, so the cap makes it fire about five times a week with each answer standing | **Yes.** Chains are portal configuration and readable; only the named office is learned (A5), and district-to-office assignment barely moves year to year |
| Repeat-filer and duplicate panel | Petitioner identity, already on the officer's screen | **Yes**, on the same terms | **No, until ask 1 lands.** The answer needs that petitioner's *complete* history |
| Document summary | The scanned file itself | **Yes**, on the same terms, though the officer downloads then uploads, roughly a minute per case | **Yes.** Staleness has no bearing on reading a document. Accuracy is unmeasured, which is C2 and C3 |
| Ageing and deadline view | The officer's **entire pending queue**, refreshed | **No.** It describes a population rather than a case, so at 2% we show one pending case in fifty and imply the rest do not exist | **No.** It lists what is pending now, so a one-time refresh does not help either. Needs ask 2 or 3 |

**Per-case features can remain useful at low coverage; population views cannot.**
The first three act on the case in front of the officer, which the officer is
already handling. They will be rare, but each answer can still be correct. The
ageing view needs the cases the officer is *not* handling, and nobody will enter
those cases by hand. At 2%, it gives a wrong picture rather than a small one.
That is why the API is essential for this feature.

**Why the repeat-filer panel fails on the current extract.** The missing period
is thirteen months long and grows by another month each month. At the 2024/25
filing rate it contains about 27,000 SSEPD cases; at July 2025's rate, about
63,000. Compared with the 45,339 cases we hold, much of a petitioner's recent
history may be missing. Repeat filings are likely to be recent. We could report
"no prior filings" for someone who filed twice last year. That is a confidently
wrong answer, not a weaker version of the right answer. One such answer that an
officer catches could end the feature. **A12 will quantify this and support
request 1.**

**Do not build low-signal triage for SSEPD.** This is a safety decision explained
in AB_PLAN §14.6. The rescope does not change it.

**Category suggestion** is still possible because B1 showed that the officer can
register cases. Its value depends on how often officers use the registration
path, which B1 still needs to establish.

**Arrival mode limits document-based features.** 36.4% of SSEPD and 22.1% of
Labour & ESI cases arrive as documents. For these cases, the text field contains
an officer-typed stub of 18 to 58 characters, while the grievance itself is in
the scan (A1). A feature that reads the citizen's words cannot work without the
document summary. Identity and date features do not have this limitation.

---

## 8. Month by month

Three tracks run in parallel. A and B produce the December deliverables. C runs
alongside and is measured, not delivered as a finished product.

| Month | A: analysis (Ghazal) | B: process and field | C: the app (Yashaswi) |
|---|---|---|---|
| **Sep** | A1, **A11** (so C2 can start), A4 lookup, A10 | B1 screen shares in weeks 1-2; write the B2 department maps in weeks 2-3 (Milinda, Yashaswi). **Choose B3 district sites and request access.** Submit the three data requests. | C1 Sarvam run at scale. C2 reference sample begins (Milinda, Aparupa) |
| **Oct** | A6, A9, A12. A5 if time. Draft the briefs late in the month. | **One trip** (Yashaswi): verify the department maps, run the B4 stopwatch study, agree the question channel, and visit the **first B3 district**. Milinda starts A3 and A7. | C2 continues. Measure the summariser in C3. |
| **Nov** | A5. Finalise the briefs. | Utkarsh joins. **Hand-deliver the briefs.** Visit the **second B3 district.** Hand over the question service. | C4 PII gate. C6 as capacity allows. |
| **Dec** | None. | **Write the integration memo**, with the question log as evidence (Yashaswi). | C5 records what can be shown, as the memo's technical annex. |

**Travel.** Yashaswi travels in October and Utkarsh is in Odisha from November.
No one else travels. Ghazal works at her desk throughout, and Milinda maps the
process remotely.

**The dependencies.** A3 waits for the B2 department map because we cannot
interpret gaps between recorded steps until we know what happens in each step.
Its *interpretation* then waits for B3 because most of the elapsed time is
downstream. We can calculate A3 in October, but we should state it confidently
only after the district visits. Everything else in A and B runs independently of
C.

---

## 9. People

| Who | Lane | Where |
|---|---|---|
| **Ghazal** (data analyst) | Workstream A: A1, A11, A4, and A10 in September; then A6, A9, and A12; A5 last. Runs the lookups from October and drafts the briefs with Milinda. | Patna, desk-only |
| **Milinda** (RA) | B1 calls and the B2 department write-up in September. A3 and A7 from October, including B3 field notes. C2 Odia reference sample with Aparupa. Drafts the briefs with Ghazal. | Remote |
| **Aparupa** (operations manager, Odia) | C2 reference transcription and Odia support for C3. | Odia-speaking |
| **Utkarsh** (PM, joins Nov) | Owns the field and officer relationship from November. Delivers the briefs. **Visits the second B3 district** and holds the question channel. | Odisha |
| **Yashaswi** (principal) | Design, the three requests, government relations, B1 and B2 with Milinda, the October trip including the first B3 district, Workstream C, and the December memo. | Bangalore, one trip |

There is no dedicated engineer, so Workstream C will move at the pace one
part-time person with agent support can manage. That is why C has no December
deadline, and why A and B do not depend on it.

**#53 now has owners.** Milinda and Aparupa own the hand-transcribed OCR
reference sample as C2. It was the cheapest item blocking the 2027 options:
without it, there is no OCR accuracy figure and the document summary cannot be
assessed.

---

## 10. Risks

- **Ghazal is split across projects.** September is the pressure point because
  she carries Workstream A while Milinda works on the department map. If needed,
  reduce A7 further rather than delay the November delivery.
- **District access never comes through.** B3 needs entry to a Collector's
  grievance cell and a block office, which is a separate permission from the
  department sign-off and runs through a different chain. **Ask in September,
  not in October**, or the trip arrives with nowhere to go. Without B3 the
  memo's claims about where time goes have no basis and A3 stays a set of
  uninterpreted intervals.
- **No data request is approved.** The question service remains blind to
  thirteen months of filings, the briefs describe a caseload ending in July
  2025, and the memo relies on stale evidence. We can state this openly, but it
  weakens the case if an officer discovers it first.
- **Officers never use the question channel.** That is a finding, not a failure,
  and it is the cheapest way to learn whether there is demand. The December memo
  would then rely on the briefs and maps alone.
- **The October trip is delayed.** The briefs could still be delivered, but the
  stopwatch baseline and first district visit would not happen. B3 would then
  depend on Utkarsh managing two districts during his first two months.
- **Workstream C produces poor results.** This is plausible given the 4/26
  residual-PII result on the summariser's development set and the lack of an OCR
  result. That is why December asks for measurement, not a finished product. A
  poor measured result is useful; an unmeasured system shown to an officer is
  not.
- **C takes time from A or B.** The tracks are independent, but they share the
  principal. If C begins to delay the September work, C6 should give way because
  it is the only item without a December deliverable.
- **Withdrawn claims remain withdrawn.** This includes routing-time savings,
  in-sample crosswalk figures (60.9 / 67.5 / 72.8), 13.7 seconds of machine
  time as officer time, and 11 to 23 days as a promised saving.

---

## 11. The 2027 decision, taken in December

Four questions, each answered by the work above.

| Question | Answered by | If no |
|---|---|---|
| Did API access or a bulk export arrive? | B5 | We reach 2% of the caseload, so a pilot is a demonstration rather than a service |
| Do officers want what we would build? | The question log | Build nothing; the memo relies on the briefs and maps |
| Is the pipeline good enough to show an officer? | C5 | Fix it before piloting, not during the pilot |
| Is the app ready, and is there someone to finish it? | C6 and hiring | Wait before starting the pilot |

The randomised comparison in AB_PLAN §14 needs all four answers and a measured
officer throughput. We do not have that throughput yet, so treat the comparison
as a 2027-28 question rather than a 2027 question.

**The memo does not require every part of the plan to succeed.** It will contain
two department briefs, a verified process map that neither department currently
has, measured results for our own system, and the 2% reach figure. That is enough
to make the case for integration, whether or not the app ships.
