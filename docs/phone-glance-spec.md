# SNDK PHONE GLANCE — BUILD SPECIFICATION v1

**One deliverable, implemented verbatim.** Portrait, dark only, 320–412 CSS px wide. Inline SVG built as a string. Plain JS, no framework, no build step, no CDN. Nothing is tappable.

The screen is one price ladder under one price, and it answers three questions in reading order: **what kind of day** (the regime word, 18px, directly under the price), **where price sits** (the ladder, ~48% of the screen), **what happens at the nearest level** (one card, the only place sentences live).

**The one-line change from what ships today:** the cards, the legend strip and the emoji lane are deleted; the walls keep the desktop's hue law (jade = call, coral = put) and gain a weight chip on a fixed 0–20%-of-book scale beside their price; the y-window is frozen between payloads and admits a level only while the day's own range still fills half the plot; and no Greek letter appears anywhere on the page.

---

## 0. THE THREE LAWS THIS SCREEN IS BUILT ON

1. **Honest-absent.** No datum → no mark. Missing must never look like zero, and a measured emptiness is a finding that gets drawn.
2. **One hue, one meaning.** Five hues, each with one job. The single reuse is separated by *form* and is stated in §4.
3. **No Greek on the surface.** Every distance is dollars. The ruler is stated once, in English, in the regime row: `TYPICAL MOVE $67`. `σ` appears in no string on this page. (Measured: 13 of 15 reviewers read a sigma field backwards.)

---

## 1. FRAME AND REGION BUDGET

Six regions, stacked. **Every region keeps its height in every state, including its empty states.** No region ever hides. The page never scrolls.

```
body padding: calc(env(safe-area-inset-top) + 14px)  14px  calc(env(safe-area-inset-bottom) + 14px)
content column CW = documentElement.clientWidth - 28      (356 at 384, 292 at 320, 384 at 412)

  region        normal   short (viewport height <= 700)
  --------------------------------------------------------
  A .masthead      88      76
  B .regime        48      44      border-top 1px --hairline
  C .ladder     var()   var()      border-top 1px --hairline
  D .gate         144     118      border-top 1px --hairline
  E .read          92      76      border-top 1px --hairline
  F .foot          20      18
  --------------------------------------------------------
  FIXED = A+B+D+E+F = 392 normal / 332 short
```

The ladder is the only elastic region and **its height is an explicit pixel value written by JS**, never `flex:1`:

```js
const SHORT = window.innerHeight <= 700;
const FIXED = SHORT ? 332 : 392;
const cs   = getComputedStyle(document.body);
const padV = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);   // resolves env() to px
const LADDER_H = Math.max(200, Math.min(560,
                  document.documentElement.clientHeight - padV - FIXED));
document.documentElement.style.setProperty('--ladder-h', LADDER_H + 'px');
```

Reference results: **384×780 → LADDER_H 360. 320×568 → 208. 412×915 → 495.**

**Defensive guard.** After writing the height, read `document.getElementById('ladder').getBoundingClientRect().height`. If it is under 180, write the SVG as the single string `<text class="p-word" x="10" y="24">LADDER TOO SHORT</text>` and return. (PACK records that the harness *supplies* the chart height and therefore cannot see a CSS collapse. The explicit pixel height makes the collapse impossible; this guard makes it visible if it somehow occurs.)

`resize` → recompute `LADDER_H`, repaint from cache. Do not refetch.

---

## 2. TOKENS — the exact `:root` block

Values are copied byte-for-byte from the Mirai Awakening desktop theme. Do not invent a value; do not use a token that is not on this list.

```css
:root{
  /* ground and structure */
  --ground:#0C1017;
  --surface:#141A24;
  --surface-2:#1B2330;
  --hairline:#28313F;
  --hairline-2:#333E50;

  /* ink, by size not by contrast (all three pass AA on --ground:
     ink 15.2:1, ink-dim 7.35:1, ink-faint 6.19:1) */
  --ink:#E7EBF3;
  --ink-dim:#98A2B6;
  --ink-faint:#8A93A8;

  /* the five meaning hues */
  --iris:#8B9CFF;      /* price, and only price                 */
  --jade:#43C59E;      /* the call side of the book             */
  --coral:#EB6A57;     /* the put side of the book              */
  --lantern:#E7B85C;   /* aging / staleness, and nothing else   */
  --l-grav:#F3AB3E;    /* the magnet, and nothing else (desktop MMETA magnet) */

  /* the two washes, used ONLY as the day-change chip's fill */
  --jade-wash:rgba(67,197,158,.10);
  --coral-wash:rgba(235,106,87,.10);

  --mono:ui-monospace,"SF Mono","JetBrains Mono","Menlo",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",sans-serif;
  --serif:ui-serif,"New York","Iowan Old Style","Palatino",Georgia,serif;

  /* layout */
  --ladder-h:360px;
  --r-mast:88px; --r-regime:48px; --r-gate:144px; --r-read:92px; --r-foot:20px;
}
@media (max-height:700px){
  :root{ --r-mast:76px; --r-regime:44px; --r-gate:118px; --r-read:76px; --r-foot:18px; }
}
```

**Deliberately unused, and they must stay unused:** `--surface-3`, `--iris-dim`, `--tower`, `--shadow`, `--radius`, `--iris-wash`, `--lantern-wash`, every other `--l-*` layer hue. The page has one surface (the gate card), so it needs no other container tokens.

> **The near-miss amber.** The magnet is `--l-grav #F3AB3E`. It is **not** `--lantern #E7B85C` and **not** the shipped phone page's `#d8a13a`. Three warm yellows that are almost the same read as drift; one that is obviously different reads as a decision. `--lantern` is reserved for staleness alone.

---

## 3. TYPE SCALE AND SPACING SCALE

**Six type steps. No others are permitted.**

| step | size / line-height | family, weight | tracking | used for |
|---|---|---|---|---|
| T1 | 40 / 1.00 | mono 600, tnum | −0.02em | the price (1 instance) |
| T2 | 30 / 1.00 | mono 600, tnum | −0.01em | the gate strike (1 instance) |
| T3 | 18 / 1.15 | sans 600 | −0.01em | the regime word (1 instance) |
| T4 | 13 / 1.40 | sans 400 | — | gate mechanism sentence, regime gloss, gate notes |
| T4s | 13 / 1.45 | **serif italic** 400 | — | the model reading sentence, and nothing else |
| T5 | 13 / 1.00 | mono 500, tnum | — | ladder tag prices, the price-chip text |
| T6 | 11 / 1.20 | mono 500, tnum | .02em | freshness chip, gate measures, gauge values, reading age, change chip, edge-marker strikes |
| T7 | 10 / 1.20 | mono 500, **UPPERCASE** | .14em | region labels, in-plot words, axis feet, expiry, ticker (.18em) |
| T8 | 10 / 1.35 | sans 400 | — | the page footer line |

Every numeral on the page is `font-variant-numeric: tabular-nums` so a digit change never reflows its neighbours across a 5-second repaint. The **serif italic** is spent on exactly one thing: the model's sentence — the one voice on the page that is an opinion rather than a measurement. It gets a different *family*, never a different colour.

**Spacing scale: 4 · 6 · 10 · 14 · 20 · 28.** Page gutter 14. Region padding 10 (8 on short). Row gaps 4 and 6. Tag-solver gap 20. No other layout number appears.

---

## 4. THE COLOUR LAW

```
--iris    PRICE, and only price.
          the price rule, the price dot, its halo ring, the filled price chip,
          the masthead live dot. Nothing else on this page is iris.

--jade    THE CALL SIDE OF THE BOOK.
          call wall rules, their weight chips, their bug triangle, their tag
          prices, the gate strike when the named level is a call wall.

--coral   THE PUT SIDE OF THE BOOK. same list, put side.

--l-grav  THE MAGNET. its rule, its glow, its diamond, its tag price.

--lantern AGING. the freshness chip once the book passes stale_book_min, and
          the reading's age once it passes 30 minutes. Nothing else.

--ink / --ink-dim / --ink-faint  everything else. Apparatus is subordinate by
          SIZE, never by contrast — this screen is read outdoors and a
          low-contrast grey outdoors means gone.
```

**The one reuse, separated by form:** the day's change is drawn in jade or coral **as a filled, fully-rounded chip** (`--jade-wash` / `--coral-wash` background). The rule is one sentence and checkable by eye:

> **filled + coloured = the day's move (exactly one object on the screen). stroked or ruled + coloured = a level on the book.**

