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

*A decision memo. Four asks at the end. The full design is in the internal plan;
this is the argument, not the implementation.*

---

## 1. What we gained and what we lost

Two departments have approved a pilot: Social Security & Empowerment of Persons
with Disabilities, and Labour & Employees' State Insurance. That is the first
named partner we have had, and it is the thing we asked for on 17 August.

Two conditions came with it, and both cost us something real.

**OCAC has not approved integration.** Our system cannot sit inside the legacy
Janasunani workflow. Officers will look at our outputs on a separate screen and
re-key what they choose into the portal by hand. Nothing about a pilot grievance
is written to, or readable from, the production system.

**Each department has one grievance officer.** Not one office, one person. Two
officers in total across the pilot.

The consequence, stated before anything else: **the stepped-wedge design we
built is unrunnable, and this pilot cannot produce a causal estimate of the
effect on citizens.** The wedge needed roughly 320 offices adopting in a
staggered order, with outcomes read automatically from the government's own
records. We have two officers and no automatic outcome capture. No estimator
recovers a department-level or state-level effect from that, and none should be
promised.

What we can still do is worth doing, and the rest of this memo is about what
survives, what it identifies, and what it costs.

---

## 2. What is still identified

We put **every** grievance reaching these two officers through our console, and
randomise, case by case, whether the AI panel is displayed or blank. Both arms
work the case in the same interface and record the same decision before
re-keying. The model still runs on control cases with its output withheld, so we
hold the counterfactual prediction.

That gives a clean, design-based **within-officer, per-case effect of showing
decision support**, with the officer's handling time as the primary outcome.
Randomisation is blocked within officer-week, so arms stay balanced against
learning and caseload drift over a short window. Inference is a Fisher
randomisation test against the sharp null, with conventional intervals reported
alongside. With two officers and a few hundred cases, an exact design-based test
is the honest choice; nothing here leans on asymptotics.

Two threats, and neither is assumed away.

**Contamination.** One officer sees both arms and learns from the treated ones.
The bias is signed: learning transfer makes control cases better, which pushes
the estimate toward zero. A positive result survives it. A null does not
distinguish no effect from full contamination, and we will say so in that
sentence rather than in a footnote. We track it by testing whether the control
arm improves as cumulative treated exposure accumulates.

**Attenuation through re-keying.** The intervention reaches a citizen only
because a human retypes it. If most of what the console produces is discarded at
that step, the treatment is diluted before it can do anything. We audit a random
tenth of treated cases each week against the portal record. If fidelity is below
the threshold we set at lock, the attenuation *is* the finding, and we make no
citizen-effect claim.

How each outcome is captured, given no integration:

| Outcome | Source | Confidence |
|---|---|---|
| Officer handling time, screens, revisits | Console telemetry, both arms, calibrated against a stopwatch baseline collected before launch | High |
| Officer decision: authority chosen, days allowed, remark | Recorded in the console before re-keying | High, self-reported |
| Re-key fidelity | Weekly 10% audit against the portal | Medium, and a headline feasibility finding in its own right |
| Time to first downstream action; 30 and 60-day resolution | Read-only API if granted; else a record-level portal export if one exists; else officer log | Low to medium |
| Citizen-reported resolution and satisfaction | Phone survey in Odia, at a fixed horizon after filing, every eligible case, whether or not the portal has closed it | Medium; a census, not a sample, which the small scale makes affordable |

The survey is deliberately not conditioned on closure. Closure is an outcome the
intervention may itself move, so surveying only closed cases would condition on
the thing we are studying.

Expect the citizen endpoints to be descriptive. The power calculation lands at
the end of September and will most likely show their intervals are too wide to
carry a claim. We would rather say that at lock than discover it at readout.

---

## 3. Why we are reversing our own design rule

The A/B plan rejected case-level randomisation on interference grounds: an
officer seeing both arms learns across them, and moving one case changes another
office's inbox. That reasoning was correct and we are overriding it knowingly.

At two officers, every coarser unit has not less power but **none**: there is no
panel of clusters to estimate on. The choice is a contaminated case-level design
or no randomised evidence at all. We take the contaminated design, sign the
bias, and bound it.

