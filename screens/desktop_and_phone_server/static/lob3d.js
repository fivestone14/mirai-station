/* ============================================================
   Day Visual LOB — the price x time board, in three.js.

   One cell per (price, time slice). It rises in JADE by how much size was
   ADDED at that price during that slice, and sinks in CORAL by how much was
   REMOVED. The slider walks the session: everything after the cursor is
   folded flat, so the board literally builds as the day goes.

   Two deliberate choices worth knowing before changing anything here:

   * HEIGHT IS SQRT-SCALED against the 95th percentile, not linear against the
     max. One 09:30 print can be forty times a quiet noon minute, and a linear
     scale spends the whole board drawing that one spike while every ordinary
     minute reads as zero. p95 also means the top 5% CLIP rather than compress
     everything below them, so a big minute still looks big.

   * REMOVED IS DRAWN POSITIVE-DOWN. Storing removals unsigned (contracts.py)
     and negating once, here, at the point of drawing, is the whole reason a
     consumer cannot accidentally draw a removal as an addition.

   The module is deferred, so index.html guards every call to window.LOB3D.
   ============================================================ */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

const PANEL_ID = 'p-lob3d';
const X_SPAN = 34;          // world units across the price axis
const MAX_H  = 3.2;         // tallest bar. Deliberately small next to the cell
                            // size: at MAX_H 9 with 390 one-minute slices the
                            // cells are 0.16 deep and the board renders as a
                            // forest whose front row hides the entire session
                            // behind it. A board has to be readable from above.

// Time depth is DERIVED so cells come out square — a "chessboard" with cells
// seven times wider than they are deep reads as a ribbon, not a board.

const S = {
  mounted:false, on:false, timer:0, view:null,
  doc:null, day:null, ticker:'SPX', slice_s:300, mode:'heat',
  k:0, playing:false, lastStep:0, showTrack:true,
  three:null, els:{}
};

function css(name, fallback){
  try{
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }catch(e){ return fallback; }
}

/* ---------- DOM ---------- */
function mount(){
  const host = document.getElementById(PANEL_ID);
  if(!host || S.mounted) return;
  host.innerHTML = `
    <div class="lob-wrap" style="display:flex;flex-direction:column;gap:10px;height:100%;min-height:0">
      <div class="lob-bar" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span class="lob-tick" title="This board is the S&amp;P 500 options tape. There is no SNDK book recorded yet.">SPX</span>
        <select id="lob-day"    class="lob-sel"></select>
        <select id="lob-slice"  class="lob-sel">
          <option value="30">30s slices</option>
          <option value="60">1m slices</option>
          <option value="120">2m slices</option>
          <option value="300" selected>5m slices</option>
        </select>
        <select id="lob-mode"   class="lob-sel">
          <option value="heat" selected>where the action is</option>
          <option value="traded">where trades printed</option>
          <option value="net">added and taken</option>
          <option value="rest">resting size</option>
        </select>
        <button id="lob-play"  class="lob-btn" type="button">▶ play</button>
        <button id="lob-track" class="lob-btn" type="button" aria-pressed="true">price ✓</button>
        <span id="lob-clock" style="font:600 13px var(--mono);color:var(--ink);min-width:96px"></span>
        <span id="lob-note"  style="font:400 12px var(--sans);color:var(--ink-faint)"></span>
        <span style="flex:1"></span>
        <span id="lob-legend" style="font:400 12px var(--sans);color:var(--ink-dim)"></span>
      </div>
      <input id="lob-time" type="range" min="0" max="0" value="0" step="1"
             style="width:100%;accent-color:var(--iris)">
      <div id="lob-stage" style="flex:1;min-height:0;position:relative;
           border:1px solid var(--hairline);border-radius:14px;overflow:hidden;
           background:var(--ground)">
        <div id="lob-msg" style="position:absolute;inset:0;display:flex;align-items:center;
             justify-content:center;font:400 13px var(--sans);color:var(--ink-dim);
             pointer-events:none;text-align:center;padding:20px"></div>
      </div>
    </div>
    <style>
      /* The instrument, said out loud. This panel lives in the SNDK rail group
         and draws S&P 500 data exclusively (S.ticker is 'SPX'; /api/lob/days
         returns no SNDK sessions), so without a label on screen a reader has
         every reason to believe they are looking at SanDisk. */
      .lob-tick{font:600 11px/1 var(--mono);letter-spacing:.14em;color:var(--ink-dim);
        background:var(--surface-2);border:1px solid var(--hairline-2);
        border-radius:7px;padding:6px 9px}
      .lob-sel,.lob-btn{background:var(--surface-2);color:var(--ink);border:1px solid var(--hairline);
        border-radius:9px;padding:6px 10px;font:500 12px var(--sans);cursor:pointer}
      .lob-btn:hover,.lob-sel:hover{border-color:var(--hairline-2)}
      #lob-stage canvas{display:block;width:100%;height:100%}
    </style>`;
  S.els = {
    day:host.querySelector('#lob-day'), slice:host.querySelector('#lob-slice'),
    mode:host.querySelector('#lob-mode'), play:host.querySelector('#lob-play'),
    clock:host.querySelector('#lob-clock'), note:host.querySelector('#lob-note'),
    time:host.querySelector('#lob-time'), stage:host.querySelector('#lob-stage'),
    msg:host.querySelector('#lob-msg'), legend:host.querySelector('#lob-legend'),
    track:host.querySelector('#lob-track')
  };
  syncLegend();
  S.els.day.addEventListener('change', ()=>{ S.day = S.els.day.value; loadBoard(); });
  S.els.slice.addEventListener('change', ()=>{ S.slice_s = +S.els.slice.value; loadBoard(); });
  S.els.mode.addEventListener('change', ()=>{ S.mode = S.els.mode.value; syncLegend(); rebuild(); });
  S.els.play.addEventListener('click', togglePlay);
  S.els.track.addEventListener('click', toggleTrack);
  S.els.time.addEventListener('input', ()=>{ S.playing=false; syncPlay(); setK(+S.els.time.value); });
  initThree();
  S.mounted = true;
  loadDays();
}

function msg(text){ if(S.els.msg) S.els.msg.textContent = text || ''; }

const AXES = 'across = price · into the screen = time';
const TRACK_KEY = ' &nbsp;·&nbsp; <b style="color:var(--ink)">━</b> where price actually was'
                + ' &nbsp; <b style="color:var(--iris)">━</b> vwap';
