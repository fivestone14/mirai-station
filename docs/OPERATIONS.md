# mirai-station — Operations runbook

> Architecture: see the top-level `README.md` and `docs/gex-glossary.md`. Live
> shape: hunter (Shift Manager) → Flow Sensor + Gravity Engine → three heads
> (Fade Lens, Watchtower, Break Lens) → diary → nightly report cards → tablet + ntfy.

## Start / stop / restart

```bash
# Stop a single agent
launchctl bootout gui/$UID/com.mirai-station.left-eye

# Start it again
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.mirai-station.left-eye.plist

# Restart the whole fleet (all 7 agents)
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

## ntfy alert channel

ntfy has no API key, but the **topic name is the credential** — the channel is
unauthenticated, so anyone holding it can read every alert and publish fakes. It
lives in the Keychain under service `mirai-station-ntfy`, and
`runtime/scripts/env.sh` exports it as `MIRAI_NTFY_TOPIC` (which beats the config
file, whose `topic` key stays empty because that file is tracked).

Rotate it — after any suspected exposure, and note that a topic that ever reached
the repo is exposed permanently, since git history keeps it:

```bash
# 1. new topic, generated straight into the Keychain (never printed, never in a file)
security add-generic-password -U -a "$USER" -s "mirai-station-ntfy" \
  -w "mirai-$(openssl rand -hex 16)"

# 2. read it once to re-subscribe the phone app
security find-generic-password -a "$USER" -s "mirai-station-ntfy" -w

# 3. the agents re-read env.sh on their next run — no restart needed, but to
#    prove it end to end:
./runtime/scripts/run-auth-check.sh
```

Until the phone is re-subscribed it receives nothing; the old topic keeps working
for whoever else knows it, which is exactly why rotation is the fix rather than
editing history.

## Market-data credential rotation

```bash
# Schwab token is refreshed by the auth-watch agent; if the 7-day login lapses,
# re-run the OAuth flow the iv-viability vault uses, then confirm:
security find-generic-password -a "$USER" -s "mirai-station/schwab-token-path" -w

# ThetaData / Cassandra's Edge bearer (the native SPX chain):
security delete-generic-password -a "$USER" -s "iv-viability-cassandra"
security add-generic-password    -a "$USER" -s "iv-viability-cassandra" -w "<new bearer>"
# No restart needed — the vault reads Keychain on every invocation.
```

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `launchctl list` shows `Status: 78` for an agent | exit code != 0; check stderr | `tail /tmp/mirai-station.<label>.err` |
| "schwab module not found" | venv not provisioned or wrong python | re-run `venv-bootstrap.sh`; confirm shebang resolves |
| GEX read falls back to SPY-proxy every scan | dead ThetaData/Cassandra bearer | re-key `iv-viability-cassandra` (see above); auth-watch also pings on this |
| No ntfy push on a fire | topic unset or phone not subscribed | check the `ntfy` block in `limits-and-cooldowns.json`; re-subscribe the app |
| MCP tool calls fail (macro brief) | MCP server config missing on mini | copy the `mcpServers` block into the mini's `~/.claude.json` (see INSTALL §5) |
| Mac mini sleeping | caffeinate plist not loaded | re-run `install-launchd.sh` |

## What the mini knows vs. doesn't

The mini operates with:
- The gex-only brain in `skills/mirai-left-eye/` (Gravity Engine + three heads)
- Schwab live chain + the ThetaData native SPX chain via the Python venv
- Cassandra's Edge MCP sources (twitter / reddit / fetch) for the morning macro brief

The mini does NOT have:
- The interactive investment vault (intentional — stays on the main machine)
- Any real-money order path — everything here is paper/shadow until the Wilson
  promotion gate clears

If a boot step flags a missing vault file, that is expected and not an error.

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
