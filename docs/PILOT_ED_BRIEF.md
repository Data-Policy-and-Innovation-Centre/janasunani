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

This memo asks for six permissions and one safety decision in section 6.

---

## Summary

- **Without API access, we can reach about 2% of the caseload.** A case must
  start at the department and arrive on paper. Only then can we take it into
  our system. This is 817 of SSEPD's 45,339 cases and 301 of Labour & ESI's
  10,459 cases: about five cases a week across both departments. The share is
  falling as paper intake becomes digital.
- **December's deliverables are three things:** two department briefs,
  hand-delivered in November; an integration memo in December; and measured
  accuracy results for our document-reading pipeline.
- Our grievance-history extract ends in July 2025. It is thirteen months old,
  and filings roughly doubled during the period it does not cover. A fresh
  extract is the cheapest and most useful request we can make.
- The data requests, from least to most costly, are a fresh extract, a report
  that the department can export, and a restarted read-only grievance API. The
  API determines whether this can grow beyond a demonstration.
- The data analysis and process mapping will run while the app is built. They
  do not depend on the app.
- **We understand the department workflow but not what happens after a case is
  forwarded.** Two district visits will fill that gap. They need separate
  permission from the department sign-off.
- We have not run the Sarvam pipeline at scale or on Odia. We therefore have
  no accuracy result for scanned documents. December will produce that result;
  about one-third of SSEPD's cases arrive as scans.
- From October, we will answer officers' case questions by hand and record
  every request. This will show whether a tool is useful before we build it.
- We are not building the low-signal flag for SSEPD. This is a safety decision
  that needs your sign-off.

---

## 1. Current situation

There is no integration. Officers would read our output on a separate screen
and type anything useful back into the portal. Each department has one
grievance officer. That officer both registers new grievances and forwards
cases that have already been routed to the department.

Two things have changed since August. **We do not have API access.** The
read-only grievance endpoint is no longer running, and we have no credentials
for it. Restoring access therefore requires both restarting the endpoint and
giving us credentials. Our grievance-history extract also ends in July 2025,
thirteen months ago, during a period when filings roughly doubled.

Without the API, we can only take a case into our system when a department
officer registers it from paper. The case must both start at the department and
arrive on paper. All other cases are already in the portal by the time the
officer sees them, so we cannot reach them.

That gives us 817 of SSEPD's 45,339 cases and 301 of Labour & ESI's 10,459
cases. **This is about 2% of the caseload, or five cases a week across both
departments.** It is also shrinking: Labour & ESI's share fell from 12.3% in
2022/23 to 1.1% in 2024/25.

We can run a demonstration without API access, but it will reach only about one
case in fifty. API access is what would turn it into a service. Sections 2 to 5
describe what we can do while that access is being considered.

---

## 2. Plan for the next four months

Four tracks will run in parallel.

**Data analysis.** We will analyse the grievance history already available to
us. This needs no department permission, travel, or software changes. Ghazal
will start this work from Patna.

**Process mapping.** The August walkthroughs gave us a good view of the work at
the departments. In September, we will write the maps and ask both officers to
correct them. We still need to observe what happens after an officer forwards a
case: at the Collector's grievance cell, the block office, and the department's
district officer. We will visit two districts, one for each department. I will
make the first visit in October; Utkarsh will make the second from November;
Milinda will write up the maps.

**Answering questions by hand.** From October, officers can send us a case
question. We will answer within one day and record every request.

**The app and its evidence.** The app will be built part time. Its December
deliverable is not a finished product. It is measured evidence of what our
document reading, summarisation, and personal-information screening can do in
both Odia and English.

**December's deliverables:** two department briefs, delivered in November; an
integration memo; and the pipeline results as a technical annex to that memo.

---

## 3. What we need to learn

**From the history we have:** volume and case mix by district and by arrival
mode; how long cases take and where they wait; how often petitioners file
again; and the resolution time officers promise at assignment. We also need to
check whether the relevant fields mean what we think they mean. Several have
not been validated.

Two analyses are especially important. First, we need to extend the reach
calculation from section 1 by district and project it forward. Second, we need
to estimate how many cases reach the grievance officer each week from the case
history. SSEPD's dashboard showed 27 cases with the officer against 1,471
pending across the department. The officer's own throughput may therefore be
much lower than the department's total volume. This number tells us whether an
officer-time study is possible and whether officers will send five questions a
week or fifty.

**At the department.** The August walkthroughs showed that both officers
receive cases already routed to them and register and assign new cases. We
still need to learn how often each path is used. We will write one map for each
department and send it back for correction. A corrected map is a useful first
deliverable because the portal does not provide one.

**In the field.** Our map ends when the officer forwards a case. We have not
observed anything after that point, even though most of the elapsed time and
the actual redress happen there. For example, we can see an eleven-day gap
between two recorded steps, but we cannot yet tell whether it was a field
enquiry, a statutory wait, or inaction.

We will visit two districts chosen by filing volume. At each site we will see
how a forwarded case arrives, who reads it, what enquiry it leads to, how long
each step takes, and what is entered in the portal versus what remains on
paper. This will also give us an independent check on what the portal's
completion dates mean.

In October, we will use a stopwatch to measure officer time by arrival mode. A
typed website complaint and a scanned Odia letter are different tasks, so we
will keep them separate.

---

## 4. What we can build without API access

No feature will be delivered to an officer this year. The list below describes
what the app is intended to support and why API access matters.

