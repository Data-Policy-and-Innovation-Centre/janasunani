# janasunani.analytics: marts and findings over the lake

The analytical layer. It reads the Parquet lake (`janasunani.olap.lake`), never
OLTP, and it produces two different kinds of thing.

## Marts (`sql/`)

Governed derived tables, written as portable SQL views over the lake's base
tables. **The SQL is the deliverable**, not an implementation detail. Several
of these are handed to the department to run for themselves, so
`marts.py` only locates and installs it. Nothing rewrites it and no mart's logic
is duplicated in Python.

Portability is on the SQL author: keep to constructs DuckDB (our lake) and
PostgreSQL (their database, and our OLTP store) both accept. Verified the only
way that means anything, by running the mart over a fixture lake in `tests/`.

| Mart | What it defines |
|---|---|
| `closure` | The disposal ladder, each resolved complaint's rung and trajectory, and the closure finding's aggregate views (#76). |
| `action_type` | The 7-class action-type lookup over high-frequency `action_taken_remark` templates, built **per status** (#75). |
| `handoff` | Descriptive elapsed time between recorded handling steps, with aggregate coverage and sensitivity tables. |

### `action_type`: what the officer did (#75)

```bash
uv run python -m janasunani.analytics.action_type   # print lookup stats
# SQL hand-over:
uv run python -c "from janasunani.analytics.marts import mart_sql; print(mart_sql('action_type'))"
```

The cheapest intelligence-layer item: no OCR, no document ingest, no GPU, and no
dependency on the dedup index. Ten distinct strings cover 45% of 6.5M action rows;
top 500 buys 62%. The August contract is **exact-match lookup over high-frequency
templates only** — the free-text tail (1.16M singletons, 17.8% of rows) is
Post-demo and is a privacy boundary.

**Taxonomy (7 + admin noise).** `forwarded_delegated` · `reported_back` ·
`disposed_no_claim` · `disposed_with_action` · `benefit_delivered` ·
`discarded_with_reason` · `reopened_escalated` plus `admin_noise` (".", "ok",
scheme names typed into the remark field). LLM-assisted drafting, human
adjudication (ROADMAP §5.6 A). The Python module
`janasunani.analytics.action_type` is the source of truth; `sql/action_type.sql`
mirrors it for lake / PostgreSQL hand-over and they are tested row-for-row.

**Per status, not corpus-wide.** Status #3 is dropdown-driven (1.18M rows,
15,390 distinct remarks), status #2 is near free text; 301 of the top 500
templates span >1 status, one spanning 12 of 15. So the lookup is keyed by
`(template, status)` with a corpus-wide fallback, not by template alone.

**Consistent with #76.** The six closure ladder strings are a subset of this
lookup: `bare` → `disposed_no_claim`, `with_action` → `disposed_with_action`,
`benefit` → `benefit_delivered`, same normalisation.

**Privacy.** `action_type_unclassified_templates` (the drift diagnostic) emits
only high-volume (≥1000) normalised templates, never free citizen prose.

## Findings (`findings/`)

The presentation built on a mart: aggregate tables, the reconciliation, and the
Markdown fragment that goes in front of an audience with the caveats that must
travel with each number. Each finding has its own CLI writing to
`outputs/findings/`; closely related findings may share audited lookup logic.

The remaining record-only Sprint 3 findings use exact, enumerated
high-frequency discard templates and never read complaint prose:

```bash
uv run janasunani-closure-headline
uv run janasunani-two-day-bare-closures
uv run janasunani-discard-reasons
uv run janasunani-confirmed-duplicates
uv run janasunani-misrouting-baseline
```

Each command writes one aggregate CSV and one Markdown fragment, labels the
result as an **Insight**, and reconciles its lookup-join result against an
independently written `CASE` query before publishing. The confirmed-duplicate
count is the manual-process baseline; the MinHash increment remains a separate
capability claim.

The dated ROADMAP counts remain in the discard outputs beside the current lake
counts and their delta. A refreshed action history therefore cannot silently
rewrite the baseline. The two closure entrypoints also reconcile the portable
window-function mart against an independently structured DuckDB `arg_max`
query before writing either artifact.

The intelligence-layer findings (Phase 15) sit alongside them and read the
dedup index and the redacted text rather than the action history:

```bash
uv run janasunani-publish-workload      # filings vs distinct problems, digest-guarded
uv run janasunani-publish-intelligence  # EWMA spike decomposition, category × district × week
uv run janasunani-publish-themes        # concentrated-and-rising themes within one category
```

These read the **redacted** text only, and label a spike by which measure drove
it — filings, distinct problems, or distinct citizens — so a campaign is not
reported as a false spike.

Two house rules, enforced by tests rather than by convention:

- **Aggregates only.** No finding prints a row of citizen writing. Where one
  emits strings at all they are high-frequency dropdown templates, bounded by a
  minimum-use threshold.
- **Insight or capability, said out loud.** An *insight* is something the record
  already contained and nobody had queried; a *capability* is something no
  existing dashboard could produce. Presenting the first as the second is the
  failure mode (ROADMAP §5.3).

### `handoff`: elapsed time between recorded steps

```bash
uv run janasunani-handoff-finding                 # aggregate findings -> outputs/findings
uv run janasunani-handoff-finding --print-sql     # SQL handover; install action_type.sql first
```

This phase-1 mart describes **completed gaps in the recorded event stream**;
it is not a routing-time estimate, delay, idle time, or saving claim. Its
coverage table separately counts rows without a ticket identifier or timestamp,
and its dedup sensitivity table is an unknown-direction subpopulation comparison
-- never a bound or correction. The rendered finding carries the fuller caveats.

### `closure`: how cases are closed (#76)

```bash
uv run janasunani-closure-finding                 # over data/interim -> outputs/findings
uv run janasunani-closure-finding --print-sql     # the view definitions, for handover
```

Officers close on a graded ladder of six standard templates: bare
disposed/resolved, *with appropriate action*, and *& beneficiary benefited*. The
finding is the share closing on the bare rung while a more specific one sat
right beside it in the dropdown.

**Reading the output.** The share moves by half depending on the denominator, so
`closure_finding_summary` reports both and neither is optional:

- share of **templated closures**: complaints closed on one of the six
- share of **all resolved complaints**, including the third closing on neither

Quote the templated-closure base in the same breath as the headline. The
Markdown renderer cannot produce one without the other.

⚠️ **Descriptive, never a failure rate.** Sometimes no action is correct: an
information request answered, an ineligible claim properly refused, a matter
already settled elsewhere. A correct closure and a premature one are identical
in this record. Turning the figure into a claim needs 300-500 closures
adjudicated by hand, which is not August work. Report at state level as an
observation about the closure workflow, **never as an office league table**.

**Trajectory is a required control, not an optional cut.** A case going
created → forwarded → ATR → disposed had work done whatever the closing phrase
says. `closure_by_trajectory` crosses action-step count with elapsed days;
`closure_two_day_bare` is the sub-finding that names a specific set of cases
rather than the system as a whole.

**Template drift is the failure mode.** An unmatched ladder string does not
error. It moves complaints into `off_ladder` and quietly shrinks the
denominator. Below 50% coverage (expect roughly two thirds) the CLI **writes
nothing and exits 1**, rather than warning beside the artifacts: a batch caller
keeping stdout and dropping stderr would otherwise publish exactly the number
the check says must not be quoted. `closure_off_ladder_templates` is still
written, because it is what tells you why.

**`diagnostics/` is engineer-facing and not part of the handover.**
`closure_off_ladder_templates` is the only output carrying remark text. A
1,000-use floor is dropdown scale rather than free text, but frequency is
evidence and not proof. A remark repeated ten thousand times could still carry
something somebody pasted into a form. It goes to its own directory so the
directory the finding is shared out of stays aggregates-only.

**Why it needs nothing else.** It reads `action_taken_remark` and structured
`complaints` columns. It never reads `complaints.grievance`, so it needs no
redaction pass, no dedup index, no gold set, no backlog slice and no GPU. A test
asserts that, both statically over the SQL and behaviourally over a lake that
carries the column.
