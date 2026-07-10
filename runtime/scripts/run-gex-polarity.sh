#!/usr/bin/env bash
# launchd-fired wrapper: after-close GEX polarity + magnet A/B grader (shadow).
# Grades today's gex_views (native-primary GexBox) and gex_theta (native) reads against the
# day's 1-min bars and persists the verdicts to state/reversion/polarity-{day}.json
# + the polarity_history.jsonl ledger. Read-only vs the live loop.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

# Weekends are not trading days — nothing to grade.
[[ $(date +%u) -gt 5 ]] && exit 0

cd "${MIRAI_STATION_ROOT}/skills/mirai-left-eye"
# The two nightly passes are independent (both read the diary, write disjoint
# state): a grader crash must not cost the day's LOB baseline fold, and vice
# versa. Preserve the grader's exit code as the job's own.
rc=0
"${MIRAI_STATION_VENV}/bin/python" gex_polarity_ab.py || rc=$?

# Layer-2 LOB (FLOW) nightly pass: the package's own head-to-head card + the
# baseline fold. Never allowed to fail the grading job.
"${MIRAI_STATION_VENV}/bin/python" lob_bridge.py --nightly || true
exit $rc
