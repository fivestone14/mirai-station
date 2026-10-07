#!/usr/bin/env bash
# launchd-fired wrapper: the headline feed, one poll every 3 minutes.
# One run = four public RSS GETs (CNBC Top News, Google News search, MarketWatch Top Stories, the Fed's press
# feed), each new item appended to state/spx_jev/headlines/{date}.jsonl stamped with the poll's own clock
# (spx_jev/headlines.py). Stdlib only; no Schwab, no key. The poll gates itself to 07:00-16:30 ET on weekdays
# (`--now` runs it regardless, by hand), and a lock file keeps two polls from interleaving.
# Installed by install-launchd.sh (com.mirai-station.spx-jev-headlines, every 180 s).
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
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.headlines --state-dir "${MIRAI_STATION_ROOT}/state"
