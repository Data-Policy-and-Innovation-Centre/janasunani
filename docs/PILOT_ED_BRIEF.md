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

Decision memo. Six asks and one sign-off in section 6.

---

## TLDR

- **Outside the workflow we can reach 2% of the caseload.** Only cases that both
  start at the department and arrive on paper can be taken into our system
  without API access. 817 of SSEPD's 45,339 and 301 of Labour & ESI's 10,459.
  About five a week across both departments, and falling as paper intake
  digitises.
- That number is the whole argument. Everything else follows from it.
- **December ships three things.** Two department briefs, hand-delivered in
  November. An integration memo in December. Measured accuracy numbers for our
  own document-reading pipeline.
- Our extract of the grievance history ends July 2025, thirteen months stale,
  over a period when volume roughly doubled. Refreshing it is the cheapest and
  most useful thing you can get us.
- Three data asks in rising order of cost: a fresh extract, a report the
  department can export, the grievance API restarted. The third decides whether
  any of this scales beyond a demonstration.
- The analysis and the process mapping happen while the app is built. Neither
  waits on the other.
- **The department workflow we already understand. The field we do not.** Our map
  stops when the officer forwards the case, which is where most of the elapsed
  time then goes. Two district visits fix that, and they need a permission
  separate from the department sign-off.
- We have not run the Sarvam pipeline at scale, and not on Odia at all. No
  accuracy figure for reading scanned documents exists. December fixes that,
  because a third of SSEPD's caseload is scanned paper.
- Rather than build a tool and hope it gets used, we answer officer questions by
  hand for two months and log every one.
- We are not building the low-signal flag for SSEPD. Safety judgement, and the
  one thing here that needs your sign-off rather than a note.

---

## 1. Where this stands

There is no integration, so officers read our outputs on a separate screen and
retype what they choose into the portal. There is one grievance officer per
department, and their account both registers grievances and forwards them on.

Two things have changed since August. **We have no API access.** The read-only
grievance endpoint we would read cases from is no longer running, and we hold no
credentials for it, so getting it is two things rather than one. And our extract
of the grievance history ends in July 2025, thirteen months out of date over a
period when filings roughly doubled.

**What that costs, in one number.** Without API access, the only cases we can
take into our own system are the ones an officer registers from paper in hand.
The case has to both start at the department and arrive on paper. Everything
else is already inside the portal by the time they see it, where we cannot reach
it.

That intersection is 817 of SSEPD's 45,339 cases and 301 of Labour & ESI's
10,459. **About 2% of the caseload, five cases a week across both departments.**
It is also shrinking, because paper intake is digitising: Labour & ESI's share
fell from 12.3% in 2022/23 to 1.1% in 2024/25.

So there is a version of this project that runs with no API access, and it
reaches one case in fifty. That is a demonstration rather than a service.
Getting that access is what section 6 asks for, and the rest of this memo is
what we do in the meantime and why it is worth doing.

---

## 2. What we do in four months

Four tracks, running in parallel. None waits on another.

**The desk study.** Everything we can learn from the grievance history we
already hold. No permission, no travel, no software. Ghazal runs it from Patna,
starting now.

**Process mapping, department and field.** We already have the department level
in good shape from the August walkthroughs, so September is a write-up and a
correction round with both officers. The gap is the field: what happens to a
case once the officer forwards it, at the Collector's grievance cell, the block
office and the department's own district officer. Two districts, one per
department. I take the first on the October trip, Utkarsh the second from
November. Milinda on the write-up.

**Answering questions by hand.** From October, officers send us a question about
a case and we answer within a day. Every request logged.

**The app and its evidence.** Built at part-time pace. Its December deliverable
is numbers rather than a product: what our document reading, summarisation and
personal-information screening actually achieve, on Odia as well as English.

**December produces:** two department briefs in November, this project's first
tangible thing for a partner department; the integration memo; and the pipeline
numbers as its technical annex.

---

## 3. What we are finding out

