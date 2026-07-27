# Janasunani — Delivery plan for the 14 August demo

> For management. Plain language, no jargon.
> The engineering detail is in [ROADMAP.md](ROADMAP.md); this page is what we are
> promising, by when, and what happens if something slips.
>
> **Written 27 July 2026. 15 working days to the demo. One engineer.**

## 1. The honest headline

The Executive Director asked for five things. **We can show all five on 14 August,
but two of them as evidence and design rather than as working software**, and one
piece of groundwork has to be started today or the whole plan fails.

The binding constraints, stated plainly:

- **One engineer, 15 working days.** There is no parallel work. Anything listed as
  "at the same time" in the engineering roadmap has to become sequential here.
- **The system is not yet running on the government cloud box.** That was already
  outstanding before this re-scope, and nothing can be demonstrated until it is.
- **The privacy measurement depends on manual labelling**, which is slow, cannot be
  automated without destroying its purpose, and blocks two of the five outcomes.

Everything below follows from those three facts.

## 2. The five outcomes, in plain language

| # | What the ED asked for | What we will show on 14 August | Confidence |
|---|---|---|---|
| a | The old DSI system, rebuilt | A grievance document goes in and a routed, redacted, categorised grievance comes out, start to finish, live. Plus a table showing how well the privacy step actually works | **High** |
| b | Spam and duplicate detection | The system flags likely junk, links repeat submissions, and groups mass campaigns. Plus a count of how much of the existing 1.37 million backlog is duplicate work | **High** |
| c | An intelligence layer | A supervisor screen with a handful of trusted management numbers and an alert when complaints spike in a district | **Medium-high** |
| d | A/B testing of the AI | A written experiment design, a calculation of how big an effect we could actually detect, and a first look at where the AI agrees with officers. **Not a running experiment** | **High for the evidence, not applicable for a live trial** |
| e | Benchmark Sarvam | A side-by-side scorecard of Sarvam against our current models on Odia, romanised Odia and English, with a switch to move between them | **Medium** |

**Why (d) and (e) are not "working features".** A live A/B trial needs officers on
the system and departmental sign-off on which offices go first; neither can happen
by 14 August, and a fake one would be worse than none. The Sarvam work is a
measurement exercise by nature: the deliverable *is* the scorecard. Presenting
either as finished software would be overselling.

## 3. Where things stand today

Written in words, not symbols.

| Area | Status |
|---|---|
| Historical data loaded (1.37m grievances) | **Done** |
| Document processing pipeline, all stages | **Done**, but never run start to finish |
| Live inference and the demo web app | **Done in draft**, connected but not deployed |
| Running on the government cloud box | **Not done.** Built and reviewed, never switched on |
| Privacy measurement (the "gold set") | **In progress.** Draft prepared, manual labelling not started |
| Spam and duplicate detection | **Not started** |
| Management metrics and spike alerts | **Not started** |
| A/B experiment design | **Not started** |
| Sarvam benchmark | **Not started** |

## 4. The plan, week by week

### Week 1 — 27 to 31 July: make it real, and start the slow thing

| Deliverable | Acceptance test | Fallback if it slips |
|---|---|---|
| Start the manual privacy labelling **on day one** | 85 pages corrected by a human | This is the critical path. It cannot be compressed later |
| Bring the system up on the government cloud box | A grievance submitted through the browser, on the box, returns a result | Demo runs from a laptop. Weakens the story, does not kill it |
| Run the pipeline start to finish once | Document in, routed grievance out, counts verified at every step | Demo a stage-by-stage walkthrough instead of one continuous run |

### Week 2 — 3 to 7 August: the two visible new features

| Deliverable | Acceptance test | Fallback if it slips |
|---|---|---|
| Spam flagging and duplicate/campaign grouping | Detects known repeat submissions in a held-out sample; never auto-rejects anything | Ship duplicates only, drop spam scoring. Duplicates are the stronger result anyway |
| How much of the backlog is duplicate work | A number, broken down by district and category | Report on a sample rather than all 1.37m |
| Three to five management metrics and one spike view | Each metric reconciled against the source data; one real spike found and explained | Fixed dashboard only, no spike detection |

### Week 3 — 10 to 14 August: measurement, and rehearsal

