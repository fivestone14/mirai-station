// gesture_harness.js — drives the REAL page.js tap on the link under the chart,
// and the sheet.js sheet it opens, with a fake clock, fake taps and a fake
// history, and prints what happened.
//
// Why a harness and not a source grep: the first build of the glance's old
// press-and-hold passed every source-level test while opening its sheet under
// a finger still on the glass — which turned the card into a dead zone for
// scrolling. Only running the events in order can see that, and the same goes
// for a double open or a close that goes back twice. No browser and no DOM
// library: the sheet touches a handful of elements, so a handful are stubbed.
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
// and `attrs` the attributes the markup gives it
function el(id, sel, attrs){
  return {id, sel: sel || [], attrs: Object.assign({}, attrs), classList: classList(), hidden: false, style: {},
          children: [], heard: {},
          closest(q){ return this.sel.some(s => q.includes(s)) ? this : null; },
          setAttribute(k, v){ this.attrs[k] = v; }, getAttribute(k){ return this.attrs[k]; },
          hasAttribute(k){ return this.sel.some(s => s === '[' + k + ']'); },
          addEventListener(t, f){ (this.heard[t] = this.heard[t] || []).push(f); },
          // a sheet's close button, the one thing sheet.js looks for inside it
          querySelector(q){ return q === '[data-sheet-close]' ? els.hwClose : null; },
          focus(){ document.activeElement = this; },
          replaceChildren(){}, appendChild(c){ return c; },
          getBoundingClientRect(){ return {width: 380, height: 300, top: 0, left: 0, right: 380, bottom: 300}; },
          set textContent(v){}, get textContent(){ return ''; }};
}
const els = {
  howto: el('howto', ['#howto'], {'aria-controls': 'howtoSheet'}),
  howtoSheet: el('howtoSheet', ['#howtoSheet', '.sheet'], {'aria-hidden': 'true'}),
  hwClose: el('hwClose', ['[data-sheet-close]']),
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
// in index.html's order: the sheet itself is sheet.js's, the link's tap is page.js's
vm.runInContext(fs.readFileSync(path.join(M, 'glance.js'), 'utf8'), ctx);
vm.runInContext(fs.readFileSync(path.join(M, 'sheet.js'), 'utf8'), ctx);
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
  els.howtoSheet.attrs['aria-hidden'] = 'true';
  history.state = null; pushes = 0; backs = 0; timers = []; advance(1000);
}
// a tap as a browser delivers it: the control's own listeners, then the document's
function tap(n){ const e = ev('click', n, 100, 100); (n.heard.click || []).forEach(f => f(e)); fire(docL, 'click', e); }
const state = () => ({open: isOpen(), hidden: els.howtoSheet.attrs['aria-hidden'], pushes, backs,
                      focus: document.activeElement && document.activeElement.id});

const out = {};

// 1. a TAP on the link under the chart opens the chart's key, with its history
//    entry and the focus on its own close button
reset(); tap(els.howto);
out.tap_opens_key = state();

// 2. the second tap of a double tap lands on the backdrop the first one just
//    put there, inside sheet.js's late-tap window: it closes nothing
advance(100); tap(els.scrim); advance(50);
out.late_tap = state();

// 3. one sheet at a time: the link again while the key is open (a keyboard can
//    reach it) opens nothing more, and pushes no second entry
advance(600); tap(els.howto);
out.open_again = state();

// 4. so ONE Back closes it: the key is hidden, nothing is left on screen, the
//    focus is back on the link, and the page's history is as it was
history.back(); advance(50);
out.back_closes = state();

// 5. the key's Got it, tapped twice before the popstate, goes back once
reset(); tap(els.howto); advance(600); tap(els.hwClose); tap(els.hwClose); advance(50);
out.got_it_twice = state();

// 6. Escape closes it too, the same one way
reset(); tap(els.howto); advance(600);
fire(docL, 'keydown', {key: 'Escape'}); fire(docL, 'keydown', {key: 'Escape'}); advance(50);
out.escape = state();

// 7. a long-press menu is refused on the sheet's text and nowhere else
const cm1 = fire(docL, 'contextmenu', ev('contextmenu', els.howtoSheet, 0, 0));
const cm2 = fire(docL, 'contextmenu', ev('contextmenu', els.text, 0, 0));
out.contextmenu_blocked = [cm1.defaultPrevented, cm2.defaultPrevented];

// 8. and nothing answers a hold: a finger held on the link for a second and
//    lifted without the tap a browser would make of it opens nothing
reset();
fire(docL, 'pointerdown', ev('pointerdown', els.howto, 100, 100));
fire(docL, 'touchstart', ev('touchstart', els.howto, 100, 100));
advance(1000);
fire(docL, 'touchend', ev('touchend', els.howto, 100, 100, {touches: []}));
fire(docL, 'pointerup', ev('pointerup', els.howto, 100, 100));
out.hold = state();

console.log(JSON.stringify(out));
