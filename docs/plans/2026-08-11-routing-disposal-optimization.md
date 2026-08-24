# Plan: Outcome-based routing — minimize disposal time conditional on correct disposal (flow-aware, officer-observable)

> **Superseded on 2026-08-13** by
> [2026-08-13-routing-outcome-completion-and-plain-briefs.md](2026-08-13-routing-outcome-completion-and-plain-briefs.md),
> and by `docs/experiments/routing-outcome-model.tex` as the design of record.
>
> The title states the error. Minimizing disposal time *conditional on* correct
> disposal conditions on a post-treatment variable: the set of correctly disposed
> cases is a different population under each flow, so the comparison rewards a
> flow for abandoning its hard cases. The replacement minimizes duration
> *subject to* a correctness constraint. See §4 of the .tex.
>
> The six-model suite below was also never built: M3 (Cox), M5 (hierarchical
> Bayes) and M6 (queue-augmented) do not exist, and `train.py` says so in its
> module docstring. Kept as the record of how the framing changed. Do not cite
> its numbers.

**Branch:** `muse/experiments` off `feat/pipeline-quality-trunk` via worktree
**Type:** implementation + eval/research (offline, no new data collection)
**Date:** 2026-08-11 — v3 (flow-aware + officer-observable identification)

## Goal

Build an experimental routing scorer that **ranks eligible flows by predicted disposal time _conditional on correct disposal_**, where the flow is the **actual administrative handling chain** (department + entry role + escalation chain via `vchAllEscUser`), not the citizen-selected coarse `office` (8 values). Produce a **transparent, simulatable counterfactual**: "holding a grievance's observable characteristics fixed, how much faster would it have closed under the scorer's flow than under its historical flow?" Because **everything a grievance officer observes at routing time is also observed by the model**, the identification argument is unusually clean — the remaining unobservables are limited to officer idiosyncrasy and downstream shocks, not case characteristics. The plan trials multiple structural models for `T` to make that claim falsifiable under explicit assumptions, not a single black box.

We do **predictive policy learning with a structural duration model**, not causal identification via instruments. OVB is not claimed away — it is **minimized by conditioning on the officer's information set** and quantified via sensitivity across models.

## Success Criteria

- A `muse/experiments` worktree + branch exists, isolated from `feat/pipeline-quality-trunk`, with no changes to OLTP, lake, or egress.
- A dated outcome mart (`janasunani/analytics/sql/routing_outcome.sql`) defines `correct_disposal` and `disposal_days` and decodes every `vch*` flow field via `janasunani-mappings` (+ documents the officer-observable conditioning set).
- A **model suite** for `T|correct` exists in `janasunani/experiments/routing_outcome/` — at least three defensible forms below — plus a correctness model `P(correct|X,flow)`, all with chronological, group-disjoint evaluation (2021-23/2024/2025).
- An offline doubly-robust OPE reports `E[T|correct]` under the flow scorer vs historical flow vs citizen-office baseline, **separately for each structural model**, with overlap diagnostics and a Rosenbaum/δ-sensitivity for residual `U`, over a frozen test slice. Citizen-office override is an explicit simulation arm.
- A transparent simulation harness can draw `Y(flow) ~ μ_m(X,flow)+residual` for any `m` in the suite and replay `Δ_flow^{(m)}`, so reviewers can toggle assumptions and see the effect move.
- A provider `JANASUNANI_ROUTER=outcome` (dept) and `outcome-flow` (chain) implements `RoutingProvider` without mutating `DEFAULT_ROUTER`; artifacts are aggregate-only.
- Documentation states the **officer-observable equivalence**, every assumption, conditioning set (with decoded `vch*`), model forms, and sensitivity needed to reject the claim.

## Context And Current Facts

### What routing does today

`DEFAULT_ROUTER` is `MappingRouter` → `RuleRouter` (`janasunani/routing/rules.py`, `janasunani/routing/mappings.py`). Live, `JANASUNANI_ROUTER=crosswalk` puts the empirical crosswalk first (`janasunani/routing/crosswalk.py:1-40`): `argmax_{dept,office} P(dept|category,district)`. `janasunani/evaluation/routing.py` adds `IncidenceRouter` as `JANASUNANI_ROUTER=incidence`. All three learn *where cases were sent*, not where they resolved well — `crosswalk.py:22-28` and `ROADMAP.md:9` defer the outcome scorer to #106. None models the actual handling flow.

### History available

`janasunani/db/models.py:79-213` — 1,371,288 `complaints`, 6,556,171 `action_history` rows. `complaints.resolved_on/created_on/assigned_on`, geography, `category/subcategory/dept/office`, `pending_with/tagged_to/review_authority`, `benefitted`, etc. `action_history` gives trajectory. Lake: `janasunani/olap/materialize.py:21-41` — `LAKE_TABLES = (complaints, action_history, grievance_redactions, ...)`. Lake is not PII-free (`ROADMAP.md:3.2`): use `grievance_redacted`. All artifacts aggregate-only (`crosswalk.py:42-44`) with `MIN_SUPPORT≥3`.

### The two "office" concepts

