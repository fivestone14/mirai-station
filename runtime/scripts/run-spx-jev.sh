#!/usr/bin/env bash
# launchd-fired wrapper for the SPX JEV decision service (record-only: nothing here trades).
# One tick = read the newest SPX diary row (state/reversion/, the left-eye scanner's) and what sits
# beside it, build the labels, ask JEV when a key is present, write state/spx_jev/{day}.jsonl and
# state/spx_jev/latest.json for the phone. Same gates as run-sndk-jev.sh, so it is quiet outside
# market hours; a separate job from the scanner, never imported by it.
# Staged, not installed: the plists are templates under skills/spx-jev/launchd/
# (com.mirai-station.spx-jev at :02 and :32; com.mirai-station.spx-jev-tape every 5 minutes 09:35 to
# 10:30 ET, which runs this script with `--lane tape` and writes under state/spx_jev/lanes/tape/ only).
# Kill switch: SPX_JEV_DISABLE=1 => exit-0 no-op.
# The key is the service's business: it reads the git-ignored file in its own folder
# and runs unsent when there is none. Nothing about the key lives in this script.
set -u
LANE="live"
if [[ "${1:-}" == "--lane" ]]; then LANE="${2:?run-spx-jev.sh: --lane needs a lane name}"; fi
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
  echo "spx-jev :: market closed, skipping tick"
  exit 0
elif [[ $GATE_RC -ne 0 ]]; then
  echo "spx-jev :: market-hours check FAILED (rc=${GATE_RC}), no tick" >&2
  exit 1
fi

# Read a finished row: wait until the newest diary row is at least 20 s old, so a tick that
# fires in the same second as the scanner's write never reads a half-written row. GNU stat
# (a Linux box) takes -c; the Mac's BSD stat refuses it and takes -f.
ROWS="${MIRAI_STATION_ROOT}/state/reversion/$(date +%F).jsonl"
if [[ -f "$ROWS" ]]; then
  if stat -c %Y "$ROWS" >/dev/null 2>&1; then MTIME=$(stat -c %Y "$ROWS"); else MTIME=$(stat -f %m "$ROWS"); fi
  AGE=$(( $(date +%s) - MTIME ))
  if (( AGE < 20 )); then sleep $(( 20 - AGE )); fi
fi

cd "${MIRAI_STATION_ROOT}/skills/spx-jev"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.service --state-dir "${MIRAI_STATION_ROOT}/state" --send --lane "$LANE"
