#!/usr/bin/env bash
# launchd-fired wrapper for the JEV decision service (record-only: nothing here trades).
# One tick = read the newest SNDK PRO diary row and what sits beside it, build the
# labels, ask JEV when a key is present, write state/jev/{day}.jsonl and
# state/jev/latest.json for the phone. Mirrors run-sndk.sh's gates so it is quiet
# outside market hours, but it is a separate job and never imported by the scanner.
# Kill switch: SNDK_JEV_DISABLE=1 => exit-0 no-op.
# Not installed by anything: copy launchd/com.mirai-station.sndk-jev.plist.template
# into runtime/launchd/ and run runtime/scripts/install-launchd.sh yourself.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SKILL_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
STATION_ROOT="$(cd "${SKILL_DIR}/../.." && pwd)"
set +e
source "${STATION_ROOT}/runtime/scripts/env.sh"
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

# Read a finished row: wait until the newest diary row is at least 20 s old (the scanner
# appends a row in one write, but a tick that fires in the same second could read the
# previous row and answer it twice).
STATE_DIR="${MIRAI_STATION_ROOT}/state"
DAY="$(date +%F)"
ROWS="${STATE_DIR}/sndk_reversion/${DAY}.jsonl"
if [[ -f "$ROWS" ]]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$ROWS") ))
  if (( AGE < 20 )); then sleep $(( 20 - AGE )); fi
fi

# The key: from the environment if already there, else from the git-ignored .env beside this script.
if [[ -z "${TYPESAFE_API_KEY:-}" && -f "${SKILL_DIR}/.env" ]]; then
  set -a; source "${SKILL_DIR}/.env"; set +a
fi
SEND=()
if [[ -n "${TYPESAFE_API_KEY:-}" ]]; then SEND=(--send); fi
cd "${SKILL_DIR}"
exec "${MIRAI_STATION_VENV}/bin/python" -m sndk_jev.service --state-dir "${STATE_DIR}" "${SEND[@]}"
