/* Mirai Viewstation — client (STORYTELLER, tablet side).
   Polls /api/snapshot, renders four surfaces:
   Overview (ambient) · Deep (under the hood) · Learning · Raw·DB (opt-in).
   Every number comes from live state; prose panes embed the canonical markdown. */
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const num = (x, d = 2) => (x == null || isNaN(x)) ? '—' : (+x).toFixed(d);
const sig = (x, d = 2) => (x == null || isNaN(x)) ? '—' : ((+x >= 0 ? '+' : '') + (+x).toFixed(d));
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
/* all on-disk timestamps are Eastern (carry a tz offset); render them in Pacific */
const PT_TZ = 'America/Los_Angeles';
const ptHM = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? String(iso).slice(11, 16) : d.toLocaleTimeString('en-US', { timeZone: PT_TZ, hour: '2-digit', minute: '2-digit', hour12: false }); };
const ptHMS = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? String(iso).slice(11, 19) : d.toLocaleTimeString('en-US', { timeZone: PT_TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }); };
const ptStamp = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? String(iso).slice(0, 16).replace('T', ' ') : d.toLocaleString('en-US', { timeZone: PT_TZ, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).replace(',', ''); };

let SNAP = null;
let VIEW = localStorage.getItem('mirai-view') || 'simple';
let timer = null;

/* ---------------- networking ---------------- */
async function fetchJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(r.status + ' ' + url);
  return r.json();
}
async function poll() {
  try {
    SNAP = await fetchJSON('/api/snapshot');
    const live = SNAP.market_phase === 'open';
    $('#pulse').className = 'pulse' + (live ? '' : ' stale');
    $('#updated').textContent = 'updated ' + ptHMS(new Date().toISOString()) + ' PT' + (live ? '' : ' · auto-refresh paused (market closed)');
    $('#sub').textContent = subtitle(SNAP);
    $('#loading').hidden = true;
    render();
  } catch (e) {
    $('#pulse').className = 'pulse err';
    $('#sub').textContent = 'connection lost — retrying…';
  }
}
/* refetch cadence: auto-refresh ONLY during live market hours (~30s). Outside
   live hours there is no recurring poll at all — the data is static between
   sessions, so the page holds the last scan until you hit ⟳ or refocus the tab.
   Polling is also gated on tab visibility (see boot). */
function pollInterval() { return (SNAP && SNAP.market_phase === 'open') ? 30000 : null; }
function scheduleNext() {
  clearTimeout(timer);
  if (document.hidden) return;            // no refresh while the tab isn't focused
  const ms = pollInterval();
  if (ms) timer = setTimeout(tick, ms);   // null outside live hours → loop stops
}
async function tick() { await poll(); scheduleNext(); }
function subtitle(s) {
  if (s.error) return 'snapshot error';
  const phase = ({ open: '🟢 market open', premarket: '🌙 premarket', closed: '🔒 session closed' })[s.market_phase] || s.market_phase;
  return `${s.session_date} · ${phase} · ${s.telemetry_rows} scans`;
}

/* ---------------- view switching ---------------- */
function setView(v) {
  VIEW = v; localStorage.setItem('mirai-view', v);
  $$('.tab').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  $$('.view').forEach(s => s.hidden = ('view-' + v) !== s.id);
  render();
}
function render() {
  if (!SNAP) return;
  if (SNAP.error) { $('#view-' + VIEW).innerHTML = card('Snapshot error', `<pre class="json">${esc(SNAP.trace || SNAP.error)}</pre>`); return; }
  ({ simple: renderSimple, deep: renderDeep, learning: renderLearning, raw: renderPipeline }[VIEW] || renderSimple)();
}

/* ---------------- small builders ---------------- */
const card = (title, body, hint) =>
  `<div class="card"><h3>${esc(title)}${hint ? ` <span class="hint">${esc(hint)}</span>` : ''}</h3>${body}</div>`;
function staleBanner(s) {
  return s.is_stale ? `<div class="stale-banner">⏳ Market is between sessions — showing the last full session (${esc(s.session_date)}).</div>` : '';
}
const regimeChip = (r) => {
  const m = { pinning: ['green', '🟢 PINNING'], trending: ['red', '🔴 TRENDING'] }[r] || ['amber', '⚪ NEUTRAL'];
  return `<span class="chip ${m[0]}">${m[1]}</span>`;
};
/* dealer-gamma regime, spelled out: short-γ = chases moves (trend) · long-γ = fades them (pin).
   "uncertain" = the flow sensor caught heavy one-way tape fighting a pin read (slide E).
   Labelled "dealers" so it can never be misread as the composite tape regime chip. */
const gammaRegimeChip = (vr) => {
  if (!vr) return '<span class="chip">dealers γ —</span>';
  if (vr === 'short_gamma') return '<span class="chip red">🔴 dealers short-γ · TRENDS</span>';
  if (vr === 'uncertain') return '<span class="chip amber" title="gravity says pin, but heavy one-way flow disagrees — stand cautious">🟡 dealers uncertain · flow vs pin</span>';
  return '<span class="chip green">🟢 dealers long-γ · PINS</span>';
};
/* native (ThetaData) challenger: does the second engine agree with the displayed read? */
const nativeBadge = (dm) => {
  const n = dm && dm.native;
  if (!n || n.regime_agree == null) return '';
  return n.regime_agree
    ? '<span class="chip green" title="native SPX chain read agrees with the SPY-proxy read">native ✓</span>'
    : `<span class="chip red" title="native SPX chain read disagrees with the SPY-proxy read">native ✗ says ${n.regime === 'short_gamma' ? 'short-γ' : 'long-γ'}</span>`;
};
/* friendly provenance for a GEX source string ("spy_proxy×10.0370", "theta_native", …) */
const srcLabel = (s) => {
  if (!s) return '—';
  s = String(s);
  if (s === 'theta_native' || s.startsWith('native')) return 'live GEX';
  if (s.startsWith('spy_proxy')) return 'SPY-OI proxy';
  return s === 'oracle' ? 'model' : s;
};

