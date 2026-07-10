# mirai-station — Mac mini install

> Architecture is described in the top-level `README.md` and `docs/gex-glossary.md`.
> This doc is the infra runbook (secrets, venv, launchd fleet). The live shape is:
> hunter (Shift Manager) → Flow Sensor + Gravity Engine → three heads (Fade Lens,
> Watchtower, Break Lens) → diary → nightly report cards → tablet + ntfy.

End-state: a dedicated Mac mini that runs the left-eye scan tick about once a minute Mon–Fri 09:30–16:00 ET, pushes paper-fire alerts to your phone via **ntfy**, keeps the Schwab login alive with a daily auth-watch ping, and operates **without** access to the main machine's vault. It runs on a `claude -p` subscription and needs **no** Anthropic API key.

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

The runtime reads all secrets from the macOS Keychain at run time, never from
files. There is **no** Anthropic API key — the model calls go through a `claude -p`
subscription. The alert channel (ntfy) has no key either: its topic is set in
`runtime/watch/config/limits-and-cooldowns.json` (see §6). What goes in the
Keychain is the market-data credentials:

```bash
# Schwab credentials (mirror the names the iv-viability vault uses)
security add-generic-password -a "$USER" -s "mirai-station/schwab-app-key"     -w "…"
security add-generic-password -a "$USER" -s "mirai-station/schwab-app-secret"  -w "…"
security add-generic-password -a "$USER" -s "mirai-station/schwab-token-path"  -w "$HOME/.local/share/mirai-station/schwab-token.json"

# ThetaData / Cassandra's Edge bearer — the native SPX chain (primary GEX source).
# Stored under the service name the iv-viability vault reads (vault.CASS_SERVICE).
security add-generic-password -a "$USER" -s "iv-viability-cassandra"           -w "<bearer-token>"
```

Test retrieval:
```bash
security find-generic-password -a "$USER" -s "iv-viability-cassandra" -w
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

mirai's MCP feeds are the **Cassandra's Edge** remote servers — HTTP MCP endpoints at `https://*.cassandrasedge.com/mcp`, authenticated per-server with a bearer token. The `market-research` endpoint also fronts the **ThetaData** native SPX chain (the primary GEX source). The morning Macro-Mood brief (`macro_mood.py`) uses **twitter, reddit, fetch**. (Other servers exist on the Edge but the routine does not use them.)

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

## 6. Phone alerts (ntfy)

The only push channel is **ntfy** (the old Discord webhook path was removed
2026-07). It needs no account and no Keychain entry — just a long, unguessable
topic name shared between the mini and your phone.

1. Pick a topic, e.g. `mirai-station-<random>`.
2. Set it in `runtime/watch/config/limits-and-cooldowns.json` under the `ntfy` block.
3. Install the **ntfy** app on your phone and subscribe to that same topic.

Fresh paper fires, wall-breach mood re-dives, and EOD direction scoring push
through it; every push intent is also logged to
`state/logs/watch-pushes-YYYY-MM-DD.jsonl`. Quiet tape → no push, by design.

## 7. Install LaunchAgents

```bash
~/.claude/plugins/mirai-station/runtime/scripts/install-launchd.sh
```

This symlinks the plists into `~/Library/LaunchAgents/` and bootstraps them via `launchctl`. Seven agents:

| Label | Cadence | What |
|---|---|---|
| `com.mirai-station.left-eye`      | every 60 s (gates to RTH) | `run-watch-left-eye.sh` — the scan tick (Phase 1 hunter scan) + ntfy alerts (Phase 2, always) |
| `com.mirai-station.caffeinate`    | always running            | keeps the mini awake |
| `com.mirai-station.lob-collector` | every 60 s                | Layer-2 LOB flow collector (shadow) |
| `com.mirai-station.viewstation`   | always running            | the Nightglass tablet HTTP server on :8787 |
| `com.mirai-station.macro-brief`   | daily 09:00 ET            | morning Macro-Mood brief (`run-macro-brief.sh`) |
| `com.mirai-station.gex-polarity`  | daily 16:15 ET            | after-close A/B report cards + LOB nightly fold (`run-gex-polarity.sh`) |
| `com.mirai-station.auth-watch`    | daily 08:00 ET            | Schwab token keep-alive + dead-bearer ping (`run-auth-check.sh`) |

> `StartCalendarInterval` jobs fire on the mini's local Pacific time (launchd
> ignores the plist `TZ`); the plist comments document the PT↔ET mapping.

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
launchctl list | grep mirai-station   # all seven should be listed
ps -ef | grep caffeinate              # caffeinate should be running
```

---

## Operating outside market hours

The market-hours gate (`runtime/watch/intraday/market_status.py`) is a self-contained NYSE date-math calendar (holidays + half-days, no network call). Outside market hours the scan phase exits instantly; launchd still wakes left-eye every 60 s, that's fine and cheap (the alert phase still runs, which is how EOD scoring lands after the close).

If you want fewer wake-ups, the optional `StartCalendarInterval` block in the plist already enumerates :00/:05/:10/…/:55 — you can constrain it to specific weekdays + hours and remove `StartInterval`. Default leaves both in: launchd will fire at whichever comes first.
