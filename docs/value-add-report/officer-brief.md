---
title: Janasunani 2.0
subtitle: What it does, and what we are asking for
author: Data, Policy and Innovation Centre
date: 23 August 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and the University of Chicago Trust
status: Working draft. Findings are from historical records, not from a pilot.
---

# What we are asking for

Four decisions. None of them is about automation. Each is small on its own, and
together they turn a set of working tools into something whose value to the
office can be proved rather than asserted.

**Table 1. Decisions requested**

| Decision | What it involves | Why it matters |
|---|---|---|
| Let officers check our work | A few officers review a fixed set of cases we have already scored | Nothing here has been confirmed by an officer. Until it is, no model can be recommended for real use |
| Name one department for a trial | A bounded rollout in one department, with a comparison group | Effects on officer time and on citizens cannot be worked out from historical records. They have to be observed |
| Record what the system suggests and what the officer does | A log entry each time a suggestion is shown and each time it is overridden | A suggestion already appears on the assignment screen today. Nobody knows whether it is any good, because nothing records what officers do with it |
| Have a few hundred closed cases read by hand | About 300 closing remarks, read and classified by an officer | It tests whether the closure-derived action proxy matches officer judgement; it does not replace a pilot |

The last row is roughly two days of one officer's time. It would repair one weak
measurement, but it cannot establish a routing effect on its own.

<!-- pagebreak -->

# The six-step workflow

A grievance arrives, as typed text or as a scanned petition. Six things have to
happen before anyone can act on it. Today a person does all six, and no record is
kept of how well any of them went.

**Table 2. The six steps**

| Step | Today | With this system |
|---|---|---|
| Read the petition | An officer reads it and retypes it as a complaint | A scanned petition is read and drafted in about 14 seconds; typed text in under a second |
| Remove personal details | Nothing removes them before the record is analysed | Names, phone numbers and addresses are stripped out first, every time |
| Decide whether it can be acted on | Officer judgement, unrecorded | Cases needing clarification are flagged, with the reason. The officer decides; the flag never blocks a filing |
| Spot a repeat complaint | Officers mark duplicates by hand when they notice them | The same problem and the same citizen are counted once |
| Choose a category | A dropdown | The likely categories, ranked. The right one is in the top three about nine times in ten |
| Send it to an office | A department and complete officer chain are selected together from a flat list | An experimental analysis compares admissible joint assignments; it is not ready to recommend one |

The last row is the new research question, and it is the subject of most of this
brief.

*Note: the fourteen seconds is machine time on a laptop, measured on test filings. It is not officer time. Officers describe roughly 10 to 15 minutes to turn a petition into a registered complaint, and that figure is their own estimate rather than something we have observed.*

<!-- pagebreak -->

# Three things the records already show

None of these depends on a model. They come from reading the grievance history
itself, and each one was a surprise.

## Nobody can see how long anything takes

The grievance cell reports disposal percentage and pendency. Both are counts.
There is no median age, no time to resolve, and no time-based measure anywhere on
the reporting screens. Whether a petitioner was actually helped is recorded in
the database, and no report uses it.

So the office cannot currently answer the question a citizen would ask first: how
long does this take, and is it getting better?

*Note: verified for the CM Grievance Cell, whose screens we reviewed on 11 August 2026. We believe it is general but have not checked a department or Collector login. Half an hour of screen sharing would settle it.*

## Closing a case and solving it are not the same thing

**Table 3. How 1,209,144 resolved grievances were closed**

| Closing wording | Cases | Share | Median days |
|---|---:|---:|---:|
| Disposed, no action claimed | 472,782 | 39% | 46 |
| Wording outside the standard set | 432,222 | 36% | 25 |
| Disposed with appropriate action | 280,887 | 23% | 54 |
| Disposed, beneficiary benefited | 23,253 | 2% | 44 |

Read the last column. Cases closed claiming action took 18 days *longer* than
cases closed claiming none. The fastest way to close a grievance is not to work
on it.

That is not an accusation. A question answered, an ineligible claim properly
refused and a case quietly dropped all close on the same phrase, and the record
cannot tell them apart. It is a warning about targets: any push on speed alone
would be met, and would make things worse.

<!-- pagebreak -->

## The portal counts filings, not problems

![Filings, distinct problems and distinct citizens in one district-year.](fig_dedup.png)

Four hundred citizens complaining about one road count as four hundred
grievances. Two hundred unrelated problems also count as two hundred. The two
need opposite responses and look identical in every report.

It also means the disposal rate can be raised by attracting duplicates and
closing them. Counting problems instead removes that.

*Note: 37,299 duplicate cases are the officers' own hand-marked baseline. How many more the software can find is not yet claimed, because we have not measured it.*

<!-- pagebreak -->

# The routing result

Every grievance is sent to an office, sometimes through a chain of three or four.
That decision is made from a flat list of about fifty preset routes, with no
default and nothing to indicate which is better.

