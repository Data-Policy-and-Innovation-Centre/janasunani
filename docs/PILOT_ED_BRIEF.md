---
title: Janasunani
subtitle: Pilot design for SSEPD and Labour & ESI, a decision memo for the Executive Director
author: Yashaswi Mohanty
date: 1 September 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and the University of Chicago Trust
status: Internal
---

# Pilot design: SSEPD and Labour & ESI

Decision memo. Four asks in section 7. The full design is in the internal plan.

---

## TLDR

- The pilot runs in three tracks: desk analysis on our existing corpus, process
  mapping and field visits, then a console phase with the two officers. The
  first two start now and need nothing from the departments.
- It cannot produce a department-level or state-level causal estimate. Two
  officers and no integration rule that out. The citizen endpoints will most
  likely be descriptive.
- The desk analysis is not preparation. Each piece decides a specific feature or
  design choice. One of them is the deliverable that buys officer cooperation.
- One field question decides the feature set: do these officers receive
  grievances already routed to the department, or do they also register intake.
  We answer it in September by screen share.
- Two interventions are high feasibility and use no model: an ageing and
  deadline view, and a repeat-filer panel. The document summary is low
  feasibility and may not ship at all. An authority suggestion depends on the
  field answer.
- Low-signal triage is excluded from SSEPD. That is a safety judgement, and it
  is the one item here that needs your sign-off rather than a note.
- The randomised comparison is one gated component, not the pilot. Gate 1 is the
  power calculation, end September 2026. Gate 2 is measured re-key fidelity in
  the shadow phase, January 2027. If either fails, the rest still runs and still
  produces the integration case.
- The four asks: department sign-off, permission to log officer decisions,
  read-only credentials to the two departments' own records, and re-confirmation
  of the 27 July research determination now that a citizen survey is involved.

---

## 1. What the pilot is

Both constraints are settled. There is no integration, so officers read our
outputs on a separate screen and re-key what they choose. There are two
grievance officers in total. What follows is what those constraints leave.

**Track 1, desk analysis.** Runs on the 1.37M-row corpus with existing code.
Needs no app, no travel and no permission. RA-owned, starts now.

**Track 2, process mapping and field work.** Remote walkthroughs with both
officers in September. One Odisha trip in November for district visits and a
stopwatch baseline of the officers' current work.

**Track 3, the console.** A four-week shadow phase with the panel shown on every
case and no arms. Then a randomised comparison, if two gates pass.

Tracks 1 and 2 produce evidence on their own. They are the feasibility case for
integration, and they do not depend on the console being built or on the
officers adopting it. Track 3 is where the pilot can fail, and the randomised
half of it is conditional.

---

## 2. What the desk analysis decides

Each analysis is tied to a decision. Nothing here is background work.

| Analysis | What it settles | What it gates |
|---|---|---|
| A1 volume and case mix | Whether Labour & ESI has usable volume | Whether Labour & ESI is randomised at all |
| A2 power | The detectable effect on officer time and on citizen endpoints | Gate 1 on the randomised comparison |
| A3 turnaround baseline | Current elapsed days and the gaps between recorded steps | The ageing view, and the follow-up window |
| A4 repeat filers | The repeat-filing rate, which the departments do not have | The repeat-filer panel, and the first department meeting |
| A5 downstream authority | Whether subcategory and district predict the authority the officer picks | The only routing feature worth building at this node |
| A6 ageing fields | Whether the 30-day deadline is computable from the record | The deadline half of the ageing view |
| A7 field semantics | The action taxonomy, censoring, and what `dept` actually records | Whether any outcome we report is interpretable |
| A8 department briefs | Nothing analytical | Officer cooperation, which every other track needs |

A8 is the packaged version of A1, A3, A4, A5 and A6, one short brief per
department, hand-delivered in November. The live portal reports counts and
disposal percentages and carries no time metric on any screen. An ageing profile
and a repeat-filing rate are things these officers have not seen about their own
caseload. It costs no engineering.

