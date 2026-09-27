#!/usr/bin/env bash
# launchd-fired wrapper: save the overnight futures bars.
# One run = /ES, /ZN, /BTC and /MBT 1- and 5-minute bars from the prior close to now, merged into
# state/spx_jev/overnight/{date}.jsonl for the night in progress and every night of the last week,
# a manifest line for each night that changed, and the roll table re-detected
# (spx_jev/overnight.py). REST only, through the station's shared client; the lob-flow streamer is
# never touched. Idempotent: a bar on disk is never written twice.
# No market-hours gate: it runs at 09:26 ET, before the open, by design. The command itself saves
# on market days only.
# Installed by install-launchd.sh (com.mirai-station.spx-jev-overnight, 09:26 and 16:20 ET).
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
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.overnight --state-dir "${MIRAI_STATION_ROOT}/state"
