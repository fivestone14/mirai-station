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
# Schwab (the live quotes and minute bars, SPX and SNDK alike). The login lasts
# 7 days; the auth-check job pings the phone as it nears the end. To sign in
# again, at the mini:
~/.local/share/mirai-station/venv/bin/python ~/.claude/plugins/mirai-station/skills/iv-viability/iv_fetcher.py --reauth
# Press Enter to open the browser. Schwab redirects to https://127.0.0.1:8182,
# which answers with a self-signed certificate: pass the browser's warning, sign
# in and approve. "Re-auth complete. New 7-day refresh token stored." means it
# took; nothing needs a restart. Confirm (prints the login's age and days left):
~/.claude/plugins/mirai-station/runtime/scripts/run-auth-check.sh
# (The check this replaced read a Keychain item, mirai-station/schwab-token-path,
# that no code writes.)

# ThetaData / Cassandra's Edge (the native SPX chain AND the SNDK chain).
# There is nothing to paste: the endpoint moved to AuthKit OAuth on 2026-09-09
# and its ACCESS tokens live 300 seconds, so a static bearer is dead five
# minutes after it is minted. Enrol once, interactively, at a browser:
python3 skills/mirai-left-eye/native_gex_feed.py --login           # at the mini
python3 skills/mirai-left-eye/native_gex_feed.py --login --manual  # over SSH
python3 skills/mirai-left-eye/native_gex_feed.py --status          # confirm
# The refresh token goes to the Keychain and every scan spends it for a short
# access token. No restart needed — the vault reads Keychain on every call.
#
# --login opens a browser and catches the redirect on 127.0.0.1:8765.
# --login --manual prints the URL instead and takes the redirected address back
# by paste, for when the browser is on a different machine than the station.
```

> [!warning] The old line here was wrong twice over
> It said `security add-generic-password -a "$USER" -s "iv-viability-cassandra"`,
> but the vault reads the account **`cassandra_edge_token`**, not `$USER` — a
> hand re-key wrote to an item nothing ever read. And since 2026-09-09 no pasted
> bearer survives five minutes anyway. Use `--login`.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `launchctl list` shows `Status: 78` for an agent | exit code != 0; check stderr | `tail /tmp/mirai-station.<label>.err` |
| `/tmp/mirai-station.<label>.err` has a line ending its market check with `FAILED (rc=N)` and the job exits 1 | the market-hours gate itself failed (venv, import). The gate exits 0 when the market is open and 3 when it is closed, and only 3 skips quietly | re-run `venv-bootstrap.sh`. Gated this way: sndk, sndk-read, sndk-bars, lob-collector, book-collector, sndk-jev, watch-left-eye (which still runs its alert pass before exiting 1) |
| Phone: "SNDK reader silent" | the reader stopped writing read rows while the scanner still writes; a silent scanner is paged under its own name instead | `tail /tmp/mirai-station.sndk-read.err`; check `SNDK_READ_DISABLE`. "SNDK reader back" follows once a read row lands |
| "schwab module not found" | venv not provisioned or wrong python | re-run `venv-bootstrap.sh`; confirm shebang resolves |
| GEX read falls back to SPY-proxy every scan | expired Cassandra/ThetaData login | `native_gex_feed.py --login` (see above); auth-watch pings on this |
| SNDK scanner silent + SPY-proxy at the same moment | one credential, both casualties — it is never two faults | `native_gex_feed.py --status`, then `--login` |
| `--login` says the server issued no refresh token | `offline_access` scope refused | check the account's permitted scopes with the provider |
| `--login` cannot reach the browser (headless/SSH) | nothing can connect to 127.0.0.1:8765 | `--login --manual` and paste the redirected URL back |
| `--login` refused at registration (422) | the provider changed what it accepts | the error quotes the field it disliked; fix `_register`'s body in `cassandra_oauth.py` |
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
