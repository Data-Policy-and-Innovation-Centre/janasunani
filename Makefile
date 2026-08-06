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
# Deliberately NOT `-include .env`: that makes GNU Make parse the file as
# Makefile syntax, not key=value (#60). `$` starts a variable reference (a
# password's `pa$word` silently loses `word`) and `#` starts a comment (drops
# the rest of the line); a stray `$(` with no matching `)` is worse — it is a
# hard parse error that aborts `make` for every target, not just this one, so
# a single unlucky character in a generated password can brick the Makefile
# entirely. Only OLTP_DB_URL needs the safe path here: it is the one dotenv
# value this Makefile actually consumes (grep confirms no other `?=`
# variable's name collides with a .env.example key).
#
# Two earlier attempts at this extraction (`grep`/`sed` for the key, then for
# one layer of matching quotes) each closed one character class and left
# another: `$`/`#`, then an embedded `'`, then a valid dotenv inline comment
# (`KEY=value # note`, which `sed`'s quote-stripping never accounted for --
# `Settings` strips it via python-dotenv, so the Make wrapper silently kept it
# and passed a corrupted DSN). Patching one more character each round is how
# this stayed a live bug through three reviews. This now shells out to
# `python-dotenv` itself -- the exact parser `Settings` uses
# (janasunani/config.py) -- instead of re-deriving its quoting/comment rules
# by hand, so the two can no longer drift: whatever `.env` value `Settings`
# resolves is what Make resolves too. `uv run` only runs when `.env` exists
# (the `ifneq` below), so a checkout with no `.env` (CI, a fresh clone) pays
# nothing.
#
# `:=` (not `?=`), so a value here overrides both this demo default and a
# shell-exported OLTP_DB_URL, matching the precedence documented above. A
# `make OLTP_DB_URL=...` command-line value still wins regardless: Make locks
# in command-line variables before reading any of the makefile, and no plain
# assignment (only `override`, unused here) can replace them -- verified with
# `make OLTP_DB_URL=... db` against a conflicting `.env` (see PR description).
#
# Two things fixed here (#103, a regression this extraction itself
# introduced): first, `uv` is looked up with PATH widened by $(USER_BIN)
# directly on *this* command, not via the `export PATH` line below -- that
# only reaches recipe subprocesses, not a $(shell ...) call evaluated here
# at Make-parse time (confirmed directly: an `export`ed PATH change does not
# reach an immediately-following $(shell ...) in GNU Make). Without this, a
# `uv` installed only under $(USER_BIN) -- the repo's own documented install
# location -- would not resolve here even though every recipe below finds it
# fine. Second, the python one-liner always prints a `DOTENV_OK:` prefix on
# success, even with an empty value, so a genuine parse failure (uv still
# not found, python-dotenv missing, any other error) is distinguishable from
# ".env exists but doesn't set OLTP_DB_URL" by that prefix's *absence* --
# never by empty output alone. A failure here is a hard `$(error ...)`, not
# a silent fallback: silently keeping the throwaway demo default while an
# operator's .env names a real database is exactly the wrong outcome `make
# OLTP_DB_URL=... db`'s guard exists to prevent, reached by a different
# route.
_DOTENV_STDERR := /tmp/.janasunani-makefile-dotenv-stderr-$(shell whoami 2>/dev/null)
ifneq (,$(wildcard .env))
_DOTENV_RAW_OLTP_DB_URL := $(shell PATH="$(USER_BIN):$$PATH" uv run python -c "from dotenv import dotenv_values; import sys; v = dotenv_values('.env').get('OLTP_DB_URL'); sys.stdout.write('DOTENV_OK:' + (v if v is not None else ''))" 2>$(_DOTENV_STDERR))
ifeq ($(findstring DOTENV_OK:,$(_DOTENV_RAW_OLTP_DB_URL)),)
$(error .env exists but OLTP_DB_URL could not be parsed from it (#103) -- refusing to silently fall back to the throwaway demo default. 'uv run python' stderr: $(shell cat $(_DOTENV_STDERR) 2>/dev/null))
endif
_DOTENV_OLTP_DB_URL := $(subst DOTENV_OK:,,$(_DOTENV_RAW_OLTP_DB_URL))
ifneq (,$(_DOTENV_OLTP_DB_URL))
OLTP_DB_URL := $(_DOTENV_OLTP_DB_URL)
endif
endif
# A fourth instance of the same bug class, in the one place the .env fix
# above does not reach: OLTP_DB_URL from `make OLTP_DB_URL=...` (command
# line) or a shell-exported OLTP_DB_URL is stored by Make as a *recursively*
# expanded value -- the raw text, re-scanned for `$`/`$(...)` on every
# reference, not just once. Referencing plain $(OLTP_DB_URL) below (even
# inside sh_quote's $(subst ...)) re-triggers that scan, so `pa$word` loses
# `word` the same way an un-fixed `.env` value once did -- this is exactly
# #60's bug, just arriving through the command-line/environment tier instead
# of the .env tier, and it is precisely the tier `make OLTP_DB_URL=... db`
# depends on to force a database for one run (the guard #60's own precedence
# note above exists to protect).
#
# $(value OLTP_DB_URL) reads the raw stored text without expanding it, which
# is exactly right for a command-line/environment value (a literal string,
# never intended as a nested Make macro) but wrong for anything else: the
# untouched default chain's raw text is the literal, unexpanded macro
# reference `$(DEMO_OLTP_URL)`, and $(value ...) on that returns exactly
# that string -- not the demo URL -- which would hand the shell a bare
# `$(DEMO_OLTP_URL)` (itself a *shell* command-substitution token). So
# $(value ...) is used only when $(origin OLTP_DB_URL) says the value came
# from the command line or the environment; every other origin (the plain
# `?=` default, or our own `:=` reassignment from .env above, already a
# fully-resolved simply-expanded string) goes through normal `$(OLTP_DB_URL)`
# expansion instead. Command-line values lock their origin regardless of
# this file's later `:=` attempt (Make ignores plain reassignment of a
# command-line variable), so this still resolves to "command line" even
# after the .env block above runs.
ifeq ($(origin OLTP_DB_URL),command line)
OLTP_DB_URL_RAW := $(value OLTP_DB_URL)
else ifeq ($(origin OLTP_DB_URL),environment)
OLTP_DB_URL_RAW := $(value OLTP_DB_URL)
else ifeq ($(origin OLTP_DB_URL),environment override)
OLTP_DB_URL_RAW := $(value OLTP_DB_URL)
else
OLTP_DB_URL_RAW := $(OLTP_DB_URL)
endif
# Embeds an arbitrary value (e.g. $(OLTP_DB_URL_RAW)) as a single shell word
# safe from further expansion: single-quoted, with each embedded `'` replaced
# by `'\''` (close the quote, an escaped literal quote outside it, reopen the
# quote) -- the standard POSIX idiom for putting a quote inside a quoted
# string. Plain single-quoting handles `$`/`#` (#60) but a DSN containing a
# literal `'` would otherwise still break the recipe's shell string; this
# closes that gap. Use as `$(call sh_quote,$(OLTP_DB_URL_RAW))` -- already
# quoted, so call sites do not wrap it in quotes themselves, and always via
# OLTP_DB_URL_RAW, never the plain $(OLTP_DB_URL) reference, per the origin
# note above.
sh_quote = '$(subst ','\'',$(1))'
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
	@echo "  make infra           Read-only health pass over the cloud infra"
	@echo ""
	@echo "  Live demo (real-inference API — see docs/DEMO.md):"
	@echo "  make models          DVC-pull ONLY the demo model artifacts"
	@echo "  make preflight       Fast readiness check (models, OCR binaries, mappings/lake/OLTP)"
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

.PHONY: docs docs-clean infra status

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


# Read-only pass over the cloud infra (EC2 boxes, SSH exposure, disk,
# containers, backups, demo health). Nothing here mutates; see
# scripts/infra_status.py.
#
# CPU_BOX_SSH is the EC2 instance, NOT `BOX_REMOTE` above — that one is the
# rclone Box.com remote. Two unrelated things both called "box"; do not wire
# one to the other.
#
#   make infra
#   make infra SITE=52-66-116-80.nip.io SG_ID=sg-0abc123
#   make infra ARGS="--no-ssh"
CPU_BOX_SSH    ?= ubuntu@52.66.116.80

infra:
	@uv run python scripts/infra_status.py --host "$(CPU_BOX_SSH)" \
	  $(if $(SITE),--site "$(SITE)") $(if $(SG_ID),--sg-id "$(SG_ID)") $(ARGS)

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
# $(call sh_quote,...): double quotes would hand a `$` in the password to
# *this* shell to re-expand (the same class of bug as #60, one layer down,
# since Make's own textual substitution here does not re-parse the value it
# pastes in -- only the shell that receives it would); a bare single-quoted
# `'$(OLTP_DB_URL)'` fixes that but then breaks on an embedded `'` instead.
# sh_quote handles both.
preflight:
	@OLTP_DB_URL=$(call sh_quote,$(OLTP_DB_URL_RAW)) uv run --extra demo janasunani-demo-preflight

# Idempotent: create the throwaway Postgres only if missing, start it if stopped,
# always (re-)apply migrations (alembic upgrade head is a no-op when current).
# Safe to depend on from `api`/`up`. Guard: acts ONLY when OLTP_DB_URL is exactly
# the throwaway DEMO_OLTP_URL, so it never creates one database and migrates
# another, and never provisions/migrates an operator's own (local or off-box) DB.
db:
	@set -e; \
	if [ $(call sh_quote,$(OLTP_DB_URL_RAW)) != $(call sh_quote,$(DEMO_OLTP_URL)) ]; then \
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
	OLTP_DB_URL=$(call sh_quote,$(OLTP_DB_URL_RAW)) uv run alembic upgrade head; \
	echo "Demo DB ready."

# `@` so the OLTP DSN is not echoed. API_HOST=0.0.0.0 to serve off-box.
# OLTP_DB_URL quoted via sh_quote for the same reason as `preflight` above.
# Note: `preflight` now opens a real, timeout-bounded connection to
# OLTP_DB_URL when one is set (janasunani/inference/service.py's
# _oltp_check). On a fresh box that means it can WARN "unreachable" here,
# before `db` (next) has started the throwaway Postgres it is pointed at --
# non-fatal (advisory, not --strict) and self-resolving once `db` runs, kept
# in this order rather than `db preflight` so the cheap model/OCR-binary
# checks still fail fast ahead of provisioning a container.
api: preflight db
	@OLTP_DB_URL=$(call sh_quote,$(OLTP_DB_URL_RAW)) JANASUNANI_API_HOST="$(API_HOST)" \
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
# See `api`'s comment above re: preflight's OLTP connectivity probe possibly
# WARNing here on a fresh box, before `db` (next) has started the throwaway
# Postgres -- non-fatal and self-resolving.
up: preflight db
	@set -e; \
	echo "Starting live API (:$(API_PORT)) in the background..."; \
	OLTP_DB_URL=$(call sh_quote,$(OLTP_DB_URL_RAW)) JANASUNANI_API_HOST="$(API_HOST)" \
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
