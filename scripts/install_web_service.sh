#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
SOURCE_PLIST="${PROJECT_DIR}/ops/com.ddakrate.web.plist"
TARGET_DIR="${HOME}/Library/LaunchAgents"
TARGET_PLIST="${TARGET_DIR}/com.ddakrate.web.plist"
LABEL="com.ddakrate.web"
WEB_PORT_VALUE="${1:-57949}"
USER_ID="$(id -u)"

if [[ ! "${WEB_PORT_VALUE}" =~ '^[0-9]+$' ]] || (( WEB_PORT_VALUE < 1024 || WEB_PORT_VALUE > 65535 )); then
  print -u2 "Usage: $0 [port between 1024 and 65535]"
  exit 2
fi

if [[ ! -x "${PROJECT_DIR}/.venv/bin/eligibility-web" ]]; then
  print -u2 "Run the project setup first: ${PROJECT_DIR}/.venv/bin/eligibility-web is missing."
  exit 1
fi

mkdir -p "${TARGET_DIR}" "${PROJECT_DIR}/.runtime"
cp "${SOURCE_PLIST}" "${TARGET_PLIST}"
/usr/libexec/PlistBuddy -c \
  "Set :ProgramArguments:0 ${PROJECT_DIR}/scripts/run_web_background.sh" \
  "${TARGET_PLIST}"
/usr/libexec/PlistBuddy -c \
  "Set :EnvironmentVariables:WEB_PORT ${WEB_PORT_VALUE}" \
  "${TARGET_PLIST}"
/usr/bin/plutil -lint "${TARGET_PLIST}" >/dev/null

if launchctl print "gui/${USER_ID}" >/dev/null 2>&1; then
  DOMAIN="gui/${USER_ID}"
else
  DOMAIN="user/${USER_ID}"
fi

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "${DOMAIN}/${LABEL}"
  # bootout is asynchronous on recent macOS releases. Re-bootstrapping the same
  # label before teardown completes produces an opaque "Bootstrap failed: 5".
  for _attempt in {1..50}; do
    if ! launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
fi
launchctl bootstrap "${DOMAIN}" "${TARGET_PLIST}"
launchctl enable "${DOMAIN}/${LABEL}"
launchctl kickstart -k "${DOMAIN}/${LABEL}"

print "Installed ${LABEL} in ${DOMAIN}"
print "Web:   http://127.0.0.1:${WEB_PORT_VALUE}/"
print "Debug: http://127.0.0.1:${WEB_PORT_VALUE}/debug"
print "Logs:  ${PROJECT_DIR}/.runtime/web.stdout.log"
