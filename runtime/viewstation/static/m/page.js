/* page.js — fetch, lay out, paint. All reasoning lives in glance.js.
 *
 * REPAINT DISCIPLINE. The payload tick (60s) recomputes the window and rebuilds
 * everything. The quote tick (5s) rebuilds the SVG against the CACHED window,
 * so the geometry is bit-identical and exactly one mark has moved. A glance is
 * then a comparison against the last one rather than a fresh read.
 */

const USER = new URLSearchParams(location.search).get('user') || 'will';
const $ = id => document.getElementById(id);

let PAY = null, LIVE = null, DIARY = [], READS = [], WIN = null, LADDER_H = 376;
// whether the plot's bracket LABEL said a side was empty this repaint. The
// gate footer speaks only when it did not.
let CLEAR_SAID = {call:false, put:false};
let T_PAY = null, T_SPOT = null;

/* ---- layout ------------------------------------------------------------ */

function sizeLadder(){
  // An explicit pixel height, never flex:1. A flexible child in a fixed column
  // is the only thing that can absorb an overflow, and on 2026-08-24 it
  // absorbed all of it and rendered at zero — correct viewBox, nothing drawn,
  // nothing thrown.
  const SHORT = window.innerHeight <= 700;
  // A+B+D+E+F. Must move with any region height or the six overrun the
  // viewport and body{overflow:hidden} clips the footer.
  const FIXED = SHORT ? 332 : 392;
  const cs = getComputedStyle(document.body);
  const padV = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  LADDER_H = Math.max(200, Math.min(560,
             document.documentElement.clientHeight - padV - FIXED));
  document.documentElement.style.setProperty('--ladder-h', LADDER_H + 'px');
  return LADDER_H;
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
    const [d, rd] = await Promise.all([
      getJSON('/api/raw/file?root=state&path=sndk_reversion/' + encodeURIComponent(pay.session) + '.jsonl&limit=400'),
      getJSON('/api/raw/file?root=state&path=sndk_reads/'      + encodeURIComponent(pay.session) + '.jsonl&limit=40'),
    ]);
    DIARY = (d.body && Array.isArray(d.body.rows)) ? d.body.rows : [];
    READS = (rd.body && Array.isArray(rd.body.rows)) ? rd.body.rows : [];
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
  const scene = PAY.scene;
  const gates = PAY.gates || {};
  const S = (gates.stale_book_min != null) ? gates.stale_book_min : 6;
  const H = (gates.heartbeat_min  != null) ? gates.heartbeat_min  : 45;
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
    points: tapePoints(DIARY),
    vwap: vwapPrice(scene, diaryLast),
    sigma: ((scene.scale || {}).one_sigma_dollars),
  };
}

function paintAll(){
  const st = state();
  paintMast(st);
  paintRegime(st);
  paintLadder(st);
  paintGate(st);
  paintRead();
  paintFoot(st);
}

/* ---- A. masthead ------------------------------------------------------- */

