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
4. **No live feed.** The grievance API is not running. The extract ends
   2025-07-30, so it is thirteen months stale over a period when volume roughly
   doubled year on year.

Constraints 3 and 4 take the console off the critical path. They do not stop it
being built. **The point of this plan is that the analysis and the process
mapping happen while the app becomes ready**, on separate tracks that neither
block nor wait on each other. December's deliverables come from the desk study
and the field work. The app matures alongside them toward a 2027 pilot, at
whatever pace part-time building allows.

**One measured number sets the ceiling on all of it.** Working outside the
workflow, the only cases we can take in natively are those that both originate
at the department and arrive on paper, because only then is the officer holding
the document at a moment they are already doing data entry. That intersection is
**1.8% of SSEPD's caseload (817 of 45,339) and 2.9% of Labour & ESI's (301 of
10,459)**. Over four full years it averages about five cases a week across both
departments, and it is shrinking: Labour & ESI's share fell from 12.3% in
2022/23 to 1.1% in 2024/25 as physical intake digitised.

So there is no version of this that reaches the bulk of the caseload without a
data feed. That is the argument the December memo makes, and it is now
arithmetic rather than assertion.

**December ships three things**, none of which waits on the app. Two department
briefs, hand-delivered in November. One integration memo, written in December.
And honest measured numbers for our own pipeline, so that whatever we pilot in
2027 is something we have tested rather than something we hope works.

No causal citizen-outcome estimate. No officer-burden estimate. AB_PLAN §14.1
says why, and §9 below says what would bring them back.

---

## 2. Workstream A: pre-pilot data analysis

Runs on the existing extract with existing code. Needs nothing from the
departments, no app and no travel. Start now.

**Ghazal owns this workstream** and works from Patna throughout; nothing here
depends on her travelling or on officer availability. **Milinda joins in
October** on A3 and A7, once she is off the process maps.

Ordered by what the November briefs need. A1, A4 and A6 are the brief's content
and come first. A3 waits for the process maps by design, because gaps between
recorded steps cannot be read before we know what the steps are. A5 feeds the
December memo rather than the briefs, so it can slip. **A2 is deferred to 2027**
along with the design it would have powered.

