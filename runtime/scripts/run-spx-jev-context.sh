#!/usr/bin/env bash
# launchd-fired wrapper: the market around SPX, one snapshot a minute.
# One run = one batched Schwab quote call for the volatility, futures, rates, sector, index-fund
# and megacap symbols, and the newest finished 1-minute bar for each NYSE breadth symbol, appended
# as one line to state/spx_jev/context/{date}.jsonl (spx_jev/market_context.py). REST only, through
# the station's shared client; the lob-flow streamer is never touched.
# Gated on the market-status helper: market hours only.
# Staged, not installed: skills/spx-jev/launchd/com.mirai-station.spx-jev-context.plist.template.
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
"${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 3)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 3 ]]; then
  echo "spx-jev-context :: market closed, skipping"
  exit 0
elif [[ $GATE_RC -ne 0 ]]; then
  echo "spx-jev-context :: market-hours check FAILED (rc=${GATE_RC}), no snapshot" >&2
  exit 1
fi

cd "${MIRAI_STATION_ROOT}/skills/spx-jev"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.market_context --state-dir "${MIRAI_STATION_ROOT}/state"
