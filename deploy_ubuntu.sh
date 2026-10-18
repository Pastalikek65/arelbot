#!/usr/bin/env bash

# Arelbot production deployment for a Linux VDS.
#
# The production topology is deliberately the same as docker-compose.yml:
# MongoDB and Redis are dependencies, `bot` receives Telegram updates and
# `worker` owns media/AI/render jobs. This script performs a safe preflight
# before changing containers and never prints the contents of .env.

set -euo pipefail

APP_DIR="${ARELBOT_APP_DIR:-/opt/arel}"
ENV_FILE="${ARELBOT_ENV_FILE:-${APP_DIR}/.env}"
COMPOSE_FILE="${ARELBOT_COMPOSE_FILE:-${APP_DIR}/docker-compose.yml}"
DRY_RUN="false"

usage() {
    cat <<'EOF'
Usage: deploy_ubuntu.sh [--dry-run]

Required production environment values are validated without being printed:
TELEGRAM_TOKEN, OWNER_ID, APP_ENV=production, TELEGRAM_MODE=webhook,
WEBHOOK_URL, WEBHOOK_SECRET and (when enabled) ENCRYPTION_KEY.

Overrides:
  ARELBOT_APP_DIR       application directory (default: /opt/arel)
  ARELBOT_ENV_FILE      dotenv path (default: $ARELBOT_APP_DIR/.env)
  ARELBOT_COMPOSE_FILE  compose path (default: $ARELBOT_APP_DIR/docker-compose.yml)
EOF
}

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="true" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! -d "$APP_DIR" || ! -f "$ENV_FILE" || ! -f "$COMPOSE_FILE" ]]; then
    echo "deployment files are missing" >&2
    exit 10
fi

cd "$APP_DIR"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "dry_run=ok app_dir=${APP_DIR} compose_profile=prod"
    echo "would_run=python3 scripts/production_preflight.py --env-file ${ENV_FILE} --compose-file ${COMPOSE_FILE}"
    echo "would_run=docker compose --env-file ${ENV_FILE} -f ${COMPOSE_FILE} --profile prod up -d --build"
    echo "would_run=docker compose --env-file ${ENV_FILE} -f ${COMPOSE_FILE} --profile prod ps"
    exit 0
fi

python3 scripts/production_preflight.py \
    --env-file "$ENV_FILE" \
    --compose-file "$COMPOSE_FILE"

docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    --profile prod \
    up -d --build

docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    --profile prod \
    ps

echo "deployment=ok profile=prod"
