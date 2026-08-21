#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
RUNTIME_DIR="${PROJECT_DIR}/.runtime"

mkdir -p "${RUNTIME_DIR}"
exec >>"${RUNTIME_DIR}/web.stdout.log" 2>>"${RUNTIME_DIR}/web.stderr.log"

cd "${PROJECT_DIR}"

if [[ ! -x "${PROJECT_DIR}/.venv/bin/eligibility-web" ]]; then
  print -u2 "Missing executable: ${PROJECT_DIR}/.venv/bin/eligibility-web"
  exit 1
fi

if [[ -f "${PROJECT_DIR}/.env" ]]; then
  set -a
  source "${PROJECT_DIR}/.env"
  set +a
fi

# app.main gives PORT precedence over WEB_PORT. A stale PORT in .env would make
# the LaunchAgent listen somewhere other than the explicitly installed port.
unset PORT
export WEB_HOST="${WEB_HOST:-127.0.0.1}"
export WEB_PORT="${WEB_PORT:-57949}"

exec "${PROJECT_DIR}/.venv/bin/eligibility-web"
