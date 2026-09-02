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

The **console** is the screen we build for the two officers. The **panel** is
the part of that screen carrying our suggestions. To **randomise** is to decide
by chance, case by case, whether the panel is shown, so that the two sets of
cases stay comparable.

---

## TLDR

- The pilot runs in three tracks: desk analysis on the records we already hold,
  process mapping and field visits, then the console with the two officers. The
  first two start now and need nothing from the departments.
- The pilot cannot show that the tool caused a change at department or state
  level. Two officers and no integration rule that out. What we report about
  citizen outcomes will be observations, with no claim about cause.
- Each piece of the desk analysis decides a specific feature or design choice.
  One of them is the deliverable that buys officer cooperation.
- The officers register grievances as well as forward them, so the category and
  department suggestions are in scope. The portal already shows overdue cases in
  7, 15 and 30-day buckets. The forwarding chains are a configured list we can
  read rather than a pattern we have to learn.
- Two features use no AI model: a per-case list of pending grievances by age
  against the time the officer allowed at assignment, and a panel showing
  whether the petitioner has filed before. The second is buildable today. The
  first needs the grievance API back up, which is ask 3, because it shows what
  is pending now and we hold only a one-time extract. The document summary is
  low feasibility and may not ship at all.
- We are excluding the low-signal flag for SSEPD, which would mark a grievance
  as unlikely to need action. That is a safety judgement, and it is the one item
  here that needs your sign-off rather than a note.
- The randomised comparison is one component of the pilot, and two conditions
  have to be met before it runs. We call them gates. Gate 1 is the September
  power calculation. Gate 2 is a measurement, taken in January, of how much of
  what the console produces actually reaches the portal when the officer
  retypes it. If either fails, the rest still runs and still produces the
  integration case.
- The four asks: department sign-off, permission to log officer decisions,
  read-only access to the two departments' own records, and re-confirmation of
  the 27 July research determination now that a citizen survey is involved.

---

## 1. What the pilot is

There is no integration, so officers read our outputs on a separate screen and
retype what they choose into the portal. There is one grievance officer per
department. Their account carries admin rights over workflow configuration, and
can register a grievance, assign the action-taking authority, and forward to a
Commissioner, a Collector, an MD or Director, or a named subordinate.

The pilot runs in three tracks.

**Track 1, desk analysis.** Runs on the 1.37 million grievances we already hold,
using code we have already written. Needs no app, no travel and no permission.
Owned by the research associate, starts now.

**Track 2, process mapping and field work.** Remote walkthroughs with both
officers in September. One Odisha trip in November for district visits and a
stopwatch measurement of the officers' current work.

**Track 3, the console.** A four-week shadow phase, meaning every case goes
through the console with the panel shown and nothing decided by chance. Then a
randomised comparison, if both gates pass.

Tracks 1 and 2 produce evidence on their own. They are the feasibility case for
integration, and they do not depend on the console being built or on the
officers adopting it. Track 3 is where the pilot can fail, and the randomised
half of it is conditional.

---

## 2. What the desk analysis decides

Eight analyses, all of them on records we already hold, using code we have
already written.

| Analysis | What we do | What it decides |
|---|---|---|
| A1 volume and case mix | Count monthly filings for both departments, 2021 to 2025, broken down by district, case type, and how the grievance arrived | Whether Labour & ESI has enough grievances to randomise at all, and which cases our features can reach |
| A2 power calculation | Compute the minimum detectable effect from those volumes and the dispersion in handling times B4 measures | Gate 1 on the randomised comparison |
| A3 turnaround baseline | Measure how long cases take end to end today, and how long they sit between recorded steps | The ageing view, and how long we follow a case after filing |
| A4 repeat filers | Match petitioners across the full history to find how often the same person files again, and what happened the previous time | The repeat-filer panel, and the first department meeting |
| A5 who the case goes to next | The portal already lists the routes a case can take, so we read those off. Which actual office gets it we count from past cases, by district and case type | Whether the choice is fixed enough to suggest, or varies enough that we should not |
| A6 ageing fields | Check the overdue date in the record against the resolution time officers typed at assignment, and against the portal's own overdue counts | What the ageing list counts down to, and whether it agrees with the portal the officer already trusts |
| A7 what the fields mean | Audit the list of action types, count how many cases are still open when a reporting window closes, and establish whether the department field records the original assignment or the final position | Whether any number we report can be interpreted |
| A8 department briefs | Package A1, A3, A4, A5 and A6 into one short report per department, and hand it over on the November trip | Officer cooperation, which every other track needs |

A8 is the door-opener. The officer's dashboard shows totals, pendings by who
holds them, and overdue counts in 7, 15 and 30-day buckets. What it does not
show is any history: how those numbers have moved, how this department compares
to its own past, or how often the same petitioner comes back. That is what the
briefs carry, and they cost no engineering.

---

## 3. What the field work decides