**From the history we hold.** Volume and case mix by district and by how the
grievance arrived. How long cases take today and where they sit waiting. How
often the same petitioner comes back. What resolution time officers promise when
they assign a case, and whether they hit it. Underneath all of it, whether those
numbers mean what we say they mean, since several fields in the record have
never been validated.

Two analyses matter beyond the briefs. **The reach figure in section 1**,
extended by district and projected forward. And **how many cases actually reach
the grievance officer each week**, estimated from the case history rather than
asked. SSEPD's dashboard showed 27 cases sitting with the officer against 1,471
pending across the department, so their personal throughput may be far smaller
than department volume suggests. That number decides whether measuring an effect
on officer time is ever possible, and whether the question service sees five
requests a week or fifty.

**At the department.** The August walkthroughs settled most of it, including
where these officers sit: they both receive cases already routed to them and
register and assign new ones. What remains is how often each path is used. So
the department map is a write-up and a correction round, and the correction
round matters as much as the map. It is a small specific ask that gets a reply,
and a corrected map is the first thing we hand a department that their own
portal cannot produce.

**In the field, where we have nothing.** Our map stops the moment the officer
forwards the case. Everything after that, which is most of the elapsed time and
all of the actual redress, we have never observed. That gap is why our
turnaround numbers are currently uninterpretable: we can measure an eleven-day
gap between two recorded steps without being able to say whether it was a field
enquiry, a statutory wait, or nobody looking at it.

Two districts, chosen on filing volume. Per site: how a forwarded case arrives,
who reads it, what enquiry it triggers, how long each step takes, and what gets
typed back into the portal against what stays on paper. This is also the first
independent read anyone has on what the portal's completion dates mean.

The stopwatch baseline in October gives us the officer-time figure this project
has never had, stratified by how the grievance arrived, since a typed website
complaint and a scanned Odia letter are different tasks.

---

## 4. What we would build, and why little of it works from outside

Nothing here ships to an officer this year. This is what the app is built
toward, and it is the concrete form of the argument for API access.

**Every one of them is capped at the same 2%.** A case has to be inside our
system before we can do anything to it, and without API access only the
register-from-paper cases get there. What differs between features is how each
one behaves once capped, and whether our thirteen-month-old data gives the right
answer.

| Feature | Usable at 2% reach | Usable on data ending July 2025 |
|---|---|---|
| Which office inside the department should get the case | **Yes.** Fires about five times a week, and each answer stands on its own | **Yes.** It learns which office covers a district, and that barely changes year to year |
| Has this petitioner filed before, and what happened | **Yes**, on the same terms | **No.** It needs that person's complete history, and ours stops in July 2025 |
| The key facts of a scanned grievance, in text | **Yes**, on the same terms | **Yes.** How old our data is has no bearing on reading a document |
| Pending cases oldest first, with days left | **No.** It describes the officer's whole queue, so at 2% we would show them one pending case in fifty | **No** |

The first three act on one case at a time, so the cap makes them fire rarely
while each answer remains correct. The last describes a population, so a 2%
sample of it is a wrong picture rather than a small one, and there is no moment
at which anyone would type the other 98% in by hand. That is the feature the
API decides.

The second row is what has changed since August, and it is why ask 1 matters
more than its price suggests. Our thirteen-month blind window plausibly holds
more SSEPD filings than the entire history we hold, since volume was still
doubling when the extract stopped. Recency is where re-filing concentrates, so
we would tell an officer that a petitioner is filing for the first time when
they filed twice last year. One wrong answer an officer catches ends the
feature.

**How grievances arrive bounds all of it.** Just over a third of SSEPD's cases
and a fifth of Labour & ESI's come in on paper. For those, the portal's text
field holds a one-line stub an officer typed, twenty to thirty characters, and
the grievance itself exists only in the scanned document. Anything that reads
what the citizen wrote is blind on that third unless our document reading works,
which is why measuring it is a December deliverable rather than housekeeping.