/* ---------------- 0DTE ROLL widget (Overview hero) ----------------
   The single most-viewed glance: roll a fresh 0DTE on SPX now, or stand
   down — one combined widget (big mood emoji + price strip with a
   direction-coloured target). Reads the per-ticker reversion status badge
   (paper/shadow signal) so it can never disagree with the canonical picklist.
   `rank` lets the combined verdict surface the most actionable ticker. */
function entryState(d) {
  const st = d.status || {}, r = d.reversion || {};
  const badge = st.badge || '';
  const side = st.setup && st.setup !== '—' ? st.setup : null;
  const stretch = (r.gap_stretch != null) ? sig(r.gap_stretch) + 'σ' : (st.stretch || '—');
  if (!d.last_ts || /no data/.test(badge))
    return { rank: 0, emoji: '🌙', verdict: 'NO DATA', sub: 'no scans yet this session', cls: 'idle' };
  if (/FIRE/.test(badge)) {
    const long = side === 'LONG';
    return {
      rank: 4, emoji: long ? '🚀' : '🐻', verdict: 'ROLL — ' + (side || ''),
      sub: long ? `fade the dip — buy calls · ${stretch} stretched`
                : `fade the rip — buy puts · ${stretch} stretched`,
      cls: long ? 'enter-long' : 'enter-short',
    };
  }
  if (/wait/.test(badge))
    return { rank: 3, emoji: '👀', verdict: 'GET READY', sub: `${stretch} stretched${side ? ' · ' + side + ' setup' : ''} — waiting for the turn`, cls: 'arming' };
  if (/skip|no runway/.test(badge))
    return { rank: 1, emoji: '🚧', verdict: 'STAND DOWN', sub: `${stretch} stretched but no room to the target`, cls: 'blocked' };
  return { rank: 1, emoji: '😴', verdict: 'STAND DOWN', sub: 'nothing stretched — wait', cls: 'standby' };
}
/* directional price target: the reversion lens's own magnet (the decision
   variable) first, then the resolved dealer-map pull as the fallback */
function targetInfo(d) {
  const r = d.reversion || {}, g = d.gex || {}, dm = d.dealer_map || {};
  const tgt = r.magnet != null ? r.magnet
    : (dm.magnet != null ? dm.magnet : (g.magnet != null ? g.magnet : (dm.flip != null ? dm.flip : g.flip)));
  if (tgt == null || d.spot == null) return null;
  const up = tgt >= d.spot;
  return { price: tgt, up, arrow: up ? '▲' : '▼' };
}
function freshness(s, lastTs) {
  if (s.is_stale) return `<span class="fresh stale">⏳ last session ${esc(s.session_date)}</span>`;
  if (!lastTs) return '';
  const age = (Date.now() - new Date(lastTs).getTime()) / 60000;
  return `<span class="fresh">${age < 7 ? '🟢' : '⏳'} last scan ${ptHM(lastTs)} PT</span>`;
}
function entryWidget(s) {
  const states = ['SPX'].map(tk => {
    const d = ((s.ult && s.ult.tickers) || {})[tk] || {};
    return { tk, d, e: entryState(d), t: targetInfo(d) };
  });
  const dom = states.reduce((a, b) => (b.e.rank > a.e.rank ? b : a), states[0]).e;
  const lastTs = states.map(x => x.d.last_ts).filter(Boolean).sort().pop();
  const strip = states.map(x => {
    const t = x.t;
    const tgt = t ? `<span class="ps-tgt ${t.up ? 'up' : 'down'}">🎯${t.arrow}${num(t.price, 2)}</span>`
                  : `<span class="ps-tgt muted">🎯 —</span>`;
    return `<div class="ps-item"><span class="ps-tk">${x.tk}</span><span class="ps-spot mono">${num(x.d.spot, 2)}</span>${tgt}</div>`;
  }).join('<span class="ps-div">·</span>');
  return `<section class="roll-widget ${dom.cls}">
      <div class="rw-head"><span class="rw-title">🎯 0DTE — roll or stand down?</span>${freshness(s, lastTs)}</div>
      <div class="rw-emoji">${dom.emoji}</div>
      <div class="rw-verdict">${esc(dom.verdict)}</div>
      <div class="rw-sub">${esc(dom.sub)}</div>
      <div class="ps-strip">${strip}</div>
      <div class="rw-note">🎯 target = fair-value pull (▲ up / ▼ down) · 🌑 shadow signal · paper only · not advice</div>
    </section>`;
}

/* ---------------- OVERVIEW (ambient) ----------------
   The most-viewed page: the dealer-gamma map LEADS (levels, magnet pull,
   break/acceleration zones), with the 0DTE roll widget directly under it. */
function renderSimple() {
  const s = SNAP;
  $('#view-simple').innerHTML = staleBanner(s) + gexMap(s) + entryWidget(s);
}

/* the big SPX dealer-gamma map — GRAVITY (where dealer positioning pulls price)
   with a FLOW row (the live shove that can overpower it): intensity band +
   spot→magnet pull + a levels ladder (walls/flip/magnet/spot with σ distances)
   + flows row + playbook. Every level comes from the resolved dealer_map, so
   this slide, the Deep cards and the markdown heat strip all tell one story. */
