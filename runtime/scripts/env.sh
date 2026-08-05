#!/usr/bin/env bash
# Shared env for all mirai-station runtime scripts.
# Sourced by run-watch-left-eye.sh, run-auth-check.sh, run-*.sh, etc.

set -u

# Resolve plugin root from this script's location (../..)
MIRAI_STATION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export MIRAI_STATION_ROOT
export MIRAI_STATION_VENV="${HOME}/.local/share/mirai-station/venv"
export MIRAI_STATION_STATE="${MIRAI_STATION_ROOT}/state"
export MIRAI_STATION_LOGS="${MIRAI_STATION_STATE}/logs"

# Force New York time for market-hour gates
export TZ="America/New_York"

# Path additions
export PATH="${HOME}/.local/bin:/usr/local/bin:/opt/homebrew/bin:${PATH}"

# Secrets — pulled from macOS Keychain at runtime (never committed).
# Mirai Watch uses the Claude subscription via `claude -p` and needs NO
# ANTHROPIC_API_KEY. Market-data credentials (Schwab, the ThetaData/Cassandra
# bearer) are read on demand via the `keychain_get` helper below — nothing
# sensitive is set at this file's top level.
keychain_get() {
  local label="$1"
  security find-generic-password -a "$USER" -s "$label" -w 2>/dev/null
}
export -f keychain_get

# ntfy topic — the topic name IS the credential. ntfy.sh is unauthenticated
# pub/sub: anyone holding the topic can read every alert this station sends AND
# publish fakes into it. It used to sit in limits-and-cooldowns.json, which is
# tracked — so it was in the repo, and in every commit of its history. It now
# lives in Keychain with the rest of the secrets. `settings.ntfy_topic()` reads
# MIRAI_NTFY_TOPIC first and falls back to the (now-empty) config value.
#
#   store:  security add-generic-password -U -a "$USER" -s mirai-station-ntfy \
#             -w "mirai-$(openssl rand -hex 16)"
#   read:   security find-generic-password -a "$USER" -s mirai-station-ntfy -w
#
# Unset when absent, not empty: push_ntfy no-ops gracefully on a missing topic
# (dispatched=False), which is the correct posture before setup — silence beats
# posting a station's read to a guessable channel.
MIRAI_NTFY_TOPIC="$(keychain_get mirai-station-ntfy || true)"
if [ -n "${MIRAI_NTFY_TOPIC}" ]; then
  export MIRAI_NTFY_TOPIC
else
  unset MIRAI_NTFY_TOPIC
fi

mkdir -p "$MIRAI_STATION_LOGS"

# Helper: timestamped log line to stdout AND the rotating file
log() {
  local msg="$*"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[$ts] $msg" | tee -a "${MIRAI_STATION_LOGS}/runtime-$(date +%Y-%m-%d).log"
}