**One exclusion needs your sign-off, because it is a safety judgement rather
than a product choice. We are not building the low-signal flag for SSEPD**,
which would mark a grievance as unlikely to need action. The signs officers
described are requests for government jobs, and requests for financial
assistance carrying no detail. Financial assistance is, in effect, SSEPD's
entire caseload. A flag built on those signs would systematically route
disability-benefit claims into a review queue. That is a foreseeable and
patterned harm to the population the department exists to serve, and no accuracy
figure would license it.

---

## 5. The four months, and the decision at the end

| Month | What happens |
|---|---|
| **September** | Screen shares with both officers, settling how often they register cases themselves and how many reach them each week. Process maps drafted and sent back for correction. The data asks tabled. Desk study starts. |
| **October** | One trip: department maps verified by watching the work, the stopwatch baseline, the question channel agreed face to face, and **the first district visited**. Desk study completes, briefs drafted. |
| **November** | Utkarsh joins and inherits a live relationship. **The two department briefs are hand-delivered.** He takes the second district and the question channel. |
| **December** | **The integration memo**, with the question log as evidence and the pipeline numbers as its technical annex. |

Running throughout and gating none of it: the Sarvam pipeline at scale, the Odia
reference sample transcribed, the app progressing.

Five people. Ghazal on the analysis from Patna, Milinda on the maps and the Odia
reference sample, Aparupa on the Odia work with her, Utkarsh in Odisha from
November and doing the second district, me in Bangalore with one trip in
October.

**In December we decide what 2027 looks like**, against four questions this work
answers. Did API access land. Does the question log show officers want what we
would build. Are the pipeline numbers good enough to put in front of an officer.
Is there anyone to finish the app. A measured effect on officer time needs all
four plus a throughput we have not established, so it is a 2028 question.

---

## 6. What we need from you

**Three data asks, in rising order of cost.** The third is the one that matters;
the first is the one you can probably get this month.

1. **A fresh extract of the grievance history, to current date.** A one-time data
   request, not a system change. Ours ends in July 2025, so we are describing a
   caseload thirteen months out of date and would tell an officer that a repeat
   petitioner is filing for the first time. Smallest thing on this list and it
   improves everything else.
2. **A record-level report the department can export.** Whatever their login can
   already download, one row per grievance. If one exists, an officer
   downloading it weekly gets us most of the way to current state at no cost to
   anyone. We are confirming this month whether it exists.
3. **The read-only grievance API restarted, and scoped to these two
   departments.** The endpoint no longer runs, so this is two things: revive it,
   then give us read access. **This is the ask that decides whether any of this
   scales.** Outside the workflow we reach 2% of the caseload; with read access
   we reach all of it. It remains far short of the integration OCAC refused. We
   would only read, we would change no workflow, and it covers two departments.

**Three smaller permissions:**

4. **Written sign-off naming both departments** for a bounded engagement.
5. **District access in two districts**, to a Collector's grievance cell and a
   block office. This runs through a different chain from the department
   sign-off, so it needs asking in September rather than assuming in October. It
   is what makes the field mapping in section 3 possible, and without it our
   turnaround numbers stay uninterpretable.
6. **Permission to log what officers ask us and what we send back.** A
   department suggestion labelled as AI-generated is already on the live
   assignment screen, and nothing anywhere records whether officers accept it.

**And one sign-off**, on the low-signal exclusion in section 4. It is a safety
judgement about disability-benefit claims and it should be yours, not mine.

Re-confirming your 27 July determination can wait. The citizen phone survey that
made it necessary is out of scope this year. It should be re-asked before any
citizen contact resumes, and data-protection obligations apply either way.

---

## 7. What we will not claim

- No routing time saving. That analysis failed to reproduce and stays withdrawn.
- Not the routing accuracy figures measured on the same data the model learned
  from. The figures from data the model never saw are much lower, and they
  measure agreement with past practice rather than correctness.
- Thirteen seconds is machine time, not officer time.
- Eleven to twenty-three days is a measured gap between recorded steps, not a
  saving we can deliver.
- For this pilot specifically: no department-level effect, no state-level
  effect, and no citizen-outcome claim.