| Field | What it is | Cardinality | Citizen vs system |
|---|---|---|---|
| `office` / `office_id` (`officeNAme`/`intOfficeId`, `schemas.py:86`) | Coarse intake office citizen selects (8 labels: `Collector`, `Departments`, `Office of Chief Minister`, `Chief Secretary`, `DG & IG Police`, `Governor`, `Superintendent of Police`, `None`) | 8 + NULL (693k Collector, 280k Departments, 217k CM Office) | Citizen choice; can be overridden in simulation |
| `pending_with`/`pending_with_id`, `all_esc_user` (`vchAllEscUser`), `tagged_to`/`tagged_by`, `review_authority`, `received_by` | Actual handling flow | `pending_with_id`: 2,670 users; `all_esc_user`: 2,747 chain strings len 1-8 (mode 2); 3115 users in `t_user_role_details` | System/officer determined; the intervention |

Evidence `office` ≠ flow: `office=Collector` but `pending_with LIKE 'Collector,%'` for same district ≈ 12%; `all_esc_user` chains terminate in CM users even when `office=Collector`; `n_esc` median `T` 4d/43d/68d/73d for len1-4; `self_assign=Yes` (233k) ≈ all len1.

### What the `vch*` fields encode (decoded via `data/raw/janasunani-mappings/`)

| Complaint col | Source | Meaning | Decode | Fact |
|---|---|---|---|---|
| `all_esc_user` | `vchAllEscUser` | Comma userId chain `intUserId,...` length `n_esc` | `t_user_role_details.intUserId→intRoleId → m_role.vchRoleName` (+ `m_office_designation_mapping` for office) | Every token is a `t_user_role_details.intUserId` (2,747 tokens ⊆ 3,115 users); `54→Secretary`, `1964→CMO`, `276→MD/Director`; max roleId 87 so >87 must be userIds |
| `pending_with_id` | `pendingWith` | Current holder userId | Same bridge | 2,670 distinct; all except `0` are userIds; `0`=empty |
| `pending_with` | `pendingwithName` | Label for holder | Derive via user→role, don't trust string | 34k `Secretary, Panchayati Raj & DW` (54), etc. |
| `tagged_to`/`tagged_by` | `taggedTo`/`taggedByName` | Scheme tag + tagging officer userId | `tagged_by_id` is userId; `tagged_to` is tag category | Treat tag as conditioning, not action |
| `review_authority` | `reviewAuthorityName` | Apex review role | `m_role`-like |  |
| `transfer_status`/`self_assign` | `transferStatus`/`isSelfAssign` | Flags | Binary | `Yes` 14k / 233k; mechanism check |

Master tables for feasible (not just observed) flows: `m_role` (83), `m_admin_hierarchy_value` (depts), `m_admin_offices` (7), `m_office_designation_mapping` (129), `m_admin_category/subcategory` (62), `t_admin_escalation` (158, `vchDesignationSequence` role ladders), `t_forward_escalation` (17,818 per-user forward edges), `t_user_role_details` (3,115 user→role), `m_demographic_hierarchy_values` (district/block). `janasunani/routing/mappings.py` degrades to `None` when missing — no invented edges.

### The officer-observable identification principle (new in v3)

**At routing time, the dealing officer literally sees every variable the model conditions on**, plus no more (except private judgment and downstream shocks). Concretely:

| What officer sees at assignment | Lake column(s) | Model has it? |
|---|---|---|
| Grievance text (typed or scanned), attachments | `grievance_redacted` (via `grievance_redactions`), `document_downloaded` | Yes — same redacted text, same doc flag; never raw `grievance` |
| Category / subcategory assigned at intake, district / block / state, mode, disability, petitioner geography, `govt_ticket`, `urgent` | `category`, `subcategory`, `district`, `district_id`, `block`, `block_id`, `state`, `mode`, `mode_id`, `disability`, `govt_ticket`, `urgent` | Yes |
| Citizen-selected intake office | `office`, `office_id` | Yes — but modeled as intervenable, not as immutable |
| Prior tags / scheme assignment, receiving officer, review authority setting at that moment | `tagged_to`, `tagged_by`, `tagged_date`, `received_by`, `review_authority` (+ `_id` variants) | Yes — `tagged_*`/`review_*` included in `X` with timestamps ≤ `created_on`/`assigned_on` |
| Current workload / backlog visible on dashboard (pending queue) | Not a single column — derived as `pending_stock_at_entry_role` / `pending_stock_at_dept` from `assigned_on ≤ t < resolved_on` counts (window over lake) over the same OLTP the officer's screen reads | Yes — derived identically from lake, point-in-time |
| Office/department historical performance (officer knows "this collector is slow on land cases") | Trailing-90d `median T|correct`, `P(correct)`, reopen/transfer rates per role and per dept — computed strictly pre-`t` from lake | Yes — strictly pre-`t` aggregates |
| Demographic context (district/block) | `district`, `block` joinable to `m_demographic_hierarchy_values` | Yes |
| Duplicate/campaign context | `dedup_groups.group_size` (where materialized) | Yes — same artifact the intelligence layer uses |
| Time context | `created_on`/`assigned_on` → year-quarter, month, day-of-week | Yes |

**What officer sees that model *does not* (the residual `U`)**: momentary unrecorded discretion ("I know this applicant"), corridor knowledge, phone calls, and downstream shocks after routing (staff leave, protest, data entry lag). None of these are grievance characteristics; they are routing-time private judgment or post-routing shocks.

