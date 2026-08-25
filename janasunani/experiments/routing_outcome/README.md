# Routing outcome experiments — flow-aware, assignment-time

**Research-only.** No serving provider reads any of this, and no module here is
part of the live routing path.

The question: can a grievance be routed so it is disposed faster *without*
losing whether action gets taken? Speed alone is gameable — a case can be closed
at any moment by recording that it was disposed — so duration is minimised
*subject to a correctness constraint*, never *conditional on* correctness.

## Stages

| Stage | Script | Reads | Writes |
|---|---|---|---|
| Mart + splits | `dataset.py` | lake parquet | `{train,val,test}_{all,resolved,correct,actionable}.parquet`, `censoring.json` |
| E0/E1 census | `e0_flow_census.py` | lake parquet, mappings | stdout tables |
| Assignment provenance | `provenance.py` | complaints + action history | aggregate `assignment_provenance.json` |
| Fit `mu`, `pi` | `train.py` | splits | `models.pkl`, `fit_metrics.json` |
| OPE | `ope.py --split val` | splits, `models.pkl` | `ope_val.json` |

```bash
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.dataset
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.e0_flow_census
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.provenance
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.train
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.ope --split val
```

Output goes to `outputs/experiments/routing_outcome/` (override with
`ROUTING_OUTCOME_OUT`). It is gitignored: `models.pkl` embeds the DVC-tracked
mapping tables, and the intermediate parquet is row-level. All joins are local;
there is no `egress` route.

Tests: `tests/test_routing_outcome_experiments.py`. They synthesise their own
mapping tables and never touch `data/`.

## Design of record

`docs/experiments/routing-outcome-model.tex` is the specification. The body is
the routing problem; the appendices hold the general theory, proved from
prerequisites taken from the `../probability` reference (Appendix A lists
exactly which). Its final appendix is the exact as-run estimator specification:
features, model classes, weights, support restrictions, threshold grid and the
fit/calibration/evaluation timeline. Read it before changing anything here.

The chronology needs one qualification that “out-of-period evaluation” alone
misses:

| Period | Statistical role |
|---|---|
| 2021–23 | Fit duration models, raw correctness classifier, empirical propensity, feature levels and eligible action sets. |
| 2024 | Calibrate the correctness classifier isotonically; fit the 2024 censoring curve on the full arrival cohort; compare duration models and run developmental OPE. |
| 2025 | Fit only the split-specific censoring curve; otherwise use the frozen fitted machinery as the temporal developmental check. |

Thus 2024 is out of period for the raw models but not held out from correctness
calibration. The 2025 evaluation is after that calibration. `G` is not carried
forward from 2021–23: administrative censoring is estimated separately on each
split's full arrival cohort.

The short version of the target design:

- **Latent actionability and its proxy.** `S*` is intake-time actionability, the
  property needed by the causal estimand. The stored `S` column is only
  `S_tilde`, a post-resolution proxy inferred from closing remarks and handling
  history, so the current run is descriptive rather than identified for the
  `S*=1` target. `C` = action taken (moved by routing, so an outcome).
  A binary correct/incorrect split scores a correctly closed duplicate as a
  failure — ~144,700 cases, 12% of resolved — and does so disproportionately
  among fast cases.
- **Constrained, not conditional.** Minimise `E[min(T,365)]` subject to
  `E[C] ≥ E[C_hist]`, rather than minimising duration *among* correct cases.
  Conditioning on `C` conditions on a post-treatment variable and compares
  different populations across flows.
- **Stochastic historical baseline.** Candidate policies are deterministic
  maps from intake covariates to an action, but historical assignments are
  draws from the logging regime `g_0(a | x)`. Its realised means provide the
  baseline; history is not forced into the deterministic candidate class.
- **Joint policy action.** For a registered but unassigned complaint, the
  responsible office chooses department and a complete workflow template
  together, then selects the named authority at each node. The executable
  policy action deliberately coarsens that transaction to
  `department_id::complete_role_chain/v1`; the support set, propensity, outcome
  models, correctness model, fallback, and OPE match all use that same pair.
  Explicit transfer remains downstream of this intention-to-route assignment.
  Interpreting the pair causally requires a fixed reference mechanism for the
  omitted named-node and resolution-time choices. The portal's **Assign Another
  ATA** control is also unresolved: if it adds a parallel assignment, the
  single-pair action is incomplete.
- **De jure versus de facto routing.** `DepartmentId` plus `vchAllEscUser` is
  the putative de jure assignment. The dated action-history path after
  `assigned_on` is de facto handling. Creation-day complaint transfers before
  `assigned_on` belong to intake history, while later transfers are downstream
  deviations. The aggregate provenance stage can separate those timings but
  cannot verify the initial values: the complaints lake has one current row per
  ticket and action history stores no department or complete-chain snapshots.
  `transfer_status` is a transient current-state flag, not an ever-transferred
  covariate, and is excluded from `X`.
