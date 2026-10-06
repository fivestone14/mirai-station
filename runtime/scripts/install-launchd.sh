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
  # The SNDK scanner's dead-man's switch. It has to be in this list to exist at
  # all — the installer enumerates explicitly, so a plist that is written but
  # never named here is a watchdog that was built and never hired.
  "com.mirai-station.sndk-deadman.plist"
  # The SNDK minute-bar sidecar (09-02): its own job on its own clock, so the
  # price record survives a stuck scanner. Named here for the same reason the
  # deadman is — an unlisted plist is a job that was built and never hired.
  "com.mirai-station.sndk-bars.plist"
  # The JEV decision service (09-22): its own job, 20 s behind the scanner, reads the
  # station's files and writes only state/jev/. Named here so a reinstall keeps it.
  "com.mirai-station.sndk-jev.plist"
  # The JEV opening lane (09-26): the same runner with `--lane tape`, every 5 minutes
  # 09:35 to 10:30 ET, writing only state/jev/lanes/tape/.
  "com.mirai-station.sndk-jev-tape.plist"
  # The SPX JEV decision service: the same shape beside the left-eye scanner, writing
  # only state/spx_jev/. The live read at :02 and :32, the opening lane, the premarket lane's
  # checkpoints before the open, the four feeds it reads (SPX minute bars, the market around
  # SPX, the day's full bars saved after the close, and the overnight futures saved at 09:26 and 16:20),
  # and the learning store rebuilt from all of them at 16:40.
  "com.mirai-station.spx-jev.plist"
  "com.mirai-station.spx-jev-tape.plist"
  "com.mirai-station.spx-jev-premarket.plist"
  # The pre-market lane's dead-man's switch: pages a checkpoint with no read, or a close-out
  # that never landed or left a call ungraded. Its own job, so the lane dying cannot silence it.
  "com.mirai-station.spx-premarket-deadman.plist"
  "com.mirai-station.spx-jev-bars.plist"
  "com.mirai-station.spx-jev-context.plist"
  "com.mirai-station.spx-jev-save-day.plist"
  "com.mirai-station.spx-jev-overnight.plist"
  "com.mirai-station.spx-jev-store.plist"
  # The Mirai Prediction System's nightly job (17:05 ET): its own tables, voice scores, pool_v2, the learners'
  # refit for tomorrow and the phone's scoreboard, all under state/spx_jev/mirai_prediction/.
  "com.mirai-station.spx-jev-prediction-nightly.plist"
  "com.mirai-station.voice.plist"
)

TARGET_DIR="${HOME}/Library/LaunchAgents"
mkdir -p "$TARGET_DIR"

# bootout returns before launchd has finished tearing a running job down; a bootstrap in that
# window fails with "Bootstrap failed: 5: Input/output error" and, under set -e, stops the whole
# install with that job unloaded (on 2026-09-27 it left the viewstation down). So wait until
# launchd no longer knows the label, up to 15 s.
wait_until_unloaded() {
  local label="$1" tries=0
  while launchctl print "gui/$UID/$label" >/dev/null 2>&1; do
    (( ++tries > 30 )) && { log "install-launchd: $label still loaded after 15 s"; return 1; }
    sleep 0.5
  done
}

# One more try after a pause, for the rare case launchd still refuses right after the wait.
bootstrap_with_retry() {
  local label="$1" plist="$2"
  launchctl bootstrap "gui/$UID" "$plist" && return 0
  log "install-launchd: bootstrap of $label refused, retrying in 3 s"
  sleep 3
  launchctl bootstrap "gui/$UID" "$plist"
}

for p in "${PLISTS[@]}"; do
  src="${MIRAI_STATION_ROOT}/runtime/launchd/$p"
  dst="${TARGET_DIR}/$p"
  label="$(basename "$p" .plist)"

  if [[ ! -f "$src" ]]; then
    log "install-launchd: missing source plist $src"
    exit 1
  fi

  # Unload any previous version (ignore failure if not loaded), then wait for it to be gone
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  wait_until_unloaded "$label"

  ln -sfn "$src" "$dst"
  log "install-launchd: linked $dst"

  bootstrap_with_retry "$label" "$dst"
  launchctl enable "gui/$UID/$label"
  log "install-launchd: bootstrapped $label"
done

log "install-launchd: all agents loaded. Inspect with: launchctl print gui/\$UID/com.mirai-station.left-eye"