**Why this matters**: the classic OVB story for routing ("harder cases go to better offices") is captured by `X` because difficulty is *written on the form* — category, block, text, mode, doc presence. We do not need to proxy it. The remaining `U` is therefore **routing-private + post-routing**, not case-difficulty. We make this explicit, then probe it: if a skeptic still worries that officers see something about the *case* that we don't, the residual must be a case trait that is (a) visible on paper, (b) not in the table above, and (c) systematically drives both office choice and speed|correct. Name it or bound it — that is the sensitivity analysis.

This is the transparent assumption the simulation harness exposes: `Y(flow) ⟂ Flow | X` where `X` is the officer's screen. We claim it is **defensible** because `X` is that screen, and we show how `Δ` moves as we relax it.

## Constraints And Non-goals

- **Must not:** mutate OLTP, `LAKE_TABLES`, egress, or `DEFAULT_ROUTER`; read `data/` only via `lake.py` or governed SQL; export row-level data; call `authorized-external`. `vchAllEscUser` decoding is local CSV joins.
- **OVB minimized, not eliminated.** Conditioning on the officer's screen is the best feasible `X`; sensitivity across models and a Γ/δ perturbation quantifies the remaining `U`. We never claim unbiased causality.
- **No live A/B in this branch.** Office-override simulation is offline. Future stepped-wedge trial is doc-only.
- **Not in this plan:** Odia upgrade, free-text tail NLP beyond `grievance_redacted`, village-level granularity, or new ingestion.

## Key Decisions

| Decision | Recommendation | Alternatives rejected & why |
|---|---|---|
| **Correct disposal** | Composite: `correct=1` if closing rung ∈ `{with_action, benefit}` **or** `benefitted ILIKE '%yes%'` or (`rung∈{with_action,benefit}` and `action_steps≥3` and no `reopened_escalated` within 90d). `bare`/`discarded_with_reason` are 0 unless `benefitted=Yes` overrides. Censor unresolved. | Single-rung or pure `benefitted` each miss ~1/3 of signal (see `closure.sql` vs `benefitted` mismatch). Composite is auditable and reconciles. |
| **Estimand** | Two-level `argmin_{dept or flow} E[T|X, flow, correct=1]` where `flow=(dept, entry_role, role_sequence_template)` with template summarized as `(n_esc, first_role, last_role)` decoded from `vchAllEscUser`. Secondary `E[T|X,flow]·I[P(correct|·)≥τ]`. Dept ranking always reported; flow headline where overlap permits. | Unconditional `E[T]` is gameable (fast `bare`). Office-only (`office_id` 0-7) conflates citizen choice with capacity — flow is the lever `t_forward_escalation` actually implements. |
| **Action space `A(X)`** | `A_dept`: depts serving `category` in `district` in trailing year. `A_flow`: per dept, top-3 role-sequence templates observed for `(dept, district, category)` with `support≥10`, forward-feasible per `m_office_designation_mapping` and `t_forward_escalation`. Template = roleIds (e.g. `6,8`=Secretary→CMO), not userIds, to keep positivity. Citizen `office` is not in `A` — it gets an explicit `do(office)` arm. | Raw userId chains (2,747 strings) kill positivity. Role templates (~100) preserve the substantive chain difference while keeping ESS viable. |
| **Conditioning set `X` — officer-observable** | **Intake form (officer sees):** category, subcategory, district/block/state, mode/mode_id, disability, govt_ticket, urgent, document flag, `grievance_redacted` length (+ optional embedding), `created_on` FE. **Flow assignment state (officer sees on screen):** `tagged_to` tag, `tagged_by` role, `received_by` role, `review_authority`, `all_esc_user` is *not* in `X` (it's the outcome flow) but its pre-`t` aggregates are (role's backlog). **System state (officer sees on dashboard):** `pending_stock_at_entry_role`, `pending_stock_at_dept`, district office count, trailing-90d role/dept `median T|correct`, `P(correct)`, reopen/transfer rates — all strictly pre-`t`. All include decoded `vch*` roles via `t_user_role_details→m_role`. | Dropping `tagged_*`/`review_*` would create artificial `U`; dropping `pending_stock` would miss queueing; dropping text would miss difficulty. Every field maps to `models.py:99-167` or a mapping CSV. `vchAllEscUser` chain itself is never a feature — it's the exposure — while its *history* (role backlog) is a feature. |
| **Microfoundation (structural interpretation)** | `T = wait(queue_at_entry_role) + Σ_i handle(role_i, match) + (n_esc-1)·handoff + transfers·delay` (see §Structural model). `wait ∝ pending_stock / role_capacity`; `handle ∝ -specialization(category|role) - district_familiarity + text_complexity`; `handoff` and `transfers` are escalation costs. `self_assign≈(n_esc=1)` is the shortcut. This is the generative model the simulation replays. | Pure ML is simulatable but not interpretable for reviewers; pure queueing cannot score unseen cells. Hybrid keeps a generative form that explains *why* `n_esc` predicts `T` even after conditioning on difficulty — the inefficiency residual. |
| **Structural model suite (the transparency device)** | Trial **all six forms** below on the same `X`; report `Δ_flow^{(m)}` for each `m` and the envelope. No single model is the claim — the spread is. | Picking one model hides assumption sensitivity. The suite makes it explicit which assumption moves `Δ`. |
| **Identification** | `Y(flow) ⟂ Flow | X` where `X` is the officer's screen (§above), plus positivity `P(flow|X)>ε`, consistency, SUTVA at role-day (interference only via `pending_stock_at_entry_role`, conditioned on). Report hierarchical ESS for dept and flow; where flow fails, fall back to dept bound. Office choice is `P(office|X)` with an intervened `do(office)` simulation arm. | IV remains out of scope. Flow ignorability is stronger than dept — therefore flow `Δ` always shown with dept `Δ` as envelope, plus Γ-sensitivity for residual `U`. |
| **Learning & OPE** | Predict-then-optimize with **doubly robust** OPE at both granularities: `μ_m(X,flow)=E[T|X,flow,correct]` per model `m` + correctness `π(X,flow)`; hierarchical propensity `e(flow|X)=e(dept|X)·e(flow|dept,X)` via penalized logit; DR `Γ_i^{(m)}(δ)=μ_{δ(x_i)}^{(m)}+I[Flow_i=δ]/e·(T_i-μ^{(m)}_{Flow_i})`. Dept backs off flow where `e<ε`. | Pure IPW is high-variance with 2.7k flows; pure outcome regression is `m`-dependent. DR is model-doubly-robust and the simulation can swap `m` without changing the OPE scaffolding. |
| **Model class** | GBM (LGBM/XGB) for the non-parametric `μ` member + calibrated `π`; hierarchical propensity via penalized logits; EB shrinkage for role-templates with `support<30`. `vch*` are decoded roles, never raw comma strings. Each structural `μ_m` below has its own parametric head. | Deep nets overfit and split envs; OLS underfits role heterogeneity. Boosting handles nullable categoricals; structural heads keep assumptions inspectable. |
| **Temporal validation** | Chronological 2021-23 train / 2024 val / 2025 test; `group_id=ticket_no` disjointness + DVC digest (`lake_snapshot_id` + `mapping_snapshot_id`). Flow artifacts respect temporal order; no post-`t` chain in `X`. | Random split leaks future role performance. Chronological is the honest OPE. |

