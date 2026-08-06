# janasunani.analytics — marts and findings over the lake

The analytical layer. It reads the Parquet lake (`janasunani.olap.lake`), never
OLTP, and it produces two different kinds of thing.

## Marts (`sql/`)

Governed derived tables, written as portable SQL views over the lake's base
tables. **The SQL is the deliverable**, not an implementation detail — several
of these are handed to the department to run for themselves — so
`marts.py` only locates and installs it. Nothing rewrites it and no mart's logic
is duplicated in Python.

Portability is on the SQL author: keep to constructs DuckDB (our lake) and
PostgreSQL (their database, and our OLTP store) both accept. Verified the only
way that means anything, by running the mart over a fixture lake in `tests/`.

| Mart | What it defines |
|---|---|
| `closure` | The disposal ladder, each resolved complaint's rung and trajectory, and the closure finding's aggregate views (#76). |

## Findings (`findings/`)

The presentation built on a mart: aggregate tables, the reconciliation, and the
Markdown fragment that goes in front of an audience with the caveats that must
travel with each number. One module per finding, each with a CLI writing to
`outputs/findings/`.

Two house rules, enforced by tests rather than by convention:

- **Aggregates only.** No finding prints a row of citizen writing. Where one
  emits strings at all they are high-frequency dropdown templates, bounded by a
  minimum-use threshold.
- **Insight or capability, said out loud.** An *insight* is something the record
  already contained and nobody had queried; a *capability* is something no
  existing dashboard could produce. Presenting the first as the second is the
  failure mode (ROADMAP §5.3).

### `closure` — how cases are closed (#76)

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

- share of **templated closures** — complaints closed on one of the six
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
error — it moves complaints into `off_ladder` and quietly shrinks the
denominator. `check_ladder_coverage` warns below 50% (expect roughly two
thirds), and `closure_off_ladder_templates` lists what stopped matching. Run
that first against any corpus this has not been run against before.

**Why it needs nothing else.** It reads `action_taken_remark` and structured
`complaints` columns. It never reads `complaints.grievance`, so it needs no
redaction pass, no dedup index, no gold set, no backlog slice and no GPU. A test
asserts that, both statically over the SQL and behaviourally over a lake that
carries the column.
