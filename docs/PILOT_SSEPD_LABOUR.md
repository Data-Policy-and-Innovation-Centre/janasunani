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

**Scope: work delivered by December 2026.** The randomised design in
[AB_PLAN.md](AB_PLAN.md) §14 is deferred, and §14 stays the record of what
integration would buy. This document is what the team can actually do in four
months.

**Executive summary for the ED:** [PILOT_ED_BRIEF.md](PILOT_ED_BRIEF.md).

---

## 1. What this pilot is

Two departments have approved a bounded trial: Social Security & Empowerment of
Persons with Disabilities (SSEPD) and Labour & Employees' State Insurance
(Labour & ESI). **The deliverable is the case for integration, and it is due in
December 2026.**

Four constraints set that scope.

1. **OCAC has not approved integration.** Officers read our outputs on a
   separate screen and re-key what they choose into the legacy portal by hand.
2. **One grievance officer per department.** N = 2, and how many cases actually
   reach them personally is unknown.
3. **No engineer.** The build list sits on the principal, part-time, alongside
   design and government relations.
4. **No API access.** The read-only grievance API is not running and we hold
   no credentials for it. The extract ends 2025-07-30, so it is thirteen months
   stale over a period when volume roughly doubled year on year.

Constraints 3 and 4 take the console off the critical path. They do not stop it
being built. **The point of this plan is that the analysis and the process
mapping happen while the app becomes ready**, on separate tracks that neither
block nor wait on each other. December's deliverables come from the desk study
and the field work. The app matures alongside them toward a 2027 pilot, at
whatever pace part-time building allows.

**One measured number sets the ceiling.** Working outside the workflow, the only
cases we can take in natively are those that both originate at the department
and arrive on paper, because only then is the officer holding the document at a
moment they are already doing data entry. That is **1.8% of SSEPD and 2.9% of
Labour & ESI**, about five cases a week across both departments, and falling.
§2 has the year-by-year working.

Nothing reaches the bulk of the caseload without API access. That is the
December memo's argument.

**December ships three things**, none of which waits on the app. Two department
briefs, hand-delivered in November. One integration memo, written in December.
And honest measured numbers for our own pipeline, so that whatever we pilot in
2027 is something we have tested rather than something we hope works.

No causal citizen-outcome estimate. No officer-burden estimate. AB_PLAN §14.1
says why, and §11 says what would bring them back.

---

## 2. What the extract already tells us

Findings, not tasks. Everything below builds on them, and §1's ceiling is
derived here.

**Measured 2026-09-02 on `data/interim/complaints.parquet`. Mode splits the
caseload into two regimes. Crossed with origin, it gives the share we can reach
without API access.**

*Document-borne* = Physical, Letter, Joint Hearing, CM Weekly, CMO district
visits. *Dept origin* = `office == 'Departments'`. *Both* = the cases the
officer registers from paper in hand, and therefore the only ones we can take in
natively.

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

Three things to read off these.

**Reach without API access is 1.9% and 3.0% over four full years**, about five
cases a week across both departments.

**It is falling.** Labour & ESI went from 12.3% in 2022/23 to 1.1% in 2024/25,
driven by document-borne share halving from 36.3% to 19.9%. SSEPD's is flatter
but also declining. Any strategy built on the paper-at-our-door population is
betting against the trend.

**Origin and mode are negatively correlated.** SSEPD at 13.3% dept origin and
36.4% document-borne would give 4.8% if independent; actual is 1.8%. Labour &
ESI would give 9.7%; actual is 2.9%. Paper mostly arrives from elsewhere in the
chain rather than at the department's own door, which matches the 14 August meeting
note on physical grievances travelling a long manual route before
reaching the grievance officer.

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

Median days to resolution also varies four-fold by mode, from 16 (Joint Hearing)
to 60 (Twitter), with Website at 30 and Physical at 43. **Mode is therefore a
pre-treatment covariate the design has to carry** (AB_PLAN §14.4).

---

## 3. Workstream A: the desk study

Runs on the existing extract with existing code. Needs nothing from the
departments, no app and no travel. Start now.

**Ghazal owns this workstream** and works from Patna throughout; nothing here
depends on her travelling or on officer availability. **Milinda joins in
October** on A3 and A7, once she is off the process maps.

