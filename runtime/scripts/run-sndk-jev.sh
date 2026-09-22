#!/usr/bin/env bash
# launchd-fired wrapper for the JEV decision service (record-only: nothing here trades).
# One tick = read the newest SNDK PRO diary row and what sits beside it, build the
# labels, ask JEV when a key is present, write state/jev/{day}.jsonl and
# state/jev/latest.json for the phone. Same gates as run-sndk.sh, so it is quiet
# outside market hours; a separate job from the scanner, never imported by it.
# Installed by install-launchd.sh (com.mirai-station.sndk-jev, :02 and :32).
# Kill switch: SNDK_JEV_DISABLE=1 => exit-0 no-op.
# The key is the service's business: it reads the git-ignored file in its own folder
# and runs unsent when there is none. Nothing about the key lives in this script.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SNDK_JEV_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/runtime"
set +e
"${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 3)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 3 ]]; then
  echo "sndk-jev :: market closed, skipping tick"
  exit 0
elif [[ $GATE_RC -ne 0 ]]; then
  echo "sndk-jev :: market-hours check FAILED (rc=${GATE_RC}), no tick" >&2
  exit 1
fi

# Read a finished row: wait until the newest diary row is at least 20 s old, so a tick that
# fires in the same second as the scanner's write never reads a half-written row.
ROWS="${MIRAI_STATION_ROOT}/state/sndk_reversion/$(date +%F).jsonl"
if [[ -f "$ROWS" ]]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$ROWS") ))
  if (( AGE < 20 )); then sleep $(( 20 - AGE )); fi
fi

cd "${MIRAI_STATION_ROOT}/skills/sndk-jev"
exec "${MIRAI_STATION_VENV}/bin/python" -m sndk_jev.service --state-dir "${MIRAI_STATION_ROOT}/state" --send