function gexMap(s) {
  const d = ((s.ult && s.ult.tickers) || {}).SPX || {};
  const g = d.gex || {}, dm = d.dealer_map || {}, hm = d.heatmap;
  if (!hm) return card('SPX — dealer gamma map', '<div class="muted">no wall structure yet this session</div>');
  const src = srcLabel(dm.source || g.source);
  const head = `<div class="gex-head">
      <div><span class="gex-tk">SPX</span><span class="gex-spot mono">${num(d.spot, 2)}</span></div>
      <div class="flex wrap">${gammaRegimeChip(dm.regime || g.views_regime)}${nativeBadge(dm)}<span class="pill">${esc(src)}</span></div>
    </div>`;
  return `<section class="card gex-map">
      <h3>SPX — dealer gamma map <span class="hint">gravity: where dealers pull price · flow: the live shove · break levels</span></h3>
      ${head}
      ${heatmap(g, hm, true)}
      ${gexLadder(d)}
      ${gexFlows(d)}
      <div class="gex-play">${gexPlaybook(d)}</div>
    </section>`;
}
/* resolved levels for one ticker: dealer_map first, raw gex fields as fallback */
function dmLevels(d) {
  const g = d.gex || {}, dm = d.dealer_map || {};
  return {
    flip: dm.flip != null ? dm.flip : (g.flip != null ? g.flip : g.gamma_flip),
    band: dm.flip_band || g.flip_band,
    magnet: dm.magnet != null ? dm.magnet : g.magnet,
    call_wall: dm.call_wall != null ? dm.call_wall : g.call_wall,
    put_wall: dm.put_wall != null ? dm.put_wall : g.put_wall,
  };
}
function gexLadder(d) {
  const spot = d.spot, sigma = d.sigma, lv = dmLevels(d);
  const bandS = (lv.band && lv.band[0] != null && lv.band[1] != null)
    ? ` · band ${num(lv.band[0], 0)}–${num(lv.band[1], 0)}` : '';
  const L = (label, price, mean, cls) => price == null ? null : {
    label, price, mean, cls, sg: sigma ? sig((price - spot) / sigma, 1) + 'σ' : '',
  };
  const rows = [
    L('Call wall', lv.call_wall, 'upside cap / resistance — break above ⚡ accelerates up', 'wall'),
    L('Gamma flip', lv.flip, `regime line — above = calm (long-γ) · below = volatile (short-γ)${bandS}`, 'flip'),
    L('Magnet', lv.magnet, "today's gamma pull — price is drawn here", 'magnet'),
    L('Spot', spot, 'SPX right now', 'spot'),
    L('Put wall', lv.put_wall, 'downside floor / support — break below ⚡ accelerates down', 'wall'),
  ].filter(Boolean).sort((a, b) => b.price - a.price);
  const body = rows.map(r => `<div class="gex-row${r.cls === 'spot' ? ' is-spot' : ''}">
      <span class="gx-dot ${r.cls}"></span>
      <span class="gx-lvl">${esc(r.label)}</span>
      <span class="gx-price mono">${num(r.price, 2)}</span>
      <span class="gx-sig mono">${esc(r.sg)}</span>
      <span class="gx-mean">${esc(r.mean)}</span>
    </div>`).join('');
  return `<div class="gex-ladder">
      <div class="gex-accel up">⚡ break above the call wall → dealers chase, move can extend up</div>
      ${body}
      <div class="gex-accel down">⚡ break below the put wall → dealers chase, move can extend down</div>
    </div>`;
}
/* the drift row: charm (time-decay hedge pressure into the bell), vanna
   (vol-move hedge pressure) and the live 0DTE options tape lean (native).
   Signs only — magnitudes are uncalibrated shadow values, deliberately hidden. */
function gexFlows(d) {
  const dm = d.dealer_map || {}, n = dm.native || {};
  const chips = [];
  if (dm.pin_top_share != null && !isNaN(dm.pin_top_share)) {
    const pct = Math.round(dm.pin_top_share * 100);
    const word = pct >= 45 ? 'sharp' : pct >= 25 ? 'firm' : 'fuzzy';
    chips.push(`<span class="chip" title="concentration: the magnet strike's share of today's pull field — sharp = one towering strike (aim precisely), fuzzy = a smear (treat as a neighborhood)">🧲 magnet ${word} ${pct}%</span>`);
  }
  if (dm.cex_sign) chips.push(`<span class="chip" title="charm = dealer hedge pressure from time decay, strongest into the close — sign is shadow/uncalibrated">⏳ charm ${esc(dm.cex_sign)}</span>`);
  if (dm.vex_sign) chips.push(`<span class="chip" title="vanna = dealer hedge pressure from a vol move — sign is shadow/uncalibrated">🌊 vanna ${esc(dm.vex_sign)}</span>`);
  if (n.flow != null && !isNaN(n.flow)) {
    const f = +n.flow;
    const lean = Math.abs(f) < 0.15 ? 'balanced' : (f > 0 ? `leaning bought ${sig(f)}` : `leaning sold ${sig(f)}`);
    chips.push(`<span class="chip" title="signed 0DTE at-the-money options flow from the native feed (−1 all sold … +1 all bought)">🎟️ 0DTE tape ${esc(lean)}</span>`);
  }
  if (!chips.length) return '';
  return `<div class="flex wrap mt" style="gap:8px">${chips.join('')}<span class="pill" title="these reads are recorded and graded but do not vote">shadow · signs uncalibrated</span></div>`;
}
function gexPlaybook(d) {
  const spot = d.spot, sigma = d.sigma, lv = dmLevels(d);
  const flip = lv.flip;
  const zone = flip == null ? 'a neutral gamma zone'
    : spot >= flip ? 'the <b>long-γ zone</b> — calm, prone to pin / mean-revert'
                   : 'the <b>short-γ zone</b> — trend-prone, moves can extend';
  const dist = (p) => (p != null && sigma) ? ` (${sig((p - spot) / sigma, 1)}σ)` : '';
  return `SPX <b>${num(spot, 2)}</b> sits in ${zone}${flip != null ? `, flip at ${num(flip, 0)}` : ''}. `
    + `Nearest pull: magnet <b>${num(lv.magnet, 0)}</b>. `
    + `Upside — break the call wall <b>${num(lv.call_wall, 0)}</b>${dist(lv.call_wall)} → acceleration up; `
    + `downside — break the put wall <b>${num(lv.put_wall, 0)}</b>${dist(lv.put_wall)} → acceleration down.`;
}

