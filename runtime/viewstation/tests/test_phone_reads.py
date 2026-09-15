"""The reads page — the riskiest change in the light rebuild, and until now the
least tested.

The list order was inverted (oldest-first-and-scroll-to-bottom became
newest-first), which moved four separate things at once: which end new readings
arrive at, which of two adjacent messages a silence belongs to, where the
retired-contract banner lands, and what "hold the reader's place" means. An
audit of that commit found three defects in this file and none of them could
have been caught, because the whole page had two incidental greps of coverage.

The page's logic is inline in the HTML, and the alternative to a browser in CI
is to run that script in node against a stand-in DOM, a fake clock and a
stand-in station — so the list, the poll and the overlay are judged on what
they do. They pin the properties whose violation is silent — an order that
reverses, a gap attributed to the wrong pair, a banner that outlives the outage
it describes. The stylesheet needs a browser to run, so its rules are read as
source.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "static" / "m" / "thread.html"
THREAD = _PATH.read_text()
_NODE = shutil.which("node")


def _fn(name):
    """One function's body, to its closing brace at column 0."""
    m = re.search(r"(?ms)^(?:async )?function " + re.escape(name) + r"\([^)]*\)\{(.*?)^\}", THREAD)
    return m.group(1) if m else None


_THREAD_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const NET = JSON.parse(fs.readFileSync(0, 'utf8'));
let clock = 1e6, timers = [], nextId = 1;
const RealDate = Date;
function FakeDate(...a){ return a.length ? new RealDate(...a) : new RealDate(clock); }
FakeDate.now = () => clock;
FakeDate.parse = RealDate.parse;
FakeDate.prototype = RealDate.prototype;
function setTimeout_(fn, ms){ timers.push({id: nextId, at: clock + (ms || 0), fn}); return nextId++; }
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

function node(text){
  const cl = new Set(), heard = {};
  return {
    children: [], hidden: false, innerHTML: '', dataset: {}, value: '', _text: text || '',
    classList: {add: (...c) => c.forEach(x => cl.add(x)), remove: (...c) => c.forEach(x => cl.delete(x))},
    get className(){ return [...cl].join(' '); },
    set className(v){ cl.clear(); String(v).split(/\s+/).filter(Boolean).forEach(x => cl.add(x)); },
    get textContent(){ return this._text + this.children.map(c => c.textContent).join(''); },
    set textContent(v){ this._text = String(v); this.children = []; },
    get childElementCount(){ return this.children.length; },
    append(...c){ this.children.push(...c); },
    addEventListener(type, fn){ (heard[type] = heard[type] || []).push(fn); },
    fire(type){ (heard[type] || []).forEach(f => f({})); },
  };
}
const els = {wrap: node(), sub: node(), day: node(), load: node()};
const document = {
  getElementById: id => els[id], createElement: () => node(), createTextNode: node,
  addEventListener(){}, visibilityState: 'visible',
  documentElement: {get scrollHeight(){ return 100 * els.wrap.children.length; }},
};
const requests = [], pending = [];
function fetch(url){
  const u = String(url);
  requests.push(u);
  const answer = () => {
    if(NET.down || (NET.downOn && u.includes(NET.downOn))) throw new TypeError('Failed to fetch');
    const body = u.startsWith('/api/sndk/thread/days') ? {days: NET.days}
               : {day: NET.days[0], messages: NET.messages, count: NET.messages.length,
                  with_something: NET.messages.length};
    return {status: 200, text: async () => JSON.stringify(body)};
  };
  if(!NET.hold) return Promise.resolve().then(answer);
  return new Promise((ok, no) => pending.push(() => { try { ok(answer()); } catch(e){ no(e); } }));
}
const ctx = {document, fetch, Date: FakeDate, setTimeout: setTimeout_, clearTimeout(){}, setInterval: () => 0,
             scrollY: 0, scrollTo(x, y){ ctx.scrollY = y; }};
ctx.window = ctx;
vm.createContext(ctx);
const run = code => vm.runInContext(code, ctx);
const html = fs.readFileSync(__THREAD__, 'utf8');
run([...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes('function render')));

