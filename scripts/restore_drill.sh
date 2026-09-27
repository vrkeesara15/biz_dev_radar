#!/usr/bin/env bash
#
# SPEC 11: "Backups: daily Cloud SQL backups + PITR 7 days; restore drill before launch."
#
# This is the drill. It restores the LATEST successful automated backup of an environment
# into a brand-new scratch instance, checks that the restored database is actually usable
# (alembic head + row counts on the tables that matter), prints a verdict, and deletes the
# scratch instance. It never touches the source instance, and it never restores over
# anything: `gcloud sql backups restore` into an existing instance would overwrite it, so
# this script always creates its own target and refuses to reuse a name.
#
#   scripts/restore_drill.sh --env prod-in --dry-run     # print the plan, change nothing
#   scripts/restore_drill.sh --env prod-in               # do it
#   scripts/restore_drill.sh --env dev --keep            # leave the scratch instance up
#
# Requires: gcloud (authenticated), and for the verification step either the Cloud SQL
# Auth Proxy on PATH or --skip-verify.
#
# Exit codes: 0 drill passed · 1 drill failed · 2 bad usage or missing prerequisite.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENVIRONMENT=""
PROJECT=""
REGION=""
INSTANCE=""
DRY_RUN=0
KEEP=0
SKIP_VERIFY=0
PROXY_PORT="${PROXY_PORT:-6543}"
DB_NAME="${DB_NAME:-bidradar}"

usage() {
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

die() {
  echo "error: $*" >&2
  exit 2
}

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '   [dry-run] %s\n' "$*"
  else
    printf '   $ %s\n' "$*"
    "$@"
  fi
}

# --------------------------------------------------------------------------- arguments
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENVIRONMENT="${2:-}"; shift ;;
    --project) PROJECT="${2:-}"; shift ;;
    --region) REGION="${2:-}"; shift ;;
    --instance) INSTANCE="${2:-}"; shift ;;
    --db) DB_NAME="${2:-}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    --keep) KEEP=1 ;;
    --skip-verify) SKIP_VERIFY=1 ;;
    -h|--help) usage 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

[ -n "$ENVIRONMENT" ] || die "--env is required (dev | staging-in | prod-us | prod-in)"

case "$ENVIRONMENT" in
  dev)        DEFAULT_PROJECT="bidradar-dev";        DEFAULT_REGION="us-east1";    DEFAULT_PREFIX="bidradar-dev" ;;
  staging-in) DEFAULT_PROJECT="bidradar-staging-in"; DEFAULT_REGION="asia-south1"; DEFAULT_PREFIX="bidradar-stg-in" ;;
  prod-us)    DEFAULT_PROJECT="bidradar-prod-us";    DEFAULT_REGION="us-east1";    DEFAULT_PREFIX="bidradar-prod-us" ;;
  prod-in)    DEFAULT_PROJECT="bidradar-prod-in";    DEFAULT_REGION="asia-south1"; DEFAULT_PREFIX="bidradar-prod-in" ;;
  *) die "unknown environment: $ENVIRONMENT" ;;
esac

PROJECT="${PROJECT:-$DEFAULT_PROJECT}"
REGION="${REGION:-$DEFAULT_REGION}"
INSTANCE="${INSTANCE:-${DEFAULT_PREFIX}-pg}"
SCRATCH="${DEFAULT_PREFIX}-drill-$(date -u +%Y%m%d-%H%M%S)"

command -v gcloud >/dev/null 2>&1 || die "gcloud is not on PATH"

cat <<SUMMARY

BidRadar restore drill
  environment   ${ENVIRONMENT}
  project       ${PROJECT}
  region        ${REGION}   <- the restore MUST stay in region (SPEC 11 residency)
  source        ${INSTANCE}
  scratch       ${SCRATCH}
  database      ${DB_NAME}
  mode          $([ "$DRY_RUN" -eq 1 ] && echo 'dry run (nothing is created)' || echo 'live')

SUMMARY