/* ---------------- DEEP (under the hood) ---------------- */
function renderDeep() {
  const s = SNAP;
  const tk = ['SPX'].map(t => tickerCard(s, t)).join('');
  $('#view-deep').innerHTML = staleBanner(s) +
    `<div class="grid deep-grid mb">${tk}</div>` +
    paperCard(s);
}
function tickerCard(s, tk) {
  const d = ((s.ult && s.ult.tickers) || {})[tk] || {};
  const g = d.gex || {}, dm = d.dealer_map || {}, r = d.reversion || {}, c = d.counts || {};
  const lv = dmLevels(d);
  const src = srcLabel(dm.source || g.source);
  /* head chip = the composite TAPE regime (gamma+range+VR+VIX); the labelled
     dealers-γ chip below is the dealer-positioning read — two different facts */
  const head = `<div class="flex between mb">
      <div><span class="tk" style="font-size:24px;font-weight:800">${tk}</span> <span class="spot mono" style="font-size:18px">${num(d.spot, 2)}</span></div>
      <div class="flex wrap">${regimeChip(d.regime)}<span class="pill">${esc(src)}</span></div>
    </div>`;
  const pipe = pipeline(d);
  const views = `<div class="flex wrap mt" style="gap:8px">
      <span class="chip blue">magnet ${num(lv.magnet, 0)}</span>
      <span class="chip">flip ${num(lv.flip, 0)}</span>
      ${lv.band ? `<span class="chip">band ${num(lv.band[0], 0)}–${num(lv.band[1], 0)}</span>` : ''}
      ${gammaRegimeChip(dm.regime || g.views_regime)}${nativeBadge(dm)}
    </div>`;
  const mets = metrics([
    ['scans', c.scans], ['armed', c.armed], ['would-fire', c.would_fire],
    ['no-runway', c.no_runway], ['lvl breaks', c.level_breaks],
    ['pin/trd/neu', `${c.pin ?? 0}/${c.trend ?? 0}/${c.neutral ?? 0}`],
  ]);
  return `<div class="card">${head}${heatmap(g, d.heatmap)}${views}
    <div class="mt">${pipe}</div>
    <div class="mt"><div class="metrics">${mets}</div></div>
    ${timeline((s.series && s.series[tk]) || [], tk)}</div>`;
}
/* cell colour = long/short hue, brightness scaled by gravity-well intensity.
   Saturation kept low and lightness spans narrow so the band reads as a soft
   gradient, not loud blocks. Tops are perceptually balanced — red rides a touch
   higher (it reads darker per unit lightness) so short-γ never looks heavier or
   more alarming than long-γ at the same intensity. */
function heatColor(kind, i) {
  const t = clamp(i || 0, 0, 1);
  if (kind === 'short') return `hsl(354 46% ${18 + 24 * t}%)`; // calm rose-red
  if (kind === 'flip') return `hsl(43 60% ${18 + 19 * t}%)`;   // golden divider
  return `hsl(162 44% ${17 + 21 * t}%)`;                       // long, teal-green
}
function heatmap(g, hm, big) {
  if (!hm || !hm.cells || !hm.cells.length) return '<div class="muted">no wall structure this tick</div>';
  const cells = hm.cells.map(c =>
    `<div class="heat-cell${c.spot ? ' spot' : ''}" style="background:${heatColor(c.kind, c.intensity)}" title="${num(c.price, 0)} · pull ${Math.round((c.intensity || 0) * 100)}%${c.spot ? ' · spot' : ''}"></div>`).join('');
  const marks = ['put_wall', 'call_wall', 'magnet'].map(k => {
    const m = hm.markers && hm.markers[k];
    if (!m || m.index == null) return '';
    const left = (m.index + 0.5) / hm.cells.length * 100;
    const lbl = { put_wall: 'put wall', call_wall: 'call wall', magnet: 'magnet' }[k];
    return `<div class="heat-mark" style="left:${clamp(left, 6, 94)}%"><b>${lbl}</b>${num(m.price, 0)}</div>`;
  }).join('');
  const p = hm.pull;
  const pullLine = p && p.magnet != null
    ? `<div class="heat-pull">spot <b>${num(p.spot, 2)}</b> <span class="pull-arr ${p.dir}">${p.dir === 'up' ? '▲' : p.dir === 'down' ? '▼' : '■'}</span> 🎯 magnet <b>${num(p.magnet, 2)}</b> <span class="pull-d ${p.dir}">(${sig(p.dpts)} pts · ${sig(p.dpct)}%)</span></div>`
    : '';
  return `<div class="heat${big ? ' heat-lg' : ''}">
      ${pullLine}
      <div class="heat-cells">${cells}</div>
      <div class="heat-scale"><span class="mono">${num(hm.lo, 0)}</span><span class="muted">dealer-gamma band</span><span class="mono">${num(hm.hi, 0)}</span></div>
      <div class="heat-marks">${marks}</div>
      <div class="heat-legend"><span><i style="background:#368c72"></i><b class="lg">long-γ</b> → price PINS / mean-reverts (calm)</span><span><i style="background:#9c3a44"></i><b class="sg">short-γ</b> → price TRENDS / amplifies (volatile)</span><span><i style="background:#977726"></i>flip = the line between them</span><span><i style="background:var(--sky)"></i>spot</span></div>
    </div>`;
}
function pipeline(d) {
  const r = d.reversion || {}, c = d.counts || {};
  const dials = (SNAP.ult && SNAP.ult.dials) || {};
  const steps = [
    ['👀', 'LOOK', `regime read · ${d.regime || '—'}`, d.regime ? 'ok' : 'no', d.regime ? d.regime : 'wait'],
    ['📏', 'STRETCH', `gap ${num(r.gap_stretch, 2)}σ from fair value`, r.armed ? 'warn' : 'no', r.armed ? 'ARMED' : 'not far'],
    ['🛣️', 'RUNWAY', `room to target · ${num(r.runway_sigma, 2)}σ (need ≥${num(dials.RUNWAY_MIN_SIGMA, 2)})`, r.armed ? (r.runway_ok ? 'ok' : 'hot') : 'no', r.armed ? (r.runway_ok ? 'room' : 'no room') : '—'],
    ['↩️', 'TURN', `candle turning back?`, r.fired ? 'ok' : 'no', r.fired ? 'FIRE' : 'wait'],
    ['🎯', 'SCORE', `today's would-fires graded`, c.would_fire ? 'ok' : 'no', `${c.would_fire ?? 0} fired`],
  ];
  return `<div class="pipe">${steps.map(([i, n, ds, st, lbl]) =>
    `<div class="pipe-step"><span class="ico">${i}</span><span class="nm">${n}</span><span class="ds">${esc(ds)}</span><span class="st ${st}">${esc(lbl)}</span></div>`).join('')}</div>`;
}
function metrics(pairs) {
  return pairs.map(([k, v]) => `<div class="metric"><div class="k">${esc(k)}</div><div class="v">${v ?? '—'}</div></div>`).join('');
}
function timeline(series, tk) {
  if (!series.length) return '';
  const maxStretch = Math.max(0.1, ...series.map(p => Math.abs(p.gap_stretch || 0)));
  const bars = series.map((p, i) => {
    const h = clamp(Math.abs(p.gap_stretch || 0) / maxStretch * 100, 8, 100);
    const cls = p.fired ? 'fired' : p.armed ? 'armed' : '';
    return `<div class="tl-bar ${cls}" style="height:${h}%" data-tk="${tk}" data-i="${i}" title="${ptHM(p.ts)} PT · ${num(p.gap_stretch, 2)}σ"></div>`;
  }).join('');
  const t0 = ptHM(series[0].ts), t1 = ptHM(series[series.length - 1].ts);
  return `<div class="mt"><div class="flex between"><span class="card-sub muted" style="font-size:12px;text-transform:uppercase;letter-spacing:.6px">Day timeline — tap a scan</span></div>
    <div class="timeline">${bars}</div><div class="tl-axis"><span>${t0}</span><span>${t1} PT</span></div></div>`;
}
function paperCard(s) {
  const p = (s.ult && s.ult.paper) || {}, sum = p.summary || {};
  const rows = (p.trades || []).map(t =>
    `<tr><td class="mono">${esc(ptHM(t.ts))}</td><td>${esc(t.ticker)}</td><td>${esc(t.fade)}</td>
      <td class="num mono">${sig(t.best_sigma)}σ</td>
      <td><span class="tag ${esc(t.outcome || 'pending')}">${esc(outcomeLabel(t.outcome || 'pending'))}</span></td>
      <td class="num mono">${t.ttr_min != null ? t.ttr_min + 'm' : '—'}</td></tr>`).join('');
  const body = `<div class="sub-line mb">Win rate <b>${sum.win_rate == null ? '—' : sum.win_rate + '%'}</b> · 🟢 ${sum.win ?? 0} win · 🔴 ${sum.loss ?? 0} loss · ⚪ ${sum.scratch ?? 0} scratch · ⏳ ${sum.pending ?? 0} pending — <span class="muted">target ${sig(sum.target_sigma)}σ, stop ${sig(sum.stop_sigma == null ? null : -sum.stop_sigma)}σ, anytime in the day</span></div>
    <div class="scroll-x"><table class="tbl"><thead><tr><th>time</th><th>ticker</th><th>fade</th><th class="num">best</th><th>result</th><th class="num">when</th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="muted">no paper guesses yet</td></tr>'}</tbody></table></div>`;
  return card('Did the guesses work? — paper only, shadow mode', body, 'target-before-stop on 1-min bars');
}
const outcomeLabel = (o) => ({ win: '✅ win', loss: '🔴 loss', scratch: '➖ scratch', pending: '⏳ pending' }[o] || o);

