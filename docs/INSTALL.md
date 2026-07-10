# mirai-station — Mac mini install

> [!warning] 2026-07-03 gex-module restructure
> This document predates the restructure where the nine-voter/bet-watching
> system was retired. Live shape: Shift Manager (hunter) → Fade Lens →
> Gravity Engine + Flow Sensor → diary → report cards → storytellers +
> alert bell. See docs/gex-glossary.md and docs/salvage-notes.md.


End-state: a dedicated Mac mini that runs the unified left-eye tick every 5 min Mon–Fri 09:30–16:00 ET (the tick folds in right-eye news watching), pushes alerts to your phone via ntfy, keeps the Schwab login alive with a daily auth-watch ping, and operates **without** access to the main machine's vault.

Estimated setup time: 60–90 min.

---

## 0. Mac mini base prep

1. **OS up to date** (System Settings → General → Software Update).
2. **Auto-login** for the local user (System Settings → Users & Groups → Automatic login). LaunchAgents only fire when a user is logged in.
3. **Energy** — System Settings → Energy:
   - Prevent automatic sleeping when display is off → On
   - Wake for network access → On
   - Start up automatically after a power failure → On
4. **Time zone** — set to `America/New_York` (or rely on the per-plist `TZ` env; both belt and suspenders).
5. **Screen Sharing / SSH** — Settings → General → Sharing → enable Screen Sharing and Remote Login. Restrict to your account.
6. **Optional: Tailscale** for zero-config SSH from anywhere: `brew install tailscale && sudo tailscaled install-system-daemon && tailscale up`.

## 1. Install developer essentials

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# CLI deps
brew install git python@3.12 jq

# Claude Code
curl -fsSL https://claude.ai/install.sh | bash    # installs ~/.local/bin/claude
```

Verify:
```bash
claude --version
python3 --version    # >= 3.12
```

## 2. Drop the plugin onto the mini

The plugin lives at `~/.claude/plugins/mirai-station/`. Copy it from your main machine:

```bash
# From main machine, replacing <mini-host> with the mini's hostname or Tailscale name
rsync -avh --exclude '__pycache__' --exclude 'state/logs/' \
  ~/.claude/plugins/mirai-station/ \
  <mini-host>:~/.claude/plugins/mirai-station/
```

Or `git clone` if you've put it in a private repo.

## 3. Provision secrets in Keychain

Three secrets go into the macOS Keychain. The runtime scripts read them at run time, never from files.

```bash
# Anthropic API key (NOT your Pro plan login; this is a dollar-billed key)
security add-generic-password \
  -a "$USER" \
  -s "mirai-station/anthropic-api-key" \
  -w "sk-ant-…"

# Discord webhook (create in Discord: server settings → Integrations → Webhooks → New Webhook → copy URL)
security add-generic-password \
  -a "$USER" \
  -s "mirai-station/discord-alert-webhook" \
  -w "https://discord.com/api/webhooks/…"

# Schwab credentials (one entry per secret your hunter needs; mirror the names your iv-viability skill uses)
security add-generic-password -a "$USER" -s "mirai-station/schwab-app-key"     -w "…"
security add-generic-password -a "$USER" -s "mirai-station/schwab-app-secret"  -w "…"
security add-generic-password -a "$USER" -s "mirai-station/schwab-token-path"  -w "$HOME/.local/share/mirai-station/schwab-token.json"
```

Test retrieval:
```bash
security find-generic-password -a "$USER" -s "mirai-station/discord-alert-webhook" -w
```

## 4. Provision the Python venv

```bash
cd ~/.claude/plugins/mirai-station
./runtime/scripts/venv-bootstrap.sh
```

Verify the hunter can import its deps:
```bash
~/.local/share/mirai-station/venv/bin/python -c "import schwab, scipy, numpy; print('ok')"
```

## 5. MCP servers (Cassandra's Edge)

mirai's MCP feeds are the **Cassandra's Edge** remote servers — HTTP MCP endpoints at `https://*.cassandrasedge.com/mcp`, authenticated per-server with a bearer token. The minimum viable set for the left-eye tick (including its right-eye news fetch) is **market-research, twitter, reddit**; **yt** and **fetch** round out what `agents/mirai.md` uses. (Other servers exist on the Edge but the routine does not use them.)

