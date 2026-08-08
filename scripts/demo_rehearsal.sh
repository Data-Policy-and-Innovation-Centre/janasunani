#!/usr/bin/env bash
# Laptop rehearsal gate for the 14 August 2026 demo — Phases A-D.
#
# Orchestrates the 13 Aug freeze gate on the laptop stack (see
# docs/plans/2026-08-08-demo-integration-rehearsal.md Part 2). Exit non-zero
# on any failure. Warn vs fail for artifact presence is configurable via
# REHEARSAL_STRICT.
#
# Usage:
#   bash scripts/demo_rehearsal.sh [--strict] [--skip-static] [--skip-stack] [--skip-artifacts] [--skip-models]
#   REHEARSAL_STRICT=1 bash scripts/demo_rehearsal.sh
#   API_URL=http://127.0.0.1:8000 FRONTEND_URL=http://127.0.0.1:3000 bash scripts/demo_rehearsal.sh
#
# Phases:
#   A — static (no running stack): ruff + pytest contract + preflight
#   B — stack smoke: health, submission, round-trip, supervisor, history, frontend
#   C — artifact presence: crosswalk, findings, aggregates, sarvam, benchmark
#   D — optional real-model smoke: JANASUNANI_RUN_MODEL_SMOKE=1 pytest
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API_URL="${API_URL:-http://127.0.0.1:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3000}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-300}"
HEALTH_POLL_S="${HEALTH_POLL_S:-2}"
REHEARSAL_STRICT="${REHEARSAL_STRICT:-0}"
REHEARSAL_SKIP_STATIC="${REHEARSAL_SKIP_STATIC:-0}"
REHEARSAL_SKIP_STACK="${REHEARSAL_SKIP_STACK:-0}"
REHEARSAL_SKIP_ARTIFACTS="${REHEARSAL_SKIP_ARTIFACTS:-0}"
REHEARSAL_SKIP_MODELS="${REHEARSAL_SKIP_MODELS:-0}"

# CLI flags override env
for arg in "$@"; do
  case "$arg" in
    --strict) REHEARSAL_STRICT=1 ;;
    --skip-static) REHEARSAL_SKIP_STATIC=1 ;;
    --skip-stack) REHEARSAL_SKIP_STACK=1 ;;
    --skip-artifacts) REHEARSAL_SKIP_ARTIFACTS=1 ;;
    --skip-models) REHEARSAL_SKIP_MODELS=1 ;;
    --help|-h)
      echo "Usage: $0 [--strict] [--skip-static] [--skip-stack] [--skip-artifacts] [--skip-models]"
      echo "  Env: API_URL, FRONTEND_URL, REHEARSAL_STRICT, HEALTH_TIMEOUT_S, HEALTH_POLL_S"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

# Counters
FAILURES=0
WARNINGS=0

log() { echo "[$1] $2"; }
info() { log "INFO" "$1"; }
ok() { log " OK " "$1"; }
warn() { log "WARN" "$1"; WARNINGS=$((WARNINGS+1)); }
fail() { log "FAIL" "$1"; FAILURES=$((FAILURES+1)); }

# Artifact helper: path, label, required (0=warn by default, 1=fail even without --strict)
check_artifact() {
  local path="$1"
  local label="$2"
  local required="${3:-0}"
  if [ -e "$path" ]; then
    ok "$label: $path"
    return 0
  fi
  local msg="$label missing: $path"
  if [ "$required" = "1" ] || [ "$REHEARSAL_STRICT" = "1" ]; then
    fail "$msg (strict)"
    return 1
  else
    warn "$msg"
    return 0
  fi
}