/* scan drill-down modal */
function openScan(tk, i) {
  const p = ((SNAP.series && SNAP.series[tk]) || [])[i];
  if (!p) return;
  const plain = `
    <dl class="kv">
      <dt>time</dt><dd class="mono">${ptHMS(p.ts)} PT</dd>
      <dt>${tk} spot</dt><dd class="mono">${num(p.spot, 2)}</dd>
      <dt>regime</dt><dd>${esc(p.regime || '—')}</dd>
      <dt>stretch from fair value</dt><dd class="mono">${num(p.gap_stretch, 3)}σ</dd>
      <dt>runway to target</dt><dd class="mono">${num(p.runway_sigma, 3)}σ ${p.runway_ok ? '✅ enough' : '🔴 not enough'}</dd>
      <dt>setup</dt><dd>${p.fired ? '↩️ <b>fired</b> a paper guess' : p.armed ? '📏 armed (stretched, waiting for turn)' : '💤 stand by'} ${p.direction && p.direction !== 'none' ? '· ' + p.direction : ''}</dd>
    </dl>`;
  modal(`<div class="flex between mb"><h3 style="margin:0">${tk} · scan @ ${ptHM(p.ts)} PT</h3>
      <div class="seg"><button class="on" data-m="plain">Plain English</button><button data-m="raw">Raw JSON</button></div></div>
     <div id="scan-plain">${plain}</div>
     <div id="scan-raw" hidden><pre class="json">${hlJSON(p)}</pre></div>`);
  $$('#modal .seg button').forEach(b => b.onclick = () => {
    $$('#modal .seg button').forEach(x => x.classList.toggle('on', x === b));
    $('#scan-plain').hidden = b.dataset.m !== 'plain';
    $('#scan-raw').hidden = b.dataset.m !== 'raw';
  });
}