| Deliverable | Acceptance test | Fallback if it slips |
|---|---|---|
| Privacy scorecard from the labelled data | Missed-PII rate reported per data type and per language | Report English only, and say plainly that Odia is unmeasured |
| Sarvam scorecard | Sarvam vs our models on the same test data, per language | Show Odia document reading only. That is the comparison that matters most |
| Experiment design, power calculation, agreement study | A written design, an honest statement of what size effect is detectable, and where AI and officers agree today | Design and power calculation only; drop the agreement study |
| **Freeze on Thursday 13 August.** Friday is rehearsal | A full run-through with no code changes | None. Do not move this |

## 5. What blocks what

The order is not a preference. These are real dependencies.

1. **Manual privacy labelling** blocks the privacy scorecard **and** the Sarvam
   benchmark, because both are measured against it. It is slow, it is manual by
   design, and it is the only item that cannot be rescued by working harder in
   week 3. **Start it today.**
2. **Duplicate detection** blocks the spike alerts, because a single mass campaign
   would otherwise look like a genuine surge in complaints.
3. **Cloud deployment** blocks the demo itself.
4. The Sarvam work needs its commercial terms agreed before any real citizen data
   is sent. See §7.

## 6. What we are deliberately not doing before 14 August

Listed so nobody expects them on the day.

- A live A/B trial with officers.
- Running Sarvam's large model on our own hardware. We use their hosted service
  for the benchmark and keep self-hosting as a proven fallback option, not an
  August deliverable.
- The model-management service (MLflow). The benchmark does not need it.
- Natural-language querying of the data. It is the most impressive-sounding item
  and the least trustworthy; it comes after the basic numbers are trusted.
- Comparing offices against each other. Doing that fairly needs statistical
  adjustment and a policy review, and doing it unfairly would be worse than not
  doing it.
- Odia language models throughout the pipeline. The Sarvam benchmark tells us
  which ones are worth adopting; adoption comes after.

## 7. Decisions we need from management

| # | Decision | Needed by | Why it matters |
|---|---|---|---|
| 1 | Written terms from Sarvam on whether our data trains their models, how long they keep it, and deletion | **Before any real data is sent**, so by 5 August | Without it we cannot use Sarvam on real grievances, and the benchmark runs on synthetic data only, which is much weaker |
| 2 | Who formally records that this evaluation does not need ethics review | Before the experiment design is published | It is very likely exempt. It should still be recorded as a decision by a named authority, not assumed by the engineering team |
| 3 | Confirmation that the demo audience may see real citizen data, and under whose login | 7 August | Determines whether we demo on real or synthetic grievances |
| 4 | Whether any department has agreed in principle to an A/B trial later | Useful by 14 August | Changes how we present outcome (d): a designed trial with a willing partner is a much stronger story than a design alone |

## 8. Risks, in plain language

| Risk | Likelihood | What it does to the demo | What we do about it |
|---|---|---|---|
| Manual labelling takes longer than expected | **High** | Privacy scorecard and Sarvam benchmark both weaken | Started day one; report partial results per language rather than waiting for completeness |
| One engineer, no slack | **High** | Any single delay pushes into rehearsal week | Every deliverable above has a named fallback. Scope gets cut, the date does not move |
| First cloud deployment goes badly | Medium | Nothing can be shown on the box | Demo from a laptop; the code path is identical |
| Sarvam terms not agreed in time | Medium | Benchmark runs on synthetic data only | Prepare the synthetic benchmark in parallel so the comparison exists either way |
| Odia results come back poor | Medium | An uncomfortable but honest finding | This is a real result, not a failure. It is the argument for the next phase of funding |
| Spam detection wrongly flags real grievances | Low, by design | Reputational, if seen | The system never rejects anything. It only advises, and an officer decides |

## 9. What "good" looks like on the day

A single sentence per outcome, which is what we should be able to say on 14 August:

- **(a)** "Here is a real scanned grievance going in, and here is what comes out,
  with the personal details removed, and here is how often we miss one."
- **(b)** "Here is how much of the existing backlog is the same complaint filed
  twice, and here is a campaign of 400 citizens that we can now see as one issue."
- **(c)** "Here are five numbers the department can trust, and here is a district
  where water complaints tripled last week."
- **(d)** "Here is how we would prove this system actually helps, how many offices
  we would need, and how long it would take to know."
- **(e)** "Here is how Sarvam compares to what we have on Odia documents, and here
  is the switch that lets us change our mind later."
