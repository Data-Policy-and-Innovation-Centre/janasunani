# Agent Notes

**Start here: [docs/HANDOFF.md](docs/HANDOFF.md)** — current state, the work
queue, cloud state, and the hard safety rules (data-loss and citizen-PII
hazards are real; read them before touching anything operational). Then
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system and
[docs/ROADMAP.md](docs/ROADMAP.md) for sequencing (source of truth — keep it
in sync as you land work).

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
- Heavy ML deps live in **mutually conflicting extras** (`pipeline-core`,
  `ocr-deepseek`, `categorizer`; light `serving`) — see
  `[tool.uv].conflicts`. Run per-env: `uv run --extra <name> …`.
- Console scripts (`janasunani-*`) are listed in `pyproject.toml`; the root
  `main.py` is a legacy stub, not the entrypoint.

## Commands
- Install/sync: `uv sync` (add `--extra …` as needed).
- Lint: `uv run ruff check .`
- Tests: `uv run --extra serving --extra pipeline-core pytest`
  — every change ships with real-code-path tests, green before "done".
  **Never run pytest against the production Postgres** (fixtures drop
  tables); see `tests/README.md`.
- Component run commands: root `README.md` §"Running the components".

## Dependencies And Data
- `dpic` is pulled from the private Git source configured in
  `pyproject.toml`; dependency resolution needs GitHub SSH access.
- DVC remote settings live in `.dvc/config`; `dvc pull`/`dvc push` require
  AWS/S3 credentials. Models load from our DVC mirrors under `models/` only.
- Box synchronization settings live in `Makefile`; rclone operations require
  a configured Box remote.
