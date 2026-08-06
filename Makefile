.DEFAULT_GOAL  := help
USER_BIN       := $(HOME)/.local/bin
BOX_REMOTE     ?= box
BOX_PROJECT_ROOT ?= 2. Projects/21. Governance/
RAW_LOCAL      ?= data/raw/
EXHIBITS_LOCAL ?= outputs/
# No embedded quotes here (#118): quoting a Box endpoint at *definition* time
# only protects the literal text of this default -- it does nothing for an
# operator's own INCOMING_REMOTE/EXHIBITS_REMOTE override (.env or command
# line), which reaches this variable already stripped of whatever dotenv/shell
# quoting it was written with (that is what dotenv quoting means: the outer
# quotes mark where the value ends, they are not part of it). A value that
# then contains spaces -- BOX_PROJECT_ROOT's own default does, and README.md
# documents overriding the full endpoint the same way -- reaches an unquoted
# recipe position and the shell word-splits it. The quoting has to happen
# where the value meets that shell word boundary -- the
# ingest/publish-raw/deliver recipes below -- via sh_quote, not here.
#
# References BOX_REMOTE_RAW/BOX_PROJECT_ROOT_RAW (defined further down, after
# the .env extraction) rather than the plain BOX_REMOTE/BOX_PROJECT_ROOT.
# Forward-referencing them is fine: `?=` makes this a recursively-expanded
# variable, and Make does not evaluate a recursive variable's text until
# something references it, by which point the whole file -- BOX_REMOTE_RAW
# included -- has been parsed. Using the _RAW forms instead of the plain ones
# matters even here, in the *unset*-default case: see the block that defines
# them below for why.
INCOMING_REMOTE ?= $(BOX_REMOTE_RAW):$(BOX_PROJECT_ROOT_RAW)/Data/Raw/
EXHIBITS_REMOTE ?= $(BOX_REMOTE_RAW):$(BOX_PROJECT_ROOT_RAW)/Analysis/Exhibits/
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
# entirely.
#
# Two earlier attempts at this extraction (`grep`/`sed` for the key, then for
# one layer of matching quotes) each closed one character class and left
# another: `$`/`#`, then an embedded `'`, then a valid dotenv inline comment
# (`KEY=value # note`, which `sed`'s quote-stripping never accounted for --
# `Settings` strips it via python-dotenv, so the Make wrapper silently kept it
# and passed a corrupted DSN). Patching one more character each round is how
# this stayed a live bug through three reviews. This shells out to
# `python-dotenv` itself -- the exact parser `Settings` uses
# (janasunani/config.py) -- instead of re-deriving its quoting/comment rules
# by hand, so the two can no longer drift: whatever `.env` value `Settings`
# resolves is what Make resolves too. `uv run` only runs when `.env` exists
# (the `ifneq` below), so a checkout with no `.env` (CI, a fresh clone) pays
# nothing.
#
# `:=` (not `?=`), so a value here overrides both the `?=` default and a
# shell-exported value, matching the OLTP_DB_URL precedence documented above
# (the same precedence applies to every key below). A `make KEY=...`
# command-line value still wins regardless: Make locks in command-line
# variables before reading any of the makefile, and no plain assignment
# (only `override`, unused here) can replace them -- verified for OLTP_DB_URL
# with `make OLTP_DB_URL=... db` against a conflicting `.env` (see PR
# description).
#
# Two things fixed for OLTP_DB_URL (#103, a regression this extraction
# itself introduced): first, `uv` is looked up with PATH widened by
# $(USER_BIN) directly on *this* command, not via the `export PATH` line
# below -- that only reaches recipe subprocesses, not a $(shell ...) call
# evaluated here at Make-parse time (confirmed directly: an `export`ed PATH
# change does not reach an immediately-following $(shell ...) in GNU Make).
# Without this, a `uv` installed only under $(USER_BIN) -- the repo's own
# documented install location -- would not resolve here even though every
# recipe below finds it fine. Second, dotenv_get below always records the
# python call's exit status to `_DOTENV_STATUS` (a file, read back via
# `dotenv_status` immediately after each call -- see #115 below for why
# this lives on a file rather than in the value's own text), so a genuine
# parse failure (uv still not found, python-dotenv missing, any other error)
# is distinguishable from "this key is not set in .env" by that status --
# never by the value's emptiness or content. A failure is a hard
# `$(error ...)`, not a silent fallback: for OLTP_DB_URL specifically,
# silently keeping the throwaway demo default while an operator's .env names
# a real database is exactly the wrong outcome `make OLTP_DB_URL=... db`'s
# guard exists to prevent, reached by a different route.
#
# OLTP_DB_URL was the only key extracted here until #104: the audit done
# when this extraction landed (17bad32) checked that no other `?=`
# variable's *name* collided with a .env.example key -- the wrong direction.
# The actual question is which variables README.md's "Box paths and data
# ops" section tells operators to persist in .env: BOX_REMOTE,
# BOX_PROJECT_ROOT, INCOMING_REMOTE and EXHIBITS_REMOTE, none of which
# collide with a .env.example key but all of which `-include .env` used to
# supply before #60. Dropping them silently turned `make ingest`/`make
# deliver` into reading from or publishing to the wrong Box folder with no
# indication the operator's own .env was ignored -- the same "silently wrong
# instead of loudly wrong" shape as #103, just for these four keys instead
# of the database URL. They are not secrets (unlike OLTP_DB_URL, never
# printed and read via `sh_quote`/OLTP_DB_URL_RAW below), so they get the
# same safe *parsing* but no additional quoting machinery beyond what
# `ingest`/`publish-raw`/`deliver`/`box-paths` already did before #60.
#
# Two more failures of this same "parse-time `uv run` call" shape, both
# #114: first, this whole extraction requires `uv` to already be
# resolvable, which is exactly what a fresh or broken checkout does not
# have -- and `scripts/setup.sh`'s install_uv() (invoked by the `setup`
# target below) is the documented mechanism that installs it. Requiring
# `uv` to run *before* `setup` can even start is a bootstrap cycle: the
# repair target can't run because the environment is unrepaired. So the
# `ifneq (,$(wildcard .env))` block immediately below only runs when
# `setup` is *not* among the requested goals (`$(MAKECMDGOALS)`, populated
# by Make from the command line before any makefile is read); `setup`
# itself never touches OLTP_DB_URL or the Box keys, so skipping the
# extraction for it costs nothing. `$(MAKE) install-hooks`, which `setup`
# shells out to once scripts/setup.sh has installed `uv` and run `uv
# sync`, is a *separate* `make` invocation with its own `MAKECMDGOALS`
# (`install-hooks`, not `setup`) and re-parses this file normally, by
# which point `uv` and a synced `.venv` both exist.
#
# Second: even with `uv` resolvable, a bare `uv run` re-syncs the
# environment against `pyproject.toml`/`uv.lock` on every invocation,
# including re-fetching metadata for direct-URL dependencies (e.g. the
# spaCy model wheel) even when nothing about them has changed -- so a
# transient network hiccup during *that* resync turns this parse-time call
# (which only ever needs `python-dotenv`, already sitting in an existing
# `.venv`) into a multi-minute hang, which the `ifeq`/$(error) guards below
# then escalate to a hard abort of *every* target, `help`/`status`/
# `box-paths` included. `--no-sync` makes the call use whatever `.venv`
# already has instead of resyncing it; `--offline` is defense in depth so
# any residual network attempt fails immediately rather than hanging.
# Verified with uv 0.7.18: together they resolve in well under a second
# against an already-synced `.venv`, and fail fast (not hang) against an
# unsynced one -- correctly, since "no python-dotenv available" is a
# genuine parse failure and must still hit the hard $(error) below, not a
# silent fallback.
#
# A fifth failure, #115: dotenv_get used to signal success by prefixing its
# stdout with a literal `DOTENV_OK:` marker, and each of the five call sites
# below stripped it back off with a global `$(subst DOTENV_OK:,,$(raw))`.
# `$(subst)` is unanchored, so a `.env` value that itself contained the
# literal substring `DOTENV_OK:` anywhere -- e.g.
# `BOX_PROJECT_ROOT=Archive/DOTENV_OK:2026` -- had *that* occurrence deleted
# too, not just the sentinel this file had prepended, silently corrupting
# the value `ingest`/`deliver` then used. Anchoring the strip instead
# (`$(patsubst DOTENV_OK:%,%,$(raw))` or `$(filter DOTENV_OK:%,$(raw))`)
# does not fix this: both are WHITESPACE-SPLITTING word operations -- they
# split text into space-separated words, transform each word independently,
# and rejoin with a single space -- so a value containing spaces (a
# documented, supported case; BOX_PROJECT_ROOT's own default is "2.
# Projects/21. Governance/") would come back with its internal spacing
# collapsed or rewritten, sentinel collision or not.
#
# The fix carries the status on a channel separate from the value's text
# instead of encoding it into that text at all: the compound shell command
# in dotenv_get now writes the python call's exit status to `_DOTENV_STATUS`
# (a small file, alongside the existing `_DOTENV_STDERR` capture) via a
# trailing `echo $?` redirected to *that file*, never to stdout -- so what
# `$(shell ...)` captures as dotenv_get's own expansion is exactly what
# python wrote to its stdout: the bare value, untouched, never a prefix this
# file has to strip back out. `dotenv_status` reads the status file back
# with `$(shell cat ...)` -- GNU Make 3.81, what this repo runs, predates
# both `$(.SHELLSTATUS)` (4.2) and `$(file ...)` (4.0), so shelling out to
# `cat` is the only portable way to get it into a Make variable, the same
# technique `_DOTENV_STDERR` already uses below to surface the error text.
# Each `dotenv_status` reference must come immediately after its
# corresponding `$(call dotenv_get,...)`, before anything else writes
# `_DOTENV_STATUS` -- exactly how each block below sequences its
# raw-value/status-check/assign lines. Verified against both an adversarial
# case this bug missed (a value containing spaces) and the one it hit (a
# value containing the literal `DOTENV_OK:` substring): both now round-trip
# unchanged (tests/test_makefile_dotenv.py).
define dotenv_get
$(shell PATH="$(USER_BIN):$$PATH" uv run --no-sync --offline python -c "from dotenv import dotenv_values; import sys; v = dotenv_values('.env').get('$(1)'); sys.stdout.write(v if v is not None else '')" 2>$(_DOTENV_STDERR); echo $$? >$(_DOTENV_STATUS))
endef
_DOTENV_STDERR := /tmp/.janasunani-makefile-dotenv-stderr-$(shell whoami 2>/dev/null)
_DOTENV_STATUS := /tmp/.janasunani-makefile-dotenv-status-$(shell whoami 2>/dev/null)
dotenv_status = $(shell cat $(_DOTENV_STATUS) 2>/dev/null)
ifneq (,$(wildcard .env))
ifeq ($(filter setup,$(MAKECMDGOALS)),)
_DOTENV_RAW_OLTP_DB_URL := $(call dotenv_get,OLTP_DB_URL)
ifneq (0,$(dotenv_status))
$(error .env exists but OLTP_DB_URL could not be parsed from it (#103) -- refusing to silently fall back to the throwaway demo default. 'uv run python' stderr: $(shell cat $(_DOTENV_STDERR) 2>/dev/null))
endif
ifneq (,$(_DOTENV_RAW_OLTP_DB_URL))
OLTP_DB_URL := $(_DOTENV_RAW_OLTP_DB_URL)
endif

_DOTENV_RAW_BOX_REMOTE := $(call dotenv_get,BOX_REMOTE)
ifneq (0,$(dotenv_status))
$(error .env exists but BOX_REMOTE could not be parsed from it (#104) -- refusing to silently fall back to the default. 'uv run python' stderr: $(shell cat $(_DOTENV_STDERR) 2>/dev/null))
endif
ifneq (,$(_DOTENV_RAW_BOX_REMOTE))
BOX_REMOTE := $(_DOTENV_RAW_BOX_REMOTE)
endif

_DOTENV_RAW_BOX_PROJECT_ROOT := $(call dotenv_get,BOX_PROJECT_ROOT)
ifneq (0,$(dotenv_status))
$(error .env exists but BOX_PROJECT_ROOT could not be parsed from it (#104) -- refusing to silently fall back to the default. 'uv run python' stderr: $(shell cat $(_DOTENV_STDERR) 2>/dev/null))
endif
ifneq (,$(_DOTENV_RAW_BOX_PROJECT_ROOT))
BOX_PROJECT_ROOT := $(_DOTENV_RAW_BOX_PROJECT_ROOT)
endif

_DOTENV_RAW_INCOMING_REMOTE := $(call dotenv_get,INCOMING_REMOTE)
ifneq (0,$(dotenv_status))
$(error .env exists but INCOMING_REMOTE could not be parsed from it (#104) -- refusing to silently fall back to the default. 'uv run python' stderr: $(shell cat $(_DOTENV_STDERR) 2>/dev/null))
endif
ifneq (,$(_DOTENV_RAW_INCOMING_REMOTE))
INCOMING_REMOTE := $(_DOTENV_RAW_INCOMING_REMOTE)
endif

_DOTENV_RAW_EXHIBITS_REMOTE := $(call dotenv_get,EXHIBITS_REMOTE)
ifneq (0,$(dotenv_status))
$(error .env exists but EXHIBITS_REMOTE could not be parsed from it (#104) -- refusing to silently fall back to the default. 'uv run python' stderr: $(shell cat $(_DOTENV_STDERR) 2>/dev/null))
endif
ifneq (,$(_DOTENV_RAW_EXHIBITS_REMOTE))
EXHIBITS_REMOTE := $(_DOTENV_RAW_EXHIBITS_REMOTE)
endif
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
# #118: BOX_REMOTE, BOX_PROJECT_ROOT, INCOMING_REMOTE and EXHIBITS_REMOTE need
# the same treatment as OLTP_DB_URL_RAW above, for the same reason -- a
# command-line (`make BOX_REMOTE=... ingest`) or shell-exported value is
# stored recursively expanded, so a later reference (including from inside
# sh_quote's $(subst ...), which #118 adds to ingest/publish-raw/deliver
# below) re-scans it for `$`/`$(...)`. None of these four are secrets/
# generated passwords like OLTP_DB_URL, so a literal `$` in one is a lower-
# probability trap, but it is the identical mechanism, and this file's own
# history (#60, #103, #114, #115) is that leaving one instance of this bug
# class unfixed while fixing the others is exactly how it keeps coming back
# -- so it gets the same fix here, not a note deferring it (verified in
# tests/test_makefile_dotenv.py).
#
# INCOMING_REMOTE and EXHIBITS_REMOTE derive from BOX_REMOTE/BOX_PROJECT_ROOT
# by default (see the `?=` lines at the top of this file), so their _RAW
# variants below are written in terms of BOX_REMOTE_RAW/BOX_PROJECT_ROOT_RAW,
# not the plain BOX_REMOTE/BOX_PROJECT_ROOT: in the "still at its default"
# case, $(INCOMING_REMOTE) re-expands whatever BOX_REMOTE currently means, and
# if BOX_REMOTE_RAW were skipped that reference would reintroduce this same
# bug one level down, inside a value this block exists to make safe. Both the
# still-default case and the .env-override case (INCOMING_REMOTE reassigned
# via `:=` above, already a simply-expanded/frozen string by this point) land
# in the same "file origin" branch below and are both safe to expand
# normally: the default's template only references the frozen _RAW leaves,
# and the .env value is already frozen text with nothing left to re-scan --
# see the dotenv_get comment block's #115 section for why a `:=` chain does
# not itself re-trigger this.
ifeq ($(origin BOX_REMOTE),command line)
BOX_REMOTE_RAW := $(value BOX_REMOTE)
else ifeq ($(origin BOX_REMOTE),environment)
BOX_REMOTE_RAW := $(value BOX_REMOTE)
else ifeq ($(origin BOX_REMOTE),environment override)
BOX_REMOTE_RAW := $(value BOX_REMOTE)
else
BOX_REMOTE_RAW := $(BOX_REMOTE)
endif

ifeq ($(origin BOX_PROJECT_ROOT),command line)
BOX_PROJECT_ROOT_RAW := $(value BOX_PROJECT_ROOT)
else ifeq ($(origin BOX_PROJECT_ROOT),environment)
BOX_PROJECT_ROOT_RAW := $(value BOX_PROJECT_ROOT)
else ifeq ($(origin BOX_PROJECT_ROOT),environment override)
BOX_PROJECT_ROOT_RAW := $(value BOX_PROJECT_ROOT)
else
BOX_PROJECT_ROOT_RAW := $(BOX_PROJECT_ROOT)
endif

ifeq ($(origin INCOMING_REMOTE),command line)
INCOMING_REMOTE_RAW := $(value INCOMING_REMOTE)
else ifeq ($(origin INCOMING_REMOTE),environment)
INCOMING_REMOTE_RAW := $(value INCOMING_REMOTE)
else ifeq ($(origin INCOMING_REMOTE),environment override)
INCOMING_REMOTE_RAW := $(value INCOMING_REMOTE)
else
INCOMING_REMOTE_RAW := $(INCOMING_REMOTE)
endif

ifeq ($(origin EXHIBITS_REMOTE),command line)
EXHIBITS_REMOTE_RAW := $(value EXHIBITS_REMOTE)
else ifeq ($(origin EXHIBITS_REMOTE),environment)
EXHIBITS_REMOTE_RAW := $(value EXHIBITS_REMOTE)
else ifeq ($(origin EXHIBITS_REMOTE),environment override)
EXHIBITS_REMOTE_RAW := $(value EXHIBITS_REMOTE)
else
EXHIBITS_REMOTE_RAW := $(EXHIBITS_REMOTE)
endif
# RAW_LOCAL/EXHIBITS_LOCAL (below, at the ingest/publish-raw/deliver recipes)
# get sh_quote at the recipe boundary too -- a local path can contain spaces
# just as easily as a Box one -- but not this _RAW/$(origin ...) treatment:
# neither is ever populated from .env (they are plain local-directory `?=`
# defaults, not part of the dotenv_get extraction above), so the only way
# either becomes a recursively-expanded value with attacker-shaped content is
# an operator directly typing `make RAW_LOCAL='...'`, a much narrower path
# than the four keys above (which a shared team .env or a copy-pasted Box URL
# routinely populates).
# Embeds an arbitrary value (e.g. $(OLTP_DB_URL_RAW)) as a single shell word
# safe from further expansion: single-quoted, with each embedded `'` replaced
# by `'\''` (close the quote, an escaped literal quote outside it, reopen the
# quote) -- the standard POSIX idiom for putting a quote inside a quoted
# string. Plain single-quoting handles `$`/`#` (#60) but a DSN containing a
# literal `'` would otherwise still break the recipe's shell string; this
# closes that gap. Use as `$(call sh_quote,$(OLTP_DB_URL_RAW))` -- already
# quoted, so call sites do not wrap it in quotes themselves, and always via
# OLTP_DB_URL_RAW, never the plain $(OLTP_DB_URL) reference, per the origin
# note above. #118 reuses this same macro for INCOMING_REMOTE_RAW/
# EXHIBITS_REMOTE_RAW/RAW_LOCAL/EXHIBITS_LOCAL at the ingest/publish-raw/
# deliver recipes below -- a Box endpoint or local path needs exactly the same
# single-shell-word guarantee a DSN does, just against spaces (BOX_PROJECT_
# ROOT's own default has one: "2. Projects/21. Governance/") rather than a
# generated password's character set.
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

# #118: both endpoints go through sh_quote so a space-bearing value -- the
# default BOX_PROJECT_ROOT ("2. Projects/21. Governance/") is the normal
# case here, not an exotic one -- reaches rclone as one argument instead of
# being word-split by the shell. INCOMING_REMOTE_RAW, never the plain
# $(INCOMING_REMOTE), per the origin note above its definition.
ingest:
	@echo "Copying all original source files from Box..."
	rclone copy $(call sh_quote,$(INCOMING_REMOTE_RAW)) $(call sh_quote,$(RAW_LOCAL)) --progress
	@echo "Ingested raw data. The original Box files were not modified."

publish-raw:
	@echo "Publishing all local raw files to Box..."
	rclone copy $(call sh_quote,$(RAW_LOCAL)) $(call sh_quote,$(INCOMING_REMOTE_RAW)) --progress
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

# #118: sh_quote for the same reason as ingest/publish-raw above.
deliver:
	@echo "Delivering exhibits to Box..."
	rclone copy $(call sh_quote,$(EXHIBITS_LOCAL)) $(call sh_quote,$(EXHIBITS_REMOTE_RAW)) --progress
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

# The _RAW forms so this echoes exactly what ingest/publish-raw/deliver
# actually use (see the #118 block above) rather than the plain variable,
# which -- for a command-line/environment-origin value containing `$` -- can
# differ from it (the plain reference re-scans; the _RAW one does not).
box-paths:
	@echo "BOX_REMOTE=$(BOX_REMOTE_RAW)"
	@echo "BOX_PROJECT_ROOT=$(BOX_PROJECT_ROOT_RAW)"
	@echo "RAW_LOCAL=$(RAW_LOCAL)"
	@echo "EXHIBITS_LOCAL=$(EXHIBITS_LOCAL)"
	@echo "INCOMING_REMOTE=$(INCOMING_REMOTE_RAW)"
	@echo "EXHIBITS_REMOTE=$(EXHIBITS_REMOTE_RAW)"


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
