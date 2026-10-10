#!/usr/bin/env bash
# launchd-fired wrapper for the spx-claude-forecast nightly job (record-only: nothing here trades).
# One run = seal what actually happened after every read of the day (graded the way the station grades,
# with the station's own forecasts copied beside), catch up any missed days, rebuild the library of past
# moments, score every forecaster, write the scorecard, mirror the folder off-disk. Writes only under
# state/spx_claude_forecast/ and the backup folder; reads every station store read-only.
# Installed by install-launchd.sh (com.mirai-station.spx-claude-forecast-nightly): 17:15 ET weekdays, after
# the 16:20 ET daily-close save and the 17:05 ET prediction nightly. The job itself refuses to seal a day
# before the close plus 75 minutes, so an early fire is harmless. A second copy stands down on the lock.
# Kill switches: SPX_CLAUDE_FORECAST_DISABLE=1 (this package) or SPX_JEV_DISABLE=1 => exit-0 no-op
# (checked here AND in python).
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SPX_CLAUDE_FORECAST_DISABLE:-0}" == "1" ]] && exit 0
[[ "${SPX_JEV_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/skills/spx-claude-forecast"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_claude_forecast.nightly --state-dir "${MIRAI_STATION_STATE}"
