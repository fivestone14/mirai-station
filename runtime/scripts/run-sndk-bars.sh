#!/usr/bin/env bash
# launchd-fired wrapper: the SNDK minute-bar SIDECAR (09-02).
# One run = fetch today's 1-min session bars from Schwab (one call, the whole
# session) and append every COMPLETED minute not already on disk to
# state/sndk_bars/{date}.jsonl. Idempotent, self-healing: a run after a gap
# writes the gap. Own clock, own process — the scanner and the reader never wait
# on it, and it never waits on them.
# Gated on the market-status helper like run-sndk.sh, PLUS a twelve-minute
# window after the close so the last bars of the day (15:59 completes at 16:00)
# still land. Kill switch: SNDK_PRO_DISABLE=1 => exit-0 no-op.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SNDK_PRO_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/runtime"
HHMM="$(TZ=America/New_York date +%H%M)"
if ! "${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 1)"; then
  if [[ "$HHMM" -lt 1600 || "$HHMM" -gt 1612 ]]; then
    echo "sndk-bars :: market closed — skipping"
    exit 0
  fi
fi

cd "${MIRAI_STATION_ROOT}/skills/sndk-pro"
exec "${MIRAI_STATION_VENV}/bin/python" sndk_bars.py