const LEGEND = {
  heat: AXES + ' &nbsp;·&nbsp; taller and hotter = more quoting going on at that price',
  traded: AXES + ' &nbsp;·&nbsp; taller and hotter = more CONTRACTS TRADED at that price',
  net:  AXES + ' &nbsp;·&nbsp; solid = what stayed &nbsp;·&nbsp; <b style="color:var(--coral)">outline</b> = what was taken',
  rest: AXES + ' &nbsp;·&nbsp; <b style="color:var(--jade)">▲</b> resting bids &nbsp; <b style="color:var(--coral)">▼</b> resting offers'
};
function syncLegend(){
  if(!S.els.legend) return;
  S.els.legend.innerHTML = (LEGEND[S.mode] || '') + (S.showTrack ? TRACK_KEY : '');
}

function toggleTrack(){
  S.showTrack = !S.showTrack;
  S.els.track.textContent = S.showTrack ? 'price ✓' : 'price';
  S.els.track.setAttribute('aria-pressed', String(S.showTrack));
  syncLegend();
  paint();
}

/* ---------- data ---------- */
async function loadDays(){
  msg('loading sessions…');
  try{
    const r = await fetch(`/api/lob/days?ticker=${encodeURIComponent(S.ticker)}`);
    const j = await r.json();
    const days = j.days || [];
    S.els.day.innerHTML = days.map(d=>`<option value="${d}">${d}</option>`).join('')
      || '<option value="">no sessions recorded</option>';
    if(!days.length){ msg('No recorded sessions yet.'); return; }
    S.day = days[0]; S.els.day.value = S.day;
    loadBoard();
  }catch(e){ msg('Could not list sessions: '+e); }
}

async function loadBoard(){
  if(!S.day) return;
  S.playing = false; syncPlay();
  msg('building board — a cold day takes a few seconds…');
  try{
    const r = await fetch(`/api/lob/board?ticker=${encodeURIComponent(S.ticker)}`
                          +`&day=${encodeURIComponent(S.day)}&slice_s=${S.slice_s}`);
    const doc = await r.json();
    if(doc.error){ S.doc=null; clearInstances(); msg(doc.error); return; }
    S.doc = doc;
    S.els.time.max = String(Math.max(0, (doc.slices||[]).length - 1));
    setK((doc.slices||[]).length - 1);
    rebuild();
    // A book feed records resting orders and never sees a trade, so `vol` is
    // zero on every bin. Drawing that as "where trades printed" would render an
    // empty board and read as a quiet session rather than as a missing feed.
    const canTrade = doc.kind === 'options_tape';
    const tradedOpt = S.els.mode.querySelector('option[value="traded"]');
    if(tradedOpt) tradedOpt.disabled = !canTrade;
    let fellBack = false;
    if(!canTrade && S.mode === 'traded'){
      S.mode = 'heat'; S.els.mode.value = 'heat'; syncLegend(); fellBack = true;
    }
    const live = (doc.slices||[]).filter(s=>s.n).length;
    S.els.note.textContent =
      `${doc.prices.length} prices · ${doc.slices.length} slices · ${live} with activity`
      + (canTrade ? '' : ' · this feed records orders, not trades');
    if(fellBack) msg('');
    msg('');
  }catch(e){ msg('Could not load the board: '+e); }
}

