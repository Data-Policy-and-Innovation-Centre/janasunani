# janasunani

Odisha's new unified AI powered grievance redressal portal Janasunani 2.0 

## Setup

Run:

```bash
make setup
```

Do not run `make setup` with `sudo`, including on WSL. Setup installs missing
user-level tools such as `uv`, `rclone`, and Linux/WSL AWS CLI v2 into
`~/.local/bin`, and adds that directory to `~/.bashrc` and `~/.profile` if it is
not already on your shell PATH. Git remains a system prerequisite.

On WSL, clone this repository inside the Linux filesystem, such as
`~/Documents/GitHub/janasunani`, not under `/mnt/c/...`. Creating the
Python virtual environment on the Windows-mounted filesystem can fail with
permission errors.

If your rclone Box remote should use a non-default name, run:

```bash
make setup BOX_REMOTE=<remote-name>
```

### Local Box Paths

You can create an optional local `.env` file in the repo root to configure
machine-specific Box/rclone paths:

```make
BOX_REMOTE=box
BOX_PROJECT_ROOT=2. Projects/21. Governance/
```

`.env` is ignored by Git and should be used only for local path or remote-name
settings, not credentials or data files. Use Make-style assignments, do not use
shell `export` lines, and do not wrap values with spaces in shell quotes.

Most users only need to set `BOX_REMOTE` and `BOX_PROJECT_ROOT`. The Makefile
derives the full remotes from those values:

```make
INCOMING_REMOTE=$(BOX_REMOTE):'$(BOX_PROJECT_ROOT)/Data/Raw/'
EXHIBITS_REMOTE=$(BOX_REMOTE):'$(BOX_PROJECT_ROOT)/Analysis/Exhibits/'
```

After editing `.env`, verify the resolved paths:

```bash
make box-paths
```

Command-line Make variables still override `.env`:

```bash
make ingest DATA=survey_dump.csv BOX_PROJECT_ROOT="/All Files/AI for Panchayats"
```

If the derived paths do not match your Box layout, override the full remotes in
`.env`:

```make
INCOMING_REMOTE=box:'/Shared/AI for Panchayats/Data/Raw/'
EXHIBITS_REMOTE=box:'/Shared/AI for Panchayats/Analysis/Exhibits/'
```

Import a stakeholder-provided original from the Box incoming folder, then
record an approved version through DVC:

```bash
make ingest DATA=survey_dump.csv
make push DATA=survey_dump.csv
```

Publish a local raw file, such as an API pull, to the Box incoming folder:

```bash
make publish-raw DATA=api_dump.csv
```

Restore approved project data from DVC:

```bash
make pull
```

Define project-specific processing stages in `dvc.yaml`, then run:

```bash
make run
```

Publish generated figures, tables, and reports to Box without deleting existing
remote files:

```bash
make deliver
```

## Data migration

Build the local complaint store `data/raw/grievance.db` (complaints + action
history) from the raw Janasunani MySQL dump. The loader restores the dump into a
MySQL server, then validates and copies the two ETL tables into the SQLite store
through one shared insert routine.

**Prerequisites**

- The dump file at `data/raw/Dump20250730.sql` (the `sociomatics_ticket`
  `mysqldump`).
- A reachable MySQL server to restore the dump into, and the `mysql` client on
  your `PATH`. The easy option is a throwaway MySQL 5.7 container:

  ```bash
  docker run -d --name mysql57 -e MYSQL_ROOT_PASSWORD=pass -p 3306:3306 mysql:5.7
  ```

- An admin MySQL URL **without** a database, exported for the DVC stage:

  ```bash
  export MYSQL_ADMIN_URL="mysql+pymysql://root:pass@127.0.0.1:3306/"
  ```

**Run it (DVC node)**

The migration is the `migrate` stage in `dvc.yaml`. Reproduce it with:

```bash
dvc repro migrate        # or: make run
```

This produces `data/raw/grievance.db` (a DVC-tracked output). Re-running is safe
and idempotent.

**Run it directly (without DVC)**

```bash
uv run janasunani-migrate-dump \
    --dump data/raw/Dump20250730.sql \
    --mysql-url "$MYSQL_ADMIN_URL"
```

Use `--skip-restore` to migrate from an already-loaded MySQL database, and
`--target-db-url` to write somewhere other than the default `grievance.db`.

**Incremental sync from a live server**

To sync new complaints from a running Janasunani MySQL instance (no dump),
set `MYSQL_URL` to a full URL *including* the database and run:

```bash
MYSQL_URL="mysql+pymysql://user:pass@host:3306/sociomatics_ticket" \
    uv run janasunani-migrate-mysql
```

## Box Paths

Collaborators may see the same shared Box folder under different path prefixes,
depending on which folder was shared with them and how their local rclone Box
remote resolves that share. The Makefile keeps the generated defaults, but all
Box endpoints can be overridden without editing the Makefile.

Print the resolved paths before ingesting or delivering files:

```bash
make box-paths
```

Override the shared project root for a single command:

```bash
make deliver BOX_PROJECT_ROOT="DPIC/janasunani"
```

To keep the override for future runs, add it to `.env`:

```make
BOX_PROJECT_ROOT=DPIC/janasunani
```

If only one endpoint differs, override the full endpoint instead:

```bash
make deliver EXHIBITS_REMOTE="box:'DPIC/janasunani/Analysis/Client Exhibits/'"
```

## Contributing

Keep pull requests small enough for a reviewer to understand in one sitting.
Separate unrelated changes into separate PRs, especially when data, analysis
logic, and report formatting change independently.

Before opening a PR, run:

```bash
uv run ruff check .
uv run pytest
```

Use Ruff for Python linting. Prefer small, explicit functions and project-local
helpers over one-off notebook-only logic when code will be reused.

If you work with notebooks, install the output-stripping hook once:

```bash
uv run nbstripout --install
```

Commit notebooks only after outputs have been stripped. Do not commit large
rendered notebook outputs, temporary exports, or local execution artifacts.

Data files under `data/` are proprietary by default. Do not commit raw,
interim, processed, or output data directly to Git. Use DVC for approved data
versions and `make deliver` for stakeholder-facing Box delivery.

Use the [pull request template](.github/PULL_REQUEST_TEMPLATE.md) when opening
a PR.

CI expects repository secret `DPIC_GITHUB_SSH_KEY` when private `dpic`
dependency resolution is required.
