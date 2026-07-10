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
)

for label in "${PLISTS[@]}"; do
  launchctl bootout "gui/$UID/$label" 2>/dev/null && log "uninstall: booted out $label" || log "uninstall: $label was not loaded"
  rm -f "${HOME}/Library/LaunchAgents/${label}.plist"
done

log "uninstall-launchd: complete"