/* ---------- scene ---------- */
function initThree(){
  const stage = S.els.stage;
  const renderer = new THREE.WebGLRenderer({antialias:true, alpha:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio||1, 2));
  stage.appendChild(renderer.domElement);

  // The canvas has to be focusable or it never receives keydown, and it needs a
  // visible focus ring or a keyboard user cannot tell it is armed.
  const cv = renderer.domElement;
  cv.tabIndex = 0;
  cv.setAttribute('role', 'application');
  cv.setAttribute('aria-label',
    'Order book board: price across, time into the screen. '
    + 'Comma and period step through the session; arrow keys turn the view; '
    + 'p shows or hides the path price actually took.');
  cv.style.touchAction = 'none';
  cv.style.outline = 'none';

  // DOM text for the axis ticks, on a transparent overlay above the canvas.
  // Real text stays crisp at any zoom and at any display DPI, which a canvas
  // texture does not, and 40-odd labels is nowhere near where that stops being
  // the right trade.
  const labels = new CSS2DRenderer();
  const ld = labels.domElement;
  ld.style.position = 'absolute'; ld.style.top = '0'; ld.style.left = '0';
  ld.style.pointerEvents = 'none';          // or the overlay eats every drag
  stage.appendChild(ld);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(46, 1, 0.5, 4000);
  scene.add(new THREE.AmbientLight(0xffffff, 0.62));
  const key = new THREE.DirectionalLight(0xffffff, 0.85);
  key.position.set(24, 40, 18); scene.add(key);

  // depthWrite:false is load-bearing. A solid floor at y=0 writes depth and
  // every bar hanging below it vanishes, so a balanced board renders as
  // uniformly green.
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({color:new THREE.Color(css('--surface','#141A24')),
                                 transparent:true, opacity:0.30, depthWrite:false}));
  floor.rotation.x = -Math.PI/2; scene.add(floor);
  const grid = new THREE.GridHelper(1, 26,
                                    new THREE.Color(css('--hairline-2','#333E50')),
                                    new THREE.Color(css('--hairline','#28313F')));
  grid.material.transparent = true; grid.material.opacity = 0.5;
  grid.material.depthWrite = false;
  scene.add(grid);

  const cursor = new THREE.Mesh(
    new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshBasicMaterial({color:new THREE.Color(css('--iris','#8B9CFF'))}));
  scene.add(cursor);

  const geo = new THREE.BoxGeometry(1, 1, 1);
  const mkMesh = (hex)=>{
    const m = new THREE.InstancedMesh(
      geo, new THREE.MeshLambertMaterial({color:new THREE.Color(hex)}), 1);
    m.count = 0; m.frustumCulled = false; scene.add(m); return m;
  };
  const up   = mkMesh(css('--jade','#43C59E'));
  const down = mkMesh(css('--coral','#EB6A57'));
  // Heat gets its own mesh with a WHITE base: three multiplies the per-instance
  // colour by the material colour, so a tinted material would wash the whole
  // ramp toward that tint.
  const heat = new THREE.InstancedMesh(
    geo, new THREE.MeshLambertMaterial({color:0xffffff}), 1);
  heat.count = 0; heat.frustumCulled = false; scene.add(heat);

  // ONE LineSegments for every cell's cap, not one object per cell: 11,700
  // separate objects would be 11,700 draw calls and the tab would crawl.
  const wire = new THREE.LineSegments(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({color:new THREE.Color(css('--coral','#EB6A57')),
                                 transparent:true, opacity:0.85}));
  wire.frustumCulled = false; scene.add(wire);

  /* ---- the price path ---------------------------------------------------
     Three objects for one line, because a 3D board has no single angle that
     shows it. Look down the board and the floor is behind the front bars;
     look from the side and a line lying on the floor is edge-on and gone.

       band   a flat ribbon on the floor whose WIDTH is the range price
              covered inside that slice — visible close up, and in the same
              plane as the feet of every bar, so "was the action where price
              was?" is a straight comparison rather than a guess
       wall   a faint curtain rising off that ribbon, so the path can still be
              followed when the front of the board is in the way
       edge   a bright line along the top of the curtain, which is what you
              actually read when looking down at the board from above

     None of the three writes depth. The price path is an ANNOTATION over the
     data and must never hide a bar. -------------------------------------- */
  const bandMat = new THREE.MeshBasicMaterial({
    color:new THREE.Color(css('--ink','#E8ECF4')), transparent:true,
    opacity:0.92, depthWrite:false, side:THREE.DoubleSide});
  const band = new THREE.Mesh(new THREE.BufferGeometry(), bandMat);
  band.frustumCulled = false; band.renderOrder = 3; scene.add(band);

  const wall = new THREE.Mesh(new THREE.BufferGeometry(),
    new THREE.MeshBasicMaterial({color:new THREE.Color(css('--ink','#E8ECF4')),
      transparent:true, opacity:0.18, depthWrite:false, side:THREE.DoubleSide}));
  wall.frustumCulled = false; wall.renderOrder = 2; scene.add(wall);

  const edge = new THREE.LineSegments(new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({color:new THREE.Color(css('--ink','#E8ECF4')),
      transparent:true, opacity:0.95, depthWrite:false}));
  edge.frustumCulled = false; edge.renderOrder = 4; scene.add(edge);

  // vwap is the day's average price paid, drawn dimmer and in the station's
  // own accent so it can never be mistaken for spot. It is the answer to
  // "was this level dear or cheap by the standards of the session".
  // DASHED on purpose, and not just a second colour: spot and vwap run within a
  // few dollars of each other for most of a session, and two solid lines that
  // close together read as one line that got thicker. Dashes survive the
  // overlap — and they carry the right meaning, since vwap is a derived average
  // rather than a place the market actually was.
  const vwap = new THREE.LineSegments(new THREE.BufferGeometry(),
    new THREE.LineDashedMaterial({color:new THREE.Color(css('--iris','#8B9CFF')),
      transparent:true, opacity:0.85, depthWrite:false,
      dashSize:0.30, gapSize:0.22}));
  vwap.frustumCulled = false; vwap.renderOrder = 4; scene.add(vwap);

  // The path shows the shape of the day; this shows the number. It rides the
  // cursor for the same reason the price ticks do — the tab opens framed on the
  // last stretch of the session, and anything pinned to an edge is off-screen.
  const spotEl = document.createElement('div');
  spotEl.style.cssText = 'font:600 11px var(--mono);white-space:nowrap;user-select:none;'
    + 'padding:2px 6px;border-radius:5px;background:var(--ink);color:var(--ground);'
    + 'box-shadow:0 1px 6px rgba(0,0,0,.5)';
  const spotTag = new CSS2DObject(spotEl);
  spotTag.center.set(0.5, 1.35);
  scene.add(spotTag);

  const tickRoot = new THREE.Group(); scene.add(tickRoot);

  const controls = new OrbitControls(camera, cv);
  // SolidWorks hands: middle-drag turns the model, left-drag slides it, wheel
  // zooms at the cursor. Left is pan rather than rotate because on a board the
  // gesture you make a hundred times an hour is travel, not inspect-from-behind.
  controls.mouseButtons = {LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.ROTATE,
                           RIGHT: THREE.MOUSE.PAN};
  controls.touches = {ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_PAN};
  controls.screenSpacePanning = false;   // pan along the ground, map-style
  controls.zoomToCursor = true;          // point at 14:30 and zoom, no re-centring
  controls.enableDamping = true;
  controls.dampingFactor = 0.09;
  controls.minPolarAngle = 0.06;         // never perfectly flat — bars lose height
  controls.maxPolarAngle = 1.45;         // never at or under the floor
  controls.enableZoom = false;           // our wheel handler owns zoom AND pan
  controls.addEventListener('change', onControlsChange);

  S.three = {renderer, labels, scene, camera, controls, up, down, heat, wire, cursor,
             band, wall, edge, vwap, spotTag, spotEl,
             geo, floor, grid, tickRoot, ticks: [], bounds: new THREE.Box3()};

  attachWheel(cv);
  attachKeys(cv);
  layout(X_SPAN * 2, 1);                 // placeholder board while a day loads
  new ResizeObserver(resize).observe(stage);
  resize();
}

/* ---------- render on demand ----------

   Nothing renders unless something changed. `controls.update()` returns true
   while the camera is still coasting on damping, which is the exit condition:
   the loop runs for the ~20 frames of inertia after the mouse is released and
   then stops dead. An always-on rAF loop on a machine that sits in a cupboard
   all day is a fan that never spins down.
   -------------------------------------------------------------------------*/
let renderRequested = false;

function render(){
  if(renderRequested || !S.three) return;
  renderRequested = true;
  requestAnimationFrame(frame);
}

function frame(){
  renderRequested = false;
  const t = S.three; if(!t) return;
  const moving = t.controls.update();
  clampTarget();
  t.renderer.render(t.scene, t.camera);
  t.labels.render(t.scene, t.camera);
  if(moving) render();
}

function onControlsChange(){ render(); }

function clampTarget(){
  // Angle and distance limits stop you tumbling; only this stops you sliding
  // off the edge of the world and being unable to find the board again.
  const t = S.three; if(!t || t.bounds.isEmpty()) return;
  const tgt = t.controls.target, before = tgt.clone();
  t.bounds.clampPoint(tgt, tgt);
  if(!before.equals(tgt)) t.camera.position.add(tgt.clone().sub(before));
}

/* ---------- framing ---------- */

