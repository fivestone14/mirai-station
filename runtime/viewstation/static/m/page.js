/* page.js — fetch, lay out, paint. All reasoning lives in glance.js.
 *
 * REPAINT DISCIPLINE. The payload tick (60s) recomputes the window and rebuilds
 * everything. The quote tick (5s) rebuilds the SVG against the CACHED window,
 * so the geometry is bit-identical and exactly one mark has moved. A glance is
 * then a comparison against the last one rather than a fresh read.
 */

const USER = new URLSearchParams(location.search).get('user') || 'will';
const $ = id => document.getElementById(id);

let PAY = null, LIVE = null, DIARY = [], READS = [], BARS = [], WIN = null, CHART = null, LADDER_H = 280;
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
  // 280 since 2026-09-19, by the owner's choice, and it is the knee.
  //
  // SVGH is LADDER_H-1, and PAD_T/PAD_B are 9 and 29 plus 13 per edge marker
  // (the 29 holds the volume ribbon and the axis feet), so the plot is 241px
  // with no edge markers, 228 with one, 215 with two on one side. It was 196,
  // where those were 157, 144 and 131 — the range in which the wall rules
  // stay separable and the price path keeps its shape, but too little for the
  // marks the chart gained since. Bar thickness and row pitch are HEIGHTS
  // (tradedBars caps a bar at TRADED_H_MAX and takes TRADED_PITCH of the
  // tightest pitch in view), so only this number moves them; bar LENGTHS are
  // widths, set by the phone's 360, and no height helps them — the full-screen
  // view writes their numbers in instead (ZOOM-SPEC.md 1, 4).
  //
  // Swept over the 514 stored boards of 2026-09-15..17 at the owner's 360px:
  // a bar was 5.9px thick at the median (p10 5.0) and at its 8px cap on 20% of
  // them, and the tightest two rows of bars 8.5px apart (p10 7.1). At 280 a
  // bar is 8.0 at the median AND at the p10, at the cap on 91%, and the
  // tightest rows are 13.4 apart (p10 11.8). Past 280 only white space grows:
  // at 300 the bars are already capped on 98% and nothing but the pitch moves.
  //
  // It costs 84px of scroll, all of it below the chart — the card ends at 493
  // instead of 409 on a 780px screen, so "Where the activity is" shows its
  // title and first line rather than its first rows. The reading was already
  // below the fold.
  //
  // A row of new contracts sits 16 from its neighbour, not 13 (paintLadder),
  // so on the 15:10 board of 2026-09-16 two of them take the plot from 228 to
  // 199. The height is budgeted and the width is not, so when the chart needs
  // room sideways it still comes from the bleed and the derived gutter.
  LADDER_H = 280;
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
    // what traded at each strike since the reading the card below shows
    since: tradedSince(PAY.since_read, READS, ((PAY.scene || {}).strikes) || null),
    // the day the pile was struck at: the PRIOR SESSION's close, never "last
    // night" — one session in five follows a weekend or a holiday
    // (sndk_board's own note on oi_calls). Off the strikes payload, which is
    // the document the rows themselves come from.
    pileDate: ((((PAY.scene || {}).data_sources || {}).open_interest || {}).prior_session_date) || null,
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
  paintToday();
  paintRead();
  paintHalf(st);
  paintFoot(st);
  // the full screen chart is the same board, so a tick that moves the glance
  // moves it too — never a frozen copy of a scan the page has left behind
  if(chartIsOpen()) paintChart();
  chartRepainted();
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

