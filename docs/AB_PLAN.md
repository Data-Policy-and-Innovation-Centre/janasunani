# A/B Analysis Plan — Estimator, Power, and Assignment Design

**Component (d) · Phase 16 · Issue #81 (parent #52)**
**Status:** DRAFT — to be locked before any outcome data is viewed
**Version:** 0.3 DRAFT — 2026-09-01 (amends 0.2 of 2026-08-10)
**Authors:** planner-opus (draft), accountable engineer (sign-off)
**Decides:** which offices get the tool when, what effect we can detect, and how we will estimate it.

> DELIVERY.md records this component as **Framework only** — a design and a
> calculation, not running software on the demo date. ROADMAP §5.4 is the
> specification; this document is a draft instantiation. It is not locked and
> none of its illustrative power values is an approved MDE.

> ⚠️ **Read §14 first.** The approved pilot (SSEPD and Labour & ESI) cannot run
> the stepped wedge specified in §3–§5. Sections 3, 4 and 5 are **superseded for
> that pilot** and retained deliberately as the design we would run with
> workflow integration. Sections 6, 7, 9, 10 and 12 carry forward substantially
> unchanged. §14 records what changed, why, and what is no longer identified.
> The operational plan lives in [PILOT_SSEPD_LABOUR.md](PILOT_SSEPD_LABOUR.md).

---

## 0. What this plan will lock and what it does not

**Will lock before launch:** randomization unit and procedure, primary and secondary outcomes
and their construction from OLTP/lake columns, the statistical estimator and
its clustering, the minimum detectable effect (MDE) given pre-period variance,
the assignment-service contract, the exposure/shadow log schema, and the harm
pause conditions.

**Does not lock yet:** the eligible intake clusters, departmental rollout
constraints, or the exact number of wedge steps. The department signs off those
constraints; within the approved strata, the seeded assignment procedure in
§7.1 randomizes switch order. The order must not be chosen using outcomes. The
power table below is a legacy algebra illustration, not a measured pre-period
result. Re-compute every endpoint from the frozen pre-period extract before
launch and record the extract and cluster-map hashes in the manifest.

**Blinding rule.** Once this plan is marked LOCKED (commit hash recorded
below), no outcome comparison by arm is viewed until the analysis harness
reports the pre-specified ITT endpoints in their locked order. Exploratory cuts
by arm before lock invalidate the MDE.

---

## 1. Question and the three claims

ROADMAP §5.4 separates three claims that must not be blurred:

| Claim | Evidence | This plan |
|---|---|---|
| The model is accurate | Gold sets, offline scorecard (Phases 13, 18) | Not this plan — reference only |
| The officer decides better with it | Exposure logs, override/agreement rates | §6–§7 — instrumentation |
| Citizens are better off | 30/90-day resolution, 90-day restricted mean time, repeat filing/reopen, officer-recorded benefit and separately citizen-reported satisfaction | §3–§7 — the experiment plus new linkage/instrumentation |

Offline agreement between model and officer labels is **suggestive, not
causal**; the models were trained on those same labels (imitation), and case
mix confounds both timing and assignment. The agreement study (§10) is reported
with that caveat on the slide, not in a footnote.

---

## 2. Causal estimand

- **Population:** eligible grievances filed in Odisha and assigned from an
  immutable, pre-treatment intake office. Citizen-level outcomes aggregate to
  the grievance.
- **Unit of randomization and clustering: an immutable intake-office transfer-
  network cluster.** Build the cluster map from pre-period authority transitions,
  freeze it with the analysis extract, and assign connected offices together
  where cross-office transfers are material. `complaints.office_id` may describe
  the current/final office, and `received_by_id` is an actor rather than a proven
  office equivalent; neither is accepted as the assignment unit without a
  source-semantics audit. If an office-level design is retained, pre-specify an
  exposure-mapping/spillover sensitivity rather than claiming SUTVA.
- **Treatment:** AI decision support is *available* to the officer on treated
  office-days (category suggestion, route suggestion, summary, spam/duplicate
  banner — all advisory, never auto-reject). Control office-days retain the
  current process unchanged. No path is removed in either arm.
- **Co-primary operational estimands:** intention-to-treat (ITT) effects of
  *being assigned* AI support on (a) seven-day first-meaningful-action
  attainment and (b) transfer-free first assignment, averaged over assignment
  clusters. Both require validated action/authority-transition semantics before
  lock.
- **Primary citizen estimand:** ITT effect on 90-day restricted mean time to
  resolution (RMST), accompanied by resolution-by-30/90-day risks. This retains
  unresolved cases through administrative censoring rather than calculating a
  resolved-only mean.
- **Secondary estimand:** treatment-on-treated / complier effect is reported
  only as a bounded sensitivity analysis under a stated exclusion assumption,
  with assigned as instrument for exposed. Primary inference is ITT.

Assigned and exposed are separate fields (§7); conflating them makes neither
ITT nor TOT identified.

---

## 3. Design — staggered intake-cluster rollout (stepped wedge)

### Why this design

- **Grievance-level parallel:** most power, but contaminates (officer sees both
  arms) and violates SUTVA via inbox composition. Rejected.
- **Intake-cluster parallel (50/50 forever):** cleaner but denies half the
  clusters the tool indefinitely; government partners rarely accept. Rejected.
- **Stepped wedge (staggered adoption):** every cluster is eventually treated,
  matches how a rollout would actually happen, retains identification from
  variation in adoption timing, and satisfies the "no cluster denied forever"
  constraint. **Chosen.**

### Structure

```
Period:   0 (all control) → 1 → 2 → … → S (all treated)
Wedge:    each period, a new block of intake clusters switches control → treated
          and stays treated thereafter (absorbing treatment).
```

- **S = 4–6 steps** is the working range; the exact S is the number of rollout
  blocks the department can staff for training/support. Fewer steps = longer
  within-step exposure per cluster (less period noise), more steps = more timing
  variation (supports identification) but shorter post-treatment windows for
  late adopters.
- **Stratified randomization of the switch order** on pre-period log filing
  volume, district, and baseline validated transfer rate. This balances load,
  geography and transfer-network intensity across early vs late adoption. The
  sequence is drawn once, published, and then frozen — it is a
  departmental sign-off artifact, not re-randomized.
- **No concurrent interventions** are scheduled in the wedge window that
  differentially affect early vs late clusters (verified with the department
  before lock). If one occurs, it is recorded as a time-varying covariate and
  the plan is amended before outcomes are viewed.

