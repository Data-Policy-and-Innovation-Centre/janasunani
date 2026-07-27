.DEFAULT_GOAL  := help
USER_BIN       := $(HOME)/.local/bin
BOX_REMOTE     ?= box
BOX_PROJECT_ROOT ?= 2. Projects/21. Governance/
RAW_LOCAL      ?= data/raw/
EXHIBITS_LOCAL ?= outputs/
INCOMING_REMOTE ?= $(BOX_REMOTE):'$(BOX_PROJECT_ROOT)/Data/Raw/'
EXHIBITS_REMOTE ?= $(BOX_REMOTE):'$(BOX_PROJECT_ROOT)/Analysis/Exhibits/'
# Live-demo stack (see docs/DEMO.md). Throwaway Postgres only — never the prod
# volume, never the :5433 pytest-fixture DB.
PG_CONTAINER   ?= janasunani-demo-oltp
# Host port for the throwaway demo Postgres. Deliberately NOT 5432 (the CPU-box
# production oltp binds 127.0.0.1:5432 in deploy/docker-compose.yml) nor 5433
# (the pytest-fixture DB, which DROPS TABLES) — keep the demo clear of both.
PG_PORT        ?= 5544
API_PORT       ?= 8000
API_HOST       ?= 127.0.0.1
FRONTEND_PORT  ?= 3000
# The one database `db` will provision/migrate a throwaway container for. `db`
# acts ONLY when OLTP_DB_URL equals this exact DSN, so it never creates one
# database and migrates another, and never touches an off-box URL.
DEMO_OLTP_URL   = postgresql+asyncpg://postgres:demo@127.0.0.1:$(PG_PORT)/janasunani
# The OLTP database Settings/preflight/api all read. Precedence, highest first:
#   `make OLTP_DB_URL=... <target>` (command line) > OLTP_DB_URL in .env >
#   a shell-exported OLTP_DB_URL > this demo default. Use the command-line form
#   to force a database for one run regardless of .env.
OLTP_DB_URL    ?= $(DEMO_OLTP_URL)
# Base URL the browser calls -- baked into the frontend bundle at build time.
# The `make` fast path is LOCAL: a box/remote deployment (open ports, Node,
# compose) is docs/DEPLOY.md's job, not `make up`. To view a local/box-run demo
# from another machine, SSH-tunnel the ports and keep this default:
#   ssh -L $(FRONTEND_PORT):127.0.0.1:$(FRONTEND_PORT) -L $(API_PORT):127.0.0.1:$(API_PORT) <box>
# API_URL/API_HOST remain overridable for advanced setups.
API_URL        ?= http://127.0.0.1:$(API_PORT)
-include .env
SHELL          := /bin/bash
.SHELLFLAGS    := -euo pipefail -c
export PATH    := $(USER_BIN):$(PATH)

help:
	@echo ""
	@echo "  make setup           First-time setup on a new machine"
	@echo "  make install-hooks   Enable repository Git hooks"
	@echo "  make pull            Get latest code, deps, and approved DVC data"
	@echo "  make ingest          Copy all original source files from Box"
	@echo "  make publish-raw     Copy all local raw files to Box"
	@echo "  make push            Version and share all tracked data via DVC"
	@echo "  make run             Run the analytics pipeline"
	@echo "  make exhibits        Regenerate all figures and tables"
	@echo "  make deliver         Copy exhibits to Box without deleting remote files"
	@echo "  make docs            Render docs/*.md to DPIC-branded Word files"
	@echo "  make box-paths       Show resolved local and Box paths"
	@echo "  make status          Show what has changed"
	@echo ""
	@echo "  Live demo (real-inference API — see docs/DEMO.md):"
	@echo "  make models          DVC-pull ONLY the demo model artifacts"
	@echo "  make preflight       Fast readiness check (models + OCR binaries)"
	@echo "  make db              Start throwaway Postgres + run migrations"
	@echo "  make api             Run the live real-inference API"
	@echo "  make frontend        Run the Next.js UI against the live API"
	@echo "  make up              Serve API + frontend together (Ctrl-C stops both)"
	@echo "  make down            Tear down the demo API + frontend + throwaway DB"
	@echo ""

setup:
	perl -pi -e 's/\r$$//' scripts/setup.sh
	BOX_REMOTE="$(BOX_REMOTE)" bash scripts/setup.sh
	$(MAKE) install-hooks

install-hooks:
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
	@echo "Git hooks enabled from .githooks/"