**Gamma sign colours nothing.** `regime.gamma_sign` is the literal string `"unknown"` on 7.1% of rows, and on the reference payload `frozen_do_not_cite` reports it unchanged for 160 minutes while price repaints every five seconds. Its entire blast radius on this screen is: the regime gloss line, the gate's mechanism sentence, and the page footer. There is **no regime wash and no regime-tinted structure.** If the sign is wrong, one sentence is wrong — not the whole instrument.

---

## 5. DOM

This is the complete markup. Every text node is written with `textContent`. **The SVG string is the only `innerHTML` on the page, and it never contains model output.**

```html
<body>

<section class="masthead" id="masthead">
  <div class="mast-row">
    <span class="ticker"  id="ticker"></span>
    <span class="livedot" id="livedot" hidden></span>
    <span class="expiry"  id="expiry"></span>
    <span class="fresh"   id="fresh"></span>
  </div>
  <div class="mast-price">
    <span class="px"  id="px">—</span>
    <span class="chg" id="chg" hidden></span>
    <span class="lastscan" id="lastscan" hidden></span>
  </div>
</section>

<section class="regime">
  <div class="reg-left">
    <div class="reg-word"  id="regWord"></div>
    <div class="reg-gloss" id="regGloss"></div>
  </div>
  <div class="reg-ruler" id="ruler"></div>
</section>

<section class="ladder" id="ladder"><svg id="svg"></svg></section>

<section class="gate" id="gate">
  <div class="g-lab">
    <span class="g-dir"  id="gDir"></span>
    <span class="g-meas" id="gMeas"></span>
  </div>
  <div class="g-head">
    <span class="g-k"    id="gStrike"></span>
    <span class="g-mech" id="gMech"></span>
  </div>
  <div class="g-load" id="gLoad">
    <div class="g-row" id="gRowA">
      <div class="g-bar"><i id="gBarA"></i></div><div class="g-val" id="gValA"></div>
    </div>
    <div class="g-row" id="gRowB">
      <div class="g-bar behind"><i id="gBarB"></i></div><div class="g-val" id="gValB"></div>
    </div>
  </div>
  <div class="g-foot" id="gFoot"></div>
</section>

<section class="read" id="read">
  <div class="rd-lab"><span>MODEL READ</span><span class="rd-age" id="rdAge"></span></div>
  <div class="rd-body"><span class="rd-mark" id="rdMark"></span><span class="rd-line" id="rdLine"></span></div>
</section>

<div class="foot" id="foot"></div>

<div class="fail" id="fail" hidden>
  <p class="fail-1" id="fail1"></p>
  <p class="fail-2" id="fail2"></p>
</div>

<script src="/m/glance.js"></script>
<script src="/m/page.js"></script>
</body>
```

`<head>` carries: `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`, `<meta name="color-scheme" content="dark">`, `<meta name="theme-color" content="#0C1017">`. There is no `prefers-color-scheme: light` branch anywhere.

---

## 6. CSS — complete

```css
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;margin:0;padding:0}
html{height:100%}
body{
  height:100dvh; overflow:hidden;
  background:var(--ground); color:var(--ink);
  font:400 13px/1.4 var(--sans);
  -webkit-font-smoothing:antialiased;
  padding:calc(env(safe-area-inset-top) + 14px) 14px calc(env(safe-area-inset-bottom) + 14px);
  display:flex; flex-direction:column;
}
body.failed > section, body.failed > .foot{display:none}

/* ============ A. MASTHEAD ============ */
.masthead{height:var(--r-mast);flex:none;display:flex;flex-direction:column;
          justify-content:center;gap:6px}
.mast-row{display:flex;align-items:center;gap:8px;height:14px}
.ticker{font:500 11px/1.2 var(--mono);letter-spacing:.18em;color:var(--ink-dim)}
.livedot{width:5px;height:5px;border-radius:50%;background:var(--iris);flex:none;
         box-shadow:0 0 0 0 rgba(139,156,255,.55);animation:pulse 2.4s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(139,156,255,.5)}
                 70%{box-shadow:0 0 0 7px rgba(139,156,255,0)}
                 100%{box-shadow:0 0 0 0 rgba(139,156,255,0)}}
@media (prefers-reduced-motion:reduce){.livedot{animation:none}}
.expiry{font:500 10px/1.2 var(--mono);letter-spacing:.14em;text-transform:uppercase;
        color:var(--ink-faint)}
.fresh{margin-left:auto;font:500 11px/1.2 var(--mono);letter-spacing:.02em;
       text-transform:uppercase;font-variant-numeric:tabular-nums;color:var(--ink-dim);
       white-space:nowrap}
.fresh.warn{color:var(--lantern)}
.fresh.bad{color:var(--lantern);border:1px solid var(--lantern);border-radius:3px;padding:1px 6px}

.mast-price{display:flex;align-items:baseline;gap:10px;min-width:0}
.px{font:600 40px/1.0 var(--mono);letter-spacing:-.02em;font-variant-numeric:tabular-nums;
    color:var(--ink);flex:none}
.px.withdrawn{color:var(--ink-dim)}
.chg{font:600 11px/1.2 var(--mono);letter-spacing:.02em;font-variant-numeric:tabular-nums;
     border-radius:999px;padding:3px 9px;white-space:nowrap;flex:none}
.chg.up{color:var(--jade);background:var(--jade-wash)}
.chg.dn{color:var(--coral);background:var(--coral-wash)}
.chg.flat{color:var(--ink-dim);background:none}
.lastscan{font:500 11px/1.2 var(--mono);letter-spacing:.02em;text-transform:uppercase;
          font-variant-numeric:tabular-nums;color:var(--ink-faint);white-space:nowrap;
          overflow:hidden;text-overflow:ellipsis}

/* ============ B. REGIME ============ */
.regime{height:var(--r-regime);flex:none;border-top:1px solid var(--hairline);
        display:flex;align-items:center;gap:10px;padding-top:6px}
.reg-left{min-width:0;flex:1 1 auto}
.reg-word{font:600 18px/1.15 var(--sans);letter-spacing:-.01em;color:var(--ink);
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.reg-word:empty{display:none}
.reg-gloss{font:400 13px/1.4 var(--sans);color:var(--ink-dim);
           white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.reg-ruler{flex:none;font:500 11px/1.2 var(--mono);letter-spacing:.02em;
           text-transform:uppercase;font-variant-numeric:tabular-nums;color:var(--ink-dim)}

/* ============ C. LADDER ============ */
.ladder{height:var(--ladder-h);flex:none;border-top:1px solid var(--hairline)}
#svg{display:block}

/* SVG mark classes — the SVG inherits this stylesheet */
.p-band   {fill:var(--ink);fill-opacity:.045}
.p-path   {fill:none;stroke:var(--ink-dim);stroke-width:1.6;stroke-opacity:.85;
           stroke-linejoin:round;stroke-linecap:round}
.p-reach  {fill:none;stroke:var(--ink);stroke-opacity:.35;stroke-width:1.2;stroke-dasharray:2 4}
.p-break  {stroke:var(--hairline-2);stroke-width:1}
.p-vwap   {stroke:var(--ink-dim);stroke-opacity:.7;stroke-width:1;stroke-dasharray:1 5}
.p-wall   {stroke-linecap:butt}
.p-wall.call{stroke:var(--jade)} .p-wall.put{stroke:var(--coral)}
.p-mag    {stroke:var(--l-grav);stroke-width:2.2}
.p-magrun {stroke:var(--l-grav);stroke-width:1.4}
.p-magglow{stroke:var(--l-grav);stroke-width:7;stroke-opacity:.10}
.p-prule  {stroke:var(--iris);stroke-opacity:.28;stroke-width:1}
.p-dot    {fill:var(--iris);stroke:var(--ground);stroke-width:1.2}
.p-halo   {fill:none;stroke:var(--iris);stroke-opacity:.28;stroke-width:1.2}
.p-brk    {fill:none;stroke:var(--ink-dim);stroke-opacity:.55;stroke-width:1.5;
           stroke-linecap:butt}
.p-chip   {fill:var(--iris)}
.p-chiptx {fill:var(--ground);font:500 13px/1 var(--mono);font-variant-numeric:tabular-nums;
           text-anchor:end}
.p-tie    {fill:none;stroke:var(--hairline-2);stroke-width:.8}
.p-bar    {fill-opacity:.85}
.p-bar.call{fill:var(--jade)} .p-bar.put{fill:var(--coral)}
.p-clip   {fill:var(--ink)}
.p-diamond{fill:var(--l-grav)}
.p-tag    {font:500 13px/1 var(--mono);font-variant-numeric:tabular-nums;text-anchor:end;
           fill-opacity:.78}
.p-tag.lead{fill-opacity:1;font-weight:600}
.p-tag.call{fill:var(--jade)} .p-tag.put{fill:var(--coral)}
.p-tag.mag {fill:var(--l-grav)} .p-tag.vwap{fill:var(--ink-dim)}
.p-lane    {font:500 10px/1 var(--mono);letter-spacing:.12em;fill:var(--ink-dim);
            text-anchor:end}
.p-word    {font:500 10px/1 var(--mono);letter-spacing:.14em;fill:var(--ink-faint);
            paint-order:stroke;stroke:var(--ground);stroke-width:3;stroke-linejoin:round}
.p-word.dim{fill:var(--ink-dim)}
.p-edge    {font:500 11px/1 var(--mono);letter-spacing:.02em;
            font-variant-numeric:tabular-nums;text-anchor:end;fill:var(--ink-faint)}
.p-axis    {font:500 10px/1 var(--mono);letter-spacing:.14em;fill:var(--ink-faint)}

/* ============ D. GATE ============ */
.gate{height:var(--r-gate);flex:none;border-top:1px solid var(--hairline);
      display:flex;flex-direction:column;gap:4px;padding:10px 0 10px}
.g-lab{display:flex;align-items:baseline;gap:8px;height:14px}
.g-dir{font:500 10px/1.2 var(--mono);letter-spacing:.14em;text-transform:uppercase;
       color:var(--ink-dim);white-space:nowrap}
.g-meas{margin-left:auto;font:500 11px/1.2 var(--mono);letter-spacing:.02em;
        text-transform:uppercase;font-variant-numeric:tabular-nums;color:var(--ink-dim);
        white-space:nowrap}
.g-head{display:flex;align-items:baseline;gap:10px;height:40px;min-width:0}
.g-k{flex:none;font:600 30px/1.0 var(--mono);letter-spacing:-.01em;
     font-variant-numeric:tabular-nums}
.g-k.call{color:var(--jade)} .g-k.put{color:var(--coral)}
.g-mech{flex:1 1 auto;min-width:0;font:400 13px/1.4 var(--sans);color:var(--ink-dim);
        display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.g-load{display:grid;grid-template-columns:1fr auto;column-gap:10px;row-gap:4px;
        align-items:center}
.g-row{display:contents}
.g-row[hidden]{display:none}
.g-bar{position:relative;height:6px;border-radius:2px;background:var(--surface-2);
       overflow:hidden}
.g-bar>i{position:absolute;inset:0 auto 0 0;border-radius:2px;background:var(--ink);
         opacity:.80;width:0}
.g-bar.behind>i{opacity:.42}
.g-bar::after{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;
              background:var(--ground)}
.g-val{font:500 11px/1.2 var(--mono);font-variant-numeric:tabular-nums;color:var(--ink-dim);
       white-space:nowrap}
.g-val b{font-weight:600;color:var(--ink)}
.g-foot{font:400 13px/1.4 var(--sans);color:var(--ink-dim);
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gate.empty .g-head{height:auto}
.gate.empty .g-mech{-webkit-line-clamp:3}

/* ============ E. READ ============ */
.read{height:var(--r-read);flex:none;border-top:1px solid var(--hairline);
      display:flex;flex-direction:column;gap:4px;padding:8px 0 8px}
.rd-lab{display:flex;align-items:baseline;height:14px;
        font:500 10px/1.2 var(--mono);letter-spacing:.14em;text-transform:uppercase;
        color:var(--ink-faint)}
.rd-age{margin-left:auto;font:500 11px/1.2 var(--mono);letter-spacing:.02em;
        font-variant-numeric:tabular-nums;color:var(--ink-dim)}
.rd-age.old{color:var(--lantern)}
.rd-body{display:flex;gap:6px;min-width:0;flex:1 1 auto}
.rd-mark{flex:none;font-size:9px;line-height:1.9;color:var(--ink-dim)}
.rd-mark:empty{display:none}
.rd-line{flex:1 1 auto;min-width:0;font:italic 400 13px/1.45 var(--serif);color:var(--ink);
         display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}
.rd-line.aged{color:var(--ink-dim)}
.rd-line.expired{font:400 13px/1.4 var(--sans);font-style:normal;color:var(--ink-dim)}

/* ============ F. FOOT ============ */
.foot{height:var(--r-foot);flex:none;display:flex;align-items:flex-end;
      font:400 10px/1.35 var(--sans);color:var(--ink-faint);
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ============ FAILURE ============ */
.fail{margin:auto 0;padding:0 6px;text-align:left}
.fail-1{font:400 15px/1.5 var(--sans);color:var(--ink);margin-bottom:10px}
.fail-2{font:500 12px/1.5 var(--mono);color:var(--ink-dim)}
```

