#!/usr/bin/env bash
# Mirai Viewstation — start the read-only tablet view server.
# Runs under the mirai-station venv so snapshot.py can import the skill modules.
#
#   ./run-viewstation.sh                 # foreground, port 8787
#   MIRAI_VIEW_PORT=9000 ./run-viewstation.sh
#
# Launched continuously by com.mirai-station.viewstation.plist (KeepAlive).
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

export MIRAI_VIEW_PORT="${MIRAI_VIEW_PORT:-8787}"
exec "${MIRAI_STATION_VENV}/bin/python" \
  "${MIRAI_STATION_ROOT}/runtime/viewstation/server.py"
