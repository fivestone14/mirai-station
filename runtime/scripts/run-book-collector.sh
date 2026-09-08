#!/usr/bin/env bash
# Supervisor tick for the book-flow collector. NOT INSTALLED: no launchd job
# loads this yet, because starting the collector takes the single Schwab
# streamer connection away from the lob-flow SPX collector (see
# skills/book-flow/README.md). Install only after that trade is intended.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

cd "${MIRAI_STATION_ROOT}/runtime"
set +e
"${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 1)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 1 ]]; then exit 0
elif [[ $GATE_RC -ne 0 ]]; then
  log "book-collector: market gate FAILED (rc=${GATE_RC}) — check venv/imports"; exit 1
fi

cd "${MIRAI_STATION_ROOT}/skills/book-flow"
exec "${MIRAI_STATION_VENV}/bin/python" -c "
import sys; sys.path.insert(0, '.')
sys.path.insert(0, '${MIRAI_STATION_ROOT}/skills/iv-viability')
import iv_fetcher
from book_flow import daemon
sys.exit(daemon.main(iv_fetcher._build_client))
"
