# Transferring mirai-station to a fresh Mac mini

> This is the **machine-move** guide (copy the dir, secrets, launchd). For the full
> first-time setup and the architecture, follow the canonical docs:
> `README.md` (what the system is) and `docs/INSTALL.md` (step-by-step provisioning).
> This doc only covers what's different when moving an existing install to new hardware.

The whole plugin is self-contained inside this directory. To move it to another Mac
you copy the directory, provision the venv, populate Keychain secrets, and load the
launchd fleet. Everything derived at runtime (venv, logs, learned state) is *not*
copied — each machine rebuilds it.

## Prerequisites on the target Mac

- macOS (Apple Silicon recommended; Intel works), Python 3.12+.
- Claude Code CLI installed and signed into the Claude subscription the agent should
  use (model calls go through `claude -p` — there is **no** Anthropic API key).
- The user account that runs the launchd jobs has access to its own Keychain.

## Steps

### 1. Copy the plugin directory (code only, not state)

```bash
# On the source Mac (this one) — exclude runtime state, venv is external anyway:
tar -C ~/.claude/plugins \
    --exclude 'mirai-station/state' \
    --exclude '__pycache__' \
    -czf /tmp/mirai-station.tar.gz mirai-station

# Transfer the tarball (scp / AirDrop / drive). Then on the target Mac:
mkdir -p ~/.claude/plugins
tar -C ~/.claude/plugins -xzf ~/Downloads/mirai-station.tar.gz
```

> **Do not copy `state/`.** Each instance rebuilds its own diary, grades, learned
> baselines, and option-book snapshots from scratch — copying them across machines
> mixes two histories.

### 2. Provision the venv + launchd fleet

Follow `docs/INSTALL.md` from §4 onward. The short version:

```bash
cd ~/.claude/plugins/mirai-station
./runtime/scripts/venv-bootstrap.sh      # builds ~/.local/share/mirai-station/venv (schwab-py, scipy, …)
./runtime/scripts/install-launchd.sh     # symlink + bootstrap the 11-agent fleet
```

### 3. Populate Keychain secrets

Model calls use a `claude -p` subscription (no Anthropic API key). Everything else
is a Keychain entry — including the ntfy topic, which is unauthenticated pub/sub
and therefore a credential in its own right:

```bash
# ntfy topic — generated straight into the Keychain; never printed, never in a file.
# NOT in limits-and-cooldowns.json: that file is tracked, and a topic written there
# is a secret committed to the repo (see docs/INSTALL.md §6).
security add-generic-password -U -a "$USER" -s "mirai-station-ntfy" \
  -w "mirai-$(openssl rand -hex 16)"
```

The market-data credentials are **not** enrolled with `security` by hand — the
`iv-viability` vault writes them through `keyring`, which pairs a *service* with an
*account* name the vault expects, and it also mints a Fernet key to encrypt the
token file. Hand-added entries land under the wrong account and the vault never
sees them. Use the interactive setup paths:

```bash
PY=~/.local/share/mirai-station/venv/bin/python

# Schwab — service iv-viability-schwab
# (accounts: api_key, app_secret, callback_url, fernet_key)
$PY skills/iv-viability/iv_fetcher.py --setup

# ThetaData / Cassandra's Edge bearer — the native SPX chain (primary GEX source)
# service iv-viability-cassandra, account cassandra_edge_token
$PY skills/mirai-left-eye/native_gex_feed.py --setup
```

Also copy the `mcpServers` block into the target's `~/.claude.json` (see INSTALL §5)
so the morning macro brief can reach the Cassandra's Edge MCP servers.

### 4. Smoke test

```bash
# One scan tick by hand (bypasses launchd; quiet tape → no output is correct):
./runtime/scripts/run-watch-left-eye.sh

# Confirm the fleet is loaded:
launchctl list | grep mirai-station      # all eleven agents

# Tail today's diary:
tail -f state/reversion/$(date +%Y-%m-%d).jsonl
```

If a paper fire clears threshold, an ntfy push lands on the subscribed phone.

## Files that travel with the plugin

```
mirai-station/
├── plugin.json                          ← skill manifest + runtime config
├── README.md                            ← what the system is (start here)
├── TRANSFER.md                          ← this file
├── skills/
│   ├── mirai-left-eye/                  ← the SPX brain: hunter · reversion_lens (3 heads) ·
│   │                                       lefteye_gex_box · native_gex_feed · watchtower ·
│   │                                       dated_gex_feed · gex_polarity_ab · …
│   ├── sndk-pro/                        ← the isolated SNDK station (beta, record-only)
│   ├── mirai-voice/                     ← ears · mouth · the day-session conversation (:8788)
│   ├── siege/                           ← effort-at-the-wall sensor (shadow)
│   ├── lob-flow/                        ← Layer-2 order-book FLOW sensor (shadow)
│   ├── iv-viability/                    ← per-contract IV gate + the Schwab/Cassandra vault
│   └── mirai-right-eye/                 ← embedder only (RAG retired) → feeds macro-mood
├── runtime/
│   ├── launchd/                         ← 11 LaunchAgent plists (the fleet)
│   ├── scripts/                         ← env.sh · venv-bootstrap · install-launchd · run-*.sh
│   ├── viewstation/                     ← the Nightglass tablet (read-only HTTP :8787)
│   └── watch/                           ← the tick chassis: cli · intraday/ (market_status,
│                                           gex_alerts, push_ntfy, macro_mood, auth) · tests
└── state/                               ← runtime state — DO NOT copy across machines
    ├── reversion/                       ← the SPX diary + nightly grades
    ├── sndk_reversion/ · sndk_reads/ · sndk_gex/ · sndk_rag/   ← the SNDK station
    ├── gex_fills/ · dated_gex/ · gex_learn/ · gex_uw/
    ├── siege/ · lob_flow/ · market_expectation/ · tape_prev/
    └── voice/ · logs/
```

> Historical note: an earlier "Mirai Watch" LangGraph tick-graph (`graph.py`,
> `tick_graph.py`, a LanceDB news store) and the nine-voter skills (`oracle`,
> `algo-read`) were retired in the 2026-07-03 gex-only restructure. If you see them
> referenced in old notes or a stale `requirements.txt` pin (`langgraph`), they are
> not part of the live system.

## Uninstalling

```bash
./runtime/scripts/uninstall-launchd.sh   # bootout + remove the plists
rm -rf ~/.claude/plugins/mirai-station   # plugin
rm -rf ~/.local/share/mirai-station      # venv
# Optionally remove Keychain secrets:
# security delete-generic-password -a "$USER" -s "mirai-station/schwab-app-key"  (etc.)
# security delete-generic-password -a "$USER" -s "iv-viability-cassandra"
```
