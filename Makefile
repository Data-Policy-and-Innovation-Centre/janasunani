.DEFAULT_GOAL  := help
USER_BIN       := $(HOME)/.local/bin
BOX_REMOTE     ?= box
BOX_PROJECT_ROOT ?= 2. Projects/21. Governance/
RAW_LOCAL      ?= data/raw/
EXHIBITS_LOCAL ?= outputs/
INCOMING_REMOTE ?= $(BOX_REMOTE):'$(BOX_PROJECT_ROOT)/Data/Raw/'
EXHIBITS_REMOTE ?= $(BOX_REMOTE):'$(BOX_PROJECT_ROOT)/Analysis/Exhibits/'
-include .env
SHELL          := /bin/bash
.SHELLFLAGS    := -euo pipefail -c
export PATH    := $(USER_BIN):$(PATH)

help:
	@echo ""
	@echo "  make setup           First-time setup on a new machine"
	@echo "  make pull            Get latest code, deps, and approved DVC data"
	@echo "  make ingest DATA=f.csv Copy an original source file from Box"
	@echo "  make publish-raw DATA=f.csv Copy a local raw file to Box"
	@echo "  make push DATA=f.csv Version and share a locally-ingested file via DVC"
	@echo "  make run             Run the analytics pipeline"
	@echo "  make exhibits        Regenerate all figures and tables"
	@echo "  make deliver         Copy exhibits to Box without deleting remote files"
	@echo "  make box-paths       Show resolved local and Box paths"
	@echo "  make status          Show what has changed"
	@echo ""

setup:
	perl -pi -e 's/\r$$//' scripts/setup.sh
	BOX_REMOTE="$(BOX_REMOTE)" bash scripts/setup.sh

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

ingest: _require_data
	@echo "Copying original $(DATA) from Box..."
	rclone copyto $(INCOMING_REMOTE)$(DATA) $(RAW_LOCAL)$(DATA) --progress
	@echo "Ingested $(DATA). The original Box file was not modified."

publish-raw: _require_data
	@echo "Publishing latest $(DATA) to Box..."
	rclone copyto $(RAW_LOCAL)$(DATA) $(INCOMING_REMOTE)$(DATA) --progress
	@echo "Published $(DATA). Existing Box files with other names were not deleted."

push: _require_data _check_git_clean
	@echo "[1/3] Versioning $(DATA) and reproducing pipeline..."
	uv run dvc add $(RAW_LOCAL)$(DATA)
	uv run dvc repro
	@echo "[2/3] Committing version pointers..."
	git add $(RAW_LOCAL)$(DATA).dvc .gitignore
	@test -f dvc.lock && git add dvc.lock || true
	git commit -m "data: update $(DATA) $$(date +%Y-%m-%d)"
	@echo "[3/3] Pushing to team remote..."
	uv run dvc push
	git push
	@echo "Done. Team can now pull $(DATA)."

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

_require_data:
	@test -n "$(DATA)" || \
	  (echo "Usage: make <ingest|push> DATA=yourfile.csv" && exit 1)
