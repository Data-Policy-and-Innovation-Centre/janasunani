#!/usr/bin/env bash
# Box-side deploy script — the ONLY sanctioned way to bring the demo stack up
# to a new image tag. Invoked by CI (.github/workflows/deploy.yml, over SSH)
# or by hand for a manual redeploy/rollback:
#
#   IMAGE_TAG=<sha-or-tag> bash deploy/deploy.sh
#
# Contract: pulls api/frontend/proxy at IMAGE_TAG, brings the stack up, waits
# for `janasunani-api` to report Docker-healthy, then smoke-checks /api/health
# through the proxy. Exits non-zero (and dumps api logs) unless the deploy is
# actually serving. NEVER runs `docker compose down` — see deploy/README.md
# and docs/DEPLOY.md hard rules; the `oltp` volume holds production data.
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "deploy/.env missing — copy .env.example and fill it in" >&2; exit 1; }
: "${IMAGE_TAG:?IMAGE_TAG must be set, e.g. IMAGE_TAG=<sha> bash deploy/deploy.sh}"

# Persist the tag into .env so a plain `docker compose up` (or a box reboot,
# which re-runs compose via whatever restart policy is in place) redeploys
# the same version rather than falling back to an empty/unset IMAGE_TAG.
if grep -q '^IMAGE_TAG=' .env; then
  sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=${IMAGE_TAG}|" .env
else
  printf 'IMAGE_TAG=%s\n' "${IMAGE_TAG}" >> .env
fi
export IMAGE_TAG

docker compose pull api frontend proxy
docker compose up -d

echo "Waiting for janasunani-api health..."
deadline=$(( $(date +%s) + 1800 ))
while true; do
  status="$(docker inspect -f '{{.State.Health.Status}}' janasunani-api 2>/dev/null || echo absent)"
  [[ "$status" == healthy ]] && break
  if (( $(date +%s) > deadline )); then
    echo "api never healthy (status: ${status}); last 100 log lines:" >&2
    docker logs --tail 100 janasunani-api >&2 || true
    exit 1
  fi
  sleep 10
done

site="$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2-)"
if [[ "$site" == :80 || -z "$site" ]]; then
  curl -sf http://127.0.0.1/api/health | grep -q '"processor":"pipeline"'
else
  curl -skf --resolve "${site}:443:127.0.0.1" "https://${site}/api/health" | grep -q '"processor":"pipeline"'
fi

docker image prune -f >/dev/null

echo "Deployed ${IMAGE_TAG} -- https://${site:-<box>}/"
