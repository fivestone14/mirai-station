#!/usr/bin/env bash
# Mirai Voice — start the SNDK spoken-conversation sidecar (skills/mirai-voice).
# WebSocket on :8788 for the tab's talk button; models (Parakeet ears, Kokoro
# mouth) warm at boot, Claude rides the subscription via the Agent SDK.
#
#   ./run-voice.sh                       # foreground, port 8788
#   MIRAI_VOICE_PORT=9788 ./run-voice.sh
#   MIRAI_VOICE_DISABLE=1                # kill switch (also checked in python —
#                                        # defense in depth, house convention)
#
# Launched continuously by com.mirai-station.voice.plist (KeepAlive). No
# weekend/market gate: conversation should work anytime the desk is on.
set -u
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do _SELF="$(readlink "$_SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

if [[ "${MIRAI_VOICE_DISABLE:-0}" == "1" ]]; then
  log "voice: disabled (MIRAI_VOICE_DISABLE=1)"
  exit 0
fi

export MIRAI_VOICE_PORT="${MIRAI_VOICE_PORT:-8788}"
# -u: launchd pipes stdout to /tmp logs; unbuffered so the log is live
exec "${MIRAI_STATION_VENV}/bin/python" -u \
  "${MIRAI_STATION_ROOT}/skills/mirai-voice/voice_server.py"
