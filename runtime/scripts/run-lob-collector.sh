#!/usr/bin/env bash
# launchd-fired supervisor tick for the Layer-2 LOB (FLOW) collector daemon.
# StartInterval=60: every minute this checks market hours and, if the collector
# is not already running (flock inside the daemon), launches it. A crashed
# collector therefore self-heals within a minute; outside RTH this exits
# immediately. The daemon itself self-terminates shortly after the close.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

cd "${MIRAI_STATION_ROOT}/runtime"

# Market-hours gate (same authoritative check the left-eye runner trusts).
# Only a CLEAN "not live" (exit 1) skips quietly — a broken venv/import (any
# other exit code) must scream in the launchd log, not read as "market closed".
set +e
"${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 1)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 1 ]]; then
  exit 0
elif [[ $GATE_RC -ne 0 ]]; then
  log "lob-collector: market gate FAILED (rc=${GATE_RC}) — check venv/imports"
  exit 1
fi

cd "${MIRAI_STATION_ROOT}/skills/mirai-left-eye"
exec "${MIRAI_STATION_VENV}/bin/python" lob_bridge.py --run-collector
