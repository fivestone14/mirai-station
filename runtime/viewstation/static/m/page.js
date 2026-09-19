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
  // 196. SVGH is LADDER_H-1, and PAD_T/PAD_B are 9 and 29 plus 13 per edge
  // marker (the 29 holds the volume ribbon and the axis feet), so the plot is
  // 157px with no edge markers, 144 with one, 131 with two on one side. That
  // is the range in which the wall rules stay separable and the price path
  // keeps its shape; below roughly 130 the label solver starts displacing a
  // label further than the level it names. A row of new contracts sits 16
  // from its neighbour, not 13 (paintLadder), so on the 15:10 board of
  // 2026-09-16 two of them take the plot from 144 to 115. The height was
  // budgeted and the width was not, so when the chart needed room it came
  // sideways — the bleed and the derived gutter in paintLadder — and this
  // constant did not move.
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
    sigma: ((scene.scale || {}).one_sigma_dollars),
    // The strikes payload, not the ladder's legacy scene: the per-strike rows
    // live there and nowhere else. An era that ships no rows draws no bars.
    strikes: ((PAY.scene || {}).strikes) || null,
    frames: ((PAY.scene || {}).frames) || null,
    vol: volumeBlocks(BARS, 5),
    openRange: ((((PAY.scene || {}).context || {}).ranges || {}).opening) || null,
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
  paintDayMove(st);
  paintLadder(st);
  paintLevels(st);
  paintToday();
  paintRead();
  paintHalf(st);
  paintFoot(st);
  clearLoading();
}

/* ---- A. masthead ------------------------------------------------------- */

function expiryDropped(){
  // The masthead's first row wraps between whole items, so a row too long for
  // the phone puts the expiry on a line of its own under the ticker. An empty
  // or unmeasured box has no height and never counts as dropped.
  const t = $('ticker').getBoundingClientRect(), e = $('expiry').getBoundingClientRect();
  return e.height > 0 && e.top >= t.bottom;
}