### What identifies the effect

Variation in *when* an intake cluster switches. Clusters that have not yet
switched are contemporaneous controls for those that have, within the same period
(controls for secular trend/seasonality).

---

## 4. Estimator — chosen and justified

### The bias to avoid

The default two-way fixed-effects (TWFE) regression

```
Y_{ct} = α_c + λ_t + τ·D_{ct} + ε_{ct}
```

is **biased under staggered timing with heterogeneous effects** (negative
weighting of late vs early adopters). With treatment effect varying by
cluster, by time-since-adoption, or by period, TWFE can even sign-flip. Merely
clustering the standard error does not fix the estimand.

### Primary estimator: Callaway–Sant'Anna (2021) — `ATT(g,t)` + aggregation

**Callaway and Sant'Anna, J. Econometrics 2021** (also Did  R package
`did`, Python `csdid`).

- Estimates group-time average treatment effects `ATT(g,t)` for each adoption
  cohort `g` (intake clusters switching at step g) at each calendar time t ≥
  g, using only not-yet-treated (or never-treated, if retained) as controls for
  that `g,t`.
- Aggregates to policy-relevant summaries:
  - **Overall ATT** (average over all treated assignment-cluster-periods) — primary.
  - **Event-study ATT(e)** by elapsed time since adoption (`e = t − g`) —
    secondary, to show whether effects grow or fade.
  - **Cohort-specific ATT(g)** — diagnostic for heterogeneity.
- Doubly-robust (`DR-IPW`) form: outcome regression + IPW on covariates
  (district, pre-period volume, baseline transfer rate). Requires only one of
  the two models correct.
- Inference: **bootstrap at the frozen assignment-cluster level** (or analytical
  multiplier bootstrap), the unit of randomization. Report 95% simultaneous
  confidence bands for the event study.

*Why CS as primary:*

1. Explicitly designed for staggered absorbing treatment with arbitrary
   heterogeneity.
2. Transparent aggregation — the overall ATT is a clearly weighted average of
   cohort effects, not a regression coefficient with opaque weights.
3. Doubly-robust and covariate-friendly (stratification variables enter
   naturally).
4. Well-understood, peer-reviewed, and implemented in audited packages.

### Robustness estimator: Sun–Abraham (2021) interaction-weighted

Re-estimate the event study via `SunAbraham` cohort-interacted saturation.
If CS and SA agree on sign and magnitude within sampling error, the result is
not estimator-dependent. Divergence triggers a pre-specified diagnostic
(cohort-size imbalance, anticipation).

### Sensitivity estimator: Borusyak–Jaravel–Spiess (2024) imputation

Imputation estimator (fit outcome model on untreated assignment-cluster-periods,
impute `Y(0)` for treated). Efficient under the same identifying assumptions
and a useful placebo check. Reported in appendix, not primary, because it is
less familiar to the departmental audience.

### Identifying assumptions (stated, to be probed)

1. **No anticipation:** grievances filed before an intake cluster's switch are not
   affected by its future switch. Plausible because filing precedes routing;
   tested via pre-trend ATT(e<0) ≠ 0 placebo.
2. **Parallel trends in untreated potential outcomes** conditional on
   stratification covariates (district, volume). Probed via pre-trend
   event-study coefficients and the Rambachan–Roth honest sensitivity
   interval (reported alongside the point estimate).
3. **Partial interference:** grievances may move between offices. The frozen
   pre-period transfer-network map groups materially connected offices into one
   assignment cluster. Residual cross-cluster transitions are measured and an
   exposure-mapping sensitivity is reported; they are not assumed away.

### What is clustered and how

- **Point estimate:** assignment-cluster-period aggregates, with grievance-level
  survival/RMST construction before aggregation where required.
- **Standard error:** clustered at the frozen assignment-cluster ID. No
  grievance-level i.i.d. SEs are reported.
- **Multiple outcomes:** the co-primary operational family and primary citizen
  endpoint use the multiplicity rule fixed before lock. Secondary outcomes use
  Benjamini–Hochberg FDR at 10% within the secondary family.

---

## 5. Power — the size of effect we could detect

### Power logic for a stepped wedge with clustering

Power is computed on **assignment-cluster-period outcomes** because the frozen
intake-office transfer-network cluster is the randomization unit. Compute power
separately for seven-day first-meaningful-action attainment, transfer-free first
assignment, and 90-day RMST/resolution. A resolved-only mean is not a primary
outcome because unresolved grievances are informatively omitted.

For a continuous illustration, the variance of the CS overall ATT is approximately

```
Var(ATT) ≈ ( σ²_within / (m · K_eff) + σ²_between / K_eff ) · DE
```

where `m` = grievances per assignment-cluster-period, `K_eff` = effective
number of independent cluster-period contrasts (function of S and total
assignment clusters K), `σ²_within` = within-cluster grievance variance,
`σ²_between` = between-cluster mean variance, and `DE` = design effect from
clustering:

```
DE = 1 + (m − 1)·ρ
```

`ρ` = intracluster correlation (ICC) of the outcome.

The **MDE at 80% power, α = 0.05 two-sided** is

```
MDE = (z_{1-α/2} + z_{0.80}) · SE(ATT) ≈ 2.80 · SE(ATT)
```

For binary outcomes the same formula applies on the risk-difference scale with
`p(1−p)` variance. Reopen, escalation and benefit cannot enter the calculation
until their event semantics and missingness are validated (§6).

### Inputs — taken from pre-period (record before lock)

| Input | How obtained | Worked value for illustration |
|---|---|---|
| K = frozen assignment clusters with adequate pre-period support | Count over the audited intake-office transfer-network map | **Not measured** (legacy office-count sensitivity below: 200, 320, 400) |
| m = grievances per assignment cluster per period | median cluster-period filing volume | **Not measured** (legacy illustration: 45) |
| S = wedge steps | departmental rollout blocks | **5** |
| Co-primary baseline risks | Validated first-meaningful-action and authority-transition events | **Not measured** |
| 90-day RMST / resolution | Kaplan–Meier or equivalent administrative-censoring construction from `created_on`, `resolved_on` and extract cutoff | **Not measured** |
| ICC ρ | between-assignment-cluster variance share for each endpoint | **Not measured** (legacy illustration: 0.04) |
| Administrative censoring | share without a validated event by the fixed horizon | **Must be measured and retained in the risk set** |

