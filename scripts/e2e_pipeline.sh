#!/usr/bin/env bash
# Tier 3 end-to-end pipeline rehearsal — thin wrapper for the full document
# pipeline over a non-PII fixture (not data/raw/).
#
# Runs:
#   janasunani-pipeline run on tests/fixtures/demo_letter.png
#   janasunani-export-pipeline (artifact DB -> OLTP)
#   janasunani-materialize (OLTP -> Parquet lake)
#   then the same curl smoke checks as scripts/demo_rehearsal.sh Phase B
#
# This is Tier 3 only — not CI. Requires models (make models), tesseract,
# poppler, and a reachable OLTP (throwaway Postgres or local SQLite).
#
# Usage:
#   bash scripts/e2e_pipeline.sh [fixture] [pipeline_db]
#   FIXTURE=tests/fixtures/demo_letter.png PIPELINE_DB=data/output/pipeline-e2e.sqlite bash scripts/e2e_pipeline.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Keep local output out of the tracked data/output/ location used by DVC;
# use a scratch DB so this never clobbers a real pipeline run.
FIXTURE="${1:-${FIXTURE:-tests/fixtures/demo_letter.png}}"
FIXTURE_DIR="$(dirname "$FIXTURE")"
PIPELINE_DB="${2:-${PIPELINE_DB:-data/output/pipeline-e2e.sqlite}}"
MODELS_DIR="${MODELS_DIR:-models}"
OLTP_URL="${OLTP_DB_URL:-}"
API_URL="${API_URL:-http://127.0.0.1:8000}"

log() { echo "[$1] $2"; }
info() { log "INFO" "$1"; }
ok() { log " OK " "$1"; }
fail() { log "FAIL" "$1"; exit 1; }

info "E2E pipeline rehearsal (Tier 3) — fixture=$FIXTURE db=$PIPELINE_DB"

# 1. Fixture check — synthetic, non-PII, committed under tests/fixtures/
#    (Unit A owns tests/fixtures/demo_letter.png). Fail with actionable
#    hint if missing rather than silently falling back to data/raw/.
if [ ! -f "$FIXTURE" ]; then
  # Fall back to any png/pdf under the fixture dir, if one exists
  ALT="$(find "$FIXTURE_DIR" -type f \( -name "*.png" -o -name "*.pdf" \) 2>/dev/null | head -n 1 || true)"
  if [ -n "$ALT" ]; then
    info "fixture $FIXTURE not found — using $ALT"
    FIXTURE="$ALT"
    FIXTURE_DIR="$(dirname "$FIXTURE")"
  else
    fail "fixture not found: $FIXTURE (and no alternative in $FIXTURE_DIR) — run the Unit A branch or create a synthetic fixture under tests/fixtures/ (never data/raw/)"
  fi
fi
ok "fixture: $FIXTURE"

# 2. Preflight hint — models must be present for the pipeline to run.
if [ ! -d "$MODELS_DIR/categorizer" ] && [ ! -d "$MODELS_DIR/page_type_classifier" ]; then
  log "WARN" "models not found under $MODELS_DIR — run 'make models' (or 'dvc pull models/...') before this"
fi

# 3. Run the pipeline (Tier 3, real weights when available).
#    Use --workers 1 for determinism in rehearsal.
mkdir -p "$(dirname "$PIPELINE_DB")"
info "janasunani-pipeline run --input $FIXTURE_DIR --db $PIPELINE_DB --models $MODELS_DIR"
# If fixture is a single file, pass --file-list via a temp list so the
# pipeline's directory walk does not ingest sibling fixtures unintentionally.
if [ -f "$FIXTURE" ]; then
  # When FIXTURE is a single file, run the pipeline pointing at its directory
  # with a file-list to scope exactly that file. Keeps behavior stable whether
  # tests/fixtures/ has 1 or 10 fixtures.
  FILE_LIST="$(mktemp)"
  trap 'rm -f "$FILE_LIST"' EXIT
  echo "$FIXTURE" > "$FILE_LIST"
  uv run --extra pipeline-core janasunani-pipeline run \
    --input "$FIXTURE_DIR" \
    --file-list "$FILE_LIST" \
    --db "$PIPELINE_DB" \
    --models "$MODELS_DIR" \
    --workers 1
  rm -f "$FILE_LIST"
  trap - EXIT
else
  uv run --extra pipeline-core janasunani-pipeline run \
    --input "$FIXTURE_DIR" \
    --db "$PIPELINE_DB" \
    --models "$MODELS_DIR" \
    --workers 1
fi
ok "pipeline run complete — $PIPELINE_DB"

# Guard: refuse to write rehearsal rows to non-throwaway OLTP without explicit confirmation
# OLTP_URL may be empty while .env still points at production Postgres (python-dotenv).
if [ "${JANASUNANI_ALLOW_PRODUCTION_E2E:-0}" != "1" ]; then
  EFFECTIVE_OLTP_URL="$(uv run python -c "from janasunani.config import Settings; print(Settings().OLTP_DB_URL)" 2>/dev/null || echo "$OLTP_URL")"
  DEMO_OLTP_URL="postgresql+asyncpg://postgres:demo@127.0.0.1:${PG_PORT:-5544}/janasunani"
  if echo "$EFFECTIVE_OLTP_URL" | grep -q "postgresql" && [ "$EFFECTIVE_OLTP_URL" != "$DEMO_OLTP_URL" ]; then
    fail "refusing to export to non-demo OLTP ($EFFECTIVE_OLTP_URL) — set JANASUNANI_ALLOW_PRODUCTION_E2E=1 to confirm or run 'make db' for throwaway (see AGENTS.md data-loss guard)"
  fi
fi

# 4. Export to OLTP
info "export to OLTP (OLTP_DB_URL=${OLTP_URL:-<default sqlite>})"
if [ -n "$OLTP_URL" ]; then
  uv run --extra pipeline-core janasunani-export-pipeline --db "$PIPELINE_DB" --oltp-url "$OLTP_URL"
else
  uv run --extra pipeline-core janasunani-export-pipeline --db "$PIPELINE_DB"
fi
ok "export complete"

# 5. Materialize to Parquet lake
info "materialize to Parquet lake"
if [ -n "$OLTP_URL" ]; then
  uv run janasunani-materialize --oltp-url "$OLTP_URL"
else
  uv run janasunani-materialize
fi
ok "materialize complete"

# 6. Curl smoke (rehearsal excerpt) — only if API is up
info "curl smoke — $API_URL/health + /history"
if curl -sf "$API_URL/health" >/dev/null 2>&1; then
  curl -sf "$API_URL/health" | python3 -m json.tool || curl -sf "$API_URL/health"
  echo ""
  ok "health reachable"
  if curl -sf "$API_URL/history?limit=5" >/dev/null 2>&1; then
    echo "history:"
    curl -sf "$API_URL/history?limit=5" | python3 -m json.tool | head -60 || true
    ok "history reachable"
  else
    log "WARN" "GET /history failed — lake may still be materializing"
  fi
else
  log "WARN" "API not running at $API_URL — skipping curl smoke (run 'make up' for live checks; pipeline artifacts are ready regardless)"
fi

info "e2e_pipeline done — pipeline DB: $PIPELINE_DB"
