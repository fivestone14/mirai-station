// gesture_harness.js — drives the REAL page.js hold gesture with a fake clock,
// fake touch/pointer events and a fake history, and prints what happened.
//
// Why a harness and not a source grep: the first build of this gesture passed
// every source-level test while opening the sheet under a finger still on the
// glass — which turned the card into a dead zone for scrolling. Only running
// the events in order can see that. No browser and no DOM library: the gesture
// touches a handful of elements, so a handful are stubbed.
//
// Usage: node gesture_harness.js <path to static/m>   → one JSON object on stdout
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const M = process.argv[2];

let clock = 1e6, timers = [], nextId = 1;
const RealDate = Date;
function FakeDate(...a){ return a.length ? new RealDate(...a) : new RealDate(clock); }
FakeDate.now = () => clock;
FakeDate.parse = RealDate.parse;
function setTimeout_(fn, ms){ const id = nextId++; timers.push({id, at: clock + (ms || 0), fn}); return id; }
function clearTimeout_(id){ timers = timers.filter(t => t.id !== id); }
function advance(ms){
  const end = clock + ms;
  for(;;){
    timers.sort((a, b) => a.at - b.at);
    const t = timers[0];
    if(!t || t.at > end) break;
    timers.shift(); clock = t.at; t.fn();
  }
  clock = end;
}

function classList(){
  const s = new Set();
  return {add: (...c) => c.forEach(x => s.add(x)), remove: (...c) => c.forEach(x => s.delete(x)),
          contains: c => s.has(c), toggle: (c, on) => (on ? s.add(c) : s.delete(c))};
}
// `sel` lists the selectors an element answers to in closest()
function el(id, sel){
  return {id, sel: sel || [], attrs: {}, classList: classList(), hidden: false, style: {}, children: [],
          closest(q){ return this.sel.some(s => q.includes(s)) ? this : null; },
          setAttribute(k, v){ this.attrs[k] = v; }, getAttribute(k){ return this.attrs[k]; },
          hasAttribute(k){ return this.sel.some(s => s === '[' + k + ']'); },
          focus(){ document.activeElement = this; },
          replaceChildren(){}, appendChild(c){ return c; },
          getBoundingClientRect(){ return {width: 380, height: 300, top: 0, left: 0, right: 380, bottom: 300}; },
          set textContent(v){}, get textContent(){ return ''; }};
}
const els = {
  levels: el('levels', ['[data-hold]']),
  sheet: el('sheet', ['#sheet']),
  shClose: el('shClose', ['[data-sheet-close]']),
  scrim: el('scrim', ['[data-sheet-close]']),
  text: el('text', []),
};
const docL = {}, winL = {};
const on = (bag) => (type, fn) => { (bag[type] = bag[type] || []).push(fn); };
const document = {
  body: el('body'), activeElement: null, hidden: false,
  documentElement: {style: {setProperty(){}}},
  getElementById: id => els[id] || (els[id] = el(id)),
  querySelector: () => null, createElement: () => el('x'),
  addEventListener: on(docL),
};
let pushes = 0, backs = 0;
const history = {
  state: null,
  pushState(s){ this.state = s; pushes++; },
  // a real browser delivers popstate after a task, which is exactly the gap a
  // second close used to fall into
  back(){ backs++; setTimeout_(() => { history.state = null; (winL.popstate || []).forEach(f => f({})); }, 10); },
};
const ctx = {
  document, history, location: {search: ''}, URLSearchParams, navigator: {},
  fetch: () => new Promise(() => {}), setInterval: () => 0, clearInterval(){},
  setTimeout: setTimeout_, clearTimeout: clearTimeout_, Date: FakeDate, console,
  Math, JSON, Number, String, isFinite, Object, Array, Infinity, Set, Promise, encodeURIComponent,
  scrollY: 300,
};
ctx.window = ctx;
ctx.addEventListener = on(winL);
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(M, 'glance.js'), 'utf8'), ctx);
vm.runInContext(fs.readFileSync(path.join(M, 'page.js'), 'utf8'), ctx);

