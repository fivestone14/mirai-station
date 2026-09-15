#!/usr/bin/env bash
# launchd-fired wrapper: SNDK-PRO scan tick (record-only: nothing here trades).
# One tick = pull the SNDK weekly chain (disk-cached for sndk_feed._CHAIN_TTL_S
# inside python), compute gex/dex/ladder/EM views via the pure left-eye engines,
# append one diary row to state/sndk_reversion/{date}.jsonl for the viewstation's
# /api/raw readers.
# Gated on the authoritative market-status (RTH only) — the python side gates
# again (defense in depth; sndk_hunter.py --force bypasses for manual runs).
# A gate that cannot answer exits 1 with one line on stderr; only its own
# "closed" (exit 3) skips quietly.
# Kill switch: SNDK_PRO_DISABLE=1 => exit-0 no-op (checked here AND in python).
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

# Kill switch + weekends (the RTH gate proper lives in market_status).
[[ "${SNDK_PRO_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/runtime"
# Not exit 1 for "closed": Python exits 1 on any uncaught exception, so a broken
# venv or import read as a closed market and launchd reported success for a
# session that never wrote a row.
set +e
"${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 3)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 3 ]]; then
  echo "sndk :: market closed — skipping tick"
  exit 0
elif [[ $GATE_RC -ne 0 ]]; then
  echo "sndk :: market-hours check FAILED (rc=${GATE_RC}) — check venv/imports; no tick" >&2
  exit 1
fi

cd "${MIRAI_STATION_ROOT}/skills/sndk-pro"
exec "${MIRAI_STATION_VENV}/bin/python" sndk_hunter.py
