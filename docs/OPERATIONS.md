# mirai-station — Operations runbook

> [!warning] 2026-07-03 gex-module restructure
> This document predates the restructure where the nine-voter/bet-watching
> system was retired. Live shape: Shift Manager (hunter) → Fade Lens →
> Gravity Engine + Flow Sensor → diary → report cards → storytellers +
> alert bell. See docs/gex-glossary.md and docs/salvage-notes.md.


## Start / stop / restart

```bash
# Stop a single agent
launchctl bootout gui/$UID/com.mirai-station.left-eye

# Start it again
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.mirai-station.left-eye.plist

# Restart all three
~/.claude/plugins/mirai-station/runtime/scripts/uninstall-launchd.sh
~/.claude/plugins/mirai-station/runtime/scripts/install-launchd.sh
```

## Logs

| What | Where |
|---|---|
| Per-scan stdout/stderr | `/tmp/mirai-station.*.{out,err}` (rotated by macOS) |
| Unified tick history   | `~/.claude/plugins/mirai-station/state/logs/watch-YYYY-MM-DD.jsonl` |
| Runtime/env messages   | `~/.claude/plugins/mirai-station/state/logs/runtime-YYYY-MM-DD.log` |
| Hunter's own jsonl     | `~/.claude/plugins/mirai-station/skills/mirai-left-eye/logs/YYYY-MM-DD.jsonl` |

Quick health check:
```bash
tail -50 ~/.claude/plugins/mirai-station/state/logs/watch-$(date +%Y-%m-%d).jsonl
```

## Force-run on demand

```bash
~/.claude/plugins/mirai-station/runtime/scripts/run-watch-left-eye.sh
```

This bypasses launchd entirely; useful for debugging. (Or `mirai-watch tick --dry-run` for a no-dispatch run.)

## Discord webhook re-key

If Discord rotates the URL:
```bash
security delete-generic-password -a "$USER" -s "mirai-station/discord-alert-webhook"
security add-generic-password -a "$USER" -s "mirai-station/discord-alert-webhook" -w "<new url>"
```

## API key rotation

```bash
security delete-generic-password -a "$USER" -s "mirai-station/anthropic-api-key"
security add-generic-password -a "$USER" -s "mirai-station/anthropic-api-key" -w "sk-ant-…"
# No restart needed — env.sh reads Keychain on every script invocation.
```

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `launchctl list` shows `Status: 78` for an agent | exit code != 0; check stderr | `tail /tmp/mirai-station.<label>.err` |
| "schwab module not found" | venv not provisioned or wrong python | re-run `venv-bootstrap.sh`; confirm shebang resolves |
| Webhook 401 from Discord | URL revoked / channel deleted | recreate webhook; update Keychain |
| Anthropic 401 | API key expired | rotate (see above) |
| MCP tool calls fail | MCP server config missing on mini | copy `~/.config/claude/` from main machine |
| Hunter alerts but no Discord ping | `DISCORD_ALERT_WEBHOOK` empty | check Keychain entry exists |
| Mac mini sleeping | caffeinate plist not loaded | re-run `install-launchd.sh` |

## What the mini knows vs. doesn't

The mini operates with:
- The vendored `knowledge/mirai/` brain (reflexes, patterns, dossier registry)
- Live MCP sources (perplexity, twitter, market-research, etc.)
- Schwab live chain via the Python venv

The mini does NOT have:
- The investment vault at `~/Documents/Investments/` (intentional — stays on main machine)
- Prism/bet case files (those live with the main machine's full skill set)
- The `tradepost` runtime (intentional — mirai-station is its own thing)

If mirai's boot sequence flags missing vault files, that is expected and not an error.

## Updating the plugin

Edit on main machine, then:
```bash
rsync -avh --exclude '__pycache__' --exclude 'state/logs/' --exclude 'state/dossiers/' \
  ~/.claude/plugins/mirai-station/ \
  <mini-host>:~/.claude/plugins/mirai-station/
```

The launchd jobs pick up script changes on next fire (no restart needed). plist changes require:
```bash
~/.claude/plugins/mirai-station/runtime/scripts/install-launchd.sh
```

## Disabling temporarily

```bash
launchctl disable gui/$UID/com.mirai-station.left-eye
launchctl disable gui/$UID/com.mirai-station.auth-watch
# caffeinate left running so the mini is still reachable
```

Re-enable:
```bash
launchctl enable gui/$UID/com.mirai-station.left-eye
launchctl enable gui/$UID/com.mirai-station.auth-watch
```
