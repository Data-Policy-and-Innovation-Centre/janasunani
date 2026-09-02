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

**Statistical design:** [AB_PLAN.md](AB_PLAN.md) §14 is authoritative for the
estimand, the estimator, power, outcome capture and governance. This document is
the operational half: what we measure beforehand, who we visit, what we build,
who does it, and when. It does not restate §14 and must not contradict it.

**Executive summary for the ED:** [PILOT_ED_BRIEF.md](PILOT_ED_BRIEF.md).

---

## 1. What this pilot is

Two departments have approved a bounded trial: Social Security & Empowerment of
Persons with Disabilities (SSEPD) and Labour & Employees' State Insurance
(Labour & ESI). The question is whether automation reduces administrative burden
and improves citizen outcomes.

Three constraints define it:

1. **OCAC has not approved integration.** Our system cannot sit inside the
   legacy workflow. Officers read our outputs and re-key what they choose into
   the legacy portal by hand.
2. **One grievance officer per department.** N = 2.
3. **Thin staffing.** A project manager joins around November 2026, one existing
   RA is reallocated, and the principal is in Bangalore with travel minimised.

**The primary track is feasibility and descriptive evidence**: Workstream A,
Workstream B, and a four-week shadow phase with the panel shown on every case
and no arms. That track starts now, does not depend on officer adoption, and is
the evidence that unlocks the integration ask.

**The randomised measurement of officer burden is a second, gated track.** It
runs only if both gates in AB_PLAN §14.9 pass: the A2 power calculation at end
September 2026, and measured re-key fidelity at end January 2027. On a fail, the
primary track runs unchanged and no arm comparison is reported.

Neither track delivers a causal citizen-outcome estimate. AB_PLAN §14.1 says
why.

---

## 2. Workstream A: pre-pilot data analysis

Runs on the existing 1.37M-row corpus with existing code. Needs nothing from the
departments, no app and no travel. Start now. RA-owned.

**A1. Department volume and case mix.** Filter the lake to `dept` in {SSEPD,
Labour & Employees' State Insurance}. Monthly filings, by district, by
category/subcategory, by `mode`, 2021 to 2025. Sizes the pilot and feeds A2.

Indicative prior from the committed crosswalk
(`janasunani/routing/reference/routing_crosswalk.json`): SSEPD's largest entry
is `financial assistance` at support 7,020; Labour & ESI's largest is
`social welfare|identity card matter` at 1,451, and it appears at no
category-level or category-district-level key at all. Treat as a hypothesis A1
tests, not a finding.

**A2. Power calculation.** Per AB_PLAN §14.4. Grievance level, officer-week
blocks, Fisher randomization inference. Due end September. Two decisions ride on
it: whether Labour & ESI is randomisable at all, and how wide the citizen
endpoints will be. Reuse `janasunani/evaluation/stats.py` (Wilson intervals,
cluster-robust sandwich with the small-cluster t correction). Do not add scipy.

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
(`docs/presentations/README.md`). Target mid-November 2026, hand-delivered on
the first field trip. **This is the buy-in deliverable and it costs no
engineering.**

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

**B1. Department login walkthrough. Partly done; finish it (September).**

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

**Still open, and these are what the September calls are for.**

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

**B2. Officer-side process map, per department.** Every step from arrival at the
department node to closure, in order, with the person holding the case named at
each one: read, decide downstream authority, free-type the resolution time,
write the remark, forward, wait, review the ATR, revert or close. Annotate each
step with how long they say it takes and what the record captures.

The only officer-time denominator anywhere in this project is "10 to 15 minutes
to turn a raw document into a registered complaint, irrespective of language"
(`scripts/create_officer_brief.py:81-84`). That is an intake number from a
different office. **It must not be reused as our baseline.** Measure our own.

**B3. Downstream field visits, one trip, November 2026.** The department officer
forwards; someone else acts. Without seeing that end we cannot interpret a
resolution date.

- Two districts, chosen from A1 as one high-volume and one low-volume for SSEPD.
- Per district: the Collector's grievance cell, one BDO office, and the
  department's district officer (District Social Security Officer for SSEPD,
  District Labour Officer and ESI for Labour).
