#!/usr/bin/env bash
# launchd-fired wrapper for the spx-claude-forecast recorder (record-only: nothing here trades).
# One run = copy station data that would otherwise be overwritten or deleted (the dated options book, siege's
# SPY minute volumes, the raw options tape, the $VIX1D close, SPX 5-minute bars from Schwab) into
# state/spx_claude_forecast/recorder/, then mirror the forecast folder off-disk. Every task copies only
# what is missing. Reads every station store read-only; writes only under its own folder and the backup.
# Installed by install-launchd.sh (com.mirai-station.spx-claude-forecast-recorder): 08:40 ET after the
# dated-book fetch, 16:25 ET after the close, and Friday 17:30 ET after the Friday book refresh.
# The Schwab task refuses to run inside market hours, so the live feeds keep the login to themselves.
# Kill switches: SPX_CLAUDE_FORECAST_DISABLE=1 (this package) or SPX_JEV_DISABLE=1 => exit-0 no-op.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SPX_CLAUDE_FORECAST_DISABLE:-0}" == "1" ]] && exit 0
[[ "${SPX_JEV_DISABLE:-0}" == "1" ]] && exit 0

cd "${MIRAI_STATION_ROOT}/skills/spx-claude-forecast"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_claude_forecast.recorder --state-dir "${MIRAI_STATION_STATE}"