---

## 3. What the field work decides

**B1 settles the feature set.** Do these officers receive grievances already
routed to the department, or do they also register intake. Everything we know
about the workflow comes from one login, the CM Grievance Cell, which is roughly
one intake in six. If the officers are downstream only, then the category and
department suggestions are worthless to them and intra-department authority is
the only routing feature left. A 45-minute screen share per department settles
it. This is task 1 of the pilot.

**B2 gives us an officer-time denominator.** A swimlane per department from
arrival at the department node to closure. The only officer-time figure anywhere
in this project is an intake number from a different office. We will not reuse
it as a baseline.

**B3 tells us whether a better department-node output can reach a citizen.** The
department officer forwards; someone else acts. Two districts, one high-volume
and one low-volume, and at each the Collector's grievance cell, a BDO office and
the department's district officer. If reverts are driven by report quality at
block level, then improving what the department node produces cannot move a
citizen outcome. We want that established before we claim one.

**B4 makes handling time measurable.** Two to three days per department with a
stopwatch and a structured form, before the console exists. It also supplies the
variance input A2 needs, and is the fallback if console telemetry underperforms.

---

## 4. Interventions, graded by feasibility

| Intervention | Needs | Feasibility | Binding constraint |
|---|---|---|---|
| Ageing and deadline view | A3, A6 | High | None. No model and no model risk |
| Repeat-filer panel | A4 | High | Dedup tables are held out of the lake. For two officers we read the operational database directly |
| Intra-department authority suggestion | A5, B1 | Medium | Runs only if B1 says the officer makes this call and A5 says it is learnable |
| Document summary | OCR reference sample, unowned | Low | Four of twenty-six drafts leave residual personal information. Not showable to an officer as it stands |
| Low-signal triage | Excluded | Not built | Safety decision, below |

The first two are built and tested in wave 1 as a single bundle. The summary is
a wave 2 item and ships only if it clears a factuality and privacy gate. If it
does not clear, it does not ship, and that is a reportable finding rather than a
delay.

**One exclusion needs your sign-off, because it is a safety judgement rather
than a product choice. We are not building low-signal triage for SSEPD.** The
markers officers described for a low-signal grievance are requests for
government jobs and requests for financial assistance carrying no detail.
Financial assistance is, in effect, SSEPD's entire caseload. A triage flag built
on those markers would systematically route disability-benefit claims into a
review queue. That is a foreseeable and patterned harm to the population the
department exists to serve, and no accuracy figure would license it. It is out
of scope for this pilot. Any later reconsideration for Labour & ESI would need
an officer-confirmed reference set, which does not exist.

---

## 5. How we test, and what each test can carry

**Shadow phase, four weeks, no arms.** Every case goes through the console with
the panel shown. This measures usability, training cost, telemetry quality and,
most importantly, how much of what the console produces survives re-keying into
the portal. It is qualitative and descriptive by design. It is also the interim
readout and the artifact for the integration ask, and it does not need
randomisation to be worth anything.

**The randomised comparison, behind two gates.** If it runs, every grievance
reaching the two officers passes through the console and we randomise, case by
case, whether the panel is shown or blank. Both arms use the same interface and
record the same decision before re-keying. The model runs on control cases with
its output withheld, so we hold the counterfactual prediction. The outcome is
the officer's handling time, blocked within officer-week, tested by
randomisation inference.

- **Gate 1, end September 2026.** The power calculation. If the detectable
  effect is not credible at the measured volumes, the comparison does not run.
  We do not run it underpowered.
- **Gate 2, January 2027.** Measured re-key fidelity from the shadow phase. The
  intervention reaches a citizen only because a person retypes it. Below the
  threshold set at lock, the attenuation is the finding and no citizen-effect
  claim is made.

One officer sees both arms and learns from the treated ones. The bias is signed:
learning transfer makes control cases better, which pushes the estimate toward
zero. A positive result survives it. A null does not separate no effect from
full contamination, and we will say that in the readout rather than in a
footnote.

