#!/usr/bin/env bash
# launchd-fired wrapper for the SPX JEV premarket lane (record-only: nothing here trades).
# One run = one checkpoint before the open: save the night's overnight futures, build the night's labels
# on a scene without a diary row, ask JEV the questions due there when a key is present, and write
# state/spx_jev/lanes/premarket/ (its records, sums and card) and the archive (spx_jev/premarket.py).
# The 10:06 ET fire is the close-out: it asks JEV nothing and grades the morning's calls from the
# settled open. REST only, through the station's shared client; the lob-flow streamer is never touched.
# No market-hours gate: it runs before the open by design. The command itself reads only on market
# days, only within 5 minutes after one of the day's checkpoints, and never from the open on, so a
# holiday, a late fire after the Mac slept, or the 04:35 fire outside Frankfurt's winter-time week
# exits 0 having written nothing.
# Installed by install-launchd.sh (com.mirai-station.spx-jev-premarket). Its 02:35 ET fire is 23:35
# on the box's Pacific clock the evening before, so the weekday is checked in New York time.
# Kill switch: SPX_JEV_DISABLE=1 => exit-0 no-op.
# The key is the service's business: it reads the git-ignored file in the skill's folder and runs
# unsent when there is none. Nothing about the key lives in this script.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SPX_JEV_DISABLE:-0}" == "1" ]] && exit 0
[[ $(TZ=America/New_York date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/skills/spx-jev"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.premarket --state-dir "${MIRAI_STATION_ROOT}/state" --send