function paintMast(st){
  const sc = st.scene, c = sc.clock || {};
  $('ticker').textContent = sc.instrument ? String(sc.instrument) : '';
  $('livedot').hidden = !(LIVE && LIVE.spot != null);

  let exp = '';
  const fe = c.front_expiry || {};
  if(fe.dte === 0) exp = 'EXPIRES TODAY';
  else if(fe.date) exp = 'EXP ' + new Date(fe.date + 'T00:00:00')
                              .toLocaleDateString('en-US', {weekday:'short'}).toUpperCase();
  else if(fe.dte != null) exp = 'EXP IN ' + fe.dte + (fe.dte === 1 ? ' DAY' : ' DAYS');
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
  // byte-for-byte the strings envWords() uses, so the phone and the desktop can
  // never describe one board in two voices
  const gloss = g == null ? 'gamma sign not measured' : (g ? 'walls hold' : 'walls give way');
  const word = r.word ? String(r.word) : '';
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
  const CW = Math.max(240, Math.round($('ladder').getBoundingClientRect().width) || 356);
  const SVGH = LADDER_H - 1;
  svg.setAttribute('width', CW);
  svg.setAttribute('height', SVGH);
  svg.setAttribute('viewBox', '0 0 ' + CW + ' ' + SVGH);

  // The explicit height makes a collapse impossible; this makes it visible if
  // one ever occurs anyway. The headless harness supplies the chart height and
  // therefore cannot see this class of fault at all.
  if($('ladder').getBoundingClientRect().height < 180){
    svg.innerHTML = '<text class="p-word" x="10" y="24">LADDER TOO SHORT</text>';
    return;
  }

  const sc = st.scene;
  const PLOT_R = CW - 86, PLOT_L = 8, PLOT_W = PLOT_R - PLOT_L;
  const MARK_L = PLOT_R + 6, MARK_R = MARK_L + 30, TAG_R = CW;

  const ref = st.ref ? st.ref.v : null;
  if(ref == null){
    svg.innerHTML = '<text class="p-word" x="11" y="' + (12 + 22) + '">NO PRICE MEASURED</text>';
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
  if(Array.isArray(mag) && mag.length && Array.isArray(mag[0]) && mag[0][0] != null)
    markDrawn(Number(mag[0][0]));
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
  if(Array.isArray(mag) && mag.length && Array.isArray(mag[0]) && mag[0][0] != null)
    push({y:Number(mag[0][0]), kind:'magnet', lead:true, share:Number(mag[0][1]), weight:1});
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
  const t0 = pts.length ? pts[0].t : 0;
  const t1 = Math.max(pts.length ? pts[pts.length-1].t : 1, lp ? lp.t : -Infinity);
  const xFor = t => PLOT_L + ((t - t0) / ((t1 > t0) ? (t1 - t0) : 1)) * PLOT_W;
  if(pts.length >= 2)
    g += '<polyline class="p-path" points="' + pts.map(q => n1(xFor(q.t)) + ',' + n1(yFor(q.s))).join(' ') + '"/>';

  const dotX = Math.min(lp ? xFor(lp.t) : (pts.length ? xFor(pts[pts.length-1].t) : PLOT_R), PLOT_R - 8);
  const priceY = yFor(ref);
  if(!st.withdrawn && lp && pts.length && lp.t > pts[pts.length-1].t){
    const gapMin = (lp.t - pts[pts.length-1].t) / 60000;
    const lx = xFor(pts[pts.length-1].t), ly = yFor(pts[pts.length-1].s);
    if(gapMin <= 30)
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
  for(const side of ['call','put']){
    if((sc.walls||{})[side + '_side_clear'] !== true) continue;
    // The flag was measured against the SCAN spot. If a wall of the other pool
    // now sits on this side of the price on screen, the side is not empty as
    // drawn — say nothing here and let the gate footer carry the qualified note.
    const other = side === 'call' ? 'put' : 'call';
    const cross = ((sc.walls||{})[other] || [])
      .concat([(sc.walls||{})[other + '_heaviest_behind']])
      .some(e => e && e.strike != null &&
                 (side === 'call' ? Number(e.strike) > ref : Number(e.strike) < ref));
    if(cross) continue;
    const a = side === 'call' ? plotTop : priceY, b = side === 'call' ? priceY : plotBottom;
    if(!(b > a)) continue;
    o += '<path class="p-brk" d="M9,' + n1(a) + ' L3,' + n1(a) + ' L3,' + n1(b) + ' L9,' + n1(b) + '"/>';
    if((b - a) >= 34){
      const by = (a + b) / 2;
      // qualified, because the flag is qualified: call_side_clear means no
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
    const clash = wordRows.some(r => Math.abs(r - by) < 12);
    if((yl - yh) >= 24 && by >= plotTop + 9 && !clash)
      o += '<text class="p-word" x="11" y="' + n1(by) + '">TODAY&#39;S RANGE</text>';
  }

  // ---- rules -------------------------------------------------------------
  for(const l of levels){
    const y = n1(yFor(l.y));
    if(l.kind === 'magnet'){
      if(l.lead) o += '<line class="p-magglow" x1="' + PLOT_L + '" y1="' + y + '" x2="' + PLOT_R + '" y2="' + y + '"/>';
      o += '<line class="' + (l.lead ? 'p-mag' : 'p-magrun') + '" x1="' + PLOT_L + '" y1="' + y
         + '" x2="' + PLOT_R + '" y2="' + y + '"'
         + (l.lead ? '' : ' style="stroke-opacity:' + (l.weight||0.5).toFixed(2) + '"') + '/>';
    } else if(l.kind === 'wall'){
      if(l.magnetLead) o += '<line class="p-magglow" x1="' + PLOT_L + '" y1="' + y + '" x2="' + PLOT_R + '" y2="' + y + '"/>';
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
  return {y:Number(e.strike), kind:'wall', side, nearest:!!nearest,
          gex:(e.gex != null && isFinite(e.gex)) ? Number(e.gex) : null,
          held:(e.unchanged_min != null) ? e.unchanged_min : e.unchanged_min_at_least,
          heldExact:e.unchanged_min != null};
}

/* ---- D. gate — the amplification of the nearest level. Never hides. ----- */

function paintGate(st){
  const sc = st.scene, walls = sc.walls || {}, FULL = 20;
  const gate = $('gate');
  const set = (id, t) => { $(id).textContent = t || ''; };
  const hideRows = () => { $('gRowA').hidden = true; $('gRowB').hidden = true; };

  if(bothSidesClear(walls)){
    gate.className = 'gate empty';
    set('gDir','NO WALL EITHER WAY'); set('gMeas',''); set('gStrike','');
    set('gMech','The board was read and holds nothing above or below price.');
    hideRows(); set('gFoot',''); $('gStrike').className = 'g-k';
    return;
  }
  const ref = st.ref ? st.ref.v : null;
  const w = nearestWall(walls, ref);
  if(!w){
    gate.className = 'gate empty';
    set('gDir','NO WALL MEASURED'); set('gMeas',''); set('gStrike',''); set('gMech','');
    hideRows(); set('gFoot',''); $('gStrike').className = 'g-k';
    return;
  }
  gate.className = 'gate';

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
  const held = (w.unchanged_min != null) ? w.unchanged_min : w.unchanged_min_at_least;
  const exact = w.unchanged_min != null;
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

  if(w.gex != null && isFinite(w.gex)){
    $('gRowA').hidden = false;
    $('gBarA').style.width = Math.max(2, Math.min(100, w.gex / FULL * 100)).toFixed(1) + '%';
    $('gValA').innerHTML = '<b>' + esc(w.gex) + '%</b> of book gamma';
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
    mark.textContent = ''; age.textContent = ''; age.className = 'rd-age';
    line.textContent = 'NO READING TODAY'; line.className = 'rd-line expired';
    return;
  }
  const a = gMinutes(m.ageMin);
  if(m.tier === 'expired'){
    mark.textContent = ''; age.textContent = ''; age.className = 'rd-age';
    line.textContent = ('LAST READING ' + a + ' AGO').toUpperCase();
    line.className = 'rd-line expired';
    return;
  }
  age.textContent = a.toUpperCase();
  age.className = 'rd-age' + (m.tier === 'aged' ? ' old' : '');
  mark.textContent = m.vector === 'up' ? '▲' : m.vector === 'down' ? '▼' : '';
  // model output, written with textContent only: it never touches innerHTML and
  // never enters the SVG string
  const at = etTime(m.at);
  line.textContent = (m.tier === 'aged' ? at + ' · ' : '') + m.line;
  line.className = 'rd-line' + (m.tier === 'aged' ? ' aged' : '');
}

/* ---- run --------------------------------------------------------------- */

function start(){
  sizeLadder();
  loadPayload(); loadSpot();
  clearInterval(T_PAY); clearInterval(T_SPOT);
  T_PAY  = setInterval(loadPayload, 60000);
  T_SPOT = setInterval(loadSpot, 5000);
}
function stop(){ clearInterval(T_PAY); clearInterval(T_SPOT); T_PAY = T_SPOT = null; }

document.addEventListener('visibilitychange', () => document.hidden ? stop() : start());
window.addEventListener('resize', () => { sizeLadder(); if(PAY) paintAll(); });
start();