> **Do not treat the worked values as measured.** They illustrate sensitivity
> to assumptions only. The locked plan must re-compute each endpoint with the
> frozen pre-period extract and record the extract and cluster-map hashes.

### Legacy worked sensitivity (not the pilot MDE)

This table is retained only to show the algebra from the 0.1 draft. It assumes
K independent offices, S=5 steps, m=45/period, SD=55, two-sided α=0.05 and 80%
power. It predates transfer-network clustering and uses a resolved-case timing
scale rather than the locked endpoints. **Do not quote it as the pilot's MDE.**

| Legacy K offices | ρ=0.02 | ρ=0.04 | ρ=0.08 |
|---|---|---|---|
| 200 | 2.4 days | 3.0 days | 3.8 days |
| **320** | **1.9 days** | **2.3 days** | **3.0 days** |
| 400 | 1.7 days | 2.1 days | 2.7 days |

If within-office SD is 75 days (heavier tail) at K=320, ρ=0.04, this legacy
helper gives MDE ≈ **3.2 days**. If SD is 40 days, it gives ≈ **1.7 days**.

**Reading for the demo.** The method and required inputs can be shown; a
measured MDE cannot. Freeze the assignment-cluster map and pre-period extract,
validate event semantics, then compute an MDE for each locked endpoint.

### What this implies

- If the validated cluster count or event support is too low for a policy-
  relevant MDE, extend the pre-period, reduce the number of endpoints, or retain
  a never-treated holdout before launch. Do not infer power from grievance count
  when assignment occurs at a much coarser cluster level.
- Heterogeneity by language, channel or office volume is exploratory unless the
  frozen power calculation supports it.

### Reproducible legacy sensitivity

The helper below reproduces the continuous-outcome approximation in the legacy
table. It is not a valid locked power analysis for the binary co-primary
endpoints or censored RMST. Final power must simulate the frozen assignment
schedule and each endpoint's observed support, ICC and censoring (or use a
validated stepped-wedge power implementation with the same inputs).

```python
# Reproduce the legacy continuous-outcome sensitivity only.
# No citizen text is loaded.

from math import sqrt
from scipy.stats import norm

def legacy_continuous_mde(K, m, sd, rho, steps=5, alpha=0.05, power=0.80):
    z_alpha = norm.ppf(1 - alpha/2)
    z_beta  = norm.ppf(power)
    # Approximation retained from v0.1; not the locked trial calculation.
    de = 1 + (m - 1) * rho
    # Effective cluster-periods scales ~ K*steps / (steps+1) for wedge
    k_eff = K * steps / (steps + 1)
    var_att = (sd**2 / m) * de / k_eff
    se = sqrt(var_att)
    return (z_alpha + z_beta) * se
```

Record the lake extract SQL and content hash, cluster-map hash, endpoint support
and censoring, code/package version, assignment schedule and computed MDEs in
the experiment manifest before rollout.

---

## 6. Outcomes — construction and validation status

The corpus carries candidate fields, not a validated outcome table. Timestamped
action semantics, immutable intake assignment, authority transitions,
missingness and censoring must be audited before lock. Officer behavior and
direct citizen experience require new event instrumentation.

| Outcome | Column(s) | Construction | Type |
|---|---|---|---|
| **Co-primary: seven-day first meaningful action** | `created_on`, `action_history.action_taken_date`, validated action taxonomy | `1` when the first validated substantive action occurs within seven days; all eligible filings remain in the denominator. Also report p50/p90 with administrative censoring | Binary + time-to-event |
| **Co-primary: transfer-free first assignment** | Validated timestamped authority/department transitions | `1` when no validated transition follows the first assignment within the locked horizon. A scalar `transfer_status` or an arbitrary action-count threshold is not a transfer count | Binary |
| **Primary citizen: 90-day RMST / resolution** | `created_on`, `resolved_on`, frozen extract cutoff | Area under the unresolved survival curve through 90 days, plus resolved-by-30/90-day risks. Never compute a resolved-only mean | Time-to-event + binary |
| **Escalation** | Validated escalation event in timestamped history | `escalation_date` is treated in the ORM as an overdue date, so non-null is not accepted as an escalation event. Secondary only after semantics validation | Binary |
| **Rework: reopen/repeat filing** | Timestamped reopen event; privacy-safe citizen/problem linkage | `reopened_by` has no reopen timestamp and cannot establish “within 90 days.” Same-citizen same-problem refiling also needs salted identity/problem linkage | Binary / count |
| **Citizen outcome: benefit** | `benefitted` | Officer-recorded benefit with its missingness and coding distribution. It is not citizen-reported benefit or satisfaction | Binary + missingness |
| **Citizen outcome: satisfaction** | Approved portal/SMS/WhatsApp/call response linked through a privacy-safe token | At a locked fixed horizon, invite every eligible case regardless of administrative closure; report invitations, responses, item missingness and resolved/satisfied among respondents by arm. A post-closure-only survey is descriptive because closure may itself be affected | Ordinal/binary + response process |
| **Officer effort: substantive touches** | Validated action taxonomy over `action_history`; later UI events | Count substantive history events. Active handling seconds, edits/clicks and packets per staffed officer-hour require UI and staffing instrumentation | Count / time |
| **Process: disposal ladder** | `action_taken_remark` (closure mart) | bare vs action-claiming disposal — secondary, for mechanism | Binary |

All outcomes are reported **ITT by assigned arm**. Exposure and treatment-on-
treated analyses are secondary. Secondary outcomes are FDR-controlled as a
family.

**Denominator discipline.** The closure ladder rate is quoted with its
denominator: bare disposals over templated disposals (776,922) vs over all
resolved (1,209,144). The plan never quotes a rate without its base.

---

## 7. Assignment / exposure / shadow instrumentation — design (not build)

This section is a **design contract** for `janasunani/experiments/` (ROADMAP
§5.4). Implementation is deferred. The demo may claim a reviewed instrumentation
design, not that the service is instrumented.

### 7.1 Assignment service

**Requirement:** reproducible, stateless, stratified assignment of frozen
pre-treatment intake-office transfer-network clusters to wedge switch time.

```
arm = assign(cluster_id, experiment_id) → {control, treated} × switch_step
switch_step ∈ {1..S} ∪ {never}  # never = holdout if retained
```

**Mechanism — deterministic seeded hash:**