There are **no** `border-radius` values on this page except: 999px on the change chip, 3px on the bad freshness box, 2px on the gauge bars, and 2px on the SVG price chip. There are no shadows, no `--surface` cards except the gate's flat region (which has no border — its separator is the region hairline), and no emoji.

---

## 7. DATA, RUNTIME, AND DERIVED VALUES

### Sources
```
/api/sndk/payload?user=<user>                                    every 60s   → PAY
/api/spot?ticker=SNDK                                            every 5s    → LIVE (fail open)
/api/raw/file?root=state&path=sndk_reversion/<session>.jsonl&limit=400  60s   → DIARY rows
/api/raw/file?root=state&path=sndk_reads/<session>.jsonl&limit=40       60s   → READS rows
```
Both timers cleared on `visibilitychange:hidden`, restarted on show. `/api/spot` failing sets `LIVE = null` and prints no error.

### Repaint discipline
- **Payload tick (60s):** recompute the window, rebuild the whole SVG string, repaint every region.
- **Quote tick (5s):** rebuild the SVG string using the **cached window** — the geometry is byte-identical and exactly one thing moves, the price dot. Also repaint the masthead price/change and the gate measures. **Do not recompute the window** unless the re-anchor test in §9.2 fires.
- **Resize:** recompute `LADDER_H`, repaint from cache. No refetch.

### Functions reused from `glance.js`, unchanged
`gUsd`, `gMinutes`, `envWords`, `gammaIsLong`, `wallBehaviour`, `beyondWall`, `farSideNote`, `bothSidesClear`, `wallDistance`, `nearestWall`, `priorClose`, `mergeLevels`, `layoutLabels`, `tapePoints`, `livePoint`, `railWidth`.

### Functions changed
| function | change |
|---|---|
| `wallTier(gex)` | New floor for outdoor legibility (Apple HIG: no stroke under 2pt at a glance). `gex >= 10 → 2.8`, `gex >= 5 → 2.0`, `else → 1.6`, `gex null → 1.8` (a default, never a claim). |
| `chartGeometry` | **Replaced** by the window solver in §9.2. |
| `drawableLevels` | Drops the flip band entirely. Adds the VWAP level (§9.3 m). |
| `freshness` | **Replaced** by `bookAge()` below. |
| `pickPrice` | **Replaced** by `shownPrice()` / `dayChange()` below. |
| `modelRead` | Sourced strictly by `reading_ts`; tiers at 30 and 120 minutes (§10E). |
| `gSigma` | **Deleted.** No sigma is printed on this page. |

### New derived values