/* ---------------- LEARNING ---------------- */
function renderLearning() {
  const s = SNAP, p = (s.ult && s.ult.paper) || {}, sum = p.summary || {}, dials = (s.ult && s.ult.dials) || {};
  const wr = sum.win_rate;
  const ringColor = wr == null ? 'var(--muted)' : wr >= 60 ? 'var(--green)' : wr >= 40 ? 'var(--amber)' : 'var(--red)';
  const total = (sum.win ?? 0) + (sum.scratch ?? 0) + (sum.loss ?? 0) || 1;
  const dist = `<div style="display:flex;height:16px;border-radius:8px;overflow:hidden;margin-top:6px">
      <div style="width:${(sum.win ?? 0) / total * 100}%;background:var(--green)"></div>
      <div style="width:${(sum.scratch ?? 0) / total * 100}%;background:var(--line2)"></div>
      <div style="width:${(sum.loss ?? 0) / total * 100}%;background:var(--red)"></div></div>
      <div class="flex wrap mt"><span class="tag win">${sum.win ?? 0} win</span><span class="tag scratch">${sum.scratch ?? 0} scratch</span><span class="tag loss">${sum.loss ?? 0} loss</span></div>`;

  const perfCard = card('Paper performance — today',
    `<div class="ring-wrap"><div class="ring" style="--p:${wr ?? 0};--c:${ringColor}">
       <div class="ring-txt"><div class="ring-num">${wr == null ? '—' : wr + '%'}</div><div class="ring-cap">with runway</div></div></div>
     <div><div class="sub-line">avg win in <b>${sum.avg_win_min ?? '—'} min</b></div>
       <div class="sub-line">avg best move <b>${sig(sum.avg_best_sigma)}σ</b> vs ${sig(sum.target_sigma)}σ target</div>
       ${dist}</div></div>`);

  const dialCard = card('The dials — what it can tune',
    `<div class="metrics">
       ${metrics([
        ['target σ', num(dials.PAPER_TARGET_SIGMA, 2)],
        ['stop σ', num(dials.PAPER_STOP_SIGMA, 2)],
        ['runway min σ', num(dials.RUNWAY_MIN_SIGMA, 2)],
        ['wall prox σ', num(dials.WALL_PROX_SIGMA, 2)],
      ])}
     </div><div class="sub-line mt">🔁 Today the dials are fixed; the auto-tuner is the next step — it will nudge these from the scored results.</div>`);

  const learned = extractSection(s.official_markdown && s.official_markdown.ult, 'What we learned today')
    || extractCallout(s.official_markdown && s.official_markdown.ult);
  const learnCard = card('What it learned today', `<div class="breath">${learned ? inline(learned) : '—'}</div>`,
    'fed back to adjust the dials');

  const loop = `<pre class="json" style="color:var(--muted)">   paper guesses ──▶ scored win/loss/scratch ──▶ tally by regime + runway
        ▲                                                    │
        └──────  nudge the dials (runway, target)  ◀─────────┘</pre>`;

  $('#view-learning').innerHTML = staleBanner(s) +
    `<div class="grid cols-2 mb">${perfCard}${dialCard}</div>` +
    `<div class="grid cols-2">${learnCard}${card('How it learns', loop)}</div>`;
}

/* ---------------- PIPELINE MAP (the whole system, stage by stage) ----------------
   Replaces the old raw file explorer. Fetches /api/pipeline once: six stages,
   each with its modules/gates translated to plain English + pointers to the data
   each one keeps (opened in a formatted view). Plus the alert signal queue.
   Raw files/tables stay reachable via the footer escape hatch + each view's Raw
   toggle, so nothing is lost. */
let PIPE = null, PIPE_STAGE = 0, DATA_REFS = [];

async function loadPipe(host) {
  if (PIPE) return true;
  host.innerHTML = `<div class="loading">loading…</div>`;
  try { PIPE = await fetchJSON('/api/pipeline'); return true; }
  catch (e) { host.innerHTML = card('Error', '<div class="muted">could not load data</div>'); return false; }
}

async function renderPipeline() {
  const host = $('#view-raw');
  if (!await loadPipe(host)) return;
  if (PIPE.error) { host.innerHTML = card('Pipeline error', `<pre class="json">${esc(PIPE.trace || PIPE.error)}</pre>`); return; }
  drawPipeline();
}

