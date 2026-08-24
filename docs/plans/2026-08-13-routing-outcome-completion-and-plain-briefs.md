# Plan: Finish the routing-outcome estimator, reconcile it against the field record, and flatten the three value-add documents

**Branch:** `muse/routing-experiments` off `feat/pipeline-quality-trunk`
**Type:** implementation + eval/research (offline), plus documentation rewrite
**Date:** 2026-08-13
**Supersedes:** [2026-08-11-routing-disposal-optimization.md](2026-08-11-routing-disposal-optimization.md)

> **Update, 23 August 2026:** This is a historical implementation plan, not the
> current evidence record. Subsequent workflow review established that the
> assigning officer jointly chooses a department and complete workflow template,
> then names the authorities occupying its nodes. `vchAllEscUser` is a stored
> serialization of those named nodes, not the literal workflow menu. The current
> complaint snapshot does not prove that either assignment field is immutable,
> and action history cannot reconstruct overwritten values because it contains
> no department-and-chain snapshots. See the design of record and
> `docs/experiments/routing-outcome-evidence-2026-08-19.json`.

## Context

`muse/routing-experiments` carries a 2,195-line design of record
([docs/experiments/routing-outcome-model.tex](docs/experiments/routing-outcome-model.tex))
and a partial implementation in
[janasunani/experiments/routing_outcome/](janasunani/experiments/routing_outcome/).
The design is complete; the estimator is not. Section 7.3 lists six gaps that
separate the provisional table from the estimand, and the README says in as many
words: **do not quote these numbers**.

Meanwhile the three value-add documents in
[docs/value-add-report/](docs/value-add-report/) carry routing *incidence*
(45.14% top-1 historical agreement) and zero routing *outcome* content, and are
written in a heavily styled, promotional register the comms team has to undo
before they can use it.

[docs/Janasunani_Canonical_Questions_14Aug_Demo.docx](docs/Janasunani_Canonical_Questions_14Aug_Demo.docx)
is the third input and it changes both halves. It is the 12 August field record
from the CM Grievance Cell: 40-odd canonical questions with the officers'
recorded answers, plus Annex B, a screen-by-screen capture of the live portal
taken on 11 August. It is the only document in the repository that describes
what officers actually do, and it does three things to this plan.

It **corroborates** that routing is an assignment-time decision, while requiring
a richer treatment definition. Figures B.9-B.11 show roughly fifty preset
workflow templates in one unsearchable flat list for the CMGC login. The officer
selects a department and complete workflow template jointly, then names the
authorities occupying its nodes. `vchAllEscUser` stores those named-node choices;
it is not itself the literal dropdown. Q2.6 says the displayed workflows are
"preset irrespective of the department, category, district," with the
office-specific caveat recorded below.

It **contradicts** part of the current framing. Q2.2 records that handwritten
Odia is *not* time-consuming for natively fluent officers, which is not what
DELIVERY.md and the briefs assume.

And it **exposes a probable measurement defect** in code that is already
running. Figure B.14 photographs the closure dropdown, and three of its six
templates do not appear in `dataset.py:LADDER_SQL`.

Three outcomes: close five of the six estimation gaps; correct the outcome
measurement against the portal's real dropdowns; and re-express all three
documents as plain Markdown carrying the routing work and the officer-side
baselines.

**Assumption stated, not checked with you:** DELIVERY.md freezes work Thursday
13 August with Friday as rehearsal. That freeze protects the demo path.
`muse/routing-experiments` is experimental, is not near `main`, and no serving
provider reads it, so this work proceeds. Nothing here touches the demo branch.

**Granted for this task:** read access to `data/interim/complaints.parquet`,
`data/interim/action_history.parquet` and `data/raw/janasunani-mappings`. Only
aggregates leave; row-level output stays in the gitignored
`outputs/experiments/routing_outcome/`.

### How to read the field record

The document mixes two kinds of statement and they must not be conflated.

**Sections 0, 2 and 3** are questions with recorded `Answer:` lines. Those
answers are the officers' own words and are the field evidence.

**Section 1, "Verify: data vs ground truth",** is the opposite direction: it
states *our* findings — computed by us from the lake and from the Box CA&GR
Analytics note — and puts them to the officers for confirmation. Most carry no
recorded answer. Nothing in section 1 is an officer statement, and none of its
numbers are adjusted for observables.

The italic line under every question in every section is our own analytic gloss
written before the meeting, not something anyone said.