# ---------------------------------------------------------------------------
# Phase A — static (no running stack)
# ---------------------------------------------------------------------------
phase_a_static() {
  if [ "$REHEARSAL_SKIP_STATIC" = "1" ]; then
    info "Phase A — static: skipped (REHEARSAL_SKIP_STATIC=1)"
    return 0
  fi
  info "Phase A — static checks (no running stack)"

  # 1. ruff
  info "  ruff check ."
  if ! uv run ruff check .; then
    fail "ruff check failed"
  else
    ok "ruff check"
  fi

  # 2. contract pytest — only on files that exist, so a half-landed wave
  #    does not hard-fail the rehearsal on a missing module. Missing files
  #    are warned, not failed, unless strict.
  info "  pytest contract (demo_contract / pipeline / routing / supervisor / triage)"
  PYTEST_FILES=()
  for f in \
    tests/test_demo_integration.py \
    tests/test_pipeline_e2e.py \
    tests/test_routing_integration.py \
    tests/test_supervisor_intelligence.py \
    tests/test_serving_triage_contract.py; do
    if [ -f "$f" ]; then
      PYTEST_FILES+=("$f")
    else
      warn "pytest target missing, skipping: $f"
    fi
  done
  if [ "${#PYTEST_FILES[@]}" -gt 0 ]; then
    if ! uv run --extra serving --extra pipeline-core pytest "${PYTEST_FILES[@]}" -m "not demo_live" -q; then
      fail "pytest contract failed"
    else
      ok "pytest contract"
    fi
  else
    warn "no contract test files found — skipping pytest"
  fi

  # 3. preflight (fast, weight-free)
  info "  janasunani-demo-preflight"
  PREFLIGHT_ARGS=()
  if [ "$REHEARSAL_STRICT" = "1" ]; then
    PREFLIGHT_ARGS+=(--strict)
  fi
  if [ "${#PREFLIGHT_ARGS[@]}" -gt 0 ]; then
    PREFLIGHT_CMD=(uv run --extra demo janasunani-demo-preflight "${PREFLIGHT_ARGS[@]}")
  else
    PREFLIGHT_CMD=(uv run --extra demo janasunani-demo-preflight)
  fi
  if ! "${PREFLIGHT_CMD[@]}"; then
    # Preflight exits 1 on FAIL; advisory WARN still exits 0. In strict mode
    # a WARN becomes FAIL, so failure here is real.
    fail "janasunani-demo-preflight failed"
  else
    ok "janasunani-demo-preflight"
  fi
}

