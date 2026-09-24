#!/bin/sh
# Container entrypoint for the janasunani-api image (deploy/api.Dockerfile).
# Checks the production schema, then execs the real live API
# (janasunani-api-live = janasunani.inference.serve:main) as PID 1.
#
# It never migrates. The database holds the migrated production data, so a
# migration is a manual step after a fresh backup (docs/DEPLOY.md,
# "Migrations"):
#
#   ~/bin/backup-oltp.sh
#   IMAGE_TAG=<sha> docker compose run --rm --no-deps --entrypoint alembic api upgrade head
#
# `api-entrypoint.sh --check-only` runs the check and exits; deploy/deploy.sh
# calls it before swapping containers, so a pending migration stops the deploy
# instead of crash-looping the new api for the whole health wait.
#
# The check:
# - database at this image's head: start.
# - database behind (a migration this image needs has not run, or an empty
#   database): refuse, and say how to migrate.
# - database at a revision this image does not know: start. That is a rollback
#   to an image older than the schema, which the MIGRATION POLICY below makes
#   safe.
# - any other alembic error (database unreachable, bad URL): refuse.
#
# MIGRATION POLICY (see docs/DEPLOY.md for the full writeup): a rollback in
# deploy/deploy.sh re-deploys the PREVIOUS image unchanged — it never runs
# `alembic downgrade` (that image doesn't have the new revision file to
# downgrade FROM). So every migration has to be expand-only / backward-
# compatible, i.e. the OLD code (the rollback target) must still be able to
# boot and run correctly against the NEW schema. Concretely: add nullable
# columns/new tables freely; don't rename or drop a column/table, narrow a
# type, or add a NOT NULL constraint without a default in the SAME deploy
# that a rollback might need to undo — split that into an expand deploy
# (ships first, old code ignores the new column) and a later contract
# deploy (only after a rollback to the expand step is no longer a
# realistic need).
set -eu
cd "${APP_DIR:-/app}"  # overridden only by tests/test_deploy_stack.py

head="$(alembic heads 2>/dev/null | awk 'NF { print $1; exit }')"
[ -n "$head" ] || { echo "Could not read this image's alembic head." >&2; exit 1; }
if current_out="$(alembic current 2>&1)"; then
  current="$(printf '%s\n' "$current_out" | awk '$1 ~ /^[0-9a-f]+$/ { print $1; exit }')"
  if [ "$current" != "$head" ]; then
    echo "Database schema is at '${current:-empty}', this image needs '${head}'." >&2
    echo "Back up, then migrate by hand (docs/DEPLOY.md, \"Migrations\"):" >&2
    echo "  ~/bin/backup-oltp.sh" >&2
    echo "  IMAGE_TAG=<sha> docker compose run --rm --no-deps --entrypoint alembic api upgrade head" >&2
    exit 1
  fi
elif printf '%s\n' "$current_out" | grep -q "Can't locate revision"; then
  echo "Database schema is newer than this image (a rollback); starting under the expand-only migration policy." >&2
else
  printf '%s\n' "$current_out" >&2
  echo "Could not read the database schema revision." >&2
  exit 1
fi

[ "${1:-}" = "--check-only" ] && exit 0
exec janasunani-api-live
