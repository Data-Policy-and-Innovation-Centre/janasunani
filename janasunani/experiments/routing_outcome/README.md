# Routing outcome experiments — flow-aware, officer-observable

Branch `muse/routing-experiments`. **Experimental, not near `main`.** No serving
provider reads any of this.

The question: can a grievance be routed so it is disposed faster *without*
losing whether action gets taken? Speed alone is gameable — a case can be closed
at any moment by recording that it was disposed — so duration is minimised
*subject to a correctness constraint*, never *conditional on* correctness.

## Stages

| Stage | Script | Reads | Writes |
|---|---|---|---|
| Mart + splits | `dataset.py` | lake parquet | `{train,val,test}_{all,resolved,correct}.parquet`, `censoring.json` |
| E0/E1 census | `e0_flow_census.py` | lake parquet, mappings | stdout tables |
| Fit `mu`, `pi` | `train.py` | splits | `models.pkl`, `fit_metrics.json` |
| OPE | `ope.py --split val` | splits, `models.pkl` | `ope_val.json` |

```bash
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.dataset
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.e0_flow_census
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
exactly which). Read it before changing anything here. The short version of the
target design:

- **Three-state outcome.** `S` = actionable (a property of the grievance, so
  safe to condition on), `C` = action taken (moved by routing, so an outcome).
  A binary correct/incorrect split scores a correctly closed duplicate as a
  failure — ~144,700 cases, 12% of resolved — and does so disproportionately
  among fast cases.
- **Constrained, not conditional.** Minimise `E[min(T,365)]` subject to
  `E[C] ≥ E[C_hist]`, rather than minimising duration *among* correct cases.
  Conditioning on `C` conditions on a post-treatment variable and compares
  different populations across flows.
- **RMST with IPCW.** Restricted mean at a 365-day horizon, censoring
  reweighted rather than dropped.
- **Cross-fitted AIPW**, Hájek-normalised, cluster-bootstrapped by
  district×year.
- **τ calibrated**, not chosen: the *smallest* floor meeting the correctness
  constraint (raising τ buys correctness and costs speed).
- **Backlog `Q_r(t)` in X**, reconstructed from custody intervals in the action
  history (`action_taken_by` + `action_taken_date`). Currently absent from the
  fitted design matrix, which weakens the officer-screen identification
  argument — congestion is on the screen and is a plain confounder.

## Implementation against that design

Built: flow decoding, the shared train-fitted feature encoder that makes
`m̂_a(x)` evaluable off-policy, ridge and GBM duration models, an action
classifier, empirical-share propensities, the AIPW score with Hájek
normalisation, ESS, and a cluster bootstrap.

Not built, in priority order:

1. **Three-state outcome `S`/`C`** — adjudicate the closing-remark templates and
   build a text model for the free-text tail. Start with "as reported" (90,061
   cases, 7.4% of the corpus, ambiguous, currently scored as failure).
2. **RMST + IPCW** — censoring is currently dropped, confining claims to
   low-censoring cohorts.
3. **τ calibration** — runs to date use τ=0, i.e. no constraint at all.
4. **Duan smearing** — log-scale predictions understate the mean by ~25–30 days.
5. **Cross-fitting** and **sample splitting for the policy** (winner's curse).
6. **Congestion and trailing performance in X** — see the backlog section of the
   .tex for the custody-interval construction and the null-`resolved_on` trap.
7. Hierarchical propensity, sensitivity analysis, queue replay, negative-control
   and placebo checks.

## Provisional numbers

Running the built subset on the 2024 cohort gives Δ between roughly 16 and 26
days, positive under both duration models and both candidate-set restrictions,
SE near 7 (cluster bootstrap). **Do not quote these.** They rest on the binary
outcome, τ=0, completers only, and no smearing or cross-fitting. The sign is
suggestive; the magnitude is not yet an estimate of the estimand.

Fit note: ridge val RMSE 1.156 beats GBM 1.240 on log(1+T). The flexible model
loses out of sample, which is consistent with flow effects being close to
additive in logs — hence `--mu` is a flag, not a constant.

## History

Earlier runs (11 Aug) reported 9.5 days from a pipeline with several defects —
categorical codes refitted per split, the two arms of the contrast using
different estimators, a cell-mean standing in for a policy. Those summaries are
in `superseded/` with the defect list. Nothing there should be cited; the
current design doc supersedes both the numbers and the framing.
