#!/usr/bin/env bash
# launchd-fired wrapper: the morning Macro-Mood brief (BETA shadow layer).
# Sweeps cross-sector news/sentiment and writes the day's market_expectation
# (direction/magnitude + per-sector + reasoning + embedding) the tick loop reads.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

# Weekends are not trading days — exit cleanly without spending an opus call.
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/runtime"
exec "${MIRAI_STATION_VENV}/bin/python" -m watch.cli macro-brief