const settle = async () => { for(let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
const release = () => pending.splice(0).forEach(f => f());
const list = () => els.wrap.children.map(n => [n.className, n.textContent]);
(async () => {
  await settle();
  console.log(JSON.stringify(await (async () => { __STEPS__ })()));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _thread(net, steps):
    """Run the page's own script in node against a stand-in DOM, a fake clock
    and a station answering from `net` — {"days", "messages"}, and optionally
    "down" (nothing answers), "downOn" (URLs containing it fail) and "hold"
    (answers wait for release()) — and return what `steps` returns. The
    station answers with the whole day whatever `since` asks for, which is the
    case the page's own timestamp filter exists for.

    `steps` is the body of an async JS function run once boot() has settled.
    In scope: NET, run(code) (evaluated inside the page: poll, render, S),
    settle(), advance(ms) on the fake clock, release(), requests (every URL
    fetched), list() (the list as [class, text], top first), els, and ctx (the
    window, whose scrollY a step may set). Every child of the list draws 100px
    tall. Skips when node is not installed."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = _THREAD_HARNESS.replace("__THREAD__", json.dumps(str(_PATH))).replace("__STEPS__", steps)
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(net),
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_DAY = ["2026-09-10"]


def _said(hm, **extra):
    """One message as /api/sndk/thread sends it, spoken at `hm` New York time."""
    return {"ts": f"2026-09-10T{hm}:00-04:00", "at": hm, "spot": 1700, "wake": "scheduled",
            "read": f"Said at {hm}.", **extra}


def test_the_list_runs_newest_first():
    """You open this to see what the model just said. The old page appended
    oldest-to-newest and auto-scrolled to the bottom, so on a 33-message session
    the newest reading was 33 cards down.

    A silence is drawn between the two readings it separates — in a descending
    list, under the newer one — and measured newer-minus-older: reverse the
    operands and every gap goes negative, minutesBetween returns null for
    anything <= 0, and every silence on the page quietly stops being drawn. The
    oldest reading has nothing older below it (`msgs[-1]` is undefined in JS,
    not the last element), so it gets no divider."""
    got = _thread({"days": _DAY, "messages": [_said("09:35"), _said("09:59"), _said("10:24"), _said("11:30")]}, """
      const rows = els.wrap.children.filter(n => n.className === 'row');
      return {list: list().map(([cls, text]) => [cls, cls === 'row' ? text.slice(0, 5) : text]),
              cards: rows.map(n => n.children[1].className)};""")
    assert got["list"] == [["row", "11:30"], ["quiet-gap", "1H 6M QUIET"],
                           ["row", "10:24"], ["quiet-gap", "25 MIN QUIET"],     # 25 minutes is a silence...
                           ["row", "09:59"], ["row", "09:35"],                  # ...24 is not
                           ["endcap", "first read of the session"]]
    assert got["cards"] == ["rc now", "rc", "rc", "rc"], "the filled card is not the newest reading"


def test_the_retired_contract_banner_is_drawn_before_the_list():
    """It explains a whole session. The old page appended it at the top and then
    scrolled to the bottom, so it was never once seen."""
    retired = [_said("09:35", contract="direction_call"), _said("09:50", contract="direction_call")]
    first = "return list()[0][0];"
    assert _thread({"days": _DAY, "messages": retired}, first) == "oldcontract", \
        "the banner is drawn after the list and will be scrolled past"
    # a session that is not wholly the retired design gets no banner
    assert _thread({"days": _DAY, "messages": retired[:1] + [_said("09:50")]}, first) == "row"


def test_the_poll_cannot_duplicate_the_tail():
    """Two callers — the 45s interval and every visibilitychange — could put two
    requests in the air with the same `since`. Both answers concat, and the pair
    draws as two identical cards with NO divider between them, because
    minutesBetween returns null for equal timestamps.

    Both guards are required: the in-flight flag stops the common case, the
    filter stops the rest, and the symptom of missing either is silent. And a
    request that fails must not wedge the poll behind a flag left up."""
    got = _thread({"days": _DAY, "messages": [_said("09:35")]}, """
      const ats = () => run('S.msgs').map(m => m.at);
      const sent = requests.length;
      NET.messages = NET.messages.concat([{ts: '2026-09-10T10:40:00-04:00', at: '10:40', read: 'New.'}]);
      NET.hold = true;
      const one = run('poll()'), two = run('poll()');
      release(); await one; await two;
      NET.hold = false;
      const airborne = {sent: requests.length - sent, ats: ats()};
      NET.messages = NET.messages.concat([{ts: '2026-09-10T10:50:00-04:00', at: '10:50', read: 'Newer.'}]);
      await run('poll()');
      const whole = ats();
      NET.down = true; await run('poll()'); NET.down = false;
      const before = requests.length;
      await run('poll()');
      return {airborne, whole, retried: requests.length - before};""")
    assert got["airborne"] == {"sent": 1, "ats": ["09:35", "10:40"]}, "two polls were in the air at once"
    assert got["whole"] == ["09:35", "10:40", "10:50"], "the tail was concatenated without a timestamp filter"
    assert got["retried"] == 1, "a failed poll wedged the next one"


def test_a_recovered_station_stops_saying_it_is_unreachable():
    """`S.stalled = false` sat behind an early return on an empty answer, so a
    day with no readings yet kept the banner up permanently while every poll
    behind it succeeded. And the subheader — the element actually asserting
    'station unreachable' — was written only by loadDay, so it never recanted."""
    got = _thread({"days": _DAY, "messages": [], "downOn": "/api/sndk/thread?day"}, """
      const down = {list: list(), sub: els.sub.textContent};
      NET.downOn = null;
      await run('poll()');
      return {down, back: {list: list(), sub: els.sub.textContent}};""")
    assert got["down"] == {"list": [["state", "Could not reach the station. Retrying…"]],
                           "sub": "station unreachable"}
    assert got["back"]["list"] == [["state", "Nothing said this session yet."]], \
        "the stalled banner stuck on a day with no readings yet"
    assert got["back"]["sub"] != "station unreachable", "poll cannot repaint the subheader"


def test_the_poll_holds_the_readers_place():
    """render() empties the container and rebuilds every node, so scrollTop
    survives nothing and browser scroll anchoring has no anchor left. A reading
    arrives ABOVE everything, so a reader parked mid-list shifts by its height
    — onto a different card, mid-sentence."""
    got = _thread({"days": _DAY, "messages": [_said("09:35")]}, """
      const arrive = hm => {
        NET.messages = NET.messages.concat([{ts: '2026-09-10T' + hm + ':00-04:00', at: hm, read: 'New.'}]);
      };
      ctx.scrollY = 400; arrive('09:50'); await run('poll()');
      const parked = ctx.scrollY;
      ctx.scrollY = 5; arrive('09:55'); await run('poll()');
      return {parked, top: ctx.scrollY};""")
    # one 100px card arrives above a reader parked at 400
    assert got["parked"] == 500, "nothing compensates for the inserted height"
    assert got["top"] == 0, "arriving at the top must still land at the top; that is the point of descending"


def test_the_document_is_the_scroller():
    """This replaces an overscroll-containment test, because the inner scroller
    it was containing is gone.

    The page used to be `html,body{height:100%}` with `body{overflow:hidden}`
    and a flex child that scrolled. A desktop browser measures that correctly;
    the Android WebView came up showing only the top of the list with the rest
    unreachable, because `height:100%` needs a definite height to resolve
    against and the column collapses to its content when it does not get one —
    while overflow:hidden clips everything past it.

    Nothing has a fixed height now, so there is nothing to resolve and the page
    scales to any screen. It also makes the shell's SwipeRefreshLayout question
    ("can the child scroll up?") answerable, which is why the JS bridge that
    used to answer it for this page could be deleted."""
    body = re.search(r"(?ms)^body\{(.*?)\}", THREAD)
    assert body is not None
    flat = body.group(1).replace(" ", "").replace("\n", "")
    assert "overflow:hidden" not in flat, "the body hides overflow again; the list would be cut off"
    assert "height:100%" not in flat, "height:100% needs a definite parent and the WebView does not give one"
    assert "min-height:var(--app-h)" in flat, "the page must span the measured height, not a vh"

    wrap = re.search(r"(?ms)^\.wrap\{(.*?)\}", THREAD)
    assert wrap is not None
    wflat = wrap.group(1).replace(" ", "")
    assert "overflow-y:auto" not in wflat, "the inner scroller is back"
    assert "flex:1" not in wflat

    hd = re.search(r"(?ms)^\.hd\{(.*?)\}", THREAD)
    assert hd and "position:sticky" in hd.group(1).replace(" ", ""), \
        "the header must stick, or it scrolls away with the list"

    # the bridge is not merely unused — it is gone from the page's code, under
    # any spelling. (The note explaining why it went is prose and may name it.)
    script = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", THREAD))
    code = "\n".join(l.split("//")[0] for l in re.sub(r"(?s)/\*.*?\*/", "", script).splitlines())
    assert "MiraiShell" not in code, "the page still reports a scroll position it no longer owns"


def test_the_overlay_covers_the_blank_page_from_the_first_frame():
    """The first version faded in after 350ms, which inverted the thing it was
    for: the reader saw the empty page for 350ms and the spinner arrived
    afterwards. A loading state that appears after the wait is not a loading
    state.

    So it is painted opaque, and the flicker problem moves to the other end —
    a floor on how briefly it may be shown."""
    assert 'id="load"' in THREAD
    load = re.search(r"(?ms)^\.load\{(.*?)\}", THREAD)
    assert load is not None
    flat = load.group(1).replace(" ", "").replace("\n", "")
    assert "opacity:1" in flat, "the overlay starts transparent; the blank page shows first"
    assert "animation:" not in flat, "a delayed reveal is back"
    assert "position:fixed" in flat and "inset:0" in flat, "it does not cover the page"
    assert "background:var(--g)" in flat, "a transparent overlay does not hide anything"

    # every path that ends a load clears it, and not before the floor — the day
    # that loaded, and the empty or unreachable archive, which never reaches
    # loadDay at all — and switching sessions is a real fetch, so it puts the
    # overlay back
    got = _thread({"days": _DAY, "messages": [_said("09:35")]}, """
      advance(199);
      const early = {hidden: els.load.hidden, cls: els.load.className};
      advance(1000);
      const loaded = els.load.hidden;
      els.day.fire('change');
      const switched = els.load.hidden;
      await settle(); advance(1000);
      return {early, loaded, switched, reloaded: els.load.hidden};""")
    assert got["early"] == {"hidden": False, "cls": ""}, \
        "the floor is too short to read as anything but a stutter"
    assert got["loaded"] is True, "a loaded day leaves the spinner up"
    assert got["switched"] is False, "a day switch leaves the previous session on screen pretending to be the new one"
    assert got["reloaded"] is True
    assert _thread({"days": [], "messages": []}, "advance(1000); return els.load.hidden;") is True, \
        "an empty or unreachable archive leaves the spinner up"


def test_the_struck_chip_stays_legible_on_the_filled_card():
    """--put-ink on the dark --fill card is 2.39:1, under even the 3:1 floor for
    a border. `.rc.struck .wake` and `.rc.now .wake` have identical specificity,
    so source order alone was deciding it."""
    assert ".rc.now.struck .wake" in THREAD, \
        "a struck reading that is also the newest would render at 2.39:1"


def test_model_text_cannot_carry_markup():
    """The one boundary for model-authored prose. The <b> chips this adds are
    the only markup that may ever enter that string."""
    f = _fn("setSaid")
    assert f is not None
    if not _NODE:
        pytest.skip("node is not installed")
    # the page's own function, run on hostile prose
    js = ("function setSaid(node, text){%s}\n"
          "const n = {innerHTML: ''};\n"
          "setSaid(n, require('fs').readFileSync(0, 'utf8'));\n"
          "console.log(JSON.stringify(n.innerHTML));") % f
    said = 'Held 1,750 <img src=x onerror="alert(1)"> & <b>1700</b>, then "quoted" ¦ 1650.'
    out = subprocess.run([_NODE, "-e", js], input=said, capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    html = json.loads(out.stdout)
    chips = re.findall(r'<b class="n">([^<]*)</b>', html)
    assert chips == ["1,750", "1700", "1650"], html          # the chips survive, around prices only
    rest = re.sub(r'<b class="n">[^<]*</b>', "", html)
    for ch in '<>"¦':
        assert ch not in rest, f"{ch!r} from the model reached innerHTML: {html}"
    assert "&amp;" in rest and not re.search(r"&(?!amp;)", rest), f"a bare & reached innerHTML: {html}"