Getting this wrong turns our own unadjusted descriptive statistics into
independent institutional corroboration of them, which is the single worst
mistake available in this document. When citing it, say which of the three
things a given line is.

**And a scope limit on all of Annex B.** Every screen was captured from one
login, in the CM Grievance Cell. A later operational clarification confirms
that an officer opening a registered but unassigned complaint in other offices
sees a similar assignment form. That generalises the assignment transaction's
structure, not its exact options: the ~50 workflow chains (B.9-B.11), the
*(Suggested by AI)* department label (B.8), named-authority menus, and other
controls remain directly verified for **CMGC only**. The closure dropdown
(B.14), discard reasons (B.16), revert modal (B.17), and reporting surface
(B.18-B.21) also remain CMGC-only evidence. Q2.6 carries the menu caveat in the
respondent's own words: "Could vary by office though, since this was just the
CMGC."

This matters for magnitude, not just phrasing. By intake office the corpus is
Collector 693,691, Departments 280,305, Office of Chief Minister 217,253, Chief
Secretary 33,037 — so the captured login corresponds to roughly one intake in
six. Treat Annex B as **one office's ground truth**, never as the schema.

Two consequences run through the plan below. Annex B is a *lower bound* on each
dropdown's contents, so it can explain a string we find in the data but cannot
rule one out. And wherever the plan leans on an Annex B screen, either scope the
claim to the CMGC in the text, or check how far it generalises against the
corpus — which for the two claims that carry real weight is cheap, and is
specified in A0.

### Hard constraint on the field record

Annex B.1 carries the line: *"Real citizen names, mobile numbers, email
addresses and home addresses are visible in these screens: internal use only, do
not circulate."* The document footer reads *"Internal — not for circulation."*

No Annex B figure, screenshot or crop goes into any brief, the .tex, or anything
rendered for comms. Facts *derived* from those screens — the contents of a
dropdown, a count on a report, the shape of a form — are fine and are most of
the value. The document may be cited as a source in the internal .tex and in
`docs/value-add-report/README.md`; it must not be attached to or excerpted in
the shareable documents.

### Where this plan lives, and what it supersedes

Save as `docs/plans/2026-08-13-routing-outcome-completion-and-plain-briefs.md`,
matching the tracked `YYYY-MM-DD-slug.md` convention.

It succeeds
[docs/plans/2026-08-11-routing-disposal-optimization.md](docs/plans/2026-08-11-routing-disposal-optimization.md),
whose title — "minimize disposal time **conditional on** correct disposal" — is
the principal-stratum error §4 of the .tex exists to correct, and whose
six-model suite (M3 Cox, M5 hierarchical Bayes, M6 queue-augmented) was never
built. Add a header note to that file pointing here rather than deleting it: it
is the record of how the framing changed, and `train.py` already cites its
unbuilt models by name.

---

## Part A — correct the outcome, then close the estimation gaps

### A0. First: reconcile the ladder against the portal (do this before anything else)

`dataset.py:LADDER_SQL` matches six exact strings. Figure B.14 photographs the
six templated closure phrases the portal actually offers. They are not the same
six.

| Portal dropdown (Figure B.14) | In `LADDER_SQL`? |
|---|---|
| Disposed with appropriate action | yes, `with_action` |
| Disposed & beneficiary benefited | yes, `benefit` |
| Disposed | yes, `bare` |
| as reported | **no** — falls to `off_ladder` |
| assigned to the concerned authority and disposed with appropriate action | **no** — falls to `off_ladder` |
| as reported by the concerned authority the grievance has been disposed and the petitioner has been benefitted | **no** — falls to `off_ladder` |

If this holds in the data it is the highest-value single finding available,
because the third missing string is unambiguously a *benefit* closure being
scored as off-ladder and therefore `C=0`. The .tex currently treats "as reported"
(90,061 cases, 7.4% of the corpus) as an ambiguous *free-text* remark; Figure
B.14 says it is a dropdown pick and a plausible truncation of the longer benefit
template.

**But do not verify it as a fixed list.** Annex B is one office's login, so the
six strings are a lower bound on what exists system-wide. Checking only those six
would find CMGC's templates and silently miss every other office's.

Invert the method. Go data-first:

1. Enumerate the top exact-normalised closing remarks by frequency across the
   whole action history — the same normalisation `LADDER_SQL` and
   `discards.py` already use — down to a floor of, say, 1,000 occurrences.
   Table 4 in the .tex was built this way, so the machinery exists.
2. Stratify that enumeration by **who closed the case**, which is the last role
   in the chain, not the intake `office`. The closure remark is written by the
   closing officer, so intake office is the wrong key.
