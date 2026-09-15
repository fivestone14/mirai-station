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
# Only the gate's own "closed" (exit 3) skips quietly. Python exits 1 on any
# uncaught exception, so with "closed" on 1 a broken venv or import read as a
# closed market and launchd reported success for bars that never landed.
set +e
"${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 3)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 3 ]]; then
  # 10# forces decimal: "0929" is not octal, and without it every 08xx/09xx
  # minute with an 8 or 9 in it errored and the gate fell open (722 lines in
  # the err log before this was found on Sep 5)
  if [[ "10#$HHMM" -lt 1600 || "10#$HHMM" -gt 1612 ]]; then
    echo "sndk-bars :: market closed — skipping"
    exit 0
  fi
elif [[ $GATE_RC -ne 0 ]]; then
  echo "sndk-bars :: market-hours check FAILED (rc=${GATE_RC}) — check venv/imports; no bars" >&2
  exit 1
fi

cd "${MIRAI_STATION_ROOT}/skills/sndk-pro"
exec "${MIRAI_STATION_VENV}/bin/python" sndk_bars.py