- **RMST with IPCW.** Restricted mean at a 365-day horizon, censoring
  reweighted rather than dropped.
- **Augmented IPCW**, Hájek-normalised and cluster-bootstrapped by
  district×year. It is doubly robust in the outcome/propensity pair only when
  the censoring model is correct, and is not the censored-data EIF without the
  censoring-martingale augmentation.
- **τ calibrated**, not chosen: the *smallest* floor meeting the correctness
  constraint (raising τ buys correctness and costs speed). If no candidate
  clears a floor, the total rule selects the highest-correctness admissible
  flow; it never substitutes the observed route or drops that row from the
  frontier.
- **Backlog `Q_r(t)` in X**, reconstructed from custody intervals in the action
  history (`action_taken_by` + `action_taken_date`). It is currently absent
  from the fitted design matrix. The captured assignment form does not show
  candidate-specific backlog or trailing performance; these are proposed
  assignment-time system-state adjustments, not verified form fields.

## Implementation against that design

Built: joint department-chain decoding, the shared train-fitted feature encoder that makes
`m̂_a(x)` evaluable off-policy, sparse one-hot ridge and cross-fitted
target-encoded GBM duration models, a target-encoded action
classifier, empirical-share propensities, the augmented IPCW score with Hájek
normalisation, ESS, a cluster bootstrap, RMST with IPCW, Duan smearing,
qualified chronological evaluation as described above, optional policy sample
splitting, and a total correctness-floor policy on a common support-restricted
population. The optional policy split was not enabled in the headline
reproduction commands.

Not built, in priority order:

1. **Intake-time actionability `S*`** — construct it from pre-treatment inputs
   and validate it independently of routing and closure. The current
   closing-remark `S_tilde` proxy cannot identify this population; free-text
   adjudication alone does not fix the timing problem.
2. **Assignment context and treatment versions** — recover the responsible
   assigning office and its office/time-specific workflow menu; verify category
   timing and **Assign Another ATA** semantics; specify the named-node and
   resolution-time mechanism under the department-template policy. The
   citizen-selected intake office is not a substitute for the assigning office.
   Source-system evidence must also verify that `DepartmentId` and
   `vchAllEscUser` preserve the initial assignment, or provide versioned
   assignment events from which to recover it.
3. **Congestion and trailing performance in X** — see the backlog section of the
   .tex for the custody-interval construction and the null-`resolved_on` trap.
4. Hierarchical propensity, sensitivity analysis, a multi-role queue replay,
   negative-control and placebo checks. The replay must update every role in the
   selected chain with capacity measured in cases per day; an entry-role load
   stress test is not the full interference correction.

## Provisional numbers

The 19 August refit evaluates 450,567 common-support 2024 rows (3,665 excluded).
At τ=0, the top-three ridge rule has a 24.50-day direct and 26.77-day augmented
descriptive contrast; the boosted versions are 23.90 and 12.40 days. The
unrestricted augmented contrast is unstable: 28.58 days with ridge but 0.50
with boosting, with ESS only 3–4% of `n`.

The correctness frontier is withdrawn pending regeneration. A post-review
audit found that its augmented score was normalized on all evaluation rows
before being restricted to rows with an observed correctness label, while the
direct value used the full population. The code now scores one labelled
population, but the corrected aggregate has not been recomputed (#284). There
is therefore no published `tau_star`.

The untouched 2025 period does not replicate the validation gain. The
top-three ridge AIPW contrast is −2.35 days (SE 3.50, ESS/`n` 0.073), while
boosting gives 0.15 days (SE 4.41, ESS/`n` 0.081). Large positive direct-method
contrasts do not survive augmentation or temporal holdout. These test results
are not used to retune τ or choose a model.

**Do not quote these as causal effects or routing recommendations.** `S_tilde`
is selected after resolution; assigning-office context, treatment versions,
and congestion are unresolved; propensity is only an empirical cell share;
unrestricted overlap is poor; and the aggregate provenance audit cannot recover
prior assignment-field values from the current snapshot.

The aggregate evidence used by the manuscript and governed benchmark bundle is
recorded in `docs/experiments/routing-outcome-evidence-2026-08-19.json`. The
dated 13 August fit, OPE, and robustness artifacts predate the joint-action
refit, temporal holdout, and reproducible robustness rerun. They are retained
only in `docs/experiments/superseded/` for audit history and must not be cited as
current evidence.

## History

Earlier runs (11 Aug) reported 9.5 days from a pipeline with several defects —
categorical codes refitted per split, the two arms of the contrast using
different estimators, a cell-mean standing in for a policy. Those summaries are
in `superseded/` with the defect list. Nothing there should be cited; the
current design doc supersedes both the numbers and the framing.