```
h = SHA-256( experiment_id || "|" || cluster_id || "|" || seed )
rank = h mod 2^32 / 2^32  # uniform [0,1)
stratum = (district, volume_tercile, baseline_transfer_rate_band)
within each stratum, sort by rank and cut into S equal blocks → switch step
```

- **Deterministic and reproducible:** same `(cluster_id, experiment_id, seed)`
  always yields same step; no database of assignments to drift.
- **Immutable map:** the membership of intake offices in each `cluster_id` is
  derived only from audited pre-period routing transitions, content-hashed and
  stored in the experiment manifest. Current/final office fields never change
  assignment after filing.
- **Stratified:** district × pre-period volume tercile × baseline transfer-rate
  band, so early vs late adopters are balanced on geography, load and network
  intensity. Stratification variables are pre-period only.
- **Stateless:** the service has no state beyond the seed and the stratum
  map; assignment can be recomputed offline for audit.
- **Seed governance:** seed is a random 256-bit value generated once, stored
  in the experiment manifest, and committed before any outcome is viewed.
  Changing the seed requires a plan amendment.

**API sketch:**

```python
# janasunani/experiments/assignment.py (planned)
class AssignmentService:
    def __init__(self, experiment_id: str, seed: str, strata: dict): ...
    def switch_step(self, cluster_id: str) -> int: ...  # 1..S or 0=control-throughout
    def is_treated(self, cluster_id: str, period: int) -> bool: ...
    def stratum(self, cluster_id: str) -> str: ...
```

**Properties to test (when built):** uniformity of rank, correct stratum sizes,
determinism on re-run, no dependence on outcome data.

### 7.2 Prediction exposure and officer decision events (append-only)

Assigned ≠ exposed. An intake cluster can be assigned to treated but the
officer may not see the suggestion (offline, dismissed, model unavailable).
Both fields are required for ITT vs TOT.

Exposure and decision happen at different times and must not be written into a
single mutable row. Serving writes append-only events to OLTP; governed lake
materialization produces the analysis tables.

**Table 1: `prediction_exposure_event`**

| Column | Type | Meaning |
|---|---|---|
| `experiment_id` | text | experiment slug |
| `ticket_no` / `grievance_id` | text | grievance key |
| `cluster_id` | text | Frozen pre-treatment assignment cluster |
| `assigned_step` | int | wedge step assigned to the cluster |
| `assigned_arm_at_event` | text | `control`/`treated` per assignment at event time |
| `visible` | bool | shown to officer (`false` for shadow/control) |
| `event_ts` | timestamp | prediction/exposure time |
| `stage` / `output_class` | text | category, route, actionability or skip/abstention class; no narrative text |
| `confidence` | float/null | calibrated confidence when defined |
| `output_hash` | text | Hash linking to any separately governed narrative artifact |
| `release_manifest_id` | text | Immutable pipeline/stage versions, thresholds and artifact digests |
| `fallback_reason` | text/null | unavailable/local fallback/other bounded reason |

**Table 2: `officer_decision_event`**

| Column | Type | Meaning |
|---|---|---|
| `experiment_id` / `ticket_no` | text | Join keys |
| `exposure_event_id` | text | The prediction event being acted on |
| `decision_ts` | timestamp | When the officer decided |
| `decision` | text | accepted / edited / rejected / ignored |
| `final_output_class` | text/null | Final category/route/actionability class; no narrative text |
| `edit_count` / `active_seconds` | int/float/null | Bounded UI burden fields when available |

**Table 3: `citizen_feedback_event`**

| Column | Type | Meaning |
|---|---|---|
| `experiment_id` / `feedback_token` | text | Experiment and privacy-safe one-time linkage; no phone number in this table |
| `invited_ts` / `responded_ts` | timestamp/null | Invitation and response times so response rates are measurable |
| `channel` | text | Approved portal, SMS, WhatsApp or call workflow |
| `resolved_reported` / `satisfied` | bool/ordinal/null | Locked survey items; missing items remain null rather than negative |
| `release_manifest_id` | text | Release in effect for the linked grievance |

**Invariants:**

- Event IDs are unique and rows are append-only; a later decision references an
  exposure event rather than updating it.
- `assigned_arm_at_event` is written at event time from the assignment
  service, not joined later (avoids retroactive redefinition).
- Control/shadow rows are written too (assigned=control, visible=false) so the
  control denominator is explicit.
- PII: none of the general event tables stores raw or redacted narrative text. A
  summary or edited narrative, if analysis genuinely requires it, stays in a
  separate `dpic-infra` store under stricter RBAC/audit and is referenced only
  by a content hash.
- Feedback linkage uses a one-time token. Contact details and message delivery
  stay in the approved communications system; reports include invitations,
  responses and item missingness, not respondent satisfaction alone. The
  invitation rule and horizon are locked before rollout and do not depend on
  model output or administrative closure.
- The assignment seed and cluster map live once in the immutable experiment
  manifest; event rows reference that manifest rather than duplicating secrets.

### 7.3 Shadow mode

The model **runs on control units too**, output suppressed before display and
retained as `prediction_exposure_event.visible=false`.

```
grievance → pipeline → typed prediction event
                    ├─ if assigned treated: show, log visible=true
                    └─ if assigned control: suppress, log visible=false
```

Why: counterfactual predictions for controls support mechanism checks (what
*would* have been suggested and agreement rates by arm). They do not improve
the number of randomized clusters or establish citizen benefit. The same event
contract can later support Phase 23's governed feedback loop.

**Fallback:** if the model is unavailable, output fields are null,
`visible=false`, and `fallback_reason` is recorded — the grievance proceeds on
the current process. No grievance is blocked on model availability. This is the same
fallback contract as the live pipeline.

---

## 8. Analysis harness — specification

**Tooling:** Python (`csdid` / `did` via `rpy2`, or `linearmodels` + manual
CS aggregation), R `did` as a check. Commit the analysis script and its lock
file; re-running on the frozen extract must be bitwise reproducible.

**Steps (primary):**

1. Build the assignment-cluster-period panel: one row per
   `(cluster_id, period)` with `assigned_step`, `is_treated`, locked endpoint
   constructions and support.
2. Estimate `ATT(g,t)` via CS `DR-IPW` with covariates `district`,
   `log_pre_volume` and baseline transfer-rate band.
