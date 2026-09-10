/* page.js — fetch, lay out, paint. All reasoning lives in glance.js.
 *
 * REPAINT DISCIPLINE. The payload tick (60s) recomputes the window and rebuilds
 * everything. The quote tick (5s) rebuilds the SVG against the CACHED window,
 * so the geometry is bit-identical and exactly one mark has moved. A glance is
 * then a comparison against the last one rather than a fresh read.
 */

const USER = new URLSearchParams(location.search).get('user') || 'will';
const $ = id => document.getElementById(id);

let PAY = null, LIVE = null, DIARY = [], READS = [], BARS = [], WIN = null, LADDER_H = 196;
// whether the plot's bracket LABEL said a side was empty this repaint. The
// gate footer speaks only when it did not.
let CLEAR_SAID = {call:false, put:false};
let T_PAY = null, T_SPOT = null;

/* ---- layout ------------------------------------------------------------ */

function fitTabs(){
  // MEASURE the fixed tab bar; do not assert its height.
  //
  // --tab-h shipped at 64 against a bar that renders 74 — icon 22, two 4px
  // gaps, a 10px label, a 5px dot, 6px of item padding and 23px of bar padding
  // — so the body reserved ten pixels too few and the footer rendered five
  // pixels under the bar. Every term in that sum is a CSS value someone can
  // change without ever looking at the constant, which is precisely how the
  // old region budget kept going wrong.
  //
  // getBoundingClientRect includes the safe-area inset the bar pads itself
  // with, so the body must NOT add the inset again.
  const bar = document.querySelector('.tabs');
  if(!bar) return;
  const h = Math.ceil(bar.getBoundingClientRect().height);
  if(h > 0) document.documentElement.style.setProperty('--tab-h', h + 'px');
}

function sizeLadder(){
  // A CONSTANT since the light rebuild (2026-09-09), and the reason is the
  // whole shape of the page.
  //
  // The old glance was a fixed-height column with overflow:hidden, so the six
  // regions had to sum to less than the viewport and the ladder was the only
  // elastic one — it absorbed every spare pixel and every deficit. That budget
  // produced three separate documented failures: a region overrunning and
  // printing through the footer, a media block that lost every cascade, and a
  // JS/CSS disagreement about WHICH viewport height to measure (innerHeight is
  // the visual one, the @media block is the layout one; measured 706 against
  // 568 on the same screen).
  //
  // The page scrolls now, so there is no budget to balance and nothing for the
  // chart to absorb. It gets the height the chart itself needs and the reading
  // below it grows to whatever the model wrote. A paragraph that can be six
  // lines or thirteen could never have lived in that column.
  //
  // 196. SVGH is LADDER_H-1, and PAD_T/PAD_B are 12 and 18 plus 13 per edge
  // marker, so the plot is 165px with no edge markers, 152 with one, 139 with
  // two on one side. That is the range in which the wall rules stay separable
  // and the price path keeps its shape; below roughly 130 the label solver
  // starts displacing a label further than the level it names.
  LADDER_H = 196;
}

/* ---- fetch ------------------------------------------------------------- */

async function getJSON(url){
  try{
    const r = await fetch(url, {cache:'no-store'});
    const t = await r.text();
    try { return {status:r.status, body:JSON.parse(t), reached:true}; }
    catch(e){ return {status:r.status, body:null, reached:true}; }
  } catch(e){ return {status:0, body:null, reached:false}; }
}

function fail(one, two){
  document.body.classList.add('failed');
  clearLoading();
  $('fail').hidden = false;
  $('fail1').textContent = one;
  $('fail2').textContent = two || '';
}

async function loadPayload(){
  const r = await getJSON('/api/sndk/payload?user=' + encodeURIComponent(USER));
  if(r.status === 403)
    return fail('The station refused the request — its permitted-user check said no.',
                'Open /m?user=<your name> with the name the front door knows you by.');
  const pay = r.body;
  if(!pay || pay.error || !pay.scene){
    // A request that failed is not an empty station. visibilitychange fires this
    // on wake — exactly when the radio has just reassociated — and blanking six
    // regions while a good payload sits in memory is the worst possible answer.
    // The retained payload cannot look fresh: bookAge() runs off row_ts against
    // the wall clock, so it goes lantern past stale_book_min and withdraws past
    // heartbeat_min on its own.
    if(PAY && PAY.scene){ paintAll(); return; }
    return fail('No SNDK scene yet.', (pay && pay.error) || 'the station returned nothing');
  }

  document.body.classList.remove('failed');
  $('fail').hidden = true;
  $('fail1').textContent = ''; $('fail2').textContent = '';
  PAY = pay;

  if(pay.session){
    // 420, not 390: a session is 390 one-minute bars and the limit has to clear
    // it. The server implements limit as a TAIL slice, so a day that ever
    // exceeded it would lose its OPENING rather than its close — the quiet end
    // of the failure, and the reason the headroom is real rather than tidy.
    const [d, rd, bars] = await Promise.all([
      getJSON('/api/raw/file?root=state&path=sndk_reversion/' + encodeURIComponent(pay.session) + '.jsonl&limit=400'),
      getJSON('/api/raw/file?root=state&path=sndk_reads/'      + encodeURIComponent(pay.session) + '.jsonl&limit=40'),
      getJSON('/api/raw/file?root=state&path=sndk_bars/'       + encodeURIComponent(pay.session) + '.jsonl&limit=420'),
    ]);
    DIARY = (d.body && Array.isArray(d.body.rows)) ? d.body.rows : [];
    READS = (rd.body && Array.isArray(rd.body.rows)) ? rd.body.rows : [];
    BARS  = (bars.body && Array.isArray(bars.body.rows)) ? bars.body.rows : [];
  }
  WIN = null;                       // a new payload earns a new window
  paintAll();
}