**All features have the same 2% limit without API access.** A case must first
reach our system. Without the API, only cases registered from paper can do so.
The features differ in what they can do with that small set of cases and in
whether the July 2025 extract is current enough.

| Feature | Usable at 2% reach | Usable on data ending July 2025 |
|---|---|---|
| Which office inside the department should get the case | **Yes.** Fires about five times a week, and each answer stands on its own | **Yes.** It learns which office covers a district, and that barely changes year to year |
| Has this petitioner filed before, and what happened | **Yes**, on the same terms | **No.** It needs that person's complete history, and ours stops in July 2025 |
| The key facts of a scanned grievance, in text | **Yes**, on the same terms | **Yes.** How old our data is has no bearing on reading a document |
| Pending cases oldest first, with days left | **No.** It describes the officer's whole queue, so at 2% we would show them one pending case in fifty | **No** |

The first three features work one case at a time. They would run rarely, but
the answer for each case could still be useful. The last feature describes the
whole queue. A 2% sample would give the officer a wrong picture, and nobody
would type the other 98% of the queue by hand. That is the feature for which
the API is essential.

The repeat-filer feature also fails on stale data. Since volume was still
doubling when our extract ended, its missing thirteen months may contain more
SSEPD filings than the history we have. Repeat filings are likely to be recent,
so we could tell an officer that someone is filing for the first time when they
filed twice last year. One wrong answer that an officer notices would end the
feature.

**Arrival mode limits all document-based features.** Just over one-third of
SSEPD cases and one-fifth of Labour & ESI cases arrive on paper. For those cases,
the portal contains only a 20- to 30-character stub typed by an officer. The
citizen's actual grievance is in the scan. We cannot read it unless document
reading works, which is why measuring that capability is a December deliverable.

**One exclusion needs your sign-off. We will not build the low-signal flag for
SSEPD.** It would mark a grievance as unlikely to need action. The signs
officers described include requests for government jobs and requests for
financial assistance with little detail. Financial assistance is almost the
whole SSEPD caseload. A flag based on these signs could send disability-benefit
claims into a review queue. That is a foreseeable and systematic risk, and an
accuracy number would not make it safe.

---

## 5. Timeline and December decision

| Month | What happens |
|---|---|
| **September** | Screen shares with both officers to learn how often they register cases and how many reach them each week. Draft process maps and send them for correction. Submit the data requests. Start the data analysis. |
| **October** | One trip: watch the department work, measure officer time, agree the question channel, and visit the first district. Complete the data analysis and draft the briefs. |
| **November** | Utkarsh joins. He visits the second district, takes over the question channel, and hand-delivers the two department briefs. |
| **December** | Write the integration memo, using the question log as evidence and the pipeline results as its technical annex. |

The Sarvam run at scale, the Odia reference sample, and app development continue
throughout. They do not block the December deliverables.

Five people are involved: Ghazal leads the analysis from Patna; Milinda leads
the maps and the Odia reference sample; Aparupa supports the Odia work; Utkarsh
works in Odisha from November and visits the second district; and I lead the
design, take the October trip, and write the memo from Bangalore.

In December, we will decide what 2027 should look like. We will ask: Did API
access arrive? Do officers want the tools we would build? Are the pipeline
results good enough to show an officer? Is the app ready, and is there someone
to finish it? Measuring an effect on officer time also requires a reliable
throughput estimate, so that question belongs in 2028.

---

## 6. Decisions and permissions needed

**Three data requests, from least to most costly:**

1. **A current extract of the grievance history.** This is a one-time data
   request, not a system change. Our extract ends in July 2025, so every current
   analysis is stale and the repeat-filer lookup can be wrong. This is the
   smallest request and would improve all the other work.
2. **A record-level report that the department can export.** It should contain
   one row per grievance. If the existing login can produce it, an officer
   could download it weekly and keep us close to the current position without
   changing the system. We are checking whether it exists this month.
3. **A restarted, read-only grievance API limited to these two departments.**
   The endpoint must first be brought back and then made available to us. This
   is the request that changes the reach from about 2% to the full caseload. It
   is much smaller than the integration OCAC refused: we would only read data,
   change no workflow, and cover only two departments.

**Three smaller permissions:**

4. **Written sign-off naming both departments** for a bounded engagement.
5. **Access to two districts**, including a Collector's grievance cell and a
   block office. This requires a separate permission chain and is needed for
   the field mapping in section 3. Without it, our turnaround numbers remain
   hard to interpret.
6. **Permission to record officers' questions and our answers.** A department
   suggestion labelled as AI-generated already appears on the live assignment
   screen, but nothing records whether officers accept it.

**One safety sign-off:** approval of the low-signal exclusion in section 4.
This concerns disability-benefit claims and should be your decision.

The 27 July research-exemption decision can be revisited later. The citizen
phone survey that prompted it is out of scope this year. Reconfirm the decision
before any citizen contact resumes, and follow the data-protection obligations
either way.

---

## 7. Claims we will not make

- No routing-time saving. That analysis did not reproduce and remains withdrawn.
- No routing-accuracy claim based on the same data used to train the model.
  Held-out results are lower and measure agreement with past practice, not
  whether the route is correct.
- Thirteen seconds is machine time, not officer time.
- Eleven to twenty-three days is a measured gap between recorded steps, not a
  saving we can promise.
- For this pilot, no department-level effect, state-level effect, or citizen
  outcome claim.