# ---------------------------------------------------------------------------
# Phase B — stack smoke (requires running API)
# ---------------------------------------------------------------------------
phase_b_stack() {
  if [ "$REHEARSAL_SKIP_STACK" = "1" ]; then
    info "Phase B — stack smoke: skipped (REHEARSAL_SKIP_STACK=1)"
    return 0
  fi
  info "Phase B — stack smoke (API $API_URL, frontend $FRONTEND_URL)"

  # 1. Poll /health until processor:pipeline (5-min window same as Makefile up)
  info "  polling $API_URL/health for processor:pipeline (timeout ${HEALTH_TIMEOUT_S}s)"
  health_ok=""
  deadline=$(($(date +%s) + HEALTH_TIMEOUT_S))
  # Quick single probe to give a fast hint if nothing is listening at all
  if ! curl -sf "$API_URL/health" >/dev/null 2>&1; then
    info "    health not yet reachable — waiting up to ${HEALTH_TIMEOUT_S}s (run 'make up' if stack is not started)"
  fi
  while [ "$(date +%s)" -lt "$deadline" ]; do
    body="$(curl -sf "$API_URL/health" 2>/dev/null || true)"
    if echo "$body" | grep -q '"processor":"pipeline"'; then
      health_ok=1
      break
    fi
    if echo "$body" | grep -q '"status":"ok"'; then
      # Got health but wrong processor — don't wait the full window, fail fast
      fail "health returned wrong processor (expected pipeline): $body — is janasunani-api-live running?"
      return 1
    fi
    sleep "$HEALTH_POLL_S"
  done
  if [ -z "$health_ok" ]; then
    fail "health never reported processor:pipeline within ${HEALTH_TIMEOUT_S}s — is 'make up' running? curl $API_URL/health"
    return 1
  fi
  ok "health: processor:pipeline"

  # Guard: refuse to persist synthetic grievance to non-throwaway stack without explicit confirmation
  # Health only checks processor, so a localhost SSH tunnel to prod would still pass.
  if [ "${REHEARSAL_ALLOW_PERSISTENT_API:-0}" != "1" ]; then
    EFFECTIVE_OLTP_URL="$(uv run python -c "from janasunani.config import Settings; print(Settings().OLTP_DB_URL)" 2>/dev/null || echo "")"
    DEMO_OLTP_URL="postgresql+asyncpg://postgres:demo@127.0.0.1:${PG_PORT:-5544}/janasunani"
    if echo "$EFFECTIVE_OLTP_URL" | grep -q "postgresql" && [ "$EFFECTIVE_OLTP_URL" != "$DEMO_OLTP_URL" ]; then
      fail "refusing to POST synthetic grievance — effective OLTP is non-demo postgres ($EFFECTIVE_OLTP_URL); set REHEARSAL_ALLOW_PERSISTENT_API=1 to confirm throwaway target or run 'make db' for local stack (see AGENTS.md)"
      return 1
    fi
    DEFAULT_API_URL="http://127.0.0.1:${API_PORT:-8000}"
    if [ "$API_URL" != "http://127.0.0.1:8000" ] && [ "$API_URL" != "$DEFAULT_API_URL" ]; then
      fail "refusing to POST synthetic grievance to non-default API_URL ($API_URL) — set REHEARSAL_ALLOW_PERSISTENT_API=1 to confirm throwaway target (localhost tunnel to prod would persist to live DB)"
      return 1
    fi
  fi

  # 2. Text submission — English grievance with district
  info "  POST /grievance (text + district) and validate response shape"
  TMPDIR_REHEARSAL="$(mktemp -d)"
  trap 'rm -rf "$TMPDIR_REHEARSAL"' EXIT
  SUBMIT_JSON="$TMPDIR_REHEARSAL/submit.json"
  GRIEVANCE_TEXT="The village water supply has been contaminated for weeks; the panchayat has not responded. Request urgent repair."

  HTTP_CODE="$(curl -s -w "%{http_code}" -o "$SUBMIT_JSON" -X POST "$API_URL/grievance" \
    -F "text=$GRIEVANCE_TEXT" \
    -F "district=Cuttack" 2>/dev/null || echo "000")"
  # curl writes code to stdout via -w; body is in file. Extract last 3 chars as code
  # Actually we used -o file -w code, so stdout is just the code
  if [ "$HTTP_CODE" != "201" ] && [ "$HTTP_CODE" != "200" ]; then
    fail "POST /grievance returned HTTP $HTTP_CODE (body: $(cat "$SUBMIT_JSON" 2>/dev/null | head -c 500))"
    return 1
  fi

  # Validate required fields via python (no jq dependency)
  if ! python3 - "$SUBMIT_JSON" <<'PY'
import json, sys
path = sys.argv[1]
data = json.loads(open(path).read())
# Required top-level shape after submit
checks = [
    ("redaction" in data and "redacted_text" in (data.get("redaction") or {}), "redaction.redacted_text"),
    ("classification" in data and "category" in (data.get("classification") or {}), "classification.category"),
    ("summary" in data, "summary"),
    ("routing" in data and "method" in (data.get("routing") or {}), "routing.method"),
    ("triage" in data and "spam" in (data.get("triage") or {}) and "spam_score" in (data["triage"].get("spam") or {}), "triage.spam.spam_score"),
]
missing = [label for ok, label in checks if not ok]
if missing:
    print(f"missing fields: {missing}", file=sys.stderr)
    print(json.dumps(data, indent=2)[:2000], file=sys.stderr)
    sys.exit(1)
# spam_score in [0,1]
try:
    score = float(data["triage"]["spam"]["spam_score"])
    assert 0 <= score <= 1, f"spam_score out of [0,1]: {score}"
except Exception as e:
    print(f"spam_score invalid: {e}", file=sys.stderr)
    sys.exit(1)
# routing.method must not be mock
method = data["routing"]["method"]
if method == "mock":
    print(f"routing.method is mock — expected learned|rules|fallback", file=sys.stderr)
    sys.exit(1)
print(f"submit ok: id={data.get('id')} routing.method={method} spam_score={score}")
PY
  then
    fail "POST /grievance response failed shape validation"
    return 1
  fi
  ok "POST /grievance shape"

  # Extract id for round-trip (GrievanceResult contract uses `id`, not `grievance_id`)
  GRIEVANCE_ID="$(python3 -c "import json; print(json.load(open('$SUBMIT_JSON'))['id'])" 2>/dev/null || true)"
  if [ -z "$GRIEVANCE_ID" ]; then
    fail "could not extract id from submission response"
    return 1
  fi

  # 3. Round-trip — GET /grievance/{id} matches POST
  info "  GET /grievance/$GRIEVANCE_ID round-trip"
  ROUNDTRIP_JSON="$TMPDIR_REHEARSAL/roundtrip.json"
  HTTP_CODE="$(curl -s -w "%{http_code}" -o "$ROUNDTRIP_JSON" "$API_URL/grievance/$GRIEVANCE_ID" 2>/dev/null || echo "000")"
  if [ "$HTTP_CODE" != "200" ]; then
    fail "GET /grievance/$GRIEVANCE_ID returned HTTP $HTTP_CODE"
    return 1
  fi
  if ! python3 - "$SUBMIT_JSON" "$ROUNDTRIP_JSON" <<'PY'
import json, sys
a = json.load(open(sys.argv[1]))
b = json.load(open(sys.argv[2]))
if a["id"] != b["id"]:
    print(f"id mismatch: {a['id']} vs {b['id']}", file=sys.stderr)
    sys.exit(1)
# At least id and ticket_no should match
if a.get("ticket_no") != b.get("ticket_no"):
    print(f"ticket_no mismatch", file=sys.stderr)
    sys.exit(1)
PY
  then
    fail "round-trip mismatch between POST and GET /grievance/$GRIEVANCE_ID"
    return 1
  fi
  ok "round-trip GET /grievance/$GRIEVANCE_ID"

  # 4. Supervisor — GET /supervisor returns 200; log panel availability
  info "  GET /supervisor"
  SUPERVISOR_JSON="$TMPDIR_REHEARSAL/supervisor.json"
  HTTP_CODE="$(curl -s -w "%{http_code}" -o "$SUPERVISOR_JSON" "$API_URL/supervisor" 2>/dev/null || echo "000")"
  if [ "$HTTP_CODE" != "200" ]; then
    fail "GET /supervisor returned HTTP $HTTP_CODE"
    return 1
  fi
  if ! python3 - "$SUPERVISOR_JSON" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
# Supervisor contract per janasunani/serving/schemas.py:SupervisorDashboard
# Top-level keys are workload, spike, closure; availability via provenance.state
expected = ["workload", "spike", "closure"]
if not all(k in data for k in expected):
    print(f"supervisor response missing workload/spike/closure: {list(data.keys())[:10]}", file=sys.stderr)
    sys.exit(2)
unavailable = 0
for k in expected:
    v = data.get(k) or {}
    prov = v.get("provenance") if isinstance(v, dict) else {}
    state = prov.get("state") if isinstance(prov, dict) else ""
    if state == "unavailable":
        unavailable += 1
    elif state == "recorded":
        continue
    else:
        unavailable += 1
if unavailable == len(expected):
    print(f"all supervisor panels unavailable", file=sys.stderr)
    sys.exit(1)
if unavailable:
    print(f"WARN: {unavailable}/{len(expected)} supervisor panels unavailable", file=sys.stderr)
PY
  then
    rc=$?
    if [ "$rc" -eq 1 ]; then
      fail "GET /supervisor: all panels unavailable"
      return 1
    elif [ "$rc" -eq 2 ]; then
      fail "GET /supervisor: unexpected shape"
      return 1
    else
      fail "GET /supervisor: validation failed"
      return 1
    fi
  else
    if python3 - "$SUPERVISOR_JSON" <<'PY2'
import json, sys
data=json.loads(open(sys.argv[1]).read())
expected=["workload","spike","closure"]
unavailable=sum(1 for k in expected if (data.get(k) or {}).get("provenance", {}).get("state") == "unavailable")
if unavailable:
    sys.exit(1)
PY2
    then
      ok "supervisor: all panels available"
    else
      warn "supervisor: some panels unavailable (closure-only fallback; see docs/DEMO.md)"
    fi
  fi

  # 5. History — GET /history?limit=5 returns 200 (may be empty on fresh DB)
  info "  GET /history?limit=5"
  HISTORY_JSON="$TMPDIR_REHEARSAL/history.json"
  HTTP_CODE="$(curl -s -w "%{http_code}" -o "$HISTORY_JSON" "$API_URL/history?limit=5" 2>/dev/null || echo "000")"
  if [ "$HTTP_CODE" != "200" ]; then
    fail "GET /history?limit=5 returned HTTP $HTTP_CODE"
    return 1
  fi
  # Validate shape, allow empty
  if ! python3 - "$HISTORY_JSON" <<'PY'
import json, sys
data=json.loads(open(sys.argv[1]).read())
if "items" not in data and "total" not in data:
    # Some history shapes return list directly
    if not isinstance(data, (list, dict)):
        print(f"unexpected history shape: {type(data)}", file=sys.stderr)
        sys.exit(1)
PY
  then
    fail "GET /history shape invalid"
    return 1
  fi
  HISTORY_COUNT="$(python3 -c "import json; d=json.load(open('$HISTORY_JSON')); print(len(d.get('items', d if isinstance(d,list) else [])))" 2>/dev/null || echo "0")"
  if [ "$HISTORY_COUNT" = "0" ]; then
    warn "history empty (fresh DB — live submissions appear in GET /grievance/{id} immediately; history is lake-backed and may lag per docs/DEMO.md sect 6)"
  else
    ok "history: $HISTORY_COUNT item(s)"
  fi

  # 6. Frontend — curl -sf FRONTEND_URL returns HTML (if started)
  info "  frontend $FRONTEND_URL"
  FRONTEND_BODY="$TMPDIR_REHEARSAL/frontend.html"
  if curl -sf "$FRONTEND_URL" -o "$FRONTEND_BODY" 2>/dev/null; then
    if grep -qi "<html" "$FRONTEND_BODY" || grep -qi "<!doctype" "$FRONTEND_BODY"; then
      ok "frontend: HTML returned"
    else
      warn "frontend: 200 but not HTML (body head: $(head -c 200 "$FRONTEND_BODY"))"
    fi
  else
    warn "frontend not reachable at $FRONTEND_URL — is 'make up' or 'make frontend' running? (optional for API-only rehearsal)"
  fi

  trap - EXIT
  rm -rf "$TMPDIR_REHEARSAL"

  ok "Phase B — stack smoke complete"
}

