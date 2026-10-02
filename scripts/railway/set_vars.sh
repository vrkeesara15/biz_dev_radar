#!/usr/bin/env bash
# Push one filled-in env file onto one Railway service, in a single call.
#
#   scripts/railway/set_vars.sh <service> <env-file> [--apply]
#
# Without --apply it prints the command it WOULD run, with every value masked, and
# changes nothing. That default is deliberate: this script's whole input is secrets.
#
# The file format is the one in infra/railway/env/*.env.example — KEY=VALUE, one per
# line, `#` comments and blank lines ignored, no `export`, no quoting rules beyond
# "everything after the first = is the value". Railway reference expressions such as
# ${{Postgres.DATABASE_URL}} are passed through VERBATIM; the platform resolves them at
# deploy time, so this script must not let the shell expand them (hence no eval and
# single-quoted heredocs everywhere).
#
# One `railway variables --set ... --set ...` call sets everything at once, which means
# ONE redeploy rather than one per variable. --skip-deploys suppresses even that; the
# runbook deploys explicitly afterwards.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: scripts/railway/set_vars.sh <service> <env-file> [--apply]

  <service>   railway service name, e.g. api | worker | beat | frontend
  <env-file>  KEY=VALUE file, e.g. infra/railway/env/api.env
  --apply     actually call `railway variables`; omit for a masked dry run

Link the project first:  railway link
USAGE
  exit 2
}

[ $# -ge 2 ] || usage
SERVICE="$1"
ENV_FILE="$2"
APPLY="${3:-}"

[ -f "$ENV_FILE" ] || { echo "no such env file: $ENV_FILE" >&2; exit 1; }
command -v railway >/dev/null || { echo "the railway CLI is not on PATH" >&2; exit 1; }

args=()
masked=()
placeholders=()
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  [ "${line#*=}" != "$line" ] || continue       # no '=' on the line: not a variable
  key="${line%%=*}"
  value="${line#*=}"
  key="${key#"${key%%[![:space:]]*}"}"          # ltrim
  key="${key%"${key##*[![:space:]]}"}"          # rtrim
  case "$value" in
    *'<'*'>'*) placeholders+=("$key") ;;        # still has a <fill me in>
  esac
  args+=(--set "$key=$value")
  case "$value" in
    '${{'*) masked+=("$key=$value") ;;          # a reference is not a secret
    *)      masked+=("$key=********") ;;
  esac
done < "$ENV_FILE"

[ ${#args[@]} -gt 0 ] || { echo "$ENV_FILE defines no variables" >&2; exit 1; }

if [ ${#placeholders[@]} -gt 0 ]; then
  printf 'refusing: these still contain a <placeholder>: %s\n' "${placeholders[*]}" >&2
  echo "fill them in (or delete the line) and run again" >&2
  exit 1
fi

if [ "$APPLY" != "--apply" ]; then
  echo "DRY RUN — would set $((${#args[@]} / 2)) variables on service '$SERVICE':"
  printf '  %s\n' "${masked[@]}"
  echo
  echo "re-run with --apply to send them."
  exit 0
fi

echo "setting $((${#args[@]} / 2)) variables on service '$SERVICE' ..." >&2
railway variables --service "$SERVICE" --skip-deploys "${args[@]}"
echo "done. deploy with: railway up ./<backend|frontend> --path-as-root --service $SERVICE --detach" >&2
