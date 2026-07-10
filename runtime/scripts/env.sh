#!/usr/bin/env bash
# Shared env for all mirai-station runtime scripts.
# Sourced by run-watch-left-eye.sh, run-auth-check.sh, discord-alert.sh, etc.

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
# The `keychain_get` helper is exported so the legacy `discord-alert.sh` push utility
# can pull its webhook on demand. Mirai Watch itself uses the Claude subscription
# via `claude -p` and does NOT need ANTHROPIC_API_KEY or DISCORD_ALERT_WEBHOOK — those
# reads have been removed from this file's top level. Scripts that need them
# call `keychain_get` directly.
keychain_get() {
  local label="$1"
  security find-generic-password -a "$USER" -s "$label" -w 2>/dev/null
}
export -f keychain_get

mkdir -p "$MIRAI_STATION_LOGS"

# Helper: timestamped log line to stdout AND the rotating file
log() {
  local msg="$*"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[$ts] $msg" | tee -a "${MIRAI_STATION_LOGS}/runtime-$(date +%Y-%m-%d).log"
}