# ---------------------------------------------------------------------------
# Phase C — artifact presence (read-only, outside data/)
# ---------------------------------------------------------------------------
phase_c_artifacts() {
  if [ "$REHEARSAL_SKIP_ARTIFACTS" = "1" ]; then
    info "Phase C — artifacts: skipped (REHEARSAL_SKIP_ARTIFACTS=1)"
    return 0
  fi
  info "Phase C — artifact presence (read-only)"

  # 1. Routing crosswalk — required; without it routing degrades to fallback
  if ! check_artifact "janasunani/routing/reference/routing_crosswalk.json" "routing crosswalk" 1; then
    info "  hint: run 'uv run janasunani-build-crosswalk' and commit the artifact to restore method:learned"
  fi

  # 2. Closure summary in outputs/findings/ (Unit 4a)
  if [ -d "outputs/findings" ]; then
    FINDINGS_COUNT="$(find outputs/findings -type f | wc -l | tr -d ' ')"
    if [ "$FINDINGS_COUNT" -gt 0 ]; then
      ok "findings: outputs/findings/ ($FINDINGS_COUNT file(s))"
      ls -1 outputs/findings | head -20 | sed 's/^/    /'
    else
      check_artifact "outputs/findings/closure_finding_summary.csv" "closure findings (outputs/findings/ empty)" 0
    fi
  else
    check_artifact "outputs/findings" "closure findings" 0
  fi

  # 3. Aggregates — explicit JANASUNANI_SUPERVISOR_FINDINGS_DIR only; data/ probe requires opt-in per AGENTS.md
  AGG_DIR="${JANASUNANI_SUPERVISOR_FINDINGS_DIR:-}"
  if [ -n "$AGG_DIR" ] && [ -d "$AGG_DIR" ]; then
    AGG_COUNT="$(find "$AGG_DIR" -type f -name "*.csv" | wc -l | tr -d ' ')"
    if [ "$AGG_COUNT" -gt 0 ]; then
      ok "aggregates: $AGG_DIR ($AGG_COUNT csv(s))"
    else
      check_artifact "$AGG_DIR/*.csv" "aggregates in JANASUNANI_SUPERVISOR_FINDINGS_DIR" 0
    fi
  else
    if [ "${REHEARSAL_ALLOW_DATA:-0}" = "1" ]; then
      if [ -d "data/aggregates" ] && [ "$(find data/aggregates -type f -name "*.csv" 2>/dev/null | wc -l | tr -d ' ')" -gt 0 ]; then
        ok "aggregates: data/aggregates/"
      else
        check_artifact "data/aggregates/*.csv" "workload/spike aggregates (data/aggregates/ or JANASUNANI_SUPERVISOR_FINDINGS_DIR)" 0
        info "  hint: run 'uv run janasunani-publish-workload' / 'janasunani-publish-intelligence --publish-aggregates' to publish"
      fi
    else
      warn "aggregates: no JANASUNANI_SUPERVISOR_FINDINGS_DIR configured — skipping data/ probe per AGENTS.md (set REHEARSAL_ALLOW_DATA=1 to inspect data/ or configure JANASUNANI_SUPERVISOR_FINDINGS_DIR outside data/)"
    fi
  fi

  # 4. Sarvam scorecard (if Unit 5 landed) — warn only
  if [ -d "outputs/sarvam" ]; then
    S_COUNT="$(find outputs/sarvam -type f | wc -l | tr -d ' ')"
    if [ "$S_COUNT" -gt 0 ]; then
      ok "sarvam: outputs/sarvam/ ($S_COUNT file(s))"
    else
      check_artifact "outputs/sarvam/*.json" "sarvam scorecard (outputs/sarvam/ empty)" 0
    fi
  else
    check_artifact "outputs/sarvam" "sarvam scorecard" 0
  fi

  # 5. Benchmark Table 2 (if generated) — warn if missing, warn if stale >7d
  if [ -f "outputs/benchmark/table2.md" ]; then
    ok "benchmark: outputs/benchmark/table2.md"
    # Staleness check — warn if >7 days old
    if command -v stat >/dev/null 2>&1; then
      # macOS stat -f %m, GNU stat -c %Y
      if stat -f %m "outputs/benchmark/table2.md" >/dev/null 2>&1; then
        mtime=$(stat -f %m "outputs/benchmark/table2.md")
      else
        mtime=$(stat -c %Y "outputs/benchmark/table2.md")
      fi
      now=$(date +%s)
      age_days=$(( (now - mtime) / 86400 ))
      if [ "$age_days" -gt 7 ]; then
        warn "benchmark table2.md is ${age_days}d old (>7d stale)"
      fi
    fi
    # Optional schema check if CLI exists
    if uv run --extra serving janasunani-evaluate-benchmark --help >/dev/null 2>&1; then
      if ! uv run --extra serving janasunani-evaluate-benchmark --check >/dev/null 2>&1; then
        warn "benchmark table2.md failed schema check (janasunani-evaluate-benchmark --check)"
      fi
    fi
  else
    check_artifact "outputs/benchmark/table2.md" "benchmark Table 2" 0
  fi
}