```js
// ---- ONE AGE, from row_ts against the wall clock. -------------------------
// data_sources.options_book.age_min is NEVER read: off-live, build_scene stamps it from the
// row's own timestamp, so it reads ~0 however old the scan is. Believing it is
// exactly the failure that let a dead Schwab login look healthy for 3.1 days.
function bookAge(pay){
  const t = Date.parse(pay && pay.row_ts);
  if(!isFinite(t)) return {min:null, unknown:true};
  return {min: Math.max(0,(Date.now()-t)/60000), unknown:false};
}

const S = (PAY.gates && PAY.gates.stale_book_min) != null ? PAY.gates.stale_book_min : 6;
const H = (PAY.gates && PAY.gates.heartbeat_min)  != null ? PAY.gates.heartbeat_min  : 45;
// Thresholds come from the payload's OWN gates, so the station's definition of
// a stale book travels with the payload and cannot drift out of sync.

// STALE  = age > S            → freshness chip lantern; axis countdown swaps to scan time
// WITHDRAWN = no live quote AND age > H

// ---- the price the whole screen is registered to -------------------------
function shownPrice(scene, live){
  const q = (live && isFinite(Number(live.spot))) ? Number(live.spot) : null;
  const v = q != null ? q : (scene.price||{}).now;
  return (v != null && isFinite(v)) ? {v, live:q!=null} : null;
}

// ---- the day's change -----------------------------------------------------
// Gated on the SCAN'S OWN DATE, not on as_of. The risk being guarded is a
// percentage measured against ANOTHER day's close; comparing clock.date to the
// browser's local date removes exactly that risk and lets a zero-minute-old
// scan show its own change. A mismatch withholds the figure, never misstates it.
function dayChange(scene, live, diaryLast){
  const localDate = new Date().toLocaleDateString('en-CA');   // YYYY-MM-DD
  if(((scene.clock||{}).date) !== localDate) return null;
  const p = scene.price || {};
  const q = (live && isFinite(Number(live.spot))) ? Number(live.spot) : null;
  if(q == null) return (p.vs_prior_close_pct != null && isFinite(p.vs_prior_close_pct))
                       ? p.vs_prior_close_pct : null;
  // prefer the diary row's EXACT prior_close (1596.08) over recovery from the
  // 1-dp percentage (1596.15). Both are fetched already; reading it costs nothing.
  let pc = (diaryLast && isFinite(Number(diaryLast.prior_close)))
             ? Number(diaryLast.prior_close) : priorClose(p);
  return (pc && isFinite(pc)) ? (q/pc - 1)*100 : null;
}

// ---- VWAP as a PRICE, never as a signed ratio ----------------------------
function vwapPrice(scene, diaryLast){
  if(diaryLast && isFinite(Number(diaryLast.vwap))) return Number(diaryLast.vwap);  // exact
  const p = scene.price||{}, sig = (scene.scale||{}).one_sigma_dollars;
  if(p.now==null||p.vwap_dist_sigma_from_live_spot==null||!isFinite(sig)) return null;
  return p.now + p.vwap_dist_sigma_from_live_spot * sig;     // (vwap - spot)/sigma, so ADD it
}
```

---

## 8. THE LADDER — COORDINATE SYSTEM AND LANES

**One `<svg>`, one string, one `innerHTML` write. Exactly one `<defs>` containing exactly one `<clipPath>`. No filters, no gradients, no other defs.**

```
svg width  = CW              (attribute)
svg height = SVGH = LADDER_H - 1     (attribute; the 1 is the region's border-top)
viewBox    = "0 0 CW SVGH"           → 1 SVG user unit = 1 CSS px, always
```

**Horizontal lanes. Every right-hand lane is FIXED; the plot absorbs the width difference.**

```
  PLOT_R  = CW - 86            PLOT_W = PLOT_R - 8 = CW - 94

  x   0 ..   8   BRACKET LANE   (8)   the clear-side bracket only
  x   8 .. PLOT_R  PLOT               path, rules, band, price rule, in-plot words
  x PLOT_R .. +5   BUG LANE     (5)   one filled triangle
  x       .. +1    gap          (1)
  x       .. +30   MARK LANE   (30)   weight bar, magnet diamond, or the word VWAP
  x       .. +4    gap          (4)
  x  CW-46 .. CW   TAG LANE    (46)   right-aligned at x = CW

  CW = 356 (384px phone) → PLOT 8..270 | bug 270..275 | mark 276..306 | tag 310..356
  CW = 292 (320px phone) → PLOT 8..206 | bug 206..211 | mark 212..242 | tag 246..292
  CW = 384 (412px phone) → PLOT 8..298 | bug 298..303 | mark 304..334 | tag 338..384
```

**Vertical.**
```
  nTop = number of refused levels ABOVE the window (0..2)
  nBot = number of refused levels BELOW the window (0..2)

  PAD_T = 12 + 13 * nTop
  PAD_B = 18 + 13 * nBot
  plotTop    = PAD_T
  plotBottom = SVGH - PAD_B
  plotH      = plotBottom - plotTop

  top edge-marker i    baseline y = 10 + 13*i
  bottom edge-marker i baseline y = plotBottom + 12 + 13*i
  axis feet            baseline y = SVGH - 5
```

**Clip.** `<defs><clipPath id="pc"><rect x="8" y="{plotTop}" width="{PLOT_W}" height="{plotH}"/></clipPath></defs>`. The regime-free plot content that can exceed the window — the day path, the reach, the range band — is drawn inside `<g clip-path="url(#pc)">`. Rules and marks derived from admitted levels are inside the window by construction and need no clip.

---

## 9. THE LADDER — MARKS, GEOMETRY, DRAW ORDER

### 9.1 Level assembly

```
CORE  (always admitted; each still subject to the exile radius)
  shownPrice.v
  scene.price.session_low, scene.price.session_high
  every point of tapePoints(DIARY)            ← keeps the path inside the window
     (all of them, matching glance.js. Narrowing this to min/max measurably
      moves WIN, so the code is right and this line was wrong.)
  scene.walls.call[0].strike, scene.walls.put[0].strike
  scene.magnet.top_strikes[0][0]
  vwapPrice()

OPTIONAL (tried one at a time, DESCENDING gex, each must pass the admission test)
  scene.walls.call_heaviest_wall_behind_the_ladder, scene.walls.put_heaviest_wall_behind_the_ladder,
  scene.walls.call[1], scene.walls.put[1]
```

### 9.2 The window solver — computed **once per payload**

```js
const FAR = 1.75;                                 // exile radius, in sigma
const far = (sig>0 && isFinite(sig)) ? sig*FAR : null;

// 1. EXILE. |strike - shownPrice| > far  →  never admitted. It becomes an EDGE
//    MARKER. It is NEVER silently dropped: on 2026-08-24 the heaviest wall on
//    the board fell outside the window and reached no pixel at all.
// 2. CORE window.
   let lo = min(core), hi = max(core);
// 3. DEGENERATE FLOOR. if (hi - lo) < 0.5*sig, expand symmetrically about
//    shownPrice until (hi - lo) === 0.5*sig.
// 4. ADMISSION TEST, per optional candidate, heaviest gex first:
//      tentative lo/hi including the candidate, then pad 6% each side.
//      sessionRange = session_high - session_low   (else the diary path's own range)
//      ADMIT only while  sessionRange / (paddedSpan) >= 0.50.
//      REFUSE  →  the candidate becomes an EDGE MARKER.
//    0.50 is a LEGIBILITY constant, not a claim about the book. Without it a
//    pinned day renders as a flat line: on the real 2026-08-11 board the
//    heaviest-behind sits at 1.746 sigma — just inside the exile radius — and
//    admitting it drops the day's whole 39-dollar range to 23% of the plot.
// 5. PAD the final set 6% each side.  k = plotH / (hi - lo)
   yFor = v => plotTop + (hi - v) * k;
```

**FROZEN.** Between payloads the window is not recomputed, so at every 5-second repaint the geometry is bit-identical and exactly one mark has moved. A glance is then a comparison against the last one rather than a fresh read.

**RE-ANCHOR.** On each quote tick, if `shownPrice.v < lo + 0.12*span` or `> hi - 0.12*span` AND price has travelled `>= 0.05*span` since the window was anchored, recompute and repaint. The travel gate is not optional: a fresh window seats price `6/112 = 5.36%` inside its own edge, already within the 12% band, so without it the board re-solves on EVERY quote — measured, 296 of 300 ticks moved a rule. The constant must stay BELOW 5.36%, which is what guarantees price re-anchors before it can leave the window it is anchored in. **No transition, no tween** — the board jumps. It is rare, and a tween would make every ordinary tick as salient as this one.

**The flip band is not on this screen at all.** It is entirely off-window on ~36% of scans; when in-window it is a zone whose character is already stated by the regime word; and it cost a three-branch painter, a margin chevron and a legend chip. `regime.flip` and `flip.no_flip_anywhere_on_board` reach no pixel. Do not re-add a chevron, a distance chip or a legend entry for it.

### 9.3 Draw order — back to front

Each row states: what it encodes · its JSON path · geometry · degradation.