async function loadSpot(){
  const r = await getJSON('/api/spot?ticker=SNDK');
  const s = r.body && r.body.spot;
  LIVE = (typeof s === 'number' && isFinite(s)) ? r.body : null;   // fail open, silently
  if(PAY) paintAll();
}

/* ---- derived state shared by every region ------------------------------ */

function state(){
  // strikes-1 (09-05): the model now reads the Strikes Payload, which has no
  // walls, magnet or regime. The station still builds the Scene Payload every
  // scan for the wake gate and ships it under `legacy`; the glance draws its
  // ladder from that, since every mark here is one of those.
  const scene = (PAY.legacy && PAY.legacy.scene) || PAY.scene;
  const gates = PAY.gates || {};
  const S = (gates.stale_book_min != null) ? gates.stale_book_min : 6;
  // 60, not 45: HEARTBEAT_MIN in sndk_read.py. The fallback only shows when the
  // payload has not sent the gates, and a wrong fallback is worse than none —
  // it tells the reader the model is due 15 minutes before it is.
  const H = (gates.heartbeat_min  != null) ? gates.heartbeat_min  : 60;
  const age = bookAge(PAY);
  const q = shownPrice(scene, LIVE);
  const withdrawn = !(LIVE && LIVE.spot != null) && (age.unknown || age.min > H);
  const diaryLast = DIARY.filter(r => r && r.ticker === 'SNDK').slice(-1)[0] || null;
  return {
    scene, S, H, age,
    stale: age.unknown || age.min > S,
    withdrawn,
    price: withdrawn ? null : q,
    ref: q,                                     // geometric reference even when withdrawn
    diaryLast,
    // Bars first, diary only when the sidecar has nothing. See barPoints for
    // the 2026-09-09 outage that made this the default rather than a nicety:
    // the diary's straight line across a six-hour hole was wrong by $66.40,
    // and it looked exactly like a real price path.
    points: pathPoints(),
    vwap: vwapPrice(scene, diaryLast),
    sigma: ((scene.scale || {}).one_sigma_dollars),
  };
}

function pathPoints(){
  // BY COVERAGE, not by presence.
  //
  // The first version was `if(bars.length >= 2) return bars` — which is the
  // mirror image of the bug the bar sidecar was introduced to fix. A bars job
  // that dies at 10:00 leaves 30 rows on disk, clears that test forever, and
  // the chart then draws 09:30 to 10:00 for the rest of the session while the
  // scanner's diary holds every minute of it. It fails honestly (the break
  // rule and the detached-quote gutter still draw, so no line is invented),
  // but honest is not the same as right.
  //
  // So the two series are compared on how far each one REACHES, and the fresher
  // record wins. Bars keep the tie and every close call: they are one-minute
  // and the diary is every couple of minutes, so on a healthy day the bars are
  // both fresher and denser, and on a day the bars never covered the diary is
  // the only thing there is.
  const b = barPoints(BARS), d = tapePoints(DIARY);
  if(b.length < 2) return d;
  if(d.length < 2) return b;
  const GRACE_MS = 10 * 60 * 1000;   // two missed bars plus a margin
  return (d[d.length - 1].t - b[b.length - 1].t > GRACE_MS) ? d : b;
}

// The overlay is painted opaque, so it covers the empty page from the first
// frame rather than arriving after it. That makes the FLOOR the thing to get
// right: a station on the same LAN answers in ~40ms, and an overlay that
// appears and vanishes inside 100ms is a flash that reads as a glitch. Held to
// 260ms it is either genuinely unnoticed or genuinely a loading state.
const LOAD_MIN_MS = 260;
const LOAD_T0 = Date.now();

function clearLoading(){
  // Real content is on screen; the overlay has done its job. Called from BOTH
  // the success and the failure paths — a station that is down must not end up
  // with a spinner sitting on top of its own error message, which is the
  // classic way a loading state outlives the load.
  const el = $('load');
  if(!el || el.hidden || el.dataset.going) return;
  el.dataset.going = '1';                       // a repaint must not restart the fade
  const wait = Math.max(0, LOAD_MIN_MS - (Date.now() - LOAD_T0));
  setTimeout(() => {
    el.classList.add('going');                  // fade, so it does not snap away
    setTimeout(() => { el.hidden = true; }, 200);
  }, wait);
}

function paintAll(){
  const st = state();
  paintMast(st);
  paintRegime(st);
  paintLadder(st);
  paintGate(st);
  paintRead();
  paintFoot(st);
  clearLoading();
}

/* ---- in-plot word placement -------------------------------------------- */

function clearRow(want, top, bottom, ruleYs){
  // A 10px word occupies roughly baseline-8 .. baseline+2. CLEAR keeps the
  // glyphs off the rule; STEP is the tag solver's own row pitch, so a displaced
  // word lands on a row of the plot rather than between two of them — at 13 it
  // cleared the VWAP rule but still sat 2px under the VWAP lane word and the
  // two read as one row.
  const CLEAR = 13, STEP = 20;
  const ok = y => (y - 9) >= top && (y + 3) <= bottom
                && !ruleYs.some(r => Math.abs(r - y) < CLEAR);
  if(ok(want)) return want;
  for(let i = 1; i <= 10; i++){
    if(ok(want + i * STEP)) return want + i * STEP;
    if(ok(want - i * STEP)) return want - i * STEP;
  }
  return null;
}

/* ---- A. masthead ------------------------------------------------------- */