- Instruments: the Annex-B click walkthrough repeated at each level, plus a
  short semi-structured interview on what arrives from the department, what is
  missing when it arrives, and what causes a revert.
- Purpose beyond description: if reverts are driven by ATR quality at block
  level, a better department-node summary cannot move a citizen outcome. We want
  to know that before we claim one.

**B4. Time-and-motion baseline, November, before the console exists.** Two to
three days per department. Observer with a stopwatch and a structured form. Per
grievance: seconds reading, seconds deciding, seconds on data entry, screens
touched, times the officer leaves the case and returns. Target 60 to 80 observed
cases per department. This makes the handling-time outcome interpretable, feeds
the A2 variance input, and is the fallback estimate if console telemetry
disappoints.

**B5. Permissions and governance, September onward, principal-owned.** These are
asks 2, 3 and 4 of the 17 August ED brief, so present them as that brief's
follow-through rather than as new requests.

- Written department sign-off naming both departments for a bounded trial.
- Permission for officers to use a DPIC-hosted tool alongside the portal, and
  for us to log what is suggested and what they do.
- **The read-only API revived, then scoped to these two departments**
  (`getGrievanceDetails`, `getGrievanceHistory`). The endpoint is not running, so
  this is revival plus credentials, not credentials alone. Still far smaller than
  the integration OCAC refused. It is the difference between a real citizen
  endpoint and a survey-only pilot, **and it is what feature 1 is blocked on**.
- Permission for the citizen follow-up call, with a data-sharing note.
- **Re-confirmation of the 2026-07-27 research-exemption determination**, which
  was made about administrative-record analysis and does not obviously extend to
  a prospective citizen survey. AB_PLAN §14.7.

---

## 4. Interventions, ranked by prior bang for buck

Ranked for this audience: a department grievance officer who both receives and
registers grievances, works scanned Odia documents, and already has aggregate overdue
counts on the dashboard but no per-case list behind them.

Both departments have already been shown a Janasunani 2.0 feature list
(auto-categorisation, similar-case detection, severity and sentiment, document
AI, department prediction, reopening). Labour & ESI keeps a ten-point wishlist
on the wall: reminders at 7 days, escalation at 15, a separate tab for reverted
grievances, section mapping, ATRs in chronological order, ATR notification to
citizens, and duplicate or bulk petitions. **Features 1 and 2 answer items on
that list.** Present them that way rather than as something new.