function fitDistance(box, camera, dir, pad = 1.08){
  // Exact for a box. The usual bounding-SPHERE fit treats a 34 x 450 board as
  // a 450-wide ball and pulls the camera miles back.
  const target = box.getCenter(new THREE.Vector3());
  const fwd = dir.clone().normalize();
  const right = new THREE.Vector3().crossVectors(camera.up, fwd).normalize();
  const up = new THREE.Vector3().crossVectors(fwd, right).normalize();
  const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
  const tanH = tanV * camera.aspect;
  const c = new THREE.Vector3();
  let d = 0;
  for(let i = 0; i < 8; i++){
    c.set(i & 1 ? box.max.x : box.min.x,
          i & 2 ? box.max.y : box.min.y,
          i & 4 ? box.max.z : box.min.z).sub(target);
    const x = c.dot(right), y = c.dot(up), z = c.dot(fwd);
    d = Math.max(d, z + Math.abs(y) / tanV, z + Math.abs(x) / tanH);
  }
  return d * pad;
}

// TRUE ISOMETRIC, and the numbers are not taste. phi = acos(1/sqrt(3)) and
// theta = 45 deg put the camera on the (1,1,1) diagonal, which is the one
// direction where the price axis and the time axis are foreshortened by exactly
// the same amount — so a cell's depth cannot be mistaken for its width, and two
// bars the same height read the same height wherever they sit on the board.
//
// It also fixes what the old down-the-length framing broke: with the camera
// nearly on the time axis (theta was -84 deg) the whole session stacked into a
// narrow corridor, the far half of the day was a smear, and the price axis —
// the axis every question on this board is actually about — was the one being
// compressed. Off the diagonal both axes open up.
const DEFAULT_VIEW = {theta: Math.PI / 4, phi: Math.acos(1 / Math.sqrt(3))};
const OPEN_SLICES = 18;      // how much of the session the tab opens framed on.
                             // Tighter than the old down-the-length framing needed:
                             // from the diagonal the camera sees BOTH axes, so a
                             // window that used to fill the frame now sits in the
                             // middle of it with the whole day around it.

function dirOf(theta, phi){
  return new THREE.Vector3(Math.sin(phi) * Math.cos(theta),
                           Math.cos(phi),
                           Math.sin(phi) * Math.sin(theta));
}

function windowBox(nSlices){
  // A whole session framed end to end makes every cell a speck. The tab opens
  // on the most recent stretch instead, where the detail is legible, and `f`
  // pulls back to the full day.
  const g = S.geomCache; if(!g) return null;
  const n = Math.min(nSlices, g.nz);
  const k = Math.max(0, Math.min(S.k, g.nz - 1));
  const z1 = -g.zSpan/2 + (k + 1) * g.cd;
  return new THREE.Box3(
    new THREE.Vector3(-X_SPAN/2, -MAX_H, z1 - n * g.cd),
    new THREE.Vector3( X_SPAN/2,  MAX_H, z1));
}

function fitToBoard(view, boxIn){
  const t = S.three; if(!t) return;
  const {camera, controls} = t;
  const box = boxIn ? boxIn.clone()
            : t.bounds.isEmpty() ? new THREE.Box3(
      new THREE.Vector3(-X_SPAN/2, -MAX_H, -X_SPAN),
      new THREE.Vector3( X_SPAN/2,  MAX_H,  X_SPAN)) : t.bounds.clone();
  const target = box.getCenter(new THREE.Vector3());
  const v = view || S.view || DEFAULT_VIEW;
  const dir = dirOf(v.theta, v.phi);
  const d = fitDistance(box, camera, dir);
  camera.position.copy(target).addScaledVector(dir, d);
  camera.near = Math.max(0.1, d * 0.004);
  camera.far = d * 6;
  camera.updateProjectionMatrix();
  controls.target.copy(target);
  controls.minDistance = d * 0.04;
  const full = t.bounds.isEmpty() ? d : fitDistance(t.bounds, camera, dir);
  controls.maxDistance = Math.max(d, full) * 3;
  controls.update();
  controls.saveState();           // Home returns HERE, not to a stale framing
  render();
}

function layout(zSpan, cd){
  const t = S.three; if(!t) return;
  t.floor.scale.set(X_SPAN, zSpan, 1);
  t.grid.scale.set(X_SPAN, 1, zSpan);
  t.cursor.scale.set(X_SPAN, 0.07, Math.max(cd * 0.5, 0.05));
  t.bounds.set(new THREE.Vector3(-X_SPAN/2, -MAX_H, -zSpan/2),
               new THREE.Vector3( X_SPAN/2,  MAX_H,  zSpan/2));
  t.panBounds = t.bounds.clone().expandByScalar(Math.max(8, zSpan * 0.05));
  if(S.fitFor !== zSpan){ S.fitFor = zSpan; fitToBoard(); }
}

function resize(){
  const t = S.three; if(!t) return;
  const {stage} = S.els;
  const w = stage.clientWidth || 1, h = stage.clientHeight || 1;
  t.renderer.setSize(w, h, false);
  t.labels.setSize(w, h);
  t.camera.aspect = w / h; t.camera.updateProjectionMatrix();
  // Aspect only — do NOT refit. A refit here fights the user: it fires after
  // the opening window fit and snaps back to the whole session, and it would
  // throw away any zoom the moment the pane changes size. `f` is the refit.
  if(!S.framedFor) fitToBoard(S.view || DEFAULT_VIEW);
  render();
}


/* ---------- SolidWorks-style hands ----------

   Mouse:  middle-drag turns · left/right-drag slides · wheel zooms at cursor
           ctrl+middle slides · shift+middle zooms
   Keys:   arrows turn in 15 deg steps · shift+arrows 90 deg · ctrl+arrows slide
           , . step the session · < > jump ten · 1-4 standard views
           f fit · 0 or Home reset
   -------------------------------------------------------------------------*/

const ROT_STEP = Math.PI / 12;          // 15 deg, SolidWorks' own arrow-key step

function attachWheel(cv){
  cv.addEventListener('wheel', e => {
    const t = S.three; if(!t) return;
    e.preventDefault();                 // or macOS page-zooms the whole browser
    const c = t.controls;
    if(e.ctrlKey || e.metaKey || e.shiftKey){
      // A macOS trackpad pinch arrives as a wheel event with ctrlKey set.
      // Exponential in distance, so the zoom rate feels the same whether you
      // are looking at the whole day or at one cell — and proportional to the
      // delta, so a flick and a shove are not the same step.
      const k = Math.pow(0.95, Math.abs(e.deltaY * 0.01) * 2.2);
      dolly(e.deltaY < 0 ? k : 1 / k, e);
    } else {
      // Two-finger drag TRAVELS. On a 450-deep board that is the gesture you
      // want a hundred times an hour; zoom is the pinch.
      panByPixels(e.deltaX, e.deltaY);
    }
    c.update();
    render();
  }, {passive:false});
}

