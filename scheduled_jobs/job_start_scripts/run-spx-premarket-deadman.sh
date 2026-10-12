#!/usr/bin/env bash
# launchd-fired wrapper: the dead-man's switch for the SPX JEV pre-market lane.
#
# Every 5 minutes it checks that each of the lane's checkpoints owed by now has its read on file and
# that the 10:06 ET close-out landed with no call left ungraded, and pages the phone once per miss
# (watch/intraday/spx_premarket_deadman.py). The market-day and time-of-day gates live inside run(), so the check
# always runs; the shell skips only the New York weekend, as the lane's own run script does, and the
# lane's kill switch, under which the lane reads nothing on purpose.
#
# It shares no process, no import and no timer with the lane it watches: if the pre-market job is
# what died, this job is unaffected.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SPX_JEV_DISABLE:-0}" == "1" ]] && exit 0
[[ $(TZ=America/New_York date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/runtime"
exec "${MIRAI_STATION_VENV}/bin/python" -m watch.intraday.spx_premarket_deadman "$@"
