#!/usr/bin/env bash
# launchd-fired wrapper: the SNDK chart's live READING (beta, record-only).
# One run = recompute the arrow from the newest diary row (pure python, free),
# and — only when the wake gate fires — spend ONE `claude -p` call to write the
# plain-English reading. Appends to state/sndk_reads/{date}.jsonl for the
# viewstation's /api/raw readers.
#
# WHY THIS IS ITS OWN JOB and not a step inside run-sndk.sh: a model call takes
# seconds and can hang to its timeout (100s in sr-2 — the read may spend a
# history lookup or a WebSearch inside the call). Inline, that would eat the
# scanner's 120s tick and could drop a diary row — the one artifact that must
# never be lost. Separate process = a slow or failed read can never cost a scan.
#
# Gated on the authoritative market-status (RTH only); python gates again
# (defense in depth; --force bypasses for manual runs).
# Kill switch: SNDK_READ_DISABLE=1 => exit-0 no-op (checked here AND in python).
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

# Kill switches + weekends. SNDK_PRO_DISABLE also stops the reading: with the
# scanner down there are no fresh rows to read, so reading on would only
# re-narrate a frozen book.
[[ "${SNDK_READ_DISABLE:-0}" == "1" ]] && exit 0
[[ "${SNDK_PRO_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/runtime"
if ! "${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 1)"; then
  echo "sndk-read :: market closed — no read"
  exit 0
fi

cd "${MIRAI_STATION_ROOT}/skills/sndk-pro"
exec "${MIRAI_STATION_VENV}/bin/python" sndk_read.py