3. Aggregate to overall ATT and event-study `ATT(e)`.
4. Assignment-clustered 95% CI (bootstrap, 999 reps).
5. Placebo: `ATT(e<0)` pre-trend test; Rambachan–Roth honest interval.
6. Robustness: re-estimate event study via Sun–Abraham.
7. Secondaries: substantive touches, validated transition/reopen/escalation
   events, repeat filing, officer-recorded benefit (with missingness), and
   citizen satisfaction with invitation/response rates and a locked nonresponse
   sensitivity; report risk differences with FDR. Omit any endpoint whose
   semantics are not locked.

**Pre-specified covariates only** (district, pre-period volume tercile,
baseline transfer-rate band). No
post-treatment covariates in the primary spec. Sensitivity adds
`category` mix as a covariate.

**Censoring:** the primary 90-day cohort is frozen to filings with at least 90
calendar days of potential follow-up at the extract cutoff. Every unresolved
filing in that mature cohort remains unresolved through day 90; it is never
dropped from a resolved-only mean. Later filings may contribute to locked
shorter-horizon or operational analyses, but are not coded unresolved at day
90 before that horizon can be observed. A cause-specific or frailty survival
model may be a sensitivity, not a replacement for the locked RMST estimand.

---

## 9. Locked pre-analysis plan — freeze procedure

1. Draft this plan (this commit) → review by the accountable engineer.
2. Re-compute endpoint-specific MDEs from the frozen pre-period extract and
   assignment schedule; record extract SQL, row count, content hash, cluster-map
   hash, endpoint support/baseline/variance, ICC, censoring, multiplicity rule,
   code/package version and MDEs in the experiment manifest.
3. Mark the plan **LOCKED** by tagging the commit
   `ab-plan-locked-v1` and recording its hash in
   `docs/AB_PLAN.md` §12 and in the manifest. No outcome data by arm is
   viewed before this tag.
4. Any change after lock requires a dated amendment with rationale, before
   outcomes are viewed. Amendments are themselves committed and tagged.

This is not a research pre-registration and no ethics review applies (DPIC
Executive Director determination 2026-07-27, ROADMAP §5.4). It is governance:
it stops the readout becoming a search for the outcome that looks best.

---

## 10. Harm monitoring and pause conditions

"No arm may leave a citizen worse off" is enforced, not asserted.

| Protection | Mechanism |
|---|---|
| Existing path retained | Control clusters retain the current process; treatment adds advice, never removes a route. Automated rejection stays off during the wedge |
| Harm indicators on the same cadence as primary | Seven-day meaningful action, transfer-free first assignment, 90-day RMST/resolution, PII leak, harmful actionable review and availability |
| Named owner | Executive Director (or delegate) reviews any arm-level degradation within 2 business days |
| Predetermined pause conditions (written before launch) | Crossing one halts rollout without a new judgement call |

**Draft pause triggers.** The effect-size margins and sequential-testing rule
are intentionally blank until the frozen pre-period extract is analyzed. They
must be populated and locked before rollout; this draft is not an active
monitoring plan.

- Seven-day first-meaningful-action attainment or transfer-free first
  assignment degrades beyond the locked harm margin.
- 90-day RMST worsens, or 30/90-day resolution falls, beyond the locked harm
  margin under the same administrative-censoring construction as primary.
- Any confirmed PII exposure attributable to the intervention, any automated
  rejection, or any actionable grievance blocked because of a low-signal flag.
- Model availability <95% in any period (shadow execution is not counted as
  officer exposure).
- Reopen/recontact or validated escalation exceeds its locked harm margin, but
  only if timestamp and event semantics pass the §6 audit.

Harm monitoring uses the same assignment clustering and locked endpoint
construction. Repeated looks use a pre-specified alpha-spending or confidence-
sequence rule; an unadjusted p<0.05 at every wedge is not permitted.

---

## 11. Retrospective analyses — what ships without the experiment

These are the "framework + evidence" the demo can show even though the wedge
has not run.

### 11.1 Counterfactual agreement study

Current evidence is narrower than the original demo promise:

- Routing has a chronological developmental held-out result: category+district
  top-1 historical-destination agreement 45.14% (95% Wilson CI 44.94–45.36),
  top-3 69.04%, n=208,267. The 2025 test was viewed during development, so a
  future frozen slice is required before promotion.
- Categorization now has a chronological, exact-text-group-disjoint 2024
  redacted typed-text development result: top-1 46.55%, top-3 90.89%,
  macro-F1 36.49%, n=3,160. It measures viewed historical-label agreement,
  not policy correctness; a newly frozen officer-confirmed set is still needed.
- The administrative actionability/low-signal evidence remains a weak-label
  feasibility audit, not learned spam quality: 106,683 eligible single-label
  tickets remained after 67 conflicts, and office-label variation failed the
  0.25 pooling gate (maximum total variation 0.522). Separately, the canonical
  57-case frontier-adjudicated development test caught 13/13 review cases and
  flagged 3/44 actionable cases. Its binary artifact can serve advisory review,
  but is not officer-confirmed, has no outside-purview support, cannot assign
  five-class reasons and is not release-eligible. The named screenshot remains
  bounded regression evidence only.
- Summary now has a single-frontier-judge enriched development baseline:
  55/84 critical facts retained, 0/26 unsupported or contradictory cases,
  8/26 drafts usable without edit and 4/26 residual-PII cases (n=30). It also
  failed all six judge-marked skip cases and all four coherent Odia cases. A
  newly frozen paired officer review is still required before any release claim.
- Transfer avoidance is not reportable until timestamped authority-transition
  semantics are validated. `transfer_status` and generic action counts are not
  substitutes.
- Language is absent from the structured routing benchmark and is not inferred
  from raw grievance text. A language result requires a governed redacted-text
  sample with observed labels.

**Caveat on the slide (not a footnote):** this is descriptive, not causal.
The models were trained on the officer labels they are now compared against,
so agreement partly measures imitation. Harder cases run longer and are routed
differently regardless of office; retrospective agreement cannot separate
case mix from office practice. Only the wedge can. Do not present agreement
as an effect of the tool.

### 11.2 Time-motion estimate

Compare technical pipeline latency (seconds, from instrumented stage timers or
validated artifact timestamps) with human process intervals derived from
`action_history.action_taken_date`. Report median and p90 per step, support and
clock definitions. This is descriptive: model runtime and elapsed
administrative time are different clocks, so their difference is not time saved
and cannot substitute for the pilot.

---

## 12. Governance and data handling