function dolly(scale, e){
  const {camera, controls} = S.three;
  const off = camera.position.clone().sub(controls.target);
  let d = off.length() / scale;
  d = Math.min(controls.maxDistance, Math.max(controls.minDistance, d));
  camera.position.copy(controls.target).add(off.setLength(d));
}

function panByPixels(dx, dy){
  const t = S.three; if(!t) return;
  const {camera, controls} = t;
  const el = t.renderer.domElement;
  const dist = camera.position.distanceTo(controls.target);
  const worldPerPx = 2 * dist * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)
                     / Math.max(el.clientHeight, 1);
  const m = camera.matrix.elements;
  const right = new THREE.Vector3(m[0], m[1], m[2]);
  // pan along the GROUND, not the screen plane: on a tilted view a screen-plane
  // pan lifts the camera off the board and the floor slides out from under you
  const fwd = new THREE.Vector3(m[8], m[9], m[10]);
  fwd.y = 0; fwd.normalize();
  const move = right.multiplyScalar(-dx * worldPerPx)
                    .add(fwd.multiplyScalar(dy * worldPerPx));
  camera.position.add(move);
  controls.target.add(move);
}

function orbitBy(dTheta, dPhi){
  const t = S.three; if(!t) return;
  const {camera, controls} = t;
  const off = camera.position.clone().sub(controls.target);
  const sph = new THREE.Spherical().setFromVector3(off);
  sph.theta += dTheta;
  sph.phi = Math.min(controls.maxPolarAngle,
                     Math.max(controls.minPolarAngle, sph.phi + dPhi));
  camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(sph));
  controls.update();
  render();
}

// 1 top · 2 front (down the price axis) · 3 side (down the time axis) · 4 home
const VIEWS = {
  Digit1: {theta: -Math.PI / 2,    phi: 0.10},
  Digit2: {theta: -Math.PI / 2,    phi: 1.40},
  Digit3: {theta: 0,               phi: 1.40},
  Digit4: DEFAULT_VIEW
};

function attachKeys(cv){
  // OrbitControls preventDefault()s pointerdown, which also cancels the browser's
  // default "focus the thing you clicked". Without this the canvas never takes
  // focus and not one keyboard shortcut fires.
  cv.addEventListener('pointerdown', () => cv.focus());

  cv.addEventListener('keydown', e => {
    if(e.metaKey) return;                       // leave cmd-R, cmd-F etc alone
    const t = S.three; if(!t) return;
    const step = e.shiftKey ? Math.PI / 2 : ROT_STEP;
    let hit = true;

    switch(e.code){
      case 'ArrowLeft':  case 'ArrowRight': {
        const dir = e.code === 'ArrowLeft' ? -1 : 1;
        if(e.ctrlKey || e.altKey) panByPixels(dir * 60, 0);
        else orbitBy(dir * step, 0);
        break;
      }
      case 'ArrowUp': case 'ArrowDown': {
        const dir = e.code === 'ArrowUp' ? -1 : 1;
        if(e.ctrlKey || e.altKey) panByPixels(0, dir * -60);
        else orbitBy(0, dir * step);
        break;
      }
      case 'Comma':  setK(S.k - (e.shiftKey ? 10 : 1)); break;
      case 'Period': setK(S.k + (e.shiftKey ? 10 : 1)); break;
      case 'KeyF':
        // f frames the recent stretch; shift-F pulls back to the whole session
        fitToBoard(S.view || DEFAULT_VIEW, e.shiftKey ? null : windowBox(OPEN_SLICES));
        break;
      case 'Home':
      case 'Digit0': S.view = DEFAULT_VIEW; fitToBoard(DEFAULT_VIEW); break;
      case 'Digit1': case 'Digit2': case 'Digit3': case 'Digit4':
        S.view = VIEWS[e.code]; fitToBoard(S.view); break;
      case 'KeyP':   toggleTrack(); break;
      case 'Space':  togglePlay(); break;
      default: hit = false;
    }
    if(hit){ e.preventDefault(); render(); }
  });

  // modifier-swapped middle button, the SolidWorks way. OrbitControls has no
  // native modifier remap, so the button map is swapped while the key is held.
  const set = (mid) => { if(S.three) S.three.controls.mouseButtons.MIDDLE = mid; };
  window.addEventListener('keydown', e => {
    if(!S.on) return;
    if(e.key === 'Control') set(THREE.MOUSE.PAN);
    else if(e.key === 'Shift') set(THREE.MOUSE.DOLLY);
  });
  window.addEventListener('keyup', e => {
    if(e.key === 'Control' || e.key === 'Shift') set(THREE.MOUSE.ROTATE);
  });
}

/* ---------- axis labels ---------- */

function clearTicks(){
  const t = S.three; if(!t) return;
  for(const o of t.ticks){
    t.tickRoot.remove(o);
    if(o.element && o.element.remove) o.element.remove();
  }
  t.ticks = [];
}

function tick(text, cls){
  const el = document.createElement('div');
  el.textContent = text;
  // a chip, not bare text: CSS2D labels have no depth test, so a tick landing
  // over the bars is unreadable without something behind it
  el.style.cssText = 'font:500 ' + (cls === 'axis' ? '11px' : '10px') + ' var(--mono);'
    + 'white-space:nowrap;user-select:none;padding:1px 4px;border-radius:4px;'
    + 'background:color-mix(in srgb, var(--ground) 78%, transparent);'
    + 'color:' + (cls === 'axis' ? 'var(--ink-dim)' : 'var(--ink-faint)')
    + (cls === 'axis' ? ';letter-spacing:.06em;text-transform:uppercase' : '');
  const o = new CSS2DObject(el);
  o.center.set(0.5, 0.5);
  return o;
}

