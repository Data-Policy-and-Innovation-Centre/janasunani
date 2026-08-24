---
title: Governed decision support over public case records
subtitle: A capability brief for public-system partners
author: Data, Policy and Innovation Centre
date: 23 August 2026
organisation: Data, Policy and Innovation Centre
partnership: Government of Odisha and the University of Chicago Trust
status: Working draft. Janasunani figures are proof points, not promised performance.
---

# What this is

We help public agencies read what citizens actually wrote, decide what to do
with it, and then measure whether the result made the service better. Janasunani,
Odisha's grievance system, is the environment where the approach was built and
tested. It is the proof environment, not the limit of the approach.

The pattern recurs wherever a public team must read mixed records, decide where
a case belongs, and answer for the outcome: grievance portals, welfare and
benefit casework, municipal and utility complaints, helplines, regulatory case
queues, inspection follow-up.

Every number in this brief describes Janasunani. None of it is a performance
guarantee on a different corpus, in a different language mix, under a different
taxonomy. A new partner starts with its own baseline and its own governed
sample, and the first deliverable is that baseline rather than a model.

# The problem this addresses

Existing dashboards read the case record: district, department, category, dates,
status. They count and compare anything in it, and they do that well. Two things
they cannot do are read what the citizen wrote, and recognise that two records
describe the same problem.

There is a third gap, and it is usually the one that matters most. Case systems
tend to measure disposal and pendency, both of which are counts. In Janasunani
the reporting surface carries no time-related metric at all: no median age, no
time to resolve, and a disposal rate whose denominator is filings rather than
problems or citizens. Whether the citizen actually benefited is recorded and
nothing reported depends on it.

So the first contribution is usually not a model. It is a measurement that the
organisation does not currently have and cannot get from its own reports.

<!-- pagebreak -->

# What has been measured, and on what

**Table 1. Janasunani results, with the evidence status of each**

| Capability | Result | Status |
|---|---|---|
| Remove personal information before analysis | 77.9% of items found, 55.0% on exact characters | Development. Recall only; 824 spans predicted against 480 labelled, so no precision figure exists |
| Separate cases needing clarification from the rest | 13/13 caught, 3/44 ordinary cases also flagged | Development, n=57. Frontier-adjudicated, not officer-confirmed. Not release-eligible |
| Suggest a category as a ranked shortlist | 90.9% top-3, 46.6% top-1 | Development, n=3,160, test viewed. Agreement with the recorded label |
| Suggest a route as a ranked shortlist | 69.0% top-3, rising to 79.7% where intake data is informative | Development, n=208,267. Where cases were sent, not where they resolved best |
| Draft a summary an officer can check | 8/26 usable unedited | Development, single judge. Residual private detail in some drafts |
| Distinguish repeat filing from broad demand | Officer-confirmed duplicates as the baseline | The incremental reviewable gain is not yet claimed |

*Note: no row above is a release result. Each rests on a test that was viewed during development, or on labels no officer confirmed, or both. They compare candidates; they do not promote one.*

The bundle behind these figures is not publication-ready, and
0 of 2 required impact
artifacts exist. Impact is therefore reported as not measured, which is
different from measured and found to be zero.

<!-- pagebreak -->

# How the approach is built

The order matters more than any individual component, and it is close to the
reverse of the usual one.

**Table 2. The sequence**

| Step | What it involves | Why it comes here |
|---|---|---|
| Reconcile the record | Define denominators, agree what a case is, freeze a baseline | Every later number is meaningless without it, and this is where most surprises surface |
| Redact first | Remove personal detail before any downstream analysis; declare each trust boundary | Matching, grouping and model work all run over citizen writing |
| Cheap local baselines | Simple text models before anything larger | They frequently win, and they establish what a larger model has to beat |
| Compare candidates on identical cases | Versioned, on one frozen sample | Otherwise "better" is unfalsifiable |
| Pin and abstain | Fix exact model bytes and parameters; fall back when evidence is weak | A moving model is not a measurable one |
| Log exposure and decision | Record what was shown and what the officer did | Without this no causal claim about benefit is ever available |

Two of those deserve emphasis because they are where the approach most often
differs from a vendor engagement.

Cheap baselines really do win. In Janasunani a word-and-character text model
beat a pretrained multilingual transformer on the actionability task. The
interpretable duration model also generalises better than gradient boosting. We
start cheap not out of thrift but because an unbeaten simple baseline is
evidence that the problem is not what everyone assumed.