### Structural model suite — all simulatable, all transparent

All six estimate `μ(X,flow)=E[T|X,flow,correct=1]` (or the distribution of `T` for simulation) on the same `X`. The **generative form** is what matters — each can draw counterfactual `Y(flow)=g^{(m)}(X,flow)+ε^{(m)}` with `ε` resampled within `X`-strata:

| # | Name | Form | What it assumes transparently | What it stresses |
|---|---|---|---|---|
| M1 | **AFT log-normal with office/role FEs** | `log T = Xβ + α_{dept} + γ_{entry_role} + δ·n_esc + θ·pending_stock + W·(specialization) + ε, ε∼N(0,σ²)` | Additive separable effects; log-normal tail; proportional time (not hazard) | Baseline; fast, interpretable α/γ are "office speed" |
| M2 | **AFT with interaction + chain** | `log T = Xβ + α_{dept,entry_role} + f(n_esc, entry_role) + X·entry_role` interaction | Chain cost depends on entry role; match quality interacts with category | Tests whether `n_esc` cost is uniform |
| M3 | **Cox PH / additive hazard** | `h(t|X,flow)=h0(t)·exp(Xβ+α_{dept}+γ_{role}+δ·n_esc)` | Proportional hazard, unspecified baseline, right-censoring principled | Stresses survival shape and censoring handling |
| M4 | **Two-part hurdle** | `P(correct|X,flow)` (logit) then `E[T|X,flow,correct]` (M1/M2 on correct subset) | Speed and correctness are separable decisions | Directly enforces `correct`-conditioning; reveals gaming via `bare` |
| M5 | **Hierarchical Bayes (partial pooling)** | `log T ∼ N(Xβ+α_{dept}+γ_{role}+δ·n_esc, σ²)`, `α∼N(0,τα), γ∼N(0,τγ)` with `τ` learned | Role/dept effects shrunk where `support<30`; honest uncertainty where data thin | Regularizes sparse flows; EB in v1 was ad-hoc — here it's generative |
| M6 | **Queue-augmented structural** | `T = wait + handle`, `wait = Q_{entry_role}(t) / cap_{role} + ε_q`, `handle = handle(X,flow)` as in M1 | `Q` is `pending_stock_at_entry_role`; `cap` is trailing role throughput → congestion is explicit. Simulation replays `Q(t)` | Makes SUTVA violation explicit: rerouting changes `Q` for others |

Non-parametric **M0 (GBM)** is run alongside as the unrestricted benchmark — if `Δ^{(M0)}` diverges from `Δ^{(M1-M6)}`, the parametric restriction matters and must be flagged. The suite is the point: **you can try as many as you want** by adding `M7…` (e.g. causal forest for `μ`) without changing the OPE/simulation harness — each just plugs a new `μ_m`.

## Recommended Approach

### 0. Prerequisite: decode the `vch*` universe once

Add `janasunani/routing/flow.py` as the **single decoder**:

```python
# Tables loaded once (reuse mappings.py pattern):
#   user_role = {intUserId -> intRoleId} from t_user_role_details.csv
#   role_name = {intRoleId -> vchRoleName} from m_role.csv
#   office_by_role = {intRoleId -> {intOfficeId}} from m_office_designation_mapping.csv
#   forward_edges = {intUserId -> {intForwardUserId}} from t_forward_escalation.csv
#   escalation_template = {dept -> vchDesignationSequence} from t_admin_escalation.csv

def decode_esc_chain(vchAllEscUser: str | None) -> Flow:
    # split on ',', map userId -> roleId -> roleName+office
    # return Flow(chain_len, role_ids, role_names, entry_role, last_role,
    #             entry_office, is_self_assign=n_esc==1, forward_feasible=...)
```

