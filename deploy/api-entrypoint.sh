#!/bin/sh
# Container entrypoint for the janasunani-api image (deploy/api.Dockerfile).
# Runs schema migrations against the configured OLTP_DB_URL, then execs the
# real live API (janasunani-api-live = janasunani.inference.serve:main) as
# PID 1. Not a one-shot migration service: models are host bind-mounted, so
# migration has to happen on every container start, right before serving.
#
# MIGRATION POLICY (see docs/DEPLOY.md for the full writeup): a rollback in
# deploy/deploy.sh re-deploys the PREVIOUS image unchanged — it never runs
# `alembic downgrade` (that image doesn't have the new revision file to
# downgrade FROM). So `alembic upgrade head` here must never be the only
# thing standing between a bad deploy and a bricked rollback: every
# migration shipped through this pipeline has to be expand-only / backward-
# compatible, i.e. the OLD code (the rollback target) must still be able to
# boot and run correctly against the NEW schema. Concretely: add nullable
# columns/new tables freely; don't rename or drop a column/table, narrow a
# type, or add a NOT NULL constraint without a default in the SAME deploy
# that a rollback might need to undo — split that into an expand deploy
# (ships first, old code ignores the new column) and a later contract
# deploy (only after a rollback to the expand step is no longer a
# realistic need).
set -eu
cd /app
alembic upgrade head
exec janasunani-api-live
