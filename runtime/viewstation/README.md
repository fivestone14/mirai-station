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

| | **SPX** (the main stack) | **SNDK Pro** |
|---|---|---|
| What it is | A stock index, traded through 0DTE options | One company's shares, traded through weekly options |
| Expiry cadence | An expiry **every day** — 0DTE mechanics all day, every day | **Weekly** expiries, so most days have no expiry at all and the book is standing positioning rather than same-day mechanics |
| Scale of a day | A typical day's move is a fraction of a percent | A typical day's move is **8–10% of the share price** — enormous, and every threshold has to be re-measured for it |
| Reasoner | Watchtower (`wt-11`), blind vote then reveal, its own memory in `state/spx_rag` | `sndk_read` (`strikes-7`), reads the Strikes Payload cold, its own memory in `state/sndk_rag` |
| Scans land in | `state/reversion/` | `state/sndk_reversion/`, with the minute bars in `state/sndk_bars/` and the side packets in `state/sndk_side/` |
| Documents per scan | One: the scene | **Three**: the Strikes Payload the model reads (`sndk_board`), the legacy Scene Payload still built for the wake gate and the memory (`sndk_read`, deprecated 2026-09-05), and the bar-anchored side packet (`side-1`) — the last two are records and displays, never a prompt |
| Extra layers | Siege, the Fade and Break lenses, the dated-book sidecar, LOB flow | **None of those** — they are SPX-only and show as dormant chips on the SNDK tab, by design, not by fault |
| Maturity | The mature stack; the graded record, the learning loop and the paper scoreboard all belong to it | **Display-only on this page.** No grading, no ledger, nothing trades off it |

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

**SNDK** — `Map`, `Payload` (Payload · Side · Diary · Memory), `Day Visual LOB`

| Surface | What it shows |
|---|---|
| **Map** | The single-stock map: net gamma by strike, the walls as ticks called out into the plot, open interest, the flip band, the session path shaded against its open, and three header drawers — the model's read, the readout (gamma, dealer delta, regime, expected move, walls), and the legend. |
| **Payload** | What the reading model is actually handed: the Strikes Payload JSON, rebuilt live through `sndk_board.build_scene_v2`, with the wake-gate thresholds read off the reader's own constants. The payload segment is a dropdown: the Strikes Payload (sent to the model), the Scene Payload v5 (legacy, deprecated, still built for the gate) and the Gate Payload (the old magnet and walls, store only). The Pipeline Architecture button opens `static/pipeline.html` in a modal: the pipeline replayed on a recorded session (every scan, book, bar and payload firing at the moment it did, with a clock, a spike strip and the calls table) above the component cards. Its Diary view is the day's rows with a dictionary of every field; its Memory view reads the model's RAG store through the same CLI the model uses. |
| **Payload → Side** | The second packet, built on the same scan from the minute-bar sidecar rather than the options book, through `sndk_side.build_side`. **Never sent to a model** — it is a record and a display, so it is allowed to be larger than the scene and none of it is a forecast. The header says the things a JSON dump cannot: that every time in it is derived from a bar index, how many bars older the option-book levels are than the price, whether any of the packet's own checks failed, and what it declares it cannot see. |
| **Day Visual LOB** | The SPX order book through a session as a board of price by time — what size was added and removed at each price — from `book_flow`'s recorded tape, drawn in three.js (`lob3d.js`). It is an SPX surface that sits in the SNDK group of the rail for now. |

It auto-refreshes on its own clock — the SPX side every 60 s and on ⟳, the SNDK
side on its own pollers (quote, scan, read, tape) while its tab is showing and
never while it is hidden. **Faithful to live data:** the SPX surfaces reuse the
project's own renderers (`dashboard.py`, `reversion_lens.py`), so every number
matches the canonical Obsidian views, and embed those markdown views verbatim for
the prose; the SNDK surfaces rebuild the model's scene through `sndk_read`'s own
functions, so what the tab shows is what the model would actually be sent.

### The phone (`/m`)

A second, far smaller set of pages for a glance on the way somewhere, not the
viewstation reflowed. One bottom tab bar on every page: **Now**, **Reads**,
**Forecast**. The Android shell (`mirai-mobile`, the APK under `static/m/`)
opens `/m`.

| Page | What it shows |
|---|---|
| **Now** — `/m` (`static/m/index.html` with `page.js`, `glance.js`, `press.js`) | The SNDK glance: the latest reading, a chart of the session from the minute bars with the walls and the strike with the most contracts, and the last half hour against the stock's own record. Reads `/api/sndk/payload`, `/api/spot` and the raw explorer. |
| **Reads** — `/m/reads.html` | The SPX reads: every graded 30-minute call, a day at a time, from `state/spx_jev/integral_grades.jsonl` through the raw explorer. |
| **Forecast** — `/m/jev-spx.html` | The SPX forecast: the combined call and JEV's own, the opening lane and the pre-market call, the calls in play with their results, the learning loop, Claude's read against the usual, and the questions behind the card — from the `state/spx_jev/` cards, `mirai_prediction/scoreboard.json` and `state/spx_claude_forecast/latest.json` through the raw explorer, with the live price from `/api/spot`. `/m/jev.html` is the SNDK JEV card; the tab bar's Forecast link still goes through it, and it forwards a phone that chose SPX to this page. |