function buildTicks(prices, slices, g){
  const t = S.three; if(!t) return;
  clearTicks();
  const add = (o) => { t.tickRoot.add(o); t.ticks.push(o); };

  // Roughly ten labels per axis, whatever the board's size. Every price on a
  // 60-wide board is unreadable; three on a 5-wide board is useless.
  // Price ticks RIDE THE CURSOR rather than sitting at the board's far edge.
  // The tab opens framed on the most recent stretch, so an edge-pinned price
  // axis is off-screen exactly when you need it.
  t.priceTicks = [];
  const pEvery = Math.max(1, Math.ceil(prices.length / 6));
  for(let i = 0; i < prices.length; i += pEvery){
    const o = tick(fmtPrice(prices[i]));
    o.position.set(-X_SPAN/2 + (i + 0.5) * g.cw, 0.1, 0);
    add(o); t.priceTicks.push(o);
  }
  const tEvery = Math.max(1, Math.ceil(slices.length / 8));
  for(let z = 0; z < slices.length; z += tEvery){
    const o = tick(String(slices[z].t).slice(11, 16));
    o.position.set(-X_SPAN/2 - g.cw * 2.4, 0.1, -g.zSpan/2 + (z + 0.5) * g.cd);
    add(o);
  }
  // Axis TITLES live in the toolbar, not the scene. A label pinned to the
  // middle of an axis has no safe position: orbit the board and it lands on
  // top of the bars, and CSS2D labels have no depth test to hide behind them.
  render();
}

function fmtPrice(v){
  return Math.abs(v) >= 1000 ? String(Math.round(v))
       : Math.abs(v) >= 10   ? v.toFixed(2).replace(/\.00$/, '')
                             : v.toFixed(2);
}

const _c = new THREE.Color(), _cA = new THREE.Color(), _cB = new THREE.Color(),
      _cC = new THREE.Color();

function heatColor(t){
  // cool -> warm -> hot. Read from the station's own palette so the board
  // stays in the same world as every other screen, and so it flips with the
  // light/dark theme instead of being hardcoded to the dark one.
  _cA.set(css('--iris', '#8B9CFF'));
  _cB.set(css('--lantern', '#E7B85C'));
  _cC.set(css('--coral', '#EB6A57'));
  return t < 0.5 ? _c.copy(_cA).lerp(_cB, t * 2)
                 : _c.copy(_cB).lerp(_cC, (t - 0.5) * 2);
}

/* ---------- geometry build ----------

   Everything for the WHOLE session is built once, in slice order, and the
   slider then just truncates it: `mesh.count` for the solid bars,
   `setDrawRange` for the wireframe. Rewriting 11,700 matrices on every slider
   step made scrubbing lurch; truncating a prebuilt buffer is one integer.
   -------------------------------------------------------------------------*/
function clearInstances(){
  if(!S.three) return;
  S.three.up.count = 0; S.three.down.count = 0; S.three.heat.count = 0;
  S.three.wire.geometry.setDrawRange(0, 0);
  for(const o of [S.three.band, S.three.wall, S.three.edge, S.three.vwap])
    o.geometry.setDrawRange(0, 0);
  S.trackCache = null;
  if(S.three.spotTag) S.three.spotTag.visible = false;
  render();
}

function pct(values, p){
  if(!values.length) return 1;
  const a = values.slice().sort((x,y)=>x-y);
  return a[Math.min(a.length-1, Math.floor(a.length*p))] || 1;
}

// sqrt against the 95th percentile. One 09:30 print can be forty times a quiet
// noon minute; a linear scale spends the whole board drawing that spike.
function hOf(v, scale){ return MAX_H * Math.min(1, Math.sqrt(Math.max(v,0) / scale)); }

const _m = new THREE.Matrix4(), _q = new THREE.Quaternion(),
      _p = new THREE.Vector3(), _s = new THREE.Vector3();

function pushBoxEdges(arr, o, cx, cz, y0, y1, bw, bd){
  const x0 = cx - bw/2, x1 = cx + bw/2, z0 = cz - bd/2, z1 = cz + bd/2;
  const c = [[x0,z0],[x1,z0],[x1,z1],[x0,z1]];
  for(let i=0;i<4;i++){                       // bottom ring, top ring, uprights
    const a = c[i], b = c[(i+1)%4];
    arr[o++]=a[0]; arr[o++]=y0; arr[o++]=a[1]; arr[o++]=b[0]; arr[o++]=y0; arr[o++]=b[1];
    arr[o++]=a[0]; arr[o++]=y1; arr[o++]=a[1]; arr[o++]=b[0]; arr[o++]=y1; arr[o++]=b[1];
    arr[o++]=a[0]; arr[o++]=y0; arr[o++]=a[1]; arr[o++]=a[0]; arr[o++]=y1; arr[o++]=a[1];
  }
  return o;
}

/* ---------- the price path ----------

   The board's price axis is a list of BINS, and trim_bins keeps only the
   busiest of them — so the axis is ordered but not guaranteed evenly spaced,
   and a real price almost never lands exactly on one. Both facts are handled
   here and nowhere else: everything downstream works in world units.
   ------------------------------------------------------------------------*/
function xOfPrice(price, prices, cw){
  const n = prices.length;
  if(!n) return null;
  if(price <= prices[0])     return -X_SPAN/2 + 0.5 * cw;
  if(price >= prices[n - 1]) return -X_SPAN/2 + (n - 0.5) * cw;
  let lo = 0, hi = n - 1;
  while(hi - lo > 1){ const m = (lo + hi) >> 1; if(prices[m] <= price) lo = m; else hi = m; }
  const span = prices[hi] - prices[lo];
  const f = span > 0 ? (price - prices[lo]) / span : 0;
  return -X_SPAN/2 + (lo + f + 0.5) * cw;
}

