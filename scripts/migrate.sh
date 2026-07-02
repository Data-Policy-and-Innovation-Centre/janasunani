#!/usr/bin/env bash
# Cold-start migration, self-contained: bring up an ephemeral MySQL, restore the
# raw dump into it (only if not already loaded), then load the OLTP store.
#
# Only needs Docker + uv. Idempotent. Tunable via env:
#   DUMP            path to the .sql dump            (default: data/raw/Dump20250730.sql)
#   MYSQL_CONTAINER container name                   (default: janasunani-migrate-mysql)
#   MYSQL_PORT      host port for MySQL              (default: 3307)
#   MYSQL_PASSWORD  root password                    (default: pass)
#   KEEP_MYSQL      1=leave container up (fast re-runs), 0=tear down  (default: 1)
#   OLTP_DB_URL     target OLTP store (default: settings default, local SQLite)
set -euo pipefail

DUMP="${DUMP:-data/raw/Dump20250730.sql}"
CONTAINER="${MYSQL_CONTAINER:-janasunani-migrate-mysql}"
PORT="${MYSQL_PORT:-3307}"
PASSWORD="${MYSQL_PASSWORD:-pass}"
DB_NAME="sociomatics_ticket"
KEEP_MYSQL="${KEEP_MYSQL:-1}"

log() { echo "[migrate.sh $(date +%T)] $*"; }

cleanup() {
  if [ "$KEEP_MYSQL" != "1" ]; then
    log "removing container $CONTAINER"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

[ -f "$DUMP" ] || { echo "dump not found: $DUMP" >&2; exit 1; }

# 1) Ensure the MySQL container is running.
if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  log "starting MySQL container $CONTAINER on port $PORT"
  docker run -d --name "$CONTAINER" \
    -e MYSQL_ROOT_PASSWORD="$PASSWORD" \
    -p "$PORT":3306 \
    mysql:8.0 --default-authentication-plugin=mysql_native_password --sql-mode="" >/dev/null
fi

# 2) Wait for readiness.
log "waiting for MySQL to accept connections"
for _ in $(seq 1 90); do
  if docker exec "$CONTAINER" mysqladmin ping -uroot -p"$PASSWORD" --silent >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# 3) Restore the dump only if the table isn't already populated.
loaded="$(docker exec "$CONTAINER" mysql -uroot -p"$PASSWORD" -N \
  -e "SELECT COUNT(*) FROM ${DB_NAME}.t_janasunani_etl_pre_data" 2>/dev/null || echo 0)"
if [ "${loaded:-0}" -gt 0 ]; then
  log "dump already loaded (${loaded} complaints) — skipping restore"
else
  log "creating database + restoring dump (this takes a few minutes)"
  docker exec "$CONTAINER" mysql -uroot -p"$PASSWORD" \
    -e "CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
  docker exec -i "$CONTAINER" mysql -uroot -p"$PASSWORD" "$DB_NAME" < "$DUMP"
  log "restore complete"
fi

# 4) Migrate MySQL -> OLTP store (restore already handled above).
log "loading OLTP store from MySQL"
uv run janasunani-migrate-dump \
  --skip-restore \
  --mysql-url "mysql+pymysql://root:${PASSWORD}@127.0.0.1:${PORT}/"

log "done"
