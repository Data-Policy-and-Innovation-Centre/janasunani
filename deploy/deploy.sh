#!/usr/bin/env bash
# Box-side deploy script — the ONLY sanctioned way to bring the demo stack up
# to a new image tag. Invoked by CI (.github/workflows/deploy.yml, over SSH)
# or by hand for a manual redeploy/rollback:
#
#   IMAGE_TAG=<sha-or-tag> bash deploy/deploy.sh
#
# Contract: pulls api/frontend/proxy at IMAGE_TAG, brings the stack up,
# reloads Caddy so a Caddyfile-only change actually takes effect (it's
# bind-mounted, so `docker compose up -d` alone won't push it into an
# already-running proxy container), waits for `janasunani-api` AND
# `janasunani-frontend` to report Docker-healthy, then smoke-checks
# /api/health through the proxy (retrying briefly — first-deploy TLS cert
# issuance takes a few seconds). If `up -d`, the Caddy reload, a health
# wait, or the smoke check fails AFTER `up -d` has already swapped in the
# new candidate, it rolls back to the last known-good IMAGE_TAG *and*
# Caddyfile, then RE-VERIFIES the rollback is actually healthy before
# claiming success — see the migration policy note below for why that
# verification matters. Only once everything passes does it persist the
# new IMAGE_TAG (and a Caddyfile snapshot) as the new rollback target. A
# flock guards against a hand-run interleaving with a CI-triggered one.
# NEVER runs `docker compose down` — see deploy/README.md and
# docs/DEPLOY.md hard rules; the `oltp` volume holds production data.
#
# Migration policy (see docs/DEPLOY.md for the full writeup): a rollback
# here re-deploys the PREVIOUS image, unchanged — it never runs `alembic
# downgrade` (the old image doesn't even have the new revision file to
# downgrade FROM). That means every migration shipped through this script
# must be expand-only / backward-compatible: the OLD code must still be
# able to boot and run against the NEW schema (no rename/drop/narrow a
# column in the same deploy that adds it). Violate that and a rollback
# can't un-migrate — the "rolled back" api container just crash-loops on
# `alembic upgrade head` hitting a revision chain it can satisfy but a
# schema its queries can't, or vice versa. This script only detects that
# and refuses to lie about it (see rollback_and_fail's health re-check);
# it can't fix it.
set -euo pipefail
cd "$(dirname "$0")"

# Guards against a hand-run interleaving with a CI-triggered deploy (the
# workflow's own `concurrency:` group only serializes CI runs against each
# other, not against someone running this by hand on the box). Blocks
# (rather than failing immediately) so a manual run just waits its turn.
lockfile="$(pwd)/.deploy.lock"
exec 9>"$lockfile"
if ! flock -n 9; then
  echo "Another deploy.sh is already running (lock: ${lockfile}) -- waiting for it to finish..." >&2
  flock 9
fi

[[ -f .env ]] || { echo "deploy/.env missing — copy .env.example and fill it in" >&2; exit 1; }
: "${IMAGE_TAG:?IMAGE_TAG must be set, e.g. IMAGE_TAG=<sha> bash deploy/deploy.sh}"
new_tag="$IMAGE_TAG"

# Capture the last known-good tag BEFORE touching anything, for the
# rollback path below. `|| true`: under `set -o pipefail`, grep finding no
# match makes the whole `grep | cut` pipeline exit non-zero even though cut
# itself succeeded on empty input, which would abort the script right here
# instead of leaving prev_tag empty (e.g. a Week-1 .env that never had an
# IMAGE_TAG= line yet -- a legitimate first-ever-deploy case, not an error).
prev_tag="$(grep -E '^IMAGE_TAG=' .env | cut -d= -f2- || true)"