- **DPDP obligations** apply regardless of research status — the analysis reads
  real citizen records. Access is via the lake with redacted text only;
  `grievance_redacted` is the analytics-facing field, never raw `grievance`
  (§3.2). The dedup index, MinHash signatures and embeddings remain
  `dpic-infra`, never `authorized-external`.
- **Departmental sign-off** on eligible clusters, strata and operational
  constraints is required before the wedge starts. The switch order within
  those approved constraints is produced by the frozen seeded randomization,
  not selected administratively or after viewing outcomes.
- **Audit:** every assignment, prediction exposure, shadow prediction and later
  officer decision is append-only and references an immutable release and
  experiment manifest. The seed and cluster map live in that manifest, not in
  each event row. The audit carries no narrative text. The kill switch for
  `authorized-external` is out of scope here (treatment is
  `same-host`/`dpic-infra`), but the same manifest discipline applies.
- **Lock record:**

```
Plan version: 0.2 (DRAFT)
Locked commit: _________________________  (fill at lock — tag ab-plan-locked-v1)
Cluster map: source __________ hash __________ K=___
Pre-period extract: SQL __________ hash __________ m=___ censoring=___
MDE (80%, α and multiplicity rule locked): 7-day action ___ pp;
transfer-free assignment ___ pp; 90-day RMST ___ days
```

---

## 13. What the demo shows (commitment)

| Demo element | Artifact | Fallback |
|---|---|---|
| Estimator and why TWFE is not used | This plan §4 | — (no fallback — the plan is the artifact) |
| Power method and required inputs | This plan §5; values remain illustrative until the cluster map and pre-period extract are frozen | State that no measured MDE exists yet |
| Assignment / exposure / shadow design | This plan §7 | — (design, not build) |
| Developmental model evidence + time-motion contract | Held-out routing/category evidence, enriched summary scorecard and §11 | Descriptive only; correct authority, officer-validated summary quality, transfer avoidance and time saved remain unmeasured |

---

## 14. Amendment v0.3 — the SSEPD / Labour & ESI pilot (2026-09-01)

**What happened.** Two departments approved a pilot: Social Security &
Empowerment of Persons with Disabilities (SSEPD) and Labour & Employees' State
Insurance (Labour & ESI). Two facts arrived with the approval and invalidate the
design in §3–§5.

1. **OCAC, the vendor maintaining the legacy Janasunani system, has not approved
   integration.** Our system cannot sit inside the workflow. Officers will read
   our outputs and re-key what they choose into the legacy portal by hand. There
   is no path by which we write to, read from, or observe the production system.
2. **Each department has one grievance officer.** N = 2 officers, total.
   Departments have no backend access; at best they can download reports from
   the portal frontend, and whether any of those reports is record-level rather
   than an aggregate count is an open question (§14.5).

### 14.1 What this costs, stated before anything else

The stepped wedge required ~320 intake-office clusters, staggered adoption, and
outcomes read automatically from the corpus. None of the three is available.
Consequently, **this pilot cannot produce a department-level or state-level
causal estimate, and cannot produce a well-powered citizen-outcome estimate.**
Any readout that implies otherwise is wrong.

**What the pilot delivers instead, in order.** The primary deliverable is
feasibility and descriptive evidence: the pre-pilot corpus analysis, the
officer-side process map, and a shadow phase in which the panel is shown on
every case with no arms. That work starts before the console exists, does not
depend on officer adoption, and is the artifact for the integration ask. The
randomized comparison in §14.3 is a bounded component of the pilot, and it runs
only if the two gates in §14.9 pass.

§5.4 of ROADMAP asserts that "the corpus already carries the outcome variables"
and that "the primary outcomes need no new instrumentation". That is true of a
retrospective analysis and false of this pilot: without integration, nothing
about a pilot grievance is written to a store we can read.

| v0.2 element | Status for this pilot |
|---|---|
| §3 stepped wedge over intake-office transfer-network clusters | **Superseded.** No integration; 2 officers is not a cluster panel |
| §4 Callaway–Sant'Anna, Sun–Abraham, Borusyak–Jaravel–Spiess | **Superseded.** No staggered cluster adoption to identify from |
| §5 power on cluster-period aggregates | **Superseded** by §14.4 |
| §2 estimand | **Downgraded**, see §14.3 |
| §6 outcome construction and its validation debts | **Carries forward**, and the semantics audit is still the blocker |
| §7 assignment / exposure / decision / feedback event contract | **Carries forward essentially unchanged**, hosted in our console instead of the portal. This is the half of v0.2 that survives intact, and it is why the pilot is buildable at all |
| §9 lock procedure, §10 harm and pause conditions, §12 governance | **Carry forward** |
| Appendix A | **Reversed**, see §14.2 |

### 14.2 Reversing Appendix A, knowingly

Appendix A rejected grievance-level randomization because an officer sees both
arms, learns from the treated ones, and applies that learning to controls. That
reasoning remains correct. It is overridden because at N = 2 officers every
coarser unit has **zero** power, not merely less: there is no cluster panel to
estimate on. The choice is a contaminated grievance-level design or no
randomized evidence.

We take the contaminated design and bound it rather than assume it away:

- **Sign.** Learning transfer from treated to control cases makes control cases
  better, which attenuates the estimate toward zero. A positive result survives
  the bias; a null does not distinguish "no effect" from "fully contaminated".
  State that in the readout, not in a footnote.
- **Diagnostic.** Estimate the control-arm outcome level against calendar time
  and against cumulative treated-case exposure. A control arm that improves as
  treated exposure accumulates is the contamination signature. This is a
  descriptive diagnostic, not a correction.
- **What it changes about the estimand.** Grievance-level assignment identifies
  the effect of *this grievance* being shown support, holding the officer's
  accumulated learning fixed at its realized level. It does not identify the
  effect of an officer having the tool at all, which is the policy quantity and
  which only §3's design could deliver.

Interference through inbox composition, the second objection in Appendix A, was
argued to be weaker here than at intake, on the assumption that these officers
sit downstream of the department routing decision.

**That assumption is now in doubt, and the trigger this paragraph set has
fired.** The 18 August Labour & ESI walkthrough and the 19 August SSEPD
dashboard reading show the grievance officer's account carries the registration
screen (`/Admin/Eabhijog/Register/regNext/...`) with
department, escalation chain, category, subcategory and remarks, alongside the
forwarding screen and the per-case action history. These officers can register
and assign, not only receive.

