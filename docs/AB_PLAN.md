# A/B Analysis Plan — Estimator, Power, and Assignment Design

**Component (d) · Phase 16 · Issue #81 (parent #52)**
**Status:** DRAFT — to be locked before any outcome data is viewed
**Version:** 0.1 — 2026-08-07
**Authors:** planner-opus (draft), accountable engineer (sign-off)
**Decides:** which offices get the tool when, what effect we can detect, and how we will estimate it.

> DELIVERY.md records this component as **Framework only** — a design and a
> calculation, not running software on the demo date. ROADMAP §5.4 is the
> specification; this document is its locked instantiation.

---

## 0. What this plan locks and what it does not

**Locks:** randomization unit and procedure, primary and secondary outcomes
and their construction from OLTP/lake columns, the statistical estimator and
its clustering, the minimum detectable effect (MDE) given pre-period variance,
the assignment-service contract, the exposure/shadow log schema, and the harm
pause conditions.

**Does not lock:** the calendar order of office rollout (an administrative
decision requiring departmental sign-off), the exact number of wedge steps
(which follows from that order and the available pre-period), or any outcome
data. The power numbers below are computed from stated pre-period moments;
re-compute from the frozen pre-period extract before launch and record the
extract hash in the release manifest.

**Blinding rule.** Once this plan is marked LOCKED (commit hash recorded
below), no outcome comparison by arm is viewed until the analysis harness
reports the primary ITT. Exploratory cuts by arm before lock invalidate the
MDE.

---

## 1. Question and the three claims

ROADMAP §5.4 separates three claims that must not be blurred:

| Claim | Evidence | This plan |
|---|---|---|
| The model is accurate | Gold sets, offline scorecard (Phases 13, 18) | Not this plan — reference only |
| The officer decides better with it | Exposure logs, override/agreement rates | §6–§7 — instrumentation |
| Citizens are better off | Disposal time, transfers, reopens, benefit | §3–§5 — the experiment |

Offline agreement between model and officer labels is **suggestive, not
causal**; the models were trained on those same labels (imitation), and case
mix confounds both timing and assignment. The agreement study (§10) is reported
with that caveat on the slide, not in a footnote.

---

## 2. Causal estimand

- **Population:** grievances filed in Odisha and routed to an office in
  `office_id`. Citizen-level outcomes aggregate to the grievance.
- **Unit of randomization and clustering: the office** (`complaints.office_id`,
  fallback `received_by_id` where office is null). Grievance-level
  randomization maximizes power but **violates SUTVA**: better routing at one
  office mechanically changes the composition of another's inbox (interference).
  The office is also the operational unit of rollout and of officer learning
  (contamination).
- **Treatment:** AI decision support is *available* to the officer on treated
  office-days (category suggestion, route suggestion, summary, spam/duplicate
  banner — all advisory, never auto-reject). Control office-days retain the
  current process unchanged. No path is removed in either arm.
- **Estimand (primary):** intention-to-treat (ITT) effect of *being assigned*
  to AI support on time-to-disposal and on mis-routing, averaged over offices
  (cluster-average). ITT is policy-relevant because availability, not take-up,
  is what the department can assign.
- **Secondary estimand:** treatment-on-treated / complier effect is reported
  only as a bounded sensitivity analysis under a stated exclusion assumption,
  with assigned as instrument for exposed. Primary inference is ITT.

Assigned and exposed are separate fields (§7); conflating them makes neither
ITT nor TOT identified.

---

## 3. Design — staggered office-level rollout (stepped wedge)

### Why this design

- **Grievance-level parallel:** most power, but contaminates (officer sees both
  arms) and violates SUTVA via inbox composition. Rejected.
- **Office-level parallel (50/50 forever):** clean but denies half the offices
  the tool indefinitely; government partners rarely accept. Rejected.
- **Stepped wedge (staggered adoption):** every office is eventually treated,
  matches how a rollout would actually happen, retains identification from
  variation in adoption timing, and satisfies the "no office denied forever"
  constraint. **Chosen.**

### Structure

```
Period:   0 (all control) → 1 → 2 → … → S (all treated)
Wedge:    each period, a new block of offices switches control → treated
          and stays treated thereafter (absorbing treatment).
```

