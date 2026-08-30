#!/usr/bin/env bash
# launchd-fired wrapper: the SNDK scanner's dead-man's switch.
#
# Deliberately NOT gated on the market-status helper the way run-sndk-read.sh is.
# That gate lives inside sndk_deadman.run() instead, because the wrapper's job
# here is to guarantee the check RUNS — a shell gate that exits 0 on a bad clock
# would silence the watchdog with no trace, which is the failure this job exists
# to catch one layer down. Weekends are cheap enough to skip in the shell.
#
# It shares no process, no import and no timer with the scanner it watches: if
# the left-eye tick or the SNDK scanner is what died, this job is unaffected.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/runtime"
exec "${MIRAI_STATION_VENV}/bin/python" -m watch.intraday.sndk_deadman "$@"