function paintMast(st){
  const sc = st.scene, c = sc.clock || {};
  // PAY, not the scene: sr-8 moved `instrument` out to the wrapper. (A stash
  // transplant had this reading `d`, a name in no scope — every paint threw.)
  $('ticker').textContent = PAY.instrument ? String(PAY.instrument) : '';
  $('livedot').hidden = !(LIVE && LIVE.spot != null);

  let exp = '';
  const fe = c.front_expiry || {};
  // sr-7 renames: dte -> days_to_expiry, date -> expiry_date
  if(fe.days_to_expiry === 0) exp = 'EXPIRES TODAY';
  else if(fe.expiry_date) exp = 'EXP ' + new Date(fe.expiry_date + 'T00:00:00')
                              .toLocaleDateString('en-US', {weekday:'short'}).toUpperCase();
  else if(fe.days_to_expiry != null)
    exp = 'EXP IN ' + fe.days_to_expiry + (fe.days_to_expiry === 1 ? ' DAY' : ' DAYS');
  $('expiry').textContent = exp;

  const f = $('fresh');
  if(st.age.unknown){ f.textContent = 'LAST SCAN · AGE UNKNOWN'; f.className = 'fresh bad'; }
  else {
    const lead = PAY.as_of === 'live' ? 'BOOK ' : 'LAST SCAN ';
    f.textContent = (lead + gMinutes(st.age.min)).toUpperCase();
    f.className = 'fresh' + (st.age.min > st.H ? ' bad' : st.age.min > st.S ? ' warn' : '');
  }

  const px = $('px');
  if(st.withdrawn || !st.price){
    px.textContent = '—'; px.className = 'px withdrawn';
    $('chg').hidden = true;
    const ls = $('lastscan');
    const v = st.ref ? gUsd(st.ref.v).replace('$','') : null;
    if(v && !st.age.unknown){
      ls.textContent = ('LAST SCAN ' + v + ' · ' + gMinutes(st.age.min) + ' AGO').toUpperCase();
      ls.hidden = false;
    } else ls.hidden = true;
    return;
  }
  px.textContent = gUsd(st.price.v).replace('$','');
  px.className = 'px';
  $('lastscan').hidden = true;

  const pct = dayChange(st.scene, LIVE, st.diaryLast);
  const chg = $('chg');
  if(pct == null){ chg.hidden = true; return; }
  chg.hidden = false;
  chg.textContent = (pct > 0 ? '▲ ' : pct < 0 ? '▼ ' : '· ') + Math.abs(pct).toFixed(2) + '%';
  chg.className = 'chg ' + (pct > 0 ? 'up' : pct < 0 ? 'dn' : 'flat');
}

/* ---- B. regime --------------------------------------------------------- */

function paintRegime(st){
  const r = st.scene.regime || {};
  const g = gammaIsLong(r);
  // From envParts, not written out again here. The phone and the desktop must
  // never describe one board in two voices, and the previous version asked a
  // comment to guarantee that.
  const parts = envParts(r);
  const gloss = parts.lean == null ? parts.unmeasured : parts.lean;
  const word = parts.word;
  const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : '';

  if(word){ $('regWord').textContent = cap(word); $('regGloss').textContent = gloss; }
  else if(r.gamma_sign != null || g != null){ $('regWord').textContent = cap(gloss); $('regGloss').textContent = ''; }
  else { $('regWord').textContent = ''; $('regGloss').textContent = 'Regime not measured'; }

  const sig = st.sigma;
  $('ruler').textContent = (sig != null && isFinite(sig)) ? 'TYPICAL MOVE $' + Math.round(sig) : '';
}

/* ---- F. foot ----------------------------------------------------------- */

function paintFoot(st){
  const g = gammaIsLong(st.scene.regime);
  $('foot').textContent = g == null
    ? 'Gamma sign not measured — no dealer behaviour claimed.'
    : "Hedge direction assumed from the board's gamma sign.";
}

/* ---- C. the ladder ----------------------------------------------------- */

function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function n1(v){ return (Math.round(v*10)/10).toFixed(1); }