What remains unknown is how often they do. If registration is a routine part of
the caseload, the Appendix A objection returns in force and the design needs
revisiting before launch. If it is an exception path used for walk-ins and
physical letters, the downstream argument mostly holds and the exposure needs
bounding rather than the design replacing. **Settling this is now a gate-1
input, not a field detail** (PILOT_SSEPD_LABOUR §B1).

### 14.3 Revised design and estimand

- **Population.** Grievances arriving at the SSEPD and Labour & ESI department
  grievance officers during the pilot window, excluding any case the officer
  flags as out of scope before assignment is revealed.
- **Randomization unit.** The grievance.
- **Blocking.** Permuted blocks within (officer × week), so arms stay balanced
  against secular drift, officer learning and caseload composition over a short
  pilot. Block membership is pre-treatment and fixed at arrival. **Whether to
  block on mode regime as well** (citizen-typed against document-borne) is a
  power question for §14.4: it is the covariate most likely to unbalance a small
  sample, and blocking on it costs nothing if the arrival rate supports the
  finer blocks.
- **Treatment.** The AI panel renders in the pilot console. **Both arms pass
  through the same console**: control cases get the identical case view with the
  panel suppressed, and the officer records their decision in both arms before
  re-keying into the portal. This is the mechanism that replaces integration.
  The model still runs on control cases with output withheld (§7.3 shadow mode,
  unchanged).
- **Primary estimand, conditional on the §14.9 gates.** ITT effect of the panel
  being *shown* on officer handling time for that grievance, log active seconds,
  within officer-week blocks. If either gate fails, this estimand is not
  reported and the pilot's outputs are the descriptive ones.
- **Secondary operational estimands.** Screens touched, revisits, decision
  changed after panel view (treated only, descriptive), and the officer's
  recorded downstream authority and typed resolution days.
- **Exploratory citizen estimands.** Time to first downstream action, 30-day and
  60-day resolution, citizen-reported resolution and satisfaction. Reported with
  their intervals and **no causal headline** unless an interval is informative,
  which §14.4 suggests it will usually not be.
- **Not estimated.** Any department-level, office-level or state-level effect.
  Any effect of tool availability as opposed to per-case display.

### 14.4 Estimator and power

The §4 staggered-adoption machinery does not apply and must not be carried over
by habit. The design is a blocked, individually randomized experiment, and the
estimator should be the simplest one that respects the blocking.

- **Point estimate.** OLS of the outcome on treatment with officer-week block
  fixed effects. Equivalently, the precision-weighted average of within-block
  differences in means.
- **Inference, primary.** Fisher randomization test against the sharp null,
  permuting within blocks under the realized assignment mechanism, at least
  10,000 draws. With two officers and a short window this is the honest choice:
  it is exact under the design and makes no asymptotic appeal.
- **Inference, reported alongside.** Neyman variance with HC2, so a
  conventional interval exists for readers who want one.
- **Covariate adjustment.** Pre-specified and pre-treatment only: **mode**,
  subcategory, district, page count of attached documents, whether the case is
  flagged a duplicate by the shadow prediction. Mode leads the list because it
  is the strongest structural predictor of the work a case represents: in the
  pilot departments the citizen-typed modes carry the grievance in the text
  field (median 217-283 characters) while the document-borne modes carry a
  20-to-30-character officer stub and a scan, and median days to resolution runs
  from 16 to 60 across modes (PILOT_SSEPD_LABOUR §A1). Omitting it would leave
  the largest source of outcome variance in the residual. Lin (2013) interaction form, to guarantee
  the adjustment cannot hurt precision asymptotically.
- **Multiplicity.** One primary outcome. The secondary operational family and
  the exploratory citizen family are each FDR-controlled at 10% within family,
  as in §4.
- **Power.** Computed at grievance level from measured pilot-department volumes
  and the observed dispersion of handling time from the time-and-motion baseline
  (PILOT_SSEPD_LABOUR §B4). **Due end September 2026, before anything is
  locked. This is gate 1 of §14.9: if the MDE for officer handling time is not
  credible at the measured volumes, the randomized comparison does not run at
  all.** The fallback is the shadow phase and the descriptive endpoints. Two
  further things it must settle:
  - **Whether the blocks can carry mode.** If (officer × week × mode regime)
    leaves blocks too thin, mode stays a covariate rather than a blocking
    factor. Decide this at gate 1, not by habit.
  - Whether either department has enough volume **passing through the officer**
    to randomize at all. The 19 August SSEPD dashboard read 51,460 tickets in
    total and 1,471 pending, of which 27 sat with the department node itself
    and 20 were overdue. Historical department volume is not the
    denominator; the officer's own throughput is, and it may be an order of
    magnitude smaller. Establish the arrival rate at the officer directly.
  - Whether Labour & ESI has enough volume to randomize at all. Indicative
    evidence says probably not on its own: in the committed crosswalk
    (`janasunani/routing/reference/routing_crosswalk.json`) SSEPD's largest
    entry carries support 7,020 while Labour & ESI's largest is 1,451, and
    Labour & ESI appears at **no** category-level or category-district-level key.
  - The MDE for the exploratory citizen endpoints, which is expected to be wide
    enough that those endpoints are descriptive. Say so at lock rather than
    discovering it at readout.

No illustrative power table is offered here. §5's legacy table is not applicable
and must not be quoted for this pilot.

### 14.5 Outcome capture without integration

§6's constructions still define the outcomes. What changes is the source, and
every source is now worse. In descending order of preference, and to be settled
by the field work:

1. **The read-only API brought back up, then scoped to these two departments**
   (`getGrievanceDetails` / `getGrievanceHistory`,
   `janasunani/ingestion/client.py`). **The endpoint is not running.** The client
   has never been exercised against a live API, and what we hold is a one-time
   historical extract with no refresh path. So this rung is two asks, not one:
   revive the service, then grant scoped read access. Still far short of the
   integration OCAC refused, and it should be made separately and explicitly.
   Until it lands, nothing in the pilot reads current state.
2. **The portal's own per-case action history**, which the 18 August walkthrough
   confirms is present in the department login: one row per step with action
   date, description, sender, and who currently holds the case. Reading it case
   by case is available today; whether it can be exported in bulk is the open
   question.
3. **A record-level portal report export.** The department login carries
   record-level reports, the Joint Hearing data report among them, so this rung
   is likelier than the CM Grievance Cell surface suggested. That surface was
   counts only, and it is no longer the best evidence about what a department
   login can do.
