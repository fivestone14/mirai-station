# Mirai Watch — the intraday runtime

> Rewritten 2026-07-04. The old README described the retired nine-voter bet system
> (reconcile → learn → scan phases); that architecture was deleted in the 2026-07-03
> gex-only restructure.

What actually runs now, every trading day:

| When | What | Entry point |
|---|---|---|
| every 60 s (launchd `left-eye`) | the SCAN: Fade Lens on SPX — flow sensor → gravity engine → gates → diary row + notes | `skills/mirai-left-eye/hunter.py` via `runtime/scripts/run-watch-left-eye.sh` |
| intraday (inside the scan) | fresh-fire phone pushes, the wall-breach bell and the feed sirens | `watch/intraday/gex_alerts.py` |
| 16:15 ET (`gex-polarity`) | the REPORT CARDS: grade both engines + the lens against the tape | `skills/mirai-left-eye/gex_polarity_ab.py` |
| 05:00 PT daily (`auth-watch`) | Schwab 7-day token keep-alive ping | `watch.intraday.auth_check` |
| every 5 min (`sndk-deadman`) | pages the phone when the SNDK scanner or its reader stops writing | `watch.intraday.sndk_deadman` via `run-sndk-deadman.sh` |
| every 5 min (`spx-premarket-deadman`) | pages the phone for a pre-market checkpoint with no read, or a close-out that never landed | `watch.intraday.spx_premarket_deadman` via `run-spx-premarket-deadman.sh` |

Package layout:

- `intraday/` — `gex_alerts.py` (fire pushes, the wall-breach bell, the feed sirens), `bayes.py` (shrink-to-neutral posterior; its one reader, the macro brief, retired 2026-10-11), `settings.py` (config accessors), `market_status.py` (RTH gate), `auth_check.py`/`token_watcher.py`/`reauth.py` (token keep-alive: the daily runner, the day-6 detector, the one-tap re-login link), `sndk_deadman.py` and `spx_premarket_deadman.py` (the two watchdogs above)
- `cli.py` — thin dispatcher for the launchd jobs (`gex-alerts`, `doctor`)
- `claude_cli.py` — the `claude -p` subprocess wrapper (timeout, one retry, JSON extraction); its one caller, the macro brief, retired 2026-10-11 (the Watchtower makes its own call)
- `push.py` / `intraday/push_ntfy.py` — phone pushes via ntfy
- `paths.py` — state-dir resolution
- `config/` — `limits-and-cooldowns.json` (auth, ntfy, models, the wall-breach bell) + `learning-settings.json` (bayes prior)

Everything is shadow/paper: fires are marked live only when `reversion_lens.live_allowed()`
clears both the hand switch and the Wilson promotion gate.
