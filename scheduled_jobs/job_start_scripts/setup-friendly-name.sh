#!/usr/bin/env bash
# Give the Mirai Viewstation an easy, stable address on your LAN.
#
# WHY: the tablet currently uses http://<ip>:8787. The IP can change when the
# router hands out a new lease. mDNS (Bonjour) fixes that — a *.local name
# resolves to whatever IP the Mac currently has, automatically, no router setup.
#
# WHAT THIS DOES (run once, needs sudo):
#   1. Renames the Mac's Local Hostname        ->  http://mirai.local:8787
#   2. (optional --port80) redirects port 80 -> 8787 via pf, persisted at boot,
#      so the address becomes just                 http://mirai.local
#
# USAGE (run on the Mac mini):
#   sudo bash setup-friendly-name.sh                 # name = mirai
#   sudo bash setup-friendly-name.sh trading-brain   # custom name
#   sudo bash setup-friendly-name.sh mirai --port80  # also drop the :8787
#
# REVERT:
#   sudo scutil --set LocalHostName mirai-station
#   sudo launchctl bootout system/com.mirai-station.pf 2>/dev/null
#   sudo rm -f /Library/LaunchDaemons/com.mirai-station.pf.plist /usr/local/etc/mirai-viewstation-pf.conf
#   sudo pfctl -d            # (only if nothing else uses pf)
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run with sudo:  sudo bash $0 ${*:-}" >&2
  exit 1
fi

NAME="mirai"
PORT80=0
APP_PORT="${MIRAI_VIEW_PORT:-8787}"
for arg in "$@"; do
  case "$arg" in
    --port80) PORT80=1 ;;
    -*)       echo "unknown flag: $arg" >&2; exit 1 ;;
    *)        NAME="$arg" ;;
  esac
done

# mDNS names must be DNS-label-safe: letters, digits, hyphens.
if ! [[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]]; then
  echo "Bad name '$NAME' — use letters, digits, hyphens only." >&2
  exit 1
fi

echo "→ Setting Local Hostname to '$NAME' (was: $(scutil --get LocalHostName 2>/dev/null || echo none))"
scutil --set LocalHostName "$NAME"

if [[ "$PORT80" -eq 1 ]]; then
  CONF="/usr/local/etc/mirai-viewstation-pf.conf"
  PLIST="/Library/LaunchDaemons/com.mirai-station.pf.plist"
  echo "→ Installing pf redirect: port 80 -> ${APP_PORT}"
  mkdir -p "$(dirname "$CONF")"
  cat > "$CONF" <<EOF
# Mirai Viewstation — redirect inbound :80 to the app on :${APP_PORT}.
rdr pass inet proto tcp from any to any port 80 -> 127.0.0.1 port ${APP_PORT}
EOF
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.mirai-station.pf</string>
  <key>ProgramArguments</key>
  <array><string>/sbin/pfctl</string><string>-E</string><string>-f</string><string>${CONF}</string></array>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>/tmp/mirai-station.pf.err</string>
</dict></plist>
EOF
  chown root:wheel "$PLIST"; chmod 644 "$PLIST"
  launchctl bootout system "$PLIST" 2>/dev/null || true
  launchctl bootstrap system "$PLIST"
  pfctl -E -f "$CONF" 2>/dev/null || true
  echo "   pf redirect active (and will reload at every boot)."
fi

IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '<mac-ip>')"
echo ""
echo "✅ Done. Open on the tablet (same Wi-Fi):"
if [[ "$PORT80" -eq 1 ]]; then
  echo "     http://${NAME}.local           ← friendly, no port"
fi
echo "     http://${NAME}.local:${APP_PORT}"
echo "     http://${IP}:${APP_PORT}         ← IP fallback if .local is flaky"
echo ""
echo "Tip: in Chrome on the tablet, open it once then ⋮ → Add to Home screen —"
echo "you'll never type the address again, and the icon survives IP changes."