pull: _check_git_clean
	@echo "[1/3] Pulling latest code..."
	@if git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then \
	  git pull --ff-only; \
	else \
	  echo "No Git upstream configured; skipping code pull."; \
	fi
	@echo "[2/3] Syncing Python environment..."
	uv sync
	@echo "[3/3] Pulling approved data versions..."
	uv run dvc pull
	@echo "Done."

ingest:
	@echo "Copying all original source files from Box..."
	rclone copy $(INCOMING_REMOTE) $(RAW_LOCAL) --progress
	@echo "Ingested raw data. The original Box files were not modified."

publish-raw:
	@echo "Publishing all local raw files to Box..."
	rclone copy $(RAW_LOCAL) $(INCOMING_REMOTE) --progress
	@echo "Published raw data. Existing Box files with other names were not deleted."

push: _check_git_clean
	@echo "[1/3] Reproducing pipeline..."
	uv run dvc repro
	@echo "[2/3] Committing version pointers..."
	@for p in dvc.lock '*.dvc' '*.gitignore'; do \
	  git add -A -- "$$p" 2>/dev/null || true; \
	done
	git commit -m "data: update $$(date +%Y-%m-%d)" || echo "No new version pointers to commit."
	@echo "[3/3] Pushing to team remote..."
	uv run dvc push
	git push
	@echo "Done. Team can now pull the latest data."

run:
	uv run dvc repro
	@echo "Pipeline complete. Check outputs/."

exhibits:
	uv run dvc repro
	@echo "Pipeline complete. Check outputs/ for regenerated exhibits."

deliver:
	@echo "Delivering exhibits to Box..."
	rclone copy $(EXHIBITS_LOCAL) $(EXHIBITS_REMOTE) --progress
	@echo "Exhibits delivered. Existing Box files were not deleted."

.PHONY: docs docs-clean

# Word renders of the planning docs, for circulation outside the repo.
# Outputs are gitignored (docs/*.docx): the Markdown is the source of truth.
DOC_SOURCES ?= docs/DELIVERY.md docs/ROADMAP.md
DOC_TARGETS := $(DOC_SOURCES:.md=.docx)

docs: $(DOC_TARGETS)
	@echo "Rendered: $(DOC_TARGETS)"

docs/%.docx: docs/%.md scripts/md_to_docx.py
	uv run python scripts/md_to_docx.py $< $@

docs-clean:
	rm -f $(DOC_TARGETS)

box-paths:
	@echo "BOX_REMOTE=$(BOX_REMOTE)"
	@echo "BOX_PROJECT_ROOT=$(BOX_PROJECT_ROOT)"
	@echo "RAW_LOCAL=$(RAW_LOCAL)"
	@echo "EXHIBITS_LOCAL=$(EXHIBITS_LOCAL)"
	@echo "INCOMING_REMOTE=$(INCOMING_REMOTE)"
	@echo "EXHIBITS_REMOTE=$(EXHIBITS_REMOTE)"

status:
	@echo "=== Git ==="
	@git status --short
	@echo ""
	@echo "=== DVC (local) ==="
	@uv run dvc status
	@echo ""
	@echo "=== DVC (remote) ==="
	@uv run dvc status --cloud

_check_git_clean:
	@git diff --quiet && git diff --cached --quiet || \
	  (echo "Uncommitted changes. Commit or stash first." && exit 1)

# --- Live demo (real-inference API). `models` and `frontend` share names with
# repo directories, so this whole group must be .PHONY or make treats them as
# up-to-date files and skips the recipe.
.PHONY: models preflight db api frontend up down

models:
	@echo "Pulling ONLY the demo model artifacts (not the PII-bearing data)..."
	uv run dvc pull models/categorizer.dvc models/page_type_classifier/vit_type_classifier.dvc

# `@` so the OLTP DSN (may carry a password) is not echoed into terminal logs.
preflight:
	@OLTP_DB_URL="$(OLTP_DB_URL)" uv run --extra demo janasunani-demo-preflight

