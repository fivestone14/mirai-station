# Mirai Viewstation — the live view

A read-only web app that shows the Mirai Awakening brain. It reads the *real*
live state on the Mac mini and renders it for whatever is to hand — desktop,
tablet or phone, in any browser. It started as a wall display for a Galaxy Tab
and outgrew that; the layout is responsive and nothing here assumes a screen
size.

## Two instruments, one page

The tab rail carries **two separate systems** that happen to share a browser
window. They are not the same engine pointed at two tickers — they have their own
scanners, their own stores, their own reasoners and their own eras, and a change
to one does not touch the other. Knowing which one you are looking at is the
first thing to know about this page.

| | **SPX** (the main stack) | **SNDK Pro** (beta) |
|---|---|---|
| What it is | A stock index, traded through 0DTE options | One company's shares, traded through weekly options |
| Expiry cadence | An expiry **every day** — 0DTE mechanics all day, every day | **Weekly** expiries, so most days have no expiry at all and the book is standing positioning rather than same-day mechanics |
| Scale of a day | A typical day's move is a fraction of a percent | A typical day's move is **8–10% of the share price** — enormous, and every threshold has to be re-measured for it |
| Reasoner | Watchtower (`wt-11`), blind vote then reveal, its own memory in `state/spx_rag` | `sndk_read` (`sr-6`), reads the scene cold, its own memory in `state/sndk_rag` |
| Scans land in | `state/reversion/` | `state/sndk_reversion/` |
| Extra layers | Siege, the Fade and Break lenses, the dated-book sidecar, LOB flow | **None of those** — they are SPX-only and show as dormant chips on the SNDK tab, by design, not by fault |
| Maturity | The mature stack; the graded record, the learning loop and the paper scoreboard all belong to it | **Beta and display-only.** No grading, no ledger, nothing trades off it |

The two never share a threshold. SNDK's constants were re-measured on SNDK's own
tape — the SPX numbers would be meaningless at ten times the daily range — and
the port in the other direction (`wt-11`) re-measured every SPX threshold rather
than copying SNDK's. If you are editing one and the other looks like it wants the
same fix, that is usually the moment to check whether it actually does.

## Surfaces

**SPX** — `Map`, `Layers`, `Diary`, `Processor`, `Replay`, `Dictionary`

| Surface | What it shows |
|---|---|
| **Map** | The live SPX gamma map: dealer-gamma by strike, the walls, the flip band, the expected-move band, the price path and the plain-English HUD over it. |
| **Layers** | The whole system as a deck of planes — every sensor, lens and record layer, each with what it feeds, its status, and its data opened in a formatted view. |
| **Diary** | Every scan of the session as a timeline; tap one for its plain-English read ⇄ raw JSON, plus the Watchtower's own sentence for the scans it spoke on. |
| **Processor** | Under the hood: the gates, their current states, and how a scan becomes a call. |
| **Replay** | Any past day scrubbed back through the same renderers, with graded calls and the sweep ledger drawn on the tape. |
| **Dictionary** | Every term this page uses, in plain English. |

**SNDK Pro** — `SNDK`, `SNDK payload` (Payload · Side · Memory · Diary)

| Surface | What it shows |
|---|---|
| **SNDK** | The single-stock map: net gamma by strike, the walls as ticks called out into the plot, open interest, the flip band, the session path shaded against its open, and three header drawers — the model's read, the readout (gamma, dealer delta, regime, expected move, walls), and the legend. |
| **SNDK payload** | What the reading model is actually handed: the exact scene JSON, rebuilt live through `sndk_read.build_scene`, with the wake-gate thresholds read off the reader's own constants. Its Memory view reads the model's RAG store through the same CLI the model uses. |
| **SNDK payload → Side** | The second packet, built on the same scan from the minute-bar sidecar rather than the options book, through `sndk_side.build_side`. **Never sent to a model** — it is a record and a display, so it is allowed to be larger than the scene and none of it is a forecast. The header says the things a JSON dump cannot: that every time in it is derived from a bar index, how many bars older the option-book levels are than the price, whether any of the packet's own checks failed, and what it declares it cannot see. |

It auto-refreshes on its own clock — the SPX side every 60 s and on ⟳, the SNDK
side on its own pollers (quote, scan, read, tape) while its tab is showing and
never while it is hidden. **Faithful to live data:** the SPX surfaces reuse the
project's own renderers (`dashboard.py`, `reversion_lens.py`), so every number
matches the canonical Obsidian views, and embed those markdown views verbatim for
the prose; the SNDK surfaces rebuild the model's scene through `sndk_read`'s own
functions, so what the tab shows is what the model would actually be sent.