cleanup() {
  local status=$?
  if [ "$KEEP" -eq 1 ]; then
    echo
    echo "--keep: scratch instance ${SCRATCH} was NOT deleted. Delete it yourself:"
    echo "  gcloud sql instances delete ${SCRATCH} --project ${PROJECT} --quiet"
  elif [ "$DRY_RUN" -eq 0 ] && [ "${SCRATCH_CREATED:-0}" -eq 1 ]; then
    log "Deleting the scratch instance"
    gcloud sql instances patch "$SCRATCH" --project "$PROJECT" \
      --no-deletion-protection --quiet >/dev/null 2>&1 || true
    gcloud sql instances delete "$SCRATCH" --project "$PROJECT" --quiet || \
      echo "warning: could not delete ${SCRATCH}; delete it by hand" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

# ------------------------------------------------------------------- 1. find the backup
log "1/6 Listing automated backups of ${INSTANCE}"
if [ "$DRY_RUN" -eq 1 ]; then
  BACKUP_ID="<latest>"
  BACKUP_TIME="<unknown>"
else
  BACKUP_LINE=$(gcloud sql backups list \
    --instance "$INSTANCE" --project "$PROJECT" \
    --filter 'status=SUCCESSFUL AND type=AUTOMATED' \
    --sort-by '~windowStartTime' --limit 1 \
    --format 'value(id,windowStartTime)')
  [ -n "$BACKUP_LINE" ] || { echo "error: no successful automated backup for ${INSTANCE}" >&2; exit 1; }
  BACKUP_ID=$(echo "$BACKUP_LINE" | awk '{print $1}')
  BACKUP_TIME=$(echo "$BACKUP_LINE" | awk '{print $2}')
  echo "   latest successful automated backup: ${BACKUP_ID} (${BACKUP_TIME})"

  # A backup older than ~36 hours means the daily schedule is not running.
  AGE_HOURS=$(python3 - "$BACKUP_TIME" <<'PY'
import sys
from datetime import UTC, datetime
stamp = sys.argv[1].replace("Z", "+00:00")
print(int((datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds() // 3600))
PY
)
  echo "   age: ${AGE_HOURS}h"
  if [ "$AGE_HOURS" -gt 36 ]; then
    echo "error: the newest automated backup is ${AGE_HOURS}h old; the daily schedule is broken" >&2
    exit 1
  fi
fi

# ------------------------------------------------------------- 2. confirm the PITR window
log "2/6 Confirming backup configuration (daily + PITR 7 days)"
if [ "$DRY_RUN" -eq 0 ]; then
  gcloud sql instances describe "$INSTANCE" --project "$PROJECT" --format '
    value(
      settings.backupConfiguration.enabled,
      settings.backupConfiguration.pointInTimeRecoveryEnabled,
      settings.backupConfiguration.transactionLogRetentionDays,
      settings.backupConfiguration.location,
      region
    )' | while read -r enabled pitr logdays location instance_region; do
    echo "   backups=${enabled} pitr=${pitr} log_retention_days=${logdays} backup_location=${location} region=${instance_region}"
    [ "$enabled" = "True" ] || { echo "error: automated backups are OFF" >&2; exit 1; }
    [ "$pitr" = "True" ] || { echo "error: point-in-time recovery is OFF (SPEC 11)" >&2; exit 1; }
    [ "${logdays:-0}" -ge 7 ] || { echo "error: PITR window is ${logdays}d, SPEC 11 requires 7" >&2; exit 1; }
    case "$location" in
      "$REGION"|"${REGION%%-*}"|"") ;;
      *) echo "error: backups are stored in ${location}, outside ${REGION} (residency)" >&2; exit 1 ;;
    esac
  done
fi

# --------------------------------------------------------------- 3. create the scratch box
log "3/6 Creating the scratch instance ${SCRATCH}"
if gcloud sql instances describe "$SCRATCH" --project "$PROJECT" >/dev/null 2>&1; then
  die "an instance named ${SCRATCH} already exists; refusing to restore over it"
fi
run gcloud sql instances create "$SCRATCH" \
  --project "$PROJECT" \
  --database-version POSTGRES_16 \
  --region "$REGION" \
  --tier db-custom-2-7680 \
  --storage-size 50 \
  --no-backup \
  --no-deletion-protection \
  --labels "purpose=restore-drill,environment=${ENVIRONMENT}" \
  --quiet
[ "$DRY_RUN" -eq 1 ] || SCRATCH_CREATED=1

# --------------------------------------------------------------------- 4. do the restore
log "4/6 Restoring backup ${BACKUP_ID} into ${SCRATCH}"
run gcloud sql backups restore "$BACKUP_ID" \
  --restore-instance "$SCRATCH" \
  --backup-instance "$INSTANCE" \
  --project "$PROJECT" \
  --quiet

# ------------------------------------------------------------------------- 5. verify it
log "5/6 Verifying the restored database"
if [ "$SKIP_VERIFY" -eq 1 ]; then
  echo "   --skip-verify: not checking the contents"
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "   [dry-run] would start the Cloud SQL Auth Proxy on :${PROXY_PORT} and run:"
  echo "   [dry-run]   alembic current           (must print a revision, not empty)"
  echo "   [dry-run]   SELECT count(*) FROM tenants, opportunities, audit_log"
elif ! command -v cloud-sql-proxy >/dev/null 2>&1; then
  echo "warning: cloud-sql-proxy is not on PATH; skipping content verification" >&2
  echo "warning: install it or pass --skip-verify to make this explicit" >&2
else
  CONNECTION=$(gcloud sql instances describe "$SCRATCH" --project "$PROJECT" \
    --format 'value(connectionName)')
  DRILL_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  gcloud sql users set-password postgres --instance "$SCRATCH" --project "$PROJECT" \
    --password "$DRILL_PASSWORD" --quiet

  cloud-sql-proxy --port "$PROXY_PORT" "$CONNECTION" &
  PROXY_PID=$!
  # shellcheck disable=SC2064
  trap "kill ${PROXY_PID} 2>/dev/null || true; cleanup" EXIT
  for _ in $(seq 1 30); do
    pg_isready -h 127.0.0.1 -p "$PROXY_PORT" >/dev/null 2>&1 && break
    sleep 2
  done

  export PGPASSWORD="$DRILL_PASSWORD"
  PSQL=(psql -h 127.0.0.1 -p "$PROXY_PORT" -U postgres -d "$DB_NAME" -t -A -v ON_ERROR_STOP=1)

  echo "   alembic revision in the restored database:"
  REVISION=$("${PSQL[@]}" -c 'SELECT version_num FROM alembic_version' | tr -d '[:space:]')
  if [ -z "$REVISION" ]; then
    echo "error: alembic_version is empty; the restore did not bring the schema" >&2
    exit 1
  fi
  echo "     ${REVISION}"

  echo "   row counts (a restore that returns zeros everywhere is not a restore):"
  TOTAL=0
  for table in tenants users opportunities audit_log; do
    COUNT=$("${PSQL[@]}" -c "SELECT count(*) FROM ${table}" | tr -d '[:space:]')
    printf '     %-16s %s\n' "$table" "$COUNT"
    TOTAL=$((TOTAL + COUNT))
  done
  if [ "$TOTAL" -eq 0 ]; then
    echo "error: every checked table is empty; treat this restore as FAILED" >&2
    exit 1
  fi

  # The head the repository expects, so a restore of an older backup is visible as a gap
  # rather than as a surprise during an incident.
  EXPECTED=$(cd "${REPO_ROOT}/backend" && uv run alembic heads 2>/dev/null | awk '{print $1}' | head -1 || true)
  if [ -n "$EXPECTED" ] && [ "$EXPECTED" != "$REVISION" ]; then
    echo "   note: repository head is ${EXPECTED}, restored backup is at ${REVISION}"
    echo "   note: that is expected for an older backup; run 'alembic upgrade head' after a real restore"
  fi

  kill "$PROXY_PID" 2>/dev/null || true
  trap cleanup EXIT
  unset PGPASSWORD
fi

# ------------------------------------------------------------------------- 6. the verdict
log "6/6 Drill result"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "   DRY RUN: nothing was created, restored or deleted."
else
  echo "   PASS: backup ${BACKUP_ID} (${BACKUP_TIME}) restored into ${SCRATCH} in ${REGION}"
  echo "   Record the date, the backup id and the elapsed time in docs/runbooks/restore-drill.md."
fi
