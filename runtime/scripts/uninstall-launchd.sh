#!/usr/bin/env bash
# Stop + remove all mirai-station LaunchAgents.

set -uo pipefail

# shellcheck source=env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

PLISTS=(
  "com.mirai-station.left-eye"
  "com.mirai-station.caffeinate"
  "com.mirai-station.auth-watch"
  "com.mirai-station.macro-brief"
  "com.mirai-station.gex-polarity"
  "com.mirai-station.viewstation"
  "com.mirai-station.lob-collector"
  "com.mirai-station.dated-book"
  "com.mirai-station.sndk"
  "com.mirai-station.sndk-read"
  "com.mirai-station.sndk-deadman"
  "com.mirai-station.sndk-bars"
  # 08-30: `voice` was in install-launchd.sh's list and missing from this one,
  # which is exactly the drift the note below warns about — an uninstalled
  # station kept a voice agent loaded. Found while adding the deadman.
  "com.mirai-station.voice"
)
# MUST stay in step with install-launchd.sh's PLISTS. A job missing from THIS
# list survives an uninstall and keeps firing on its interval — and for the two
# added 08-01 that means a "removed" station still spending `claude -p` calls
# every 120s with nothing left to read them.

for label in "${PLISTS[@]}"; do
  launchctl bootout "gui/$UID/$label" 2>/dev/null && log "uninstall: booted out $label" || log "uninstall: $label was not loaded"
  rm -f "${HOME}/Library/LaunchAgents/${label}.plist"
done

log "uninstall-launchd: complete"