Each item serves a named December deliverable. Anything that served the
randomised design and nothing else is deferred.

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

A3 waits for the B2 process maps (§4) by design: gaps between recorded steps
cannot be read before we know what the steps are.

**A1. Department volume and case mix.** Filter the lake to `dept` in {SSEPD,
Labour & Employees' State Insurance}. Monthly filings, by district, by
category/subcategory, by `mode`, 2021 to 2025. Brief content, and the input to
A9. **Partly done: §2 has the measured mode and origin split.**

Indicative prior from the committed crosswalk
(`janasunani/routing/reference/routing_crosswalk.json`): SSEPD's largest entry
is `financial assistance` at support 7,020; Labour & ESI's largest is
`social welfare|identity card matter` at 1,451, and it appears at no
category-level or category-district-level key at all. Treat as a hypothesis A1
tests, not a finding.

**A2. Power calculation. Deferred to 2027.** It gated a randomised comparison
that is no longer in scope. When it runs, the denominator is throughput at the
officer rather than department volume: SSEPD showed 27 cases pending with the
department node against 1,471 pending in the department. Reuse
`janasunani/evaluation/stats.py` (Wilson intervals, cluster-robust sandwich with
the small-cluster t correction). Do not add scipy.

**A3. Turnaround baseline.** Run the existing marts restricted to the two
departments: `janasunani/analytics/sql/closure.sql` for `elapsed_days`, the
disposal ladder and `closure_two_day_bare`; `handoff.sql` for
`handoff_intervals` and `gap_days` by action type. Carry both caveats verbatim.
A gap is not idle time; it can include field enquiry, statutory waiting periods
and citizen response. And the action-history dedup index
(`janasunani/db/models.py:199-214`) excludes `action_taken_date`, so any
inter-step duration inherits an unsigned bias.

**A4. Repeat filers, and the lookup behind the question service.** Two outputs,
not one.

The rate, for the briefs: run `janasunani-dedup-index` scoped to the two
departments. Officers said in the 12 August field record that they do not know
their repeat-filing rate.

**The lookup, for answering officer questions** (§6): given a petitioner name,
mobile or ticket number, return that person's filing history and what happened
to each case.
Ghazal runs this by hand on request from October, so it needs to be queryable
rather than a finished statistic. Wanted in October, ahead of the briefs.

**A5. Who the officer sends the case to next.** The portal splits this into two
choices, and only one of them is a research question.

**The route.** A dropdown of fixed chains: DLO to Commissioner to Secretary, BDO
to Collector to Secretary, DSSO to Collector to Secretary, Self Assign, and so
on. These name ranks, not people. The list is stored in the portal as settings
and shown on the escalation admin screens. **Read it off. There is nothing to
predict here.**

**The actual office.** If the chain says DLO, which DLO? The dropdown holds
about thirty district Labour Officers. That choice appears nowhere in the
settings. It exists only in the history of past cases, in
`action_history.action_taken_by`.

So: for the first forward on each past case, count which office received it,
split by district and case type. The question is whether that choice is already
determined. If a Ganjam case nearly always goes to the same office, a suggestion
is a lookup and cheap to build. If it varies, find out what drives it before
suggesting anything.

Catch: `action_taken_by` is free text and is never joined to a list of valid
offices (`janasunani/analytics/sql/handoff.sql:31`). The same office will appear
spelled several ways, so it needs cleaning before any count means anything.

**A6. Ageing, and what it counts down to.** There is no statutory deadline
here. The officer types a resolution time freely at assignment, per case, so the
allowed time is a field in the record rather than a constant. Establish its
distribution for these two departments first: any ageing feature counts down to
that number, not to a fixed 30 days.

`escalation_date` is ingested, documented as an overdue date
(`janasunani/db/models.py:161`), and read by nothing downstream. Audit its
semantics against `created_on`, `resolved_on` and the typed resolution time.

**Now also a reconciliation job.** The department dashboard carries its own
overdue panel bucketed at within 7, more than 7, more than 15 and more than 30
days (SSEPD, 19 August). Our per-case ageing numbers have to agree with the
counts the officer already sees, or the feature loses trust on first contact.
Reconcile against that panel, not only against the raw fields.

**A7. Field-semantics audit.** Scoped to whatever makes the brief's numbers
defensible, rather than the full audit AB_PLAN §6 wanted for the experiment.
Validate the action taxonomy, whether `dept` is the assignment or the final
snapshot, whether `all_esc_user` is overwritten (action history holds no
chain snapshots, so overwrites are unrecoverable), and censoring. Censoring ran
at 34.4% in 2025 (`janasunani/experiments/routing_outcome/dataset.py:28-36`), so
any completion rate quoted in a brief has to say what it excludes.

**A8. Retrospective department report.** Package A1, A3, A4 and A6 as a short
brief per department. The officer's dashboard shows totals, pendings by
holder and overdue counts in 7/15/30-day buckets, but carries no history: no
movement over time, no comparison against the department's own past, no
repeat-filing rate. That is what these briefs add.
Aggregates only, no citizen text, no portal screenshots
(`docs/presentations/README.md`). Drafted by Ghazal and Milinda in late October,
hand-delivered by Utkarsh in mid-November. **This is the buy-in deliverable, it
costs no engineering, and it is one of the three things December ships.**

**A9. Reach without API access.** The origin-by-mode crossing in A1, kept
current and owned. It is the memo's headline number and it currently exists only
as an ad-hoc calculation. Extend it two ways: by district, since a department
whose paper arrives centrally differs from one where it arrives at the block;
and forward, to say where the trend lands in 2027 if document-borne share keeps
falling at the 2022-2025 rate.

**A10. Officer throughput.** How many cases actually reach the department
grievance officer per week. B1 asks them; the action history can estimate it.
Count cases where the department node appears as an action step, by week, and
compare against the 27-pending snapshot from the SSEPD dashboard.

This is the number that decides whether a randomised comparison is ever
possible, and it is also what tells us whether the question service will see five
requests or fifty. Do it early.

**A11. OCR sample selection, for C2.** Choose the scanned grievances that
Milinda and Aparupa transcribe. Stratify by department, mode and script, and
include the illegible ones rather than the clean ones, since a reference sample
of easy documents produces an accuracy figure that flatters us. Target size set
by what two people can transcribe alongside their other work. September, so C2
can start.

**A12. What staleness costs.** Estimate how many repeat filers the lookup will
miss, given the extract ends 2025-07-30 and volume roughly doubled year on year.
Turns the staleness caveat into a number, and gives ask 1 a price tag.

---

## 4. Workstream B: process mapping and field work

Everything we know about the workflow comes from two logins, the CM Grievance
Cell and Labour & ESI. They are different kinds of office and neither
generalises to the other. Annex B of the 12 August field record is a lower bound
on any dropdown's contents, never the schema. **SSEPD still needs its own Annex
B, and Labour & ESI's needs completing.** Work from
`outputs/Janasunani_Canonical_Questions_14Aug_Demo.docx`, the PII-free twin.
The 9.3 MB copy in `docs/` carries real citizen data in its Annex B.1 figures
and must not be quoted from or circulated. Both are gitignored (`docs/*.docx`),
so they exist in a working checkout and not in a fresh clone.

**B1. Department login walkthrough. Partly done; finish it in the first two
weeks of September.** Yashaswi and Milinda together on both calls. Milinda sits
in because she then owns the maps, and second-hand notes are a poor basis for a
process map.

Source: Labour & ESI session 18 August 2026 and SSEPD dashboard 19 August 2026,
in Box under `2. Projects/21. Governance/Grievance Redressal/Department
Summaries and Photos`, plus the Labour & ESI meeting summary of 14 August.
Screenshots stay in Box and are not copied into this repo.

**Established.**

- The grievance officer's account carries admin rights: workflow config, the
  escalation settings screens under `/Admin/Setting/Escalation`, and Janasunani
  Reports.
- It carries the **registration path** (`/Admin/Eabhijog/Register/regNext/...`):
  citizen details, then Assign ATA with department, `Define Workflow`, the named
  office at each level, category, subcategory and remarks. So the officer both
  receives and registers. **This fires the trigger AB_PLAN §14.2 set for itself**
  and is now a gate-1 input.
- `Define Workflow` is a fixed list of escalation chains, and the admin
  escalation table lists them per office. A5's chain half is a lookup.
- **Forward To**: designation, named assignee, a seven-item canned remark
  dropdown plus Other, free remark (500 characters on the subordinate path, 2000
  at assignment), and a jpg/png/pdf upload capped at 5 MB.
- **Action History** is on screen per case: action date, description (Assigned,
  Forwarded To Subordinate, Replied, Forward), sender, and who currently holds
  the case. Accept, Revert and Forward-to-subordinate are the reply actions.
- The dashboard carries mode counts, pendings split by holder, and an **overdue
  panel bucketed at 7, 15 and 30 days**. The "no time metric on any screen"
  finding came from the CM Grievance Cell login and does not hold here.
- Record-level reports exist, the Joint Hearing data report among them.
- Physical grievances arrive through a manual chain and OSWAS or e-Dispatch.
  There is no OSWAS integration for grievance handling.

**Still open, and these are what the September calls are for.** The first two
matter most, because both decide what 2027 can be.

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

**B2. Department-level process map. Largely in hand. September, weeks 2 to 3.
Milinda and Yashaswi, remote.** The August walkthroughs already cover the
department node in detail: how a case arrives, the Forward To screen, the fixed
escalation chains, the action history, the dashboard and its overdue buckets.
The work in September is consolidation rather than discovery. Write it up as one
map per department, every step from arrival to closure in order with the holder
named at each: read, decide downstream authority, free-type the resolution time,
write the remark, forward, wait, review the ATR, revert or close. Annotate each
step with how long they say it takes and what the record captures. Close the
open items listed in B1 on the same calls.

SSEPD's version is thinner than Labour & ESI's, since 19 August was a dashboard
session rather than a full walkthrough. Budget an extra call there.

Send both back for correction. A small specific ask that gets a reply, before we
ask for anything expensive. The corrected map is also the first thing we hand a
department that their portal cannot produce.

The only officer-time denominator anywhere in this project is "10 to 15 minutes
to turn a raw document into a registered complaint, irrespective of language"
(`scripts/create_officer_brief.py:81-84`). That is an intake number from a
different office. **It must not be reused as our baseline.** Measure our own.

**B3. Field-level process mapping. This is the actual gap, and it is back in
scope for 2026.** The department map ends the moment the officer forwards. What
the case meets after that we have never seen: the Collector's grievance cell,
the BDO office, and the department's own district officer, DLO for Labour & ESI
and DSSO for SSEPD.

Three things depend on it.

- **A3 is mostly measuring downstream time.** The gaps between recorded steps
  sit in the field, not at the department node. Without knowing what happens in
  them, an 11-day gap is a number with no interpretation.
- **A5's named-office question is decided in the field.** Which DLO gets a
  Ganjam case is a choice made against a reality we cannot see from the portal.
- **The memo claims where time goes.** Every such claim rests on the field
  level, and we have no basis for one today.

**Two districts, one per department, chosen on volume from A1.** Per site: how a
forwarded case actually arrives, who reads it, what enquiry it triggers, what an
ATR is in practice, how long each step takes, and what gets typed back into the
portal against what stays on paper.

**Yashaswi takes the first district on the October trip**, alongside the
department verification and B4. **Utkarsh takes the second from November**,
which is what having someone based in Odisha is for. If only one district
happens by December, that is still the first field evidence this project has.

**B4. Stopwatch baseline. October, on the one trip, Yashaswi.** Two to three
days per department. Observer with a stopwatch and a structured form. Per
grievance: seconds reading, seconds deciding, seconds on data entry, screens
touched, times the officer leaves the case and returns. Target 60 to 80 observed
cases per department, **stratified by mode**. A typed 217-character Website
complaint and a scanned Odia letter are different tasks; an unstratified average
over them describes neither (A1).

Two uses now, neither of them a console. It is the officer-time denominator the
project has never had, and it is the number that tells us what a future console
would have to beat: double entry into a separate screen costs an estimated two
to four minutes per case, so if handling time turns out to be eight minutes, a
console has to save a third of the work before the officer breaks even.

**B5. The data asks, in cost order. September, principal-owned.** The current
brief makes one data ask. It should be three, cheapest first, because the cheap
one unblocks the most and is not currently being made anywhere.

1. **A fresh extract to current date.** A one-time data request, not a service
   revival. It refreshes every analysis in Workstream A and is the only thing
   between us and a working repeat-filer capability. **Make it first and make it
   sound as small as it is.**
2. **A record-level export the officer can download.** Weekly download and
   upload. The only realistic route to anything showing current state, and
   cheap enough that an officer would actually do it. Confirm on the September
   calls whether one exists (B1).
3. **The read-only API revived, then scoped to these two departments**
   (`getGrievanceDetails`, `getGrievanceHistory`). The endpoint is not running,
   so this is revival plus credentials rather than credentials alone. Still far
   smaller than the integration OCAC refused.

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

## 5. Workstream C: the app becomes ready, in parallel

Runs alongside A and B. Gates neither. Its only December deliverable is a set of
measured numbers.

**We have not run the Sarvam pipeline at scale, and not on Odia at all.** No OCR
accuracy figure exists anywhere in this project. The December bar is honest
numbers, not a shippable product.

The document-borne third of SSEPD's caseload is where OCR and summarisation are
the only things that help, and it is the least measured part of the stack.

**C1. Run the Sarvam pipeline at scale** on a pilot-department slice, Odia
included. Produce the run and the list of what breaks. Owner: Yashaswi with
agents.

**C2. The OCR reference sample.** Hand-transcribe a bounded sample of scanned
grievances so an accuracy figure can exist. Issue #53, unowned since 7 August
2026. **Owners: Milinda and Aparupa.** Odia and English both.

**C3. Measure the summariser against C2**, Odia and English. Development
evidence: 55/84 critical facts retained, 8/26 usable without edit, 4/26 with
residual personal information, and it skipped all four coherent Odia cases. Odia
first.

**C4. Separate the PII gate from the DSI reference constant.**
`janasunani-evaluate-pii` exits non-zero because 78.3% coverage sits below the
80.56% constant at `janasunani/pipeline/pii_eval.py:31`, a reference every other
document says is not a target. Set a real threshold. **Do not relax it to make a
number pass**, and do not ship model output to an officer until it holds.

**C5. Write down what is showable.** One page: per stage, the measured number,
the sample, and whether it clears a bar we would defend in front of an officer.
Annex to the December memo.

Sarvam spend resumed 25 August 2026. Budget C1 and C3 before running them.

**C6. The app.** Cloud stack (#30, open since July, two rollout gaps in #32),
auth and RBAC in place of site-wide Caddy `basic_auth`, then the officer-facing
views. Behind C1 to C4. **No December deadline.** Readiness is assessed at the
§11 decision.

Not built in 2026: the assignment service and event tables. They support
randomisation only, and return with the design in AB_PLAN §14.

---

## 6. Answering officer questions by hand

Before building a repeat-filer panel, find out whether officers want the answer.

Officers send a ticket number or a petitioner detail. We return the filing
history within a day, by hand, from the extract. **Log every request**: what was
asked, what we returned, and whether it changed what the officer did.

**Split so neither half needs anyone in the room.** Utkarsh holds the channel
with the officer and owns the relationship. Ghazal runs the lookup from Patna
against her own A4 output and sends it back. The request is field-facing, the
answer is analytics.

Why this instead of a console:

- No build, no deployment.
- Two named officers saying they used something and want it in their workflow
  beats any memo we write.
- If they never ask, we know before building.
- The log is the demand evidence and the specification.

Starts on the October trip, where the channel is agreed face to face. Utkarsh
takes it over in November. Runs to December.

**State the staleness rather than letting them find it.** Until ask 1 lands,
lookups are blind to thirteen months of filings, so a petitioner who filed in
March 2026 shows as a first-time filer. Say so when the channel is agreed.

---

## 7. What we would build, and what each needs

Workstream C6 builds toward these at part-time pace; none of them ships to an
officer in 2026. This is the feature analysis the December memo argues from, and
the specification the question log will refine.

**The §1 ceiling applies uniformly to all of them.** A case has to be inside our
system before any feature can act on it, and without API access only the
register-from-paper cases get there. Every row below is capped at 1.8% of SSEPD
and 2.9% of Labour & ESI. Two further things then vary by feature: whether the
feature still means anything at that cap, and whether a thirteen-month-old
extract gives the right answer. They have different fixes. Only ask 3 moves the
cap; ask 1 fixes staleness on its own.

| Feature | What must cross into our system | Meaningful at 2% reach | Correct on data ending 2025-07-30 |
|---|---|---|---|
| Intra-department forwarding suggestion | District and subcategory, two fields | **Yes.** Acts per case, so the cap makes it fire about five times a week with each answer standing | **Yes.** Chains are portal configuration and readable; only the named office is learned (A5), and district-to-office assignment barely moves year to year |
| Repeat-filer and duplicate panel | Petitioner identity, already on the officer's screen | **Yes**, on the same terms | **No, until ask 1 lands.** The answer needs that petitioner's *complete* history |
| Document summary | The scanned file itself | **Yes**, on the same terms, though the officer downloads then uploads, roughly a minute per case | **Yes.** Staleness has no bearing on reading a document. Accuracy is unmeasured, which is C2 and C3 |
| Ageing and deadline view | The officer's **entire pending queue**, refreshed | **No.** It describes a population rather than a case, so at 2% we show one pending case in fifty and imply the rest do not exist | **No.** It lists what is pending now, so a one-time refresh does not help either. Needs ask 2 or 3 |

**Per-case features degrade gracefully; population views do not.** The first
three act on the case in front of the officer, which they are handling anyway,
so the cap makes them rare while each answer stays correct. The ageing view
needs the cases the officer is *not* handling, and there is no moment at which
anyone would enter those by hand. At 2% it is a wrong picture rather than a
small one, which is why the API decides that feature and nothing else does.

**Why the repeat-filer panel fails on the current extract.** The blind window is
thirteen months and grows by a month every month. At the 2024/25 filing rate it
holds about 27,000 SSEPD cases; at July 2025's rate, about 63,000. Against the
45,339 we hold, a majority of a petitioner's recent filing history is plausibly
invisible, and recency is where re-filing concentrates. The failure mode is a
confidently wrong answer rather than a degraded one: we would report "no prior
filings" for someone who filed twice last year. One such answer that the officer
catches ends the feature. **A12 puts a number on this, and it is the argument
for ask 1.**

**Low-signal triage stays excluded from SSEPD entirely.** A safety decision, and
the argument is in AB_PLAN §14.6. Nothing in the rescope touches it.

**Category suggestion** is no longer ruled out, since B1 disproved the
downstream-only reading. How much it is worth depends on how often the
registration path is used, which B1 still has to answer.

**The mode split bounds all of it.** 36.4% of SSEPD and 22.1% of Labour & ESI
arrive document-borne, where the text field holds an officer-typed stub of 18 to
58 characters and the grievance exists only in the scan (A1). Any feature
reading what the citizen wrote is blind there without the summary. Identity and
date features are not.

---

## 8. Month by month

Three tracks in parallel. A and B carry the December deliverables; C runs
alongside and is assessed rather than delivered.

| Month | A: analysis (Ghazal) | B: process and field | C: the app (Yashaswi) |
|---|---|---|---|
| **Sep** | A1, **A11** (so C2 can start), A4 lookup, A10 | B1 screen shares weeks 1-2, B2 department map written up weeks 2-3 (Milinda, Yashaswi). **B3 district sites chosen and access sought.** Three data asks tabled | C1 Sarvam run at scale. C2 reference sample begins (Milinda, Aparupa) |
| **Oct** | A6, A9, A12. A5 if time. Briefs drafted late | **One trip** (Yashaswi): department maps verified, B4 stopwatch, question channel agreed, **B3 first district**. Milinda on A3 and A7 | C2 continues, C3 summariser measured |
| **Nov** | A5. Briefs finalised | Utkarsh onboards. **Briefs hand-delivered.** **B3 second district.** Question service handed over | C4 PII gate. C6 as capacity allows |
| **Dec** | none | **The integration memo**, with the question log as evidence (Yashaswi) | C5 what is showable, as the memo's technical annex |

**Who travels.** Yashaswi in October, Utkarsh from November. Nobody else.
Ghazal is desk-only for the duration and Milinda's mapping is remote.

**The dependency that does exist.** A3 waits for the B2 department map, because
gaps between recorded steps cannot be read before we know what the steps are.
Its *interpretation* then waits for B3, since most of the elapsed time sits
downstream. A3 can therefore be computed in October and only stated with
confidence after the district visits. Everything else in A and B runs
independently of C.

---

## 9. People

| Who | Lane | Where |
|---|---|---|
| **Ghazal** (data analyst) | Workstream A: A1, A11, A4, A10 in September, then A6, A9, A12. A5 last. Runs the lookups from October. Drafts the briefs with Milinda. | Patna, desk-only |
| **Milinda** (RA) | B1 calls and the B2 department write-up in September. A3 and A7 from October, including the B3 field notes. C2 Odia reference sample with Aparupa. Drafts the briefs with Ghazal. | Remote |
| **Aparupa** (operations manager, Odia) | C2 reference transcription, and Odia support across C3. The reason an Odia accuracy figure is possible at all. | Odia-speaking |
| **Utkarsh** (PM, joins Nov) | Owns the field and the officer relationship from November. Delivers the briefs. **Takes the second B3 district**, which is the main reason to have someone based there. Holds the question channel. | Odisha |
| **Yashaswi** (principal) | Design, the three asks, government relations. B1 and B2 with Milinda. The October trip, including the first B3 district. Workstream C. The December memo. | Bangalore, one trip |

No dedicated engineer, so Workstream C moves at the pace one part-time person
with agents can manage. That is the reason C has no December deadline, and the
reason A and B were designed not to depend on it.

**#53 now has owners.** The hand-transcribed OCR reference sample, unowned since
7 August, sits with Milinda and Aparupa as C2. It was the single cheapest thing
blocking the 2027 options, because without it no OCR accuracy figure can exist
and the document summary cannot be assessed at all.

---

## 10. Risks

- **Ghazal is split across projects.** September is where this plan breaks,
  since she carries Workstream A alone while Milinda is on the department map.
  The fix is to cut A7 further rather than to slip the November delivery.
- **District access never comes through.** B3 needs entry to a Collector's
  grievance cell and a block office, which is a separate permission from the
  department sign-off and runs through a different chain. **Ask in September,
  not in October**, or the trip arrives with nowhere to go. Without B3 the
  memo's claims about where time goes have no basis and A3 stays a set of
  uninterpreted intervals.
- **No data ask lands.** The question service runs thirteen months blind for its
  whole life, the briefs describe a caseload that ends in July 2025, and the
  memo argues from stale evidence. Survivable if stated, corrosive if an officer
  discovers it first.
- **Officers never use the question channel.** That is a finding rather than a
  failure, and it is the cheapest possible way to learn it. It does mean the
  December memo rests on the briefs and the maps alone.
- **The October trip slips.** Everything field-side then lands on a new joiner's
  first six weeks. The briefs would still ship. The stopwatch baseline and the
  first district would not, and B3 would then depend entirely on Utkarsh
  managing two districts in his first two months.
- **Workstream C's numbers come back bad.** The likeliest single outcome, given
  4/26 residual PII on the summariser's development set and no OCR figure at
  all. It is why the December bar is measurement rather than a shippable
  product. A measured bad number is a result; an unmeasured stack in front of an
  officer is the embarrassment we are avoiding.
- **C absorbs the principal and A or B slips.** The tracks are independent by
  design, but they share one person at the top. If C starts eating September,
  C6 is what gives way, since it is the only item with no December deliverable.
- **Withdrawn claims stay withdrawn.** No routing time saving. Not the in-sample
  crosswalk numbers (60.9 / 67.5 / 72.8). 13.7 seconds is machine time, not
  officer time. 11 to 23 days is a measured gap, not a saving.

---

## 11. The 2027 decision, taken in December

Four questions, each answered by the work above.

| Question | Answered by | If no |
|---|---|---|
| Did API access or a bulk export land? | B5 | We reach 2% of the caseload, so a pilot is a demonstration rather than a service |
| Do officers want what we would build? | The question log | Build nothing; the memo stands on the briefs and the maps |
| Is the pipeline good enough to show someone? | C5 | Fix it before piloting, not during |
| Is the app ready, and is there anyone to finish it? | C6, and hiring | The pilot waits |

The randomised comparison in AB_PLAN §14 needs all four and an officer
throughput we have not yet measured. Treat it as a 2027-28 question rather than
a 2027 one.

**The memo does not depend on any of this going well.** Two department briefs, a
verified process map neither department has, measured numbers for our own stack,
and the 2% reach figure. A complete case for integration whether or not anything
ships.
