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
