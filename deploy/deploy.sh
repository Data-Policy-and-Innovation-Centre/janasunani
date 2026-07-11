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
# /api/health through the proxy. Exits non-zero (and dumps logs) unless the
# deploy is actually serving. NEVER runs `docker compose down` — see
# deploy/README.md and docs/DEPLOY.md hard rules; the `oltp` volume holds
# production data.
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "deploy/.env missing — copy .env.example and fill it in" >&2; exit 1; }
: "${IMAGE_TAG:?IMAGE_TAG must be set, e.g. IMAGE_TAG=<sha> bash deploy/deploy.sh}"

# Fail closed on the site-wide Basic Auth credential. docker-compose.yml
# deliberately does NOT gate on this itself (a compose-level `:?` would abort
# even an oltp-only `docker compose up -d oltp`) — this script is where "no
# real password hash configured" must stop a full-stack deploy instead of
# silently exposing production /history and /api behind a broken or
# default(ish) auth.
[[ -f proxy.env ]] || { echo "deploy/proxy.env missing — copy proxy.env.example and set DEMO_PASSWORD_HASH" >&2; exit 1; }
demo_hash="$(grep -E '^DEMO_PASSWORD_HASH=' proxy.env | cut -d= -f2-)"
if [[ -z "$demo_hash" || ! "$demo_hash" =~ ^\$2[aby]\$ ]]; then
  echo "DEMO_PASSWORD_HASH in deploy/proxy.env is missing or doesn't look like a bcrypt hash. Generate one with:" >&2
  echo "  docker run --rm caddy:2-alpine caddy hash-password --plaintext '<password>'" >&2
  exit 1
fi

# Persist the tag into .env so a plain `docker compose up` (or a box reboot,
# which re-runs compose via whatever restart policy is in place) redeploys
# the same version rather than falling back to an empty/unset IMAGE_TAG.
if grep -q '^IMAGE_TAG=' .env; then
  sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=${IMAGE_TAG}|" .env
else
  printf 'IMAGE_TAG=%s\n' "${IMAGE_TAG}" >> .env
fi
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

docker compose pull api frontend proxy
docker compose up -d

if [[ "$proxy_was_running" == "true" ]]; then
  echo "Reloading Caddy config..."
  docker compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
fi

wait_healthy() {
  local name="$1"
  echo "Waiting for ${name} health..."
  local deadline=$(( $(date +%s) + 1800 ))
  while true; do
    local status
    status="$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo absent)"
    [[ "$status" == healthy ]] && break
    if (( $(date +%s) > deadline )); then
      echo "${name} never healthy (status: ${status}); last 100 log lines:" >&2
      docker logs --tail 100 "$name" >&2 || true
      exit 1
    fi
    sleep 10
  done
}

wait_healthy janasunani-api
wait_healthy janasunani-frontend

site="$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2-)"
if [[ "$site" == :80 || -z "$site" ]]; then
  curl -sf http://127.0.0.1/api/health | grep -q '"processor":"pipeline"'
else
  curl -skf --resolve "${site}:443:127.0.0.1" "https://${site}/api/health" | grep -q '"processor":"pipeline"'
fi

docker image prune -f >/dev/null

echo "Deployed ${IMAGE_TAG} -- https://${site:-<box>}/"
