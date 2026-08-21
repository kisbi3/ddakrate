#!/bin/zsh
set -u

LABEL="com.ddakrate.web"
USER_ID="$(id -u)"
PORT_VALUE="${1:-57949}"

if launchctl print "gui/${USER_ID}/${LABEL}" >/dev/null 2>&1; then
  DOMAIN="gui/${USER_ID}"
elif launchctl print "user/${USER_ID}/${LABEL}" >/dev/null 2>&1; then
  DOMAIN="user/${USER_ID}"
else
  print "${LABEL}: not loaded"
  exit 1
fi

launchctl print "${DOMAIN}/${LABEL}" | sed -n '1,34p'
print ""
if curl --silent --show-error --fail --max-time 3 \
  "http://127.0.0.1:${PORT_VALUE}/healthz"; then
  print "\nHTTP health: OK"
else
  print "\nHTTP health: FAILED"
  exit 1
fi
