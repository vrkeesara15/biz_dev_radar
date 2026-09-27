#!/usr/bin/env bash
# Start clamd inside the api/worker container (SPEC 11: ClamAV scan before parsing).
#
# Virus definitions are NOT baked into the image: they are ~300 MB and stale the day the
# image is built, so freshclam fetches them on first start into /var/lib/clamav (an
# emptyDir on Cloud Run, so once per container). Both steps are best-effort: if clamd
# never comes up, app/services/scanner.py raises ScannerUnavailableError and uploads fail
# CLOSED with 503 — which is the behaviour we want, not a crash-looping container.
set -uo pipefail

if [ "${SCANNER_BACKEND:-noop}" != "clamav" ]; then
  echo "clamd: SCANNER_BACKEND=${SCANNER_BACKEND:-noop}, not starting" >&2
  exit 0
fi

CLAMAV_DB_DIR="${CLAMAV_DB_DIR:-/var/lib/clamav}"
mkdir -p "$CLAMAV_DB_DIR" /run/clamav
chown -R "$(id -u):$(id -g)" "$CLAMAV_DB_DIR" /run/clamav 2>/dev/null || true

if ! ls "$CLAMAV_DB_DIR"/*.c[vl]d >/dev/null 2>&1; then
  echo "clamd: fetching virus definitions (first start)" >&2
  freshclam --quiet --datadir="$CLAMAV_DB_DIR" --stdout || \
    echo "clamd: freshclam failed; uploads will fail closed until it succeeds" >&2
fi

echo "clamd: starting on ${CLAMAV_HOST:-127.0.0.1}:${CLAMAV_PORT:-3310}" >&2
exec clamd --foreground --config-file=/etc/clamav/clamd.conf
