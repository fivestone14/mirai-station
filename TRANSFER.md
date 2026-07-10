# Transferring mirai-station to a fresh Mac mini

> [!warning] 2026-07-03 gex-module restructure
> This document predates the restructure where the nine-voter/bet-watching
> system was retired. Live shape: Shift Manager (hunter) → Fade Lens →
> Gravity Engine + Flow Sensor → diary → report cards → storytellers +
> alert bell. See docs/gex-glossary.md and docs/salvage-notes.md.


The whole plugin (including Mirai Watch) is self-contained inside this directory. To move it to another Mac, you copy the directory, run one setup script, populate Keychain secrets, and load the launchd plists. Everything else (venv, LanceDB, logs) is derived at runtime.

## Prerequisites on the target Mac

- macOS (Apple Silicon recommended; Intel works).
- Python 3.9+ installed at `/usr/bin/python3` or `/opt/homebrew/bin/python3`.
- Claude Code CLI installed (`brew install claude` or per anthropic docs) and signed into the Claude subscription you want the agent to use.
- The user account that will run the launchd jobs has access to its own Keychain.

## Steps

### 1. Copy the plugin directory

```bash
# On the source Mac (this one):
tar -C ~/.claude/plugins -czf /tmp/mirai-station.tar.gz mirai-station

# Transfer the tarball (scp / AirDrop / external drive). Then on the target Mac:
mkdir -p ~/.claude/plugins
tar -C ~/.claude/plugins -xzf ~/Downloads/mirai-station.tar.gz
```

Verify the directory landed at `~/.claude/plugins/mirai-station/`.

### 2. Run the setup script

```bash
bash ~/.claude/plugins/mirai-station/runtime/scripts/setup-watch.sh
```