3. Use Figure B.14 and B.16 to *interpret* the CMGC-origin strings, not to
   define the set.

Three things fall out, and all three are wanted. Which remarks currently land in
`off_ladder` and at what volume. Whether the template vocabulary differs sharply
by closing role — if it does, that is a finding in itself and the `S`/`C` map may
have to be role-aware, which is an open question this check settles. And whether
"as reported" (90,061 cases, 7.4%) is concentrated in the CMGC or is
system-wide, which decides whether Figure B.14's longer benefit template is a
plausible reading of it or a local coincidence.

If the three strings turn out already matched, or vanishingly rare, say so and
move on. The check is cheap and the hypothesis is disposable.

The same exercise for discards. Figure B.16 photographs fourteen reasons;
[janasunani/analytics/findings/discards.py](janasunani/analytics/findings/discards.py)
`TEMPLATES` covers eight families. Unmapped there: *Anonymous*, *Complaint
details not legible*, *Will be considered as per rule in due course of time*,
*Cannot be considered beyond rule*, *Thanks for the Suggestions*, *Other*. Four
of the named five are clearly non-actionable and would lift `S=0` coverage above
the .tex's estimated 84% — but again, the empirical enumeration decides the set
and Figure B.16 only helps read it.

`discards.py` says adding a template is "a governed lookup-table change." Do not
silently edit it. Build the extension as a routing-outcome-local map that imports
`TEMPLATES` for the shared strings and declares each addition with its source
(empirical frequency, plus Figure B.16 where it corroborates), and note in the PR
body that upstreaming into `discards.py` is a separate governed change.

### A0b. How far does the workflow menu generalise?

The assignment-form structure is now operationally confirmed beyond CMGC. The
remaining Annex B claim carrying real weight is that the menu itself contains
~50 hardcoded chains in one flat list with no district or category logic
(B.9-B.11, Q2.6). Scoped to CMGC that is an observation; asserted system-wide
it is the premise of the deployability argument in §6.3.

`e0_flow_census.py` can describe which role templates were realised in the
corpus, but realised assignments do not identify the menu that each assigning
office was offered. Stratification by intake office is additionally not a
substitute for the responsible assigning office. The census therefore measures
empirical support only; it cannot establish that the CMGC menu generalises.

Generalisation requires either source-system workflow configuration versioned by
office and date, or direct verification from other assigning-office logins. Until
then, the ~50-template flat-list description remains CMGC-only evidence.

### A1. `outcome.py` — the three-state outcome (gap 1, "highest priority")

Per Def. 2.5: `S` = actionable (a property of the grievance, safe to condition
on), `C` = substantive action taken (moved by routing, an outcome).

Reuse:

- `discards.py:TEMPLATES` — the governed exact-match discard families,
  normalised the same way `LADDER_SQL` normalises.
- [janasunani/evaluation/actionability.py](janasunani/evaluation/actionability.py)
  `WEAK_LABELS_BY_DISCARD_FAMILY` — families to the five-class taxonomy.
- The A0 extensions from Figure B.16.

One deliberate divergence, commented in the code. That mapping gives
`case_already_taken_up` and `duplicate_copy` a `WeakLabel(None,
eligible_for_training=False)`, because for the *actionability classifier* the
duplicate signal belongs to the dedup task. For the *three-state outcome* a
duplicate is exactly `S=0` — it is the case §2.3.2 leads with. So
`routing_outcome` needs its own family-to-`S` map that borrows the strings and
assigns actionability itself.

Three buckets, not two:

| Bucket | Rule |
|---|---|
| `S=0` | Remark matches a discard family mapping to underspecified, irrelevant, out_of_scope, policy_blocked, either duplicate family, or an A0 addition |
| `S=1` | Remark is on the corrected disposal ladder, where `C` follows from the rung |
| `S` unknown | Everything else |

Do not build a text model for whatever tail remains. The spec requires one
validated against a hand-adjudicated sample and that sample does not exist.
Report the unknown share as a stated limitation.

Changes `dataset.py`: emit `S`, `C`, `s_bucket`; stop dropping censored rows
(A2 needs them); keep writing `censoring.json`.

### A2. `censoring.py` — RMST and IPCW (gap 3)

Per Def. 2.7 and Thm. C.2. `Y = min(T, 365)`. Censoring is administrative, so
`D_i` is a deterministic function of arrival date and snapshot — the favourable
case. Kaplan-Meier for `G(t | x, a)` within coarse strata; district-year is the
natural stratum since it already indexes the cluster bootstrap. IPCW weight
`R_i / G(Y_i-)`.

