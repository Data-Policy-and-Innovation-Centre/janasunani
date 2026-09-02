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

- **December ships three things.** Two department briefs, hand-delivered in
  November. An integration memo in December. Measured accuracy numbers for our
  own pipeline.
- **Outside the workflow we reach 2% of the caseload.** Only cases that both
  start at the department and arrive on paper can be taken in without a data
  feed. 817 of SSEPD's 45,339 and 301 of Labour & ESI's 10,459. About five a
  week across both departments, and falling as paper intake digitises.
- Our extract ends July 2025, thirteen months stale, over a period when volume
  roughly doubled. Refreshing it is the cheapest thing you can get us.
- Three data asks in rising cost: a fresh extract, a report the department can
  export, the API restarted. The third decides whether any of this scales.
- Analysis and process mapping run while the app is built. Neither waits on the
  other.
- We have not run the Sarvam pipeline at scale, and not on Odia at all. No
  accuracy figure for reading scanned documents exists. December fixes that.
- Just over a third of SSEPD's cases arrive on paper, where the text field is a
  one-line stub and the grievance is only in the scan. Anything reading what the
  citizen wrote is blind there.
- Rather than build a tool and hope it gets used, we answer officer questions by
  hand for two months and log every one.
- We are excluding the low-signal flag for SSEPD. Safety judgement, and the one
  item here needing your sign-off.

---

## 1. What the pilot is

There is no integration, so officers read our outputs on a separate screen and
retype what they choose into the portal. One grievance officer per department.
Their account carries admin rights over workflow configuration, and can register
a grievance, assign the action-taking authority, and forward to a Commissioner,
a Collector, an MD or Director, or a named subordinate.

The grievance API is not running and our extract ends July 2025. That takes the
app off the critical path without stopping it being built.

Four tracks, in parallel.

**1. Desk study.** On the extract we hold. No permission, no travel, no build.
Ghazal, from Patna, starting now.

**2. Process mapping.** Screen shares with both officers in September, then a
map of every step from arrival to closure, sent back to them for correction.
Milinda and me. Verified in person on one October trip, which also collects a
stopwatch baseline of the officers' current work.

**3. Concierge test.** From October, officers send us a question about a case
and we answer by hand within a day. Every request logged. Tells us whether they
want a tool before we build one.

**4. The app and its evidence.** December deliverable is numbers, not a product:
what our document reading, summarisation and personal-information screening
actually achieve, on Odia as well as English. We have not run the Sarvam
pipeline at scale, and not on Odia at all. Aparupa and Milinda own the Odia
reference sample that makes an accuracy figure possible.

That last one matters because a third of SSEPD's caseload is scanned paper,
which is exactly where document reading is the only thing that helps.

Briefs to the departments in November. Memo to you in December.

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

A8 is the door-opener. The officer's dashboard shows totals, pendings by holder,
and overdue counts in 7, 15 and 30-day buckets. It shows no history: how those
numbers have moved, how the department compares to its own past, how often the
same petitioner comes back. That is what the briefs carry.

---

## 3. What the field work decides

**B1, where the officer sits in the chain.** The August walkthroughs settled most
of it. These officers do both: they receive cases routed to them, and they can
register and assign. Their screen carries registration, forwarding and the
per-case action history. Category and department suggestions are therefore in
scope. What remains is how often each path is used, and how many cases reach
them per week.

**B2, the process map.** Every step from arrival at the department to closure,
in order, with the person holding the case named at each one. Drafted in
September from the screen shares, sent back to the officers for correction,
verified in October by watching the work.

**B4, the stopwatch baseline.** Two to three days per department in October,
before anything is built. Stratified by mode, since a typed website complaint
and a scanned Odia letter are different tasks. It gives us the officer-time
figure this project has never had, and the number any future tool has to beat.

**B3, district visits, is cut from 2026.** It exists to interpret what happens
after the department forwards, which matters for a measurement we are not making
this year.

---

## 4. Features, graded by feasibility

Nothing here ships to an officer in 2026. This is what the app is built toward,
and what the December memo argues we could do from inside the workflow. Read
every row against the 2% ceiling.

| Feature | Feasibility outside the workflow | What decides it |
|---|---|---|
| Repeat-filer panel: has this petitioner filed before, and what happened | **Workable.** It matches on who filed, so the history we already hold is enough | Blind to thirteen months until the extract is refreshed |
| Forwarding suggestion: which office inside the department should get the case | **Workable.** The chains are configured in the portal and can be read off; only the choice of named office is learned | Two fields the officer types anyway |
| Document summary: the key facts of a scanned grievance | **Marginal.** The officer would download the scan and upload it, about a minute per case | Whether the reading time saved beats the minute spent. And it is unmeasured until the Odia reference sample exists |
| Ageing view: pending cases oldest first, days left against the time the officer allowed | **Not possible.** It lists what is pending now, and we hold a snapshot with no way to refresh it | A data feed. Nothing else |
| Low-signal flag: marking a grievance as unlikely to need action | Excluded | Safety decision, below |

