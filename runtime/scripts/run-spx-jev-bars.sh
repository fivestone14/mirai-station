#!/usr/bin/env bash
# launchd-fired wrapper: the SPX live-bars sidecar.
# One run = fetch today's 1-min SPX session from Schwab (one call, the whole session) and append
# every FINISHED minute not already on disk to state/spx_jev/bars/{date}.jsonl. Idempotent and
# self-healing: a run after a gap writes the gap. Its own clock and process; the SPX JEV service
# reads the file, and the scanner never waits on it.
# Gated on the market-status helper like run-sndk-bars.sh, plus the twelve minutes after the day's
# real close (13:00 on a half day, none on a holiday), so the last bar of the day (15:59 finishes at
# 16:00) still lands: the gate answers 4 when the market was live 13 minutes ago and is closed now.
# Installed by install-launchd.sh (com.mirai-station.spx-jev-bars, every 60 s).
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

cd "${MIRAI_STATION_ROOT}/runtime"
set +e
"${MIRAI_STATION_VENV}/bin/python" -c "
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from watch.intraday import market_status as m
now = datetime.now(ZoneInfo('America/New_York'))
sys.exit(0 if m.check(now).is_live else 4 if m.check(now - timedelta(minutes=13)).is_live else 3)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 3 ]]; then
  echo "spx-jev-bars :: market closed, skipping"
  exit 0
elif [[ $GATE_RC -eq 4 ]]; then
  echo "spx-jev-bars :: after the close, fetching the day's last bars"
elif [[ $GATE_RC -ne 0 ]]; then
  echo "spx-jev-bars :: market-hours check FAILED (rc=${GATE_RC}), no bars" >&2
  exit 1
fi

cd "${MIRAI_STATION_ROOT}/skills/spx-jev"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.bars --state-dir "${MIRAI_STATION_ROOT}/state"
