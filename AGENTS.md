# Agent Notes

`CLAUDE.md` is a symlink to this file. Edit `AGENTS.md`.

[.dpic/standards/agent-conventions.md](.dpic/standards/agent-conventions.md) is
authoritative for commits, branches, pull requests, subagents, and handling
review findings. It is synced from `dpic-org` by `dpic-sync-standards` — do not
edit it here; propose changes upstream and re-sync.
[CONTRIBUTING.md](CONTRIBUTING.md) is authoritative for required checks, the
data and PII policy, gold-metric gates, pull request size, and the review
protocol — self-review the diff, request a Codex review with `@codex review`,
then verify each finding before acting on it. Neither file is restated here —
read them. This file covers what they do not: where the project stands, how it
is laid out, and which commands run it.

**Start here: [docs/ROADMAP.md](docs/ROADMAP.md)** — current state, phase
status, and sequencing (the source of truth, incl. the project snapshot for a
fresh reviewer; keep it in sync as you land work).
[docs/DELIVERY.md](docs/DELIVERY.md) is the dated commitment for the **14 August
2026** demo — check it before assuming a phase is in scope for the demo. The hard safety rules
(data-loss and citizen-PII hazards are real; read them before touching anything
operational) live in this file below and in [docs/DEPLOY.md](docs/DEPLOY.md).
Then [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system overview.

## Data Access Restriction
- Treat everything under `data/` as proprietary and sensitive (real citizen
  grievances and PII).
- Do not list, read, search, preview, summarize, inspect metadata for, or
  otherwise access files or directories under `data/`.
- Do not run commands that recurse into `data/`, including broad repository
  searches or listings, unless they explicitly exclude `data/`.
- Only access a specific path under `data/` when the user gives explicit
  permission for that specific task and path.

## Project Shape
- One Python package (`janasunani/`) managed by `uv`; `pyproject.toml` +
  `uv.lock` are the dependency source of truth. Python pinned via
  `.python-version`.
- Heavy ML deps live in per-stage extras (`pipeline-core`, standalone `pii`,
  `ocr-deepseek`, `categorizer`; light `serving`) with incompatible pairs in
  `[tool.uv].conflicts`. Run per-env: `uv run --extra <name> …`; redaction and
  PII evaluation use `--extra pii` separately from `pipeline-core`.
- Console scripts (`janasunani-*`) are listed in `pyproject.toml`; the root
  `main.py` is a legacy stub, not the entrypoint.

### Where a new module goes
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the full package table and is
the place to look first. Keep it current: a new package that is not in that
table sends the next person to the wrong place.

The distinctions that have actually caused drift, in the order they bite:

- **Gate or report.** Does the code fail a run when the number is bad? A gate
  lives with the thing it gates (`pipeline/pii_eval.py`). A harness that
  measures and prints goes in `evaluation/`.
- **Batch or single-item.** The same model called over a corpus belongs in
  `pipeline/`; called once for a live request, in `inference/`.
- **Lake or OLTP.** Read-only analysis over Parquet goes in `analytics/`.
  Anything writing to the OLTP database is `pipeline/` or `db/`.
- **Leaving DPIC control.** Any call carrying citizen data to a third party
  goes in `egress/` and nowhere else. This one is enforced by CI, not
  convention.

Prefer an existing package. A new top-level package is a real decision: say so
in the PR body, and add it to the ARCHITECTURE table in the same PR.

When two branches in one sprint could both plausibly add the same module,
check what is already on the other branch before choosing. Three of the Sprint
3 collisions (duplicate branches, an empty PR, two packages disagreeing about
where scorecards live) were parallel work that never looked sideways.

## Commands
- Install/sync: `uv sync` (add `--extra …` as needed).
- Lint: `uv run ruff check .`
- Tests: `uv run --extra serving --extra pipeline-core pytest`
  — every change ships with real-code-path tests, green before "done".
  **Never run pytest against the production Postgres** (fixtures drop
  tables); see `tests/README.md`.
- The full pre-PR check list, the Presidio-gated PII suite, and the gold-metric
  gates are in [CONTRIBUTING.md](CONTRIBUTING.md).
- Component run commands: root `README.md` §"Running the components".

## Dependencies And Data
- `dpic` is pulled from the private Git source configured in
  `pyproject.toml`; dependency resolution needs GitHub SSH access.
- DVC remote settings live in `.dvc/config`; `dvc pull`/`dvc push` require
  AWS/S3 credentials. Models load from our DVC mirrors under `models/` only.
- Box synchronization settings live in `Makefile`; rclone operations require
  a configured Box remote.