function paintLadder(st, T){
  // T IS THE SAME CHART SOMEWHERE ELSE (2026-09-19, ZOOM-SPEC.md 5): the full
  // screen view hands in its own <svg>, its own size and the room its bars may
  // take, and gets the glance's board back drawn to it. With no T this is the
  // glance, byte for byte as before. Nothing about the board is decided here
  // that T could change — the same window, the same levels, the same words —
  // so the two cannot say different things about one scan.
  const svg = T ? T.svg : $('svg');
  // Two SVGs in one document may not share an id, and the puts' stripes are
  // reached from the stylesheet by one (the key's swatches keep a third copy,
  // #tradedPutsKey, for the same reason).
  const PCID = T ? T.id + 'Pc' : 'pc', PATID = T ? T.id + 'TradedPuts' : 'tradedPuts';
  if(T) T.bars = false;                            // true only once they are drawn
  // The geometry a finger reads is this drawing's, so it goes when the drawing
  // does: every guard below leaves the chart saying why it drew nothing, and a
  // lens over the board drawn last would be magnifying a chart that is no
  // longer on the screen.
  if(!T) CHART = null;
  // The head says what the chart shows and which scan drew it. A stale axis
  // then needs only the clock at its right foot, 09:30 to 15:11, where it
  // said "SCAN 15:11" (WORDS-SPEC #8, #9, #12).
  const scanAt = etTime(Date.parse(PAY.row_ts));
  if(!T) $('ldWhen').textContent = scanAt ? 'AS OF ' + scanAt : '';
  // Measure RAW, then decide, then clamp. The old line did Math.max(240, ...)
  // inline, which meant a collapsed container silently became a 240px chart —
  // the clamp erased the very condition worth reporting.
  const rawW = T ? T.W : Math.round($('ladder').getBoundingClientRect().width);
  // The height is a constant now, so a vertical collapse is not reachable; what
  // still can be is a zero-WIDTH container — a card that has not laid out yet,
  // or a parent with display:none. The old guard measured HEIGHT and would not
  // have seen it. Under 240 there is no room for the plot plus the label
  // gutter, and drawing anyway produces a garbled chart rather than an empty
  // one. rawW of 0 is the not-laid-out case and must not draw either. rawW is
  // the card's whole width since the ladder bled into its padding (.ladder),
  // so this trips at a 272px phone, and at 240 the plot is still ~183px wide.
  const SVGH = (T ? T.H : LADDER_H) - 1;
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
  // full screen: a row above the plot for the two side names, which the glance
  // has no room for and does not need — its key is one tap away. Reserved on
  // exactly the condition they are drawn on, or the svg clips them: without
  // this the words drew at y 61.58 in an svg whose top is 70 and 8.4px of a
  // 13.9px cap went.
  const PAD_T = 9 + stackH(aboveAt) + (T ? 16 : 0), PAD_B = 29 + stackH(belowAt);
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
  // the puts' stripes: their red with a line of card 1px in 3, at 45 degrees
  o += '<defs><pattern id="' + PATID + '" patternUnits="userSpaceOnUse" width="3" height="3"'
     + ' patternTransform="rotate(45)"><rect class="p-tradedputbg" width="3" height="3"/>'
     + '<rect class="p-tradedputln" width="1" height="3"/></pattern>'
     + '<clipPath id="' + PCID + '"><rect x="' + PLOT_L + '" y="' + plotTop
     + '" width="' + PLOT_W + '" height="' + n1(plotH) + '"/></clipPath></defs>';

  // ---- clipped plot content ---------------------------------------------
  // No shade behind it since 2026-09-18. Eight bands of contracts share
  // resolved to five greys a reader could tell apart, and the one band that
  // read was on the put wall's strike, already ruled and tagged, on 24 of 24
  // scans of 2026-09-16 (INK-SPEC.md 1.2).
  let g = '';
  // WHERE CONTRACTS TRADED TODAY, puts and calls apart, as bars behind the
  // price line. At each strike its puts grow left from a zero TRADED_ZERO of
  // the plot in from its left edge, striped red, and its calls right, solid
  // green, on one scale (glance.js). Which side of the zero says puts or calls
  // before the hue does, and the stripes say it again with no colour at all:
  // the two fills are only 1.041:1 apart. The zero is a gap of card between
  // them, TRADED_GAP each side of it, never an inked upright: left to right on
  // this chart is the time of day, and a line standing in the plot reads as a
  // moment (CPB-SPEC.md 2.3). Each side's outer end, paler in its own hue, is
  // what traded there since the reading on the card below (tradedSince), on
  // the side's own scale; on the puts the stripes stop where it starts. Under
  // a pixel there is nothing to see, so nothing is drawn. First in the clip,
  // so every other mark is on top of them.
  // Full screen the bars take the room they are short of on the card: thicker,
  // and a quarter of the plot a side instead of a fifth, with the zero further
  // left so the longer puts still fit inside the plot (ZOOM-SPEC.md 5).
  const traded = tradedBars(st.strikes, WIN.lo, WIN.hi, plotTop, plotBottom, T && T.hMax);
  const ZERO_AT = (T && T.zero) || TRADED_ZERO, SIDE_AT = (T && T.side) || TRADED_SIDE;
  const TRADED_GAP = 0.5, ZERO = PLOT_L + ZERO_AT * PLOT_W;
  const kSide = traded && traded.most > 0 ? (SIDE_AT * PLOT_W - TRADED_GAP) / traded.most : 0;
  const barX = b => ZERO - TRADED_GAP - b.vp * kSide;    // the pair's left end, where its puts end
  const barXR = b => ZERO + TRADED_GAP + b.vc * kSide;   // its right end, where its calls end
  // a part of a bar, its two edges rounded to the tenth, so parts that meet
  // share one edge and the gap stays one pixel of card
  const tRect = (cls, x0, x1, y, h) => {
    const a = +n1(x0), b = +n1(x1);
    return b > a ? '<rect class="' + cls + '" x="' + n1(a) + '" y="' + n1(y) + '" width="' + n1(b - a)
                 + '" height="' + n1(h) + '"/>' : '';
  };
  if(traded){
    for(const b of traded.bars){
      const y = b.y - traded.h / 2, xp = barX(b), xc = barXR(b);
      const d = st.since ? st.since.by[b.v] : null;
      const lc = d ? d[0] * kSide : 0, lp = d ? d[1] * kSide : 0;
      // where each side stood at the reading
      const pAt = lp >= 1 ? xp + lp : xp, cAt = lc >= 1 ? xc - lc : xc;
      g += tRect('p-tradedputsince', xp, pAt, y, traded.h) + tRect('p-tradedput', pAt, ZERO - TRADED_GAP, y, traded.h)
         + tRect('p-tradedcall', ZERO + TRADED_GAP, cAt, y, traded.h) + tRect('p-tradedcallsince', cAt, xc, y, traded.h);
      // each side's end, where its length is read, on a side of 3px or more:
      // on a stub two ticks and a sliver of fill read as a dumbbell, not a
      // length, and a side that traded nothing draws nothing
      for(const [ex, len] of [[xp + 0.6, b.vp], [xc - 0.6, b.vc]])
        if(len * kSide >= 3)
          g += '<line class="p-tradedend" x1="' + n1(ex) + '" y1="' + n1(y) + '" x2="' + n1(ex)
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
  o += '<g clip-path="url(#' + PCID + ')">' + g + '</g>';

  // ---- no words in the plot ----------------------------------------------
  // NO CALL WALL ABOVE / NO PUT WALL BELOW and the bracket down the plot's left
  // edge that scoped it came off on 2026-09-18, by the owner's choice. The
  // levels card that gave a side measured empty its own row went on
  // 2026-09-19, so no part of the page says it now.

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

  // ---- the bugs: the nearest wall on each side ----------------------------
  // Both since 2026-09-10, when the levels card stopped naming only the nearer
  // of the two under a direction word. The card itself went on 2026-09-19.
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

  // ---- full screen: which side of the zero is which ------------------------
  // The two side names, once, over the two sides of the zero. They are the
  // chart's ONLY key to the hatched fill, and the hatch is the only channel
  // that carries the split without colour: the put and call inks were built to
  // the same lightness, 0.1122 and 0.1126, so they are 1.003:1 apart and come
  // out the identical grey #5E5E5E in greyscale and under both dichromat
  // simulations. They went off with the bar figures on 2026-09-20 because they
  // were drawn inside that block; they were never part of it.
  //
  // Full screen only, and above the plot rather than in it: the glance has no
  // room for a row of its own, and its key is one tap away from the page the
  // glance is on, which this view is not.
  if(T && traded)
    o += '<text class="p-colhead" x="' + n1(ZERO - 4) + '" y="' + n1(plotTop - 6)
       + '" text-anchor="end">PUTS</text>'
       + '<text class="p-colhead" x="' + n1(ZERO + 4) + '" y="' + n1(plotTop - 6) + '">CALLS</text>';

  // ---- the count on the longest bar ---------------------------------------
  // The bars' one number, "3,861 PUTS": the longest single side in view and
  // which side it is, the whole day's, in the grey family, where the
  // brackets' words below say just now, in theirs. The scale is per scan, so a
  // full-length side was 3,861 puts at 15:10 on 2026-09-16, and only this says
  // which. It sits 4px left of its pair, past the puts' end on the card side,
  // where a bar chart puts its value, and at the plot's right end only if
  // that would put it on the live dot's ring or off the plot; the brackets'
  // word below may move it. The busiest strike is usually one the chart
  // already rules, so the count's card halo cuts that rule for its width.
  //
  // THE GLANCE'S, and not the full view's: it exists because the glance has
  // room for one number and every other length is read against it. Full
  // screen the table below prints every one of them, so it was a second name
  // for a figure already on the screen — 1,679 at 1,500 on 2026-09-15 10:39,
  // in the row the reader is looking at, with the put wall's dashed rule
  // through it.
  let count = null;
  if(traded && !T){
    const {b, n, call} = traded.lead;
    // Centred on its bar: the figures' ink runs 8px above the baseline and a
    // comma 2.2 below it, so the baseline sits 0.36em under the bar's middle.
    // The ring is 7 round the dot, and 2 more keeps them apart.
    const num = gUsd(n, 0).replace('$',''), s = num + (call ? ' CALLS' : ' PUTS'),
          w = figW(num, 11, 600) + (call ? COUNT_CALLS_W : COUNT_PUTS_W);
    const by = b.y + 0.36 * 11;
    const top = by - 8, bottom = by + 2.2, tip = barX(b);
    const clear = xe => xe - w >= PLOT_L + 2 && xe <= PLOT_R - 2
      && !(dot && xe > dotX - 9 && xe - w < dotX + 9 && top < priceY + 9 && bottom > priceY - 9);
    const xe = [tip - 4, PLOT_R - 4].find(clear);
    count = {b, s, w, by, top, bottom, tip, clear, x:xe != null ? xe : tip - 4};
  }

  // ---- the word ------------------------------------------------------------
  // One, on the biggest area the plot shows, counting any area it could not:
  // past the cap, or a second off the plot on a side whose edge row is taken.
  // It says what happened, that trading here just took a bigger share of the
  // board's than it had, and nothing about what price does next. It never
  // touches the count or the dot's ring (wordRow), nor a bar while any row
  // clear of the bars is left. Where the busiest strike is the one that
  // changed, the count and the word want one row, and a count stacked under
  // the word reads as the word's own number. The count then moves to the first
  // of three spots that clears the word by 12px and the ring: right of its
  // pair, where no bar is; centred over the zero; just inside the puts' end.
  // Inside the end was the one spot while the bars grew from the plot's edge;
  // here that end is the puts' outer end, so it comes last (CPB-SPEC.md 8.4).
  // With no spot clear the word keeps its distance or leaves its box.
  let word = null;
  if(boxes.length){
    const box = boxes[0];
    const wx = PLOT_L + 6, ww = NEW_WORD + (unshown ? NEW_MORE : 0);
    const across = (x0, x1, air) => x0 < wx + ww + air && x1 > wx - air;
    // The count keeps 5.5px above or below the word, what the chart's closest
    // two labels keep, and 12 beside it, so it is not read as the word's next
    // line or its next figure. Bars, the ring and another box keep 2; a pair
    // is measured across its own width, puts' end to calls' end. `loose`, the
    // last resort, lets the word cross bars, its card halo cutting them as it
    // cuts a rule: split bars stand across more of the plot than one grey bar
    // did, and at 320 the long "· 1 MORE" form found no row clear of them on
    // 14 boards of 2026-09-15..17.
    const row = loose => {
      const hard = [];
      if(count && across(count.x - count.w, count.x, 12)) hard.push([count.top - 5.5, count.bottom + 5.5]);
      for(const b of (traded && !loose ? traded.bars : []))
        if(across(barX(b), barXR(b), 2)) hard.push([b.y - traded.h / 2 - 2, b.y + traded.h / 2 + 2]);
      if(dot && across(dotX - 9, dotX + 9, 2)) hard.push([priceY - 11, priceY + 11]);
      for(const x of boxes.slice(1)) hard.push([x.t - NEW_W / 2 - 2, x.t + NEW_W / 2 + 2], [x.b - NEW_W / 2 - 2, x.b + NEW_W / 2 + 2]);
      return wordRow(box.t, box.b, inks.map(([y, hw]) => [y - hw, y + hw]), hard, plotTop + 1, plotBottom - 1);
    };
    const inBox = y => y != null && y - WORD_UP >= box.t && y + WORD_DOWN <= box.b;
    const stacked = y => y != null && across(count.x - count.w, count.x, 12)
                      && y - WORD_UP < count.bottom + 12 && y + WORD_DOWN > count.top - 12;
    let by = row();
    const past = count ? [barXR(count.b) + 4 + count.w, ZERO + count.w / 2, count.tip + 4 + count.w]
      .find(x => !across(x - count.w, x, 12) && count.clear(x)) : null;
    if(count && (!inBox(by) || stacked(by)) && count.x === count.tip - 4 && past != null){
      count.x = past;
      const moved = row();
      if(inBox(moved)) by = moved; else count.x = count.tip - 4;
    }
    if(by == null) by = row(true);
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
  // When a nearest wall is itself off-window the bug triangle cannot
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

  // what the caller cannot know until the chart is solved: whether there are
  // bars at all, and which prices got one — the full view's table marks the
  // rows that did not, and its foot counts them
  if(T){ T.bars = !!traded; T.barsAt = traded ? traded.bars.map(b => b.v) : []; }
  // WHAT A FINGER ON THE CHART READS (C3). Every number and every height here
  // was solved above; a gesture that worked any of it out again would be a
  // second chart, and the two would disagree on the day the rules changed.
  // The glance's goes in CHART, so the lens magnifies what was drawn and the
  // sideways read reads the line that was drawn. The full screen view reads
  // its own drawing the same way, so its geometry is handed back on T rather
  // than thrown away — T.bars and T.barsAt already travel out this way. Only
  // the GLANCE's goes in the global, because only the glance's is magnified.
  const geo = {W:CW, H:SVGH, plotL:PLOT_L, plotR:PLOT_R, pathR:PATH_R, top:plotTop, bottom:plotBottom,
               ribB:RIB_B, t0, t1, since:st.since, bh:traded ? traded.h : 0,
               bars: traded ? traded.bars.map(b => ({v:b.v, y:b.y, vc:b.vc, vp:b.vp, x0:barX(b), x1:barXR(b)})) : [],
               pts: pts.map(q => ({t:q.t, s:q.s, x:xFor(q.t), y:yFor(q.s)})),
               live: (!st.withdrawn && inWin(ref)) ? {v:ref, x:dotX, y:priceY, t:lp ? lp.t : tapeEnd} : null,
               vol: st.vol.map(b => ({t0:b.t0, t1:b.t1, sum:b.sum, x0:xFor(b.t0), x1:xFor(b.t1)}))};
  if(T) T.geo = geo; else CHART = geo;
  // The lens is a <use> of this drawing, so the drawing needs a name. Only the
  // glance's takes it: two elements with one id in a document would have the
  // lens magnifying whichever the browser found first.
  //
  // THE MARKS A SIDEWAYS DRAG DRAWS GO INSIDE THE DRAWING, in a layer of its
  // own at the end so they sit over it. The glance cannot do that — its
  // drawing is wrapped for the lens to <use>, and a <use> would magnify the
  // marks with it — so the glance keeps a second <svg> over the top, and its
  // id is the one thing the two do differently. Written empty on every paint,
  // which is what puts a stale reading away when a tick redraws the board.
  svg.innerHTML = T ? o + '<g id="' + T.id + 'Marks"></g>' : '<g id="ldg">' + o + '</g>';
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

// THE CHART'S KEY opens on a tap. The link under the chart is a control and
// looks like one, as the reads page's button does, so a plain tap opens it.
// The sheet it opens is the one its aria-controls names; the rest is
// sheet.js's.
$('howto').addEventListener('click', () => MiraiSheet.open($('howto')));

/* ---- C2. the chart, full screen ----------------------------------------

   The lengths on the glance are WIDTHS, set by the phone's 360px, and no
   height makes them longer: at 360 a side of a bar is 12.3px at the median
   and what traded since the reading is 2.5, which is 0.5mm on the owner's
   screen. So the chart opens (ZOOM-SPEC.md 1 and 5), at the screen's size,
   where the day's shape is big enough to read.

   IT GIVES UP MORE THAN HALF THAT ROOM TO A TABLE (the owner's decision of
   2026-09-20, FULL2-SPEC.md 4): the chart takes FULL_CHART_SHARE of what the
   head and the foot leave and writes nothing on its bars, and every price the
   scan listed is written out under it instead. What the chart keeps is the
   shape — which price has the long bar, which side of the zero it is on,
   where price went — and the glance's one count with it.

   THE TABLE IS WHAT THE CHART CANNOT DO. It does not care how close two
   prices are, so the board with nine prices inside $40 reads exactly as the
   open one does; it can list a price the chart's window leaves out; and it
   has room to print a measured +0 where the chart could only leave a blank.

   IT IS A SHEET. sheet.js already knows every way a screen like this is
   closed and every way that goes wrong on a phone: one history entry, so the
   phone's Back closes it rather than leaving the page; the close button, the
   backdrop and Escape all unwinding that entry so there is one closing path;
   the second tap of a double tap refused; the focus going back to what opened
   it; and the shell told the page is not at the top while it is open, so a
   drag inside it is not a pull-to-refresh. A second mechanism would have to
   learn all of it again, and a page with two would have two things that
   believe they are the only one open. The only thing this adds is the
   painting, which is the page's job anyway.

   PORTRAIT ONLY. The shell is locked to it (AndroidManifest screenOrientation
   = "portrait"), so there is no sideways to draw and none is offered. */

// 14 rather than the glance's 8, and a quarter of the plot each side rather
// than a fifth, with the zero at 0.55 instead of 0.72 so a quarter of the plot
// of puts still fits inside it (ZOOM-SPEC.md 5). FULL_MIN_H is a floor under
// the SUBTRACTION, not a design number: a screen this view cannot measure
// would otherwise ask paintLadder for a negative height, and a chart drawn too
// short is a chart that says so (the ladder's own guard).
const FULL_H_MAX = 14, FULL_SIDE = 0.25, FULL_ZERO = 0.55, FULL_MIN_H = 240;

// THE CHART AND THE TABLE SPLIT THE SCREEN THE HEAD AND THE FOOT LEAVE, half
// each (FULL2-SPEC.md 4.4, raised from 0.46 on 2026-09-20 at the owner's ask
// for a chart "increased in size slightly").
//
// MEASURED, on all 514 boards of 09-15..17 at the owner's 360x780. The head is
// 70px on every one of them; the foot is a paragraph and is not — 139px on 413
// of the 514, 122 on 66, 105 on 25, 20 on the day's first 10. So the common
// board has 571px to divide, and the share decides the chart to the pixel: 262
// at 0.46, 285 at 0.50.
//
// WHAT IT COSTS IS COUNTED IN ROWS, because that is the only unit the table
// spends in: a row is 21px, flat, on every board. The table's box loses the 23
// the chart gains, and 23 is one row and the change from it — 12 rows fit
// today (on 417 of the 514), 11 fit at 0.50, and 14.5px of the 12th stay in
// view to say the board has not ended. It takes from a table that was already
// scrolling: 77% of boards list more prices than fit, and 51% list 15 or more.
//
// WHAT IT BUYS is bar thickness, which is a HEIGHT and so the only thing a
// taller chart can move (a length is a width, set by the phone — glance.js).
// Across those boards the median bar goes 8.80px to 9.60 and the thinnest bar
// on any board 4.40 to 4.90, and the price line's own rise and fall grows from
// 104px to 115. Not a knee, a straight line: the chart takes what the table
// can spare, and one row is what "slightly" is worth.
const FULL_CHART_SHARE = 0.50;

// The full screen chart's geometry, the way CHART is the glance's: what
// paintLadder solved, so a finger reads the drawing that is on the screen
// rather than working the chart out a second time.
let CHART_FULL = null;

function chartIsOpen(){ return $('chartFull').getAttribute('aria-hidden') === 'false'; }

function boxH(id){
  const r = $(id).getBoundingClientRect();
  return (r && isFinite(r.height)) ? r.height : 0;
}

function paintChart(){
  // The MEASURED screen, never a viewport unit: inside the app 100vh reads 0
  // (the token in index.html's :root). The same numbers --app-h is written
  // from.
  const W = document.documentElement.clientWidth || window.innerWidth || 0;
  const screenH = window.innerHeight || document.documentElement.clientHeight || 0;
  // COUNTED TO, not AS OF: this view carries two clocks and they are different
  // moments from different fields — the scan every figure in the table is
  // counted to, and the reading the "+n" figures are measured from. They were
  // 38 minutes apart on the 10:39 board of 2026-09-15, both a bare HH:MM, with
  // nothing saying which was which. This one says what it is the time of; the
  // foot names the other by its own noun.
  const scanAt = etTime(Date.parse(PAY.row_ts));
  $('cfWhen').textContent = scanAt ? 'COUNTED TO ' + scanAt : '';
  const st = state();
  const T = {svg:$('cfSvg'), id:'cf', W, when:scanAt,
             hMax:FULL_H_MAX, side:FULL_SIDE, zero:FULL_ZERO};
  // The foot's words are part of what the chart worked out, and the foot's
  // height is part of the room the chart gets, so the two settle against each
  // other rather than one guessing at the other: draw, write the foot, and
  // draw once more if writing it moved the floor.
  for(let pass = 0; pass < 2; pass++){
    const was = boxH('cfFoot');
    T.H = Math.max(FULL_MIN_H,
                   Math.round((screenH - boxH('cfHead') - was) * FULL_CHART_SHARE));
    paintLadder(st, T);
    chartTable(st, T);
    $('cfFoot').innerHTML = chartFoot(st, T);
    if(boxH('cfFoot') === was) break;
  }
  // the LAST pass's, which is the one on the screen: the first pass was drawn
  // to a foot height the foot then disagreed with
  CHART_FULL = T.geo || null;
}

function cfEl(tag, cls, text){
  const e = document.createElement(tag);
  if(cls) e.className = cls;
  if(text != null) e.textContent = text;      // textContent only: nothing here writes markup
  return e;
}

function cfCount(cls, n, since){
  // <td><span class="cf-cell"><span class="n cf-put">899</span>
  //     <span class="s">+307</span></span></td>
  // The count against its own track and the since after it on its own, so the
  // counts read straight down the column whatever the since is. Two absences
  // and neither is a nought: a side the scan did not measure gets an EMPTY
  // cell, and a strike the reading never listed gets its count and no since.
  // A measured +0 is printed — it is a real zero, and the only screen with
  // the room to tell it from a blank.
  const td = cfEl('td');
  if(n == null) return td;
  const cell = cfEl('span', 'cf-cell');
  cell.appendChild(cfEl('span', 'n ' + cls, gUsd(n, 0).replace('$','')));
  cell.appendChild(cfEl('span', 's', since == null ? '' : '+' + gUsd(since, 0).replace('$','')));
  td.appendChild(cell);
  return td;
}

function chartTable(st, T){
  // EVERY PRICE THE SCAN LISTED, one row, highest first — the figures the
  // chart above stopped writing on its bars (FULL2-SPEC.md 4). tableRows
  // decides what each row says and what it cannot say; this places it.
  //
  // The chart is scaled to a window, so 3 or 4 of a board's prices usually
  // fall outside it. Those rows are still here, their price a shade lighter
  // and counted in the foot, because a price the scan listed is a fact and a
  // missing row would read as one the scan never saw.
  const rows = tableRows(st.strikes, st.since, (st.frames || {}).book_times);
  T.rows = rows.length;
  T.offChart = 0;
  // which columns hold anything at all, so the foot names a column only where
  // something was measured for it: the day's first books carry the pile and
  // no counts, and a strike the reading never listed leaves no "+n" anywhere.
  T.counted = rows.some(r => r.vp != null || r.vc != null);
  T.sinced = rows.some(r => r.sp != null || r.sc != null);
  T.piled = rows.some(r => r.pile != null);
  // Under TABLE_NARROW the two count columns cannot both be paid for, so the
  // pace column goes and the foot says so. The dropped column is REMOVED
  // rather than hidden: a <col> at width:0 still takes a share of the
  // surplus under table-layout:fixed, which left the counts 55px at 320
  // instead of the 79 they are owed.
  T.paced = T.W >= TABLE_NARROW;
  T.worded = T.paced && rows.some(r => r.word != null);
  T.blank = false;                            // true once an empty cell is drawn
  $('cfScroll').hidden = !rows.length;
  $('cfTable').classList.toggle('no-pace', !T.paced);
  const col = px => { const c = cfEl('col'); if(px) c.style.width = px + 'px'; return c; };
  const cols = [col(TABLE_PRICE), col(), col(), col(TABLE_PILE), col(TABLE_TURN)];
  if(T.paced) cols.push(col(TABLE_PACE));
  $('cfCols').replaceChildren(...cols);
  const drawn = new Set(T.barsAt || []);
  const out = [];
  for(const r of rows){
    const off = !drawn.has(r.v);
    if(off) T.offChart++;
    const tr = cfEl('tr', off ? 'cf-off' : null);
    tr.appendChild(cfEl('td', 'cf-px', gUsd(r.v, 0).replace('$','')));
    tr.appendChild(cfCount('cf-put', r.vp, r.sp));
    tr.appendChild(cfCount('cf-call', r.vc, r.sc));
    tr.appendChild(cfEl('td', 'cf-pile', r.pile == null ? null : gUsd(r.pile, 0).replace('$','')));
    tr.appendChild(cfEl('td', 'cf-turn', gTimes(r.mult)));
    if(T.paced) tr.appendChild(cfEl('td', 'cf-pace c-pace', r.word));
    // read off the cells themselves rather than worked out again from the row,
    // so what the foot says about a blank cannot drift from what is drawn
    for(const td of tr.children) if(!td.textContent) T.blank = true;
    out.push(tr);
  }
  $('cfBody').replaceChildren(...out);
}

function chartFoot(st, T){
  // What the view says under its table: which colour is which side, when the
  // counts were counted, what the small figure after one is, what each of the
  // house-word columns holds, and what an empty cell means. It is the only
  // place a column's own name can be explained — the tracks are 48 to 52px
  // and no heading that explains anything fits one. Every clause is left out
  // rather than written empty (law 1) — no reading, no since; no empty cell,
  // nothing about blanks; no price off the chart, nothing about one; and
  // nothing drawn and nothing listed says nothing at all.
  //
  // THE PILE'S DATE IS PRINTED, never "last night": it is the prior SESSION's
  // close, and one session in five follows a weekend or a holiday. A payload
  // without the date says "the prior session's close", which is true and
  // vaguer, rather than naming a day it does not know.
  if(!T.rows) return '';
  // THE TWO CLOCKS ARE TOLD APART HERE. Every count is the head's scan and
  // every "+n" is measured from the reading, and those are up to 38 minutes
  // apart. The head says COUNTED TO; this points at it rather than printing
  // the same time twice, and names the reading by its own noun.
  const to = T.when ? ', counted to the time above' : '';
  const since = T.sinced
    ? '; <b>+n</b> is what traded since the ' + etTime(st.since.at) + ' reading,'
      + ' <b>+0</b> that none did' : '';
  const traded = T.counted
    ? '<span class="put">Puts</span> and <span class="call">calls</span>'
      + ' traded at each price today' + to + since + '.' : '';
  const day = etDay(st.pileDate);
  // ONE SENTENCE OF COLUMN NAMES, each named only where its column holds
  // something. PACE is the one nothing else on the screen explains: its cells
  // read `faster`, and no heading that says faster than WHAT fits the 52px
  // track — `VS EARLIER` measures 65.80 even untracked — so the referent is
  // carried here, in the words the glance's own head over these three uses.
  //
  // TIMES SAYS WHAT IS DIVIDED BY WHAT. "how many times over it changed hands"
  // says what the figure DOES and names neither side of the division, so a
  // first-time reader cannot get from it to the number. Both periods are on
  // the screen: the numerator's in this clause and the denominator's in the
  // PILE clause before it, which is pushed with it, so "that pile" is never
  // orphaned.
  const named = [];
  if(T.piled) named.push('<b>PILE</b> was already sitting there at the '
                         + (day || 'prior session’s') + ' close',
                         '<b>TIMES</b> is what traded today divided by that pile');
  if(T.worded) named.push('<b>PACE</b> is trading now against earlier');
  const cols = named.length ? ' ' + named.join('; ') + '.' : '';
  // A BLANK IS NOT A NOUGHT, and this is the one screen with the room to print
  // both: a measured +0 where nothing traded there since the reading, an empty
  // cell where nothing was measured at all. Said only where there is an empty
  // cell on the screen to say it of, and only where something else on the
  // board WAS measured — over a table of nothing but blanks there is no
  // figure to tell one from, and the foot there says nothing at all.
  const blank = (T.blank && (traded || cols)) ? ' An empty cell was not measured.' : '';
  // only where there ARE bars: with none drawn at all, a price without one is
  // a price nothing was measured at, which the blank cells already say
  const off = (T.bars && T.offChart)
    ? ' ' + T.offChart + (T.offChart === 1 ? ' price has' : ' prices have')
      + ' no bar: outside the chart’s range.' : '';
  const narrow = T.paced ? '' : ' This screen is too narrow for the pace column.';
  return (traded + cols + blank + off + narrow).trim();
}

// THE CORNER CONTROL IN THE CARD'S HEAD OPENS IT, and nothing else does: a tap
// on the chart itself does nothing at all (the owner's decision of 2026-09-19,
// which overturns the tap of ZOOM-SPEC.md 5). What the chart answers a finger
// with is the lens and the sideways reading (C3), and both of those show what
// is already on the glance while the finger is down. Covering the page with
// another screen is not that, so it is asked for the way everything else on
// the page is asked for — a control that looks like one, says what it opens
// and can be reached without a touchscreen.
$('cfOpen').addEventListener('click', () => openChart());

function openChart(){
  // Nothing drawn, nothing to open (law 1); and one screen over the page at a
  // time, which is sheet.js's own rule — the key and this cannot both be up.
  if(!PAY || MiraiSheet.isOpen()) return;
  paintChart();
  MiraiSheet.open($('cfOpen'));
}

/* ---- C3. a finger on the chart ------------------------------------------ */

// The chart answers a finger held still by magnifying what is under it, and a
// finger dragged sideways by reading the price line at that minute — the
// owner's choice of 2026-09-19 off ZOOM-SPEC.md 3 and 6. Anything else is the
// page's own scroll, and a lift that armed neither gesture does nothing: the
// chart full screen is the corner control's (C2), never a tap on the glass.
//
// ONE STATE MACHINE, because one touch cannot be read by two. touchKind
// (glance.js) is asked the same question by the hold's timer and by every
// move; the verdict is reached once and never revisited. touchstart stays
// passive, so a scroll begins exactly as it did before there were gestures
// here, and the only preventDefault is on a move after a gesture has armed.
//
// WHAT THE PAGE HAS TO SAY TO THE SHELL. The shell wraps the WebView in a
// SwipeRefreshLayout, which takes any downward drag past its slop whenever the
// page says it is at the top — in native code, before a preventDefault here
// counts (ZOOM-RESEARCH.md 3). The chart sits near the top of the page, so
// without this a drag on it reloads the page out from under the reader. While
// a finger is on the chart the page says it is NOT at the top, through
// MiraiSheet.pin: sheet.js holds the page's one bridge to the shell and this
// is not a second one. It is pinned on touchstart rather than when a gesture
// arms, because the shell decides in the drag's first millimetres, long before
// a 250ms hold could have. The cost is that a drag begun ON THE CHART no
// longer pulls to refresh; begun anywhere else on the page it still does.
let TOUCH = null;                // the finger on the chart, and what it turned out to be
let T_HOLD = null;

function chartTouch(e){
  // One finger only. A second is a pinch, which this WebView does not zoom
  // (setSupportZoom(false)), and a gesture steered by two fingers is no
  // gesture: whatever was running is abandoned.
  if(e.touches.length !== 1){ chartOver(); return; }
  const t = e.touches[0];
  TOUCH = {x0:t.clientX, y0:t.clientY, x:t.clientX, y:t.clientY, at:Date.now(), kind:'wait'};
  MiraiSheet.pin(true);
  T_HOLD = setTimeout(() => chartDecide(null, HOLD_MS), HOLD_MS);
}

function chartMove(e){
  if(!TOUCH || !e.touches.length) return;
  const t = e.touches[0];
  TOUCH.x = t.clientX; TOUCH.y = t.clientY;
  if(TOUCH.kind === 'hold'){ e.preventDefault(); lensShow(TOUCH.x, TOUCH.y); return; }
  if(TOUCH.kind === 'read'){ e.preventDefault(); scrubShow(TOUCH.x); return; }
  if(TOUCH.kind === 'wait') chartDecide(e, Date.now() - TOUCH.at);
}

function chartDecide(e, ms){
  // `e` is the move that asked, or null for the hold's own timer, which passes
  // the time it waited — measuring time is the timer's job and measuring
  // movement is the move's, and touchKind weighs the two the same way for
  // both. A verdict is reached once, so a hold is not undone by the drag that
  // steers it and a scroll is never taken back off the page.
  if(!TOUCH || TOUCH.kind !== 'wait') return;
  const kind = touchKind(TOUCH.x - TOUCH.x0, TOUCH.y - TOUCH.y0, ms);
  if(kind === 'wait') return;
  TOUCH.kind = kind;
  clearTimeout(T_HOLD); T_HOLD = null;
  if(kind === 'scroll') return;
  if(e) e.preventDefault();
  if(kind === 'read'){ scrubShow(TOUCH.x); return; }
  shellTick();
  lensShow(TOUCH.x, TOUCH.y);
}

function chartOver(){
  // THE LIFT AND THE CANCEL ARE THE SAME ENDING, since the owner's decision of
  // 2026-09-19: a lift leaves nothing behind it to act on, so a finger taken
  // off the glass and a finger taken away by the system (a call arriving, the
  // app backgrounded) both come to this. Whatever the touch armed goes away
  // with it, the hold's timer with it — a timer left running fires part way
  // into the NEXT touch — and the shell is told the truth about the page's
  // scroll position again.
  clearTimeout(T_HOLD); T_HOLD = null;
  TOUCH = null;
  lensHide(); scrubHide();
  MiraiSheet.pin(false);
}

function shellTick(){
  // One tick as the lens arms, the way a long press confirms itself on the
  // phone. Not press.js's haptic, which fires on a CLICK because a click is a
  // committed action (its WCAG 2.5.2 note); this confirms that the gesture was
  // taken, which is the one thing the reader cannot otherwise tell. A tick per
  // strike would need a bridge method the shell has not got
  // (ZOOM-RESEARCH.md 3.5), so there is one, here.
  try { if(window.MiraiShell && typeof MiraiShell.tick === 'function') MiraiShell.tick(); }
  catch(err){ /* a phone that will not buzz must not break the gesture */ }
}

function chartPoint(svg, geo, cx, cy){
  // A point on the screen as a point on a chart's own drawing. The svg is
  // drawn at the width it measured, so the two are the same size — unless
  // max-width has shrunk it, and then everything here scales by the same
  // ratio. Kept inside the chart, so a finger over the card's padding reads
  // the nearest edge of it rather than nothing.
  //
  // WHICH CHART IS ASKED FOR, because there are two on this page and they are
  // different sizes: passing the drawing and the geometry together is what
  // stops a point on one being read against the other's numbers.
  const r = svg.getBoundingClientRect();
  const k = (geo && r.width) ? geo.W / r.width : 1;
  return {x: Math.min(Math.max(cx - r.left, 0), r.width) * k,
          y: Math.min(Math.max(cy - r.top, 0), r.height) * k};
}

function lensShow(cx, cy){
  // THE LENS IS NOT A PICTURE OF THE CHART MADE BIGGER. It is a second <svg>
  // whose viewBox is the small region under the finger and whose content is a
  // <use> of the chart's own drawing, so the browser redraws every bar,
  // stripe, rule and letter in it at LENS_ZOOM times its size, as crisp as the
  // chart itself. A quote tick that repaints the chart repaints this with it.
  if(!CHART) return;
  const p = chartPoint($('svg'), CHART, cx, cy), at = chartAt(CHART, p.x, p.y);
  const read = $('lensRead');
  read.innerHTML = lensWords(at);
  $('lens').classList.add('on');               // measured while shown, or the readout has no height
  const box = lensBox(cx, cy, read.offsetHeight,
                      document.documentElement.clientWidth, window.innerHeight);
  const svg = $('lensSvg'), w = LENS_W / LENS_ZOOM, h = box.h / LENS_ZOOM;
  svg.setAttribute('width', LENS_W);
  svg.setAttribute('height', box.h);
  svg.setAttribute('viewBox', [p.x - w / 2, p.y - h / 2, w, h].map(n1).join(' '));
  // the reticle: the finger's own point, with a gap in the middle so it covers
  // nothing, over an outline of the bar the readout is about
  const a = 2.2, c = 6;
  $('lensSpot').setAttribute('d', 'M' + n1(p.x - c) + ',' + n1(p.y) + ' H' + n1(p.x - a)
    + ' M' + n1(p.x + a) + ',' + n1(p.y) + ' H' + n1(p.x + c)
    + ' M' + n1(p.x) + ',' + n1(p.y - c) + ' V' + n1(p.y - a)
    + ' M' + n1(p.x) + ',' + n1(p.y + a) + ' V' + n1(p.y + c));
  const bar = at && at.kind === 'strike' ? at.bar : null;
  const mark = $('lensBar');
  if(bar){
    mark.setAttribute('x', n1(bar.x0 - 1.4));
    mark.setAttribute('width', n1(bar.x1 - bar.x0 + 2.8));
    mark.setAttribute('y', n1(bar.y - CHART.bh / 2 - 1.4));
    mark.setAttribute('height', n1(CHART.bh + 2.8));
  }
  mark.style.display = bar ? '' : 'none';
  $('lens').style.left = n1(box.left) + 'px';
  $('lens').style.top = n1(box.top) + 'px';
}

function lensWords(at){
  // The readout under the magnified window: a small table, not a sentence.
  // The strike, then puts and calls in two columns, today and since the
  // reading in two rows — and where a count was never measured it says so in
  // words rather than printing a zero (law 1). Nothing here says anything
  // about where price goes; there are no verbs in it at all.
  if(!at) return '';
  const f = n => gUsd(n, 0).replace('$', '');
  const now = at.price
    ? '<div class="lens-now"><b>' + gUsd(at.price.v, 2).replace('$', '') + '</b> latest price'
      + (at.price.t ? ', ' + etTime(at.price.t) : '') + '</div>' : '';
  if(at.kind === 'shares')
    return now + '<div class="lens-now"><b>' + f(at.block.sum) + '</b> SNDK shares traded '
         + etTime(at.block.t0) + '–' + etTime(at.block.t1 + 60000) + '</div>';
  if(at.kind === 'noblock') return now + '<div class="lens-now">No five-minute block here</div>';
  if(at.kind === 'nostrike') return now + '<div class="lens-now">No option counts on the chart yet</div>';
  const cell = (n, cls) => '<td class="' + cls + '">' + f(n) + '</td>';
  let o = now + '<table class="lens-t"><tr><th class="k">' + f(at.bar.v) + '</th>'
        + '<th class="put">puts</th><th class="call">calls</th></tr>'
        + '<tr><td>today</td>' + cell(at.bar.vp, 'put') + cell(at.bar.vc, 'call') + '</tr><tr>';
  if(at.at == null) o += '<td colspan="3" class="na">since the reading: not counted yet</td>';
  else if(!at.since) o += '<td colspan="3" class="na">since ' + etTime(at.at) + ': not counted here</td>';
  else o += '<td>since ' + etTime(at.at) + '</td>' + cell(at.since.p, 'put') + cell(at.since.c, 'call');
  return o + '</tr></table>';
}

function lensHide(){ $('lens').classList.remove('on'); }

function scrubDraw(geo, marks, row, x){
  // A sideways drag reads the price LINE, which is the one mark on this chart
  // that is a time of day. The reading goes in a fixed row the finger is never
  // on, and the marks on the chart are a line at the finger's minute, a dot on
  // the price there and an outline round the five-minute block of shares it
  // falls in.
  //
  // NO LINE ACROSS THE CHART AT THAT PRICE. A rule at a price that is not a
  // level reads as a target, which is the one thing nothing here may imply
  // (ZOOM-RESEARCH.md 3.6); the dot says where on the line the finger is and
  // the head row says the number.
  //
  // ONE WRITER FOR BOTH CHARTS, because both are the same board: a second copy
  // of this is two screens that can come to disagree about one minute. It is
  // told which geometry, which marks layer and which row, and it puts the
  // marks in that chart's OWN coordinates — the caller has already given the
  // layer its box, or it is inside the drawing and has one. -> false where
  // there was nothing to read.
  const a = priceAt(geo, x);
  if(!a) return false;
  const block = volumeBlockAt(geo.vol, a.x);
  marks.innerHTML =
    '<line class="sc-at" x1="' + n1(a.x) + '" y1="' + geo.top + '" x2="' + n1(a.x)
    + '" y2="' + geo.ribB + '"/>'
    + (block ? '<rect class="sc-blk" x="' + n1(block.x0 - 1) + '" y="' + (geo.ribB - 12)
             + '" width="' + n1(block.x1 - block.x0 + 1) + '" height="13" rx="1.5"/>' : '')
    + (a.y != null ? '<circle class="sc-dot" cx="' + n1(a.x) + '" cy="' + n1(a.y) + '" r="4.2"/>' : '');
  const left = a.kind === 'gap'
    ? '<b>' + etTime(a.t) + '</b>&ensp;no price recorded'
    : '<b>' + etTime(a.t) + '</b>&ensp;<em>' + gUsd(a.s, 2).replace('$', '') + '</em>'
      + (a.kind === 'latest' ? '&ensp;latest' : '');
  // The block's own clock is dropped where the row cannot hold it, the way the
  // strip's name is (stripName): at 320 the two halves overrun the card by
  // 32px with it on 2026-09-17's board, and the block is outlined on the strip
  // in any case. Measured on the row itself rather than from a table of
  // widths, because it is already on the page and already laid out.
  const shares = block ? gUsd(block.sum, 0).replace('$', '') + ' shares' : '';
  const when = block ? ' ' + etTime(block.t0) + '–' + etTime(block.t1 + 60000) : '';
  const write = right => { row.innerHTML = '<span>' + left + '</span><span class="r">' + right + '</span>'; };
  // SHOWN BEFORE IT IS MEASURED. A row still display:none has no width at all,
  // so the comparison below was 0 > 0 on the FIRST reading of every touch and
  // the clock was kept whatever it cost: at 09:30 on 2026-09-17 the row ran
  // 40.6px past its own box at 320 and onto the close control at 360.
  row.classList.add('on');
  write(shares + when);
  if(when && row.scrollWidth > row.clientWidth) write(shares);
  return true;
}

function scrubShow(cx){
  // THE GLANCE'S. Its marks are a second <svg> over the chart rather than a
  // layer inside it, so it is the one that has to say how big that svg is
  // before anything is drawn into it.
  if(!CHART) return;
  const marks = $('scrubMarks');
  marks.setAttribute('width', CHART.W);
  marks.setAttribute('height', CHART.H);
  marks.setAttribute('viewBox', '0 0 ' + CHART.W + ' ' + CHART.H);
  if(!scrubDraw(CHART, marks, $('scrubRead'), chartPoint($('svg'), CHART, cx, 0).x)) scrubHide();
  else marks.classList.add('on');
}

function scrubHide(){ $('scrubMarks').classList.remove('on'); $('scrubRead').classList.remove('on'); }

/* ---- C4. a finger on the chart, full screen -----------------------------

   ONE GESTURE ON THIS SCREEN, and it is the sideways read (the owner's ask of
   2026-09-20). A hold is not offered: the lens is the glance's answer to a
   chart too small to read, and this is that chart drawn bigger — magnifying it
   would be answering a question this screen exists to have already answered.
   So the verdict comes from dragKind, which has no clock in it, and a reader
   who rests a moment before dragging still gets the read.

   WHAT THE TABLE KEEPS. The listeners are on the chart's own <svg>, which is
   flex:none and so is exactly the band the chart is drawn in: a touch that
   lands on the table belongs to the table and scrolls it, and a touch that
   lands on the chart is this. There is no seam to arbitrate, because the two
   never both see one touch — and a drag that begins on the chart and wanders
   down over the table keeps reading the chart, which is right, since the
   reading only ever depended on how far ACROSS the finger is.

   UP AND DOWN IS NOT TAKEN. #cfSvg is touch-action:pan-y and dragKind calls a
   vertical drag 'scroll', so nothing here ever preventDefaults one: the sheet
   scrolls if a longer-than-measured foot has given it something to scroll, and
   nothing moves if it has not. A chart that cannot scroll says so by not
   moving, which is the honest answer, and the table is never scrolled by a
   finger that is not on it.

   NOTHING IS SAID TO THE SHELL, and MiraiSheet.pin is not called. The glance
   has to tell the shell a finger is down, or its SwipeRefreshLayout takes the
   drag and reloads the page; here sheet.js has already told it the page is not
   at the top for as long as a sheet is open, in the one line it keeps for that.
   A second claim on the same bridge would only be a second thing that believes
   it is the only one.

   THE PHONE'S BACK IS THE EDGES', and it is decided in native code before this
   sees anything. A sideways drag begun within Android's own edge strip — some
   20dp each side — is Back, and Back closes this view, which is one of the
   three ways out it already has. The cost is that a read cannot be STARTED in
   those strips; begun anywhere inboard it reads all the way out to them,
   because the finger's place across the chart is clamped into the plot
   (chartPoint, priceAt). The glance escapes this by arming on a 250ms hold,
   which stock Android abandons its Back swipe for; a screen whose one gesture
   is movement cannot buy the same exemption without a bridge method the shell
   has not got. */
let CF_TOUCH = null;

function cfTouch(e){
  // One finger only, as on the glance: a gesture steered by two is no gesture.
  if(e.touches.length !== 1){ cfOver(); return; }
  const t = e.touches[0];
  CF_TOUCH = {x0:t.clientX, y0:t.clientY, x:t.clientX, kind:'wait'};
}

function cfMove(e){
  if(!CF_TOUCH || !e.touches.length) return;
  const t = e.touches[0];
  CF_TOUCH.x = t.clientX;
  if(CF_TOUCH.kind === 'wait'){
    // the verdict is reached once and never revisited, so a scroll is never
    // taken back off the screen half way through
    const kind = dragKind(t.clientX - CF_TOUCH.x0, t.clientY - CF_TOUCH.y0);
    if(kind === 'wait') return;
    CF_TOUCH.kind = kind;
    if(kind === 'scroll') return;
  }
  if(CF_TOUCH.kind !== 'read') return;
  e.preventDefault();
  cfScrubShow(CF_TOUCH.x);
}

function cfOver(){
  // The lift and the cancel are one ending, as they are on the glance: a lift
  // leaves nothing behind it to act on, and a finger taken away by the system
  // must not leave a reading standing on the screen.
  CF_TOUCH = null;
  cfScrubHide();
}

function cfScrubShow(cx){
  if(!CHART_FULL) return;
  const marks = $('cfMarks');
  if(!marks) return;                          // between paints there is no layer
  if(!scrubDraw(CHART_FULL, marks, $('cfRead'),
                chartPoint($('cfSvg'), CHART_FULL, cx, 0).x)) cfScrubHide();
}

function cfScrubHide(){
  const marks = $('cfMarks');
  if(marks) marks.innerHTML = '';
  $('cfRead').classList.remove('on');
}

function chartRepainted(){
  // A tick repaints the chart under an open lens or reading. The numbers
  // beside it are read from the board too, so they are read again from the new
  // one: a readout that keeps the old counts beside the new drawing lies.
  //
  // The full view's marks layer is written empty by the repaint itself, so a
  // finger that is NOT on it needs nothing done: there is no reading left to
  // be stale. One that is gets the new board's.
  if(CF_TOUCH && CF_TOUCH.kind === 'read') cfScrubShow(CF_TOUCH.x);
  if(!TOUCH) return;
  if(TOUCH.kind === 'hold') lensShow(TOUCH.x, TOUCH.y);
  else if(TOUCH.kind === 'read') scrubShow(TOUCH.x);
}

// ONE SET OF LISTENERS ON THE CHART, because one touch has one meaning. THE
// MOVE IS THE ONLY ONE THAT EVER TAKES ANYTHING: it cannot be passive, because
// a move after a hold or a sideways drag has armed belongs here and not to the
// scroller. The landing and the ending say so — a touch begins and ends on
// this chart exactly as it does on a page with no gestures at all. Chrome
// makes a document-level touch listener passive whatever it asks for, so these
// sit on the chart itself (ZOOM-SPEC.md 3). Android's long press would
// otherwise open text selection or its menu over the lens, and a live
// selection turns the next drag into handle-dragging, so the menu is refused.
$('ladder').addEventListener('touchstart', chartTouch, {passive: true});
$('ladder').addEventListener('touchmove', chartMove, {passive: false});
$('ladder').addEventListener('touchend', chartOver, {passive: true});
$('ladder').addEventListener('touchcancel', chartOver, {passive: true});
$('ladder').addEventListener('contextmenu', e => e.preventDefault());

// AND ONE SET ON THE CHART FULL SCREEN, on the <svg> itself, which is the band
// the chart is drawn in and nothing else. Same shape as the glance's and for
// the same reasons: the landing and the ending are passive, so a touch begins
// and ends here as it would on a screen with no gestures at all, and the move
// is the only one that can take anything.
$('cfSvg').addEventListener('touchstart', cfTouch, {passive: true});
$('cfSvg').addEventListener('touchmove', cfMove, {passive: false});
$('cfSvg').addEventListener('touchend', cfOver, {passive: true});
$('cfSvg').addEventListener('touchcancel', cfOver, {passive: true});
$('cfSvg').addEventListener('contextmenu', e => e.preventDefault());

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

  // replaceChildren, the same idiom paintHalf uses. A clear loop written
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