This is idempotent. It:
- Creates the venv at `~/.local/share/mirai-station/venv` (outside the plugin dir so a re-copy of the plugin doesn't wipe it).
- Installs Mirai Watch dependencies (`langgraph`, `langgraph-checkpoint-sqlite`, `lancedb`, `pandas`, `pyarrow`) per `runtime/watch/requirements.txt`.
- Creates `state/logs/` and `state/locks/`.
- Verifies LangGraph imports cleanly.

If you also need the legacy skill deps (oracle, algo-read, hunter.py), run `runtime/scripts/venv-bootstrap.sh` afterward — that installs the broader skill set.

### 3. Install the `mirai-watch` command on PATH

```bash
mkdir -p ~/.local/bin
ln -sf ~/.claude/plugins/mirai-station/runtime/scripts/mirai-watch.sh ~/.local/bin/mirai-watch
# Ensure ~/.local/bin is in your shell's PATH (zsh):
grep -q 'HOME/.local/bin' ~/.zshrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
exec zsh -l
```

Sanity check:

```bash
mirai-watch help
```

### 4. Populate Keychain secrets

The plugin reads secrets from the macOS Keychain at runtime (never committed to git). On the target Mac:

```bash
# Anthropic API key (only used by legacy non-subscription paths; safe to set even under subscription)
security add-generic-password -a "$USER" -s "mirai-station/anthropic-api-key" -w "sk-ant-..."

# Discord webhook for the legacy discord-alert.sh (kept for backward compat; Mirai Watch's push channel is TBD)
security add-generic-password -a "$USER" -s "mirai-station/discord-alert-webhook" -w "https://discord.com/api/webhooks/..."

# Schwab credentials (only needed for left-eye hunter.py)
security add-generic-password -a "$USER" -s "mirai-station/schwab-app-key"      -w "..."
security add-generic-password -a "$USER" -s "mirai-station/schwab-app-secret"   -w "..."
security add-generic-password -a "$USER" -s "mirai-station/schwab-refresh-token" -w "..."
```

Skip any secret whose feature you aren't using; the install will not fail.

### 5. Smoke test the pipeline (dry-run)

```bash
mirai-watch tick --dry-run
```

This should print a streaming `[HH:MM:SS] node → outcome` log, run through the whole unified tick graph, and exit 0 without dispatching anything.

If it passes, run the pytest suite:

```bash
cd ~/.claude/plugins/mirai-station/runtime && \
~/.local/share/mirai-station/venv/bin/python -m pytest watch/tests/ -v
```

The whole suite should pass.

### 6. Load the launchd schedule

```bash
mirai-watch activate
mirai-watch status
```

`status` should show three jobs loaded: `com.mirai-station.left-eye`, `com.mirai-station.caffeinate`, `com.mirai-station.auth-watch`.

The schedule:
- Left-eye: every 5 minutes; the unified tick graph's first node enforces the 09:30–16:00 ET market-hours gate (it exits instantly when the market is closed).
- Caffeinate: keeps the Mac mini awake.
- Auth-watch: daily at 08:00 ET — the Schwab token keep-alive ping.

### 7. Push channel

The live channel is **ntfy**: `runtime/watch/intraday/push_ntfy.py` is registered into `runtime/watch/push.py` via `set_channel()` on real (non-dry) ticks. Set the topic in `runtime/watch/config/limits-and-cooldowns.json` (`ntfy.topic`) and subscribe your phone to it. Every push intent is also logged to `state/logs/watch-YYYY-MM-DD.jsonl`, so dry-runs and real runs leave the same trail. (`runtime/scripts/discord-alert.sh` is a legacy webhook utility kept for backward compatibility.)

## Files that travel with the plugin

```
mirai-station/
├── plugin.json                          ← manifest
├── README.md                            ← top-level plugin docs
├── TRANSFER.md                          ← this file
├── agents/mirai.md                      ← the mirai agent
├── skills/                              ← right-eye, left-eye, oracle, algo-read, ...
├── knowledge/mirai/                     ← dossiers, reflexes, thesis registry
├── runtime/
│   ├── launchd/
│   │   ├── com.mirai-station.left-eye.plist     ← every 5m → run-watch-left-eye.sh
│   │   ├── com.mirai-station.caffeinate.plist   ← keeps the mini awake
│   │   └── com.mirai-station.auth-watch.plist   ← daily 08:00 ET → run-auth-check.sh
│   ├── scripts/
│   │   ├── env.sh                       ← shared env (paths, helpers; no secrets read at top level)
│   │   ├── setup-watch.sh               ← one-shot installer (venv + deps)
│   │   ├── venv-bootstrap.sh            ← installs the broader legacy skill deps
│   │   ├── install-launchd.sh           ← symlink + bootstrap the three plists
│   │   ├── uninstall-launchd.sh         ← bootout + remove the three plists
│   │   ├── mirai-watch.sh               ← CLI dispatcher (activate/deactivate/status/tick/doctor)
│   │   ├── run-watch-left-eye.sh        ← launchd wrapper: reconcile → learn → scan → tick
│   │   ├── run-auth-check.sh            ← launchd wrapper: Schwab token keep-alive
│   │   └── discord-alert.sh             ← legacy push utility (kept; pulls webhook on demand)
│   └── watch/                           ← Mirai Watch LangGraph orchestrator
│       ├── __init__.py
│       ├── README.md                    ← tick-graph diagram + walkthrough
│       ├── requirements.txt
│       ├── paths.py
│       ├── log.py
│       ├── budget.py
│       ├── claude_cli.py
│       ├── market_feed.py
│       ├── push.py                      ← push-channel registry (ntfy on real ticks)
│       ├── graph.py                     ← LangGraph checkpointer
│       ├── tick_graph.py                ← the unified tick StateGraph
│       ├── right_eye_skill.py           ← loader for the mirai-right-eye skill package
│       ├── cli.py                       ← python -m watch.cli tick|learn|doctor
│       ├── config/                      ← signal-triggers / signal-metadata / learning-settings / limits-and-cooldowns
│       ├── intraday/                    ← the net-new thinking core (signals, derivatives, yardstick, regime, bayes, learn, …)
│       └── tests/                       ← pytest suite
└── state/                               ← runtime state (DO NOT commit / DO NOT copy across machines)
    ├── right_eye.lance/                 ← LanceDB (regenerates from scratch on first run)
    ├── logs/                            ← daily JSONL streaming logs
    ├── locks/                           ← per-thesis advisory locks
    └── checkpoints.sqlite               ← LangGraph tick-graph checkpoints
```

State (LanceDB, logs, checkpoints) is per-machine. Do not copy `state/` between Macs — each instance learns its own signal posteriors and news store from scratch.

## Uninstalling

```bash
mirai-watch deactivate                   # unload launchd
rm -rf ~/.claude/plugins/mirai-station   # plugin
rm -rf ~/.local/share/mirai-station      # venv
rm ~/.local/bin/mirai-watch              # CLI symlink
# Optionally remove Keychain secrets:
# security delete-generic-password -a "$USER" -s "mirai-station/..."
```