## Architecture

```
runtime/viewstation/
  server.py             stdlib ThreadingHTTPServer — serves the app + JSON; no third-party deps
  snapshot.py           assembles one live snapshot (reuses dashboard.py + reversion_lens.py)
  pipeline.py           builds the 5-stage Senses→Brain→Record→Memory→Tell map
  static/               index.html · manifest · icon  (single self-contained page — all CSS/JS inlined)
```
- `GET /api/snapshot` — the assembled live SPX state, 5 s memo cache. SNDK does not go through it: that tab reads its own scans straight out of `state/sndk_reversion/` via the raw explorer.
- `GET /api/pipeline` — the stage map.
- `GET /api/replay?day=YYYY-MM-DD` — feeds the Replay tab from past `state/reversion/*.jsonl`.
- `GET /api/raw/index|file` — opt-in raw explorer (whitelisted to `state/`, the paper
  ledgers, and `config/`; path-traversal blocked; opened read-only).
- `GET /api/health` — liveness.
- `GET /api/version` — the page's build id (`index.html` mtime); every open page polls it every 10 s and shows a "new version · tap to refresh" pill when it changes.
- `GET /api/sndk/payload?user=will` — the SNDK Payload tab: the exact scene JSON `sndk_read.build_scene` hands the model (rebuilt live through the same functions) + the user-message wrapper; 403 unless `user` names the permitted user (`MIRAI_PAYLOAD_USER`, default `will`) or a front door forwards that name — a route-level name check, never auth. The page's matching UI lock (name box + Unlock) was removed 08-22: the tab now opens straight into the live scene, because nothing reaches it that has not already come through the front door. Since 09-03 the same response also carries `side`, the bar-anchored packet rebuilt through `sndk_side`'s own builder — a sibling of `scene`, never a child, because `user_prompt` and `scene_chars` are pinned to the scene alone. A packet that cannot be built comes back as `null` and the scene renders unaffected.

**Read-only, no auth, no writes.** There is no `do_POST` and no route that
writes anything — the one write it ever had (the SNDK reasoning pause) went on
2026-08-23 when the station went public, because a switch that silences the
model is not something a visitor should reach. That pause is an operator control
now: edit `state/sndk_reads/control.json`, which `sndk_read.reasoning_on` reads
and which fails **open**.

The absence of auth is deliberate, not forgotten: reach this server directly and
you get everything, so **never port-forward this port**. The front door is
somebody else's job — Cloudflare Tunnel into Caddy, which holds the password and
proxies here (see the hosting notes). What the server *does* enforce is that a
request is addressed to it by a name it recognises, which is what stops a
DNS-rebinding page reading the raw explorer through a visitor's browser. Public
names come in through `MIRAI_VIEW_HOSTS` (csv) — set it when the hosted name
changes, or the front door starts 403ing everything it forwards.

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

## Open it

**On the same network** (desktop, tablet or phone):

1. macOS **System Settings → Network → Firewall**: if the firewall is on, allow
   incoming connections for Python (or turn it off on a trusted home network).
2. Point a browser at:
   - `http://YOUR-MAC-LAN-IP:8787`  ← the Mac mini's current LAN IP, **or**
   - `http://mirai-station.local:8787`  ← Bonjour name (survives IP changes)

**Over the tailnet:** the same URL with the tailnet address. Tailscale addresses
live in CGNAT space, which Python does not count as private, so `_host_ok` allows
that range explicitly — reaching the view by tailnet IP works without config.

**From anywhere:** the hosted name, behind Cloudflare Tunnel → Caddy's password.
That path never touches this port; see the hosting notes for the chain.

**Make it feel like an app:** browser menu → **Add to Home screen** / **Install**
→ it launches full-screen with no browser chrome (a PWA — still just the browser,
nothing installed from a store). Works on Android, iOS and desktop Chrome alike.
Note the manifest link carries `crossorigin="use-credentials"`: without it the
browser fetches the manifest anonymously, the password wall 401s it, and you are
asked to log in a second time.

**Wall display (optional):** raise the screen timeout (or "never while charging")
and leave it on the Overview tab for an ambient glance.

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
2. **Add to Home screen** (browser menu): you never type the address again, and the icon points at the `.local` name so it follows IP changes.
3. **DHCP reservation** in your router: pin the Mac mini's IP so it never changes (belt-and-suspenders for flaky mDNS).
4. **Router hostname / Pi-hole / dnsmasq**: map a bare name (`mirai`, no `.local`) for the whole network — only needed if mDNS is unreliable.

Revert anything the script did: see the header comment in `setup-friendly-name.sh`.