function drawPipeline() {
  const host = $('#view-raw');
  DATA_REFS = [];
  const stages = PIPE.stages || [];
  const ribbon = stages.map((s, i) =>
    `<button class="stage-chip ${i === PIPE_STAGE ? 'active' : ''}" data-stage="${i}">
       <span class="se">${s.emoji}</span><span class="sl"><b>${esc(s.title)}</b><small>${esc(s.tag)}</small></span>
     </button>`).join('<span class="stage-arrow">→</span>');

  const st = stages[PIPE_STAGE] || {};
  const mods = (st.modules || []).map(moduleCard).join('');
  const stageHead = `<div class="stage-head">
      <div class="sh-emoji">${st.emoji || ''}</div>
      <div class="sh-body"><div class="sh-title">${esc(st.title || '')}</div>
        <div class="sh-what">${esc(st.what || '')}</div>
        <div class="sh-tech mono">${esc(st.tech || '')}</div></div></div>`;

  host.innerHTML =
    `<div class="pipe-intro">🗺️ The whole system, stage by stage. Tap a stage to see what each part does in plain English — and open the data it keeps. No code required.</div>
     <div class="stage-ribbon">${ribbon}</div>
     <div class="stage-detail">${stageHead}<div class="grid cols-2 mods">${mods}</div></div>
     <div class="raw-foot"><button id="raw-browse" class="ghost-btn">⋯ Browse all raw files & tables</button></div>`;

  $$('#view-raw .stage-chip').forEach(b => b.onclick = () => {
    PIPE_STAGE = +b.dataset.stage; drawPipeline();
    $('#view-raw .stage-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  $$('#view-raw .dref[data-ref]').forEach(b => b.onclick = () => {
    const r = DATA_REFS[+b.dataset.ref];
    openData(r);
  });
  const rb = $('#raw-browse'); if (rb) rb.onclick = openRawBrowser;
}

/* --- open a data ref in a formatted modal (Plain | Raw) --- */
async function openData(ref) {
  modal(`<h3 style="margin:0 0 10px">${esc(ref.label)}</h3><div class="muted">loading…</div>`);
  let payload;
  try {
    if (ref.kind === 'inline') payload = { kind: 'inline', data: ref.data };
    else payload = await fetchJSON(`/api/raw/file?root=${encodeURIComponent(ref.root)}&path=${encodeURIComponent(ref.path)}&limit=400`);
  } catch (e) { modal(`<h3>${esc(ref.label)}</h3><div class="muted">failed to load</div>`); return; }
  if (payload && payload.error) { modal(`<h3>${esc(ref.label)}</h3><div class="muted">${esc(payload.error)}</div>`); return; }
  const plain = formatData(ref, payload);
  modal(`<div class="flex between mb"><h3 style="margin:0">${esc(ref.label)}</h3>
      <div class="seg"><button class="on" data-m="plain">Plain</button><button data-m="raw">Raw</button></div></div>
     ${ref.note ? `<div class="sub-line mb">${esc(ref.note)}</div>` : ''}
     <div id="d-plain">${plain}</div>
     <div id="d-raw" hidden><pre class="json">${rawOf(payload)}</pre></div>`);
  $$('#modal .seg button').forEach(bt => bt.onclick = () => {
    $$('#modal .seg button').forEach(x => x.classList.toggle('on', x === bt));
    $('#d-plain').hidden = bt.dataset.m !== 'plain';
    $('#d-raw').hidden = bt.dataset.m !== 'raw';
  });
}
function rawOf(p) {
  if (p.kind === 'jsonl') return hlJSON(p.rows);
  if (p.kind === 'json' || p.kind === 'inline') return hlJSON(p.data);
  return esc(p.text || p.error || 'empty');
}
function formatData(ref, p) {
  const rows = p.rows || [], data = p.data !== undefined ? p.data : null;
  try {
    switch (ref.format) {
      case 'mood': return fmtMood(data);
      case 'events': return fmtEvents(rows);
      case 'kv': return fmtKv(data);
      case 'reversion': return fmtReversion(rows);
      default: return fmtGeneric(p);
    }
  } catch (e) { return fmtGeneric(p); }
}

/* --- shape-aware formatters --- */
function moodWord(x) { return x <= -0.5 ? 'Bearish' : x < -0.15 ? 'Lean bearish' : x < 0.15 ? 'Flat / two-sided' : x < 0.5 ? 'Lean bullish' : 'Bullish'; }
function fmtMood(d) {
  if (!d || !d.overall) return '<div class="muted">no mood read</div>';
  const o = d.overall, dir = o.direction == null ? 0 : o.direction, needle = clamp((dir + 1) / 2 * 100, 1, 99);
  const secs = Object.entries(d.sectors || {}).map(([k, v]) => {
    const dd = v.direction || 0, w = clamp(Math.abs(dd) * 100, 4, 100), col = dd >= 0 ? 'var(--green)' : 'var(--red)';
    return `<div class="flex between" style="margin:6px 0"><span class="muted" style="width:84px">${esc(k)}</span>
      <div style="flex:1;height:8px;background:#1b2536;border-radius:5px;overflow:hidden;position:relative">
        <div style="position:absolute;${dd >= 0 ? 'left:50%' : 'right:50%'};top:0;bottom:0;width:${w / 2}%;background:${col}"></div>
        <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line2)"></div></div>
      <span class="mono" style="width:46px;text-align:right;color:${col}">${sig(dd)}</span></div>`;
  }).join('');
  return `<div class="sub-line mb">${esc(d.date || '')} · ${esc(d.reason || '')} · confidence ${num(o.confidence, 2)}</div>
    <div class="bignum sm">${esc(moodWord(dir))}</div>
    <div class="gauge"><div class="gauge-track"><div class="gauge-needle" style="left:${needle}%"></div></div>
      <div class="gauge-ends"><span>bearish</span><span>flat</span><span>bullish</span></div></div>
    <div class="mt">${secs}</div>
    ${d.reasoning ? `<div class="breath mt" style="font-size:14px">${esc(d.reasoning)}</div>` : ''}`;
}
function shortKeys(o) { return Object.keys(o).slice(0, 4).map(k => `${k}=${JSON.stringify(o[k])}`).join(' · ').slice(0, 160); }
function fmtEvents(rows) {
  if (!rows.length) return '<div class="muted">nothing recorded</div>';
  const items = rows.slice().reverse().map(e => {
    const ts = ptStamp(e.ts || e.time);
    const s = e.summary || e.message || e.reason || e.title || e.text || e.signal || e.kind || shortKeys(e);
    return `<div class="ev-row"><span class="ev-ts mono">${esc(ts)}</span><span class="ev-txt">${esc(String(s).slice(0, 220))}</span></div>`;
  }).join('');
  return `<div class="sub-line mb">${rows.length} record(s) · newest first</div><div class="ev-list">${items}</div>`;
}
function fmtKv(d) {
  if (!d || typeof d !== 'object') return fmtGeneric({ kind: 'json', data: d });
  const rows = [];
  (function walk(obj, pre) {
    for (const [k, v] of Object.entries(obj)) {
      if (k.startsWith('_')) continue;
      if (v && typeof v === 'object' && !Array.isArray(v)) walk(v, pre ? pre + '.' + k : k);
      else rows.push([pre ? pre + '.' + k : k, Array.isArray(v) ? v.join(', ') : String(v)]);
    }
  })(d, '');
  if (!rows.length) return '<div class="muted">empty</div>';
  const about = d._about ? `<div class="sub-line mb">${esc(d._about)}</div>` : '';
  return about + `<dl class="kv kv-wide">${rows.map(([k, v]) => `<dt class="mono">${esc(k)}</dt><dd class="mono">${esc(v)}</dd>`).join('')}</dl>`;
}
function fmtReversion(rows) {
  if (!rows.length) return '<div class="muted">no fade scans today</div>';
  const body = rows.slice(-80).reverse().map(x => {
    const re = x.reversion_extreme || {}, ts = ptHM(x.ts);
    const state = re.fired ? '🎯 fire' : re.armed ? '📏 armed' : '💤 idle';
    return `<tr><td class="mono">${esc(ts)}</td><td>${esc(x.ticker || '')}</td><td class="mono">${num(x.spot, 2)}</td>
      <td>${esc(x.regime || '')}</td><td class="num mono">${re.gap_stretch != null ? sig(re.gap_stretch) + 'σ' : '—'}</td><td>${state}</td></tr>`;
  }).join('');
  return `<div class="sub-line mb">${rows.length} fade scan(s) · paper/shadow · newest first</div>
    <div class="scroll-x"><table class="tbl"><thead><tr><th>time</th><th>tk</th><th>spot</th><th>regime</th><th class="num">stretch</th><th>state</th></tr></thead><tbody>${body}</tbody></table></div>`;
}
function fmtGeneric(p) {
  if (p.kind === 'jsonl') return `<div class="sub-line mb">${p.total} rows · last ${p.shown}</div>` + (p.rows || []).slice().reverse().map(r => `<pre class="json">${hlJSON(r)}</pre>`).join('');
  if (p.kind === 'json' || p.kind === 'inline') return `<pre class="json">${hlJSON(p.data)}</pre>`;
  if (p.kind === 'sqlite') return `<pre class="json">${hlJSON(p.rows)}</pre>`;
  return `<pre class="json">${esc(p.text || p.error || 'empty')}</pre>`;
}

/* --- raw escape hatch: the old file/table explorer, in a modal --- */
async function openRawBrowser() {
  modal(`<h3 style="margin:0 0 10px">⋯ Raw files & tables</h3><div class="muted">loading index…</div>`);
  let idx;
  try { idx = await fetchJSON('/api/raw/index'); }
  catch (e) { modal(`<h3>Raw files</h3><div class="muted">could not load</div>`); return; }
  const groups = [];
  for (const [label, items] of Object.entries(idx)) {
    if (label === 'sqlite' || !items || !items.length) continue;
    const list = items.slice().sort((a, b) => b.mtime - a.mtime).map(f =>
      `<div class="fileitem" data-root="${esc(label)}" data-rel="${esc(f.rel)}"><span class="fn">${esc(f.rel)}</span><span class="meta">${esc(f.ext)} · ${fmtSize(f.size)} · ${fmtAge(f.mtime)}</span></div>`).join('');
    groups.push(`<div class="group-h">${esc(label)} · ${items.length} files</div><div class="filelist">${list}</div>`);
  }
  modal(`<h3 style="margin:0 0 12px">⋯ Raw files & tables <span class="hint">the on-disk state behind every view</span></h3>${groups.join('')}`);
  $$('#modal .fileitem').forEach(it => it.onclick = () => {
    openFile(it.dataset.root, it.dataset.rel);
  });
}
async function openFile(root, rel) {
  modal(`<h3 style="margin:0 0 10px">${esc(rel)}</h3><div class="muted">loading…</div>`);
  try {
    const d = await fetchJSON(`/api/raw/file?root=${encodeURIComponent(root)}&path=${encodeURIComponent(rel)}&limit=300`);
    let body;
    if (d.kind === 'jsonl') {
      body = `<div class="sub-line mb">${d.total} rows · showing last ${d.shown}</div>` +
        d.rows.slice().reverse().map(r => `<pre class="json">${hlJSON(r)}</pre>`).join('');
    } else if (d.kind === 'json') {
      body = `<pre class="json">${hlJSON(d.data)}</pre>`;
    } else {
      body = `<pre class="json">${esc(d.text || d.error || 'empty')}</pre>`;
    }
    modal(`<h3 style="margin:0 0 10px">${esc(rel)}</h3>${body}`);
  } catch (e) { modal(`<h3>${esc(rel)}</h3><div class="muted">failed to load</div>`); }
}
const fmtSize = (b) => b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
function fmtAge(mtime) {
  const s = Date.now() / 1000 - mtime;
  if (s < 90) return 'just now'; if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago'; return Math.round(s / 86400) + 'd ago';
}

/* ---------------- modal ---------------- */
function modal(html) { $('#modal-body').innerHTML = html; $('#modal').hidden = false; }
function closeModal() { $('#modal').hidden = true; }

/* ---------------- JSON highlight ---------------- */
function hlJSON(obj) {
  let j = JSON.stringify(obj, null, 2);
  j = esc(j);
  j = j.replace(/("(\\.|[^"\\])*")(\s*:)?/g, (m, str, _g, colon) =>
    colon ? `<span class="json-key">${str}</span>${colon}` : `<span class="json-str">${str}</span>`);
  j = j.replace(/\b(-?\d+\.?\d*(e[+-]?\d+)?)\b/gi, '<span class="json-num">$1</span>');
  j = j.replace(/\b(true|false|null)\b/g, '<span class="json-bool">$1</span>');
  return j;
}