Unit-tested against lake: `decode_esc_chain("54")== (1, [6], ["Secretary"])`; `decode_esc_chain("22,54,81")→ ["Chief Secretary","Secretary","CMO"]`. Invalid token → `None` (excluded from flow OPE, retained as `flow_missing` in conditioning). Every module imports this one function.

### 1. Isolate the experiment

```bash
git fetch origin
git worktree add .worktrees/muse-experiments -b muse/experiments feat/pipeline-quality-trunk
cd .worktrees/muse-experiments
# optional: uv sync --extra serving --extra pipeline-core
```

`.worktrees/` is the repo convention. Branch `muse/experiments` never merges to `main` until reviewed; CI unchanged.

### 2. Build the outcome mart (SQL is the deliverable)

New `janasunani/analytics/sql/routing_outcome.sql` (portable DuckDB/Postgres):

- `routing_outcome_base`: one row per `complaint`, `disposal_days`, `rung` (same `CASE regexp_replace` as `closure.sql`), `benefitted_norm`, `reopened_90d` (`action_type=reopened_escalated` ≤90d post-resolution), `transfer_count`, `action_steps`, `pending_stock_at_entry_role` / `pending_stock_at_dept` (window `assigned_on≤t<resolved_on`), **citizen office** `office/office_id`, **flow raw**: `n_esc`, `all_esc_user`, `pending_with_id/pending_with`, `review_authority_id`, `tagged_by_id`, `tagged_to`, `received_by_id`, `transfer_status`, `self_assign`. Raw `all_esc_user` kept in SQL; role decoding is Python enrichment to keep SQL portable.
- `routing_outcome_decoded`: `entry_role_id`, `last_role_id`, `flow_template` (role sequence string), `flow_feasible` (via `t_forward_escalation`), derived via Python UDF documented in mart README.
- `routing_outcome_label`: `correct` (composite), `disposal_days_capped = LEAST(days,365)`, `censored`.
- Views: `routing_outcome_correct_only`, `office_capacity_90d`, `flow_capacity_90d` (per role), `routing_outcome_overlap` (dept+flow ESS), `routing_outcome_office_contrast` (citizen vs pending divergence), `officer_observable_inventory` (documents the table in §Context for auditors).

`janasunani/analytics/marts.py` registers it; `findings/routing_outcome.py` reconciles against `RECONCILIATION_SQL` and asserts `MIN_LADDER_COVERAGE_PCT` plus `flow_decode_rate≥95%`.

### 3. Counterfactual prediction recipe (flow-aware, multi-model)

For each `A_dept(X)` / `A_flow(X)`:

`A_dept`: depts serving `category` in `district` in trailing year. `A_flow`: per dept, top-3 role-sequence templates for `(dept,district,category)` with `support≥10`, feasible per `m_office_designation_mapping` and `t_forward_escalation`. Template = roleIds (e.g. `6,8`), not userIds.

For each model `m ∈ {M0…M6}`:

1. **Fit** `μ^{(m)}_flow(X,flow)=E[T|X,flow,correct=1]` and `π(X,flow)=P(correct|X,flow)` on train.
2. **Fit** hierarchical `e(flow|X)=e(dept|X)·e(flow|dept,X)` + `e_office(office|X)` on train.
3. **Score** `δ^{(m)}_flow(X)=argmin_{flow∈A_flow: π≥τ} μ^{(m)}_flow(X,flow)`, backing off to `δ_dept` where `e<ε`. `τ` tuned on val s.t. `E[π_{δ}]≥E[π_{hist}]`.
4. **OPE on test** DR per model:
   ```
   Γ_i^{(m)}(δ) = μ^{(m)}_{δ(x_i)} + I[Flow_i=δ]/e_{δ} · (T_i - μ^{(m)}_{Flow_i})
   V^{(m)}(δ) = mean Γ_i^{(m)}    (restricted to correct via weighting)
   Δ^{(m)}_dept = V^{(m)}(hist_dept) - V^{(m)}(δ_dept)
   Δ^{(m)}_flow = V^{(m)}(hist_flow) - V^{(m)}(δ_flow)
   Δ^{(m)}_office_override = V^{(m)}(hist_flow) - V^{(m)}(δ_flow with do(office=dept_default))
   ```
   Clip `e<0.01`, ESS-gated. Also outcome-only and IPW-only bounds.

5. **Simulate counterfactual `T`**: for each test grievance, draw `U^{(m)}∼ Residual^{(m)}|X-stratum` (stratified resampling of `T-μ^{(m)}` within `category×district` cells), set `Y^{(m)}(flow)=μ^{(m)}(X,flow)+U^{(m)}` (or `exp(...)` for M1/2/5), and replay arrival queue via `pending_stock_at_entry_role` evolution to get `Δ^{(m)}_{sim}` that accounts for congestion (especially M6 where `wait` explicitly moves).

