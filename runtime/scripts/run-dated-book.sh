#!/usr/bin/env bash
# launchd-fired wrapper: DATED BOOK SIDECAR nightly pull (shadow, display-only).
# Fetches the next two SPX AM monthlies + the quarter-end EOM chain (ONE batched
# cass_market_run) and persists state/dated_gex/book.json. Runs pre-open at
# 05:17 PT daily plus Friday 14:10 PT (fires EVERY Friday — only the OpEx one
# matters, when python rolls to the new front monthlies; the rest are cheap
# re-pulls of the same fronts). Physically
# separate from the scan tick — the live loop only ever cache-reads the store.
# Kill switch: DATED_BOOK_DISABLE=1 => exit-0 no-op (checked here AND in python).
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

# Kill switch + weekends (holiday no-op is stamped inside pull()).
[[ "${DATED_BOOK_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/skills/mirai-left-eye"
exec "${MIRAI_STATION_VENV}/bin/python" dated_gex_feed.py --pull