The second half of the old objection is weaker here. These officers sit
downstream of the department routing decision, so treating one case does not
move another into or out of their queue. That claim depends on a fact we have
not yet verified, which is the first thing we check in the field. If it turns
out these officers also register intake, the objection returns in full and the
design needs revisiting before launch.

The superseded wedge is not wasted. It is costed, reviewed, and it is the
clearest statement we have of what integration would buy. It becomes the
technical annex to the integration ask.

---

## 4. What we will test, and the one thing we will not build

The features worth testing are the ones the portal conspicuously lacks and our
corpus work already supports.

**An ageing and deadline view.** The officer's pending cases, oldest first, days
elapsed, days to the 30-day mark. There is no time metric on any screen of the
live portal: it reports counts and disposal percentages and nothing else. This
needs no model and carries no model risk.

**A repeat-filer panel.** "This petitioner has filed three times before; here is
what happened." Officers told us in August that they do not know their own
repeat rate. We can compute it across the full history.

**A document summary**, only if it clears a factuality and privacy gate first.
On its current development set it leaves residual personal information in four
of twenty-six drafts. That is not showable to an officer, and it either clears
or it waits for a later wave.

**One exclusion needs your sign-off, because it is a safety judgement rather
than a product choice. We are not building low-signal triage for SSEPD.** The
markers officers described for a low-signal grievance are requests for
government jobs and requests for financial assistance carrying no detail.
Financial assistance is, in effect, SSEPD's entire caseload. A triage flag built
on those markers would systematically route disability-benefit claims into a
review queue. That is a foreseeable and patterned harm to precisely the
population the department exists to serve, and no accuracy figure would license
it. It is out of scope for this pilot, and any later reconsideration for Labour
would need an officer-confirmed gold set that does not exist.

---

## 5. Volume, and one open question about the second department

Labour & ESI may be too small to randomise on its own. In our historical data
its largest single case type carries about a seventh the volume of SSEPD's, and
it does not appear at all in the coarser groupings we can route on. The
September power calculation settles it.

If it cannot be powered, we run Labour & ESI as a feasibility and qualitative
arm and randomise SSEPD only. That is a stated go/no-go with a date, decided
before launch and not after seeing any outcome.

---

## 6. Timeline and cost

| Quarter | What happens |
|---|---|
| **Q4 2026** | Baseline analysis on the existing history. Remote walkthroughs with both officers. Permissions tabled. Project manager onboards in November. One Odisha trip: district visits to Collector, BDO and department offices, plus a stopwatch baseline of the officers' current work. We hand each department a report on its own caseload that nobody has ever given them. |
| **Q1 2027** | Console built and deployed. Four-week shadow phase with the panel always on, no randomisation: usability, training, and the re-keying measurement. Then the analysis plan is locked and tagged before any arm is compared. |
| **Q2 2027** | The randomised pilot runs, fourteen to sixteen weeks. An interim feasibility readout in March. |
| **Q3 2027** | Citizen follow-up calls, a closing field visit, analysis against the locked plan, and the report. |

Two trips to Odisha for me. One field-based project manager from November, one
reallocated research associate from now, and a part-time Odia-speaking surveyor
for three months in the spring. **The project manager's November start is the
binding constraint on all field work**, which is why the desk analysis is
sequenced first.

**If the integration case is needed sooner:** the department reports plus the
shadow phase alone make a credible feasibility argument by **February 2027**. It
is the randomised half that runs to July.

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

Unchanged from every prior readout, and worth restating because a pilot is
exactly when these drift:

- No routing time saving. That analysis failed to replicate and stays withdrawn.
- Not the in-sample routing figures. The held-out numbers are much lower and are
  agreement with past practice, not correctness.
- Thirteen seconds is machine time, not officer time.
- Eleven to twenty-three days is a measured gap between recorded steps, not a
  saving we can deliver.
- And, for this pilot specifically: no department-level effect, no state-level
  effect, and no citizen-outcome claim unless the interval turns out to support
  one.