The source of truth is the main machine's user-scope config: the top-level `mcpServers` block in `~/.claude.json` (NOT `~/.config/claude/`). To set up the mini, copy that block into the mini's `~/.claude.json` (user scope = available to every `claude -p` regardless of cwd), e.g.:

```bash
# on the main machine
python3 -c "import json;print(json.dumps(json.load(open('$HOME/.claude.json'))['mcpServers'],indent=2))"
# paste into the mini's ~/.claude.json under "mcpServers", or re-add via:
#   claude mcp add --scope user --transport http market-research https://market-research.cassandrasedge.com/mcp --header "Authorization: Bearer <token>"
```

**Headless permission requirement:** unattended `claude -p` calls never show permission prompts — an MCP tool that isn't allowlisted is silently unavailable to the fetchers. The servers must be allowed via `permissions.allow` in `~/.claude/settings.json` on the mini (server-level rules: `mcp__market-research`, `mcp__twitter-mcp`, `mcp__reddit-mcp`, `mcp__yt-mcp`, `mcp__fetch-mcp`), or per-invocation with `--allowedTools`. All five servers are read-only by design.

Smoke test (verifies endpoint, auth, and headless tool access in one shot):
```bash
cd ~/.claude/plugins/mirai-station/runtime
claude -p --model haiku \
  --allowedTools "mcp__market-research__cass_market_search" \
  "Call cass_market_search with query 'stock brief' and output only the name of the first tool it returns."
```

## 6. Discord channel + webhook (legacy / optional)

> The live push channel is **ntfy** — set `ntfy.topic` in
> `runtime/watch/config/limits-and-cooldowns.json` and subscribe your phone to that
> topic. The Discord webhook below is the legacy `discord-alert.sh` path, kept for
> backward compatibility; skip this section if you only use ntfy.

1. Create a private Discord server (or use an existing one).
2. New channel: `#mirai-station-alerts`.
3. Channel settings → Integrations → Webhooks → New → name it "mirai-station" → copy URL.
4. Paste URL into the Keychain entry from step 3.

Test:
```bash
~/.claude/plugins/mirai-station/runtime/scripts/discord-alert.sh "test" "smoke test from mac mini"
```

Expect to see the message in `#mirai-station-alerts` within a second.

## 7. Install LaunchAgents

```bash
~/.claude/plugins/mirai-station/runtime/scripts/install-launchd.sh
```

This symlinks the plists into `~/Library/LaunchAgents/` and bootstraps them via `launchctl`. Three agents:

| Label | Cadence | What |
|---|---|---|
| `com.mirai-station.caffeinate` | always running | keeps the mini awake |
| `com.mirai-station.left-eye`   | every 5 min     | runs `run-watch-left-eye.sh` (reconcile → learn → scan → unified tick; market gate inside) |
| `com.mirai-station.auth-watch` | daily 08:00 ET  | Schwab token keep-alive ping (`run-auth-check.sh`) |

Verify:
```bash
launchctl list | grep mirai-station
```

## 8. Smoke test the full chain

```bash
# Run a single unified tick immediately (bypasses the launchd schedule)
~/.claude/plugins/mirai-station/runtime/scripts/run-watch-left-eye.sh

# Tail today's watch log
tail -f ~/.claude/plugins/mirai-station/state/logs/watch-$(date +%Y-%m-%d).jsonl
```

If a signal clears the alert threshold, an ntfy push lands on your phone. If the tape is quiet, no push — that's correct.

## 9. Verify auto-resume on reboot

```bash
sudo shutdown -r now
```

Log back in, then:
```bash
launchctl list | grep mirai-station   # all three should be listed
ps -ef | grep caffeinate              # caffeinate should be running
```

---

## Operating outside market hours

The market-hours gate is the first node of the unified tick graph (`runtime/watch/intraday/market_status.py`), authoritative via the oracle calendar. Outside market hours the tick exits instantly; launchd still wakes left-eye every 5 min, that's fine and cheap.

If you want fewer wake-ups, the optional `StartCalendarInterval` block in the plist already enumerates :00/:05/:10/…/:55 — you can constrain it to specific weekdays + hours and remove `StartInterval`. Default leaves both in: launchd will fire at whichever comes first.
