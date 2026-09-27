#!/usr/bin/env bash
# One image, six modes (SPEC 10.1 / 12: the SAME image runs the API, the workers and every
# per-adapter Cloud Run job in us-east1 and asia-south1; REGION decides bucket and DB).
#
#   api                 uvicorn app.main:app on $PORT            Cloud Run service
#   worker              celery worker (+ /healthz on $PORT)      Cloud Run service
#   beat                celery beat   (+ /healthz on $PORT)      Cloud Run service, max 1
#   job:<source_id>     python -m app.jobs.run_source <id>       Cloud Run job per adapter
#   migrate             alembic upgrade head                     Cloud Run job, pre-deploy
#   smoke               python -m app.jobs.smoke                 Cloud Run job, nightly
#
# Anything else is exec'd verbatim, so `docker run <image> bash` still works.
set -euo pipefail

MODE="${1:-api}"
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

case "$MODE" in
  api)
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
    exec alembic upgrade head "$@"
    ;;
  smoke)
    exec python -m app.jobs.smoke "$@"
    ;;
  *)
    exec "$MODE" "$@"
    ;;
esac