function paintLadder(st){
  CLEAR_SAID = {call:false, put:false};   // reset above every early return
  const svg = $('svg');
  // Measure RAW, then decide, then clamp. The old line did Math.max(240, ...)
  // inline, which meant a collapsed container silently became a 240px chart —
  // the clamp erased the very condition worth reporting.
  const rawW = Math.round($('ladder').getBoundingClientRect().width);
  // The height is a constant now, so a vertical collapse is not reachable; what
  // still can be is a zero-WIDTH container — a card that has not laid out yet,
  // or a parent with display:none. The old guard measured HEIGHT and would not
  // have seen it. Under 240 there is no room for the plot plus the label
  // gutter, and drawing anyway produces a garbled chart rather than an empty
  // one. rawW of 0 is the not-laid-out case and must not draw either.
  const SVGH = LADDER_H - 1;
  if(!isFinite(rawW) || rawW < 240){
    svg.setAttribute('width', 240);
    svg.setAttribute('height', SVGH);
    svg.setAttribute('viewBox', '0 0 240 ' + SVGH);
    // the class carries a min-width, so the message is readable even when the
    // container it is reporting on has no width to give it
    svg.classList.add('too-narrow');
    svg.innerHTML = '<text class="p-word" x="10" y="24">CHART TOO NARROW</text>';
    return;
  }
  svg.classList.remove('too-narrow');
  const CW = rawW;
  svg.setAttribute('width', CW);
  svg.setAttribute('height', SVGH);
  svg.setAttribute('viewBox', '0 0 ' + CW + ' ' + SVGH);

  const sc = st.scene;
  const PLOT_R = CW - 86, PLOT_L = 8, PLOT_W = PLOT_R - PLOT_L;
  const MARK_L = PLOT_R + 6, MARK_R = MARK_L + 30, TAG_R = CW;
  // The live quote is a continuation of the tape only inside this gap; past it
  // it is a separate observation. One constant, used by BOTH the reach/break
  // rule and the x-domain, because they are the same judgement.
  const REACH_MAX_MIN = 30;
  // the gutter a detached quote's dot is drawn in, past the end of the record
  const DETACH_W = 20;

  const ref = st.ref ? st.ref.v : null;
  if(ref == null){
    svg.innerHTML = '<text class="p-word" x="11" y="34">NO PRICE MEASURED</text>';
    return;
  }

  // ---- window, computed once per payload and then frozen ----------------
  const p = sc.price || {};
  const sessRange = (p.session_high != null && p.session_low != null)
                  ? (p.session_high - p.session_low)
                  : (st.points.length >= 2
                     ? Math.max.apply(null, st.points.map(x=>x.s)) - Math.min.apply(null, st.points.map(x=>x.s))
                     : null);
  if(!WIN){
    const core = coreLevels(sc, ref, st.vwap, st.points);
    WIN = solveWindow(core, optionalLevels(sc), ref, st.sigma, sessRange);
    if(WIN) WIN.anchor = ref;
  }
  if(!WIN){ svg.innerHTML = '<text class="p-word" x="11" y="34">NO PRICE MEASURED</text>'; return; }

  // re-anchor: price near the edge earns a fresh window, and it JUMPS. A tween
  // would make every ordinary tick as salient as this rare one.
  //
  // The travel gate is what keeps it rare. A fresh window leaves price
  // 6/112 = 5.36% of the span inside its own edge — already inside the 12% band
  // — so without it the test is true again on the very next quote and the frozen
  // board slides on every tick instead of jumping once. Measured before the
  // gate: 296 of 300 ticks moved the 1450 rule, and a ten-cent oscillation at
  // the exile radius swung it 34px, flipping a wall between a rule and a marker.
  // Keep the constant BELOW 5.36%: that is what guarantees price re-anchors
  // before it can leave the window it is anchored in.
  const span0 = WIN.hi - WIN.lo;
  const nearEdge = (ref < WIN.lo + 0.12*span0 || ref > WIN.hi - 0.12*span0);
  const moved = (WIN.anchor == null) || Math.abs(ref - WIN.anchor) >= 0.05*span0;
  if(nearEdge && moved){
    const core = coreLevels(sc, ref, st.vwap, st.points);
    const w2 = solveWindow(core, optionalLevels(sc), ref, st.sigma, sessRange);
    if(w2){ WIN = w2; WIN.anchor = ref; }        // a null re-solve must not blank WIN
  }

  // A zero span sends NaN into every y, cy and height below, and a browser
  // silently falls back to 0 for each invalid length — the plot renders as
  // garbage pinned to the top edge with no message. Reachable with
  // one_sigma_dollars absent (the degenerate floor cannot fire) and one
  // distinct core level. Guarded here rather than inside solveWindow so the
  // re-anchor path above cannot throw.
  const span = WIN.hi - WIN.lo;
  if(!(span > 0)){ svg.innerHTML = '<text class="p-word" x="11" y="34">NO PRICE MEASURED</text>'; return; }
  const inWin = v => v != null && isFinite(v) && v >= WIN.lo && v <= WIN.hi;

  const wc = (sc.walls||{}).call || [], wp = (sc.walls||{}).put || [];
  const mag = (sc.magnet||{}).top_strikes;

  // every y that WILL receive a rule below, gated by push()'s own test
  const drawnY = new Set();
  const markDrawn = y => { if(inWin(y)) drawnY.add(+y); };
  if(wc[0] && wc[0].strike != null) markDrawn(Number(wc[0].strike));
  if(wp[0] && wp[0].strike != null) markDrawn(Number(wp[0].strike));
  for(const l of WIN.admitted) markDrawn(l.y);
  if(Array.isArray(mag) && mag.length && mag[0] && mag[0].strike != null)
    markDrawn(Number(mag[0].strike));                    // sr-7: dict entries
  for(const m of magnetRunners(sc)) markDrawn(m.y);

  // Only a BOOK level can be named at an edge. A tape price or the session low
  // has no strike, and its off-window portion is already carried by the clip;
  // admitting them breaks the slot count the never-silently-dropped guarantee
  // rests on — which is the 2026-08-24 bug reappearing, ~70 exiled tape points
  // taking both slots while the wall the gate names in 30px type reaches no
  // pixel. Rank by KIND first: a wall's gex and a magnet's share are different
  // denominators and must never share a gauge. A level that already got a rule
  // is not named again. Split on the PRICE, not the padded bounds, or a level
  // exiled by less than the pad width lands in neither stack.
  const rank = l => (l.kind === 'wall' ? 2 : 1);
  const leftover = WIN.refused.concat(WIN.exiled)
    .filter(l => (l.kind === 'wall' || l.kind === 'magnet') && !drawnY.has(+l.y))
    .sort((a, b) => rank(b) - rank(a) || (b.gex || 0) - (a.gex || 0));
  // gex priority picks the pair; PRICE decides the row, on a plot whose whole
  // grammar is vertical = price. Descending on BOTH stacks: the top stack's row
  // 0 is the row farthest from the plot, the bottom stack's row 0 the nearest.
  const byPrice = a => a.sort((x, y) => y.y - x.y);
  const above = byPrice(leftover.filter(l => l.y > ref).slice(0, 2));
  const below = byPrice(leftover.filter(l => l.y < ref).slice(0, 2));
  const PAD_T = 12 + 13*above.length, PAD_B = 18 + 13*below.length;
  const plotTop = PAD_T, plotBottom = SVGH - PAD_B, plotH = plotBottom - plotTop;
  const k = plotH / span;
  const yFor = v => plotTop + (WIN.hi - v) * k;

  // ---- levels ------------------------------------------------------------
  const ruled = [];
  const push = l => { if(inWin(l.y)) ruled.push(l); };
  if(wc[0] && wc[0].strike != null) push(_lvlWall(wc[0], 'call', true));
  if(wp[0] && wp[0].strike != null) push(_lvlWall(wp[0], 'put', true));
  for(const l of WIN.admitted) push(l);
  if(Array.isArray(mag) && mag.length && mag[0] && mag[0].strike != null)
    push({y:Number(mag[0].strike), kind:'magnet', lead:true,
          share:Number(mag[0].share_of_book_gamma_pp), weight:1});
  for(const m of magnetRunners(sc)) push(m);
  const levels = mergeLevels(ruled, span);

  let o = '';
  o += '<defs><clipPath id="pc"><rect x="' + PLOT_L + '" y="' + plotTop
     + '" width="' + PLOT_W + '" height="' + n1(plotH) + '"/></clipPath></defs>';

  // ---- clipped plot content ---------------------------------------------
  let g = '';
  if(p.session_high != null && p.session_low != null){
    const yh = yFor(p.session_high), yl = yFor(p.session_low);
    g += '<rect class="p-band" x="' + PLOT_L + '" y="' + n1(yh) + '" width="' + PLOT_W
       + '" height="' + n1(Math.max(0, yl - yh)) + '"/>';
  }
  const lp = livePoint(LIVE);
  const pts = st.points;
  const tapeEnd = pts.length ? pts[pts.length-1].t : null;
  // ---- the x-domain -----------------------------------------------------
  // The live quote may EXTEND the domain only while it is continuous with the
  // tape. Past REACH_MAX_MIN it is a separate observation, and stretching the
  // axis out to reach it crushes the session it is supposed to draw.
  //
  // Measured 2026-09-07: the newest scan was 2026-09-04's, 189 tape points
  // across 6.47 hours, and the live quote was 78.5 hours newer. t1 took the
  // quote's clock, so the domain ran 85.0 hours and the whole session drew
  // inside the leftmost 7.6% of the plot (x 8..28 of 8..276) with 92% empty --
  // under an axis still labelled 09:30 on the left and SCAN 15:58 on the right.
  // The feet named an interval the plot did not draw.
  //
  // The reach/break rule below already rules that a gap this wide is not a
  // continuation. The domain has to make the same ruling or the two contradict
  // each other on the same pixels.
  const gapMin = (lp && tapeEnd != null) ? (lp.t - tapeEnd) / 60000 : null;
  const detached = gapMin != null && gapMin > REACH_MAX_MIN;
  // detached: the record owns the plot, and the quote gets its own gutter at
  // the right so the dot is legibly PAST the end of the record, not on it
  const PATH_R = detached ? PLOT_R - DETACH_W : PLOT_R;
  const t0 = pts.length ? pts[0].t : 0;
  const t1 = detached ? tapeEnd
           : Math.max(tapeEnd != null ? tapeEnd : 1, lp ? lp.t : -Infinity);
  const xFor = t => PLOT_L + ((t - t0) / ((t1 > t0) ? (t1 - t0) : 1)) * (PATH_R - PLOT_L);
  if(pts.length >= 2)
    g += '<polyline class="p-path" points="' + pts.map(q => n1(xFor(q.t)) + ',' + n1(yFor(q.s))).join(' ') + '"/>';

  // Detached, the dot is centred in its own gutter, clear of the break rule on
  // one side and the plot's right edge on the other. Clamped to PLOT_R-8 it sat
  // half on the edge of the range band with its halo spilling over it.
  const dotX = detached ? (PLOT_R - DETACH_W / 2)
             : Math.min(lp ? xFor(lp.t) : (tapeEnd != null ? xFor(tapeEnd) : PLOT_R), PLOT_R - 8);
  const priceY = yFor(ref);
  if(!st.withdrawn && lp && tapeEnd != null && lp.t > tapeEnd){
    const lx = xFor(tapeEnd), ly = yFor(pts[pts.length-1].s);
    if(!detached)
      g += '<path class="p-reach" d="M' + n1(lx) + ',' + n1(ly) + ' L' + n1(dotX) + ',' + n1(priceY) + '"/>';
    else
      // a dash across six hours implies a continuity that does not exist
      g += '<line class="p-break" x1="' + n1(lx) + '" y1="' + plotTop + '" x2="' + n1(lx) + '" y2="' + n1(plotBottom) + '"/>';
  }
  o += '<g clip-path="url(#pc)">' + g + '</g>';

  // ---- the clear side, drawn with its extent -----------------------------
  // Drawn BEFORE the range label so its baselines are known: both are in-plot
  // words at the left edge, and on a 280px ladder they landed 8px apart.
  const wordRows = [];
  // Every horizontal rule this plot will draw. An in-plot word that lands on
  // one is unreadable: the --ground halo strokes GLYPHS, and the widest gaps in
  // a word are its spaces, which have no glyph to stroke. Measured 2026-09-07,
  // "NO PUT WALL BELOW" sat at baseline 244.5 with the VWAP rule at 240.7 — the
  // dashes ran through the word's three spaces and on into "VWAP 1,684" in the
  // lanes, so the whole row read as one sentence.
  const ruleYs = levels.map(l => yFor(l.y));
  if(inWin(st.vwap)) ruleYs.push(yFor(st.vwap));
  if(!st.withdrawn && inWin(ref)) ruleYs.push(priceY);
  for(const side of ['call','put']){
    if((sc.walls||{})[side + '_side_has_no_wall'] !== true) continue;   // sr-7 rename
    // The flag was measured against the SCAN spot. If a wall of the other pool
    // now sits on this side of the price on screen, the side is not empty as
    // drawn — say nothing here and let the gate footer carry the qualified note.
    const other = side === 'call' ? 'put' : 'call';
    const cross = ((sc.walls||{})[other] || [])
      .concat([(sc.walls||{})[other + '_heaviest_wall_behind_the_ladder']])
      .some(e => e && e.strike != null &&
                 (side === 'call' ? Number(e.strike) > ref : Number(e.strike) < ref));
    if(cross) continue;
    const a = side === 'call' ? plotTop : priceY, b = side === 'call' ? priceY : plotBottom;
    if(!(b > a)) continue;
    o += '<path class="p-brk" d="M9,' + n1(a) + ' L3,' + n1(a) + ' L3,' + n1(b) + ' L9,' + n1(b) + '"/>';
    if((b - a) >= 34){
      const by = clearRow((a + b) / 2, a, b, ruleYs);
      if(by == null) continue;   // nowhere clear: the bracket alone states the
                                 // extent and the gate footer takes the words
      // qualified, because the flag is qualified: call_side_has_no_wall means no
      // CALL-SIGNED cluster above spot. A wrongly-signed pile there is dropped
      // from both pools and the flag still fires — true on 79 of 79 rows of the
      // reference diary, over a cluster carrying 34.6% of book gamma.
      o += '<text class="p-word dim" x="12" y="' + n1(by) + '">'
         + (side === 'call' ? 'NO CALL WALL ABOVE' : 'NO PUT WALL BELOW') + '</text>';
      wordRows.push(by);
      CLEAR_SAID[side] = true;
    }
  }

  if(p.session_high != null && p.session_low != null){
    const yh = yFor(p.session_high), yl = yFor(p.session_low), by = yh - 4;
    // The bracket label wins a collision: it reports a measured emptiness, a
    // finding, while this names something the band's own shading already shows.
    // A RULE wins for the same reason, and this label cannot be moved the way
    // the bracket's can — it means "the top of the band" and nowhere else. At
    // 320x568 its baseline lands 10px under the 1,750 wall and its --ground
    // halo takes a bite out of a 2.8px jade rule; the band's own shading still
    // states the range, so the words go and the mark stays.
    const clash = wordRows.some(r => Math.abs(r - by) < 12)
               || ruleYs.some(r => Math.abs(r - by) < 12);
    if((yl - yh) >= 24 && by >= plotTop + 9 && !clash)
      o += '<text class="p-word" x="11" y="' + n1(by) + '">TODAY&#39;S RANGE</text>';
  }

  // ---- rules -------------------------------------------------------------
  for(const l of levels){
    const y = n1(yFor(l.y));
    if(l.kind === 'magnet'){
      o += '<line class="' + (l.lead ? 'p-mag' : 'p-magrun') + '" x1="' + PLOT_L + '" y1="' + y
         + '" x2="' + PLOT_R + '" y2="' + y + '"'
         + (l.lead ? '' : ' style="stroke-opacity:' + (l.weight||0.5).toFixed(2) + '"') + '/>';
    } else if(l.kind === 'wall'){
      o += '<line class="p-wall ' + l.side + '" x1="' + PLOT_L + '" y1="' + y + '" x2="' + PLOT_R + '" y2="' + y
         + '" style="stroke-width:' + wallTier(l.gex) + ';stroke-opacity:' + (l.nearest ? '1' : '.62') + '"/>';
    }
  }
  if(inWin(st.vwap))
    o += '<line class="p-vwap" x1="' + PLOT_L + '" y1="' + n1(yFor(st.vwap)) + '" x2="' + PLOT_R
       + '" y2="' + n1(yFor(st.vwap)) + '"/>';

  // ---- price -------------------------------------------------------------
  if(!st.withdrawn && inWin(ref)){
    o += '<line class="p-prule" x1="' + PLOT_L + '" y1="' + n1(priceY) + '" x2="' + PLOT_R + '" y2="' + n1(priceY) + '"/>';
    o += '<circle class="p-halo" cx="' + n1(dotX) + '" cy="' + n1(priceY) + '" r="7"/>';
    o += '<circle class="p-dot"  cx="' + n1(dotX) + '" cy="' + n1(priceY) + '" r="3.6"/>';
  }

  // ---- the bug: this one, the one the card is about ----------------------
  const near = nearestWall(sc.walls, ref);
  if(near && inWin(near.strike)){
    const y = yFor(near.strike);
    o += '<path class="p-bar ' + near.side + '" d="M' + PLOT_R + ',' + n1(y)
       + ' L' + (PLOT_R+5) + ',' + n1(y-5) + ' L' + (PLOT_R+5) + ',' + n1(y+5) + ' Z" style="fill-opacity:1"/>';
  }

  // ---- tag rows, solved once for every member including the price chip ---
  const members = [];
  for(const l of levels){
    // The never-drop set is exactly six (chip, both nearest, both
    // heaviest_behind, the lead magnet), which is what the cap of 7 was sized
    // for. walls.*[1] is the one wall that may drop, and VWAP drops before it.
    // Without the tier, a cap overflow could drop a heaviest_behind and leave
    // the thickest stroke on the plot with its price nowhere on screen.
    if(l.kind === 'wall')
      members.push({y:l.y, cls:'p-tag ' + l.side, lvl:l, keep:(l.nearest || l.behind) ? 2 : 1});
    else if(l.kind === 'magnet' && l.lead) members.push({y:l.y, cls:'p-tag mag', lvl:l, keep:2});
  }
  if(inWin(st.vwap)) members.push({y:st.vwap, cls:'p-tag vwap', vwap:true, keep:0});
  if(!st.withdrawn && inWin(ref)) members.push({y:ref, chip:true, keep:3});
  members.sort((a,b) => (b.keep - a.keep) || (a.y - b.y));
  const kept = members.slice(0, 7).sort((a,b) => a.y - b.y);
  const rows = layoutLabels(kept.map(m => yFor(m.y)), 20, plotTop + 10, plotBottom - 10);

  kept.forEach((m, i) => {
    const trueY = yFor(m.y), rowY = rows[i];
    if(Math.abs(rowY - trueY) > 2)
      o += '<path class="p-tie" d="M' + (PLOT_R+1) + ',' + n1(trueY) + ' L' + (MARK_L-1) + ',' + n1(rowY) + '"/>';
    if(m.chip){
      o += '<rect class="p-chip" x="' + (CW-46) + '" y="' + n1(rowY-9) + '" width="46" height="18" rx="2"/>';
      o += '<text class="p-chiptx" x="' + (CW-5) + '" y="' + n1(rowY+4.5) + '">'
         + gUsd(m.y, 0).replace('$','') + '</text>';
      return;
    }
    if(m.vwap) o += '<text class="p-lane" x="' + MARK_R + '" y="' + n1(rowY+3.5) + '">VWAP</text>';
    const l = m.lvl;
    if(l && l.kind === 'wall'){
      const bar = railWidth(l.gex, 20);          // null gex -> no bar AND no track
      if(bar){
        o += '<rect class="p-bar ' + l.side + '" x="' + MARK_L + '" y="' + n1(rowY-1.5)
           + '" width="' + n1(bar.w) + '" height="3"/>';
        if(bar.clipped) o += '<rect class="p-clip" x="' + (MARK_L+20) + '" y="' + n1(rowY-3.5) + '" width="2" height="7"/>';
      }
    }
    if(l && (l.magnet || l.kind === 'magnet'))
      o += '<rect class="p-diamond" x="' + (MARK_L+24.5) + '" y="' + n1(rowY-2.5)
         + '" width="5" height="5" transform="rotate(45 ' + (MARK_L+27) + ' ' + n1(rowY) + ')"/>';
    const lead = (near && l && l.kind === 'wall' && l.y === near.strike) ? ' lead' : '';
    o += '<text class="' + m.cls + lead + '" x="' + TAG_R + '" y="' + n1(rowY+4.5) + '">'
       + gUsd(m.y, 0).replace('$','') + '</text>';
  });

  // ---- refused levels are NAMED, never silently dropped -------------------
  // When the nearest wall is itself off-window the bug triangle cannot point at
  // it, so its marker carries the weight instead. Matched on kind AND side AND
  // strike, so an exiled magnet on the same strike cannot steal the emphasis.
  const namedEdge = (near && !inWin(near.strike)) ? Number(near.strike) : null;
  const edgeCls = l => 'p-edge' + ((namedEdge != null && l.kind === 'wall'
                     && l.side === near.side && +l.y === namedEdge) ? ' lead' : '');
  above.forEach((l, i) => {
    o += '<text class="' + edgeCls(l) + '" x="' + TAG_R + '" y="' + (10 + 13*i) + '">▲ '
       + gUsd(l.y,0).replace('$','') + (l.behind ? ' HEAVIEST' : '') + '</text>';
  });
  below.forEach((l, i) => {
    o += '<text class="' + edgeCls(l) + '" x="' + TAG_R + '" y="' + n1(plotBottom + 12 + 13*i) + '">▼ '
       + gUsd(l.y,0).replace('$','') + (l.behind ? ' HEAVIEST' : '') + '</text>';
  });

  // ---- axis feet ---------------------------------------------------------
  const c = sc.clock || {};
  if(pts.length)
    o += '<text class="p-axis" x="' + PLOT_L + '" y="' + (SVGH-5) + '">'
       + etTime(pts[0].t) + '</text>';
  // a countdown computed at scan time is a lie when read hours later, and it is
  // the one label on the plot that ages silently
  let rightFoot = '';
  if(st.stale){
    const t = Date.parse(PAY.row_ts);
    const et = etTime(t);
    if(et) rightFoot = 'SCAN ' + et;
  } else if(c.minutes_to_close != null){
    rightFoot = c.minutes_to_close > 0 ? (gMinutes(c.minutes_to_close) + ' left').toUpperCase() : 'CLOSED';
  }
  if(rightFoot)
    o += '<text class="p-axis" x="' + PLOT_R + '" y="' + (SVGH-5) + '" text-anchor="end">' + esc(rightFoot) + '</text>';

  svg.innerHTML = o;
}

