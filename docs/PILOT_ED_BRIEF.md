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

- **December ships three things.** Two department briefs on their own caseloads,
  hand-delivered in November. One memo in December on what integration would
  buy. And measured numbers for our own pipeline, so that whatever we pilot next
  year is something we have tested.
- **The number that makes the case: working outside the workflow, we can reach
  about 2% of the caseload.** Only cases that both originate at the department
  and arrive on paper can be taken in without a data feed. That is 817 of
  SSEPD's 45,339 cases and 301 of Labour & ESI's 10,459, about five a week
  across both departments, and the share is falling as paper intake digitises.
- The analysis and the process mapping happen while the app is built, on tracks
  that do not wait on each other. December's deliverables come from the first
  two. The app matures toward a 2027 pilot at the pace part-time building
  allows.
- Our extract of the grievance history ends July 2025. It is thirteen months
  stale, over a period when volume roughly doubled. Refreshing it is the
  cheapest and most useful thing you can get us.
- Three data asks, cheapest first: a fresh extract, then an export the officer
  can download, then the API revived. The first is a one-time data request and
  is worth pressing on its own.
- The officers register grievances as well as forward them, so the category and
  department suggestions are in scope. The portal already shows overdue cases in
  7, 15 and 30-day buckets. The forwarding chains are a configured list we can
  read rather than a pattern we have to learn.
- Rather than build a tool and hope it is used, we answer officer questions by
  hand for two months and log every one. If they ask repeatedly, that is the
  evidence for building it. If they never ask, we have saved three months.
- We have never run our own pipeline end to end and have no accuracy figure for
  reading scanned documents. Fixing that is a December deliverable, because a
  pilot built on an unmeasured stack is how we get embarrassed in front of a
  department.
- Just over a third of SSEPD's cases arrive on paper, where the text field is a
  one-line stub and the grievance is only in the scan. Anything reading what the
  citizen wrote is blind there.
- We are excluding the low-signal flag for SSEPD, which would mark a grievance
  as unlikely to need action. That is a safety judgement, and it is the one item
  here that needs your sign-off rather than a note.

---

## 1. What the pilot is

There is no integration, so officers read our outputs on a separate screen and
retype what they choose into the portal. There is one grievance officer per
department. Their account carries admin rights over workflow configuration, and
can register a grievance, assign the action-taking authority, and forward to a
Commissioner, a Collector, an MD or Director, or a named subordinate.

Two further constraints decide what four months can hold. **We have no
engineer**: the build sits on me, part-time, alongside the design and the
government asks. **We have no live data**: the grievance API is not running, and
our extract of the grievance history ends in July 2025.

Together those take the app off the critical path. They do not stop it being
built. The analysis and the process mapping run while it is built, and neither
waits on the other.

**Track 1, the desk study.** Runs on the extract we hold. Needs no permission,
no travel and no build. Owned by Ghazal from Patna, starting now.

**Track 2, process mapping.** Screen shares with both officers in September,
then a map of every step from arrival to closure, sent back to them for
correction. Milinda and me. Verified in person on one October trip, which also
collects a stopwatch baseline of the officers' current work.

**Track 3, the concierge test.** From October, officers send us a question about
a case and we answer it by hand within a day. We log every request. This tells
us whether they want a tool before we spend three months building one.

**Track 4, the app and its evidence.** Running in parallel, at part-time pace.
Its December deliverable is not a product but a set of honest numbers: what our
document reading, summarisation and personal-information screening actually
achieve, measured on Odia as well as English. We have never run the pipeline end
to end, and no accuracy figure for reading scanned documents exists anywhere in
this project. Aparupa and Milinda own the Odia reference sample that makes such
a figure possible, which has been unowned since August.

**Why this is a deliverable rather than housekeeping.** Just over a third of
SSEPD's caseload arrives as scanned paper, and that is precisely where document
reading is the only thing that helps. Piloting it unmeasured in front of a
department is the failure mode worth spending four months to avoid.

The two departments get their briefs in November. You get the integration memo
in December.

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

Nothing here ships to an officer in 2026. This is what the app is being built
toward, and what the December memo argues we could do from inside the workflow.

