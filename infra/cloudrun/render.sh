#!/usr/bin/env bash
# Substitute the ${PLACEHOLDER}s in the generated manifests and print (or apply) them.
#
#   ./infra/cloudrun/render.sh services/api.yaml            # to stdout
#   ./infra/cloudrun/render.sh --all --out /tmp/rendered    # every manifest to a directory
#
# Terraform (infra/terraform) is the authoritative deployer; this exists for a one-off
# `gcloud run services replace` and to eyeball what a given environment resolves to.
# Every variable listed below must be exported, or envsubst would silently blank it.
set -euo pipefail

VARS=(
  APP_ENV BACKEND_IMAGE CLOUD_SQL_INSTANCE CORS_ORIGINS FRONTEND_IMAGE GCP_REGION
  GCS_BUCKET_IN GCS_BUCKET_US NEXT_PUBLIC_API_BASE_URL REGION SECRET_PREFIX
  SERVICE_ACCOUNT VPC_CONNECTOR
)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALL=0
OUT=""
FILES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --all) ALL=1 ;;
    --out) OUT="$2"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) FILES+=("$1") ;;
  esac
  shift
done

missing=()
for v in "${VARS[@]}"; do
  if [ -z "${!v:-}" ]; then missing+=("$v"); fi
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "error: unset placeholders: ${missing[*]}" >&2
  exit 2
fi

SUBST=""
for v in "${VARS[@]}"; do SUBST="$SUBST \$$v"; done

if [ "$ALL" -eq 1 ]; then
  while IFS= read -r f; do FILES+=("${f#"$HERE"/}"); done \
    < <(find "$HERE" -name '*.yaml' | sort)
fi
if [ ${#FILES[@]} -eq 0 ]; then
  echo "error: name a manifest or pass --all" >&2
  exit 2
fi

for rel in "${FILES[@]}"; do
  src="$HERE/${rel#"$HERE"/}"
  if [ -n "$OUT" ]; then
    mkdir -p "$OUT/$(dirname "$rel")"
    envsubst "$SUBST" < "$src" > "$OUT/$rel"
    echo "$OUT/$rel"
  else
    envsubst "$SUBST" < "$src"
  fi
done