function _lvlWall(e, side, nearest){
  // sr-7/obs-2 renames on the scene entry, mirroring glance.js _wall — the
  // INTERNAL name for the share stays `gex`.
  const g = e.cluster_share_of_book_gamma_pp;
  return {y:Number(e.strike), kind:'wall', side, nearest:!!nearest,
          gex:(g != null && isFinite(g)) ? Number(g) : null,
          held:(e.unchanged_for_min != null) ? e.unchanged_for_min : e.unchanged_for_at_least_min,
          heldExact:e.unchanged_for_min != null};
}

/* ---- D. gate — the amplification of the nearest level. Never hides. ----- */

function paintGate(st){
  const sc = st.scene, walls = sc.walls || {}, FULL = 20;
  const gate = $('gate');
  const set = (id, t) => { $(id).textContent = t || ''; };
  const hideRows = () => { $('gRowA').hidden = true; $('gRowB').hidden = true; };

  if(bothSidesClear(walls)){
    gate.classList.add('empty');   // NOT className=: it would drop `card`
    set('gDir','NO WALL EITHER WAY'); set('gMeas',''); set('gStrike','');
    set('gMech','The board was read and holds nothing above or below price.');
    hideRows(); set('gFoot',''); $('gStrike').className = 'g-k';
    return;
  }
  const ref = st.ref ? st.ref.v : null;
  const w = nearestWall(walls, ref);
  if(!w){
    gate.classList.add('empty');   // NOT className=: it would drop `card`
    set('gDir','NO WALL MEASURED'); set('gMeas',''); set('gStrike',''); set('gMech','');
    hideRows(); set('gFoot',''); $('gStrike').className = 'g-k';
    return;
  }
  gate.classList.remove('empty');

  // Against the price ON SCREEN, like the distance and the sentence beside it.
  // walls_ladder buckets a cluster by the SCAN spot, so a live tick through the
  // nearest wall makes the payload's side label name a direction the plot
  // directly above this card contradicts.
  let dir = (w.strike > ref ? '▲ NEXT ABOVE' : '▼ NEXT BELOW');
  if(st.withdrawn){
    const t = Date.parse(PAY.row_ts);
    const et = etTime(t);
    if(et) dir += ' · AT THE ' + et + ' SCAN';
  }
  set('gDir', dir);

  // measured against the price ON SCREEN, never walls[].sigma — that was taken
  // against a spot the reader can no longer see
  const d = wallDistance(w.strike, ref);
  // sr-7 rename: unchanged_for_min / unchanged_for_at_least_min
  const held = (w.unchanged_for_min != null) ? w.unchanged_for_min : w.unchanged_for_at_least_min;
  const exact = w.unchanged_for_min != null;
  const bits = [];
  if(d) bits.push('$' + Math.round(d.dollars));
  if(held != null) bits.push(('held ' + gMinutes(held) + (exact ? '' : '+')).toUpperCase());
  set('gMeas', bits.join(' · '));

  $('gStrike').textContent = gUsd(w.strike, 0).replace('$','');
  $('gStrike').className = 'g-k ' + w.side;

  // gamma_sign is the literal string "unknown" on 7.1% of rows. No sign, no
  // sentence — the strike, direction, distance and gauge all still stand.
  const b = wallBehaviour(sc.regime, w.strike, ref);
  set('gMech', b ? b.english : '');

  const beyond = beyondWall(walls, w.side);
  let foot = '';

  // w is the raw scene entry (nearestWall passes it through), so the share
  // wears its obs-2 scene name here rather than the internal `gex`
  const share = w.cluster_share_of_book_gamma_pp;
  if(share != null && isFinite(share)){
    $('gRowA').hidden = false;
    $('gBarA').style.width = Math.max(2, Math.min(100, share / FULL * 100)).toFixed(1) + '%';
    $('gValA').innerHTML = '<b>' + esc(share) + '%</b> of book gamma';
  } else { $('gRowA').hidden = true; foot = 'Weight not measured.'; }

  if(beyond && beyond.gex != null && isFinite(beyond.gex)){
    $('gRowB').hidden = false;
    $('gBarB').style.width = Math.max(2, Math.min(100, beyond.gex / FULL * 100)).toFixed(1) + '%';
    $('gValB').innerHTML = '<b>' + esc(beyond.gex) + '%</b> at '
                         + esc(gUsd(beyond.strike,0).replace('$','')) + ' · '
                         + (beyond.heaviest ? 'heaviest' : 'next out');
  } else {
    $('gRowB').hidden = true;
    if(!foot){
      if(beyond && beyond.alone) foot = 'Nothing else on this side of the board.';
      else if(beyond && beyond.strike != null) foot = 'Next wall at ' + gUsd(beyond.strike,0) + '.';
    }
  }
  // The measured-empty side appears exactly once. It is normally said by the
  // bracket's LABEL in the plot, which also shows its extent; the footer speaks
  // only when that label did not draw — a bracket under 34px, a bracket
  // suppressed because price crossed a wall of the other pool, or a ladder that
  // took an early return. Gating on the path instead said it twice in the
  // withdrawn state and lost it entirely at 320px.
  const _far = w.side === 'call' ? 'put' : 'call';
  if(!foot && !CLEAR_SAID[_far]) foot = farSideNote(walls, w.side) || '';
  set('gFoot', foot);
}

