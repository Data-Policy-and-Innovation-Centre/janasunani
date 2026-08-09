# Contributing

Org-wide conventions for commits, branches, pull requests, subagents, and
handling review findings live in
[.dpic/standards/agent-conventions.md](.dpic/standards/agent-conventions.md).
That file is synced from `dpic-org` by `dpic-sync-standards` and is not edited
here. This file covers what it does not: the checks this repo requires, its
data and PII policy, and its gold-metric gates.

Changes should be reviewable and reproducible. A pull request should normally
change one pipeline stage, one serving endpoint, or one bounded slice of the
demo. Split refactors, generated outputs, and notebook changes from production
pipeline changes.

## Required checks

Before opening a pull request:

```bash
uv lock --check
uv sync --locked --all-groups --extra serving
uv run ruff check .
uv run --extra serving --extra pipeline-core pytest
uv run dvc dag
uv run dpic-sync-standards --check
find notebooks -name '*.ipynb' -print0 | xargs -0 -r uv run nbstripout --verify
```

`dpic-sync-standards --check` reports drift in the synced `.dpic/standards/`
and `.opencode/skills/` trees. If it fails, either the files were hand-edited
here, which is not allowed, or `dpic` has moved upstream. To take an upstream
change:

```bash
uv lock --upgrade-package dpic && uv sync && uv run dpic-sync-standards
```

CI runs Lint, Pipeline, Data Check, and the Codex Review Gate on every pull
request. It does not run the heavy ML extras, and it never executes a data
stage.

`main` is governed by the `no-push-only-pr` ruleset: no force-push, no
deletion, and changes land only through a pull request. Its required status
checks are `ruff`, `test-and-validate-pipeline`, and `no-raw-data-in-git`, and
**every review thread must be resolved** before the merge button unlocks. No
approving review is required, and nobody can bypass the ruleset.

The Codex Review Gate publishes a check run named `codex-review` that turns
that protocol into a pass/fail signal
([.github/workflows/codex-review-gate.yml](.github/workflows/codex-review-gate.yml)).
`codex-review` is **not yet in the ruleset's required checks**, so today it
reports without blocking a merge. Making it binding means adding that context
to the ruleset: add the check name, not the job that creates it.

Every feature ships with pytest tests that exercise the real code path. Green
before "done", not after review.

## PII and redaction

Changes that touch redaction, PII detection, or evaluation also need the
Presidio-gated suite, which runs in its own environment:

```bash
uv run --extra pii pytest \
  tests/test_pii_extra_contract.py \
  tests/test_pii_redaction.py \
  tests/test_redact_grievance.py \
  tests/test_rederive_pii_draft.py \
  tests/test_bootstrap_pii_gold.py
```

The heavy ML extras conflict pairwise (see `[tool.uv].conflicts`), so `pii`
work is run separately from `pipeline-core`, not in one combined environment.

## Data access

- Everything under `data/` is real citizen grievances and PII. The access
  restriction is in [AGENTS.md](AGENTS.md) and applies to every contributor,
  human or agent.
- No data or generated output belongs in a code review. The Data Check
  workflow rejects tracked files under `data/` and `outputs/`; the only
  exceptions are `.gitkeep`, `.dvc` pointers, and provenance sidecars, which
  carry metadata only.
- Never point pytest at the production Postgres. Fixtures drop tables. See
  `tests/README.md`.

## Gold metrics

DVC-tracked data does not leave the local machine, so the gold gates cannot run
in Actions. Run them locally and paste the observed numbers into the pull
request.

```bash
uv run --extra pii janasunani-evaluate-pii --gold <gold.jsonl>   # coverage >= 0.8056
uv run python scripts/verify_pii_gold.py
```

Any change that can affect detection, redaction, categorization, or a published
metric must be run against the gold artifacts before merge. The comparison must
pass, or the pull request must state the observed movement, the gold artifact
version, and why the movement is correct. Never relax a gate to reproduce a
number.

## Reproducibility

A file that affects a generated output has to be committed and listed as a
`dvc.yaml` dependency of the stage that consumes it. No stage output is
currently agent-generated, so the synced standards file is not yet a stage
dependency; add it when one becomes so.

Stage dependencies are listed file by file rather than by package directory
(see the note in `dvc.yaml`). Adding a stage to a command means adding its
files to `deps`.

## Pull request size

Prefer a sequence of small pull requests whose tests pass independently.
Changes above roughly 500 non-generated lines, or touching several phases at
once, should be split unless the pull request explains why the pieces cannot
land separately.

## Review

Every pull request gets a real review before merge, including one opened by an
agent. Reviewing means reading the diff against the code it changes, not
restating the description.

1. **Self-review the diff first.** Read `git diff` against the base branch end
   to end. Check the failure modes the change introduces, not just the happy
   path, and confirm the tests would fail without the fix.
2. **Request a Codex review** by commenting `@codex review` on the pull
   request. Do this for anything touching the pipeline, serving, deploy, PII,
   or data paths. Codex names the commit it read, and the gate binds its
   verdict to that sha, so **re-request after every push**, not only after a
   material change. A clean run leaves no review at all, only a :+1: reaction;
   the gate accepts that when the reaction is newer than the head commit.
3. **Handle the findings under the protocol** in
   [.dpic/standards/agent-conventions.md](.dpic/standards/agent-conventions.md).
   Findings are claims to verify, not instructions to execute. Reproduce before
   acting, put the command and its output in the reply whether you accept or
   reject, and treat the suggested remedy as a separate claim from the finding.
   Automated reviewers do fabricate findings that cite real-looking commits and
   paths, so a citation is not evidence.
4. **File what is valid but not blocking as a tech-debt issue** rather than
   fixing it in the branch. The delivery date in
   [docs/DELIVERY.md](docs/DELIVERY.md) decides what blocks. Say in the reply
   which issue the finding became.

Do not merge with review comments left unanswered. Every finding ends in one of
three states: fixed in the branch, filed as an issue, or rejected in a reply
that shows the evidence — and then **the thread is resolved**. Resolving is what
both the ruleset and the gate count, so a finding answered in a reply but left
open blocks the merge outright.

### When the review is not required

The gate decides this itself; it is not a judgement call at merge time.

- **Docs-only branches** are exempt automatically, up to 400 changed lines.
  Past that the branch goes through the normal review: a docs change large
  enough to restate a policy is worth reading.
- **Config-only branches** cannot be told apart from a diff, so they need the
  `codex-review-not-required` label. Say in the pull request why.

If Codex answers `@codex review` with a plain comment about usage limits, the
account is out of review credits and read nothing. The check stays red and
re-commenting cannot clear it; wait for the quota, or use the label and say so.
