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
PG_PORT        ?= 5432
API_PORT       ?= 8000
FRONTEND_PORT  ?= 3000
# Single source of truth for the OLTP database. Settings/preflight/api all read
# OLTP_DB_URL, so use that one name here too. `?=` means an operator-provided
# OLTP_DB_URL (shell env or .env) wins over this throwaway-demo default, keeping
# preflight, db, and api pointed at the SAME database.
OLTP_DB_URL    ?= postgresql+asyncpg://postgres:demo@127.0.0.1:$(PG_PORT)/janasunani
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

preflight:
	OLTP_DB_URL="$(OLTP_DB_URL)" uv run --extra demo janasunani-demo-preflight

# Idempotent: create the throwaway Postgres only if missing, start it if stopped,
# always (re-)apply migrations (alembic upgrade head is a no-op when current).
# Safe to depend on from `api`/`up`. Guard: only ever provisions/migrates the
# LOCAL container — if OLTP_DB_URL was overridden to an off-box database we skip
# entirely and never migrate it (protects prod; that DB is the operator's job).
db:
	@set -e; \
	case "$(OLTP_DB_URL)" in \
	  *@127.0.0.1:*|*@localhost:*) ;; \
	  *) echo "OLTP_DB_URL is not local ($(OLTP_DB_URL)); skipping throwaway-Postgres provisioning — manage that database yourself."; exit 0 ;; \
	esac; \
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

api: preflight db
	OLTP_DB_URL="$(OLTP_DB_URL)" JANASUNANI_API_PORT="$(API_PORT)" \
	  uv run --extra demo janasunani-api-live

frontend:
	cd frontend && npm install && NEXT_PUBLIC_API_URL="$(API_URL)" npm run dev

# One command for the demo: ensure the DB, then API in the background + frontend
# in the foreground. The trap fires on Ctrl-C (INT) or normal exit and reaps the
# background API, so a single Ctrl-C stops both. `preflight` fails fast if models
# or OCR binaries are missing; `db` brings up the throwaway Postgres first.
up: preflight db
	@echo "Serving API (background, :$(API_PORT)) + frontend (foreground, :$(FRONTEND_PORT)). Ctrl-C stops both."
	OLTP_DB_URL="$(OLTP_DB_URL)" JANASUNANI_API_PORT="$(API_PORT)" \
	  uv run --extra demo janasunani-api-live & \
	trap 'pkill -f janasunani-api-live 2>/dev/null || true' EXIT INT TERM; \
	cd frontend && npm install && NEXT_PUBLIC_API_URL="$(API_URL)" npm run dev

down:
	-pkill -f janasunani-api-live
	-PIDS=$$(lsof -ti tcp:$(FRONTEND_PORT) 2>/dev/null); [ -n "$$PIDS" ] && kill $$PIDS || true
	-docker rm -f $(PG_CONTAINER)
	-docker volume rm $(PG_CONTAINER)
	@echo "Demo stack torn down."