6. **Report the envelope**: `Δ_flow` is not a number but the interval `[min_m Δ^{(m)}_flow , max_m Δ^{(m)}_flow]` plus the GBM benchmark `Δ^{(M0)}`. If models disagree in sign, the claim is "model-sensitive". The simulation harness lets reviewers swap `m`, toggle `τ`, remove `n_esc` from `X`, or inject a synthetic `U` correlated with `Flow` (δ-sensitivity) and see `Δ` move continuously.

Shrinkage & uncertainty: EB posterior for templates with `support<30`; cluster bootstrap by `district-year` for CIs; **Rosenbaum Γ** and **Cinelli-Hazlett robustness value** for residual `U` (report `Γ` at which `Δ` crosses 0, and `RV` for "how strong must omitted `U` be to explain `Δ` given officer-observable `X` is already saturated").

### 4. Feasible experiments (each self-contained offline run)

All read the lake via `lake.py` + `mappings.py` + `flow.py`; results aggregate CSV + Markdown fragment under `outputs/findings/routing_outcome/` (no row-level output).

| # | Experiment | What it varies | Counterfactual it supports | Cost |
|---|---|---|---|---|
| **E0** | Flow census | Decode every `all_esc_user` via `t_user_role_details→m_role`; tabulate `n_esc`, role sequences, `office→pending_with` divergence | Documents actual flow; validates decode rate; needed before any OPE | min |
| **E1** | Descriptive audit (officer-observable) | Stratify `T|correct` and `P(correct)` by `rung, district, category, office(citizen), pending_with_role, n_esc, entry_role, tagged_to` — all officer-visible | Baselines; proves office≠flow; officer-observable inventory audit | min |
| **E2** | Outcome model bake-off | **6+1 models** `M0-M6` as above; ablation: dept-only vs +flow vs +office vs full officer-observable `X` | Which structural assumption moves `Δ`; selects `μ` suite (keep all, not winner-take-all) | ~1h |
| **E3** | Correctness model bake-off | `π`: GBM vs logit; `τ` sweep | Validates not gaming via `bare`; selects `τ` | ~30m |
| **E4** | Propensity + overlap (hierarchical) | `e(dept|X)`, `e(flow|dept,X)`, `e(office|X)` with full officer-observable `X`; ESS, positivity by `category×district` and `n_esc` | Where flow `Δ` credible vs fall back to dept | CPU |
| **E5** | Policy DR evaluation (multi-model, two levels) | `δ_dept`/`δ_flow` vs `hist_flow`/`hist_dept`/`crosswalk`/`incidence`/`random` — **separately for each `m`** | `Δ^{(m)}_dept`, `Δ^{(m)}_flow`, `P(correct)`, `n_esc` saved, transfers saved — the envelope | CPU |
| **E6** | Office-override simulation | `do(office=dept_default_office)` arm per model `m` via `m_office_designation_mapping` | Isolates citizen-choice effect per model | CPU |
| **E7** | Heterogeneity / CATE | DR `Δ^{(m)}_flow(x)` by `category, district, n_esc, self_assign, pending_stock, group_size` | Where flow routing matters most | CPU |
| **E8** | Chain mechanism | Regress `n_esc`, `transfer_count`, `T` on `I[δ≠hist]` per `m`; mediation via `forwarded_delegated` | Checks `Δ` is shorter chain, not relabeling | CPU |
| **E9** | Sensitivity & spec curve (residual `U`) | Vary `correct` (3), `T` cap (90/180/365), `e` clip (0.005/0.01/0.02), role vs user granularity, **plus** synthetic `U∼Flow` injection (Γ) and `n_esc`-removed `X` | How strong must omitted `U` be to kill `Δ` given `X` is already officer-observable | CPU |
| **E10** | Queue simulation (flow-aware) | Discrete-event replay per `m`; service `μ^{(m)}_flow`, state `Q_{entry_role}(t)`, routing `δ^{(m)}_flow`; congestion via `flow_capacity_90d` (M6 structurally, others via replay) | `Δ^{(m)}_{sim}` vs naive `Δ^{(m)}` — corrects SUTVA when `δ` concentrates on popular Secretary | ~1h |
| **E11** | Forward-graph feasibility | `t_forward_escalation` reachability of each `δ` chain vs historical vs `t_admin_escalation` templates | Legal-flow flag per `Δ^{(m)}` | CPU |
| **E12** | Model horse-race & residual probe | Compare `M0-M6` on val `RMSE`/`loglik`/`calibration` + residual `U^{(m)} ⟂ Flow | X` test (regress `residual` on `Flow` dummies within `X` strata) | Which model best soaks up officer-observable variation; naming the remaining `U` | CPU |
| **E13** | Shadow-logging design (doc) | Schema `X(=officer screen), office, A_dept, A_flow, e_*, sampled, T, correct, n_esc`, stepped-wedge power for office-override trial, kill switch | Future RCT | Doc |

`E0-E9` mandatory for a credible claim; `E10` strongly recommended (role congestion is the SUTVA violation); `E11-E12` required before any simulated flow is called implementable or any model is called "best"; `E13` required before live deployment. Because the harness is model-agnostic, **adding M7 (e.g. causal forest, deep AFT) is a one-file addition** — no OPE/simulation change — so "try as many as you want" is literally `janasunani/experiments/routing_outcome/models/m7_*.py` + re-run.

### 5. Provider & serving seam