`sheet.js` is the one copy of how a phone sheet opens and closes, loaded by
every page that has one. The pages keep their own copies of the palette and
the sheet's CSS on purpose (a shared stylesheet would be a render-blocking
re-fetch on every open over the tunnel); the tests hold the copies equal.

## Architecture

```
runtime/viewstation/
  server.py             stdlib ThreadingHTTPServer — serves the pages + JSON; no third-party deps
  snapshot.py           the JSON builders: the live spot and its tape, a replay day, the SNDK payload
                        and its refresher, the pipeline replay, the memory overview
  static/               index.html (the desktop, one self-contained page) · pipeline.html · voice.js
                        · lob3d.js + three.module.min.js + addons/ · manifest · icon
  static/m/             the phone: index.html + page.js + glance.js + press.js (Now) · reads.html
                        · jev-spx.html · jev.html · sheet.js · the typeface · the Android APK
  tests/                pytest; the tests that run the pages' JavaScript need node
```

Every route `server.py` serves:

**Pages and files**
- `GET /` and `/index.html` — the desktop page.
- `GET /m`, `/m/` and `/m/index.html` — the phone's Now page (the Android shell opens `/m`).
- Anything else under `static/` by its path — `/m/jev-spx.html`, `/m/reads.html`, `/m/jev.html`, `/pipeline.html`, the scripts, the font, the APK. A content-hashed `.woff2` is sent `immutable` for a year; everything else `no-cache`.

**Open JSON**
- `GET /api/spot?ticker=SPX` — the streaming price: one Schwab quote behind a 2 s cache, single-flight. A null spot means "keep showing the scan price". SNDK quotes during market hours also land on the spot tape under `state/sndk_tape/`.
- `GET /api/replay?day=YYYY-MM-DD` — the Replay tab: the Watchtower sweep ledger and fires for one past session, graded by the nightly card's own grader.
- `GET /api/lob/days?ticker=SPX` and `GET /api/lob/board?day=YYYY-MM-DD&ticker=SPX&slice_s=60` — the Day Visual LOB board, through `book_flow`; one cold build at a time.
- `GET /api/raw/index` and `GET /api/raw/file?root=state&path=…&limit=500` — the raw explorer over `state/` (`.json`, `.jsonl`, `.txt`, `.md`; `voice/` withheld; two reads at a time; path traversal refused; opened read-only). `MIRAI_STATE_DIR` names the folder, as it does for every SNDK reader.
- `GET /api/health` — liveness.
- `GET /api/version` — the page's build id (`index.html` mtime); every open page polls it every 10 s and shows a "new version · tap to refresh" pill when it changes.
- `GET /api/whoami` — who the front door says you are, and whether that is the permitted user.

**Named-user JSON** — `?user=will`, or the front door's forwarded user (`X-Forwarded-User` and its kin); otherwise 403 `locked`. A route-level name check, never auth: the payload is derivable from the raw explorer anyway.
- `GET /api/sndk/payload` — the SNDK Payload tab and the phone's Now page: the Strikes Payload JSON `sndk_board.build_scene_v2` hands the model, rebuilt through the reader's own code and served from a cache a refresher thread keeps warm (a rebuild when a diary row, a reading or a bar lands, or a minute passes). Beside `scene`: `legacy` (the Scene Payload v5, labelled deprecated), `gate_payload` (the old magnet and walls), `side` (the bar-anchored packet, a sibling of the scene and never sent to a model), `gates` read off the reader's own constants, and for the phone `earlier_half_hours` and `since_read`. `user_prompt` and `scene_chars` are pinned to the scene alone.
- `GET /api/sndk/pipeline?day=YYYY-MM-DD` and `GET /api/sndk/pipeline/days` — the Pipeline Architecture replay for `static/pipeline.html`: a recorded session's scans, distinct books, minute bars and read rows as minutes since the open, derived from the rows' own clocks.
- `GET /api/sndk/memory?kind=overview|query|series&…` — the Memory view: an inventory of `state/sndk_rag`, or the same `sndk_rag.py` CLI the model runs, in a subprocess under this server's interpreter.
- `GET /api/_access` — the last 300 requests (time, path, Host, client, UA, status), for telling which device is polling. In memory only.

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
and leave it on the Map tab for an ambient glance.

> The LAN IP (`YOUR-MAC-LAN-IP`) can change if the router reassigns leases — prefer the
> `.local` name, or set a DHCP reservation for the Mac mini on your router.