**Read every row against the 2% ceiling.** Without a data feed, the only cases
we can take in are those that both start at the department and arrive on paper.

| Feature | Feasibility outside the workflow | What decides it |
|---|---|---|
| Repeat-filer panel: has this petitioner filed before, and what happened | **Workable.** It matches on who filed, so the history we already hold is enough | Blind to thirteen months until the extract is refreshed |
| Forwarding suggestion: which office inside the department should get the case | **Workable.** The chains are configured in the portal and can be read off; only the choice of named office is learned | Two fields the officer types anyway |
| Document summary: the key facts of a scanned grievance | **Marginal.** The officer would download the scan and upload it, about a minute per case | Whether the reading time saved beats the minute spent. And it is unmeasured until the Odia reference sample exists |
| Ageing view: pending cases oldest first, days left against the time the officer allowed | **Not possible.** It lists what is pending now, and we hold a snapshot with no way to refresh it | A data feed. Nothing else |
| Low-signal flag: marking a grievance as unlikely to need action | Excluded | Safety decision, below |

**The ageing view is the one no amount of effort rescues.** Every other feature
needs data about the case in front of the officer, which they are handling
anyway. This one needs data about the cases they are not handling. Asking an
officer to keep a parallel list by hand so that we can help them with
list-keeping is self-defeating.

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

## 5. How we find out whether any of it is wanted

**The concierge test, October to December.** Rather than build a tool and hope
it gets used, we answer questions by hand and see whether they keep coming.

An officer sends a ticket number or a petitioner's details. Within a day we send
back what we can find: how many times that person has filed before, what
happened to those cases, whether this one looks like a duplicate. Utkarsh holds
the channel with the officer, Ghazal runs the lookup from Patna.

We log every request: what was asked, what we sent, and whether it changed what
the officer did.

- It costs no engineering, which is why it is the one thing we can be sure of
  delivering.
- Two named officers saying they used something and want it inside their
  workflow is a stronger argument for integration than anything we could write.
- If they never ask, we have learned that for the price of a WhatsApp thread
  rather than three months of build.
- The log is also the specification. If we do build, it says which feature
  first.

One thing we will tell them at the outset rather than let them discover: until
the first data ask lands, our answers are blind to thirteen months of filings,
so someone who filed in March 2026 will look like a first-time filer.

**What we can measure this year, and what we cannot.** The stopwatch baseline in
October gives the officer-time denominator this project has never had. The desk
study gives the caseload. Neither gives an effect. With no console there is no
comparison, and with two officers and unknown throughput there may never be a
well-powered one. That question is settled in 2027, not now.

---

## 6. The four months

| Month | What happens |
|---|---|
| **September** | Screen shares with both officers, which settle how often they register cases themselves and how many reach them each week. Process maps drafted and sent back for correction. The three data asks tabled. Desk study starts. |
| **October** | One trip to Bhubaneswar: the maps verified by watching the work, a stopwatch baseline of how long a case actually takes, and the concierge channel agreed face to face. Desk study completes and the briefs are drafted. |
| **November** | Utkarsh joins and inherits a live relationship rather than starting cold. **The two department briefs are hand-delivered.** He takes over the concierge channel. |
| **December** | **The integration memo**, with the concierge log as its evidence and the pipeline numbers as its technical annex. The 2027 decision taken. |

Throughout, in parallel and gating none of the above: the pipeline runs end to
end, the Odia reference sample gets transcribed, and the app progresses at
whatever pace part-time building allows.

Five people. Ghazal on the analysis from Patna, Milinda on the maps and the Odia
reference, Aparupa on the Odia work with her, Utkarsh in Odisha from November,
me in Bangalore with one trip in October. No dedicated engineer, which is why
the app has no December deadline and why the other tracks were designed not to
depend on it.

**The 2027 decision, in December.** Four questions. Did a data feed land, without
which we reach 2% of the caseload. Does the concierge log show officers want
what we would build. Are the pipeline numbers good enough to show someone. Is
there anyone to finish the app. The randomised measurement of officer time needs
all four and a caseload throughput we have not yet measured, so treat it as a
2028 question.

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
