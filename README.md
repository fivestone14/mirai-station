# mirai-station

Self-contained gex-module 0DTE system, running unattended on a Mac mini via launchd.

The live shape (post 2026-07-03 restructure): every 5 minutes the **Shift Manager**
(hunter.py) runs the **Fade Lens** — the paper brain — on SPX: the **Gravity
Engine** (lefteye_gex_box, fill-ledger fed, flow cross-checked) maps where dealer
positioning pulls price; the lens bets paper fades toward the magnet; the **diary**
records everything; nightly **report cards** grade both engines; the tablet + notes
tell one dealer story and the **alert bell** pushes fresh fires. Nothing trades real
money until the Wilson-guarded record proves the engine.

Glossary: `docs/gex-glossary.md` · retired-system salvage: `docs/salvage-notes.md`
· backup of the pre-restructure system: `../mirai-station.backup-pre-gexmodule-20260703`

---

## Pre-restructure notes (historical)


Self-contained mirai research stack designed to run unattended on a dedicated Mac mini.

Bundles:
- **`mirai` agent** — autonomous deep-research investment analyst
- **`mirai-left-eye` skill** — intraday 0DTE tape hunter, scheduled every 5 min during market hours
- **Sibling skills** — `oracle`, `algo-read`, `iv-viability` (vendored, no external repo dependency)
- **Brain files** — `knowledge/` (reflexes, patterns, ledger, dossiers)
- **Runtime** — launchd plists + headless `claude -p` wrappers + ntfy phone alerts (legacy Discord webhook kept for backward compat)
- **Python venv** — provisioned at `~/.local/share/mirai-station/venv` by `runtime/scripts/venv-bootstrap.sh`

## Quickstart

```
cd ~/.claude/plugins/mirai-station
./runtime/scripts/venv-bootstrap.sh
./runtime/scripts/install-launchd.sh
```

See `docs/INSTALL.md` for full Mac mini setup (auto-login, Caffeinate, MCP servers, Keychain secrets, Discord webhook).

See `docs/OPERATIONS.md` for runbook (start/stop, logs, troubleshooting, alert routing).

## Layout

```
mirai-station/
├── plugin.json
├── agents/mirai.md
├── skills/
│   ├── mirai-left-eye/      hunter.py + 13 signal modules (index-only: SPX/QQQ)
│   ├── oracle/              dealer-positioning bookmap
│   ├── algo-read/           regime classifier
│   └── iv-viability/        per-contract IV gating
├── knowledge/mirai/         brain files + dossiers + queues
├── runtime/
│   ├── launchd/             *.plist files
│   └── scripts/             wrappers + bootstrap + alert
├── state/                   logs + queues + dossiers (runtime-mutable)
└── docs/                    INSTALL + OPERATIONS
```

## Mandate

mirai-station is the always-on twin. The main machine retains the interactive vault; mirai-station emits alerts (ntfy push to phone) and writes dossiers to its own `state/` directory. Cross-machine sync of the vault is out of scope — by design.