- `janasunani/experiments/routing_outcome/` package: `flow.py`, `features.py` (officer-observable `X` only), `models/` (`m0_gbm.py`, `m1_aft.py`, … `m6_queue.py` each exposing `fit/predict/sample_residual`), `propensity.py` (hierarchical), `policy.py` (dept+flow per `m`), `ope.py` (model-indexed), `simulate.py` (draws `Y(δ)` under chosen `m`), `artifacts.py`, `office_override.py`.
- Artifact `models/routing_outcome.json` — aggregate only: per-`(category,district,dept)` shrunken `μ_dept,π_dept`, per-`(category,district,role_template)` `μ^{(m)}_flow,π_flow`, `e` coefficients, `office_default` map, plus `model_suite = [m0…m6]` summaries. Carries `schema_version`, `lake_snapshot_id`, `mapping_snapshot_id`, `checksum`. `load` verifies all before serving; missing mappings → fall back to dept.
- `janasunani/routing/provider.py`: `ROUTER_OUTCOME="outcome"` (dept, default `m` ensemble/median) and `ROUTER_OUTCOME_FLOW="outcome-flow"` (flow, with `?model=m6` query-style override for simulation). Returns `RoutingResult(method="learned", evidence={n_esc, entry_role, dept, model_id})`. Falls back `outcome-flow→outcome→incidence→MappingRouter` forever (never raises).
- `router_status` reports which model backs the active `outcome` without warming.

## Work Plan

| Unit | Depends on | Surface | Deliverable |
|---|---|---|---|
| **0. Worktree & branch** | — | git | `git worktree add .worktrees/muse-experiments -b muse/experiments feat/pipeline-quality-trunk`; `git push -u origin muse/experiments` |
| **0b. Flow decoder + officer-observable inventory** | 0 | `janasunani/routing/flow.py` + `tests/test_flow_decode.py` + `docs/…officer_observable.md` | Single decoder + inventory table (§Context) test (`54→Secretary`, `chain_len` dist, `decode_rate≥95%`, `X` vs officer screen audit) |
| **1. Outcome mart + flow enrichment** | 0,0b | `sql/routing_outcome.sql`, `marts.py`, `findings/routing_outcome.py` | Portable SQL + enrichment; `…-reconcile` CLI; `test_routing_outcome_mart.py` (fixture lake) |
| **2. Feature builder (officer-observable, no leakage)** | 1 | `experiments/routing_outcome/features.py` | Point-in-time `X` (≤`created_on`) with decoded flow history only as `pre-t` aggregates; synthetic-OLTP tests |
| **3. Label & split harness** | 1,2 | `experiments/routing_outcome/dataset.py` | Chronological train/val/test, `group_id=ticket_no`, censoring, `A_dept`/`A_flow` eligibility (support≥10, forward-feasible), DVC digests (lake+mappings) |
| **4. E0-E1 audit** | 3 | `notebooks/routing_outcome_E0_E1_audit.py` | Flow census + `T|correct` audit + officer-observable coverage table (office vs pending divergence, `n_esc` vs days) |
| **5. E2-E3 + E12 model suite** | 3 | `models/m0_gbm.py … m6_queue.py`, `train.py` | Fitted `M0-M6`, val RMSE/loglik/calibration, residual `U|X,Flow` probe, ablation (dept/flow/office/full `X`) |
| **6. E4 hierarchical propensity + overlap** | 3 | `propensity.py` | `e(dept)`, `e(flow|dept)`, `e(office)` with full `X`, ESS, positivity heatmaps, abstention sets |
| **7. E5-E9 policy + OPE + sensitivity (multi-model envelope)** | 5,6 | `policy.py`, `ope.py`, `office_override.py`, `simulate.py` | `Δ^{(m)}_{dept,flow,office_override}` (DR) + bounds, `τ` selection, spec curve, chain mediation, Γ/δ sensitivity per `m` |
| **8. E10-E11 queue + forward feasibility** | 5 | `simulation/queue.py`, `forward_check.py` | Queue-aware `Δ^{(m)}_{sim}` + `t_forward_escalation` reachability per `Δ^{(m)}` |
| **9. E13 shadow design doc** | 7 | `docs/plans/routing-outcome-shadow-design.md` | Logging schema (`X=officer screen, office, A_*, e_*, sampled, T, correct, n_esc, model_id`), role-week randomization, stepped-wedge power, kill switch |
| **10. Provider + artifact (multi-model)** | 7 | `routing/provider.py`, `artifacts.py`, `routing/reference/routing_outcome.json` | `JANASUNANI_ROUTER=outcome/ outcome-flow?model=m*` opt-in, status probe, checksummed dept+flow+suite artifact |
| **11. Evidence bundle** | 1-10 | `outputs/findings/routing_outcome/`, `docs/plans/` | Reviewer report: officer-observable table, full `vch*` decoded conditioning set, structural forms M1-M6, `Δ` envelope across models, `n_esc` mediation, overlap, Γ/δ sensitivity, forward feasibility, "what would falsify this" + simulation notebook where reviewer picks `m` |

Each unit ships with a real-code-path test (fixture lake, never prod Postgres — `tests/README.md`). `vch*` tests use a synthetic `mappings/` fixture.

## Validation Plan