/* ---------------- tiny markdown (for the canonical prose panes) ---------------- */
function inline(t) {
  return esc(t)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/(^|[\s(])\*(?!\s)([^*]+?)\*/g, '$1<i>$2</i>')
    .replace(/(^|[\s(])_(?!\s)([^_]+?)_/g, '$1<i>$2</i>');
}
function extractCallout(md) {
  if (!md) return '';
  const lines = md.split('\n'); const out = [];
  let on = false;
  for (const ln of lines) {
    if (/^>\s*\[!/.test(ln)) { on = true; continue; }
    if (on) {
      if (/^>\s?/.test(ln)) out.push(ln.replace(/^>\s?/, '')); else break;
    }
  }
  return out.join(' ').trim();
}
function extractSection(md, heading) {
  if (!md) return '';
  const lines = md.split('\n'); const out = [];
  let on = false;
  for (const ln of lines) {
    if (/^#{1,4}\s/.test(ln)) {
      if (on) break;
      if (ln.replace(/^#+\s/, '').toLowerCase().includes(heading.toLowerCase())) { on = true; continue; }
    }
    if (on && ln.trim() && !/^[-=]{3,}$/.test(ln.trim())) out.push(ln.replace(/^>\s?/, '').replace(/^[-*]\s/, ''));
  }
  return out.join(' ').replace(/\s+/g, ' ').trim();
}

/* ---------------- boot ---------------- */
function boot() {
  $$('.tab').forEach(b => b.onclick = () => setView(b.dataset.view));
  $('#refresh').onclick = tick;
  $('#modal-close').onclick = closeModal;
  $('#modal').onclick = (e) => { if (e.target.id === 'modal') closeModal(); };
  // delegated scan-bar taps (timeline)
  document.addEventListener('click', (e) => {
    const bar = e.target.closest('.tl-bar');
    if (bar) openScan(bar.dataset.tk, +bar.dataset.i);
  });
  $$('.tab').forEach(b => b.classList.toggle('active', b.dataset.view === VIEW));
  $$('.view').forEach(s => s.hidden = ('view-' + VIEW) !== s.id);
  tick();
  // refresh only while the tab is visible/focused; resume + refetch on return
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(timer);
    else tick();
  });
}
document.addEventListener('DOMContentLoaded', boot);
