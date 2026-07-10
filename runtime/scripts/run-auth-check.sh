#!/usr/bin/env bash
# launchd-fired wrapper: one Schwab login keep-alive check.
# Reads the token's age and pings the phone to re-auth if it's near the 7-day wall.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

cd "${MIRAI_STATION_ROOT}/runtime"
exec "${MIRAI_STATION_VENV}/bin/python" -m watch.intraday.auth_check
