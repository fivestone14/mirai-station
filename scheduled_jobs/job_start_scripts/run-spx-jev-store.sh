#!/usr/bin/env bash
# launchd-fired wrapper: after the day's saves, rebuild the learning store.
# One run = every market day of the last week, today once its after-close saves have run, turned
# from the raw files under data/ into checked Parquet under data/spx/jev/tables/ and data/market/tables/ (spx_jev/store.py).
# Reads the raw files only; no Schwab call, no JEV call. Idempotent: a day is rebuilt whole from its
# files each time.
# No market-hours gate: it runs after the close by design. The command itself builds market days
# only, and today only once the 16:20 saves have run.
# Then, once a night, every finished day file older than 14 days in the big daily stores is gzipped
# in place, losslessly ({day}.jsonl -> {day}.jsonl.gz: station_store_files.compress_finished_days,
# the stores it names in COMPRESSED_DAY_FILE_STORES); every reader reads a .gz day as the plain file.
# It runs after the build, which reads only the last week's plain files, and never fails the job.
# Installed by install-launchd.sh (com.mirai-station.spx-jev-store, 16:40 ET).
# Kill switch: SPX_JEV_DISABLE=1 => exit-0 no-op.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SPX_JEV_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/pipelines/spx_jev_questions_and_pools"
rc=0
"${MIRAI_STATION_VENV}/bin/python" -m spx_jev.store --state-dir "${MIRAI_STATION_STATE}" || rc=$?
"${MIRAI_STATION_VENV}/bin/python" "${MIRAI_STATION_ROOT}/station_store_files.py" --compress-finished-days \
  --state-dir "${MIRAI_STATION_STATE}" || true
exit $rc
