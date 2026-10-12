#!/usr/bin/env bash
# launchd-fired wrapper: one Mirai Awakening wake = scan -> alerts.
# (Gex-only chassis, post 2026-07-03 restructure: the reconcile/learn/tick
#  phases died with the nine-voter bet system; the Fade Lens is the brain.)
#
# Phase 1 (scan, MARKET HOURS ONLY): hunter.py runs the Fade-Lens SCAN on each
#   index — flow sensor → gravity engine → lens gates — records the diary row,
#   refreshes the Obsidian notes. Gated on the authoritative market-status so
#   after-hours ticks never pollute the learning stream with stale data.
# Phase 2 (alerts): the ALERT BELL — fire pushes, the wall-breach bell, the feed
#   sirens; runs after hours too, so a fire from the day's last scan is pushed and
#   a silent scanner in a live market is paged whatever the scan phase did.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

cd "${MIRAI_STATION_ROOT}/alerts_and_phone_pushes"

# Phase 1 — scan (RTH only). A scan failure must not block the alert pass.
# Only the gate's own "closed" (exit 3) skips the scan quietly. Python exits 1 on
# any uncaught exception, so with "closed" on 1 a broken venv or import read as a
# closed market; now it is reported and fails the job after the alert pass.
set +e
"${MIRAI_STATION_VENV}/bin/python" -c "from watch.intraday import market_status as m; import sys; sys.exit(0 if m.check().is_live else 3)"
GATE_RC=$?
set -e
if [[ $GATE_RC -eq 0 ]]; then
  "${MIRAI_STATION_VENV}/bin/python" "${MIRAI_STATION_ROOT}/pipelines/spx_options_scanner/hunter.py" || true
elif [[ $GATE_RC -eq 3 ]]; then
  echo "mirai-left-eye :: market closed — skipping scan"
else
  echo "mirai-left-eye :: market-hours check FAILED (rc=${GATE_RC}) — check venv/imports; no scan" >&2
fi

# Phase 2 — alert bell (always: any not-yet-pushed fires, the wall-breach bell, the feed sirens).
if [[ $GATE_RC -ne 0 && $GATE_RC -ne 3 ]]; then
  "${MIRAI_STATION_VENV}/bin/python" -m watch.cli gex-alerts || true
  exit 1
fi
exec "${MIRAI_STATION_VENV}/bin/python" -m watch.cli gex-alerts
