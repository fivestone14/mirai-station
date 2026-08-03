#!/usr/bin/env bash
# Install + load all mirai-station LaunchAgents.
# Symlinks the plists from the plugin tree into ~/Library/LaunchAgents/
# then bootstraps each via launchctl.

set -euo pipefail

# shellcheck source=env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

PLISTS=(
  "com.mirai-station.left-eye.plist"
  "com.mirai-station.caffeinate.plist"
  "com.mirai-station.auth-watch.plist"
  "com.mirai-station.macro-brief.plist"
  "com.mirai-station.gex-polarity.plist"
  "com.mirai-station.viewstation.plist"
  "com.mirai-station.lob-collector.plist"
  "com.mirai-station.dated-book.plist"
  "com.mirai-station.sndk.plist"
  "com.mirai-station.sndk-read.plist"
  "com.mirai-station.voice.plist"
)

TARGET_DIR="${HOME}/Library/LaunchAgents"
mkdir -p "$TARGET_DIR"

for p in "${PLISTS[@]}"; do
  src="${MIRAI_STATION_ROOT}/runtime/launchd/$p"
  dst="${TARGET_DIR}/$p"

  if [[ ! -f "$src" ]]; then
    log "install-launchd: missing source plist $src"
    exit 1
  fi

  # Unload any previous version (ignore failure if not loaded)
  launchctl bootout "gui/$UID/$(basename "$p" .plist)" 2>/dev/null || true

  ln -sfn "$src" "$dst"
  log "install-launchd: linked $dst"

  launchctl bootstrap "gui/$UID" "$dst"
  launchctl enable "gui/$UID/$(basename "$p" .plist)"
  log "install-launchd: bootstrapped $(basename "$p" .plist)"
done

log "install-launchd: all agents loaded. Inspect with: launchctl print gui/\$UID/com.mirai-station.left-eye"
