#!/usr/bin/env bash
# Runs once on first container start (docker-entrypoint-initdb.d). Idempotent SQL so it
# can also be re-run by `make db-reset` and by CI against a plain postgres service.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export PGPASSWORD="${POSTGRES_PASSWORD:-bidradar}"
PSQL=(psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-bidradar}" -h "${PGHOST:-/var/run/postgresql}" -p "${PGPORT:-5432}")
"${PSQL[@]}" -d postgres -v app_password="${BIDRADAR_APP_PASSWORD:-bidradar_app}" -f "$DIR/sql/roles.sql"
for db in bidradar bidradar_test; do
  "${PSQL[@]}" -d "$db" -f "$DIR/sql/extensions.sql"
done
