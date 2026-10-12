#!/usr/bin/env bash
# `mirai-watch` wrapper — dispatches activate / deactivate / status / tick.
#
# Install (per-machine, idempotent):
#   ln -sf "$HOME/.claude/plugins/mirai-station/runtime/scripts/mirai-watch.sh" \
#          "$HOME/.local/bin/mirai-watch"

set -u
# Resolve through any symlink (e.g., installed at ~/.local/bin/mirai-watch).
_SELF="${BASH_SOURCE[0]}"
while [[ -L "$_SELF" ]]; do
  _SELF="$(readlink "$_SELF")"
  case "$_SELF" in /*) ;; *) _SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$_SELF" ;; esac
done
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
# env.sh may pull secrets that don't exist on a fresh box; don't abort.
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

PY="${MIRAI_STATION_VENV}/bin/python"
LAUNCHD="${MIRAI_STATION_ROOT}/runtime/launchd"
UID_GUI="gui/$(id -u)"

usage() {
  cat <<EOF
Usage: mirai-watch <command> [args]

Commands:
  activate                Load the launchd plists (5-min scan+tick + caffeinate)
  deactivate              Unload the launchd plists
  status                  Show launchd state + tail today's log
  tick                    Run one unified scan->monitor tick now
                          Optional: --dry-run, --verbose, --quiet
  doctor                  Self-diagnose paths, deps, claude CLI, role briefs
EOF
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  activate)
    for label in com.mirai-station.left-eye com.mirai-station.caffeinate; do
      plist="${LAUNCHD}/${label}.plist"
      if [[ -f "$plist" ]]; then
        launchctl bootstrap "$UID_GUI" "$plist" && echo "  loaded $label" || echo "  (already loaded?) $label"
      fi
    done
    ;;
  deactivate)
    for label in com.mirai-station.left-eye com.mirai-station.caffeinate; do
      launchctl bootout "$UID_GUI/$label" 2>/dev/null && echo "  unloaded $label" || echo "  (not loaded) $label"
    done
    ;;
  status)
    echo "== launchd =="
    launchctl list | grep com.mirai-station || echo "  (no mirai-station jobs loaded)"
    echo
    echo "== today's log (last 20 lines) =="
    log="${MIRAI_STATION_LOGS}/watch-$(date +%F).jsonl"
    if [[ -f "$log" ]]; then tail -n 20 "$log"; else echo "  (no log for today)"; fi
    ;;
  tick)
    # Gex-only chassis: one manual tick = the same scan+alerts pass launchd runs.
    exec "${SCRIPT_DIR}/run-watch-left-eye.sh"
    ;;
  doctor)
    cd "${MIRAI_STATION_ROOT}/runtime"
    exec "$PY" -m watch.cli doctor
    ;;
  help|-h|--help|"")
    usage
    ;;
  *)
    echo "unknown command: $cmd"
    usage
    exit 2
    ;;
esac
