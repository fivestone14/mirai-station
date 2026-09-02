#!/usr/bin/env bash
# launchd-fired wrapper: SNDK-PRO scan tick (record-only: nothing here trades).
# One tick = pull the SNDK weekly chain (240s-TTL cached inside python), compute
# gex/dex/ladder/EM views via the pure left-eye engines, append one diary row to
# state/sndk_reversion/{date}.jsonl for the viewstation's /api/raw readers.
# Gated on the authoritative market-status (RTH only) — the python side gates
# again (defense in depth; sndk_hunter.py --force bypasses for manual runs).
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
if ! "${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 1)"; then
  echo "sndk :: market closed — skipping tick"
  exit 0
fi

cd "${MIRAI_STATION_ROOT}/skills/sndk-pro"
exec "${MIRAI_STATION_VENV}/bin/python" sndk_hunter.py