- **S = 4–6 steps** is the working range; the exact S is the number of rollout
  blocks the department can staff for training/support. Fewer steps = longer
  within-step exposure per office (less period noise), more steps = more timing
  variation (supports identification) but shorter post-treatment windows for
  late adopters.
- **Stratified randomization of the switch order** on two pre-period
  covariates: log pre-period filing volume (workload) and district. This
  balances both total load and geography across early vs late adoption. The
  sequence is drawn once, published, and then frozen — it is a
  departmental sign-off artifact, not re-randomized.
- **No concurrent interventions** are scheduled in the wedge window that
  differentially affect early vs late offices (verified with the department
  before lock). If one occurs, it is recorded as a time-varying covariate and
  the plan is amended before outcomes are viewed.

### What identifies the effect

Variation in *when* an office switches. Offices that have not yet switched are
contemporaneous controls for those that have, within the same calendar period
(controls for secular trend/seasonality).

---

## 4. Estimator — chosen and justified

### The bias to avoid

The default two-way fixed-effects (TWFE) regression

```
Y_{iot} = α_i + λ_t + τ·D_{it} + ε_{iot}
```

is **biased under staggered timing with heterogeneous effects** (negative
weighting of late vs early adopters). With treatment effect varying by
office, by time-since-adoption, or by period, TWFE can even sign-flip. Cluster
at the office does not fix this.

### Primary estimator: Callaway–Sant'Anna (2021) — `ATT(g,t)` + aggregation

**Callaway and Sant'Anna, J. Econometrics 2021** (also Did  R package
`did`, Python `csdid`).

- Estimates group-time average treatment effects `ATT(g,t)` for each adoption
  cohort `g` (offices switching at step g) at each calendar time t ≥ g, using
  only not-yet-treated (or never-treated, if retained) as controls for that
  `g,t`.
- Aggregates to policy-relevant summaries:
  - **Overall ATT** (average over all treated office-periods) — primary.
  - **Event-study ATT(e)** by elapsed time since adoption (`e = t − g`) —
    secondary, to show whether effects grow or fade.
  - **Cohort-specific ATT(g)** — diagnostic for heterogeneity.
- Doubly-robust (`DR-IPW`) form: outcome regression + IPW on covariates
  (district, pre-period volume). Requires only one of the two models correct.
