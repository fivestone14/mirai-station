#!/usr/bin/env bash
# launchd-fired wrapper: after the close, save the day's full 1-minute bars.
# One run = every market-feed symbol's whole session to state/spx_jev/context/bars/{date}.jsonl,
# and SPX's own session to state/spx_jev/bars/{date}.jsonl when that file is short
# (spx_jev/save_day.py), for today and any market day of the last week not yet on disk. REST only,
# through the station's shared client; the lob-flow streamer is never touched. Idempotent: a day on
# disk is never fetched again.
# No market-hours gate: it runs after the close by design. The command itself saves market days
# only, and only a session that has closed.
# Installed by install-launchd.sh (com.mirai-station.spx-jev-save-day, 16:20 ET).
# Kill switch: SPX_JEV_DISABLE=1 => exit-0 no-op.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SPX_JEV_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/skills/spx-jev"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.save_day --state-dir "${MIRAI_STATION_ROOT}/state"
