#!/usr/bin/env bash
# One image, seven modes (SPEC 10.1 / 12: the SAME image runs the API, the workers and every
# per-adapter job in us-east1 and asia-south1; REGION decides bucket and DB).
#
#   api                 uvicorn app.main:app on $PORT            Cloud Run / Railway service
#   worker              celery worker (+ /healthz on $PORT)      Cloud Run / Railway service
#   beat                celery beat   (+ /healthz on $PORT)      Cloud Run / Railway service, max 1
#   job:<source_id>     python -m app.jobs.run_source <id>       Cloud Run job per adapter
#   migrate             bootstrap_db + alembic upgrade head      pre-deploy hook
#   seed                python -m app.seed                       one-off, first deploy
#   smoke               python -m app.jobs.smoke                 Cloud Run job, nightly
#
# Anything else is exec'd verbatim, so `docker run <image> bash` still works.
#
# The mode comes from the first argument, or from $BIDRADAR_MODE when there is none.
# Railway deploys all three backend services from ONE railway.json with no startCommand,
# so the only thing that differs between api, worker and beat there is that variable;
# Cloud Run keeps passing the mode as an argument (infra/cloudrun/services/*.yaml).
set -euo pipefail

MODE="${1:-${BIDRADAR_MODE:-api}}"
shift || true
PORT="${PORT:-8080}"

start_clamd() {
  if [ "${SCANNER_BACKEND:-noop}" = "clamav" ]; then
    /app/docker/clamd-start.sh &
  fi
}

start_health_server() {
  BIDRADAR_COMPONENT="$1" PORT="$PORT" python -m app.jobs.health_server &
}

run_migrations() {
    # Railway runs ONE preDeployCommand for every service that shares backend/railway.json,
    # so this mode has to be safe on worker and beat too: exactly one service (api) sets
    # RUN_MIGRATIONS=1 and owns the schema. Two concurrent `alembic upgrade head` runs
    # would race for the same lock and one of them would fail the deploy.
    if [ "${RUN_MIGRATIONS:-0}" != "1" ]; then
      echo "entrypoint: RUN_MIGRATIONS is not 1, skipping migrate for mode ${BIDRADAR_MODE:-api}"
      return 0
    fi
    # A managed Postgres (Railway, RDS, Neon) has no initdb hook, so the extensions and
    # the non-owner bidradar_app role that every RLS policy needs are created here, on
    # DATABASE_URL_OWNER, before the schema. Idempotent; BOOTSTRAP_DB=0 skips it once the
    # role exists and the deploy role no longer needs CREATE ROLE.
    if [ "${BOOTSTRAP_DB:-1}" = "1" ]; then
      python -m app.jobs.bootstrap_db
    fi
    alembic upgrade head "$@"
    # Internal tenant + platform admin (SPEC 3), idempotent. Its JSON line carries the
    # tenant uuid the front end needs for BIDRADAR_DEV_TENANT_ID (OQ-11); on Railway that
    # is read back out of `railway logs --service api --build`.
    if [ "${SEED_ON_START:-0}" = "1" ]; then
      python -m app.seed
    fi
    return 0
}

case "$MODE" in
  api)
    # Railway (CLI uploads) does not reliably run preDeployCommand; the schema owner
    # (RUN_MIGRATIONS=1, single replica) migrates on start. Idempotent.
    if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then run_migrations; fi
    start_clamd
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
      --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --timeout-keep-alive 65 "$@"
    ;;
  worker)
    start_clamd
    start_health_server worker
    exec celery -A app.celery_app worker \
      --loglevel "${CELERY_LOG_LEVEL:-info}" \
      --queues "${CELERY_QUEUES:-bidradar}" \
      --concurrency "${CELERY_CONCURRENCY:-4}" \
      --max-tasks-per-child "${CELERY_MAX_TASKS_PER_CHILD:-100}" "$@"
    ;;
  beat)
    start_health_server beat
    exec celery -A app.celery_app beat \
      --loglevel "${CELERY_LOG_LEVEL:-info}" \
      --schedule "${CELERY_BEAT_SCHEDULE_FILE:-/tmp/celerybeat-schedule}" "$@"
    ;;
  job:*)
    start_clamd
    exec python -m app.jobs.run_source "${MODE#job:}" "$@"
    ;;
  migrate)
    run_migrations "$@"
    exit 0
    ;;
  seed)
    # Run ONCE after the first migrate; prints a JSON line whose internal_tenant_id
    # becomes the front end's BIDRADAR_DEV_TENANT_ID (OQ-11).
    exec python -m app.seed "$@"
    ;;
  smoke)
    exec python -m app.jobs.smoke "$@"
    ;;
  *)
    exec "$MODE" "$@"
    ;;
esac