**A1. Department volume and case mix.** Filter the lake to `dept` in {SSEPD,
Labour & Employees' State Insurance}. Monthly filings, by district, by
category/subcategory, by `mode`, 2021 to 2025. Sizes the pilot and feeds A2.

**Measured, 2026-09-02, on `data/interim/complaints.parquet` (55,798 cases:
SSEPD 45,339, Labour & ESI 10,459). Mode splits the caseload into two regimes
that need different things from us.**

| | SSEPD | Labour & ESI |
|---|---|---|
| Citizen-typed (Website, Twitter, WhatsApp, Mobile, Email) | 63.6% | 77.9% |
| Document-borne (Physical, Letter, Joint Hearing, CM visits) | 36.4% | 22.1% |

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

**A4. Repeat filers and duplicates.** Run `janasunani-dedup-index` scoped to the
two departments. Officers told us in the 12 August field record that they do not
know their repeat-filer rate. This is both a pilot input and the best available
door-opener for the first department meeting.

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

**A7. Field-semantics audit.** The AB_PLAN §6 blocker, scoped to the pilot
slice. Validate the action taxonomy, whether `dept` is the assignment or the
final snapshot, whether `all_esc_user` is overwritten (action history holds no
chain snapshots, so overwrites are unrecoverable), and censoring. Censoring ran
at 34.4% in 2025 (`janasunani/experiments/routing_outcome/dataset.py:28-36`);
the pilot's follow-up window must be chosen with that in view.

**A8. Retrospective department report.** Package A1, A3, A4, A5 and A6 as a
short brief per department. The officer's dashboard shows totals, pendings by
holder and overdue counts in 7/15/30-day buckets, but carries no history: no
movement over time, no comparison against the department's own past, no
repeat-filing rate. That is what these briefs add.
Aggregates only, no citizen text, no portal screenshots
(`docs/presentations/README.md`). Drafted by Ghazal and Milinda in late October,
hand-delivered by Utkarsh in mid-November. **This is the buy-in deliverable, it
costs no engineering, and it is one of the three things December ships.**

---

## 3. Workstream B: process mapping and field visits

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

**B2. Officer-side process map, per department. September, weeks 2 to 4.
Milinda and Yashaswi, remote.** Every step from arrival at the department node
to closure, in order, with the person holding the case named at each one: read,
decide downstream authority, free-type the resolution time, write the remark,
forward, wait, review the ATR, revert or close. Annotate each step with how long
they say it takes and what the record captures.

**This runs first because the rest depends on it.** A3 reads gaps between
recorded steps, and those gaps mean nothing until we know what the steps are
operationally. The people who map the process are also the ones who write the
briefs and the memo.

Draft from the B1 screen shares, then send the maps back to the officers for
correction. That round is worth having on its own: it is a small specific ask
that gets a reply, it establishes the working relationship before we ask for
anything expensive, and a corrected process map is the first thing we hand a
department that their own portal cannot produce.

Verified in person on the October trip. Expect the drafts to be wrong in places.
Watching the work is how you find the steps nobody mentions on a call.

The only officer-time denominator anywhere in this project is "10 to 15 minutes
to turn a raw document into a registered complaint, irrespective of language"
(`scripts/create_officer_brief.py:81-84`). That is an intake number from a
different office. **It must not be reused as our baseline.** Measure our own.

**B3. Downstream field visits. Cut from 2026.** Two districts, the Collector's
grievance cell, a BDO office and the department's district officer. It exists to
interpret a resolution date at the end of the chain, which the deferred
randomised design needed and the December memo does not. A multi-day trip we
cannot afford in four months. Revisit in 2027 with the design it serves.

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
- Permission to log what officers ask us and what we return, which is what the
  concierge test records.
- **Re-confirmation of the 2026-07-27 research-exemption determination** is no
  longer urgent, because the citizen survey that prompted it is cut from 2026.
  Ask again before any citizen contact resumes. AB_PLAN §14.7.

---

## 4. Workstream C: the app becomes ready, in parallel

**This track runs alongside A and B and gates neither of them.** Ghazal's
analysis and Milinda's process maps need nothing from it, and it needs nothing
from them. Its only December deliverable is a set of measured numbers.

We have never run the pipeline end to end, and no OCR accuracy figure exists
anywhere in this project. Everything we would show an officer in 2027 rests on a
stack we have not measured. **The bar for December is honest numbers**, not a
shippable product: we may not like what we find, but we will know it and be able
to say what is showable.

This matters most for exactly the cases the mode split identified. The
document-borne third of SSEPD's caseload is where OCR and summarisation are the
only things that help, and it is the part of the stack we have measured least.

**C1. One clean end-to-end run** on a pilot-department slice. The pipeline's
stages are individually exercised and have never been run start to finish
(ROADMAP §"the pipeline has never run end to end"). Produce the run and the list
of what breaks. Owner: Yashaswi with agents.

**C2. The OCR reference sample.** Hand-transcribe a bounded sample of scanned
grievances so an accuracy figure can exist. This is issue #53, unowned since
7 August 2026, and it is the reason no OCR number appears anywhere.
**Owners: Milinda and Aparupa.** Odia and English both, because an
English-only figure would not describe the caseload.

**C3. Measure the summariser against C2**, on Odia as well as English. Current
development evidence is 55/84 critical facts retained, 8/26 usable without edit,
4/26 with residual personal information, and it skipped all four coherent Odia
cases. That last point makes an Odia measurement the priority rather than a
refinement.

**C4. Separate the PII gate from the DSI reference constant.**
`janasunani-evaluate-pii` exits non-zero because 78.3% coverage sits below the
80.56% constant at `janasunani/pipeline/pii_eval.py:31`, a reference every other
document says is not a target. Set a real threshold. **Do not relax it to make a
number pass**, and do not ship model output to an officer until it holds.

**C5. Write down what is showable.** One page: per stage, the measured number,
the sample it came from, and whether it clears a bar we would defend in front of
an officer. This is the annex to the December memo that says what a 2027 pilot
could actually put on a screen.

Sarvam spend resumed on 25 August 2026, so the paid path is available for C1 and
C3. Budget it explicitly rather than discovering the cost.

**C6. The app itself, at whatever pace part-time allows.** Cloud stack (#30,
open since July, with the two rollout gaps in #32), auth and RBAC in place of
site-wide Caddy `basic_auth`, then the officer-facing views. Sequenced behind
C1 to C4, because there is no point deploying a stack that serves output we have
not measured. **No December deadline and no December deliverable.** It is ready
when it is ready, and the 2027 decision in §10 is where its readiness is
assessed against everything else.

What this track deliberately does not build in 2026: the assignment service and
event tables, which exist only to support randomisation. They come back with the
design in AB_PLAN §14, not before.

---

## 5. The concierge test

Before building a repeat-filer panel, find out whether officers want the answer.

Officers send a ticket number or a petitioner detail. We return the filing
history within a day, by hand, from the extract. **Log every request**: what was
asked, what we returned, and whether it changed what the officer did.

**Split so neither half needs anyone in the room.** Utkarsh holds the channel
with the officer and owns the relationship. Ghazal runs the lookup from Patna
against her own A4 output and sends it back. The request is field-facing, the
answer is analytics.

Why this instead of a console:

- No build and no deployment, so it survives the capacity constraint.
- Two named officers saying they used something and want it inside their
  workflow is a better artifact for the integration ask than any memo we write.
- If they never ask, we have learned that for the price of a WhatsApp thread
  rather than three months of engineering.
- The request log is both the demand evidence and the specification, if we
  later build.

Starts on the October trip, where the channel is agreed face to face. Utkarsh
takes it over in November. Runs to December.

**State the staleness rather than letting them find it.** Until ask 1 lands,
lookups are blind to thirteen months of filings, so a petitioner who filed in
March 2026 shows as a first-time filer. Say so when the channel is agreed.

---

## 6. What we would build, and what each needs

Workstream C6 builds toward these at part-time pace; none of them ships to an
officer in 2026. This is the feature analysis the December memo argues from, and
the specification the concierge log will refine.

**The ceiling first.** Working outside the workflow, the cases we can take in
natively are those that both originate at the department and arrive on paper:
1.8% of SSEPD (817 cases) and 2.9% of Labour & ESI (301). About five a week
across both departments, and falling. Whatever we build, that is its reach until
a data feed exists. Every row below should be read against it.

| Feature | What must cross into our system | Feasible without integration |
|---|---|---|
| Repeat-filer and duplicate panel | Petitioner identity, already on the officer's screen | **Yes.** Matches an arriving case against history, so a snapshot suffices. Degraded by thirteen months until ask 1 lands |
| Intra-department forwarding suggestion | District and subcategory, two fields | **Yes.** Chains are portal configuration and readable; only the named office is learned (A5) |
| Document summary | The scanned file itself | **Marginal.** Officer downloads then uploads, roughly a minute per case, against a saving that depends on document length |
| Ageing and deadline view | The officer's **entire pending queue**, refreshed | **No.** It lists what is pending now. Human effort scales with queue size rather than case flow, so there is no workaround. Needs ask 1 or 2 |

**The ageing view is the one human effort cannot rescue**, and it was previously
ranked first in this document. Every other feature needs data about the case in
front of the officer, which they are already handling. This one needs data about
the cases they are not handling, and asking an officer to maintain a parallel
register by hand to receive help with bookkeeping is self-defeating.

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

## 7. Month by month

Three tracks in parallel. A and B carry the December deliverables; C runs
alongside and is assessed rather than delivered.

| Month | A: analysis (Ghazal) | B: process and field | C: the app (Yashaswi) |
|---|---|---|---|
| **Sep** | A1, A4, A6 | B1 screen shares weeks 1-2, B2 maps weeks 2-4 (Milinda, Yashaswi). Three data asks tabled | C1 end-to-end run. C2 reference sample begins (Milinda, Aparupa) |
| **Oct** | Finishes A6, starts A5. Briefs drafted late | **One trip** (Yashaswi): maps verified, B4 stopwatch, concierge channel agreed. Milinda on A3 and A7 | C2 continues, C3 summariser measured |
| **Nov** | A5. Briefs finalised | Utkarsh onboards. **Briefs hand-delivered.** Concierge handed over | C4 PII gate. C6 as capacity allows |
| **Dec** | none | **The integration memo**, with the concierge log as evidence (Yashaswi) | C5 what is showable, as the memo's technical annex |

**Who travels.** Yashaswi in October, Utkarsh from November. Nobody else.
Ghazal is desk-only for the duration and Milinda's mapping is remote.

**The dependency that does exist.** A3 waits for the B2 maps, because gaps
between recorded steps cannot be read before we know what the steps are.
Everything else in A and B runs independently of C.

---

## 8. People

| Who | Lane | Where |
|---|---|---|
| **Ghazal** (data analyst) | Workstream A end to end: A1, A4, A6, then A5. The analytics we need in hand before anything can be argued. Concierge lookups. Drafts the briefs with Milinda. | Patna, desk-only |
| **Milinda** (RA) | B1 calls and B2 process maps in September. A3 and A7 from October. C2 Odia reference sample with Aparupa. Drafts the briefs with Ghazal. | Remote |
| **Aparupa** (operations manager, Odia) | C2 reference transcription, and Odia support across C3. The reason an Odia accuracy figure is possible at all. | Odia-speaking |
| **Utkarsh** (PM, joins Nov) | Owns the field and the officer relationship from November. Delivers the briefs. Holds the concierge channel. | Odisha |
| **Yashaswi** (principal) | Design, the three asks, government relations. B1 and B2 with Milinda. The October trip. Workstream C. The December memo. | Bangalore, one trip |

No dedicated engineer, so Workstream C moves at the pace one part-time person
with agents can manage. That is the reason C has no December deadline, and the
reason A and B were designed not to depend on it.

**#53 now has owners.** The hand-transcribed OCR reference sample, unowned since
7 August, sits with Milinda and Aparupa as C2. It was the single cheapest thing
blocking the 2027 options, because without it no OCR accuracy figure can exist
and the document summary cannot be assessed at all.

---

## 9. Risks

- **Ghazal is split across projects.** September is where this plan breaks,
  since she carries Workstream A alone while Milinda is on the maps. The fix is
  to cut A7 further rather than to slip the November delivery.
- **No data ask lands.** The concierge test runs thirteen months blind for its
  whole life, the briefs describe a caseload that ends in July 2025, and the
  memo argues from stale evidence. Survivable if stated, corrosive if an officer
  discovers it first.
- **Officers never use the concierge channel.** That is a finding rather than a
  failure, and it is the cheapest possible way to learn it. It does mean the
  December memo rests on the briefs and the maps alone.
- **The October trip slips.** Everything field-side then lands on a new joiner's
  first six weeks. The briefs would still ship; the stopwatch baseline would
  not.
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

## 10. The 2027 decision, taken in December

Four questions, each answered by the work above.

| Question | Answered by | If no |
|---|---|---|
| Did a data feed land? | B5 | We reach 2% of the caseload, so a pilot is a demonstration rather than a service |
| Do officers want what we would build? | The concierge log | Build nothing; the memo stands on the briefs and the maps |
| Is the pipeline good enough to show someone? | C5 | Fix it before piloting, not during |
| Is the app ready, and is there anyone to finish it? | C6, and hiring | The pilot waits |

The randomised comparison in AB_PLAN §14 needs all four and an officer
throughput we have not yet measured. Treat it as a 2027-28 question rather than
a 2027 one.

**The memo does not depend on any of this going well.** Two department briefs, a
verified process map neither department has ever had, a measured account of what
our own stack can and cannot do, and an arithmetic argument that working outside
the workflow reaches 2% of the caseload. That is a complete case for integration
whether or not anything ships.