| Intervention | Needs | Feasibility | Binding constraint |
|---|---|---|---|
| 1. Ageing and deadline view | A3, A6, **live feed** | High to build, blocked to run | No model risk, but it lists what is pending *now*. The API is dead and our extract is a snapshot. Blocked on B5's revival ask |
| 2. Repeat-filer and duplicate panel | A4 | High | Nothing outside our control: it matches an arriving case against history, so the historical extract suffices. `dedup_signatures` / `dedup_groups` held out of the lake pending Phase 18 RBAC; read OLTP directly for two officers |
| 4. Intra-department authority suggestion | A5 | High | The escalation chains are configured in the portal and readable. Only the named-office choice has to be learned |
| 3. Document summary and key-fact extraction | OCR reference sample (#53), unowned | Low | 4/26 residual PII on the development set. Not showable to an officer as it stands |
| 5. Low-signal triage | Excluded from SSEPD | Not built | Safety decision, AB_PLAN §14.6 |
| 6. Category suggestion | | Not built | Officers said category does not change routing |

Numbering follows the tiers below, which carry the argument.

**Tier 1, build and test.**

1. **Ageing and deadline view.** Pending cases oldest first, days elapsed, the
   resolution time typed at assignment, days left against it. No model and no
   model risk. The dashboard already gives the aggregate overdue counts; what it
   lacks is the per-case list behind them, which is what an officer needs to act
   on a specific case. Reconcile against the portal's own buckets (A6).

   **Blocked on a live feed, and this is the binding constraint on the whole
   feature.** The view shows what is pending today. We hold a one-time
   historical extract, the API that would refresh it is not running, and no
   other refresh path exists. Cheapest feature to write, hardest to supply.
   B5's revival ask is the dependency. If it does not land, feature 2 carries
   wave 1 by itself.
2. **Repeat-filer and duplicate panel.** "This petitioner has filed three times
   before; here is what happened." **The only Tier 1 feature that runs on what we
   already hold**: it matches an arriving case against history, so a snapshot is
   enough and no live feed is needed. Already built and corpus-wide capable since
   26 August. Officers do not have this and said they do not know their repeat
   rate. Needs `dedup_signatures` and `dedup_groups`, which are held out of the
   lake pending Phase 18 RBAC (`janasunani/olap/materialize.py:29-41`); for two
   officers, read OLTP directly under existing controls.
3. **Document summary and key-fact extraction.** Largest potential burden
   reduction, least ready. Development evidence: 55/84 critical facts retained,
   8/26 usable without edit, 4/26 residual PII, and it skipped all four coherent
   Odia cases. **It cannot be shown to an officer at 4/26 residual PII.** It
   clears a gate before launch or it ships in a later wave.

**Tier 2, only if Tier 1 lands early.**

4. **Intra-department authority suggestion**, built from A5, keyed on
   (subcategory, district), restricted to these two departments. Two halves: the
   escalation chain, which the portal holds as configuration and we read; and the
   named office within it, which we learn. Distinct from the existing crosswalk,
   which predicts the *department* and cannot reach Labour & ESI from the live
   path at all, because Labour & ESI exists only at subcategory keys and the live
   classifier predicts no subcategory
   (`janasunani/routing/crosswalk.py:15-22`).

   The category and department suggestions are **no longer ruled out**. That
   exclusion rested on the officer being downstream-only, which B1 has
   disproved. How much weight they carry depends on how often the registration
   path is used.

**Tier 3, excluded.**

5. **Actionability / low-signal triage. Excluded from SSEPD entirely.** This is
   a safety decision, and the argument is in AB_PLAN §14.6.
6. **Category suggestion.** Officers already said category does not meaningfully
   change routing, so they do not invest in getting it right. Low value at the
   department node.

**Rollout.** Do not stage features across officers; there are two of them.

- **Shadow phase, 4 weeks, no arms:** features 1 and 2 shown on every case.
  Usability, training, telemetry validation and the re-key fidelity measurement
  that is gate 2. This phase produces the feasibility evidence on its own.
- **Wave 1, pilot weeks 1 to 16, only if both AB_PLAN §14.9 gates pass:**
  features 1 and 2 as a single bundle, grievance-level randomised. This is the
  primary comparison of the gated track.
- **Wave 2:** feature 3 added mid-pilot *only if* it clears a PII and factuality
  gate, as a second randomisation nested among treated cases, so the
  bundle-versus-control contrast is preserved. If it does not clear, it does not
  ship, and that is a finding.
- Per-feature acceptance telemetry (shown, viewed, accepted, edited, rejected,
  ignored) gives descriptive per-feature evidence without a factorial design we
  cannot power.

---

## 5. What has to be built before launch

Ordered. Items 1 to 3 are prerequisites regardless of feature scope.

1. **Bring the cloud stack up.** Issue #30, open since July. Deployment
   automation is written, reviewed and never run. Two known rollout gaps (#32):
   a workflow timeout can kill SSH mid-`deploy.sh` and skip the rollback, and a
   rollback can ship the current compose beside an old image SHA.
2. **Auth and RBAC.** Today there is site-wide Caddy `basic_auth` and nothing
   else. Two named government officers logging into a system holding citizen
   grievances need real accounts, real audit and a tested restore. Phase 18, and
   gate 1 of the five Part III gates, none of which is currently met.
3. **The pilot console.** Queue view, case view, the AI panel rendered or
   suppressed by assignment, and a decision form the officer completes before
   re-keying. Build on the existing Next.js frontend against the frozen serving
   API contract; use the `frontend-dpic` agent.
4. **Assignment service and event tables**, AB_PLAN §7.1 to §7.3, including
   shadow mode so control cases still receive a hidden prediction. The test
   suite is already specified in AB_PLAN Appendix C.
5. **Ageing view and dedup panel**, features 1 and 2.
6. **Odia handling for the pilot slice.** Non-English submissions currently skip
   the summarizer and are marked `Uncategorized`, which for an Odisha department
   pilot is the largest functional gap in the system. Scope it to what features
   1 and 2 need, which is very little, so it does not block launch. It becomes
   blocking only if feature 3 ships.
7. **PII gate resolution.** `janasunani-evaluate-pii` exits non-zero because
   78.3% coverage sits below the 80.56% DSI reference constant at
   `janasunani/pipeline/pii_eval.py:31`, a reference every other document says
   is not a target. Separate the gate from the reference and set a real
   threshold before an officer sees model output. Do not relax it to make a
   number pass.

---

## 6. Timeline

Indicative. Assumes the PM starts in November 2026.

| Window | Work | Owner |
|---|---|---|
| **Sep 2026** | A1, A2, A7. B1 remote walkthroughs. B5 permissions tabled. AB_PLAN §14 written. **Gate 1 read at end of month.** | RA, principal |
| **Oct 2026** | A3 to A6. Build items 1 and 2. Console design. **API revival decided**, so the console is scoped to the feed we will actually have. | RA, engineer, principal |
| **Nov 2026** | PM onboards. **Field trip 1**, principal travels: B3 district visits, B4 time-and-motion, A8 briefs delivered. Build items 3 to 5. | All |
| **Dec 2026** | Console complete, event tables built and tested. Dry-run rehearsal, remote. Pre-analysis plan drafted from measured A2 inputs. | Engineer, PM |
| **Jan 2027** | **Shadow phase, 4 weeks.** Officers use the console on every case, panel shown on all of them, no randomisation. Purpose: usability, transfer-loss measurement, telemetry validation, training. | PM |
| **late Jan 2027** | **Gate 2 read** from shadow-phase re-key fidelity. If it passes, **lock**: tag `ab-plan-locked-v1`; record extract hash, seed, MDEs, pause margins. No arm outcome viewed before this. | Principal |
| **Feb to May 2027** | **Live randomised pilot, 14 to 16 weeks, if both gates passed.** Weekly officer call, monthly harm review, 10% re-key audit. | PM |
| **Mar 2027** | **Interim feasibility readout.** Adoption, transfer loss, telemetry quality, officer testimony. The artifact for the integration ask. No outcome comparison by arm. | Principal |
| **May to Jun 2027** | Citizen phone follow-up at fixed horizon. **Field trip 2**, close-out interviews. | PM, RA |
| **Jul 2027** | Analysis against the locked plan, then the report. | RA, principal |

**Compression option.** If the integration ask is urgent, A8 plus the shadow
phase alone constitute a credible feasibility case by **February 2027**. The
randomised component is what runs to July.

---

## 7. Personnel

| Role | Activities | When |
|---|---|---|
| **Principal (Bangalore)** | Design, AB_PLAN §14, government relations, permissions, plan lock, readouts. Two Odisha trips total. | Throughout |
| **Project manager (Odisha, joins Nov 2026)** | Owns the field. Officer relationship, B3 visits, B4 time-and-motion, shadow-phase support, weekly calls, harm monitoring, close-out. | Nov 2026 to Jun 2027 |
| **RA (reallocated, available now)** | Workstream A end to end, A8 briefs, power calculation, final analysis. Odia capability strongly preferred; if present, also runs the citizen survey. | Sep 2026 to Jul 2027 |
| **Engineer plus agents** | Build items 1 to 7, telemetry, deploy, on-call during the live pilot. | Oct 2026 to May 2027 |
| **Odia-speaking surveyor (part-time, ~3 months)** | Citizen follow-up calls if the RA cannot cover them. Census not sample, likely 300 to 800 calls total. Contract, not hire. | Apr to Jun 2027 |
| **Department nodal officers (2)** | The subjects and the partners. Budget their time explicitly: about 2 hours setup, about 30 minutes a week during the pilot, plus the shadow-phase learning cost. | Nov 2026 to May 2027 |

**Unowned, and it blocks feature 3.** Nobody owns the hand-transcribed OCR
reference sample (#53, unowned since 7 August, and the reason no OCR accuracy
figure exists anywhere in this project). If the summarizer is to ship, that
owner has to be named.

---

## 8. Risks and pause conditions

- **The API stays down.** Feature 1 does not run, wave 1 is the repeat-filer
  panel alone, and citizen outcomes fall back to the survey plus whatever the
  officer can read off the portal case by case. Decide by end October, so the
  console is built for what we will actually have.
- **Volume too small to randomise.** Most likely for Labour & ESI. Decision
  point is A2. If it cannot be powered, run it as a feasibility and qualitative
  arm and randomise SSEPD only. Decide before launch, not after.
- **Officers do not use the console.** At N = 2 this is existential rather than a
  nuisance. Mitigated by the 4-week shadow phase, and adoption is itself a
  reported outcome.
- **Transfer loss swamps the effect.** If re-keying discards most of what the
  console produces, the intervention is diluted to nothing. The 10% audit
  measures it. Below the threshold set at lock, that attenuation is the finding
  and no citizen-effect claim is made.
- **Contamination across arms.** One officer, both arms, learning. Signed toward
  the null; diagnostic and interpretation in AB_PLAN §14.2.
- **Harm.** AB_PLAN §10 carries forward unchanged. Control keeps the current
  process, treatment adds advice only, no automated rejection, any confirmed PII
  exposure halts the pilot, the ED reviews arm-level degradation within 2
  business days. Margins filled at lock from A2.
- **Withdrawn claims stay withdrawn.** No routing time saving. Not the in-sample
  crosswalk numbers (60.9 / 67.5 / 72.8). 13.7 seconds is machine time, not
  officer time. 11 to 23 days is a measured gap, not a saving.

---

## 9. Checkpoints

Each is a go/no-go with a named artifact, not a status update.

1. **End Sep 2026. Gate 1** (AB_PLAN §14.9). A1 volumes and the A2 power table
   exist. Go/no-go on whether the randomised comparison runs at all, and
   separately on whether Labour & ESI is randomisable.
2. **End Oct 2026.** Stack live on AWS with real auth and a tested restore.
   Nothing touches an officer before this.
3. **Mid Nov 2026.** Department Annex B written for both departments; B4
   baseline collected; A8 briefs delivered. Go/no-go on the feature set, which
   depends entirely on B1's answer about where the officer sits.
4. **End Dec 2026.** Console passes an end-to-end rehearsal. Assignment service
   passes AB_PLAN Appendix C: determinism over 1,000 re-runs, rank uniformity,
   no dependence on outcome data.
5. **End Jan 2027. Gate 2** (AB_PLAN §14.9). Shadow phase complete, re-key
   fidelity measured against the threshold. On a pass, plan tagged
   `ab-plan-locked-v1` with extract hash, seed and MDEs recorded. On a fail, the
   attenuation is the finding and no arm comparison is run.
6. **Mar 2027.** Interim feasibility readout delivered. It either wins the
   integration approval or tells us it will not come.
7. **Jul 2027.** Final report, analysed strictly against the locked plan.