| # | mark | path | geometry | absent → |
|---|---|---|---|---|
| a | **clip def** | — | `<defs><clipPath id="pc">` per §8 | — |
| b | **TODAY'S RANGE band** | `price.session_high`, `price.session_low` | `<rect class="p-band" x=8 y=yFor(high) width=PLOT_W height=yFor(low)-yFor(high)>`, inside the clip group. A wall inside this band has already been tested today; one outside it has not — carried by pure geometry, no words, no number. | either endpoint absent → no band, no label |
| c | **range label** | — | `TODAY'S RANGE`, class `p-word`, `x=11`, baseline `yFor(high)-4` | band under 24px tall, or baseline above `plotTop+9` → omitted |
| d | **day path** | `tapePoints(DIARY)` | `<polyline class="p-path">` inside the clip. x maps `t0`(first point) → `t1`(max of last point and the live quote's t) across `8..PLOT_R`. The live quote is **never spliced into this path**. | fewer than 2 points → no path; the ladder still draws every level, the price rule and the dot |
| e | **reach** | `livePoint(LIVE)` newer than the last diary point | gap ≤ 30 min → `<path class="p-reach">` from the last measured point to the dot. gap > 30 min → **no dash**; instead `<line class="p-break">` vertical at the last measured x, full plot height. A dash across six hours implies a continuity that does not exist. | no live quote → neither mark |
| f | **wall rules** | `walls.call[]`, `walls.put[]`, `walls.*_heaviest_wall_behind_the_ladder` | `<line class="p-wall call\|put" x1=8 x2=PLOT_R>`, `stroke-width = wallTier(gex)`. Nearest wall on its side: `stroke-opacity:1`. Every other: `.62`. | strike absent → nothing. Level refused by the window → mark p |
| g | **magnet glow (lead)** | `magnet.top_strikes[0]` | `<line class="p-magglow">` beneath the lead magnet's y (a second wide low-opacity stroke — **never an SVG filter**) | absent → nothing |
| h | **magnet rules** | `magnet.top_strikes[i]` | lead → `class="p-mag"`. Runners-up → `class="p-magrun"` with `stroke-opacity = max(0.28, share / topShare)` — **continuous, no threshold anywhere**. sr-3 deleted a hardcoded 5.0pp tie constant; any cutoff here re-imports it. A near-tie must *look* like a tie without anyone deciding where a tie begins. | absent → nothing |
| i | **merge** | `mergeLevels(levels, hi-lo)`, tol = span × 0.006 | A wall within tolerance of a magnet absorbs it: the wall keeps its own stroke and hue, the magnet's glow is laid beneath, and the merged level carries a magnet flag (→ diamond in the mark lane, mark n). Two levels on one strike is the ordinary case, not the edge case. | — |
| j | **VWAP** | `vwapPrice()` | `<line class="p-vwap" x1=8 x2=PLOT_R>`, dash `1 5` — the desktop's own VWAP grammar. **Rendered as a price at a position. The signed ratio is never printed, never shown.** | neither source → no line, no tag |
| k | **clear-side bracket** | `walls.call_side_has_no_wall === true` / `walls.put_side_has_no_wall === true`, AND no wall of the other pool now sits on that side of the price on screen | `<path class="p-brk">` at x=3: a vertical from `plotTop` to `priceY` (call) or `priceY` to `plotBottom` (put), with 6px ticks turned **inward** at both ends. A measured emptiness has an *extent*, and the bracket draws it. | flag absent or false → **no bracket** — which correctly reads "not measured", a different thing from "measured empty" |
| l | **bracket label** | — | `NO CALL WALL ABOVE` / `NO PUT WALL BELOW`, class `p-word dim`. Qualified because the flag is: `call_side_has_no_wall` means no CALL-SIGNED cluster above spot, and a wrongly-signed pile there is dropped from both pools while the flag still fires, `x=12`, baseline at the bracket's vertical midpoint. The `--ground` halo (`paint-order:stroke`) lets it survive the path crossing it. | bracket under 34px tall → bracket only, no label |
| m | **price rule** | `shownPrice()` | `<line class="p-prule" x1=8 x2=PLOT_R>` at `yFor(price)` — this is what lets the eye read above/below for every level in one saccade | WITHDRAWN, or price off-window → not drawn |
| n | **price dot** | same | `<circle class="p-halo" r=7>` then `<circle class="p-dot" r=3.6>` at (dotX, priceY), where `dotX = min(xFor(t_now), PLOT_R - 8)` | WITHDRAWN → not drawn. Price cannot be off-window — it is an unconditional member of `coreLevels` (200,000 randomised solver trials: zero) — so the `inWin` guards are cheap defence, not a branch |
| o | **the bug** | `nearestWall(walls, shownPrice.v)` | one filled triangle, apex touching the nearest wall's rule end: `M PLOT_R,y L PLOT_R+5,y-5 L PLOT_R+5,y+5 Z`, fill = that wall's hue. It says "this one — this is the level the card below is about", with no word, no legend. | no wall → no triangle. That wall exiled → no triangle, and its edge marker renders at weight 600 instead (`.p-edge.lead`), matched on kind AND side AND strike so an exiled magnet on the same strike cannot steal it |
| p | **weight bar** | the level's own `.gex` | `<rect class="p-bar call\|put" x=MARK_L y=rowY-1.5 height=3 width=clamp(2, gex/20*20, 20)>`. **Fixed full scale: 20% of book gamma = 20px, always** — never the scan's own maximum, so two days are comparable. `gex > 20` → a 2×7px `p-clip` notch at `MARK_L+20`. | **`gex` null → NO BAR AND NO TRACK.** An empty track reads as zero; absence is not zero |
| q | **magnet diamond** | the level's magnet flag | a 5px `p-diamond` rotated square centred at `MARK_L+27`. A different glyph from the bar because **magnet share is a fraction of `mass_by_strike` while wall gex is a fraction of `net_by_strike`** — two denominators must never share one gauge | not a magnet → nothing |
| r | **VWAP lane word** | — | `VWAP`, class `p-lane`, right-aligned at `MARK_R`, at the VWAP tag's solved row | — |
| s | **tie lines** | — | `<path class="p-tie">` from `(PLOT_R+1, trueY)` to `(MARK_L-1, rowY)` whenever `|rowY - trueY| > 2` | — |
| t | **tags** | each level's strike / the VWAP price | `<text class="p-tag …" x=CW>`, `gUsd(v,0)` with the `$` stripped → `1,450`. Hue = the level's own hue. The wall the gate card names gets `class="p-tag lead"` (weight 600, full opacity); every other tag sits at `.78`. | — |
| u | **price chip** | `shownPrice()` | `<rect class="p-chip" x=CW-46 y=rowY-9 width=46 height=18 rx=2>` + `<text class="p-chiptx" x=CW-5 y=rowY+4.5>` with the price in **whole dollars** (`1,489`). The chip states a *position*; the exact price with cents lives once, at 40px, in the masthead. | WITHDRAWN → no chip |
| v | **edge markers** | BOOK levels (wall or magnet) exiled or refused | `▲ 1,550` (top) / `▼ 1,150 HEAVIEST` (bottom), class `p-edge`, right-aligned at `CW`, at the fixed rows in §8. Max 2 per side — which is exactly the size of the optional set, so **nothing can ever be silently dropped**. | none refused → no markers, `PAD_T`/`PAD_B` stay at 12/18 |
| w | **axis feet** | `clock`, `payload.row_ts` | class `p-axis`, baseline `SVGH-5`. Left at x=8: the first plotted time, `09:31`. Right, `text-anchor:end` at `PLOT_R`: **not STALE** → `gMinutes(minutes_to_close).toUpperCase() + " LEFT"`, or `CLOSED` when ≤ 0. **STALE** → `"SCAN " + row_ts` local `HH:MM`. A countdown computed at 12:12 is a lie when you read the screen at 16:00, and it is the one label on the plot that ages silently. | no path → no left foot; the right foot still draws |
| x | **no-price state** | no `shownPrice` at all | the plot draws nothing but `NO PRICE MEASURED`, class `p-word`, `x=11`, `y=plotTop+22`. **Levels are not drawn either**: with no price there is no side, and side is what the geometry encodes. | — |

### 9.4 Tag row solving

One call, all rows including the price chip:

```js
rows = layoutLabels(desiredYs, 20, plotTop + 10, plotBottom - 10);
```
Members, sorted by true y: every drawable wall tag, the lead magnet's tag when it did not merge, the VWAP tag, and the price chip. Edge markers are **not** members — they sit outside the solver's bounds at their fixed rows.

**Cap 7 rows.** Never dropped: the price chip, the nearest wall on each side, `*_heaviest_wall_behind_the_ladder`, the lead magnet. Drop order: VWAP first, then `walls.*[1]`.

---

## 10. REGIONS — EVERY ELEMENT, ITS PATH, ITS TEXT, ITS DEGRADATION

### A. MASTHEAD

| id | path | form | absent → |
|---|---|---|---|
| `ticker` | `scene.instrument` | T7, `.18em`, `--ink-dim`, uppercased | **nothing.** Never a hardcoded `"SNDK"` |
| `livedot` | derived: `/api/spot` returned a finite `.spot` | 5px `--iris` disc, 2.4s pulse; static under `prefers-reduced-motion` | hidden. **Never a grey dot** — a grey dot reads as "live, calm" |
| `expiry` | `clock.front_expiry.days_to_expiry`, `.date` | T7, `--ink-faint`. `dte === 0` → `EXPIRES TODAY`. `.date` present → `EXP ` + 3-letter weekday, uppercase → `EXP FRI`. `.date` absent, `dte` present → `EXP IN 4 DAYS` (`EXP IN 1 DAY` at 1) | nothing |
| `fresh` | `bookAge(PAY)` + `PAY.as_of` | T6, right-aligned. `as_of === 'live'` → `BOOK ` + age; else → `LAST SCAN ` + age, both uppercased. Class: `age <= S` → none (`--ink-dim`); `S < age <= H` → `.warn`; `age > H` → `.bad` (a 1px `--lantern` box). `row_ts` unparseable → `LAST SCAN · AGE UNKNOWN`, `.bad` | — |
| `px` | `shownPrice()` | T1. `gUsd(v)` with `$` stripped → `1,489.21`. **WITHDRAWN** → `—`, class `.withdrawn` | no quote and no `price.live_spot` → `—`, and the ladder enters its no-price state |
| `chg` | `dayChange()` | T6 filled chip. `▲ 1.18%` / `▼ 6.70%` / `· 0.00%` (`Math.abs(pct).toFixed(2)`). Classes `.up` / `.dn` / `.flat` | **hidden entirely.** No dash, no zero |
| `lastscan` | shown only when WITHDRAWN | T6, `--ink-faint`, replaces the chip: `LAST SCAN 1,489.21 · 3H 46M AGO` | hidden |

> **Withdrawal, not dimming.** Past the payload's own `heartbeat_min` with no live quote, the price becomes an em-dash and the price rule, dot, chip and reach leave the plot. **The levels stay.** A 45-minute-old *price* is worthless; a 45-minute-old *wall* is not — the measured `unchanged_for_min` on real boards reads 86, 160+, 233 and 247 minutes. Dimming a stale price outdoors is the same as hiding it, so it is stated instead.

### B. REGIME

| id | path | text |
|---|---|---|
| `regWord` | `regime.regime_label` | T3, `--ink`, first letter capitalised: `Trending` |
| `regGloss` | `gammaIsLong(scene.regime)` | T4, `--ink-dim`. `true` → `walls hold`; `false` → `walls give way`; `null` → `gamma sign not measured`. **The two live strings are copied byte-for-byte from `envWords()`** so the phone and the desktop can never describe one board in two voices |
| `ruler` | `scale.one_sigma_dollars` | T6, right-aligned, `--ink-dim`: `TYPICAL MOVE $67` (`Math.round`). This states the ruler once, in English. It is why no Greek letter appears anywhere else |

Degradation: word absent → the gloss promotes to the T3 line and `regGloss` is empty. Both absent → `regWord` empty, `regGloss` reads `Regime not measured`. `one_sigma_dollars` absent → `ruler` empty. **The region keeps its height in every case.**

### C. LADDER — §8 and §9.

### D. GATE — the amplification of the nearest level. **This region never hides.**

Fed by `nearestWall(scene.walls, shownPrice.v)`. When WITHDRAWN, `scene.price.live_spot` is the geometric reference and `gDir` gains the clause `· AT THE 12:12 SCAN` (the scan's local `HH:MM`).

| id | path | text |
|---|---|---|
| `gDir` | the wall's strike against the price on screen | `▼ NEXT BELOW` / `▲ NEXT ABOVE`. Not `w.side`: walls_ladder buckets by the SCAN spot, so a live tick through the wall makes that label contradict the plot directly above it |
| `gMeas` | `wallDistance(strike, shownPrice.v)` + `unchanged_for_min` | `$39 · HELD 1H 26M`. `unchanged_for_at_least_min` → a `+` suffix (`HELD 2H 40M+`) — the datum is censored and the `+` is load-bearing. Each clause drops independently with its own datum, and its separator goes with it. **Distance is measured against the price ON SCREEN, never `walls[].sigma`**, which was measured against a spot the reader can no longer see. **No sigma term.** |
| `gStrike` | the wall's `.strike` | T2, `gUsd(strike,0)` minus `$` → `1,450`. Class `.call` / `.put` — the same hue as its rule in the ladder and as the bug triangle pointing at it |
| `gMech` | `wallBehaviour(scene.regime, strike, shownPrice.v).english` | T4, 2-line clamp. **The four `snkArrows` strings, verbatim, never reworded** (§11) |
| `gRowA` | the wall's `.gex` | bar width `clamp(2%, gex/20*100%, 100%)`, `--ink` at .80. Value: `<b>6.9%</b> of book gamma` |
| `gRowB` | `beyondWall(walls, side)` | bar `--ink` at .42. Value: `<b>14.8%</b> at 1,400 · heaviest` (or `· next out`) |
| `gFoot` | see below | T4, `--ink-dim` |

Both bars are on **one grid**, so the two tracks are provably the same scale (same denominator: the full `net_by_strike` surface), with a 1px `--ground` notch at 50% of the track = **10% of book**, a fixed reference. **Full scale is fixed at 20%** (p90 of 18,510 recorded wall observations), never the scan's own maximum. `gex > 20` → the fill clips at 100%.

Degradations, in priority order:
1. `bothSidesClear(walls)` → `gate` gains class `.empty`. `gDir` = `NO WALL EITHER WAY`, `gMeas` empty, `gStrike` empty, `gMech` = `The board was read and holds nothing above or below price.`, both bars hidden, `gFoot` empty.
2. `gammaIsLong()` null → **`gMech` is empty.** No sentence, no dealer claim. The strike, the direction, the distance and the gauge all still stand. This is the single most important degradation on the page: `gamma_sign` is the literal string `"unknown"` on 7.1% of rows, and a confident direction rendered in the largest words on screen from a field whose value is the word "unknown" is the exact bug `gammaIsLong()` was written to stop.
3. `w.gex` null → `gRowA` hidden, `gFoot` = `Weight not measured.`
4. `beyondWall()` with no `gex` → `gRowB` hidden, `gFoot` = `Next wall at $1,400.` or `Nothing else on this side of the board.`
5. The clear-side bracket's LABEL could not draw (a bracket under 34px, a bracket suppressed by W5's crossed-price test, or a ladder early return) → `gFoot` = `farSideNote()` — `No call wall above price.` / `No put wall below price.` Otherwise the measured-empty side appears **exactly once**, as the bracket in the plot, which also shows its extent.
6. `nearestWall()` null and not both-clear → `gDir` = `NO WALL MEASURED`, everything else empty. **The region still occupies its height.**

### E. READ — a different epistemic class: an opinion, not a measurement.

Source: the READS row with the newest **`reading_ts`**. **Never `ts`** — the store re-emits the same reading every couple of minutes with a fresh `ts` while `reading_ts` stays put. On the reference file the last row carries `ts 15:58` and `reading_ts 11:47`: a 251-minute reading wearing a 0-minute timestamp.

| age | `rdMark` | `rdLine` | `rdAge` |
|---|---|---|---|
| ≤ 30 min | `▲` / `▼` from `reading.vector` (`up`/`down` only); anything else → **empty, never a `·`** | serif italic, `--ink`, 3-line clamp | `--ink-dim` |
| 31–120 min | empty (an aged opinion loses its arrow) | serif italic, `--ink-dim`, prefixed with the reading's own clock time: `11:47 · ` | `--lantern` |
| > 120 min | empty | class `.expired`, sans, the sentence **is not shown**: `LAST READING 4H 11M AGO` | empty |
| no rows / no `.line` | empty | class `.expired`: `NO READING TODAY` | empty |

`rdAge` = `gMinutes(ageMin)` uppercased; under 1 minute → `JUST NOW`.

**120 minutes is 4× the reading's own horizon** — every claim it makes is about a 30-minute window — so a reading four horizons old has expired, not merely aged. Measured p95 is 214 minutes and the record is 341, so this fires often and is meant to. **A reading never renders without its age.**

> `rdLine.textContent = String(reading.line)`. It is model output. It never touches `innerHTML` and it never enters the SVG string.

### F. FOOT
`gammaIsLong()` is a boolean → `Hedge direction assumed from the board's gamma sign.`
`gammaIsLong()` is null → `Gamma sign not measured — no dealer behaviour claimed.`

This says out loud the one honest difference between this screen and the desktop: `snkArrows` reads the gamma sign **per strike** off `gex_views.net_by_strike`; the scene does not ship that surface, so this reads the **board's** sign.

### Full-page failure states (`body.failed`, all regions hidden)
- HTTP 403 → `fail1`: `The station refused the request — its permitted-user check said no.` `fail2`: `Open /m?user=<your name> with the name the front door knows you by.`
- no payload / `payload.error` / no `scene` → `fail1`: `No SNDK scene yet.` `fail2`: `payload.error` verbatim via `textContent`, or `the station returned nothing`.

---

## 11. VERBATIM TEXT — the complete inventory

Nothing else appears on this screen. If a string is not on this list, it is not on the page.

**Masthead:** `SNDK` · `EXPIRES TODAY` · `EXP FRI` · `EXP IN 4 DAYS` · `EXP IN 1 DAY` · `BOOK 2M` · `LAST SCAN 3H 46M` · `LAST SCAN · AGE UNKNOWN` · `1,489.21` · `—` · `LAST SCAN 1,489.21 · 3H 46M AGO` · `▲ 1.18%` · `▼ 6.70%` · `· 0.00%`

**Regime:** `Trending` · `walls hold` · `walls give way` · `gamma sign not measured` · `Regime not measured` · `TYPICAL MOVE $67`

**Ladder:** `TODAY'S RANGE` · `VWAP` · `NO CALL WALL ABOVE` · `NO PUT WALL BELOW` · `NO PRICE MEASURED` · `LADDER TOO SHORT` · `09:31` · `3H 47M LEFT` · `CLOSED` · `SCAN 12:12` · tag prices `1,500` `1,489` `1,452` `1,450` `1,420` `1,400` · edge markers `▲ 1,550` `▼ 1,150 HEAVIEST`

**Gate:** `▼ NEXT BELOW` · `▲ NEXT ABOVE` · `· AT THE 12:12 SCAN` · `$39 · HELD 1H 26M` · `HELD 2H 40M+` · `1,450` ·
`Dealers buy the dips here — it holds price up.`
`Dealers sell the rallies here — it caps the move.`
`Dealers must buy a break up — moves speed up.`
`Dealers must sell a break down — moves speed up.`
· `6.9% of book gamma` · `14.8% at 1,400 · heaviest` · `14.8% at 1,400 · next out` · `Weight not measured.` · `Next wall at $1,400.` · `Nothing else on this side of the board.` · `No call wall above price.` · `No put wall below price.` · `NO WALL EITHER WAY` · `The board was read and holds nothing above or below price.` · `NO WALL MEASURED`

**Read:** `MODEL READ` · `26M` · `4H 11M` · `JUST NOW` · `11:47 · ` · `LAST READING 4H 11M AGO` · `NO READING TODAY` · `▲` · `▼` · `reading.line` verbatim

**Foot:** `Hedge direction assumed from the board's gamma sign.` · `Gamma sign not measured — no dealer behaviour claimed.`

**Failure:** the four strings in §10F.

The four mechanism sentences use an **em dash U+2014 with spaces**, exactly as in `snkArrows`. Do not reword, re-punctuate, or "improve" them.

---

## 12. WORKED EXAMPLE — assert against these numbers

`payload-live.json` (session 2026-08-24, `row_ts` 12:12:10, `as_of` "last scan"), `diary-live.json` (79 rows, 09:31:44 → 12:12:10), `reads-live.json`, rendered at **12:14:10 ET** on a 384×780 viewport with no live quote.

```
CW 356 · LADDER_H 376 · SVGH 375 · PLOT_R 270 · PLOT_W 262
no refused levels → PAD_T 12, PAD_B 18, plotTop 12, plotBottom 357, plotH 345

WINDOW
  core   = {1489.21, 1422.21, 1492.23, 1450, 1500, 1451.4096}  → [1422.21, 1500.00]
  padded 6% → [1417.54, 1504.67], session range 70.02 / 87.13 = 80.4%
  try 1400 (gex 14.8): |1400-1489.21| = 89.21 <= 1.75*67.18 = 117.57  → not exiled
         new [1394.00, 1506.00], span 112.00, range 70.02/112 = 62.5% >= 50%  → ADMIT
  try 1420 (gex 4.9): already inside → admitted free
  FINAL lo 1394.00  hi 1506.00  span 112.00   k = 345/112 = 3.080357 px per dollar

Y TABLE   yFor(v) = 12 + (1506 - v) * 3.080357
  1506.00  →  12.00 (plot ceiling)      1451.41 → 180.16  vwap
  1500.00  →  30.48  magnet lead        1450.00 → 184.50  walls.put[0]
  1492.23  →  54.41  band top           1422.21 → 270.10  band bottom
  1489.21  →  63.72  price              1420.00 → 276.91  walls.put[1]
                                        1400.00 → 338.52  put_heaviest_wall_behind_the_ladder
                                        1394.00 → 357.00  (plot floor)

  range band = 215.7px tall = 62.5% of the plot.
  merge tolerance = 112 * 0.006 = $0.67 → VWAP (1451.41) and the 1450 wall do
  NOT merge; they are two rules 4.3px apart. This is the ordinary crowded case.

TAG SOLVER  layoutLabels([30.48, 63.72, 180.16, 184.50, 276.91, 338.52], 20, 22, 347)
  →         [30.48, 63.72, 180.16, 200.16, 276.91, 338.52]
  The 1,450 tag is displaced +15.7px and gets a tie line. The VWAP tag keeps its
  true row. This is the EXPECTED output — do not "fix" it; the tie line and the
  MARK-LANE word "VWAP" make both unambiguous.

MERGES     magnet #3 (1450, 9.46) merges into walls.put[0]  → diamond on 1,450
           magnet #2 (1400, 9.56) merges into put_heaviest_wall_behind_the_ladder → diamond on 1,400
           magnet #1 (1500, 15.47) stands alone → p-mag rule + glow + its own tag

WEIGHT     1450: 6.9/20*20  =  6.9px bar     1420: 4.9px     1400: 14.8px
           No clip notch anywhere (nothing exceeds 20%).

BRACKET    walls.call_side_has_no_wall === true → bracket from y 12 to y 63.7 at x=3,
           label "NO WALL ABOVE" at x=12, baseline ~38. Bracket 51.7px tall > 34 ✓

MASTHEAD   SNDK · no live dot · EXP FRI · "LAST SCAN 2M" (2 <= S=6, plain --ink-dim)
           1,489.21 · clock.date 2026-08-24 === today → chip "▼ 6.70%" (--coral)
REGIME     "Trending"  /  "walls give way"     right: "TYPICAL MOVE $67"
GATE       ▼ NEXT BELOW        $39 · HELD 1H 26M
           1,450 (coral)  "Dealers must sell a break down — moves speed up."
           bar A 34.5%  "6.9% of book gamma"
           bar B 74.0%  "14.8% at 1,400 · heaviest"
           foot empty (the bracket carried the clear side)
READ       reading_ts 11:47:39 → 27M ≤ 30 → ▲, serif italic in --ink, age --ink-dim
FOOT       "Hedge direction assumed from the board's gamma sign."
```

**Second state, same file, read at 15:58 ET** (the state the payload was actually captured in): scan age 226m > H=45 → `fresh` reads `LAST SCAN 3H 46M` in a `--lantern` box; `px` = `—` in `--ink-dim`; `chg` hidden; `lastscan` = `LAST SCAN 1,489.21 · 3H 46M AGO`; the price rule, dot, chip and reach leave the plot; **every level and the range band draw exactly as above, at exactly the same y values**; the axis right foot reads `SCAN 12:12` instead of `3H 47M LEFT`; `gDir` = `▼ NEXT BELOW · AT THE 12:12 SCAN`; the read region shows `LAST READING 4H 11M AGO` and no sentence.

**Third state, the pinned-day check (2026-08-11, 14:40 ET):** spot 1268.09, σ 67.60, session 1246.17–1285.12, `put_heaviest_wall_behind_the_ladder` 1150 at **1.746σ — just inside the 1.75 exile radius.** Core window padded = [1236.4, 1303.6], range 58%. Admitting 1150 → span 168, range 23.2% < 50% → **REFUSED**. So do `walls.put[1]` 1180 (29.0%) and `walls.call[1]` 1350 (31.6%), so three levels are refused: bottom markers `▼ 1,180` then `▼ 1,150 HEAVIEST` (`PAD_B` 44) and top marker `▲ 1,350` (`PAD_T` 25). Without the 50% test this day renders as a flat line inside a 224-dollar window, which is the single failure mode a pinned board has.

---

## 13. BUILD ORDER

Implement in this sequence. Each step is checkable before the next begins.

1. **Shell.** Tokens, DOM, CSS, all six regions at their fixed heights with hardcoded placeholder text; `LADDER_H` computed and written; the SVG given an explicit width/height. **Check: no vertical scroll at 320×568, 384×780 and 412×915, and the ladder is 208 / 360 / 495px.**
2. **Fetch + masthead + regime + foot** against `payload-live.json`. **Check: `LAST SCAN 2M`, `1,489.21`, `▼ 6.70%`, `Trending / walls give way`, `TYPICAL MOVE $67`.** Then force the clock to 15:58 and check the withdrawal path.
3. **Ladder skeleton.** The window solver, `yFor`, the tag solver, the axis feet — drawing **only** wall rules and tags. **Check every number in §12's Y TABLE to one decimal, and the solver output `[30.48, 63.72, 180.16, 200.16, 276.91, 338.52]`.** Then run the 08-11 pinned board and confirm 1150 is refused and named.
4. **Price rule, dot, chip, day path, reach, clip group.** Check the dot sits at y 63.72 and the path's right end meets it.
5. **The rest of the plot:** magnet + glow + merge, VWAP, range band, weight bars, magnet diamonds, the bug triangle, the clear-side bracket, edge markers.
6. **Gate card,** including all six degradations.
7. **Read region,** including all four age tiers.
8. **Degradation sweep** against `t-bare.json`, `t-clear.json`, `t-unknown.json`, `t-nopath.json`. `t-unknown.json` must produce: no mechanism sentence, `gamma sign not measured`, and the null-sign footer.
9. **A real browser at 320px and 412px, in daylight if possible.** The harness stubs `getBoundingClientRect` with a supplied chart height, so a clean harness run is not proof of anything about layout. Run it last, for overlap and overflow only.

---

## 14. MUST NOT REGRESS

Every item below is measured. Breaking one is a defect, not a taste call.

1. **`gamma_sign === "unknown"`** (7.1% of rows) → `gammaIsLong()` returns null → **no mechanism sentence and no dealer claim anywhere on the page.** The regime gloss says `gamma sign not measured` out loud; it does not fall silent.
2. **Gamma sign colours nothing.** No regime wash, no regime-tinted rules, no hue-coded card accent. Its total blast radius is one gloss line, one sentence and one footer line.
3. **VWAP is rendered as a price at a position.** `vwap_dist_sigma_from_live_spot` is `(vwap − spot)/σ`, so a *negative* value means price is *above* its average, and 13 of 15 reviewers read it backwards. The signed ratio is never printed. Prefer the diary row's exact `vwap` (1451.4096) over the derived value.
4. **Weight rides a fixed 0–20%-of-book scale**, never the scan's own maximum. 20.4% is p90 of 18,510 recorded observations; roughly one wall in ten clips, and the clip is marked.
5. **`gex` null → no bar and no track.** An empty track reads as zero. Absence is not zero.
6. **A refused BOOK level is always named.** Exiled or refused by the 50% test, a wall or magnet becomes an edge marker with its strike. Tape points and session extremes are not eligible: they carry no strike, their off-window portion is already handled by the clip, and admitting them takes the slots. Ranked by kind then weight, split on the price, laid out descending on both stacks. Max 2 per side is exactly the size of the optional set, so nothing can be silently dropped — that is the 2026-08-24 bug where the heaviest wall on the board reached no pixel at all.
7. **The magnet's share never touches the gex scale.** `top_strikes` shares are a fraction of `mass_by_strike`; wall `gex` is a fraction of `net_by_strike`. Two denominators must never share one gauge. The magnet gets a diamond; it never gets a bar.
8. **No magnet-tie threshold, ever.** Runner-up opacity is `max(0.28, share/topShare)`, continuous. sr-3 deleted a hardcoded 5.0pp constant for shipping a near-constant as a finding.
9. **`data_sources.options_book.age_min` is never read.** Off-live, `build_scene` stamps it from the row's own timestamp, so it reads ~0 however old the scan is — the failure that let a dead Schwab login look healthy for 3.1 days. One age, from `row_ts` against the wall clock.
10. **Staleness thresholds come from `payload.gates`** (`stale_book_min`, `heartbeat_min`), never hardcoded.
11. **The countdown must not age silently.** `minutes_to_close` is computed at scan time; when STALE, the axis right foot shows the scan's clock time instead.
12. **The model reading is sourced by `reading_ts`, never `ts`**, and is never shown without its age. Median 12 min, p95 214, max 341.
13. **`reading.line` is written with `textContent` only.** It is model output. It never touches `innerHTML` and never enters the SVG string.
14. **The four mechanism sentences are byte-identical to `snkArrows`.** The regime gloss strings are byte-identical to `envWords()`. The desktop and the phone must never describe one board in two voices, so **no English on this page is authored.**
15. **Distance is measured against the price on screen**, never `walls[].sigma`, which was taken against a spot the reader can no longer see.
16. **`*_side_has_no_wall` absent ≠ false.** Only `=== true` draws the bracket; absence correctly reads "not measured".
17. **Banned outright, reaching no pixel:** `reading.magnitude_sigma` (over-predicts the ordinary day, blind to the tail), `dealer_positioning`, `breadth`, `momentum`, `regime.charm` — and `charm.drifts_toward_strike` in particular, a target price inside a key on a block whose own docstring refuses to emit a direction word.
18. **Never parse prose.** `frozen_do_not_cite`, `magnet.top_strike_lead_vs_own_history` and `breadth.vs_own_history` are never regexed, never re-worded, and never printed.
19. **The change % is gated on `scene.clock.session_date === the browser's local date`.** A mismatch withholds it; nothing is ever guessed.
20. **No emoji.** They are colour bitmaps: they carry no theme token, cannot be tinted to mean a side, do not dim with the page, and render differently on every OS.
21. **No legend.** Every mark either labels itself in the mark lane or is named in the card. A legend is a confession that the marks do not read.
22. **No Greek letter, anywhere.** All distances in dollars; the ruler is stated once as `TYPICAL MOVE $67`.
23. **The chart height is an explicit pixel value.** Never `flex:1`. A clean harness run is not proof — the harness *supplies* the chart height and cannot see a CSS collapse.
24. **The plot content is clipped.** The day path is in the window's candidate set *and* inside `clip-path:url(#pc)`.
25. **Nothing is tappable.** No hover, no `cursor:pointer`, no `title` attributes, no tooltips, no toggles, no tabs.
26. **Nothing that is not in the payload is ever drawn.** No ghosts of levels that no longer exist, no trend vectors, no extrapolated direction, no invented history.

---

## 15. AMENDMENTS AFTER REVIEW (2026-08-24)

An adversarial review of the shipped build raised 38 findings; 23 survived a
refutation pass and collapsed to 14 work items, all applied. The corrections
above are folded in rather than appended, so this document remains the single
build gate. The substantive changes to the DESIGN, as opposed to defects in the
build, were:

- **Edge markers admit book levels only** (§9.3 v, §14.6). A tape point has no
  strike, and ~70 exiled ones could take both slots while the wall the gate
  names in 30px type reached no pixel. That is the very bug the edge marker
  exists to prevent, reintroduced through the queue.
- **The re-anchor gained a travel gate** (§9.2). "Frozen between payloads" was
  not true: a fresh window seats price inside its own trigger band.
- **The gate's direction is derived, not read** (§10D). The payload's `side` is
  bucketed against the scan spot and goes stale the moment a live tick crosses
  the wall.
- **The clear-side bracket is qualified and conditional** (§9.3 k, §11). The
  flag means no correctly-SIGNED cluster on that side, which is a narrower claim
  than the words made, and it can be contradicted by a live tick.
- **The gate region was shorter than its own content** (§1). The only child with
  `overflow:hidden` absorbed the whole deficit, so a sanctioned sentence
  rendered as a 2px smear at 320px. This is the one finding verified in a real
  engine rather than by reading.

Two things this review could not close, recorded so nobody assumes otherwise:

1. **A third off-window book level per side is still dropped.** §14.6's absolute
   wording holds only while at most two sit off-window per side.
2. **No browser has rendered this.** Every check here is headless or by reading,
   and the harness supplies the ladder height rather than deriving it, so it is
   structurally blind to a layout collapse. §13 step 9 remains outstanding.