# ---------------------------------------------------------------------------
# Phase D — optional real-model smoke
# ---------------------------------------------------------------------------
phase_d_models() {
  if [ "$REHEARSAL_SKIP_MODELS" = "1" ]; then
    info "Phase D — model smoke: skipped (REHEARSAL_SKIP_MODELS=1)"
    return 0
  fi
  # Only run if explicitly opted in via env
  if [ "${JANASUNANI_RUN_MODEL_SMOKE:-0}" != "1" ]; then
    info "Phase D — model smoke: skipped (set JANASUNANI_RUN_MODEL_SMOKE=1 to enable)"
    return 0
  fi
  info "Phase D — real-model smoke (JANASUNANI_RUN_MODEL_SMOKE=1)"
  if [ ! -f "tests/test_inference_model_smoke.py" ]; then
    warn "tests/test_inference_model_smoke.py not found — skipping model smoke"
    return 0
  fi
  if ! JANASUNANI_RUN_MODEL_SMOKE=1 uv run --extra demo pytest tests/test_inference_model_smoke.py -v; then
    fail "model smoke tests failed"
  else
    ok "model smoke tests passed"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  info "Demo rehearsal — $ROOT (strict=$REHEARSAL_STRICT, API=$API_URL)"
  echo ""

  # Track whether any phase hard-failed (FAILURES>0) vs just warnings
  set +e
  phase_a_static
  A_RC=$?
  phase_b_stack
  B_RC=$?
  phase_c_artifacts
  C_RC=$?
  phase_d_models
  D_RC=$?
  set -e

  echo ""
  info "Rehearsal summary: $FAILURES failure(s), $WARNINGS warning(s)"
  if [ "$FAILURES" -gt 0 ]; then
    log "FAIL" "rehearsal FAILED — $FAILURES failure(s)"
    if [ "$WARNINGS" -gt 0 ]; then
      log "WARN" "$WARNINGS warning(s) also present"
    fi
    exit 1
  fi
  if [ "$WARNINGS" -gt 0 ]; then
    if [ "$REHEARSAL_STRICT" = "1" ]; then
      log "FAIL" "rehearsal FAILED in strict mode — $WARNINGS warning(s) treated as failures"
      exit 1
    fi
    log "WARN" "rehearsal PASSED with $WARNINGS warning(s) (run with --strict to fail on warnings)"
  else
    log " OK " "rehearsal PASSED"
  fi
}

main "$@"
