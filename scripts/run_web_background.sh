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
  # Treat dotenv values as data, not shell source. Encoded service keys can
  # legitimately contain '&' and other shell metacharacters.
  while IFS= read -r dotenv_line || [[ -n "${dotenv_line}" ]]; do
    [[ -z "${dotenv_line}" || "${dotenv_line}" == \#* ]] && continue
    [[ "${dotenv_line}" != *=* ]] && continue
    dotenv_key="${dotenv_line%%=*}"
    dotenv_value="${dotenv_line#*=}"
    if [[ ! "${dotenv_key}" =~ '^[A-Za-z_][A-Za-z0-9_]*$' ]]; then
      print -u2 "Ignoring invalid dotenv key"
      continue
    fi
    if [[ "${dotenv_value}" == \"*\" || "${dotenv_value}" == \'*\' ]]; then
      dotenv_value="${dotenv_value:1:-1}"
    fi
    export "${dotenv_key}=${dotenv_value}"
  done < "${PROJECT_DIR}/.env"
fi

# app.main gives PORT precedence over WEB_PORT. A stale PORT in .env would make
# the LaunchAgent listen somewhere other than the explicitly installed port.
unset PORT
export WEB_HOST="${WEB_HOST:-127.0.0.1}"
export WEB_PORT="${WEB_PORT:-57949}"

exec "${PROJECT_DIR}/.venv/bin/eligibility-web"