# Idempotent: create the throwaway Postgres only if missing, start it if stopped,
# always (re-)apply migrations (alembic upgrade head is a no-op when current).
# Safe to depend on from `api`/`up`. Guard: acts ONLY when OLTP_DB_URL is exactly
# the throwaway DEMO_OLTP_URL, so it never creates one database and migrates
# another, and never provisions/migrates an operator's own (local or off-box) DB.
db:
	@set -e; \
	if [ "$(OLTP_DB_URL)" != "$(DEMO_OLTP_URL)" ]; then \
	  echo "OLTP_DB_URL is not the throwaway demo default; skipping provisioning — create and migrate that database yourself."; \
	  exit 0; \
	fi; \
	if [ -n "$$(docker ps -q -f name=^$(PG_CONTAINER)$$)" ]; then \
	  echo "Postgres '$(PG_CONTAINER)' already running."; \
	elif [ -n "$$(docker ps -aq -f name=^$(PG_CONTAINER)$$)" ]; then \
	  echo "Starting existing Postgres '$(PG_CONTAINER)'..."; docker start $(PG_CONTAINER) >/dev/null; \
	else \
	  echo "Creating throwaway Postgres '$(PG_CONTAINER)' on 127.0.0.1:$(PG_PORT)..."; \
	  docker run -d --name $(PG_CONTAINER) -e POSTGRES_PASSWORD=demo \
	    -e POSTGRES_DB=janasunani -p 127.0.0.1:$(PG_PORT):5432 \
	    -v $(PG_CONTAINER):/var/lib/postgresql/data postgres:17 >/dev/null; \
	fi; \
	echo "Waiting for Postgres to accept connections..."; \
	for i in $$(seq 1 30); do \
	  docker exec $(PG_CONTAINER) pg_isready -U postgres -d janasunani >/dev/null 2>&1 && break; \
	  sleep 1; \
	done; \
	OLTP_DB_URL="$(OLTP_DB_URL)" uv run alembic upgrade head; \
	echo "Demo DB ready."

# `@` so the OLTP DSN is not echoed. API_HOST=0.0.0.0 to serve off-box.
api: preflight db
	@OLTP_DB_URL="$(OLTP_DB_URL)" JANASUNANI_API_HOST="$(API_HOST)" \
	  JANASUNANI_API_PORT="$(API_PORT)" uv run --extra demo janasunani-api-live

frontend:
	cd frontend && npm install && \
	  PORT="$(FRONTEND_PORT)" NEXT_PUBLIC_API_URL="$(API_URL)" npm run dev

# One command for the demo: ensure the DB, start the API in the background, wait
# for it to report `processor: pipeline`, THEN start the frontend in the
# foreground. If the API dies or never turns healthy we abort instead of serving
# a UI against a dead backend. The trap reaps only the API process we launched
# (its PID + children) -- never a global `pkill` that could hit an unrelated
# live API on the same box. A single Ctrl-C on the frontend stops both.
up: preflight db
	@set -e; \
	echo "Starting live API (:$(API_PORT)) in the background..."; \
	OLTP_DB_URL="$(OLTP_DB_URL)" JANASUNANI_API_HOST="$(API_HOST)" \
	  JANASUNANI_API_PORT="$(API_PORT)" uv run --extra demo janasunani-api-live & \
	API_PID=$$!; \
	trap 'pkill -P $$API_PID 2>/dev/null; kill $$API_PID 2>/dev/null || true' EXIT INT TERM; \
	echo "Waiting for the API to report processor=pipeline (model warm-up)..."; \
	ready=; \
	for i in $$(seq 1 150); do \
	  if ! kill -0 $$API_PID 2>/dev/null; then echo "Live API exited during startup; aborting."; exit 1; fi; \
	  if curl -sf http://127.0.0.1:$(API_PORT)/health 2>/dev/null | grep -q '"processor":"pipeline"'; then ready=1; break; fi; \
	  sleep 2; \
	done; \
	[ -n "$$ready" ] || { echo "Live API did not become healthy in time; aborting."; exit 1; }; \
	echo "Live API healthy (:$(API_PORT)). Starting frontend (:$(FRONTEND_PORT))..."; \
	cd frontend && npm install && \
	  PORT="$(FRONTEND_PORT)" NEXT_PUBLIC_API_URL="$(API_URL)" npm run dev

# Tear down by PORT (overridable), not a global process-name match, so this
# never kills an unrelated live API/frontend on the same machine. Needs `lsof`
# to find the port owners; if it is absent we say so rather than silently
# leaving the API/frontend running.
down:
	@if command -v lsof >/dev/null 2>&1; then \
	  for p in $(API_PORT) $(FRONTEND_PORT); do \
	    PIDS=$$(lsof -ti tcp:$$p 2>/dev/null); [ -n "$$PIDS" ] && kill $$PIDS 2>/dev/null || true; \
	  done; \
	else \
	  echo "lsof not found — cannot stop API/frontend by port; stop them manually (e.g. 'docker compose down' on the box)."; \
	fi
	-docker rm -f $(PG_CONTAINER)
	-docker volume rm $(PG_CONTAINER)
	@echo "Demo stack torn down."