/* ---- E. read — an opinion, not a measurement ---------------------------- */

function paintRead(){
  const m = modelRead(READS);
  const mark = $('rdMark'), line = $('rdLine'), age = $('rdAge');
  if(!m){
    // classList, NOT className. The chip is `class="r"` — the label row's
    // right-hand slot, which is what gives it margin-left:auto and its colour —
    // and assigning className wiped that on every paint, so the age drifted
    // left and dimmed. Same fault as the one paintGate had; the markup was
    // renamed in the light rebuild and these two lines were not.
    mark.textContent = ''; age.textContent = ''; age.classList.remove('old');
    // `expired` was a tier obs-1 deleted (see below) and no rule has existed
    // for it since. Absence is not age, and it is styled as itself.
    line.textContent = 'NO READING TODAY';
    line.className = 'rd-line wordless';
    return;
  }
  // obs-1 removed the 'expired' tier. It hid a reading past 120 minutes, which
  // was four times a 30-minute forecast horizon; an observation has no horizon,
  // so age is shown rather than used to blank the text. NO READING TODAY above
  // is a different thing and stays: that is absence, not age.
  const a = gMinutes(m.ageMin);
  age.textContent = a.toUpperCase();
  age.classList.toggle('old', m.tier === 'aged');
  // obs-1: a count of what is unusual, not a direction. Empty when quiet,
  // because the common answer must not look like an alarm.
  mark.textContent = m.quiet ? '' : String(m.count || '');
  // model output, written with textContent only: it never touches innerHTML and
  // never enters the SVG string
  const at = etTime(m.at);
  // `wordless` means the model authored no sentence and what follows is a note
  // attached to a LEVEL. It is shown — it is the only thing there is — but not
  // in the reading's own voice, and it names the level it belongs to, so the
  // surface is never caught putting words in the model's mouth.
  const lead = m.tier === 'aged' ? at + ' · ' : '';
  line.textContent = m.wordless
    ? lead + 'No sentence this scan — ' + m.line
    : lead + m.line;
  line.className = 'rd-line' + (m.tier === 'aged' ? ' aged' : '')
                             + (m.wordless ? ' wordless' : '');
}

/* ---- run --------------------------------------------------------------- */

function start(){
  fitTabs();
  sizeLadder();
  loadPayload(); loadSpot();
  clearInterval(T_PAY); clearInterval(T_SPOT);
  T_PAY  = setInterval(loadPayload, 60000);
  T_SPOT = setInterval(loadSpot, 5000);
}
function stop(){ clearInterval(T_PAY); clearInterval(T_SPOT); T_PAY = T_SPOT = null; }

document.addEventListener('visibilitychange', () => document.hidden ? stop() : start());
window.addEventListener('resize', () => { fitTabs(); sizeLadder(); if(PAY) paintAll(); });
start();
