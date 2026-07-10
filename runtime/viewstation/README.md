# Mirai Viewstation — live tablet view

A tiny, read-only web app that shows the Mirai Awakening brain on a tablet
(built for the **Galaxy Tab A11+**, works on any browser). It reads the *real*
live state on the Mac mini and renders five surfaces:

| Surface | What it shows |
|---|---|
| **Overview** | Ambient, glanceable: the 0DTE enter/stay-out entry widget (SPX with a mood emoji), real-money scoreboard, paper win-rate ring, morning-mood gauge, SPX regime, the "in one breath" line. |
| **Deep** | Under the hood: live GEX dealer-gamma heatmap, the LOOK→STRETCH→RUNWAY→TURN→SCORE pipeline with current gate states, the GEX-views numbers, per-ticker counts, a tappable day-timeline (tap a scan → plain-English ⇄ raw JSON), and the paper-grading table. |
| **Learning** | The dials it can tune, paper performance + outcome distribution, "what it learned today," the learning loop. |
| **🗺️ Pipeline** | The whole system, stage by stage (Senses → Brain → Pick → Watch → Memory → Settings), each module translated to plain English with its data opened in a formatted view (Plain ⇄ Raw). The raw file/sqlite explorer lives here as a "Browse all raw files" drill-down. |

It auto-refreshes every 60 s and on pull/tap of ⟳. **Faithful to live data:** it
reuses the project's own renderers (`dashboard.py`, `reversion_lens.py`), so every
number matches the canonical Obsidian views; it also embeds those markdown views
verbatim for the prose.

## Architecture

```
runtime/viewstation/
  server.py             stdlib http.server — serves the app + JSON; no third-party deps
  snapshot.py           assembles one live snapshot (imports the skill modules)
  pipeline.py           builds the Pipeline map + the Signals queue (plain-English)
  teach_examples.py     detect untaught fields + safe-merge agent-authored examples
  static/               index.html · app.js · style.css · manifest · icon
```
- `GET /api/snapshot` — the assembled live state (Overview/Deep/Learning).
- `GET /api/pipeline` — the stage map + the signals queue.
- `GET /api/raw/index|file|sqlite` — opt-in raw explorer (whitelisted to `state/`,
  the paper ledgers, and `config/`; path-traversal blocked; sqlite opened read-only).

**Autonomous teaching examples:** when the recommender mines a new signal field,
has a teaching agent author a beginner caption + gauge anchors, and safe-merges them

LAN-only by design: **read-only, no auth, no writes.** Don't port-forward it.

## Run it (on the Mac mini)

Foreground (quick test):
```bash
~/.claude/plugins/mirai-station/runtime/scripts/run-viewstation.sh
# → serving on http://0.0.0.0:8787
```

Always-on (survives reboot/crash) via launchd:
```bash
cp ~/.claude/plugins/mirai-station/runtime/launchd/com.mirai-station.viewstation.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mirai-station.viewstation.plist
# stop:  launchctl unload ~/Library/LaunchAgents/com.mirai-station.viewstation.plist
# logs:  /tmp/mirai-station.viewstation.{out,err}
```
(If a manual `run-viewstation.sh` is already bound to 8787, stop it first so the
launchd copy can take the port.)

## Open it on the Galaxy Tab A11+

1. Put the tablet on the **same Wi-Fi** as the Mac mini.
2. macOS **System Settings → Network → Firewall**: if the firewall is on, allow
   incoming connections for Python (or turn it off on the trusted home network).
3. In Chrome on the tablet, go to:
   - `http://YOUR-MAC-LAN-IP:8787`  ← the Mac mini's current LAN IP, **or**
   - `http://mirai-station.local:8787`  ← Bonjour name (survives IP changes)
4. **Make it feel like an app:** Chrome menu (⋮) → **Add to Home screen** → it
   launches full-screen, landscape, no browser chrome (a PWA — still just the
   browser, nothing installed from the Play Store).
5. **Wall-display / kiosk (optional):** in the tablet's Display settings raise the
   screen-timeout (or set "Screen saver → never while charging"), and keep it on
   the Overview tab for an ambient glance display.

> The LAN IP (`YOUR-MAC-LAN-IP`) can change if the router reassigns leases — prefer the
> `.local` name, or set a DHCP reservation for the Mac mini on your router.

## A friendly, stable address (survives IP/port changes)

The IP can change; the port is pinned (`8787` in the launchd plist + run script, so
it won't drift on its own). To get a clean name that resolves to whatever IP the
Mac currently has — via mDNS/Bonjour, no router setup — run once on the Mac mini:

```bash
sudo bash ~/.claude/plugins/mirai-station/runtime/scripts/setup-friendly-name.sh mirai
#   → http://mirai.local:8787

# optional: also drop the :8787 (root pf redirect 80→8787, persisted at boot)
sudo bash ~/.claude/plugins/mirai-station/runtime/scripts/setup-friendly-name.sh mirai --port80
#   → http://mirai.local
```

Robustness ladder (easiest → most bulletproof):
1. **mDNS name** (the script): `http://mirai.local:8787` — handles IP changes, zero router config. Android's `.local` is usually fine; the IP always works as fallback.
2. **Add to Home screen** on the tablet (Chrome ⋮): you never type the address again, and the icon points at the `.local` name so it follows IP changes.
3. **DHCP reservation** in your router: pin the Mac mini's IP so it never changes (belt-and-suspenders for flaky mDNS).
4. **Router hostname / Pi-hole / dnsmasq**: map a bare name (`mirai`, no `.local`) for the whole network — only needed if mDNS is unreliable.

Revert anything the script did: see the header comment in `setup-friendly-name.sh`.