This is what makes test 2025 admissible. At 34.4% censored its completers are
selected on speed, which is why the current code confines itself to val 2024.

**New caveat from the field record.** Q3.7 records that physical grievances
carry an unmeasured receipt-to-registration lag, and that Odia filers are
disproportionately in the Physical channel. `T` starts at registration, so for
roughly one filing in six it understates the citizen's true wait, and the
shortfall correlates with language. This is a limitation of `T` itself, not of
the censoring model. It belongs in §2.5 and in the briefs; it cannot be fixed
from the data.

### A3. `smear.py` — Duan retransformation (gap 4)

Per Def. 5.3. `s_hat = mean(exp(residual))` from the training log fit, computed
within strata rather than pooled. Applied at both points where a log-scale
prediction becomes days:
[policy.py:112](janasunani/experiments/routing_outcome/policy.py#L112) and
[run_ope.py:63](janasunani/experiments/routing_outcome/run_ope.py#L63).

§7.3 measures the uncorrected gap at roughly 25-30 days, which is why
`Delta_DM` and `Delta_DR` disagree by 11 days for ridge. Fixing it should
visibly close that disagreement — a good end-to-end check that the correction
is real.

### A4. `crossfit.py` — cross-fitting and policy sample splitting (gap 5)

Per eq. (5.4) and §6.1. `K` folds; nuisances fitted outside each fold, score
evaluated inside it. Nested so the `argmin` policy is learned on a fold
complementary to the one it is scored on, defusing the winner's curse of
Lemma F.3.

Folds respect the district-year cluster. Splitting within a cluster leaks the
shared shock the clustering exists to price.

### A5. `tau.py` — calibrating the correctness constraint (gap 2)

Per Cor. 4.6 and §6.2. Sweep `tau` over a grid; for each, form `delta_tau`;
estimate `V_C(delta_tau)` by the same augmented score with `C` in place of `Y`;
take the **smallest** `tau` meeting `E[C] >= E[C_hist]`.

Emit the whole curve, not the selected point. The speed-correctness frontier is
the single best exhibit this work produces for a non-technical reader.

`policy.py:132` already threads `tau` through `score_policy` and gates it on
`pi_model`. `pi_gbm` must be isotonically calibrated first or `tau` is
uninterpretable (Table 8) — one `CalibratedClassifierCV` in `train.py`, and the
status table already flags it as "Built, uncalibrated."

### A6. The written-judgement covariate — a design question to settle, not a mechanical add

Figure B.12 shows *Resolution Time (In Days)* free-typed as 15 on the assignment
form, and Q0.3 confirms it is free entry with no SLA behind it.

This matters because §3.3 rests the whole identification argument on the claim
that unconfoundedness here requires "no unwritten judgement" rather than "no
unobserved case characteristics". Here is judgement that *is* written, on the
assignment screen, and it is not in `X`.

There is no `resolution_time` column in
[janasunani/db/models.py](janasunani/db/models.py), but there is
`escalation_date` ("treated as overdue date") alongside `assigned_on`, and
Figure B.6 shows a due date on the review card. So the typed value is probably
recoverable as `escalation_date - assigned_on`. **Verify that derivation
reproduces plausible free-typed integers before using it.**

The design question, which the .tex must answer rather than the code assume: the
officer types the days and picks the flow in the same act on the same form
(Figure B.12). So the value is not cleanly pre-treatment with respect to the
flow — it may be a mediator or a co-decision, and conditioning on a co-decision
is not free. The honest treatment is probably as a *proxy for private judgement
in the sensitivity analysis* rather than a covariate in the main design. Resolve
it in §2.2 and §8 with an argument; do not just append a column.

The same workflow record shows named authorities selected for every workflow
node and an **Assign Another ATA** control. The role-template policy therefore
coarsens a richer assignment transaction. Verify how named-node choices are
stored and whether **Assign Another ATA** adds a simultaneous destination,
replaces the first assignment, or starts a separate transaction. If it creates
parallel assignments, the current single-pair treatment is incomplete; do not
bury that question inside the resolution-time covariate decision.

### A7. Tests

Extend [tests/test_routing_outcome_experiments.py](tests/test_routing_outcome_experiments.py),
one section per module, matching the existing convention: synthetic frames built
in-file, mapping tables synthesised in `tmp_path`, deterministic seeds, nothing
reads `data/`.

| Module | Test |
|---|---|
| `outcome.py` | Each of the six Figure B.14 strings lands on its intended rung; `duplicate copy` gives `S=0`; an unrecognised family raises rather than defaulting; whatever remains unknown stays unknown and never becomes `S=1` |
| `censoring.py` | Under synthetic uninformative censoring with known truth, IPCW-weighted RMST recovers the uncensored RMST and the completers-only estimate is biased low |
| `smear.py` | On a log-normal draw the smeared prediction recovers `E[T]` and naive `expm1` recovers something near the median |
| `crossfit.py` | On a pure-noise design the in-fold score is optimistic and the out-of-fold score is not; folds never split a cluster |
| `tau.py` | The frontier is monotone: raising `tau` weakly raises `V_C` and weakly raises `V_T` |

Gate: `uv run --extra pipeline-core pytest tests/test_routing_outcome_experiments.py`
and `uv run ruff check .`, both green before any run over `data/`.

### A8. Run and document

```bash
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.dataset
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.train
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.tau --split val
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.ope --split val
uv run --extra pipeline-core python -m janasunani.experiments.routing_outcome.ope --split test
```

Then edit the .tex:

- **§2.3.2 and Table 4** — rewrite around A0. If the three portal strings are
  confirmed missing, "as reported" stops being a free-text puzzle and becomes a
  dropdown truncation, and the ~84% coverage estimate rises. Cite Figure B.14 as
  the source without reproducing it.
- **§2.4 (Treatment)** — describe the assignment transaction, scoped honestly.
  The department and complete workflow template are jointly selected; named
  authorities instantiate the nodes. Roughly fifty preset workflows appear in
  one unsearchable flat list **as captured in the CM Grievance Cell** (Figures
  B.9-B.11; Q2.6 carries the same caveat). `vchAllEscUser` is the resulting
  named-node serialization, not the menu itself. Do not claim the offered menu
  generalises without source configuration or another assigning-office login,
  and do not interpret current snapshot fields as immutable initial assignments
  until source-system evidence establishes that provenance.
- **§2.2 and §8** — the written-judgement covariate question from A6.
- **§2.5** — the physical-channel measurement lag from A2.
- **Table 5 (corpus)** — three-state counts replace the binary `C=1` column;
  add the `S` unknown share.
- **§7.3** — rewrite. Heading stops saying "Provisional". The six-gap list drops
  to congestion plus the unadjudicated tail. State plainly which way the
  estimate moved as each correction landed; if the magnitude falls, that is the
  result.
- **New: the PMAY worked example, as a demonstration of what adjustment does.**
  Q1.3 contrasts Route 2 (Collector→BDO, 23 days) with Route 4
  (CM Cell→Collector→BDO→Collector, 48 days, 9 of them the final return), and
  extrapolates ~32k PMAY cases a year.

  **These are our own numbers, not the officers'.** They come from the Box
  CA&GR Analytics note and are raw mean durations by route with no adjustment
  for observables; Q1.3 puts them to the officers to verify, and no answer is
  recorded against it. So there is no field corroboration in either direction.

  That makes the example better than a checkable field figure. It is a published,
  naive, flow-level contrast of exactly the kind §3 and §4 exist to correct —
  and Table 7 already shows why: harder cases are escalated further, so part of
  any raw gradient by chain length is selection rather than mechanism. The
  contribution is to run the same comparison through the built estimator and
  report how much of the raw 25-day gap survives adjustment for observables,
  censoring, retransformation and the winner's curse. Expect it to shrink; say
  by how much.

  Nothing derived from the unadjusted contrast — including the ~32k/yr
  extrapolation and the "saves 9 days" reading — may be quoted as a benefit
  estimate in any document until the adjusted figure exists to sit beside it.
- **New table** — the `tau` speed-correctness frontier.
- **Table 12 (status)** — five rows flip to Built. Congestion, sensitivity,
  queue replay, hierarchical propensity and negative controls stay `\notbuilt`.
- **§7.2** — refit metrics after calibration and smearing.
- **Abstract and date.**

Rebuild: `latexmk -pdf docs/experiments/routing-outcome-model.tex`. Build output
is gitignored (`714e3de`).

Update [janasunani/experiments/routing_outcome/README.md](janasunani/experiments/routing_outcome/README.md):
"Not built" loses its first five entries, "Provisional numbers" becomes
"Results", `superseded/` note stays.

**Fallback if the run does not finish.** Ship A0-A7 with the status table
flipped to Built and §7.3's withdrawn-results framing intact, and have the
briefs say the machinery exists and the rerun is pending. Do not ship a
half-corrected number.

---

## Part B — three plain Markdown documents

### B1. Format

Markdown in the DPIC dialect, rendered by the installed converter:

```bash
dpic-build-brief docs/value-add-report/officer-brief.md \
                 docs/value-add-report/Janasunani_2.0_IAS_Officer_Brief_August_2026.docx
```

`dpic-build-brief` is a console script from the `dpic` package
(`dpic.documents.brief:main`). The dialect, parsed by
`dpic/documents/markdown.py`, is deliberately small:

- YAML frontmatter: `title`, `subtitle`, `author`, `date`, `organisation`,
  `partnership`, `status`. [docs/DELIVERY.md](docs/DELIVERY.md) already uses
  exactly this frontmatter — copy its shape.
- `#`, `##`, `###` headings only.
- Paragraphs, `> blockquote`, `*italic note*`.
- Tables as `**Table N. Caption**` then a pipe table, with optional
  `<!-- widths: 30 40 30 -->` and a following `*Note: ...*`.
- `![caption](path)` figures with `<!-- width: 6.5 -->`, resolved against an
  `Exhibits/` directory beside the source.
- `<!-- pagebreak -->`, `---` rule, `[^1]:` footnotes.

**The dialect has no list support.** Bullets become paragraphs. Anything
currently a list must become prose or a table row — and most of the existing
bullet runs are really tables with the headers left off.

| Markdown source | Renders to |
|---|---|
| `docs/value-add-report/value-add-report.md` | `Janasunani_2.0_Value_Add_Report_August_2026.docx` |
| `docs/value-add-report/officer-brief.md` | `Janasunani_2.0_IAS_Officer_Brief_August_2026.docx` |
| `docs/value-add-report/capability-brief.md` | `Janasunani_2.0_Public_Systems_Capability_Brief_August_2026.docx` |

### B2. What happens to the generators

[scripts/create_officer_brief.py](scripts/create_officer_brief.py) (592 lines),
[scripts/create_public_systems_capability_brief.py](scripts/create_public_systems_capability_brief.py)
(330) and [scripts/update_value_add_report.py](scripts/update_value_add_report.py)
(1,002) are almost entirely python-docx layout code — `_shade`, `_callout`,
`_headline_cards`, `_set_table_geometry`, palette constants. That goes.

What must survive is the fail-closed contract. All three load `BenchmarkFacts`
via [janasunani/evaluation/value_add_benchmark_facts.py](janasunani/evaluation/value_add_benchmark_facts.py)
and refuse to build if the bundle lacks real timing, the selected actionability
test, the weak-label audit, the PII scorecard or both routing scorecards. The
value-add README promises this. Each generator becomes a **Markdown emitter**
that still loads the bundle and still raises on a missing artifact: numbers stay
derived from one bundle ID, only the output format changes.

Figures in [docs/value-add-report/figures/](docs/value-add-report/figures/) need
an `Exhibits/` directory beside the Markdown, or the `exhibits_dir` argument via
a thin wrapper.

### B3. What "plain" means

- No palette. No navy/teal/gold, no shaded cells, no coloured body text, no
  callout boxes, no headline cards, no `⚠` glyphs, no arrow chains.
- Headings name their subject. "Turn a promising tool into measurable public
  value" becomes "Decision requested". "One screen, three decisions" becomes
  "What the officer sees".
- No sentence sells. "The value proposition", "Example of better triage" and
  "Honest claim" stop being framing devices; the content stands as prose under a
  neutral heading.
- One claim per paragraph. Every number carries its denominator in the same
  sentence and its evidence-ladder status in the same paragraph.
- Emphasis comes from a table or a heading, never from bold inside a tinted box.

The audience is the comms team. They need source material, not a designed
document to undo.

### B4. New content: the routing work

Absent from all three documents today.

1. The gameability argument. Under the binary label "correct" disposals are
   18 days *slower* at the median than "incorrect" ones. The fastest way to
   close a grievance is not to work on it, so an unconstrained speed objective
   learns the wrong policy.
2. The disposal ladder over 1,209,144 resolved grievances: 39.1% no action
   claimed, 35.7% non-standard text, 23.2% appropriate action, 1.9% benefited —
   restated against whatever A0 corrects.
3. The three-state design and why a binary label misscores roughly 144,700
   correctly-closed non-actionable cases as failures, disproportionately among
   fast cases.
4. The flow census: 1,344,908 chains all decode; 1,318 role templates over
   1,047 category-district cells; 676 cells with an admissible set. Set beside
   the fifty-chain dropdown, which is what those templates are in practice.
5. The intake label is not the treatment: of 693,691 grievances filed at
   "Collector", 12.8% are ever pending with a Collector.
6. Handoffs cost time: median duration roughly doubles from a two-link to a
   four-link chain — before adjustment, and Table 7 notes that part of that
   gradient is selection, since harder cases are escalated further.
7. Ridge beats gradient boosting out of sample (1.156 vs 1.240 val RMSE) — the
   argument for an interpretable deployed model.
8. The `tau` frontier and whatever `Delta` survives A8, with its limitations.
9. The PMAY route contrast as the worked illustration of item 6: a raw 23-vs-48
   day gap we published in the CA&GR Analytics note, set beside what survives
   adjustment. This is the clearest available demonstration of what the whole
   estimator is for, and it should carry the caution that the unadjusted gap was
   ours and was never a benefit estimate.

Items 1-7 stand regardless of how A8 lands. Items 8 and 9 are contingent on it.

Long report and capability brief carry all nine. The officer brief carries
1, 2, 5, 6, 8 and 9 — the ones that change what an officer would do.

### B5. New content: the field record

This is the part that makes the briefs true rather than merely plain.

**The KPI vacuum, which is the actual value proposition.** Q0.1: the only thing
tracked is disposal %. Q0.2: the cell's dashboards carry no time-related metric,
only disposal and pendency. Q0.4: `benefitted` reaches the database and no KPI
depends on it. Figure B.19: the pendency report is counts only, with no median
age or time-to-resolve anywhere on the screen. Figure B.20 prints its own
formula, `Disposal rate = (Resolve/Total)*100`, over filings rather than
problems or citizens.

So the routing-outcome work is not a better number for an existing KPI. It is a
KPI the office does not have. That is the Monday framing and it should open the
officer brief.

**Scope it in the sentence, not in a footnote.** All of that is the CM Grievance
Cell: its dashboards, its reports, its login. Q2.9's answer gestures wider — "the
portal has very poor analytics and only tracks pendency and resolution" — but it
is the same respondent describing the same product. Write it as *verified for the
CM Grievance Cell, believed general, not checked against a department or
Collector login*, and say plainly that checking it is a half-hour of screen
sharing with any other office. An unverified claim with the check named is
credible; the same claim stated flat is the kind of thing a Secretary corrects
from memory in the room.

**Two governance facts that belong beside it**, both carrying the same CMGC
scope. Figure B.21 shows a colour-coded disposal-% league table by subordinate
role already shipping in the product — so office comparison is not a
hypothetical we are introducing, it exists and is unadjusted for case mix. And
Figure B.8 shows the department field already labelled *(Suggested by AI)*: a
suggestion exists today, with no measured accuracy and no record of officer
override. The incumbent is not "no suggestion." It is an unmeasured one, and the
missing override log is exactly the exposure-and-decision instrumentation
[docs/IMPACT_METRICS.md](docs/IMPACT_METRICS.md) says a causal claim needs.

The AI-suggestion point is the one most worth confirming beyond CMGC before
Monday, because "there is already an unmeasured model in production" is a strong
claim and it is the premise for asking to measure it. One screenshot from a
Collector login settles it.

**The officer-time baseline.** Q2.2: turning a raw document into a complaint
takes roughly 10-15 minutes, irrespective of language. Self-reported by
officers, not measured. It is nonetheless the only officer-time denominator
anyone has, and every time-saving claim needs one. Report it as self-reported.

**A correction the briefs owe.** Q2.2 also records that handwritten Odia is *not*
time-consuming for officers who are natively fluent in it. DELIVERY.md and the
current briefs lean on handwritten Odia as the bottleneck. That framing has to
change: the bottleneck the officers describe is the fixed 10-15 minutes of
comprehension and typing, not the script.

**A qualification the category benchmark owes.** Q2.5: category "is actually not
meaningfully important for routing and doesn't necessarily change routing
decisions," so officers do not invest in getting it right. The 46.55% top-1
historical-agreement figure is therefore measured against labels that are noisy
*because the field carries no operational weight*. That is a stronger and more
useful caveat than the current "historical labels, not policy correctness," and
it should replace it wherever the category number appears.

**For the capability brief.** Q3.6: voice is a requested feature, and in the
respondent's own words would "obviate the need for complex document processing
except for pure verification tasks." That is a strategic signal about where this
programme goes next and it belongs in the portable account.

**Continuity hook for Monday.** Annex D records that the demo's own routing card
already reads *"based on 204 past cases, 89% — past practice not best
practice."* That sentence is the routing-outcome thesis, already on stage on
14 August. Monday's script should pick it up verbatim: the demo stated the
problem, this work is the beginning of the answer.

### B6. README and cross-references

[docs/value-add-report/README.md](docs/value-add-report/README.md):

- Regeneration commands replaced with `dpic-build-brief`.
- A routing-outcome row in the "Verification status" table.
- A routing-outcome entry in "Known gaps": congestion absent from `X`, the
  unadjudicated remark tail, no officer validation of the `S` map, and the
  physical-channel registration lag.
- Rendering section rewritten — `dpic-build-brief` replaces the `DOCX_RENDERER` /
  `render_docx.py` dance. Keep the instruction to open every rendered page and
  check clipping; still true.
- Add the canonical-questions document to "Sources", with its
  not-for-circulation status noted.

[docs/QUALITY_BENCHMARKS.md](docs/QUALITY_BENCHMARKS.md) gets a routing-outcome
subsection under its existing routing heading, clearly separated from the
incidence benchmark. The two measure different things and the register exists to
stop exactly that conflation.

---

## Verification

**Part A**

1. `uv run ruff check .`
2. `uv run --extra pipeline-core pytest tests/test_routing_outcome_experiments.py -v`
   — every new property test green before touching `data/`.
3. `uv run --extra serving --extra pipeline-core pytest` — full suite, confirming
   nothing in `evaluation/` or `routing/` regressed.
4. A0's empirical remark enumeration runs, stratified by closing role, and
   reports explicit counts whichever way they come out — including whether the
   template vocabulary varies by role enough to force a role-aware `S`/`C` map.
5. A0b reports the share of non-CMGC flow volume falling inside the ~50 captured
   chains, and §2.4 states the dropdown claim no more broadly than that share
   supports.
6. The five-command run completes; `ope_val_ridge.json` and `ope_test_ridge.json`
   carry an `S`/`C` population, an IPCW-weighted RMST, a smearing factor, fold
   IDs and a calibrated `tau`.
7. Sanity checks that would catch a silently wrong correction: `Delta_DM` and
   `Delta_DR` agree far more closely than the current 11-day ridge gap; the
   test-2025 realised mean rises from 40.6 days toward the 2024 figure once
   censoring is weighted rather than dropped; `tau > 0` costs speed.
8. The PMAY route contrast is re-run through the estimator and reported beside
   the unadjusted CA&GR figure, with the adjusted-versus-raw difference stated
   explicitly. If adjustment does not shrink it, that is a finding about the
   design and needs explaining, not burying.
9. `latexmk -pdf docs/experiments/routing-outcome-model.tex` builds clean and the
   PDF is read end to end — no `??` references, no overfull tables.

**Part B**

10. All three `dpic-build-brief` invocations succeed.
11. Each `.docx` is opened and read page by page: no clipped tables, no repeated
    headers, no blank pages, no font substitution. Successful conversion is not
    visual verification.
12. Every number reconciles against `docs/QUALITY_BENCHMARKS.md`, and every
    officer or citizen outcome against `docs/IMPACT_METRICS.md`.
13. Every claim sourced from Annex B is scoped to the CM Grievance Cell in its
    own sentence, carries A0b's generalisation share, or is limited to the
    cross-office assignment-form structure covered by the later operational
    clarification. Grep the three Markdown sources for the Annex B claims and
    check each one.
14. Grep all three Markdown sources and the rendered `.docx` for Annex B figure
    references, screenshot paths and any citizen identifier. Nothing from the
    field record's screens may appear.
15. Delete a required artifact from a scratch copy of the bundle and confirm all
    three generators still fail closed. This is the guarantee the README makes
    and the rewrite is the moment it would quietly break.

## Out of scope

Congestion `Q_r(t)` and trailing performance in `X`; hierarchical propensity;
sensitivity analysis; queue replay; negative-control and placebo checks. All
remain `\notbuilt` in Table 12 and are named as such in the briefs. A6 settles
the written-judgement covariate on paper only; it is not added to the fitted
design in this pass.

Upstreaming the Figure B.16 discard additions into
`janasunani/analytics/findings/discards.py`. That is a governed lookup-table
change and gets its own PR.

Landing the 13-PR stack, and the untracked base branch
`feat/evaluation-classification-core`. Separate decision, after Monday.