**Citizen endpoints, descriptive.** A phone survey in Odia at a fixed horizon
after filing, for every eligible case, whether or not the portal has closed it.
Closure is an outcome the intervention may itself move, so surveying only closed
cases would condition on the thing being studied. At this scale a census is
affordable. Expect the intervals to be too wide to carry a claim, and expect to
say so at lock rather than at readout.

Outcome capture, given no integration:

| Route | What it gives us | Feasibility |
|---|---|---|
| Console telemetry | Handling time, screens, revisits, both arms | High. Calibrated against the B4 stopwatch baseline |
| Officer decision form | Authority chosen, days allowed, remark, before re-keying | High, self-reported |
| Weekly 10% re-key audit | Fidelity, and gate 2 | High, and a headline finding in its own right |
| Read-only API, scoped to the two departments | Time to first downstream action, 30 and 60-day resolution | Medium. Depends on ask 3 |
| Record-level portal export | The same, less reliably | Low. May not exist at the department login |
| Citizen phone survey | Reported resolution and satisfaction | Medium. Depends on ask 4 |

---

## 6. Timeline and cost

| Quarter | What happens |
|---|---|
| **Q4 2026** | Desk analysis on the existing history. Remote walkthroughs with both officers. Permissions tabled. Project manager onboards in November. One Odisha trip: district visits to Collector, BDO and department offices, plus the stopwatch baseline. Each department receives a report on its own caseload. |
| **Q1 2027** | Console built and deployed. Four-week shadow phase, panel always on, no arms. This is a deliverable, not a warm-up: it produces the feasibility evidence and the re-key measurement. Gate 2 is read at the end of it, then the analysis plan is locked and tagged. |
| **Q2 2027** | If both gates pass, the randomised comparison runs for fourteen to sixteen weeks. Interim feasibility readout in March either way. |
| **Q3 2027** | Citizen follow-up calls, a closing field visit, analysis against the locked plan, and the report. |

Two trips to Odisha for me. One field-based project manager from November, one
reallocated research associate from now, and a part-time Odia-speaking surveyor
for three months in the spring. The project manager's November start is the
binding constraint on all field work, which is why the desk analysis is
sequenced first.

If the integration case is needed sooner, the department reports plus the shadow
phase make a credible feasibility argument by **February 2027**. Only the
randomised half runs to July.

---

## 7. What we need from you

These are the follow-through on asks two, three and four from the 17 August
brief, not new requests.

1. **Written sign-off naming both departments** for a bounded trial.
2. **Permission to log what is suggested and what the officer does.** An
   AI-labelled department suggestion is already on the live assignment screen and
   nothing anywhere records whether officers accept it.
3. **Read-only credentials to the two departments' own grievance records.** This
   is the ask that OCAC's refusal did not cover, and it is much smaller than
   integration: no writes, no workflow change, two departments only. It is the
   difference between a real citizen endpoint and a survey-only pilot, and it is
   worth pressing separately and explicitly.
4. **Re-confirmation of your 27 July determination** that this is programme
   evaluation rather than human-subjects research. That determination was made
   about analysing administrative records. This pilot adds a phone survey of
   identified citizens, which is prospective collection from individuals and a
   materially different activity. It should be re-asked before the first call is
   made, not after. Data-protection obligations apply either way.

---

## 8. What we will not claim

Unchanged from every prior readout, and restated because a pilot is when these
drift:

- No routing time saving. That analysis failed to replicate and stays withdrawn.
- Not the in-sample routing figures. The held-out numbers are much lower and are
  agreement with past practice, not correctness.
- Thirteen seconds is machine time, not officer time.
- Eleven to twenty-three days is a measured gap between recorded steps, not a
  saving we can deliver.
- For this pilot specifically: no department-level effect, no state-level effect,
  and no citizen-outcome claim unless an interval turns out to support one.