There has never been a way to tell whether the choice was a good one. The only
available standard has been "the same as last time", and any system built on that
standard inherits every bad habit in the history. A department that receives every
land dispute in a district and resolves none of them looks, by that standard,
exactly right.

So we changed the question. Not *where do cases like this usually go*, but
whether an initially assigned department-and-complete-chain intention is
associated with faster resolution without reducing action taken. The existing
records can support a developmental comparison, not a routing recommendation.

**Table 4. What we found**

| Question | Answer |
|---|---|
| Did the 2024 analysis favour alternative assignments? | Yes. The augmented estimates were 12.4 to 26.8 days lower on 450,567 common-support cases |
| Did that result repeat in 2025? | No. The same estimates were -2.3 and 0.1 days on 113,535 cases, both statistically compatible with no gain |
| Has today's routing been shown to be slower? | No. The direct and augmented methods disagree, and the later-period augmented result does not reproduce the gain |
| Can we recommend acting on it today? | No. No routing gain is established, and the prior correctness frontier is withdrawn pending a corrected labelled-row rerun (#284) |

Three limitations prevent a stronger interpretation. First, actionability is
currently inferred from closing remarks, so the analysed population is selected
after resolution rather than defined at intake. Second, the available snapshot
does not prove that the stored department and chain are the immutable initial
assignment rather than a later state. Third, the direct prediction and augmented
estimators disagree sharply on the 2025 holdout. Reading 300 cases would improve
the first measurement; an assignment event log and governed pilot are still
needed for the other two.

*Note: an earlier internal comparison of two housing-scheme routes, 23 days against 48, was a raw average with no adjustment for the fact that harder cases travel further. It was never a saving.*

<!-- pagebreak -->

# What we expect this to change

This section is what we think will happen. It is not evidence, and it is kept
separate for that reason.

## Officer workload

Registering complaints, at the officers' own estimate of 10 to
15 minutes each, accounts for somewhere between
201,000 and 302,000 officer-hours across the
1,209,144 grievances already resolved. We are not claiming to save
any of it. That is the size of the pot a saving would come out of, and it is why
the question is worth measuring properly.

Three things would reduce it. Checking a draft instead of producing one is the
largest and the least certain, because a poor draft is slower than no draft.
Handling a repeat complaint once instead of four times is smaller and much more
certain. Sending a case straight to the office that resolves it, instead of
through three, removes two officers who each read the case from scratch.

## What citizens would notice

A governed routing pilot could test shorter waits; the historical analysis does
not establish them. Fewer occasions to file the same complaint again remain a
separate expectation, because a repeat is currently indistinguishable from a new
problem and so the system cannot see its own failures.

One warning. The database already records whether a petitioner benefited, and it
is tempting to report that as satisfaction. It should not be. Cases closed
claiming no action carry that flag *more* often than cases closed claiming
appropriate action, so it is not measuring what its name suggests. Satisfaction is
something a citizen tells you, and nothing currently asks them.

<!-- pagebreak -->

**Table 5. What could be measured, and when**

| Expectation | Can we measure it today? |
|---|---|
| Citizens wait less | No. Needs assignment/exposure logging, a comparison group and mature follow-up |
| Fewer repeat complaints | Almost. Needs a safe way to link a citizen to a problem |
| Cases reach the right office first time | Needs the log of suggestions and overrides, decision three |
| Action starts sooner | Needs the closing remarks read by hand, decision four |
| Officers spend less time per case | Needs timing built into the screen, and a staffing figure to divide by |
| Citizens are more satisfied | No. Nothing asks them. A survey channel would have to be approved |

The pattern in that table is the argument of this brief. Almost everything worth
knowing is close to measurable, and what blocks it is instrumentation and
permission rather than software. The software is the part that is nearly done.

<!-- pagebreak -->

# What we are not claiming

No officer minutes saved. No faster resolution. No improvement in citizen
satisfaction. 2 kinds of record are needed before any
impact claim can be made, and 0 of them exist. So
impact is reported as not measured. That is different from measured and found to
be zero, and closing the difference is the second decision in Table 1.

Three honest weaknesses, stated here rather than left to be found.

Personal-data removal is good at the things it can be checked on and unproven on
the rest. Across every redacted complaint in one district-year,
55,544 records, no mobile number, Aadhaar number, PAN or
non-government email survived. Names are harder. On a small hand-corrected set the
software found 78% of the personal
details a human marked, and we have not measured how often it removes too much,
which has its own cost.

The review flag was tested on 57 cases. It caught all 13 of the ones
that genuinely needed clarification, and also sent 3 of
44 ordinary complaints to review unnecessarily. That second figure
is the cost the officer pays, and 57 cases is a small test.

Draft summaries are the weakest part of the system. Most still need editing, and
some retain details that should have been removed. We are not asking for a
decision on them.

Everything above was measured on historical records, or on test filings on a
laptop. None of it was measured in a live office, which is what the second
decision would change.

*Source: figures come from benchmark bundle f64a999f47bf3240. Every definition, denominator and limitation is in the value-add report, which is the full evidence record, and in docs/QUALITY_BENCHMARKS.md.*
