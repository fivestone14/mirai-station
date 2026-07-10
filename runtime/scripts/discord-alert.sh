#!/usr/bin/env bash
# Push a message to the configured Discord webhook.
# Usage:
#   discord-alert.sh "title" "body"
#   echo "body" | discord-alert.sh "title"

set -euo pipefail

# shellcheck source=env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

title="${1:-mirai-station alert}"
if [[ $# -ge 2 ]]; then
  body="$2"
else
  body="$(cat)"
fi

# env.sh no longer reads webhook secrets at top level — pull on demand here.
DISCORD_ALERT_WEBHOOK="${DISCORD_ALERT_WEBHOOK:-$(keychain_get 'mirai-station/discord-alert-webhook')}"
if [[ -z "${DISCORD_ALERT_WEBHOOK:-}" ]]; then
  log "discord-alert: DISCORD_ALERT_WEBHOOK not set in Keychain (mirai-station/discord-alert-webhook); skipping"
  exit 0
fi

# Discord 2000-char message cap; truncate hard.
body="${body:0:1900}"

payload=$(python3 - <<'PY' "$title" "$body"
import json, sys
title, body = sys.argv[1], sys.argv[2]
content = f"**{title}**\n```\n{body}\n```"
print(json.dumps({"content": content}))
PY
)

curl -fsS -X POST -H "Content-Type: application/json" \
  -d "$payload" \
  "$DISCORD_ALERT_WEBHOOK" \
  >/dev/null \
  && log "discord-alert: sent '$title'" \
  || log "discord-alert: FAILED to send '$title'"