function fire(bag, type, e){ (bag[type] || []).forEach(f => f(e)); return e; }
function ev(type, target, x, y, extra){
  return Object.assign({type, target, clientX: x, clientY: y, isPrimary: true, pointerType: 'touch',
                        touches: [{clientX: x, clientY: y}], cancelable: true, defaultPrevented: false,
                        preventDefault(){ this.defaultPrevented = true; }}, extra || {});
}
const isOpen = () => document.body.classList.contains('sheet-open');
function reset(){
  if(isOpen()){ document.body.classList.remove('sheet-open'); }
  history.state = null; pushes = 0; backs = 0; timers = []; advance(1000);
}
// a finger on the card at (100, 100)
function down(){ fire(docL, 'pointerdown', ev('pointerdown', els.levels, 100, 100)); }
function move(dy){
  fire(docL, 'pointermove', ev('pointermove', els.levels, 100, 100 + dy));
  fire(docL, 'touchmove', ev('touchmove', els.levels, 100, 100 + dy));
}
function lift(){
  const e = fire(docL, 'touchend', ev('touchend', els.levels, 100, 100, {touches: []}));
  fire(docL, 'pointerup', ev('pointerup', els.levels, 100, 100));
  return e;
}

const out = {};

// 1. the timer ARMS; it never opens under a finger still on the glass
reset(); down(); advance(1000);
out.open_while_finger_down = isOpen();
out.armed_class = els.levels.classList.contains('armed');
const e1 = lift();
out.open_after_lift = isOpen();
out.lift_tap_cancelled = e1.defaultPrevented;
out.pushes_after_open = pushes;

// 2. rest on the card, then scroll: never opens
reset(); down(); advance(700); move(40); lift();
out.rest_then_scroll_opens = isOpen();

// 3. a quick scroll: never opens
reset(); down(); advance(120); move(40); advance(600); lift();
out.quick_scroll_opens = isOpen();

// 4. a tap: never opens
reset(); down(); advance(150); lift();
out.tap_opens = isOpen();

// 5. the browser claims the gesture for a pan before it arms: never opens
reset(); down(); advance(200); fire(docL, 'pointercancel', ev('pointercancel', els.levels, 100, 100)); advance(600); lift();
out.pan_claimed_opens = isOpen();

// 6. a long-press pointercancel AFTER it armed does not strand the hold
reset(); down(); advance(600); fire(docL, 'pointercancel', ev('pointercancel', els.levels, 100, 100)); lift();
out.armed_survives_pointercancel = isOpen();

// 7. the page scrolling under the finger cancels it
reset(); down(); advance(200); fire(winL, 'scroll', {}); advance(600); lift();
out.scroll_event_opens = isOpen();

// 8. the lift's late tap on the backdrop cannot close what it just opened
reset(); down(); advance(600); lift();
fire(docL, 'click', ev('click', els.scrim, 100, 100));
advance(20);
out.ghost_click_closes = !isOpen();

// 9. a real close after that window closes it, ONCE, however many taps
advance(600);
fire(docL, 'click', ev('click', els.shClose, 100, 100));
fire(docL, 'click', ev('click', els.shClose, 100, 100));     // double tap, before popstate
advance(50);
out.closed_by_button = !isOpen();
out.backs_on_double_tap = backs;

// 10. long-press menus are refused on the card and on the sheet's text
const cm1 = fire(docL, 'contextmenu', ev('contextmenu', els.levels, 0, 0));
const cm2 = fire(docL, 'contextmenu', ev('contextmenu', els.sheet, 0, 0));
const cm3 = fire(docL, 'contextmenu', ev('contextmenu', els.text, 0, 0));
out.contextmenu_blocked = [cm1.defaultPrevented, cm2.defaultPrevented, cm3.defaultPrevented];

console.log(JSON.stringify(out));
