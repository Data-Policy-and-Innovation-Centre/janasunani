#!/bin/sh
# Container entrypoint for the janasunani-api image (deploy/api.Dockerfile).
# Runs schema migrations against the configured OLTP_DB_URL, then execs the
# real live API (janasunani-api-live = janasunani.inference.serve:main) as
# PID 1. Not a one-shot migration service: models are host bind-mounted, so
# migration has to happen on every container start, right before serving.
set -eu
cd /app
alembic upgrade head
exec janasunani-api-live