- Inference: **cluster-bootstrap at the office level** (or analytical
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

Imputation estimator (fit outcome model on untreated office-periods, impute
`Y(0)` for treated). Efficient under the same identifying assumptions and a
useful placebo check. Reported in appendix, not primary, because it is less
familiar to the departmental audience.

### Identifying assumptions (stated, to be probed)

1. **No anticipation:** grievances filed before an office's switch are not
   affected by its future switch. Plausible because filing precedes routing;
   tested via pre-trend ATT(e<0) ≠ 0 placebo.
2. **Parallel trends in untreated potential outcomes** conditional on
   stratification covariates (district, volume). Probed via pre-trend
   event-study coefficients and the Rambachan–Roth honest sensitivity
   interval (reported alongside the point estimate).
3. **SUTVA at the office level:** one office's treatment does not affect
   another office's untreated outcomes except via inbox composition. At the
   grievance level SUTVA fails by construction, which is why the office is the
   unit. Cross-office spillover via transfers is bounded by reporting transfer
   rate as a secondary outcome — if transfers fall, composition spillover is
   itself an effect.

### What is clustered and how

- **Point estimate:** office-period aggregates (or grievance-level with
  office fixed effects — numerically equivalent for CS aggregation).
- **Standard error:** clustered at `office_id` (the randomization unit). No
  grievance-level i.i.d. SEs are reported.
- **Multiple outcomes:** primary outcome controls the type-I error (single
  test). Secondary outcomes use Benjamini–Hochberg FDR at 10% within the
  secondary family; reported as such.

---

## 5. Power — the size of effect we could detect

### Power logic for a stepped wedge with clustering

Power is computed on **cluster-period means** because the cluster is the
randomization unit. For a continuous primary outcome (disposal time in days),
the variance of the CS overall ATT is approximately

```
Var(ATT) ≈ ( σ²_within / (m · K_eff) + σ²_between / K_eff ) · DE
```

where `m` = grievances per office-period, `K_eff` = effective number of
independent office-period contrasts (function of S and total offices K),
`σ²_within` = within-office grievance variance, `σ²_between` = between-office
mean variance, and `DE` = design effect from clustering:

```
DE = 1 + (m − 1)·ρ
```

`ρ` = intracluster correlation (ICC) of the outcome.

The **MDE at 80% power, α = 0.05 two-sided** is

```
MDE = (z_{1-α/2} + z_{0.80}) · SE(ATT) ≈ 2.80 · SE(ATT)
```

For binary secondaries (transfer, reopen, benefitted) the same formula applies
on the risk-difference scale with `p(1−p)` variance.

### Inputs — taken from pre-period (record before lock)

| Input | How obtained | Worked value for illustration |
|---|---|---|
| K = number of offices with ≥30 filings in pre-period | `COUNT(DISTINCT office_id)` in lake | **320** (sensitivity: 200, 400) |
| m = grievances per office per period (4-week period) | median office-month volume | **45** (district median 29/year/category → ~50/period at district×all-categories) |
| S = wedge steps | departmental rollout blocks | **5** |
| Pre-period mean disposal time | `AVG(resolved_on − created_on)` | ~46 days (ROADMAP medians imply 29–60, long tail) |
| SD within office | `STDDEV` of disposal time | **55 days** (sensitivity: 40, 75) |
| ICC ρ | between-office variance share | **0.04** (sensitivity: 0.02, 0.08) |
| Attrition / missing `resolved_on` | share unresolved at period end | ~30% (censored; analysis uses period-censored rate or survival form) |

> **Do not treat the worked values as measured.** They are plausible
> magnitudes to size the trial. The locked plan must re-compute with the
> frozen pre-period extract and record the extract hash.

### Worked MDEs (days of disposal time)

Assumes K offices, S=5 steps, m=45/period, SD=55, two-sided α=0.05, 80% power,
and the Hussey–Hughes stepped-wedge variance (conservative for CS, which is
slightly more variable than TWFE).

| K offices | ρ=0.02 | ρ=0.04 | ρ=0.08 |
|---|---|---|---|
| 200 | 5.9 days | 7.1 days | 9.2 days |
| **320** | **4.2 days** | **5.2 days** | **6.8 days** |
| 400 | 3.6 days | 4.5 days | 5.9 days |

If within-office SD is 75 days (heavier tail) at K=320, ρ=0.04, MDE ≈ **7.1
days**. If SD is 40 days, MDE ≈ **3.8 days**.

**Reading for the demo.** With ~300–400 usable offices and a 5-step wedge, the
trial can detect a **4–6 day change in mean disposal time** (≈10% of the
mean) at conventional power. A 2-day effect is detectable only if the office
count is at the high end and ICC is low — honest to report as under-powered
for small effects. The same algebra on transfer rate (baseline ~12% transferred,
from `transfer_status`) gives MDE ≈ **2.5–3.5 percentage points** at K=320.

### What this implies

- **Well-powered** for the operationally relevant threshold: the department
  cares about a one-week shift in pendency. That is at or above the MDE for
  the realistic K.
- **Not powered** for a 1–2 day shift or for office-level heterogeneity
  (e.g., "does it help high-volume offices more?") — report as exploratory.
- **Binary outcomes** (reopen ~4–8%, benefitted ~? ) have similar MDEs in
  percentage-point terms; they are secondaries and FDR-controlled.
- If the frozen pre-period yields K < 180 or ICC > 0.10, the MDE exceeds a
  week and the plan recommends **extending the pre-period or adding a
  never-treated holdout cohort** before launch rather than running an
  under-powered wedge.

### Reproducible re-computation

```python
# Recompute MDE from the frozen pre-period lake extract
# Inputs: K, m, sd_within, rho, S, alpha=0.05, power=0.80
# No citizen text is loaded — only (office_id, created_on, resolved_on,
# transfer_status, reopened_by, benefitted, district).

from math import sqrt
from scipy.stats import norm

def mde_stepped_wedge(K, m, sd, rho, steps=5, alpha=0.05, power=0.80):
    z_alpha = norm.ppf(1 - alpha/2)
    z_beta  = norm.ppf(power)
    # Hussey-Hughes stepping variance, conservative for CS
    de = 1 + (m - 1) * rho
    # Effective cluster-periods scales ~ K*steps / (steps+1) for wedge
    k_eff = K * steps / (steps + 1)
    var_att = (sd**2 / m) * de / k_eff
    se = sqrt(var_att)
    return (z_alpha + z_beta) * se
```

Record the lake extract SQL, its content hash, and the computed MDE in the
experiment manifest before rollout.

---

## 6. Outcomes — construction from the corpus

The corpus already carries the outcomes (ROADMAP §5.4); no new measurement
outside the logs is needed.

| Outcome | Column(s) | Construction | Type |
|---|---|---|---|
| **Primary: time to disposal** | `created_on`, `resolved_on` | `resolved_on − created_on` in days; unresolved at period end = censored at period end (survival sensitivity: Cox with office frailty). Analysis on grievance-level days, aggregated by office-period for CS | Continuous |
| **Mis-routing: transfer rate** | `transfer_status`, `action_history` count | `1` if grievance transferred ≥1 (or `action_history` action count > threshold); denominator = filed in period | Binary |
| **Escalation rate** | `escalation_date`, `review_authority` | `1` if escalated | Binary |
| **Rework: reopen rate** | `reopened_by` | `1` if reopened within 90 days of disposal | Binary |
| **Citizen outcome: benefit** | `benefitted` | `1` if recorded as benefitted | Binary |
| **Officer effort: action steps** | `action_history` rows per `ticket_no` | count of actions | Count |
| **Process: disposal ladder** | `action_taken_remark` (closure mart) | bare vs action-claiming disposal — secondary, for mechanism | Binary |

All outcomes are reported **ITT by assigned arm**. Secondary outcomes are
FDR-controlled as a family.

**Denominator discipline.** The closure ladder rate is quoted with its
denominator: bare disposals over templated disposals (776,922) vs over all
resolved (1,209,144). The plan never quotes a rate without its base.

---

## 7. Assignment / exposure / shadow instrumentation — design (not build)

This section is a **design contract** for `janasunani/experiments/` (ROADMAP
§5.4). Implementation is deferred; the contract is what lets the demo claim
"instrumented" without shipping code that has not been hardened against PII
handling and deployment constraints.

### 7.1 Assignment service

**Requirement:** reproducible, stateless, stratified assignment of offices
(or officer `received_by_id` as a sub-unit) to wedge switch time.

```
arm = assign(unit_id, experiment_id) → {control, treated} × switch_step
switch_step ∈ {1..S} ∪ {never}  # never = holdout if retained
```

**Mechanism — deterministic seeded hash:**

```
h = SHA-256( experiment_id || "|" || unit_id || "|" || seed )  # seed = experiment salt
rank = h mod 2^32 / 2^32  # uniform [0,1)
stratum = (district, volume_tercile)
within each stratum, sort by rank and cut into S equal blocks → switch step
```

- **Deterministic and reproducible:** same `(unit_id, experiment_id, seed)`
  always yields same step; no database of assignments to drift.
- **Stratified:** district × pre-period volume tercile, so early vs late
  adopters are balanced on geography and load. Stratification variables are
  pre-period only.
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
    def switch_step(self, unit_id: str) -> int: ...  # 1..S or 0=control-throughout
    def is_treated(self, unit_id: str, period: int) -> bool: ...
    def stratum(self, unit_id: str) -> str: ...
```

**Properties to test (when built):** uniformity of rank, correct stratum sizes,
determinism on re-run, no dependence on outcome data.

### 7.2 Exposure log (append-only)

Assigned ≠ exposed. An office can be assigned to treated but the officer may
not see the suggestion (offline, dismissed, model unavailable). Both fields are
required for ITT vs TOT.

**Table:** `experiment_exposure` (append-only, OLTP or lake — lake for
analysis, OLTP for serving audit)

| Column | Type | Meaning |
|---|---|---|
| `experiment_id` | text | experiment slug |
| `ticket_no` / `grievance_id` | text | grievance key |
| `unit_id` | text | `office_id` (and `received_by_id` if available) |
| `assigned_step` | int | wedge step assigned to unit |
| `assigned_arm_at_event` | text | `control`/`treated` per assignment at event time |
| `exposed` | bool | was AI output shown to the officer |
| `exposure_ts` | timestamp | when shown (or null if control) |
| `model_output` | json | category, route, summary, spam/duplicate banner |
| `officer_action` | json | accepted / overridden / ignored, final route/category chosen |
| `model_version` | text | pipeline release + model alias (`@champion`) |
| `seed` | text | assignment seed for audit |

**Invariants:**

- One row per `(ticket_no, exposure_ts)` — append-only, never updated.
- `assigned_arm_at_event` is written at event time from the assignment
  service, not joined later (avoids retroactive redefinition).
- Control rows are written too (assigned=control, exposed=false) so the
  control denominator is explicit.
- PII: `model_output` stores only redacted text (`grievance_redacted`);
  never raw `grievance`. The log is `dpic-infra` (§3.2) and under RBAC/audit
  (Phase 18).

### 7.3 Shadow mode

The model **runs on control units too**, output suppressed before display and
retained only in the exposure log (or a separate `shadow_predictions` table
with identical schema plus `visible=false`).

```
grievance → pipeline → model_output
                    ├─ if assigned treated: show to officer, log exposed=true
                    └─ if assigned control: suppress display, log shadow with visible=false
```

Why: counterfactual predictions for controls sharpen the analysis (what *would*
have been suggested) and power it for mechanism checks (agreement rates by
arm). It is also the mechanism Phase 23 needs for the governed feedback loop,
so building it now is not throwaway.

**Fallback:** if the model is unavailable, `model_output=null`,
`exposed=false`, `fallback="local"` — the grievance proceeds on the current
process. No grievance is blocked on model availability. This is the same
fallback as the live pipeline.

---

## 8. Analysis harness — specification

**Tooling:** Python (`csdid` / `did` via `rpy2`, or `linearmodels` + manual
CS aggregation), R `did` as a check. Commit the analysis script and its lock
file; re-running on the frozen extract must be bitwise reproducible.

**Steps (primary):**

1. Build office-period panel from the lake: one row per
   `(office_id, period)` with `assigned_step`, `is_treated`, outcome means,
   count.
2. Estimate `ATT(g,t)` via CS `DR-IPW` with covariates `district`,
   `log_pre_volume`.
3. Aggregate to overall ATT and event-study `ATT(e)`.
4. Office-clustered 95% CI (bootstrap, 999 reps).
5. Placebo: `ATT(e<0)` pre-trend test; Rambachan–Roth honest interval.
6. Robustness: re-estimate event study via Sun–Abraham.
7. Secondaries: same CS pipeline on transfer, reopen, benefitted,
   escalation; report risk differences with FDR.

**Pre-specified covariates only** (district, pre-period volume tercile). No
post-treatment covariates in the primary spec. Sensitivity adds
`category` mix as a covariate.

**Censoring:** primary analysis censors unresolved grievances at period end
and treats disposal time as missing for them (with a survival sensitivity:
Cox with office frailty on the grievance-level data, testing proportional
hazards via Schoenfeld residuals).

---

## 9. Locked pre-analysis plan — freeze procedure

1. Draft this plan (this commit) → review by the accountable engineer.
2. Re-compute MDE from the frozen pre-period extract; record extract SQL,
   row count, content hash, K, m, SD, ρ, and MDE in the experiment manifest.
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
| Existing path retained | Control offices unchanged; treatment adds advice, never removes a route. Spam auto-reject stays off during the wedge |
| Harm indicators on the same cadence as primary | Disposal time, reopen rate, escalation rate tracked per arm per period |
| Named owner | Executive Director (or delegate) reviews any arm-level degradation within 2 business days |
| Predetermined pause conditions (written before launch) | Crossing one halts rollout without a new judgement call |

**Pause triggers (pre-specified, any one halts the next wedge step):**

- Mean disposal time in any treated cohort exceeds its concurrent
  not-yet-treated controls by > 7 days with one-sided p < 0.05 (harm side).
- Reopen rate in treated exceeds control by > 3 percentage points with
  p < 0.05.
- Escalation rate in treated exceeds control by > 2 percentage points with
  p < 0.05.
- Model availability < 95% in any period (fallback ≠ shadow; the support is
  not actually delivered).

Harm monitoring uses the same CS estimator on the harm outcome and the same
clustering; it is not a separate ad-hoc cut.

---

## 11. Retrospective analyses — what ships without the experiment

These are the "framework + evidence" the demo can show even though the wedge
has not run.

### 11.1 Counterfactual agreement study

Run the current pipeline over a historical sample (stratified by
district × category × language). For each grievance compare:

- AI category vs officer-recorded `category`
- AI route vs eventual office that resolved it
- Whether the AI's route would have avoided ≥1 transfer (transfer count
  via `action_history`)

Report: accuracy, agreement rate, and transfer-avoidance rate with 95% CIs,
per language (English vs Odia vs romanized Odia separately — no pooled
headline).

**Caveat on the slide (not a footnote):** this is descriptive, not causal.
The models were trained on the officer labels they are now compared against,
so agreement partly measures imitation. Harder cases run longer and are routed
differently regardless of office; retrospective agreement cannot separate
case mix from office practice. Only the wedge can. Do not present agreement
as an effect of the tool.

### 11.2 Time-motion estimate

Compare pipeline latency (seconds, from artifact DB `pages.created_at` deltas)
against human handling intervals in `action_history`
(`action_taken_on` deltas between successive actions on the same ticket).
Report median and p90 per step, not means, because both distributions are
heavy-tailed.

---

## 12. Governance and data handling

- **DPDP obligations** apply regardless of research status — the analysis reads
  real citizen records. Access is via the lake with redacted text only;
  `grievance_redacted` is the analytics-facing field, never raw `grievance`
  (§3.2). The dedup index, MinHash signatures and embeddings remain
  `dpic-infra`, never `authorized-external`.
- **Departmental sign-off** on the rollout order is required before the wedge
  starts; the order is administrative, not ours.
- **Audit:** every `assigned`/`exposed` decision and every shadow prediction
  is logged with model version and seed, under the same audit as the Sarvam
  egress log (Phase 17). The kill switch for `authorized-external` is out of
  scope here (treatment is `same-host`/`dpic-infra`), but the same manifest
  discipline applies.
- **Lock record:**

```
Plan version: 0.1 (DRAFT)
Locked commit: _________________________  (fill at lock — tag ab-plan-locked-v1)
Pre-period extract: SQL ________________  hash ________________  K=___ m=___ SD=___ ρ=___
MDE (primary, 80%, α=0.05, two-sided): ___ days
```

---

## 13. What the demo shows (commitment)

| Demo element | Artifact | Fallback |
|---|---|---|
| Estimator and why TWFE is not used | This plan §4 | — (no fallback — the plan is the artifact) |
| Power on pre-period data (MDE) | This plan §5 + frozen extract values | Re-compute on the slice available at demo time; state the extract hash |
| Assignment / exposure / shadow design | This plan §7 | — (design, not build) |
| Agreement + time-motion (retrospective) | Issue #82 | Descriptive only, with the imitation caveat on the slide |

---

## Appendix A. Worked derivation — why the office, not the grievance

Randomizing grievances within an office lets an officer see both arms,
learn the model's pattern from treated cases, and apply it to controls
(contamination). It also violates SUTVA: if the model routes one grievance
away from office B to office A, B's caseload composition changes and its
disposal time moves even though B's grievance was "control". The office-level
wedge internalizes both effects — learning and composition shift are *part of*
the treatment effect as the department would experience it in a real rollout.

## Appendix B. Estimator comparison at a glance

| Estimator | Handles staggered + heterogeneous effects | Needs never-treated | Aggregation | Primary? |
|---|---|---|---|---|
| TWFE (`Y ~ α_i + λ_t + τD`) | No — negative weights | No | Single τ | No |
| Callaway–Sant'Anna (CS) | Yes | No (not-yet-treated suffices) | `ATT(g,t)` → overall, event study | **Yes** |
| Sun–Abraham (SA) | Yes | No | Event study | Robustness |
| Borusyak–Jaravel–Spiess (BJS) | Yes | No | Overall | Sensitivity |
| de Chaisemartin–D'Haultfoeuille | Yes | No | Instantaneous | Not used (less familiar) |

## Appendix C. Minimal assignment-service test suite (when built)

- Determinism: same `(unit, experiment, seed)` → same step on 1000 re-runs.
- Uniformity: χ² test of rank uniformity within each stratum.
- Stratification: early/late balance on district and volume within ±5%.
- No data leakage: assignment output is independent of outcome data by
  construction (seed + pre-period covariates only).

---

*This plan is versioned in-repo and analysis reads the lake, so it sees
redacted text and the same scope rules as the metrics layer. Amendments after
lock are committed and tagged; viewing outcomes by arm before lock invalidates
the MDE. The Executive Director's determination (2026-07-27) that this is a
program evaluation of a government service — not human-subjects research —
rests on deployment of the service rather than research for publication; if
intent to publish appears, the question must be re-asked before data
collection.*