function paintMast(st){
  const sc = st.scene, c = sc.clock || {};
  // PAY, not the scene: sr-8 moved `instrument` out to the wrapper. (A stash
  // transplant had this reading `d`, a name in no scope — every paint threw.)
  $('ticker').textContent = PAY.instrument ? String(PAY.instrument) : '';
  $('livedot').hidden = !(LIVE && LIVE.spot != null);

  let exp = '';
  const fe = c.front_expiry || {};
  // sr-7 renames: dte -> days_to_expiry, date -> expiry_date. What ends is
  // said, not abbreviated: "EXP FRI" taught nothing to a reader who does not
  // already know what expires.
  if(fe.days_to_expiry === 0) exp = 'OPTIONS END TODAY';
  else if(fe.expiry_date) exp = 'OPTIONS END ' + new Date(fe.expiry_date + 'T00:00:00')
                              .toLocaleDateString('en-US', {weekday:'short'}).toUpperCase();
  else if(fe.days_to_expiry != null)
    exp = 'OPTIONS END IN ' + fe.days_to_expiry + (fe.days_to_expiry === 1 ? ' DAY' : ' DAYS');
  $('expiry').textContent = exp;

  // The pill with its subject, then without it. It shares the first row with
  // the ticker and the expiry, and their widths turn on the weekday, the live
  // dot and the age, whose figures are proportional here, so the choice is
  // made on the row as laid out, not on the phone's width alone. The subject
  // goes first; the expiry leaves the row only if the bare age still does not
  // fit beside it.
  const f = $('fresh');
  let words;
  if(st.age.unknown){
    words = ['SCAN AGE UNKNOWN', 'AGE UNKNOWN'];
    f.className = 'fresh bad';
  } else {
    const g = gMinutes(st.age.min).toUpperCase();
    const lead = PAY.as_of === 'live' ? 'BOOK ' : 'SCAN ';
    words = g === 'JUST NOW' ? ['JUST SCANNED', g] : [lead + g + ' OLD', g + ' AGO'];
    f.className = 'fresh' + (st.age.min > st.H ? ' bad' : st.age.min > st.S ? ' warn' : '');
  }
  f.textContent = words[0];
  if(expiryDropped()) f.textContent = words[1];

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

/* ---- B. the day's move ------------------------------------------------- */

function paintDayMove(st){
  // A day's move, and it says so. "Typical move" did not say over what, and
  // the half-hour card puts a usual half hour of $6 on the same stock.
  //
  // The regime word that shared this row is gone (2026-09-18). It was
  // classify_regime()'s vote of four reads — gamma sign, range against the
  // expected move, variance ratio, VIX term structure — needing two to agree.
  // On SNDK the last two are never fed (0 of 185 scans on 09-17), so it read
  // "Neutral" because too few reads voted, not because anything measured a
  // neutral market. Its gloss, "walls hold" / "walls give way" off the gamma
  // sign, had already gone on 2026-09-10 (law 2 in glance.js).
  const sig = st.sigma;
  const text = (sig != null && isFinite(sig)) ? 'USUAL DAY MOVE $' + Math.round(sig) : '';
  $('ruler').textContent = text;
  $('dayMove').hidden = !text;
}

/* ---- F. foot ----------------------------------------------------------- */

function paintFoot(){
  // One standing line, the same on every scan. It used to name the gamma sign
  // the dealer sentences were keyed on; with those gone there is no claim left
  // to caveat, only the thing every mark here is.
  $('foot').textContent = 'Where the option positions sit — not a forecast of where price goes.';
}

/* ---- C. the ladder ----------------------------------------------------- */

function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function n1(v){ return (Math.round(v*10)/10).toFixed(1); }

function paintLadder(st){
  const svg = $('svg');
  // The head says what the chart shows and which scan drew it. A stale axis
  // then needs only the clock at its right foot, 09:30 to 15:11, where it
  // said "SCAN 15:11" (WORDS-SPEC #8, #9, #12).
  const scanAt = etTime(Date.parse(PAY.row_ts));
  $('ldWhen').textContent = scanAt ? 'AS OF ' + scanAt : '';
  // Measure RAW, then decide, then clamp. The old line did Math.max(240, ...)
  // inline, which meant a collapsed container silently became a 240px chart —
  // the clamp erased the very condition worth reporting.
  const rawW = Math.round($('ladder').getBoundingClientRect().width);
  // The height is a constant now, so a vertical collapse is not reachable; what
  // still can be is a zero-WIDTH container — a card that has not laid out yet,
  // or a parent with display:none. The old guard measured HEIGHT and would not
  // have seen it. Under 240 there is no room for the plot plus the label
  // gutter, and drawing anyway produces a garbled chart rather than an empty
  // one. rawW of 0 is the not-laid-out case and must not draw either. rawW is
  // the card's whole width since the ladder bled into its padding (.ladder),
  // so this trips at a 272px phone, and at 240 the plot is still ~183px wide.
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
    const core = coreLevels(sc, ref, null, st.points);
    WIN = solveWindow(core, optionalLevels(sc, ref), ref, st.sigma, sessRange);
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
    const core = coreLevels(sc, ref, null, st.points);
    const w2 = solveWindow(core, optionalLevels(sc, ref), ref, st.sigma, sessRange);
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

  // ---- the gutter, as wide as the widest number it holds ----------------
  // The chip and the level tags sit right-aligned at TAG_R, and every one of
  // them is a price inside the window, so pricing the window's two ends prices
  // them all — and the gutter moves only when the window does. The chip keeps
  // 5px either side of its number; a tag at its heaviest weight clears the 5px
  // diamond at MARK_L by 4. It was a literal 56: a $1,500 board's number, which
  // a $9 board paid in full to print a 7px one.
  const ends = [WIN.lo, WIN.hi].map(v => gUsd(v, 0).replace('$',''));
  const widest = (px, weight) => Math.max(...ends.map(s => figW(s, px, weight)));
  const CHIP_W = Math.max(34, Math.ceil(widest(12, 700)) + 10, 5 + 4 + Math.ceil(widest(12, 800)));
  // PLOT_L 4 rather than 8 now the ladder bleeds to the card's edge: still an
  // inset, not a clipped edge
  const PLOT_L = 4, PLOT_R = CW - (6 + CHIP_W + 3), PLOT_W = PLOT_R - PLOT_L;
  const MARK_L = PLOT_R + 6, TAG_R = CW - 3;

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
  // THREE, not two. The cap has to be at least the size of the optional set or a
  // refused level reaches no pixel and no name — the 2026-08-24 bug. That set
  // was four (a behind wall and a second wall each side) and the cap matched it
  // exactly; it is six now that a nearest wall the day cannot reach is tried
  // here rather than anchored. The pad is computed from these lengths, so the
  // third row costs its 13px only on a day that has a third thing to say.
  const above = leftover.filter(l => l.y > ref).slice(0, 3);
  const below = leftover.filter(l => l.y < ref).slice(0, 3);

  // ---- where new contracts arrived, and where the plot cannot show it ------
  // newContracts (glance.js) finds the price areas the board's newest contracts
  // moved to. The window is solved from price, the session and the walls, and
  // the change is wherever contracts trade: on 2026-09-16, 6 of the day's 19
  // areas lay outside it, and at 15:10 both did (CHANGE-SPEC.md 9). Widening
  // the window to fetch one would flatten the tape, the trade MIN_RANGE_SHARE
  // refuses a far wall, so an area the plot cannot show, or shows less than 15%
  // of, is NAMED in the edge stack on its side, the way a refused wall is.
  const fresh = newContracts(st.strikes, st.frames);
  const lit = [];
  let unshown = fresh ? fresh.more : 0;
  for(const a of (fresh ? fresh.areas : [])){
    const lo = Math.max(a.lo, WIN.lo), hi = Math.min(a.hi, WIN.hi);
    if(hi - lo > 0 && hi - lo >= 0.15 * (a.hi - a.lo)){ lit.push({a, lo, hi}); continue; }
    const mid = (a.lo + a.hi) / 2, stack = mid > ref ? above : below;
    if(stack.some(l => l.kind === 'new')) unshown++;
    else stack.push({y:mid, kind:'new', area:a});
  }
  // With no box in the plot there is no word to count what got no mark of its
  // own, so the count rides the row of the biggest area off the plot instead.
  const rowsNew = above.concat(below).filter(l => l.kind === 'new');
  if(!lit.length && unshown && rowsNew.length)
    rowsNew.reduce((p, q) => (q.area.lift > p.area.lift ? q : p)).more = unshown;
  byPrice(above); byPrice(below);
  // Rows are 13px apart, and 16 either side of a row of new contracts: at 13
  // two 11px rows leave 2.88px of white where one row's comma meets the next
  // row's capitals, and 16 leaves 5.88 (CHANGE-SPEC.md 9). The 3px is for the
  // pair, so a row of new contracts alone on its side costs the plot 13, as a
  // wall's does, and the row nearest the plot keeps a wall row's 12 from it.
  const pitch = (a, b) => (a.kind === 'new' || b.kind === 'new') ? 16 : 13;
  const offsets = rows => rows.reduce((at, l, i) => at.concat(i ? at[i-1] + pitch(rows[i-1], l) : 0), []);
  const aboveAt = offsets(above), belowAt = offsets(below);
  const stackH = at => at.length ? 13 + at[at.length - 1] : 0;
  const PAD_T = 9 + stackH(aboveAt), PAD_B = 29 + stackH(belowAt);
  const RIB_B = SVGH - 19, RIB_T = RIB_B - 10;   // the volume ribbon's own band
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
  // No shade behind it since 2026-09-18. Eight bands of contracts share
  // resolved to five greys a reader could tell apart, and the one band that
  // read was on the put wall's strike, already ruled and tagged, on 24 of 24
  // scans of 2026-09-16 (INK-SPEC.md 1.2).
  let g = '';
  // WHERE CONTRACTS TRADED TODAY, as bars behind the price line, grown from the
  // plot's right edge: they sit beside "now", which is what a count up to now
  // is about, and the left edge is where a bar's end would hide under a word
  // (103 of 314 ends there against 2 of 314 here, ALT-BEHIND-SPEC.md 3.2).
  // First in the clip, so every other mark is on top of them.
  const traded = tradedBars(st.strikes, WIN.lo, WIN.hi, plotTop, plotBottom);
  const barX = b => PLOT_R - b.share * TRADED_FULL * PLOT_W;   // where a bar ends
  const late = traded ? tradedLately(st.strikes, st.frames) : null;
  if(traded){
    for(const b of traded.bars){
      const x = n1(barX(b)), y = b.y - traded.h / 2;
      g += '<rect class="p-traded" x="' + x + '" y="' + n1(y) + '" width="' + n1(PLOT_R - x)
         + '" height="' + n1(traded.h) + '"/>';
      // how it is changing: its outer end, a shade darker, is what traded here
      // in the last half hour, on the bar's own scale and never longer than
      // it. Under a pixel there is nothing to see, so nothing is drawn.
      const r = late ? late.by[b.v] : null;
      const lw = r > 0 && traded.most > 0 ? Math.min(r, b.n) / traded.most * TRADED_FULL * PLOT_W : 0;
      if(lw >= 1)
        g += '<rect class="p-tradedlate" x="' + x + '" y="' + n1(y) + '" width="' + n1(lw)
           + '" height="' + n1(traded.h) + '"/>';
      // the end, where the length is read; kept inside the plot so a strike
      // that traded nothing still shows a mark where an absent one shows none
      const ex = n1(Math.min(+x + 0.6, PLOT_R - 0.6));
      g += '<line class="p-tradedend" x1="' + ex + '" y1="' + n1(y) + '" x2="' + ex
         + '" y2="' + n1(y + traded.h) + '"/>';
    }
  }
  const orng = st.openRange;
  if(orng && inWin(orng.high) && inWin(orng.low)){
    for(const v of [orng.high, orng.low])
      g += '<line class="p-orb" x1="' + PLOT_L + '" y1="' + n1(yFor(v)) + '" x2="' + PLOT_R
         + '" y2="' + n1(yFor(v)) + '"/>';
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
  // Nothing rides on the line. A dot for each model call sat on it until
  // 2026-09-18, and 19 of the 22 on the 15:11 board of 09-16 were woken by
  // "price ran": the line itself. The reading below says when and why in words.
  //
  // The line runs over a card-coloured edge 1px wider on each side. On the bare
  // card it cannot be seen; where the line crosses a bar it cuts a channel, so
  // the line is read against the card (5.00:1) and not the bar's fill (3.64:1,
  // and 3.27:1 at a 2x screen's worst crossing, ALT-BEHIND-SPEC.md 4.2).
  if(pts.length >= 2){
    const line = pts.map(q => n1(xFor(q.t)) + ',' + n1(yFor(q.s))).join(' ');
    g += '<polyline class="p-casing" points="' + line + '"/>';
    g += '<polyline class="p-path" points="' + line + '"/>';
  }

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

  // ---- no words in the plot ----------------------------------------------
  // NO CALL WALL ABOVE / NO PUT WALL BELOW and the bracket down the plot's left
  // edge that scoped it came off on 2026-09-18, by the owner's choice. The
  // levels card below still gives a side measured empty its own row.

  // ---- rules -------------------------------------------------------------
  for(const l of levels){
    const y = n1(yFor(l.y));
    if(l.kind === 'magnet'){
      // A runner's weight is its WIDTH, 1.0-1.9px, under the lead's 2.2. It
      // was its opacity, down to .28, which measured 1.18:1 over the darkest
      // band of the old shade, and the opacities that clear 3:1 span too little
      // to see.
      o += '<line class="' + (l.lead ? 'p-mag' : 'p-magrun') + '" x1="' + PLOT_L + '" y1="' + y
         + '" x2="' + PLOT_R + '" y2="' + y + '"'
         + (l.lead ? '' : ' style="stroke-width:' + (1 + 0.9*(l.weight||0.5)).toFixed(2) + '"') + '/>';
    } else if(l.kind === 'wall'){
      // WHERE, not how much. The stroke was wallStroke(l.gex) until 2026-09-16:
      // thickness on the book-gamma denominator, which is the one number this
      // screen may not name in English. The width says only nearest or not, at
      // full opacity — the second wall at .55 measured 1.49:1 over the darkest
      // band of the old shade. A wall price has already passed keeps its width,
      // not its hue.
      //
      // A magnet on the wall's strike was folded into it by mergeLevels, and the
      // one rule then said nothing of it: it takes the magnet's long dash, so the
      // colour says which side and the dash says the busiest strike, on the one
      // row. They share a strike on 74% of 66 scans over 09-15..09-17
      // (INK-SPEC.md 1.4); on the rest each keeps a rule of its own.
      o += '<line class="p-wall ' + (wallPassed(l.side, l.y, ref) ? 'passed' : l.side)
         + (l.magnet ? ' mag' : '')
         + '" x1="' + PLOT_L + '" y1="' + y + '" x2="' + PLOT_R + '" y2="' + y
         + '" style="stroke-width:' + (l.nearest ? '2.0' : '1.2') + '"/>';
    }
  }

  // ---- price -------------------------------------------------------------
  if(!st.withdrawn && inWin(ref)){
    o += '<line class="p-prule" x1="' + PLOT_L + '" y1="' + n1(priceY) + '" x2="' + PLOT_R + '" y2="' + n1(priceY) + '"/>';
    o += '<circle class="p-halo" cx="' + n1(dotX) + '" cy="' + n1(priceY) + '" r="7"/>';
    o += '<circle class="p-dot"  cx="' + n1(dotX) + '" cy="' + n1(priceY) + '" r="3.6"/>';
  }

  // ---- the bugs: the two walls the card below names ----------------------
  // It named ONE wall until 2026-09-10, the nearer of the two, under a
  // direction word; the card now lists both, so both are marked.
  for(const side of ['call', 'put']){
    const e = ((sc.walls||{})[side] || [])[0];
    if(!e || e.strike == null || !inWin(Number(e.strike))) continue;
    const k = Number(e.strike), y = yFor(k);
    o += '<path class="p-bar ' + (wallPassed(side, k, ref) ? 'passed' : side) + '" d="M' + PLOT_R + ',' + n1(y)
       + ' L' + (PLOT_R+5) + ',' + n1(y-5) + ' L' + (PLOT_R+5) + ',' + n1(y+5) + ' Z" style="fill-opacity:1"/>';
  }

  // ---- where new contracts arrived, in the plot ---------------------------
  // Four corners round the area, a tab beside it in the gutter, and on the
  // biggest the word. Corners, not two lines across it: two full-width lines
  // read as a channel, and a channel promises price does something between them
  // (CHANGE-SPEC.md 5.2). The corners sit 4px inside the plot, off the wall
  // bugs, and a right-hand one that would land on the live dot's ring stops
  // short of it.
  const dot = !st.withdrawn && inWin(ref);
  const inks = levels.map(l => [yFor(l.y), (l.kind === 'wall' ? (l.nearest ? 2 : 1.2)
                                            : l.lead ? 2.2 : 1 + 0.9 * (l.weight || 0.5)) / 2]);
  if(dot) inks.push([priceY, 0.5]);
  if(orng && inWin(orng.high) && inWin(orng.low)) inks.push([yFor(orng.high), 0.5], [yFor(orng.low), 0.5]);
  // The arms keep off every rule's ink (newBox), on a plot taken 1px in from
  // its edge, not the half-stroke, so the tenth the coordinates are rounded to
  // cannot put the stroke past it.
  const boxes = lit.map(({a, lo, hi}) => {
    const {t, b} = newBox(yFor(hi), yFor(lo), inks, plotTop + 1, plotBottom - 1);
    return {a, t, b};
  });
  for(const {t, b} of boxes){
    for(const [x0, dx] of [[PLOT_L + 4, 1], [PLOT_R - 4, -1]]) for(const [y, dy] of [[t, 1], [b, -1]]){
      // the ring's ink runs 7.5 from the dot's centre; the corner, arm or leg,
      // keeps 2 more
      const onRing = dot && x0 - NEW_ARM < dotX + 10 && Math.min(y, y + dy * NEW_LEG) < priceY + 10
                   && Math.max(y, y + dy * NEW_LEG) > priceY - 10;
      const x = (dx < 0 && onRing) ? dotX - 10 : x0;
      o += '<path class="p-new" d="M' + n1(x) + ',' + n1(y + dy * NEW_LEG) + ' L' + n1(x) + ',' + n1(y)
         + ' L' + n1(x + dx * NEW_ARM) + ',' + n1(y) + '"/>';
    }
  }

  // ---- the count on the longest bar ---------------------------------------
  // The bars' one number, "7,456 TODAY": the whole day's, in the bars' grey,
  // where the brackets' words below say just now, in theirs. Their scale is
  // per scan, so a full-length bar was 7,456 contracts at 15:10 on 2026-09-16
  // and 3,264 at 11:01, and only this says which. It sits 4px past the bar's
  // end on the card side, where a bar chart puts its value, and inside the bar
  // at its root only if that would put it on the live dot's ring or off the
  // plot; the brackets' word below may move it inside, just past the end. The
  // busiest strike is usually one the chart already rules, so the count's card
  // halo cuts that rule for its width: a rule or a dash on 167 of the 188
  // boards of 09-16.
  let count = null;
  if(traded){
    const b = traded.bars.find(r => r.n === traded.most);
    // Centred on its bar: the figures' ink runs 8px above the baseline and a
    // comma 2.2 below it, so the baseline sits 0.36em under the bar's middle.
    // The ring is 7 round the dot, and 2 more keeps them apart.
    const num = gUsd(b.n, 0).replace('$',''), s = num + ' TODAY', w = figW(num, 11, 600) + COUNT_TODAY_W;
    const by = b.y + 0.36 * 11;
    const top = by - 8, bottom = by + 2.2, tip = barX(b);
    const clear = xe => xe - w >= PLOT_L + 2 && xe <= PLOT_R - 2
      && !(dot && xe > dotX - 9 && xe - w < dotX + 9 && top < priceY + 9 && bottom > priceY - 9);
    const xe = [tip - 4, PLOT_R - 4].find(clear);
    count = {s, w, by, top, bottom, tip, clear, x:xe != null ? xe : tip - 4};
  }

  // ---- the word ------------------------------------------------------------
  // One, on the biggest area the plot shows, counting any area it could not:
  // past the cap, or a second off the plot on a side whose edge row is taken.
  // It says what happened, that trading here just took a bigger share of the
  // board's than it had, and nothing about what price does next. It never
  // touches the count, a bar or the dot's ring (wordRow). Where the busiest
  // strike is the one that changed, the count and the word want one row, and
  // a count stacked under the word reads as the word's own number: the count
  // then moves inside its bar, past the end, if that clears the word by 12px,
  // and otherwise the word keeps its distance or leaves its box.
  let word = null;
  if(boxes.length){
    const box = boxes[0];
    const wx = PLOT_L + 6, ww = NEW_WORD + (unshown ? NEW_MORE : 0);
    const across = (x0, x1, air) => x0 < wx + ww + air && x1 > wx - air;
    // The count keeps 5.5px above or below the word, what the chart's closest
    // two labels keep, and 12 beside it, so it is not read as the word's next
    // line or its next figure. Bars, the ring and another box keep 2.
    const row = () => {
      const hard = [];
      if(count && across(count.x - count.w, count.x, 12)) hard.push([count.top - 5.5, count.bottom + 5.5]);
      for(const b of (traded ? traded.bars : []))
        if(across(barX(b), PLOT_R, 2)) hard.push([b.y - traded.h / 2 - 2, b.y + traded.h / 2 + 2]);
      if(dot && across(dotX - 9, dotX + 9, 2)) hard.push([priceY - 11, priceY + 11]);
      for(const x of boxes.slice(1)) hard.push([x.t - NEW_W / 2 - 2, x.t + NEW_W / 2 + 2], [x.b - NEW_W / 2 - 2, x.b + NEW_W / 2 + 2]);
      return wordRow(box.t, box.b, inks.map(([y, hw]) => [y - hw, y + hw]), hard, plotTop + 1, plotBottom - 1);
    };
    const inBox = y => y != null && y - WORD_UP >= box.t && y + WORD_DOWN <= box.b;
    const stacked = y => y != null && across(count.x - count.w, count.x, 12)
                      && y - WORD_UP < count.bottom + 12 && y + WORD_DOWN > count.top - 12;
    let by = row();
    const past = count ? count.tip + 4 + count.w : null;
    if(count && (!inBox(by) || stacked(by)) && count.x === count.tip - 4
       && !across(past - count.w, past, 12) && count.clear(past)){
      count.x = past;
      const moved = row();
      if(inBox(moved)) by = moved; else count.x = count.tip - 4;
    }
    if(by != null)
      word = '<text class="p-newword" x="' + wx + '" y="' + n1(by) + '">TRADING PICKED UP'
           + (unshown ? ' · ' + unshown + ' MORE' : '') + '</text>';
  }
  if(count)
    o += '<text class="p-tradednum" x="' + n1(count.x) + '" y="' + n1(count.by) + '">' + count.s + '</text>';
  if(word) o += word;

  // ---- tag rows, solved once for every member including the price chip ---
  const members = [];
  for(const l of levels){
    // The never-drop set is exactly six (chip, both nearest, both
    // heaviest_behind, the lead magnet), which is what the cap of 7 was sized
    // for. walls.*[1] is the one wall that may drop, and VWAP drops before it.
    // Without the tier, a cap overflow could drop a heaviest_behind and leave
    // the thickest stroke on the plot with its price nowhere on screen.
    if(l.kind === 'wall')
      members.push({y:l.y, cls:'p-tag ' + (wallPassed(l.side, l.y, ref) ? 'passed' : l.side),
                    lvl:l, keep:(l.nearest || l.behind) ? 2 : 1});
    else if(l.kind === 'magnet' && l.lead) members.push({y:l.y, cls:'p-tag mag', lvl:l, keep:2});
  }
  if(!st.withdrawn && inWin(ref)) members.push({y:ref, chip:true, keep:3});
  members.sort((a,b) => (b.keep - a.keep) || (a.y - b.y));
  const kept = members.slice(0, 7).sort((a,b) => a.y - b.y);
  const rows = layoutLabels(kept.map(m => yFor(m.y)), 20, plotTop + 10, plotBottom - 10);

  // The tab beside each area, in the mark column, first in the gutter so a
  // diamond and a rung's tick draw whole over it. It stops 3px short of the
  // price chip, which starts in the same column: run into the chip it read as
  // the chip's stem. A piece shorter than it is wide is left out rather than
  // drawn as a dot, so where the chip covers the whole area the corners carry
  // it alone.
  const chip = kept.findIndex(m => m.chip);
  const tab = (y0, y1) => (y1 - y0 >= 4
    ? '<rect class="p-newtab" x="' + MARK_L + '" y="' + n1(y0) + '" width="4" height="' + n1(y1 - y0) + '"/>' : '');
  for(const {t, b} of boxes){
    const a = chip < 0 ? b : Math.max(t, rows[chip] - 12), z = chip < 0 ? b : Math.min(b, rows[chip] + 12);
    o += z > a ? tab(t, a) + tab(z, b) : tab(t, b);
  }

  // ---- the price ruler, in the gutter's silences -------------------------
  // The gutter is already a column of prices, so the scale goes into it rather
  // than down a second column on the left: the named levels in their hue at
  // 12px, the round prices between them in small grey. Drawn before the tags
  // so a tag paints over a rung if the clearance rule were ever wrong. The
  // tick sits in the diamond's column; a label right-aligned at TAG_R starts
  // some 23px from the plot, and the tick is what ties it to its height.
  const tagRows = kept.map((m, i) => ({v:m.y, row:rows[i]}));
  for(const t of priceTicks(WIN.lo, WIN.hi, plotTop, plotBottom, tagRows)){
    o += '<line class="p-stick" x1="' + MARK_L + '" y1="' + n1(t.y) + '" x2="' + (MARK_L+4) + '" y2="' + n1(t.y) + '"/>';
    o += '<text class="p-scale" x="' + TAG_R + '" y="' + n1(t.y+3.5) + '">' + t.label + '</text>';
  }

  kept.forEach((m, i) => {
    const trueY = yFor(m.y), rowY = rows[i];
    if(Math.abs(rowY - trueY) > 2)
      o += '<path class="p-tie" d="M' + (PLOT_R+1) + ',' + n1(trueY) + ' L' + (MARK_L-1) + ',' + n1(rowY) + '"/>';
    if(m.chip){
      // the gutter's full width, right edge shared with the tags
      o += '<rect class="p-chip" x="' + MARK_L + '" y="' + n1(rowY-9) + '" width="' + CHIP_W + '" height="18" rx="2"/>';
      o += '<text class="p-chiptx" x="' + (TAG_R-5) + '" y="' + n1(rowY+4.5) + '">'
         + gUsd(m.y, 0).replace('$','') + '</text>';
      return;
    }
    const l = m.lvl;
    if(l && (l.magnet || l.kind === 'magnet'))
      o += '<rect class="p-diamond" x="' + MARK_L + '" y="' + n1(rowY-2.5)
         + '" width="5" height="5" transform="rotate(45 ' + (MARK_L+2.5) + ' ' + n1(rowY) + ')"/>';
    const lead = (l && l.kind === 'wall' && l.nearest) ? ' lead' : '';
    o += '<text class="' + m.cls + lead + '" x="' + TAG_R + '" y="' + n1(rowY+4.5) + '">'
       + gUsd(m.y, 0).replace('$','') + '</text>';
  });

  // ---- refused levels are NAMED, never silently dropped -------------------
  // When a wall the card names is itself off-window the bug triangle cannot
  // point at it, so its marker carries the weight instead. Matched on kind as
  // well as the nearest flag, so an exiled magnet on the same strike cannot
  // steal the emphasis. A pick-up the plot cannot show takes a plain arrow
  // where a level takes a solid one, and the brackets' own words in their ink
  // (pickedRow), so the row says it is the same thing, off the chart.
  const namedEdge = l => l.kind === 'wall' && !!l.nearest;
  const edgeCls = l => 'p-edge' + (namedEdge(l) ? ' lead' : '');
  const edgeText = (l, up) => {
    if(l.kind !== 'new')
      return (up ? '▲ ' : '▼ ') + gUsd(l.y,0).replace('$','') + (l.behind ? ' BIGGEST PILE' : '');
    const r = pickedRow(up, l.area.strikes, l.more, TAG_R - 1);
    return r.lead + ': <tspan class="p-edgeword">TRADING PICKED UP</tspan>' + r.tail;
  };
  above.forEach((l, i) => {
    o += '<text class="' + edgeCls(l) + '" x="' + TAG_R + '" y="' + (10 + aboveAt[i]) + '">'
       + edgeText(l, true) + '</text>';
  });
  below.forEach((l, i) => {
    o += '<text class="' + edgeCls(l) + '" x="' + TAG_R + '" y="' + n1(plotBottom + 12 + belowAt[i]) + '">'
       + edgeText(l, false) + '</text>';
  });

  // ---- how busy each stretch was -----------------------------------------
  // Whole five-minute blocks on a scale fixed across sessions, so one height is
  // one fact on every day. The x-domain is the path's own, or a block would sit
  // over a minute it does not describe.
  let strip = false;
  for(const b of st.vol){
    const x0 = xFor(b.t0), x1 = xFor(b.t1);
    if(!(x1 > x0) || x1 < PLOT_L || x0 > PATH_R) continue;
    strip = true;
    const h = b.weight * 10;
    o += '<rect class="p-vol" x="' + n1(x0) + '" y="' + n1(RIB_B - h)
       + '" width="' + n1(Math.max(1, x1 - x0 - 1)) + '" height="' + n1(h) + '"/>';
    if(b.capped)
      o += '<rect class="p-clip" x="' + n1(x0) + '" y="' + RIB_T + '" width="'
         + n1(Math.max(1, x1 - x0 - 1)) + '" height="1.4"/>';
  }

  // ---- axis feet ---------------------------------------------------------
  const c = sc.clock || {};
  const leftFoot = pts.length ? etTime(pts[0].t) : '';
  if(leftFoot)
    o += '<text class="p-axis" x="' + PLOT_L + '" y="' + (SVGH-5) + '">' + leftFoot + '</text>';
  // a countdown computed at scan time is a lie when read hours later, and it is
  // the one label on the plot that ages silently
  let rightFoot = '';
  if(st.stale){
    if(scanAt) rightFoot = scanAt;
  } else if(c.minutes_to_close != null){
    rightFoot = c.minutes_to_close > 0 ? (gMinutes(c.minutes_to_close) + ' left').toUpperCase() : 'CLOSED';
  }
  if(rightFoot)
    o += '<text class="p-axis" x="' + PLOT_R + '" y="' + (SVGH-5) + '" text-anchor="end">' + esc(rightFoot) + '</text>';
  // The strip's name, centred in the room the two feet leave on their row, by
  // their measured widths; no strip drawn, no name
  if(strip && leftFoot){
    const L = PLOT_L + axisW(leftFoot), R = PLOT_R - (rightFoot ? axisW(rightFoot) : 0);
    const name = stripName(R - L);
    if(name)
      o += '<text class="p-axis p-volname" x="' + n1((L + R) / 2) + '" y="' + (SVGH-5)
         + '" text-anchor="middle">' + name + '</text>';
  }

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

/* ---- D. the three levels — where the weight sits, never what price does -- */
//
// REPLACED 2026-09-10. The card used to name ONE wall under "▲ NEXT ABOVE" or
// "▼ NEXT BELOW" with a dealer sentence beside it. Three things were wrong with
// it, all measured:
//   - the direction came from the live price and the wall from a book up to
//     minutes old, so the two could disagree on screen;
//   - the sentence ("Dealers sell the rallies here — it caps the move") is the
//     claim the model is forbidden to make and SNDK's record does not support;
//   - it could only ever show one of the two walls.
// Now: both nearest walls with their weight, the strike with the most
// contracts as a COUNT, ordered by price, and no direction word anywhere.
//
// Every number here is set with textContent. None of it is model output, but
// the card is built from payload strings and there is no reason to let any of
// them near innerHTML.

function lvRow(r){
  const row = document.createElement('div');
  const side = r.kind === 'absent' ? r.side : (r.passed ? 'passed' : r.side === 'most' ? 'mag' : r.side);
  row.className = 'lv ' + side + (r.kind === 'absent' ? ' absent' : '');

  const tags = [];
  if(r.side === 'call') tags.push('Call wall');
  else if(r.side === 'put') tags.push('Put wall');
  if(r.side === 'most' || r.most) tags.push('Most contracts');
  if(r.heaviest) tags.push('Heaviest');
  if(r.passed) tags.push('Price passed it');
  const lab = document.createElement('span');
  lab.className = 'lv-side';
  lab.textContent = tags.join(' · ');
  row.appendChild(lab);

  if(r.kind === 'absent'){
    // a measured emptiness is a finding and is DRAWN, never left blank
    const t = document.createElement('span');
    t.className = 'lv-none';
    t.textContent = r.text;
    row.appendChild(t);
    return row;
  }

  const k = document.createElement('span');
  k.className = 'lv-k';
  k.textContent = gUsd(r.strike, 0).replace('$', '');
  row.appendChild(k);

  if(r.kind === 'wall'){
    const pct = shareBarPct(r.share);
    const bar = document.createElement('span');
    const v = document.createElement('span');
    v.className = 'lv-v';
    if(pct != null){
      bar.className = 'lv-bar';
      const fill = document.createElement('i');
      fill.style.width = pct.toFixed(1) + '%';
      bar.appendChild(fill);
      v.textContent = r.share.toFixed(1) + '%';
    } else {
      // no share: no bar AND no track — an empty track reads as zero
      bar.className = 'lv-bar none';
      v.textContent = '';
    }
    row.appendChild(bar);
    row.appendChild(v);
  }
  // A COUNT, never a bar. The most-contracts strike is chosen by contracts,
  // and a gamma bar beside it measured something that did not choose it: on
  // 66.9% of replayed scans it was not even the heaviest gamma strike nearby.
  if(r.most){
    const n = document.createElement('span');
    n.className = 'lv-n';
    n.textContent = r.most.count != null
      ? r.most.count.toLocaleString('en-US') + ' contracts'
      : 'Count not measured';
    row.appendChild(n);
  }
  return row;
}

function paintLevels(st){
  const sc = st.scene, walls = sc.walls || null;
  const ref = st.ref ? st.ref.v : null;
  const lv = PAY.levels || {};
  // the count rides the display wrapper; the strike itself is the scene's own
  // magnet, so the card and the chart can never name two different strikes
  const top = ((sc.magnet || {}).top_strikes || [])[0];
  let most = lv.most_contracts || null;
  if(!most && top && top.strike != null) most = {strike: top.strike};
  const rows = levelRows(walls, most, lv.heaviest || null, ref);

  const box = $('lvRows');
  box.replaceChildren(...rows.map(lvRow));

  // The levels are the BOOK's, whatever the quote is doing, so a stale book
  // says which scan it came from rather than letting a fresh price vouch for it.
  let when = '';
  if(st.stale){
    const et = etTime(Date.parse(PAY.row_ts));
    if(et) when = 'At the ' + et + ' scan';
  }
  $('lvWhen').textContent = when;
  paintSheet(rows, most, lv);
}

/* ---- G. the explainer sheet --------------------------------------------- */
//
// What each level IS, in plain words — and, as carefully, what it is not. The
// first draft of this text was the textbook: dealers buy dips and sell rallies
// near the strike, price gets pinned late in the day, a wall is where price
// bounces or breaks. A fact-check against the station's own findings found
// every one of those measured false on SNDK (docs/sndk-plan.md, "Closed by
// measurement"), so the sheet says where the weight is and stops there.

function paintSheet(rows, most, lv){
  const set = (id, t) => { $(id).textContent = t || ''; };
  const k = side => { const r = rows.find(x => x.side === side || (side === 'most' && x.most));
                      return r && r.strike != null ? gUsd(r.strike, 0).replace('$', '') : ''; };
  set('shCall', k('call')); set('shPut', k('put')); set('shMost', k('most'));

  const win = most && most.window_dollars != null ? Math.round(most.window_dollars) : null;
  set('shWindow', win != null ? ', about $' + win + ' either side right now' : '');

  const note = lightNote(lv);
  $('shLight').hidden = !note;
  if(note){
    const f = v => gUsd(v, 0).replace('$', '');
    // "the put wall at 1,650 carries" / "a put wall further out, at 1,600, carries"
    const who = note.further ? 'a ' + note.side + ' wall further out, at ' + f(note.heavy) + ','
                             : 'the ' + note.side + ' wall at ' + f(note.heavy);
    let s = 'Right now ' + who + ' carries the most gamma, '
          + note.share.toFixed(1) + '% of the board. ' + f(note.strike) + ' has the most contracts';
    if(note.count != null){
      s += ', ' + note.count.toLocaleString('en-US');
      if(note.traded != null) s += ', and ' + note.traded.toLocaleString('en-US') + ' of them traded today';
    }
    set('shLightNow', s + '.');
  }
}

// PRESS, HOLD, LET GO. The one gesture on this card, and the one exception to
// "the glance is not a control" (see test_the_glance_itself_is_not_a_control).
// It opens an explanation of the card; nothing on the card changes by touching
// it.
//
// THE SHEET OPENS WHEN THE FINGER LIFTS, never while it is down (2026-09-10).
// The first build opened on the 450ms timer, under a finger still on the glass,
// and the rest of that one touch then belonged to a sheet that had not existed
// when it began:
//   - ~50ms later Android's own long-press fired on the SHEET's text and began
//     a text selection, so the drag that followed extended a selection instead
//     of scrolling — the card read as a dead zone, and the only place a swipe
//     still scrolled was the sliver of screen above it;
//   - any slow start to a scroll (under 10px in the first 450ms) fell into it;
//   - a lift before the platform's long-press timeout counted as a TAP, which
//     Chrome hit-tests after the handlers have run — on the backdrop now under
//     the finger, which closed the sheet the instant it opened;
//   - and the Back entry was pushed from a timer rather than a gesture, which
//     Chrome may skip, so Back could leave the page instead of the sheet.
// Now the 450ms only ARMS it: the bar along the card's foot completes and the
// phone ticks. Moving at any point cancels it and the gesture stays a scroll.
// Lifting an armed hold opens the sheet, from the touchend itself, with that
// touchend's tap cancelled.
//
// It opens a SHEET, not a tooltip: the text runs to several paragraphs, and a
// tooltip that lives only while a finger is down asks you to read with your
// thumb over the screen. What the sheet does once it is open — its history
// entry, the shell bridge, the one way it closes — is sheet.js's, shared with
// the reads page's sheet. Only the gesture that opens this one is here.
(function(){
  const HOLD_MS = 450, SLOP = 10;
  let t = 0, x0 = 0, y0 = 0, card = null, armed = false;

  function tick(){
    try {
      if(window.MiraiShell && typeof MiraiShell.tick === 'function') MiraiShell.tick();
      else if(navigator.vibrate) navigator.vibrate(12);
    } catch(e){ /* a phone that will not buzz must not stop the sheet */ }
  }

  function cancel(){
    if(t){ clearTimeout(t); t = 0; }
    if(card){ card.classList.remove('holding', 'armed'); card = null; }
    armed = false;
  }
  function release(e){
    const held = card && armed ? card : null;
    cancel();
    if(!held) return;
    // an uncancelled touchend becomes the tap described above
    if(e.type === 'touchend' && e.cancelable) e.preventDefault();
    MiraiSheet.open(held);
  }
  document.addEventListener('pointerdown', e => {
    const c = e.target.closest && e.target.closest('[data-hold]');
    if(!c || !e.isPrimary || MiraiSheet.isOpen()) return;
    cancel();
    card = c; x0 = e.clientX; y0 = e.clientY;
    c.classList.add('holding');
    t = setTimeout(() => { t = 0; armed = true; if(card){ card.classList.add('armed'); tick(); } }, HOLD_MS);
  });
  const drifted = (x, y) => Math.abs(x - x0) > SLOP || Math.abs(y - y0) > SLOP;
  document.addEventListener('pointermove', e => { if(card && drifted(e.clientX, e.clientY)) cancel(); });
  // Touch events keep flowing after the browser has claimed a gesture and sent
  // pointercancel, so a thumb that moved is caught here whatever the browser
  // decided — including at the very bottom of the page, where a drag scrolls
  // nothing and whether a pointercancel arrives at all is a browser detail.
  document.addEventListener('touchmove', e => {
    const p = e.touches && e.touches[0];
    if(card && p && drifted(p.clientX, p.clientY)) cancel();
  }, {passive: true});
  // A finger lets go with touchend, which arrives even when a long-press has
  // made the browser send pointercancel first; a mouse or pen with pointerup.
  // Non-passive so the tap can be cancelled — a touchend listener never delays
  // scrolling, only touchstart and touchmove can.
  document.addEventListener('touchend', release, {passive: false});
  document.addEventListener('pointerup', e => { if(e.pointerType !== 'touch') release(e); });
  document.addEventListener('touchcancel', cancel);
  // before the hold arms, a pointercancel means the browser took it for a pan
  document.addEventListener('pointercancel', () => { if(!armed) cancel(); });
  window.addEventListener('scroll', cancel, {passive: true});
  // or a real phone opens its own long-press menu on the card (sheet.js refuses
  // it on the sheet's text)
  document.addEventListener('contextmenu', e => {
    if(e.target.closest && e.target.closest('[data-hold]')) e.preventDefault();
  });
  // a hold is not something a keyboard can do
  document.addEventListener('keydown', e => {
    const c = document.activeElement;
    if((e.key === 'Enter' || e.key === ' ') && c && c.hasAttribute && c.hasAttribute('data-hold')){
      e.preventDefault(); MiraiSheet.open(c);
    }
  });
})();

// THE CHART'S KEY opens on a tap. The link under the chart is a control and
// looks like one, as the reads page's button does, so a plain tap opens it and
// the levels card keeps the page's one press-and-hold. The sheet it opens is
// the one its aria-controls names; the rest is sheet.js's.
$('howto').addEventListener('click', () => MiraiSheet.open($('howto')));

/* ---- E. read — an opinion, not a measurement ---------------------------- */

/* ---- D2. today — where the activity is, and what changed ----------------

   EVERY LINE IS A FACT THE BUILDER ALREADY WROTE. The `day` block is computed
   on the mini from the whole session and graded against the same board the
   sentence below it was written from, so nothing is derived here: the panel
   cannot drift from the words underneath. When the block is absent — an older
   payload, or the session's first look — the panel says so rather than
   printing a shape with nothing in it (law 1, honest-absent). */

function acEl(cls, text){
  const e = document.createElement('div');
  e.className = cls;
  if(text != null) e.textContent = text;      // textContent only: nothing here writes markup
  return e;
}

function todayHeadline(day){
  // The one sentence, unchanged. Every word of it is a fact the builder wrote.
  if(!day || !Object.keys(day).length) return 'Not measured yet this session.';
  const runs = (day.leaders || {}).contracts || [];
  const now = runs.length ? runs[runs.length - 1] : null;
  if(!now) return 'The session has not settled on a busiest strike yet.';
  const k = gUsd(now[0], 0).replace('$','');
  return runs.length > 1
    ? 'The busiest strike keeps changing hands. ' + k + ' has held it since ' + now[1] + '.'
    : k + ' has been the busiest strike since ' + now[1] + '.';
}

const AC_WORD = {new: 'got busy', held: 'busy all day', gone: 'went quiet'};

// The head row holds ONE head. The pace column's and the multiple's side by side
// are 298.78px of the 152.03 the row has, and the pace word needs its subject
// over it more — without "trading", "slower" beside a strike price reads as the
// price. So the multiple's unit, "× what was already there", is not on the card.
// OPEN FOR THE OWNER (RATE-SPEC 11.3): this line is the whole of that choice, and
// {text: '× what was already there', w: 138.14, heads: r => r.mult != null} puts
// it back. `w` is its width at 12px/400 in the shipped face, which decides where
// activityGrid can put it; `heads` says which rows have something under it.
const AC_HEAD = {text: 'trading now against earlier', w: 148.62, heads: r => !!r.pace && !r.thin};

function acRow(r, track){
  // <div class="ac-row new"><div class="ac-k">1,630</div><div class="ac-dot"></div>
  //  <div class="ac-word">got busy</div><div class="ac-m">4.8×</div>
  //  <div class="ac-gauge" style="width:46px"><i style="width:96.6%"></i></div></div>
  // Honest-absent holds on the row too: no word on a continuation, no time
  // element at all where the builder recorded none, and no multiple, gauge or
  // track where the scan measured nothing — an empty track reads as zero. A
  // `track` of null is a card too narrow for the gauge, and the multiple says
  // it alone.
  const row = acEl('ac-row ' + r.state);
  row.appendChild(acEl('ac-k', gUsd(r.y, 0).replace('$','')));
  row.appendChild(acEl('ac-dot'));
  if(r.first) row.appendChild(acEl('ac-word', AC_WORD[r.state]));
  if(r.at) row.appendChild(acEl('ac-time', r.at));
  else if(r.mult != null){
    row.appendChild(acEl('ac-m', gTimes(r.mult)));
    if(track){
      const bar = turnoverBar(r.mult);
      const g = acEl('ac-gauge' + (bar.over ? ' over' : ''));
      g.style.width = track + 'px';
      const fill = document.createElement('i');
      fill.style.width = bar.pct.toFixed(1) + '%';
      g.appendChild(fill);
      row.appendChild(g);
    }
    // One cell, one thing, and a warning outranks a rate: under 500 contracts
    // standing the multiple is mostly the smallness of the pile, and that is
    // the headline. Otherwise faster / steady / slower, or nothing at all when
    // the series cannot carry a word — no element, not an empty one.
    const last = r.thin ? 'small pile' : r.pace;
    if(last) row.appendChild(acEl('ac-tr', last));
  }
  return row;
}

function acMore(n, word, extra){
  // the two count rows carry nothing in the fourth column, so the column's head
  // rides in the top one and the gauge's scale in the bottom one, at no height
  const row = acEl('ac-row more');
  if(n){
    row.appendChild(acEl('ac-more', '+' + n));
    row.appendChild(acEl('ac-word', word));
  }
  if(extra) row.appendChild(extra);
  return row;
}

function acScale(track){
  // 1× under the tick every bar is read against, the full scale under the
  // track's end. The zero is the track's own left end, on every row, and is not
  // numbered: at 11px a "0" centred on it overlaps the "1×". The scale is the
  // track's length, so each label stays on its mark however long that is.
  const s = acEl('ac-scale');
  s.style.width = track + 'px';
  s.appendChild(acEl('one', '1×'));
  s.appendChild(acEl('full', FULL_TURNOVER + '×'));
  return s;
}

function todayNotes(day, map){
  // -> [[kind, text], ...]. `said` is the model's own track record — what it
  // named that has since gone, and what it claimed that no longer holds. `board`
  // is what the market did. The two are marked differently because one is the
  // instrument reporting on itself and the other is the instrument reporting on
  // the world, and a reader owes them different weight.
  //
  // Everything the old label/value rows carried that the ladder does not is
  // kept here rather than dropped. A note with no datum behind it is not
  // written at all.
  const said = [], board = [];

  // GROUPED BY WHAT HAPPENED, not one line each. The list grows through the
  // session — five by midday on 2026-09-17 — and a line apiece would push the
  // notes past the ladder they are a footnote to. Grouping keeps every strike
  // named and bounds the block at two lines; the clock survives only where
  // there is one of them, because the strike is the fact and the time the detail.
  const gone = namedGone(day), fmt = y => gUsd(y, 0).replace('$','');
  const list = ys => ys.length > 1
    ? ys.slice(0, -1).map(fmt).join(', ') + ' and ' + fmt(ys[ys.length-1])
    : fmt(ys[0]);
  for(const [inBook, tail] of [[true, 'off the list.'], [false, 'out of the book.']]){
    const g = gone.filter(n => n.inBook === inBook);
    if(!g.length) continue;
    said.push(g.length === 1 && g[0].at
      ? list(g.map(n => n.y)) + ' was named at ' + g[0].at + ' and is now ' + tail
      : list(g.map(n => n.y)) + ' were named earlier and are now ' + tail);
  }
  let moved = 0;
  for(const g of ((day || {}).earlier_claims || [])) for(const cl of (g.claims || []))
    if(cl.now === 'changed' || cl.now === 'off_list') moved++;
  // Not "call" and "holds": a reader takes the one for a contract and the
  // other for a position before either reads as a claim that stopped being true.
  if(moved) said.push(moved === 1 ? 'One thing it said earlier no longer applies.'
                                  : String(moved) + ' things it said earlier no longer apply.');

  const vol = ((day || {}).leaders || {}).volume || [];
  const con = ((day || {}).leaders || {}).contracts || [];
  const v = vol.length ? vol[vol.length - 1] : null, c = con.length ? con[con.length - 1] : null;
  if(v && (!c || v[0] !== c[0]))
    board.push(gUsd(v[0], 0).replace('$','') + ' has traded the most since ' + v[1] + '.');
  const pace = (day || {}).volume_in_reach_vs_same_time_prior_sessions;
  if(typeof pace === 'number' && isFinite(pace)){
    const word = pace >= 1.25 ? 'busier than usual' : (pace <= 0.8 ? 'quieter than usual' : 'about usual');
    // Today against the same clock minute of recent sessions, which "for this
    // hour" did not say.
    board.push('Trading is ' + word + ' for this time of day.');
  }
  return said.map(t => ['said', t]).concat(board.map(t => ['board', t]));
}

function paintToday(){
  const sc = ((PAY || {}).scene) || {};
  const day = sc.day;
  $('tdLine').textContent = todayHeadline(day);
  const from = (day || {}).lists_from;
  $('tdWhen').textContent = from ? ('SINCE ' + from) : '';

  // replaceChildren, the same idiom paintLevels uses. A clear loop written
  // against firstChild/removeChild is a silent no-op in the stand-in DOM the
  // phone tests run in, and the rows double on the second paint — which is
  // every poll.
  const map = activityRows(day, (sc.price || {}).live_spot, 5, sc.strikes,
                           (sc.frames || {}).book_times);
  // the headline runs the card's whole measure, so its box is the ladder's width
  const grid = activityGrid($('tdLine').getBoundingClientRect().width, AC_HEAD.w);
  const above = [], below = [], px = [];
  let lift = false;
  if(map){
    // a head or a scale over a column with nothing in it would label an absence.
    // A word under no head is worse: "faster" beside a strike price, with no
    // "trading" over it, reads as the price. So the head keeps a row of its own
    // on a ladder with no count above price, and costs 18px there. On a card
    // too narrow to hold it beside the count, it takes a row of its own out of
    // the 18px above the ladder, and the card keeps its height.
    const rows = map.above.concat(map.below);
    const where = map.moreAbove ? grid.head : 'alone';
    const head = rows.some(AC_HEAD.heads) ? acEl('ac-head ' + where, AC_HEAD.text) : null;
    lift = !!(head && map.moreAbove && where === 'alone');
    if(lift) above.push(acMore(0, null, head));
    if(map.moreAbove || head) above.push(acMore(map.moreAbove, 'further above', lift ? null : head));
    for(const r of map.above) above.push(acRow(r, grid.track));
    for(const r of map.below) below.push(acRow(r, grid.track));
    const scale = grid.track && rows.some(r => r.mult != null) ? acScale(grid.track) : null;
    if(map.moreBelow) below.push(acMore(map.moreBelow, 'further below', scale));
    const v = shownPrice(sc, LIVE);
    if(v){
      const chip = acEl('ac-chip');
      const s = gUsd(v.v).replace('$','');          // "1,608.20"
      const dot = s.lastIndexOf('.');
      chip.appendChild(acEl('int', dot > 0 ? s.slice(0, dot) : s));
      if(dot > 0) chip.appendChild(acEl('', s.slice(dot)));
      px.push(chip, acEl('ac-now', 'Price now'));
    }
  }
  // both halves share one template, so the columns run straight through the price
  const cols = grid.cols.map(c => c + 'px').join(' ') + ' auto';
  $('acAbove').style.gridTemplateColumns = cols;
  $('acBelow').style.gridTemplateColumns = cols;
  $('acAbove').classList.toggle('lift', lift);
  $('acAbove').replaceChildren(...above);
  $('acPx').replaceChildren(...px);
  $('acBelow').replaceChildren(...below);
  $('acNote').replaceChildren(...todayNotes(day, map).map(([k, t]) => acEl('ac-n ' + k, t)));
}

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
    line.textContent = 'No reading yet today.';
    line.className = 'rd-line wordless';
    return;
  }
  // obs-1 removed the 'expired' tier. It hid a reading past 120 minutes, which
  // was four times a 30-minute forecast horizon; an observation has no horizon,
  // so age is shown rather than used to blank the text. "No reading yet
  // today." above is a different thing and stays: that is absence, not age.
  const a = gMinutes(m.ageMin);
  age.textContent = a === 'just now' ? 'JUST NOW' : (a + ' ago').toUpperCase();
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
    ? lead + 'No note written yet — ' + m.line
    : lead + m.line;
  line.className = 'rd-line' + (m.tier === 'aged' ? ' aged' : '')
                             + (m.wordless ? ' wordless' : '');
}

/* ---- E2. the last half hour, and apart from it the record ---------------

   Two stanzas of one shape, each a head with its scope on the right and then
   what it says, so the division needs no sentence: SINCE 14:40 is a moment and
   33 TRADING DAYS is a span. The words and counts are halfHour's (glance.js);
   this only places them, with textContent. */

function paintHalf(st){
  // st.scene, the scene the day's-move row comes from, not the Strikes
  // Payload: the move was divided by the diary row's sigma, which is this
  // scene's one_sigma_dollars, so the same number turns it back into dollars.
  // The Strikes Payload clamps its ruler to the day's anchor on an expiry
  // afternoon, and there it would not.
  const c = halfHour(st.scene, PAY.earlier_half_hours);
  $('hh').hidden = !c;
  if(!c) return;
  const el = (tag, text) => { const e = document.createElement(tag); e.textContent = text; return e; };
  $('hhSince').textContent = 'Since ' + c.since;
  $('hhSay').replaceChildren(...c.say.map(t => el('span', t)));
  $('hhSpan').textContent = c.span;
  $('hhSet').replaceChildren(...c.set.map(t => el('span', t)));
  // The whole bar is the n in the sentence above it and each segment one count,
  // so the two differ in length and nothing else. A count of zero draws no
  // segment: the floor in the stylesheet would give it 8px of bar for nothing.
  $('hhBar').replaceChildren(...c.out.filter(o => o.n > 0).map(o => {
    const seg = document.createElement('i');
    seg.style.flexGrow = String(o.n);
    return seg;
  }));
  // the space before the words is part of the text: without it the count
  // closes up on its first word and reads as part of it
  $('hhOut').replaceChildren(...c.out.map(o => {
    const cap = document.createElement('span');
    cap.appendChild(el('b', o.n.toLocaleString('en-US')));
    cap.appendChild(el('span', ' ' + o.words));
    return cap;
  }));
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