function buildTrack(track, prices, g){
  const t = S.three; if(!t) return null;
  const clear = () => {
    for(const o of [t.band, t.wall, t.edge, t.vwap]) o.geometry.setDrawRange(0, 0);
    return null;
  };
  if(!track || !track.last) return clear();

  const {nz, cw, cd, zSpan} = g;
  const yTop = MAX_H * 1.08;
  // In "added and taken" the board hangs below the floor, so the curtain has to
  // reach down through it or the path floats over a hole.
  const yBot = (S.mode === 'net') ? -MAX_H * 1.08 : 0;
  const yBand = 0.014;                     // just off the floor, never in it
  const minW  = cw * 0.55;                 // a slice where price sat still is
                                           // still a slice you have to be able to see
  const X = new Array(nz);
  for(let z = 0; z < nz; z++){
    const v = track.last[z];
    X[z] = (v == null) ? null : xOfPrice(v, prices, cw);
  }

  const bandArr = new Float32Array(nz * 18);       // 2 triangles
  const wallArr = new Float32Array(nz * 18);
  const edgeArr = new Float32Array(nz * 6);        // 1 segment
  const vwapArr = new Float32Array(nz * 6);
  const bandEnd = new Int32Array(nz), wallEnd = new Int32Array(nz),
        edgeEnd = new Int32Array(nz), vwapEnd = new Int32Array(nz);
  let bo = 0, wo = 0, eo = 0, vo = 0;

  const push = (arr, o, x, y, zz) => { arr[o++]=x; arr[o++]=y; arr[o++]=zz; return o; };

  for(let z = 0; z < nz; z++){
    const cz = -zSpan/2 + (z + 0.5) * cd;
    const z0 = cz - cd/2, z1 = cz + cd/2;

    // the floor ribbon: how much ground price covered inside this slice
    if(X[z] != null){
      const lv = track.lo[z], hv = track.hi[z];
      let xa = (lv == null) ? X[z] : xOfPrice(lv, prices, cw);
      let xb = (hv == null) ? X[z] : xOfPrice(hv, prices, cw);
      if(xb < xa){ const s2 = xa; xa = xb; xb = s2; }
      if(xb - xa < minW){ const m = (xa + xb) / 2; xa = m - minW/2; xb = m + minW/2; }
      bo = push(bandArr, bo, xa, yBand, z0); bo = push(bandArr, bo, xb, yBand, z0);
      bo = push(bandArr, bo, xb, yBand, z1);
      bo = push(bandArr, bo, xa, yBand, z0); bo = push(bandArr, bo, xb, yBand, z1);
      bo = push(bandArr, bo, xa, yBand, z1);
    }

    // the curtain and its bright top edge, joining this slice to the last one
    if(z > 0 && X[z] != null && X[z-1] != null){
      const pz = cz - cd, xa = X[z-1], xb = X[z];
      wo = push(wallArr, wo, xa, yBot, pz); wo = push(wallArr, wo, xb, yBot, cz);
      wo = push(wallArr, wo, xb, yTop, cz);
      wo = push(wallArr, wo, xa, yBot, pz); wo = push(wallArr, wo, xb, yTop, cz);
      wo = push(wallArr, wo, xa, yTop, pz);
      eo = push(edgeArr, eo, xa, yTop, pz); eo = push(edgeArr, eo, xb, yTop, cz);
    }

    // vwap rides at the same height as the spot edge on purpose: the two only
    // mean anything against each other, and that comparison is along the PRICE
    // axis, so putting them at different heights would only make it harder.
    if(z > 0 && track.vwap[z] != null && track.vwap[z-1] != null){
      const pz = cz - cd, y = yTop - 0.015;
      vo = push(vwapArr, vo, xOfPrice(track.vwap[z-1], prices, cw), y, pz);
      vo = push(vwapArr, vo, xOfPrice(track.vwap[z],   prices, cw), y, cz);
    }
    bandEnd[z] = bo/3; wallEnd[z] = wo/3; edgeEnd[z] = eo/3; vwapEnd[z] = vo/3;
  }

  const set = (obj, arr, o) => {
    obj.geometry.setAttribute('position',
      new THREE.BufferAttribute(arr.subarray(0, o), 3));
    obj.geometry.attributes.position.needsUpdate = true;
  };
  set(t.band, bandArr, bo); set(t.wall, wallArr, wo);
  set(t.edge, edgeArr, eo); set(t.vwap, vwapArr, vo);
  t.vwap.computeLineDistances();      // dashes are computed, not free
  return {bandEnd, wallEnd, edgeEnd, vwapEnd, X, yTop, last: track.last};
}

function rebuild(){
  const doc = S.doc; if(!doc || !S.three) return;
  const prices = doc.prices || [], slices = doc.slices || [];
  const nx = prices.length, nz = slices.length;
  if(!nx || !nz){ clearInstances(); msg('Nothing recorded for this session.'); return; }

  const F = doc.fields || [], ix = n => F.indexOf(n);
  const A1 = ix('bid_add'), A2 = ix('ask_add'), R1 = ix('bid_rem'), R2 = ix('ask_rem');
  const T1 = ix('bid_rest'), T2 = ix('ask_rest'), V = ix('vol');
  const g0 = (c, i) => (i >= 0 && c && c[i]) || 0;

  // Each cell yields [solid, wire]: how much SURVIVED the slice (signed), and
  // how much was TAKEN OUT of it. Solid + wire together stand for everything
  // that appeared at that price, so the wireframe cap literally shows the bite
  // taken out of the bar.
  const traded = S.mode === 'traded';
  const rest = S.mode === 'rest', hot = traded || S.mode === 'heat';
  // Heat asks a question the book CAN answer: how much went on at this price.
  // Adds and removes both count, because a price where size is being posted and
  // pulled hard is busy whichever way it nets out — and netting them to zero is
  // exactly how a violently contested price ends up looking asleep.
  // `traded` is the only mode that draws CONTRACTS THAT CHANGED HANDS. Every
  // other mode draws quoting — size posted and pulled — which is a far larger
  // number and a completely different claim about the day.
  const cell = traded
    ? c => [g0(c,V), 0]
    : hot
    ? c => [g0(c,A1)+g0(c,A2)+g0(c,R1)+g0(c,R2), 0]
    : rest
    ? c => [g0(c,T1) - g0(c,T2), 0]
    : c => { const a = g0(c,A1)+g0(c,A2), r = g0(c,R1)+g0(c,R2);
             return [a - r, r]; };

  const mags = [];
  for(const sl of slices) for(const c of (sl.c||[])){
    if(!c) continue;
    const [n, w] = cell(c);
    const top = (rest || hot) ? Math.abs(n) : Math.max(Math.abs(n), Math.abs(n) + w);
    if(top > 0) mags.push(top);
  }
  const scale = pct(mags, 0.95) || 1;

  const cw = X_SPAN / Math.max(nx, 1), cd = cw, zSpan = cd * nz;
  const bw = cw * 0.82, bd = cd * 0.82;
  layout(zSpan, cd);

  const cap = nx * nz;
  S.three.up   = resize_mesh(S.three.up,   cap);
  S.three.down = resize_mesh(S.three.down, cap);
  S.three.heat = resize_mesh(S.three.heat, cap);
  const wireArr = new Float32Array(cap * 72);     // 12 edges x 2 verts x 3
  const upEnd = new Int32Array(nz), downEnd = new Int32Array(nz),
        wireEnd = new Int32Array(nz);

  let ui = 0, di = 0, wo = 0, hi = 0;
  for(let z = 0; z < nz; z++){
    const cells = slices[z].c || [];
    const cz = -zSpan/2 + (z + 0.5) * cd;
    for(let x = 0; x < nx; x++){
      const c = cells[x]; if(!c) continue;
      const [net, taken] = cell(c);
      const cx = -X_SPAN/2 + (x + 0.5) * cw;
      if(hot){
        if(net > 0){
          const t = Math.min(1, Math.sqrt(net / scale));
          const h = Math.max(MAX_H * t, 0.03);
          _p.set(cx, h/2, cz); _s.set(bw, h, bd);
          S.three.heat.setMatrixAt(hi, _m.compose(_p, _q, _s));
          S.three.heat.setColorAt(hi, heatColor(t));
          hi++;
        }
        continue;
      }
      const netY = net >= 0 ? hOf(net, scale) : -hOf(-net, scale);
      if(net !== 0){
        const h = Math.max(Math.abs(netY), 0.02);
        _p.set(cx, netY/2, cz); _s.set(bw, h, bd);
        if(net > 0) S.three.up.setMatrixAt(ui++, _m.compose(_p, _q, _s));
        else        S.three.down.setMatrixAt(di++, _m.compose(_p, _q, _s));
      }
      if(taken > 0){
        // the cap runs from the top of what survived up to everything that
        // appeared, so its HEIGHT is the amount taken and its POSITION shows
        // where in the bar it was taken from
        const topY = hOf(Math.max(net, 0) + taken, scale);
        if(topY - netY > 0.01) wo = pushBoxEdges(wireArr, wo, cx, cz, netY, topY, bw, bd);
      }
    }
    upEnd[z] = hot ? hi : ui; downEnd[z] = di; wireEnd[z] = wo / 3;
  }

  S.three.up.instanceMatrix.needsUpdate = true;
  S.three.down.instanceMatrix.needsUpdate = true;
  S.three.heat.instanceMatrix.needsUpdate = true;
  if(S.three.heat.instanceColor) S.three.heat.instanceColor.needsUpdate = true;
  S.hot = hot;
  const wg = S.three.wire.geometry;
  wg.setAttribute('position', new THREE.BufferAttribute(wireArr.subarray(0, wo), 3));
  wg.attributes.position.needsUpdate = true;

  S.geomCache = {nx, nz, cw, cd, bw, bd, scale, zSpan, upEnd, downEnd, wireEnd};
  S.trackCache = buildTrack(doc.track, prices, S.geomCache);
  buildTicks(prices, slices, S.geomCache);
  paint();
  if(S.framedFor !== doc.day + doc.slice_s + S.mode){
    S.framedFor = doc.day + doc.slice_s + S.mode;
    fitToBoard(S.view || DEFAULT_VIEW, windowBox(OPEN_SLICES));
  }
}