Local by default. Production models run on controlled infrastructure. External
frontier models are used selectively and with explicit approval, for one-time
adjudication and research rather than in the serving path, and their use is
recorded with its provenance limits.

<!-- pagebreak -->

# The part most engagements skip

Model quality, officer behaviour and citizen outcome are three different claims,
and a good score on the first implies nothing about the others.

An offline accuracy figure says a model would have agreed with a historical
label. It does not say an officer saw the suggestion, or accepted it, or that
the case moved faster, or that the citizen was better served. Each of those
needs its own instrument, and the chain breaks at the first missing one.

In Janasunani a routing suggestion already ships in the live portal. There is no
measured accuracy for it and no record of when an officer overrode it. That is
the ordinary situation, and it is why the exposure-and-decision log is a
precondition rather than a refinement.

<!-- pagebreak -->

# A worked example: routing

Routing is the clearest illustration of why measurement has to come before
optimisation, so it is worth following in full.

The production rule sends a case wherever cases of its type have gone before.
That has a specific blind spot: it learns where cases *were* sent, never where
they were *handled well*. A department that receives every land dispute in a
district and resolves none of them is, to that rule, the right destination.

The obvious correction is to route for speed. It fails, and the failure is
instructive. Closure is an action the officer controls directly, so an objective
rewarding short durations without qualification is maximised by closing
everything immediately and doing nothing. This is not hypothetical: the most
common closing remark in the system claims no action, and among standard-phrase
closures the ones claiming action are 18 days *slower* at the median than those
claiming none.

So the question has to be posed as a constrained one. Can cases be disposed
faster *without* losing whether action is taken. Not "how fast are the cases that
came out correct", which compares a different set of cases under each routing
choice and rewards a route for abandoning its hard ones.

Getting that distinction wrong is measurable, not merely theoretical. Adding
the joint department-and-chain assignment reduces validation prediction error by
0.0305 among cases selected as correct
completers, but only 0.0002
among closure-proxy actionable completers. The restricted and weighted rungs are
identical to the latter because the closure-derived proxy is observed only after
resolution. That is a post-treatment selection and measurement warning, not an
estimate of what an intake-time constraint would do.

**Table 3. What the corrected analysis supports**

| Question | Answer |
|---|---|
| What did the 2024 comparison show? | Augmented estimates favoured alternative assignments by 12.4 to 26.8 days on 450,567 common-support cases |
| Did the result repeat in 2025? | No. The same estimates were -2.3 and 0.1 days on 113,535 cases, compatible with no gain |
| Is historical routing shown to be time-optimal? | No conclusion. Direct and augmented estimates disagree, and no correctness-constrained threshold is resolved |
| Is this a recommendation? | No. Assignment provenance, intake-time actionability and a governed pilot remain missing |

*Note: the treatment is the jointly selected department and complete intended chain. The available snapshot does not prove those fields preserve the initial assignment, and actionability is inferred from closure rather than intake.*

The transferable lesson is not the number. It is that a plausible objective, a
plausible sample and a plausible model can each be individually reasonable and
still combine into a measurement of the wrong thing, and that this is detectable
before anything is deployed.

# Working with us

**Table 4. What an engagement needs, and what it produces**

| From the partner | From us |
|---|---|
| A named service problem, stated as a decision someone makes | A reconciled baseline and an honest account of what the record can and cannot answer |
| A governed extract, or secure access on the partner's infrastructure | A working local pipeline that runs where the data lives |
| The officers who know what a good decision looks like | Scorecards with denominators, intervals and stated limitations |
| Agreement on the outcome that would count as success | A decision on whether the use case deserves a pilot, including "no" |

The last item is the point. The deliverable of a first engagement is evidence
about whether the problem is worth automating, and that answer is sometimes no.
A finding that a simple query answers the question, or that the record cannot
support the claim anyone wanted, is a successful outcome and is cheaper to reach
early.

# What we do not claim

No officer time saved. No correct-authority rate. No faster resolution. No
change in citizen satisfaction. Those require exposure logging, adjudication
against something other than historical labels, and a governed rollout with a
comparison group. Offline scores cannot substitute for any of them.

Nor do we claim the Janasunani figures transfer. A different corpus, language
mix, taxonomy and set of officers will produce different numbers, and the first
job of a new engagement is to find out what they are.

*Source: benchmark-backed figures come from development bundle 0f74815cb1291ba1, publication_ready=false. Definitions and reproduction limits are in the long report and docs/QUALITY_BENCHMARKS.md.*