# Preflight: docker-compose.yml's proxy service needs env_file's `format: raw`
# (deploy/proxy.env) to keep a bcrypt hash's `$` from being interpolated —
# that key was only added in Compose 2.30.0. On an older Compose the whole
# file fails to *parse*, before any service starts, with an opaque error;
# fail loudly here instead with a clear message.
compose_version="$(docker compose version --short)"
required_compose_version="2.30.0"
if [[ "$compose_version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
  compose_major="${BASH_REMATCH[1]}"
  compose_minor="${BASH_REMATCH[2]}"
else
  echo "Could not parse Docker Compose version from '${compose_version}'." >&2
  exit 1
fi
if (( compose_major < 2 || (compose_major == 2 && compose_minor < 30) )); then
  echo "Docker Compose >= ${required_compose_version} is required (env_file 'format: raw' in deploy/docker-compose.yml); found ${compose_version}." >&2
  exit 1
fi

# Fail closed on the site-wide Basic Auth credential. docker-compose.yml
# deliberately does NOT gate on this itself (a compose-level `:?` would abort
# even an oltp-only `docker compose up -d oltp`) — this script is where "no
# real password hash configured" must stop a full-stack deploy instead of
# silently exposing production /history and /api behind a broken or
# default(ish) auth.
[[ -f proxy.env ]] || { echo "deploy/proxy.env missing — copy proxy.env.example and set DEMO_PASSWORD_HASH" >&2; exit 1; }
# `|| true`: same pipefail footgun as prev_tag above -- an empty/missing
# DEMO_PASSWORD_HASH must fall through to the explicit check below (which
# prints a clear, actionable error), not abort the script on grep's bare
# no-match exit code.
demo_hash="$(grep -E '^DEMO_PASSWORD_HASH=' proxy.env | cut -d= -f2- || true)"
if [[ -z "$demo_hash" || ! "$demo_hash" =~ ^\$2[aby]\$ ]]; then
  echo "DEMO_PASSWORD_HASH in deploy/proxy.env is missing or doesn't look like a bcrypt hash. Generate one with:" >&2
  echo "  docker run --rm caddy:2-alpine caddy hash-password --plaintext '<password>'" >&2
  exit 1
fi

# Disk preflight: this root volume also holds prod Postgres, models, the
# Parquet lake, the HF cache, AND the nightly pg_dump backup target — a
# ~8-12 GB api image pull that runs the disk out of room risks taking all
# of those down together, not just the deploy. 20 GiB (overridable, like the
# timeouts above, so tests can run on a dev machine without that much free)
# is a few multiples of one api image, comfortable headroom for the pull
# plus the old image staying around as the rollback target.
min_free_kib="${MIN_FREE_KIB:-$((20 * 1024 * 1024))}"
free_kib="$(df -Pk . | tail -1 | awk '{print $4}')"
if (( free_kib < min_free_kib )); then
  echo "Only $((free_kib / 1024 / 1024)) GiB free on this filesystem; need at least $((min_free_kib / 1024 / 1024)) GiB headroom before pulling images. Aborting without touching the running stack." >&2
  exit 1
fi

# Only exported for THIS run so compose picks it up — deliberately NOT
# written to .env yet (see the end of this script).
export IMAGE_TAG

# The Caddyfile is bind-mounted (deploy/proxy/Caddyfile), so if the proxy
# container is already running, `up -d` below will leave it untouched even
# though a freshly-shipped Caddyfile is sitting on disk — Caddy never reads
# it again on its own. Remember whether it was already up so we know whether
# a reload is needed after; a brand-new container already loads the current
# file at startup, and `docker compose exec` on a not-yet-existing container
# would just fail.
proxy_was_running=false
if [[ "$(docker inspect -f '{{.State.Running}}' janasunani-proxy 2>/dev/null)" == "true" ]]; then
  proxy_was_running=true
fi

# Reusable health wait — used both for the forward deploy below and, in
# rollback_and_fail, to verify the ROLLBACK actually came up healthy rather
# than just claiming success. Returns non-zero on timeout instead of
# exiting directly, so the two call sites can each decide what "failed"
# means for them (forward: roll back; rollback: report loudly and give up).
# Overridable (not just hardcoded) so tests can exercise the timeout path
# in seconds instead of real minutes, without touching production defaults.
wait_healthy_timeout_s="${WAIT_HEALTHY_TIMEOUT_S:-1800}"
wait_healthy_poll_s="${WAIT_HEALTHY_POLL_S:-10}"

wait_healthy() {
  local name="$1"
  echo "Waiting for ${name} health..."
  local deadline=$(( $(date +%s) + wait_healthy_timeout_s ))
  while true; do
    local status
    status="$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo absent)"
    [[ "$status" == healthy ]] && return 0
    if (( $(date +%s) > deadline )); then
      echo "${name} never healthy (status: ${status}); last 100 log lines:" >&2
      # This lands in repo-readable CI Actions logs. The api must never log
      # a grievance payload (raw/redacted text, PII spans, document bytes)
      # to stdout/stderr — confirmed: janasunani/serving/api.py's only
      # request-path log line (POST /grievance) emits grievance_id,
      # ticket_no, source, processor name, category, and dept only, never
      # extraction/redaction content; uvicorn's own access log is
      # method/path/status, not bodies.
      docker logs --tail 100 "$name" >&2 || true
      return 1
    fi
    sleep "$wait_healthy_poll_s"
  done
}

# `docker compose up -d` (below) immediately replaces whatever was
# previously running with the new candidate — by the time `up -d` itself,
# the Caddy reload, a health wait, or the smoke check fails, the public
# demo is already down on the broken one. Explicit call sites (not a
# blanket ERR trap: a trap would also fire on the preflight/hash-check/
# disk-space exits above, which happen before anything is touched and have
# nothing to roll back) route through this instead of a bare `exit 1`.
#
# Rolls back BOTH things that changed: the image tag (api/frontend only —
# compose leaves oltp/proxy alone since their own config didn't change; the
# previous image is still on disk, this script only ever prunes dangling
# images and old SHA tags, never the current+previous pair) and the
# Caddyfile (bind-mounted, not tag-versioned, so it needs its own snapshot —
# deploy/proxy/Caddyfile.deployed, written at the end of a successful run,
# below).
#
# Critically, it then RE-VERIFIES the rollback with the same wait_healthy
# used for a forward deploy, and reports honestly: if the previous image
# can't come up healthy either (see the migration policy note at the top of
# this file — a rollback can't un-migrate), this says so loudly instead of
# printing a false "rolled back" success while the demo stays down.
rollback_and_fail() {
  echo "Deploy of ${new_tag} failed." >&2

  # Restore the Caddyfile FIRST and independently of the image-tag decision
  # below: a redeploy of the SAME IMAGE_TAG done specifically to ship a
  # Caddyfile-only change can fail this way too, and even though `caddy
  # reload` is atomic (a bad file is rejected and the already-running
  # config keeps serving live traffic), the ON-DISK file stays corrupted
  # unless restored — a later container restart (box reboot, an unrelated
  # future `docker compose up`) would load that broken file and crash-loop.
  if [[ -f proxy/Caddyfile.deployed ]] && ! cmp -s proxy/Caddyfile proxy/Caddyfile.deployed; then
    echo "Restoring the last known-good Caddyfile..." >&2
    cp proxy/Caddyfile.deployed proxy/Caddyfile
    if [[ "$(docker inspect -f '{{.State.Running}}' janasunani-proxy 2>/dev/null)" == "true" ]]; then
      docker compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || true
    fi
  fi

  if [[ -z "$prev_tag" || "$prev_tag" == "$new_tag" ]]; then
    echo "No prior known-good IMAGE_TAG to roll back to (first-ever deploy, or redeploying the same tag) -- leaving the running containers as-is." >&2
    exit 1
  fi

  echo "Rolling back to last known-good IMAGE_TAG=${prev_tag}..." >&2
  if ! IMAGE_TAG="$prev_tag" docker compose up -d; then
    echo "Deploy of ${new_tag} failed AND rollback to ${prev_tag} failed to start -- manual intervention required on the box." >&2
    exit 1
  fi
  # Best-effort: the Caddyfile was already restored+reloaded above; this
  # covers the case where the proxy container itself just got recreated by
  # the `up -d` above (e.g. it wasn't running yet) and so didn't pick up
  # that reload.
  docker compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || true

  if wait_healthy janasunani-api && wait_healthy janasunani-frontend; then
    echo "Deploy of ${new_tag} failed; rolled back to ${prev_tag} and verified healthy." >&2
  else
    echo "ROLLBACK FAILED -- manual intervention required on the box. Deploy of ${new_tag} failed, and the rollback to ${prev_tag} did not come back healthy either (this can happen if ${new_tag} shipped a migration ${prev_tag} can't satisfy -- see the migration policy note at the top of this file and in docs/DEPLOY.md). The demo is DOWN." >&2
  fi
  exit 1
}

docker compose pull api frontend proxy
docker compose up -d || rollback_and_fail

if [[ "$proxy_was_running" == "true" ]]; then
  echo "Reloading Caddy config..."
  docker compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || rollback_and_fail
fi

wait_healthy janasunani-api || rollback_and_fail
wait_healthy janasunani-frontend || rollback_and_fail

site="$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2- || true)"
echo "Smoke-checking the proxy..."
smoke_check_timeout_s="${SMOKE_CHECK_TIMEOUT_S:-120}"
smoke_check_poll_s="${SMOKE_CHECK_POLL_S:-5}"
smoke_deadline=$(( $(date +%s) + smoke_check_timeout_s ))
while true; do
  if [[ "$site" == :80 || -z "$site" ]]; then
    curl -sf http://127.0.0.1/api/health 2>/dev/null | grep -q '"processor":"pipeline"' && break
  else
    # First deploy of a fresh nip.io hostname: Caddy is still issuing the
    # Let's Encrypt cert, so give this a couple of minutes before giving up.
    curl -skf --resolve "${site}:443:127.0.0.1" "https://${site}/api/health" 2>/dev/null | grep -q '"processor":"pipeline"' && break
  fi
  if (( $(date +%s) > smoke_deadline )); then
    echo "Proxy smoke check never succeeded after ${smoke_check_timeout_s}s." >&2
    rollback_and_fail
  fi
  sleep "$smoke_check_poll_s"
done

# Only NOW — after pull, up, reload, both health waits, and the end-to-end
# smoke check have all passed — persist the tag into .env, so a later plain
# `docker compose up` (or a box reboot re-running compose via whatever
# restart policy is in place) redeploys the same version. If anything above
# failed, .env still holds the last tag that actually deployed successfully,
# not this one. Same idea for the Caddyfile snapshot: only a Caddyfile that
# just proved itself (reload succeeded, smoke check passed) becomes the new
# rollback target.
if grep -q '^IMAGE_TAG=' .env; then
  sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=${IMAGE_TAG}|" .env
else
  printf 'IMAGE_TAG=%s\n' "${IMAGE_TAG}" >> .env
fi
cp proxy/Caddyfile proxy/Caddyfile.deployed

# Retention: keep only the current + previous (the rollback target) SHA-
# tagged images per app repo. Each api image is ~8-12 GB; this volume also
# holds prod Postgres + models + lake + HF cache + nightly pg_dump, so
# unbounded accumulation across many deploys is a real disk-full risk, not
# just tidiness.
api_image="$(grep -E '^API_IMAGE=' .env | cut -d= -f2- || true)"
api_image="${api_image:-ghcr.io/data-policy-and-innovation-centre/janasunani-api}"
frontend_image="$(grep -E '^FRONTEND_IMAGE=' .env | cut -d= -f2- || true)"
frontend_image="${frontend_image:-ghcr.io/data-policy-and-innovation-centre/janasunani-frontend}"
for repo in "$api_image" "$frontend_image"; do
  docker images "$repo" --format '{{.Tag}}' | while read -r tag; do
    if [[ "$tag" != "$new_tag" && "$tag" != "$prev_tag" ]]; then
      docker rmi "${repo}:${tag}" >/dev/null 2>&1 || true
    fi
  done
done
docker image prune -f >/dev/null

echo "Deployed ${IMAGE_TAG} -- https://${site:-<box>}/"