Four field tasks.

**B1, where the officer sits in the chain.** The August walkthroughs settled
most of it. These officers do both: they receive cases routed to them, and they
can register and assign. Their screen carries registration, forwarding and the
per-case action history. Category and department suggestions are therefore in
scope.

What remains is how often each path is used, which decides how much weight the
registration-side features carry.

**B2, the officer-time baseline, so we know how long a case takes an officer
today.** A step-by-step map per department, from the case arriving at the
department to closure, with our own timings against each step.

**B3, district visits to the offices that act on what the department forwards.
This tells us whether a better output at the department stage can reach a
citizen.** The department officer forwards; someone else acts. Two districts,
one high-volume and one low-volume, and at each the Collector's grievance cell,
a BDO office and the department's district officer. If cases come back because
the block-level report was poor, then improving what the department produces
cannot move a citizen outcome. We want that established before we claim one.

**B4, the stopwatch measurement, which makes handling time comparable later.**
Two to three days per department with a stopwatch and a structured form, before
the console exists. It also gives A2 the spread in handling times it needs, and
is the fallback if the console's own recording underperforms.

---

## 4. Features, graded by feasibility

| Feature | Needs | Feasibility | What would stop it |
|---|---|---|---|
| Ageing view: pending cases oldest first, days elapsed, days left against the time the officer allowed at assignment | A3, A6 | High | Nothing. No AI model involved, so no model risk. The portal has the aggregate overdue counts; what it lacks is the per-case list behind them |
| Repeat-filer panel: has this petitioner filed before, and what happened | A4 | High | The duplicate-detection tables sit outside the analysis store. For two officers we read the live database directly |
| Forwarding suggestion: which office inside the department should get the case | A5 | High | The escalation chains are already configured in the portal and can be read off. Only the choice of named office has to be learned |
| Document summary: the key facts of a scanned grievance, in text | A hand-checked sample of scanned documents, which nobody owns yet | Low | Four of twenty-six drafts left personal information in the summary. It cannot be shown to an officer in that state |
| Low-signal flag: marking a grievance as unlikely to need action | Excluded | Not built | Safety decision, below |

The first two are built and tested together in wave 1. The summary is a wave 2
item and ships only if it first passes a check for accuracy and for leaked
personal information. If it fails that check, it does not ship, and that outcome
is itself worth reporting.

We will put these to the departments as answers to their own ten-point list,
which already asks for reminders at 7 days, escalation at 15, and handling of
duplicate and bulk petitions.

**How grievances arrive bounds what any of this reaches.** Just over a third of
SSEPD's cases, and a fifth of Labour & ESI's, come in on paper: physical
submissions, letters, joint hearings and CM visits. For those the portal's text
field holds a one-line stub an officer typed, twenty to thirty characters, and
the grievance itself exists only in the scanned document. So everything that
reads what the citizen wrote is blind on that third unless the document summary
ships. The repeat-filer panel is unaffected, because it matches on who filed
rather than on what they wrote, and so is the ageing view, which reads dates.

**One exclusion needs your sign-off, because it is a safety judgement rather
than a product choice. We are not building the low-signal flag for SSEPD.** The
signs officers described for a grievance unlikely to need action are requests
for government jobs, and requests for financial assistance carrying no detail.
Financial assistance is, in effect, SSEPD's entire caseload. A flag built on
those signs would systematically route disability-benefit claims into a review
queue. That is a foreseeable and patterned harm to the population the department
exists to serve, and no accuracy figure would license it. It is out of scope for
this pilot. Any later reconsideration for Labour & ESI would need a set of cases
that officers have labelled by hand, which does not exist.

---

## 5. How we test, and what each test can carry

**Shadow phase, four weeks, nothing decided by chance.** Every case goes through
the console with the panel shown. This measures usability, training cost, the
quality of what the console records automatically, and most importantly how much
of what the console produces survives the officer retyping it into the portal.
It produces description, usability evidence and officer testimony. It is also the interim report and the evidence for
the integration ask.

**The randomised comparison, behind two gates.** If it runs, every grievance
reaching the two officers passes through the console, and chance decides case by
case whether the panel is shown or left blank. Both sets of cases use the same
screen and record the same decision before the officer retypes. On the blank
cases the model still runs with its output hidden, so we know what it would have
said. The measure is the officer's handling time. Assignment is balanced within
each officer's week, so the two sets stay comparable as the officer learns and
as the caseload shifts. We judge the result by re-shuffling the assignments many
times and asking how often chance alone would produce a gap this large. At this
sample size that is more honest than the standard formula, which assumes larger
numbers than we have.

- **Gate 1, end September 2026.** The power calculation. If the minimum
  detectable effect is implausibly large at the volumes we measure, the
  comparison does not run. SSEPD's own dashboard shows why this is the likeliest
  gate to fail: 51,460 tickets in total, 1,471 pending, and 27 of those with the
  officer we would randomise on. Department volume is large.
  Officer throughput is what gate 1 has to work from, and it is small.