- **Unit:** `uv run --extra serving --extra pipeline-core pytest tests/test_flow_decode.py tests/test_routing_outcome_mart.py tests/test_routing_outcome_features.py tests/test_routing_outcome_models.py -v` + `uv run ruff check .`
- **Mart reconciliation:** `uv run python -m janasunani.analytics.findings.routing_outcome --check` must match `RECONCILIATION_SQL` (0 tolerance) and `flow_decode_rate≥95%`; fails closed on drift.
- **Model suite gate:** each `m` must pass `E12` residual probe (`residual ⟂ Flow | X` within strata, joint F-test p>0.05 after BH) or be flagged "residual confounding" — no model is silently promoted.
- **Offline E2E (per model):** `uv run python -m janasunani.experiments.routing_outcome.train --train 2021-2023 --val 2024 --test 2025 --models m0,m1,m2,m3,m4,m5,m6` → `outputs/findings/routing_outcome/` with `Δ^{(m)}_DR_{dept,flow,office_override}` + 95% cluster bootstrap CIs + ESS. Envelope is the deliverable, not a single `Δ`.
- **Simulation replay:** `uv run python -m janasunani.experiments.routing_outcome.simulate --model m6 --test 2025` yields `Δ^{(m)}_{sim}`; M6's structural `wait` must move `Δ` toward `Δ^{(m0)}` baseline when congestion is high — if not, queue model is misspecified.
- **Artifact governance:** `routing_outcome.json` carries `schema_version, lake_snapshot_id, mapping_snapshot_id, model_suite, checksum`; `load` verifies all.
- **Preflight:** `router_status` with `JANASUNANI_ROUTER=outcome(.flow)` reports first rung without warming.
- **Reviewer check:** spec curve (`E9`) shows `Δ` envelope; if envelope straddles 0, headline is "model- and assumption-sensitive", not "faster." Residual `U` bound (`RV`) is printed alongside `Δ`.

## Risks / Rollback

| Risk | Mitigation |
|---|---|
| **Residual `U` (officer discretion / post-routing shock)** | `X` = officer's screen saturates case-difficulty; hierarchical DR + Γ/δ sensitivity + `E12` residual probe quantify remaining `U`; report `RV` ("how strong must `U` be to explain `Δ`"). Never claim `U=0`. |
| **Gaming via `bare`** | Conditional estimand + `π≥τ` + mandatory `P(correct)` per `m`; `M4` two-part is the dedicated diagnostic. |
| **Flow positivity** | `A_flow` support≥10 + forward-feasible; DR backs off to dept; ESS per `category×district×n_esc`; envelope flags sparse cells. |
| **Stale mapping snapshot** | Mapping digest in artifact; `flow_decode_rate` gate; `load` falls back to dept on mismatch. |
| **Model misspecification** | Suite `M0-M6` is the hedge; `M0` GBM is the unrestricted comparator; adding `M7` is one file. Envelope, not point, is the claim. |
| **Queue SUTVA (role congestion)** | `pending_stock_at_entry_role` in `X` + `E10` role-level queue simulation (M6 structurally); cap `δ` per role-day if `Δ_sim ≪ Δ_naive`. |
| **Citizen office override not deployable** | `E6` reports `office_override` `Δ^{(m)}` separately with `m_office_designation_mapping` legality flag. |
| **Censoring / tail** | Winsorize 365, treat unresolved as censored (M3 naturally), report median+p90; sensitivity to cap. |
| **Leakage** | Point-in-time `X` (≤`created_on`); `Flow` never in `X` (exposure), only `pre-t` role aggregates; `assert_group_disjoint`; chronological split. |
| **PII egress / branching** | Only `grievance_redacted` features; `vch*` local CSVs; aggregate artifacts; worktree tracks trunk, rebase in worktree only, never force-push trunk. |

Rollback: `JANASUNANI_ROUTER` unset → `crosswalk`; `outcome-flow` missing → `outcome` → `crosswalk`; `provider.py` never raises. Worktree removed with `git worktree remove .worktrees/muse-experiments`.

## Open Questions

1. **Correct disposal adjudication.** 90d reopen + `benefitted` override sufficient, or 200-case manual adjudication to calibrate `correct`? (Owner: ED; blocks causal language for all `m`.)
2. **`grievance_redacted` embeddings.** Frozen TF-IDF/on-box embedding available, or suite runs on structured+flow only?
3. **Eligible flow authority.** Empirical trailing-year support vs `t_admin_escalation`/`t_forward_escalation` feasibility — hybrid with feasibility filter assumed here; confirm.
4. **`τ` owner.** Who sets correctness floor per `m` — engineering (val-optimal) or dept policy? Vary per `m` or single `τ`?
5. **Simulation horizon & capacity proxy.** 90d vs until-resolved horizon; `Q/cap` definition (headcount vs trailing throughput) for `M6`?
6. **Office-override deployability.** Can citizen `office` actually be overridden, or must scorer stay within citizen's `office`? Determines whether `Δ_office_override` is simulatable or diagnostic — and which `m` to headline.
7. **Model suite headliner.** If `Δ^{(M0-M6)}` disagree, which `m` is primary for the provider? Propose `M6` (structural, queue-aware) as headline with `M0` envelope, `M1` as fast fallback — confirm.

---
*No worktree or branch was created — awaiting approval. This is v3; it supersedes v2 by adding the officer-observable identification (§Context) and the multi-model simulatable suite (M0-M6) so the counterfactual's assumptions are inspectable and the "try as many as you want" instruction is literally one file per new model. Plan saved as `docs/plans/2026-08-11-routing-disposal-optimization.md`.*