**The ageing view is the one no amount of effort rescues.** Every other feature
needs data about the case in front of the officer, which they are handling
anyway. This one needs data about the cases they are not handling, and there is
no moment at which anyone would enter those by hand.

We will put the workable ones to the departments as answers to their own
ten-point list, which already asks for reminders at 7 days, escalation at 15,
and handling of duplicate and bulk petitions.

**How grievances arrive bounds what any of this reaches.** Just over a third of
SSEPD's cases, and a fifth of Labour & ESI's, come in on paper: physical
submissions, letters, joint hearings and CM visits. For those the portal's text
field holds a one-line stub an officer typed, twenty to thirty characters, and
the grievance itself exists only in the scanned document. So everything that
reads what the citizen wrote is blind on that third unless the document summary
works, which is exactly why measuring it is a December deliverable. The
repeat-filer panel is unaffected, because it matches on who filed rather than on
what they wrote.

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

## 5. The concierge test

October to December. An officer sends a ticket number or a petitioner's details.
Within a day we send back what we can find: how many times that person has filed
before, what happened to those cases, whether this one looks like a duplicate.
Utkarsh holds the channel, Ghazal runs the lookup from Patna.

Every request logged: what was asked, what we sent, whether it changed what the
officer did.

Two officers saying they used something and want it in their workflow is a
stronger argument for integration than anything we would write. If they never
ask, we know that before building anything. The log is also the specification.

We will tell them at the outset that until the first data ask lands, our answers
are blind to thirteen months of filings, so someone who filed in March 2026 will
look like a first-time filer.

**What we can measure this year.** The stopwatch baseline gives the officer-time
figure this project has never had. The desk study gives the caseload. Neither
gives an effect: with no console there is no comparison, and with two officers
and unmeasured throughput there may never be a well-powered one.

---

## 6. The four months

| Month | What happens |
|---|---|
| **September** | Screen shares with both officers, settling how often they register cases themselves and how many reach them each week. Process maps drafted and sent back for correction. Three data asks tabled. Desk study starts. |
| **October** | One trip to Bhubaneswar: maps verified by watching the work, stopwatch baseline, concierge channel agreed. Desk study completes, briefs drafted. |
| **November** | Utkarsh joins. **Briefs hand-delivered.** He takes over the concierge channel. |
| **December** | **The integration memo**, with the concierge log as evidence and the pipeline numbers as technical annex. 2027 decision taken. |

In parallel throughout, gating none of it: the Sarvam pipeline run at scale, the
Odia reference sample transcribed, the app progressing.

Five people. Ghazal on the analysis from Patna, Milinda on the maps and the Odia
reference, Aparupa on the Odia work, Utkarsh in Odisha from November, me in
Bangalore with one trip in October.

**The 2027 decision, in December.** Four questions. Did a data feed land. Does
the concierge log show officers want what we would build. Are the pipeline
numbers good enough to show someone. Is there anyone to finish the app. The
randomised measurement of officer time needs all four plus a throughput we have
not measured, so treat it as a 2028 question.

---

## 7. What we need from you

**Three data asks, in rising order of cost.** The third is the one that matters,
and the first is the one you can probably get this month.

1. **A fresh extract of the grievance history, to current date.** A one-time
   data request, not a system change. Ours ends in July 2025, over a period when
   volume roughly doubled, so we are describing a caseload that is thirteen
   months out of date and telling officers someone is a first-time filer when
   they are not. Smallest thing on this list and it improves everything.
2. **A record-level report the department can export.** Whatever the department
   login can already download, one row per grievance. If one exists, an officer
   downloading it weekly gets us most of the way to current state at no cost to
   anyone. We are confirming this month whether it exists.
3. **The read-only grievance API restarted, and scoped to these two
   departments.** The endpoint no longer runs, so this is two things: revive it,
   then give us read access. **This is the ask that decides whether any of this
   scales.** Outside the workflow we reach 2% of the caseload. With a feed we
   reach all of it. Still far short of the integration OCAC refused: we would
   only read, change no workflow, and cover two departments.

**Two smaller things:**

4. **Written sign-off naming both departments** for a bounded engagement.
5. **Permission to log what officers ask us and what we send back**, which is
   what the concierge test records. A department suggestion labelled as
   AI-generated is already on the live assignment screen, and nothing anywhere
   records whether officers accept it.

**One thing that is no longer urgent.** Re-confirming your 27 July determination
can wait, because the citizen phone survey that made it necessary is out of
scope this year. It should be re-asked before any citizen contact resumes.
Data-protection obligations apply either way.

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
  and no citizen-outcome claim unless an interval supports one.