- **Gate 2, January 2027.** The shadow-phase measurement of how much of the
  console's output actually reaches the portal. The tool reaches a citizen only
  because a person retypes it. Below the threshold we fix in advance, the
  dilution itself is the finding, and we make no claim about citizen outcomes.

One officer sees both sets of cases and learns from the ones with the panel. We
know which way that pushes the result: what the officer learns carries over to
the blank cases and makes them better, which shrinks the measured gap. So a
positive result survives the problem. A flat result cannot tell apart "the tool
does nothing" from "the learning carried over completely", and we will say so
in the report.

**Citizen outcomes, reported as observations.** A phone survey in Odia at a
fixed interval after filing, for every eligible case, whether or not the portal
has closed it. Closure is itself something the tool may change, so surveying
only closed cases would bias the answer towards what we are trying to measure.
At this scale we can afford to call every case rather than a sample. Expect the
margin of error to be too wide to carry a claim, and expect to say so when we
fix the analysis plan rather than when we report.

Where each measurement comes from, given no integration:

| Source | What it gives us | Feasibility |
|---|---|---|
| What the console records automatically | Handling time, screens opened, returns to a case, on all cases | High. Checked against the B4 stopwatch measurement |
| The officer's decision form in the console | Office chosen, days allowed, remark, before retyping | High, though it is the officer's own account |
| Weekly audit of 10% of cases against the portal | How much of the console output was retyped accurately, and gate 2 | High, and a headline finding in its own right |
| The portal's own action history, one row per step | Date, what was done, who sent it, who holds the case now | High for reading case by case. It is on screen in the department login. Whether it can be exported in bulk is the open question |
| Read-only access to the two departments' records | The same, without an officer opening each case | Low today. The endpoint is not running; ask 3 is to revive it |
| An export from the portal with one row per grievance | Whole-caseload figures without our reading each case | Medium. Record-level reports do exist at the department login, so this is likelier than we assumed |
| Citizen phone survey | Whether the citizen says it was resolved, and satisfaction | Medium. Depends on ask 4 |

---

## 6. Timeline and cost

| Quarter | What happens |
|---|---|
| **Q4 2026** | Desk analysis on the existing history. Remote walkthroughs with both officers. Permissions tabled. Project manager onboards in November. One Odisha trip: district visits to Collector, BDO and department offices, plus the stopwatch measurement. Each department receives a report on its own caseload. |
| **Q1 2027** | Console built and deployed. Four-week shadow phase, panel always on, nothing decided by chance. It produces the feasibility evidence and the retyping measurement. Gate 2 is read at the end of it. Then we lock the analysis plan, meaning we fix and timestamp it before anyone looks at a result. |
| **Q2 2027** | If both gates pass, the randomised comparison runs for fourteen to sixteen weeks. Interim feasibility report in March either way. |
| **Q3 2027** | Citizen follow-up calls, a closing field visit, analysis against the locked plan, and the report. |

Two trips to Odisha for me. One field-based project manager from November, one
reallocated research associate from now, and a part-time Odia-speaking surveyor
for three months in the spring. The project manager's November start is what
limits all field work, which is why the desk analysis is sequenced first.

If the integration case is needed sooner, the department reports plus the shadow
phase make a credible feasibility argument by **February 2027**. Only the
randomised half runs to July.

---

## 7. What we need from you

1. **Written sign-off naming both departments** for a bounded trial.
2. **Permission to log what is suggested and what the officer does.** A
   department suggestion labelled as AI-generated is already on the live
   assignment screen, and nothing anywhere records whether officers accept it.
3. **Read-only credentials to the two departments' own grievance records.** This
   is the ask that OCAC's refusal did not cover, and it is much smaller than
   integration: we would only read, we would change no workflow, and it covers
   two departments. It is the difference between measuring what actually happened
   to a citizen's case and relying on the survey alone. It is worth pressing
   separately and explicitly.
4. **Re-confirmation of your 27 July determination** that this is programme
   evaluation rather than human-subjects research. That determination was made
   about analysing records the government already holds. This pilot adds a phone
   survey of named citizens, which means collecting new information directly from
   people, and is a materially different activity. It should be re-asked before
   the first call is made, not after. Data-protection obligations apply either
   way.

---

## 8. What we will not claim

- No routing time saving. That analysis failed to reproduce and stays withdrawn.
- Not the routing accuracy figures measured on the same data the model learned
  from. The figures from data the model never saw are much lower, and they
  measure agreement with past practice rather than correctness.
- Thirteen seconds is machine time, not officer time.
- Eleven to twenty-three days is a measured gap between recorded steps, not a
  saving we can deliver.
- For this pilot specifically: no department-level effect, no state-level effect,
  and no claim about citizen outcomes unless the margin of error turns out to
  support one.
