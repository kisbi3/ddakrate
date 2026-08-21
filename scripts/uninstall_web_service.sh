#!/bin/zsh
set -u

LABEL="com.ddakrate.web"
USER_ID="$(id -u)"
TARGET_PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

for DOMAIN in "gui/${USER_ID}" "user/${USER_ID}"; do
  launchctl bootout "${DOMAIN}/${LABEL}" >/dev/null 2>&1 || true
done

if [[ -f "${TARGET_PLIST}" ]]; then
  rm -f "${TARGET_PLIST}"
fi

print "Uninstalled ${LABEL}. Runtime logs were kept."