function resize_mesh(old, count){
  if(old.instanceMatrix.count >= count){ old.count = 0; return old; }
  const m = new THREE.InstancedMesh(old.geometry, old.material, count);
  m.count = 0; m.frustumCulled = false;
  old.parent.add(m); old.parent.remove(old); old.dispose();
  return m;
}

function paint(){
  const g = S.geomCache; if(!g || !S.three) return;
  const k = Math.max(0, Math.min(S.k, g.nz - 1));
  S.three.up.count   = S.hot ? 0 : g.upEnd[k];
  S.three.heat.count = S.hot ? g.upEnd[k] : 0;
  S.three.down.count = g.downEnd[k];
  S.three.wire.geometry.setDrawRange(0, g.wireEnd[k]);

  // The path is clipped to the cursor for the same reason the bars are: the
  // board is meant to read as the day BUILDING, and a price line already drawn
  // out to the close would give away every move the bars have not reached yet.
  const tc = S.trackCache, on = S.showTrack && !!tc;
  S.three.band.geometry.setDrawRange(0, on ? tc.bandEnd[k] : 0);
  S.three.wall.geometry.setDrawRange(0, on ? tc.wallEnd[k] : 0);
  S.three.edge.geometry.setDrawRange(0, on ? tc.edgeEnd[k] : 0);
  S.three.vwap.geometry.setDrawRange(0, on ? tc.vwapEnd[k] : 0);
  const sx = on ? tc.X[k] : null, sv = on ? tc.last[k] : null;
  S.three.spotTag.visible = sx != null && sv != null;
  if(S.three.spotTag.visible){
    S.three.spotTag.position.set(sx, tc.yTop, -g.zSpan/2 + (k + 0.5) * g.cd);
    S.three.spotEl.textContent = fmtPrice(sv);
  }

  const cz = -g.zSpan/2 + (k + 0.5) * g.cd;
  S.three.cursor.position.z = cz;
  for(const o of (S.three.priceTicks || [])) o.position.z = cz + g.cd * 2.5;
  render();
}


/* ---------- time ---------- */
function setK(k){
  const n = ((S.doc && S.doc.slices) || []).length;
  S.k = Math.max(0, Math.min(k, n - 1));
  if(S.els.time) S.els.time.value = String(S.k);
  const s = S.doc && S.doc.slices && S.doc.slices[S.k];
  if(S.els.clock && s){
    const t = String(s.t).slice(11, 16);
    S.els.clock.textContent = `${t}  ·  ${S.k + 1}/${n}`;
  }
  paint();
}

function togglePlay(){
  S.playing = !S.playing;
  syncPlay();
  if(S.playing){
    // a timer, not requestAnimationFrame: playback advances 14 slices a second
    // and does not need a 60 Hz wake-up to do it
    S.timer = setInterval(() => {
      const n = ((S.doc && S.doc.slices) || []).length;
      if(!S.playing || S.k >= n - 1){ S.playing = false; syncPlay(); stopTimer(); return; }
      setK(S.k + 1);
    }, 70);
  } else stopTimer();
}
function stopTimer(){ if(S.timer){ clearInterval(S.timer); S.timer = 0; } }
function syncPlay(){ if(S.els.play) S.els.play.textContent = S.playing ? '❙❙ pause' : '▶ play'; }


/* ---------- lifecycle ---------- */
window.LOB3D = {
  get mounted(){ return S.mounted; },
  show(){
    mount();
    S.on = true;
    resize();
    render();
  },
  hide(){
    // stop the clock cold when the panel is not showing: the station runs all
    // day on a mac mini and a hidden rAF loop is a fan that never spins down
    S.on = false; S.playing = false; syncPlay(); stopTimer();
  }
};
