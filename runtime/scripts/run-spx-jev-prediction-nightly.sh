#!/usr/bin/env bash
# launchd-fired wrapper: the Mirai Prediction System's nightly job, after the learning store's rebuild.
# One run = today's work for the live lane (spx_jev/mirai_prediction/nightly_job.py): build this system's tables
# from its raw record files, score every voice against the day's grades, let pool_v2 learn the day, refit the
# three learners for the next market day, write the phone's scoreboard. Reads the store only; no Schwab call, no
# JEV call; writes only under state/spx_jev/mirai_prediction/. Idempotent: tables are rewritten whole, a day is
# learned once.
# Installed by install-launchd.sh (com.mirai-station.spx-jev-prediction-nightly, 17:05 ET), weekdays only.
# Kill switches: SPX_JEV_DISABLE=1 or SPX_JEV_PREDICTION_DISABLE=1 => exit-0 no-op.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

[[ "${SPX_JEV_DISABLE:-0}" == "1" ]] && exit 0
[[ "${SPX_JEV_PREDICTION_DISABLE:-0}" == "1" ]] && exit 0
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/skills/spx-jev"
exec "${MIRAI_STATION_VENV}/bin/python" -m spx_jev.mirai_prediction.nightly_job --state-dir "${MIRAI_STATION_STATE}" --lane live