4. **Public ticket-status lookup** for the pilot cohort, whose ticket numbers we
   hold because they passed through our console. Requires department permission
   and a stated position on rate and terms of use before anyone writes it.
5. **Officer weekly log.** Self-reported, lowest confidence, always available.

**Re-key fidelity is a first-class measurement, not an implementation detail.**
The intervention reaches the citizen only through a human retyping it. Audit a
random 10% of treated cases weekly by having the officer show the portal record
against the console record. If fidelity falls below the threshold set at lock,
the finding is the attenuation itself, and no citizen-effect claim is made.

### 14.6 Feature scope, and one exclusion that is a safety decision

The tested bundle is decided in PILOT_SSEPD_LABOUR §4. One exclusion belongs in
this document because it is a harm judgement rather than a product choice.

**Actionability / low-signal triage is excluded from SSEPD entirely.** The
low-signal markers officers described in the 12 August field record are requests
for government jobs and requests for financial assistance carrying no detail.
`financial assistance` is effectively SSEPD's entire caseload. A triage flag
built on those markers would systematically route disability-benefit claims to
review. That is a foreseeable, patterned harm to the exact population the
department exists to serve, and no measured accuracy figure would license it.
It is not built for this pilot. Any later reconsideration for Labour & ESI needs
an officer-confirmed five-class gold set, which does not exist.

Separately, the two features most likely to be tested are the ones the corpus
work already supports and the portal conspicuously lacks: an ageing and deadline
view, and a repeat-filer panel. Neither depends on a model clearing a gate. The
document summarizer does, and at 4/26 residual-PII cases on its development set
it cannot be shown to an officer as it stands.

### 14.7 Governance changes

- **The research-exemption determination must be re-confirmed, not assumed.**
  The ED's 2026-07-27 determination (§9, §12) rests on this being deployment of
  a government service rather than research for publication, and it was made
  about an analysis of administrative records. **This pilot adds a direct
  citizen phone survey**, which is prospective data collection from identified
  individuals and is a materially different activity. Put it back to the
  decision owner before any call is made. DPDP obligations apply regardless.
- **Sign-off scope narrows.** §12 requires departmental sign-off on eligible
  clusters and switch order. There is no switch order now. What replaces it:
  written sign-off naming the two departments, permission to log what is
  suggested and what the officer does, and permission for the citizen follow-up.
- **Harm monitoring (§10) carries forward with the same discipline**, on the
  pilot's own endpoints. Control cases keep the current process, treatment adds
  advice only, no automated rejection, any confirmed PII exposure halts the
  pilot, and the ED reviews arm-level degradation within 2 business days. The
  effect-size margins stay blank until §14.4's power calculation lands.
- **The lock procedure (§9) is unchanged and now matters more.** With one
  primary outcome, two officers and a short window, the temptation to search
  across endpoints is at its highest. Tag `ab-plan-locked-v1` before any arm
  outcome is viewed.

### 14.8 What v0.2 is now for

Sections 3 through 5 are not dead weight. They are the design we would run if
the tool sat inside the workflow, they are costed and reviewed, and they are the
most concrete statement available of what integration would buy. Use them as the
technical annex to the integration ask, unchanged.

### 14.9 Sequencing: descriptive first, randomization gated

The randomized comparison runs only if both gates pass. Both are read against a
criterion fixed beforehand.

- **Gate 1, end September 2026, principal.** The §14.4 power calculation. Fails
  if the MDE for officer handling time is not credible at the measured pilot
  volumes. Gate 1 is also where Labour & ESI is included or dropped from
  randomization independently of SSEPD.
- **Gate 2, end January 2027, principal.** Re-key fidelity measured over the
  four-week shadow phase (§14.5), against a threshold written into the lock. The
  intervention reaches a citizen only through a human retyping it; below the
  threshold the treatment is diluted before it can act, and the attenuation is
  the finding.

**On a fail of either gate**, the shadow phase, the process map, the department
reports and the descriptive citizen endpoints run unchanged. What is lost is the
within-officer causal estimate. The readout states which gate failed and reports
no arm comparison.

§9 is unchanged. The lock still happens before any arm outcome is viewed, and
gate 2 is read from shadow-phase data in which no randomization has occurred.

---

## Appendix A. Why assignment is coarser than the grievance

> **Reversed for the SSEPD / Labour & ESI pilot, 2026-09-01.** The reasoning
> below stands; it is overridden because at two officers no coarser unit has
> any power at all. See §14.2 for the argument and the bias it accepts.

Randomizing grievances within an office lets an officer see both arms, learn
from treated cases and apply that learning to controls. Assigning individual
offices still leaves interference: routing a grievance away from office B to A
changes both inboxes, which may put connected offices in opposite arms. The
pre-period transfer-network map therefore groups materially connected intake
offices into one assignment cluster. Residual cross-cluster transitions are
measured through an exposure-mapping sensitivity rather than assumed absent.

## Appendix B. Estimator comparison at a glance

| Estimator | Handles staggered + heterogeneous effects | Needs never-treated | Aggregation | Primary? |
|---|---|---|---|---|
| TWFE (`Y ~ α_i + λ_t + τD`) | No — negative weights | No | Single τ | No |
| Callaway–Sant'Anna (CS) | Yes | No (not-yet-treated suffices) | `ATT(g,t)` → overall, event study | **Yes** |
| Sun–Abraham (SA) | Yes | No | Event study | Robustness |
| Borusyak–Jaravel–Spiess (BJS) | Yes | No | Overall | Sensitivity |
| de Chaisemartin–D'Haultfoeuille | Yes | No | Instantaneous | Not used (less familiar) |

## Appendix C. Minimal assignment-service test suite (when built)

- Determinism: same `(cluster_id, experiment, seed)` → same step on 1000 re-runs.
- Uniformity: χ² test of rank uniformity within each stratum.
- Stratification: early/late balance on district, volume and baseline transfer-rate band within the locked tolerance.
- No data leakage: assignment output is independent of outcome data by
  construction (seed + pre-period covariates only).

---

*This plan is versioned in-repo. Primary impact analysis uses structured event
fields and identifiers; narrative text is not part of the general experiment
event table. Any separately governed text analysis follows the lake scope
rules. Amendments after lock are committed and tagged; viewing outcomes by arm
before lock invalidates
the MDE. The Executive Director's determination (2026-07-27) that this is a
program evaluation of a government service — not human-subjects research —
rests on deployment of the service rather than research for publication; if
intent to publish appears, the question must be re-asked before data
collection.*
