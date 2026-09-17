"""/m — the phone view's address.

The static fallback at the bottom of do_GET resolves a route under static/ and
serves it only when it is a FILE. "/m" is a directory, so before this route
existed the address a phone would actually be given 404'd while
"/m/index.html" worked — the classic case of the documented URL and the working
URL being different strings. The Android shell is configured with "/m", so
test_every_route_answers pins all three spellings onto the same file, over HTTP.

Driving do_GET needs no socket: the handler's only I/O goes through _send_file
and _send_json, and a subclass that captures both exercises the route table
directly (the same stand-in habit test_host_guard uses for _host_ok)."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import server


def _block(sel):
    """The declaration block for a selector, read to its closing brace rather
    than by a character count — the comments inside these rules are longer than
    any slice, and a test that cannot see past prose teaches you to delete the
    prose.

    `sel` is given with its brace (".p-edge{"). The stylesheet pads some
    selectors out to a column (".p-edge    {"), so the brace is matched
    separately from the name rather than as one literal."""
    import re as _re
    name = sel.rstrip("{").rstrip()
    m = _re.search(r"(?m)^" + _re.escape(name) + r"\s*\{([^}]*)\}", PHONE)
    return m.group(1) if m else None


class _Stub(server.Handler):
    """Handler with the transport removed — never call BaseHTTPRequestHandler's
    __init__, which would start servicing a connection we do not have."""

    def __init__(self, path):
        self.path = path
        self.headers = {"Host": "localhost:8787"}
        self.request_version = "HTTP/1.1"
        self.sent_file = None
        self.sent_json = None

    def _send_file(self, path):
        self.sent_file = path

    def _send_json(self, payload, code=200):
        self.sent_json = (payload, code)


class _Wire(server.Handler):
    """Handler that runs the REAL _send_file and keeps what it put on the wire.
    The caching and content-type rules exist only as headers, so they are
    judged as headers rather than by reading _send_file's source."""

    def __init__(self, path="/"):
        self.path = path
        self.headers = {"Host": "localhost:8787"}
        self.request_version = "HTTP/1.1"
        self.client_address = ("127.0.0.1", 0)
        self.close_connection = False
        self.wfile = io.BytesIO()
        self.status = None
        self.sent = {}

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, key, value):
        self.sent[key] = value

    def end_headers(self):
        pass


def test_the_phone_page_exists_on_disk():
    """Every file the two phone pages load — scripts, the typeface, the link
    between them — is served by the real route table as a file that exists. A
    page that names a script the station 404s paints nothing and says nothing."""
    refs = set()
    for html in (PHONE, THREAD):
        refs.update(re.findall(r'(?:src|href)="(/m/[^"]*)"', html))
        refs.update(re.findall(r'url\("(/m/[^"]+)"\)', html))
    assert {"/m/glance.js", "/m/page.js", "/m/press.js"} <= refs, refs
    for ref in sorted(refs):
        h = _Stub(ref)
        h.do_GET()
        assert h.sent_json is None, f"{ref} 404s: {h.sent_json}"
        assert h.sent_file is not None and Path(h.sent_file).is_file(), ref


def test_an_apk_declares_itself_installable(tmp_path):
    """The shell is downloaded from this server, behind the same password wall
    it later talks through. Chrome tolerates the octet-stream fallback; naming
    the type means the file says what it is rather than relying on that."""
    apk = tmp_path / "mirai-mobile-1.2.apk"
    apk.write_bytes(b"PK\x03\x04")
    h = _Wire("/mirai-mobile-1.2.apk")
    h._send_file(apk)
    assert h.status == 200
    assert h.sent["Content-Type"] == "application/vnd.android.package-archive"


def test_the_phone_page_is_not_reachable_by_a_rebound_name():
    """The phone view is served through the same host guard as everything
    else — a new route must not become a new hole."""
    h = _Stub("/m")
    h.headers = {"Host": "evil.com"}
    h.do_GET()
    assert h.sent_file is None


# --- the rebuilt glance: §14 MUST NOT REGRESS ------------------------------
# Every item below is measured. Breaking one is a defect, not a taste call.
# The numbers in the docstrings are the evidence, kept here so a later reader
# can weigh the rule instead of guessing at it.
M = Path(__file__).resolve().parents[1] / "static" / "m"
PHONE = (M / "index.html").read_text()
GLANCE = (M / "glance.js").read_text()
PAGE = (M / "page.js").read_text()
THREAD = (M / "thread.html").read_text()
ET = ZoneInfo("America/New_York")
_NODE = shutil.which("node")
_BUILT_AT = "2026-08-19T13:02:00-04:00"
_NOW = "2026-09-10T11:00:00-04:00"


def _glance(js, data=None, tz=None):
    """Run `js` against the REAL glance.js in node — `g` is its exports, `D` is
    `data` — and return the one JSON value the script prints. glance.js is pure
    on purpose so its rules can be run rather than grepped. Skips (after any
    source assertions the caller made first) when node is not installed."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = ("const g=require(%s);const D=JSON.parse(require('fs').readFileSync(0,'utf8'));%s"
              % (json.dumps(str(M / "glance.js")), js))
    env = dict(os.environ, TZ=tz) if tz else None
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data),
                         capture_output=True, text=True, timeout=20, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_PAGE_HARNESS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const M = __M__;
const NET = JSON.parse(fs.readFileSync(0, 'utf8'));
const RealDate = Date, NOW = RealDate.parse(NET.now);
function FakeDate(...a){ return a.length ? new RealDate(...a) : new RealDate(NOW); }
FakeDate.now = () => NOW;
FakeDate.parse = RealDate.parse;
FakeDate.prototype = RealDate.prototype;

function node(){
  const cl = new Set();
  return {
    attrs: {}, style: {}, dataset: {}, children: [], hidden: false, innerHTML: '', _text: '',
    classList: {add: (...c) => c.forEach(x => cl.add(x)), remove: (...c) => c.forEach(x => cl.delete(x)),
                contains: c => cl.has(c),
                toggle: (c, on) => ((on === undefined ? !cl.has(c) : on) ? cl.add(c) : cl.delete(c))},
    get className(){ return [...cl].join(' '); },
    set className(v){ cl.clear(); String(v).split(/\s+/).filter(Boolean).forEach(x => cl.add(x)); },
    get textContent(){ return this._text + this.children.map(c => c.textContent).join(''); },
    set textContent(v){ this._text = String(v); this.children = []; },
    setAttribute(k, v){ this.attrs[k] = String(v); },
    appendChild(c){ this.children.push(c); return c; },
    replaceChildren(...c){ this._text = ''; this.children = c; },
    focus(){},
    getBoundingClientRect: () => ({width: NET.width == null ? 380 : NET.width, height: 0}),
  };
}
const els = {}, listeners = {document: {}, window: {}};
const on = bag => (type, fn) => { (bag[type] = bag[type] || []).push(fn); };
const document = {
  body: node(), hidden: false, documentElement: {style: {setProperty(){}}},
  getElementById: id => els[id] || (els[id] = node()),
  querySelector: () => null, createElement: () => node(), addEventListener: on(listeners.document),
};
async function fetch(url){
  if(NET.down) throw new TypeError('Failed to fetch');
  const u = String(url), rows = key => ({rows: NET[key] || []});
  const body = u.startsWith('/api/sndk/payload') ? NET.payload
             : u.startsWith('/api/spot') ? (NET.live || {ticker: 'SNDK', spot: null})
             : u.includes('path=sndk_reversion/') ? rows('diary')
             : u.includes('path=sndk_reads/') ? rows('reads')
             : u.includes('path=sndk_bars/') ? rows('bars') : {error: 'not found'};
  return {status: 200, text: async () => JSON.stringify(body)};
}
const ctx = {document, fetch, Date: FakeDate, URLSearchParams, location: {search: ''},
             setTimeout: () => 0, clearTimeout(){}, setInterval: () => 0, clearInterval(){}};
ctx.window = ctx;
ctx.addEventListener = on(listeners.window);
vm.createContext(ctx);
const run = code => vm.runInContext(code, ctx);
run(fs.readFileSync(path.join(M, 'glance.js'), 'utf8'));
run(fs.readFileSync(path.join(M, 'page.js'), 'utf8'));

const settle = async () => { for(let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
const view = n => ({text: n.textContent, cls: n.className, hidden: n.hidden, html: n.innerHTML,
                    attrs: n.attrs, style: n.style, kids: n.children.map(view)});
const dump = () => Object.assign({body: document.body.className},
                                 ...Object.keys(els).sort().map(id => ({[id]: view(els[id])})));
(async () => {
  await settle();
  console.log(JSON.stringify(await (async () => { __STEPS__ })()));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _page(net, steps="return dump();", tz=None):
    """Run the REAL page.js, over the real glance.js, in node against a
    stand-in DOM and a station that answers from `net` — {"payload", "now"},
    and optionally "live" (/api/spot), "reads", "diary", "bars" (the raw file
    rows), "width" (the ladder's measured width) and "down" (nothing answers) —
    and return what `steps` returns.

    `steps` is the body of an async JS function run once the first load has
    painted. In scope: NET (what the station answers next), run(code)
    (evaluated inside the page, so loadPayload, loadSpot and WIN are
    reachable), settle(), dump() (every element the page touched: text, class,
    hidden, innerHTML, attributes, style, children), els and listeners. The
    wall clock stands still at `now`. Skips when node is not installed."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = _PAGE_HARNESS.replace("__M__", json.dumps(str(M))).replace("__STEPS__", steps)
    env = dict(os.environ, TZ=tz) if tz else None
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(net, default=str),
                         capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _board(scene, now=_NOW, payload=None, **net):
    """A station holding one scan of `scene` taken at `now`, in the wrapper
    /api/sndk/payload sends, with the reader's own gates. `payload` overrides
    wrapper keys (row_ts, gates, levels); anything else is passed to _page."""
    R = _reader()
    wrapper = {"scene": scene, "row_ts": now, "session": now[:10],
               "gates": {"stale_book_min": R.STALE_BOOK_MIN, "heartbeat_min": R.HEARTBEAT_MIN}}
    wrapper.update(payload or {})
    return {"payload": wrapper, "now": now, **net}


def _card(got):
    """The levels card as painted, top row first: (row class, label,
    {child class: (text, the bar fill's style)})."""
    return [(row["cls"], row["kids"][0]["text"],
             {k["cls"]: (k["text"], k["kids"][0]["style"] if k["kids"] else None) for k in row["kids"][1:]})
            for row in got["lvRows"]["kids"]]


def _svg_texts(got, cls):
    """[(class, text)] for every <text> the ladder drew whose class starts with `cls`."""
    return re.findall(r'<text class="(%s[^"]*)"[^>]*>([^<]*)</text>' % re.escape(cls), got["svg"]["html"])


def _right_foot(got):
    return re.findall(r'<text class="p-axis"[^>]*text-anchor="end">([^<]*)</text>', got["svg"]["html"])


def _built_payload(tmp_path, monkeypatch):
    """The payload the phone fetches, built by the real builder from one diary
    row in a throwaway state dir. No model call: sndk_payload only builds."""
    import snapshot
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 13, 1, tzinfo=ET)
    row = {"ticker": "SNDK", "ts": t.isoformat(), "spot": 1586.2, "sigma": 80.0,
           "prior_close": 1554.5, "gamma_sign": "negative", "regime": "trending",
           "gex_views": {"front_dte": 2, "magnet": 1600.0,
                         "mass_by_strike": [[1600.0, 40.0], [1700.0, 30.0], [1500.0, 25.0]],
                         "net_by_strike": [[1550.0, -3.0e6], [1600.0, -2.0e6], [1700.0, 4.0e6]]},
           "meta": {"expiries": [{"date": "2026-08-21", "dte": 2}]}}
    d = tmp_path / "sndk_reversion"
    d.mkdir(parents=True)
    (d / "2026-08-19.jsonl").write_text(json.dumps(row) + "\n")
    return snapshot.sndk_payload(datetime.fromisoformat(_BUILT_AT))


def _phone_scene(payload):
    # the same choice page.js state() makes: the legacy Scene Payload when it rides
    return (payload.get("legacy") or {}).get("scene") or payload["scene"]


def _reader():
    import snapshot
    if str(snapshot._SNDK_PRO_DIR) not in sys.path:
        sys.path.insert(0, str(snapshot._SNDK_PRO_DIR))
    import sndk_read
    return sndk_read


def test_the_regime_word_carries_no_claim_about_what_price_will_do(tmp_path, monkeypatch):
    """The gloss under the regime word said "walls hold" or "walls give way",
    read off the gamma sign. That is a claim that hedging damps or speeds a
    move — the sentence the model is forbidden to write (sndk_read.py's
    doctrine) and the effect docs/sndk-plan.md records as measured absent on
    SNDK. The sign itself was the literal string "unknown" on 490 of 5,423
    scans (9.0%), and on the rest it rests on an assumed dealer convention.

    So the gamma sign reaches no pixel at all now: not the gloss, not the card,
    not the footer, not a colour. The regime word stands alone. Its whole blast
    radius, if it ever came back, should be one sentence and not the
    instrument. The builder still ships the sign, so the real page is painted
    from the real builder's scene under every sign, and all of them paint
    alike."""
    code = _code_only(GLANCE) + _code_only(PAGE)
    for gone in ("walls hold", "walls give way"):
        assert gone not in code, gone
    payload = _built_payload(tmp_path, monkeypatch)
    assert _phone_scene(payload)["regime"]["gamma_sign"] == "negative", \
        "the builder no longer ships the sign this test varies"
    painted = {}
    for sign in ("negative", "positive", "unknown", None):
        p = json.loads(json.dumps(payload, default=str))
        regime = _phone_scene(p)["regime"]
        if sign is None:
            regime.pop("gamma_sign")
        else:
            regime["gamma_sign"] = sign
        painted[sign] = _page({"payload": p, "now": _BUILT_AT})
    assert painted["negative"]["regWord"]["text"] == "Trending"
    for sign, got in painted.items():
        assert got == painted["negative"], f"the page paints gamma_sign={sign!r} differently"
    # no word measured: it says so rather than falling silent
    p = json.loads(json.dumps(payload, default=str))
    _phone_scene(p)["regime"].pop("regime_label")
    got = _page({"payload": p, "now": _BUILT_AT})
    assert got["regWord"]["text"] == "" and got["regGloss"]["text"] == "Regime not measured"
    # the footer names what every mark is, rather than caveating a claim
    assert "not a forecast of where price goes" in got["foot"]["text"]


def test_the_average_price_is_off_the_chart_entirely():
    """The volume-weighted average had a dashed rule, a lane word, a gutter tag
    and a place in the window solve. All four are gone (2026-09-16), at the
    reader's word: the reading names it in prose beside the chart, and a mark
    that repeats the prose costs a gutter row and a level's worth of window.

    Off the chart has to mean off the WINDOW too. A level nobody can see must
    not decide what everyone else is drawn against, which is the quiet half of
    a removal and the half that survives a careless one."""
    scene = {"price": {"live_spot": 1700, "vwap_minus_live_spot_sigma": -3.0},
             "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}],
                       "put": [{"strike": 1690, "cluster_share_of_book_gamma_pp": 10}]}}
    diary = [{"ticker": "SNDK", "ts": _NOW, "spot": 1700, "vwap": 1580.0}]
    page = _page(_board(scene, diary=diary))
    svg = page["svg"]["html"]
    assert "p-vwap" not in svg and "p-lane" not in svg and "vwap" not in svg.lower()
    # the average sits $120 below price, three sigma away. Were it still a level
    # the window would have to stretch to it or name it at the edge; it does
    # neither, because it is not one.
    assert "1,580" not in svg
    assert PHONE.count("p-vwap") == 0 and PHONE.count("p-lane") == 0

def test_weight_rides_one_fixed_scale_and_absence_is_not_zero():
    """ONE full scale for the card's bars, the chart's rail bars and the chart's
    line thickness, so the three can never rank a wall differently.

    30, not 20. Over 15,653 wall observations since 07-27 the share runs p50
    9.4%, p90 25.6%, p95 33.1%; the last eight sessions run heavier. At 20 a
    full bar was 15.5% of all walls and 27.1% of recent ones — a quarter of the
    levels drew identically at the cap. At 30 the cap takes 6.4%. A per-scan
    maximum is still wrong: it makes the biggest wall full every scan and
    destroys comparison between days. gex null draws no bar AND no track: an
    empty track reads as zero.

    The most-contracts row is a COUNT, never a bar. A bar beside it measured
    something that did not choose it: on 66.9% of replayed scans the
    most-contracts strike was not the heaviest gamma strike in its own window,
    and on 09-02 its share sat under 1% on 100 of 186 scans because its calls
    and puts cancel in the netted surface."""
    # THE CHART'S HALF OF THIS IS GONE (2026-09-16). wallStroke and railWidth
    # drew this same share as a rule's thickness and a gutter bar's length;
    # weight on the chart is shade now, on the contracts denominator, and both
    # helpers were deleted rather than left unused. What the card does with the
    # share is unchanged, and that is what is measured below.
    got = _glance("""console.log(JSON.stringify({full: g.FULL_SHARE,
      bars: [0, 15, 30, 45].map(g.shareBarPct), none: g.shareBarPct(null)}));""")
    assert got["full"] == 30
    # full at the cap, half at half of it, and no further past it. A measured
    # zero keeps a 2% sliver: it is a datum, and it must not read as absence.
    assert got["bars"] == [2, 50, 100, 100]
    # no share: no bar at all, never a zero-width one — an empty track reads as zero
    assert got["none"] is None
    # the page draws through those rules, on the card and the chart at once
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 100},
             "magnet": {"top_strikes": [{"strike": 1700, "share_of_book_gamma_pp": 30}]},
             "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 15}], "put": [{"strike": 1680}]}}
    page = _page(_board(scene, payload={"levels": {"most_contracts": {"strike": 1700, "contracts": 9000}}}))
    call, most, put = _card(page)
    assert call[:2] == ("lv call", "Call wall")
    assert call[2]["lv-bar"] == ("", {"width": "50.0%"}) and call[2]["lv-v"][0] == "15.0%"
    assert put[:2] == ("lv put", "Put wall") and put[2]["lv-bar none"] == ("", None)
    assert most[:2] == ("lv mag", "Most contracts")
    assert most[2] == {"lv-k": ("1,700", None), "lv-n": ("9,000 contracts", None)}, "the most-contracts row grew a bar"
    svg = page["svg"]["html"]
    # on the chart both walls draw the same hairline whatever their share — the
    # one with 15% and the one with none at all
    assert sorted(re.findall(r'<line class="p-wall (\w+)"[^>]*stroke-width:([\d.]+)', svg)) == \
        [("call", "1.6"), ("put", "1.6")]
    assert re.findall(r'<rect class="p-bar (\w+)"', svg) == []


def test_a_refused_level_is_always_named():
    """Exiled or refused by the legibility test, a BOOK level becomes an edge
    marker carrying its strike. That is the 2026-08-24 bug, where the heaviest
    wall on the board reached no pixel at all.

    A tape point has no strike, and ~70 exiled ones could take both slots while
    the wall the gate names in 30px type reached no pixel — the very bug the
    edge marker exists to prevent, coming back through the queue. So only walls
    and magnets are named, walls before magnets (two denominators), split on
    the price rather than the padded window, and never a level that already
    has a rule."""
    got = _glance("""
      const w = g.solveWindow(
        [{y:1700, kind:'price'}, {y:1720, kind:'wall', side:'call'}, {y:1900, kind:'wall', side:'call'}],
        [{y:1640, kind:'wall', side:'put', gex:40}], 1700, 40, 5);
      console.log(JSON.stringify({exiled: w.exiled.map(l => l.y), refused: w.refused.map(l => l.y),
                                  admitted: w.admitted.map(l => l.y)}));""")
    assert got["exiled"] == [1900]      # past the 1.75-sigma radius
    assert got["refused"] == [1640]     # would flatten a $5 day into a line
    assert got["admitted"] == []
    # price 1700 on a $2 day: every level past the nearest walls is exiled or refused
    day = {"price": {"live_spot": 1700, "session_high": 1701, "session_low": 1699},
           "scale": {"one_sigma_dollars": 40},
           "magnet": {"top_strikes": [{"strike": 1800, "share_of_book_gamma_pp": 30}]},
           "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}, {"strike": 1850}],
                     "put": [{"strike": 1680, "cluster_share_of_book_gamma_pp": 10}],
                     "call_heaviest_wall_behind_the_ladder": {"strike": 1900, "cluster_share_of_book_gamma_pp": 40},
                     "put_heaviest_wall_behind_the_ladder": {"strike": 1690, "cluster_share_of_book_gamma_pp": 20}}}
    # twenty minute bars far below the book: exiled tape, with no strike to name
    bars = [{"ts": f"2026-09-10T09:{30 + i}:00-04:00", "close": 1500 + i} for i in range(20)]
    edges = [text for _, text in _svg_texts(_page(_board(day, bars=bars)), "p-edge")]
    # the second call wall has no share at all and still outranks the magnet; the
    # refused put pile inside the window is named on its own side of price
    #
    # THREE ABOVE, not two (2026-09-16). This list used to stop at 1,850 and the
    # exiled magnet at 1,800 reached no pixel and no name — the very drop the
    # docstring above says cannot happen, sitting inside the test written to
    # prevent it, because the cap of two was set to the size of the OPTIONAL set
    # and an exiled magnet does not come from there. The cap is three now:
    # enough for the three walls a side can offer (nearest, second, behind) once
    # a nearest wall too far for the day to reach is tried as optional rather
    # than anchored. The pad is computed from the row count, so the third row
    # costs its 13px only on a day that has a third thing to say.
    assert edges == ["▲ 1,900 HEAVIEST", "▲ 1,850", "▲ 1,800", "▼ 1,690 HEAVIEST"]
    # a refused level on the strike of a wall already ruled is not named twice
    day["walls"]["put"].append({"strike": 1690, "cluster_share_of_book_gamma_pp": 5})
    day["walls"]["put_heaviest_wall_behind_the_ladder"] = {"strike": 1680, "cluster_share_of_book_gamma_pp": 20}
    edges = [text for _, text in _svg_texts(_page(_board(day)), "p-edge")]
    assert edges == ["▲ 1,900 HEAVIEST", "▲ 1,850", "▲ 1,800", "▼ 1,690"]


def test_the_magnet_never_shares_a_gauge_with_anything_else():
    """top_strikes shares are a fraction of mass_by_strike; a wall's share is a
    fraction of net_by_strike; the shade behind both is a fraction of the
    contracts on the board. Three denominators, and no two may share a gauge.

    The wall's rail bar used to carry the second of those as a LENGTH beside a
    rule carrying it as a THICKNESS. Both went on 2026-09-16 when weight became
    shade, so the gutter now holds marks that say WHICH level a row is — a
    diamond for the magnet — and never how much."""
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 100},
             "magnet": {"top_strikes": [{"strike": 1712, "share_of_book_gamma_pp": 30}]},
             "walls": {"call": [{"strike": 1740, "cluster_share_of_book_gamma_pp": 12}],
                       "put": [{"strike": 1660, "cluster_share_of_book_gamma_pp": 10}]}}
    svg = _page(_board(scene))["svg"]["html"]
    # no length-gauge anywhere in the gutter, and exactly one diamond
    assert re.findall(r'<rect class="p-bar \w+"', svg) == []
    assert len(re.findall(r'<rect class="p-diamond"', svg)) == 1
    # the two walls keep the plot-edge arrow that points at them, which names a
    # side and a position and carries no quantity at all
    assert sorted(re.findall(r'<path class="p-bar (\w+)"', svg)) == ["call", "put"]
    # and every wall rule is the same weight, whatever its share
    widths = set(re.findall(r'<line class="p-wall \w+"[^>]*stroke-width:([\d.]+)', svg))
    assert widths == {"1.6"}, "a wall rule is drawing its share as thickness again"

def test_the_magnet_list_is_read_as_dicts(tmp_path, monkeypatch):
    """sr-7 reshaped magnet.top_strikes from [strike, share] pairs into
    {strike, share_of_book_gamma_pp} dicts. The phone kept indexing arrays,
    Array.isArray(mag[0]) went false on every scan, and the magnet never drew
    again — while nothing here noticed.

    So the scene the real builder hands back is fed to the real glance.js and
    the real page: if either end reshapes the list, the magnet stops drawing
    here too."""
    payload = _built_payload(tmp_path, monkeypatch)
    scene = _phone_scene(payload)
    top = scene["magnet"]["top_strikes"]
    assert len(top) >= 2, "the fixture no longer builds runners to draw"
    got = _glance("""
      const core = g.coreLevels(D, D.price.live_spot, null, []);
      console.log(JSON.stringify({mag: core.filter(l => l.kind === 'magnet'),
                                  runners: g.magnetRunners(D)}));""", scene)
    assert [(m["y"], m["lead"], m["share"]) for m in got["mag"]] == \
        [(top[0]["strike"], True, top[0]["share_of_book_gamma_pp"])]
    assert [(r["y"], r["share"]) for r in got["runners"]] == \
        [(t["strike"], t["share_of_book_gamma_pp"]) for t in top[1:]]
    page = _page({"payload": payload, "now": _BUILT_AT})
    assert len(re.findall(r'<line class="p-mag"', page["svg"]["html"])) == 1
    assert _svg_texts(page, "p-tag mag") == [("p-tag mag", f"{top[0]['strike']:,.0f}")]


def test_no_magnet_tie_threshold():
    """sr-3 deleted a hardcoded 5.0pp constant for shipping a near-constant as
    a finding. A near-tie must look like a tie without anyone deciding where a
    tie begins."""
    shares = [20, 19.99, 19.9, 19, 15, 10, 6, 5.6, 2]
    w = _glance("""
      const top = D.map((s, i) => ({strike: 1700 - 10*i, share_of_book_gamma_pp: s}));
      console.log(JSON.stringify(g.magnetRunners({magnet:{top_strikes: top}}).map(r => r.weight)));""",
                shares)
    assert len(w) == len(shares) - 1
    assert w[0] > 0.99                                    # 19.99 against 20 draws as a tie
    assert all(a >= b for a, b in zip(w, w[1:]))          # lighter never draws heavier
    assert min(w) > 0                                     # a runner never vanishes
    # linear in share above the visibility floor: no step where a "tie" begins
    steps = [(w[i] - w[i + 1]) / (shares[i + 1] - shares[i + 2])
             for i in range(len(w) - 1) if w[i + 1] > min(w) + 1e-9]
    assert len(steps) >= 4 and max(steps) - min(steps) < 1e-9, steps


def test_book_age_min_is_never_read():
    """Off-live, build_scene stamps clock.book_age_min from the row's own
    timestamp, so it reads ~0 however old the scan is. That is the failure that
    let a dead Schwab login look healthy for 3.1 days."""
    got = _glance("""
      const now = Date.parse('2026-08-19T16:01:00-04:00');
      const fresh = {clock:{book_age_min:0}};
      console.log(JSON.stringify([
        g.bookAge({row_ts:'2026-08-19T13:01:00-04:00', scene:fresh, legacy:{scene:fresh}}, now),
        g.bookAge({scene:fresh, legacy:{scene:fresh}}, now)]));""")
    # three hours old by the wall clock, whatever the scene claims
    assert got[0] == {"min": 180, "unknown": False}
    # no row_ts: unknown, never the scene's self-measured zero
    assert got[1] == {"min": None, "unknown": True}
    # the masthead reads that age, not either of the scene's own clocks
    scene = {"clock": {"book_age_min": 0}, "data_sources": {"options_book": {"age_min": 0}},
             "price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40}}
    old = _page(_board(scene, payload={"row_ts": "2026-09-10T08:00:00-04:00"}))
    assert (old["fresh"]["text"], old["fresh"]["cls"]) == ("LAST SCAN 3H", "fresh bad")
    unstamped = _page(_board(scene, payload={"row_ts": None}))
    assert (unstamped["fresh"]["text"], unstamped["fresh"]["cls"]) == ("LAST SCAN · AGE UNKNOWN", "fresh bad")


def test_staleness_thresholds_come_from_the_payload():
    """stale_book_min and heartbeat_min ride payload.gates, so the reader's own
    numbers decide when the masthead warns, when it calls the book dead and
    when the price is withdrawn — never a copy in the page. Judged on gates the
    page's fallbacks (6 and 60) would call differently, at each boundary."""
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40}}
    gates = {"stale_book_min": 20, "heartbeat_min": 90}

    def aged(minutes, **net):
        row_ts = (datetime.fromisoformat(_NOW) - timedelta(minutes=minutes)).isoformat()
        return _page(_board(scene, payload={"row_ts": row_ts, "gates": gates}, **net))

    live = {"ticker": "SNDK", "spot": 1701}
    assert [aged(m, live=live)["fresh"]["cls"] for m in (20, 21, 90, 91)] == \
        ["fresh", "fresh warn", "fresh warn", "fresh bad"]
    # with no live quote, the price stands until the payload's heartbeat, not the page's
    assert aged(90)["px"]["text"] == "1,700.00"
    assert aged(91)["px"]["text"] == "—"


def test_the_countdown_does_not_age_silently():
    """minutes_to_close is computed at scan time. A countdown read hours later
    is a lie, and it is the one label on the plot that ages without saying so."""
    scene = {"clock": {"minutes_to_close": 178}, "price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}]}}
    assert _right_foot(_page(_board(scene, payload={"row_ts": "2026-09-10T10:59:00-04:00"}))) == ["2H 58M LEFT"]
    # two hours stale: the foot says which scan it is instead
    assert _right_foot(_page(_board(scene, payload={"row_ts": "2026-09-10T09:00:00-04:00"}))) == ["SCAN 09:00"]


def test_the_reading_is_sourced_by_reading_ts_and_never_shown_without_its_age():
    """The store re-emits the same reading with a fresh ts while reading_ts
    stays put: the reference row carries ts 15:58 and reading_ts 11:47, a
    251-minute reading wearing a 0-minute timestamp. Median 12, p95 214,
    max 341.

    obs-1: the 120/30-minute tiers are gone. They were pegged to a 30-minute
    forecast horizon that no longer exists — an observation describes a
    measurement, so what makes it stale is that measurement no longer being
    current, which is the reader's own book ceiling. The phone's copy of that
    ceiling must be the reader's number."""
    m = re.search(r"const STALE_BOOK_MIN_UI\s*=\s*(\d+(?:\.\d+)?)\s*;", GLANCE)
    assert m, "the phone's book ceiling is no longer a literal"
    ceiling = float(m.group(1))
    assert ceiling == _reader().STALE_BOOK_MIN, "the phone and the reader disagree on when a book is stale"
    got = _glance("""
      const at = s => '2026-09-10T' + s + ':00-04:00';
      const now = Date.parse(at('15:58'));
      const reemitted = {ts: at('15:58'), reading_ts: at('11:47'), reading: {read: 'The old sentence.'}};
      const newer = {ts: at('12:00'), reading_ts: at('11:59'), reading: {read: 'The newer sentence.'}};
      const aged = mins => g.modelRead([{ts: at('15:58'), reading_ts: new Date(now - mins*60000).toISOString(),
                                         reading: {quiet: true}}], now).tier;
      const r1 = g.modelRead([reemitted], now);
      console.log(JSON.stringify({
        age: r1.ageMin, tier: r1.tier, line: r1.line,
        pick: g.modelRead([newer, reemitted], now).line,
        edge: [aged(D), aged(D + 0.5)],
        unstamped: g.modelRead([{ts: at('15:58'), reading: {read: 'no reading_ts'}}], now)}));""", ceiling)
    # aged by reading_ts, never by the fresh ts, and still painted: no 'expired' tier
    assert got["age"] == 251 and got["line"] == "The old sentence." and got["tier"] == "aged"
    # the newest READING wins, not the newest row
    assert got["pick"] == "The newer sentence."
    assert got["edge"] == ["fresh", "aged"]
    assert got["unstamped"] is None
    # obs-1 removed the LAST READING blanking branch with the expired tier: the
    # page paints a 251-minute reading, with its age and its clock, and genuine
    # ABSENCE is still its own message
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40}}
    old = _page(_board(scene, reads=[{"ts": _NOW, "reading_ts": "2026-09-10T06:49:00-04:00",
                                      "reading": {"read": "The old sentence."}}]))
    assert old["rdAge"]["text"] == "4H 11M"
    assert old["rdLine"]["text"] == "06:49 · The old sentence."
    none = _page(_board(scene))
    assert none["rdLine"]["text"] == "NO READING TODAY" and none["rdAge"]["text"] == ""


def test_model_output_never_touches_innerhtml():
    """It is model output. It never enters the SVG string either."""
    said = '<img src=x onerror="alert(1)"> 1,750 holds the most contracts.'
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1750, "cluster_share_of_book_gamma_pp": 12}]}}
    got = _page(_board(scene, reads=[{"ts": _NOW, "reading_ts": _NOW, "reading": {"read": said}}]))
    assert got["rdLine"]["text"] == said
    assert got["rdLine"]["html"] == "", "paintRead writes markup"
    for region, el in got.items():
        if isinstance(el, dict):
            assert "onerror" not in el["html"] and "holds the most" not in el["html"], \
                f"the reading reaches {region}'s markup"


def _day_block():
    """A session with a lead that changed hands, strikes in and out, and a
    graded claim — the 2026-09-11 shape, which is what the builder writes."""
    return {"lists_from": "09:36",
            "leaders": {"contracts": [[1600, "09:36", "10:58"], [1700, "11:02", "13:52"],
                                      [1650, "13:56", None]],
                        "volume": [[1700, "09:40", "13:06"], [1650, "13:10", None]]},
            "joined": [1605, 1630], "left": [[1550, "11:15"], [1575, "10:09"]],
            "volume_in_reach_vs_same_time_prior_sessions": 1.37, "prior_sessions_compared": 5,
            "earlier_claims": [{"said_at": "13:11", "claims": [{"strike": 1700, "now": "changed"},
                                                               {"strike": 1600, "now": "holds"}]}]}


def _ac_rows(el):
    """[(state, figure, word, time)] for one side of the ladder, as painted."""
    out = []
    for row in el["kids"]:
        by = {k["cls"]: k["text"] for k in row["kids"]}
        out.append((row["cls"].replace("ac-row ", ""),
                    by.get("ac-k") or by.get("ac-more"),
                    by.get("ac-word"), by.get("ac-time")))
    return out


def _overview(got):
    """The panel as painted: (clock, headline, above, price, below, notes)."""
    px = {k["cls"]: k["text"] for k in got["acPx"]["kids"]}
    return (got["tdWhen"]["text"], got["tdLine"]["text"],
            _ac_rows(got["acAbove"]), px.get("ac-chip"), _ac_rows(got["acBelow"]),
            [(n["cls"].replace("ac-n ", ""), n["text"]) for n in got["acNote"]["kids"]])


def test_the_overview_maps_the_strikes_instead_of_counting_them():
    """It printed "Newly busy: 2 strikes / Gone quiet: 2 strikes" and threw away
    which ones. The panel now lays the strikes out in price order around a price
    row, nearest first, so the SHAPE of the day is the thing you see: on the live
    2026-09-17 board everything newly busy sat just above price and everything
    that went quiet just below, which the counts could never show.

    Every line is still a fact the builder wrote into `day` and nothing is
    derived here, so the ladder and the sentence over it cannot disagree."""
    day = dict(_day_block(),
               joined=[1610, 1615, 1620, 1630, 1640], left=[[1625, "09:59"], [1595, "10:36"]],
               stood=[1600, 1605],
               named_off_list=[{"strike": 1500, "named_at": "09:31", "in_book": False}])
    scene = {"price": {"live_spot": 1608.20}, "scale": {"one_sigma_dollars": 40}, "day": day}
    when, head, above, chip, below, notes = _overview(_page(_board(scene)))
    assert when == "SINCE 09:36"
    # the figure column groups its thousands, so the sentence must too
    assert head.endswith("1,650 has held it since 13:56.")
    # nearest to price first, both sides running high to low, the word once a run
    assert above == [("more", "+1", "further above", None),
                     ("new", "1,630", "got busy", None),
                     ("gone", "1,625", "went quiet", "09:59"),
                     ("new", "1,620", "got busy", None),
                     ("new", "1,615", None, None),
                     ("new", "1,610", None, None)]
    assert below == [("held", "1,605", "busy all day", None),
                     ("held", "1,600", None, None),
                     ("gone", "1,595", "went quiet", "10:36")]
    assert chip == "1,608.20"
    # what the label rows carried and the ladder does not is kept as a note
    assert notes[0] == ("said", "1,500 was named at 09:31 and is now out of the book.")
    # what the MODEL said and what the BOARD did are marked apart, and each kind
    # is emitted in one run so its mark reads as one group rather than alternating
    assert [k for k, _ in notes] == sorted([k for k, _ in notes], reverse=True)
    assert any(k == "board" and t.startswith("Trading ") for k, t in notes)

def test_the_overview_is_honestly_absent_before_the_day_has_facts():
    """Law 1. The session's first look, and any payload older than the day
    block, have nothing to say here — and a ladder printed with nothing in it
    reads as "measured, and nothing moved", which is a different claim."""
    when, head, above, chip, below, notes = _overview(_page(_board(
        {"price": {"live_spot": 1634.16}, "scale": {"one_sigma_dollars": 40}})))
    assert head == "Not measured yet this session."
    assert above == [] and below == [] and notes == [] and chip is None and when == ""
    # a day block that never named a strike still draws no ladder and no price row
    _, head2, above2, chip2, below2, _ = _overview(_page(_board(
        {"price": {"live_spot": 1634.16}, "scale": {"one_sigma_dollars": 40},
         "day": {"lists_from": "09:36", "joined": [], "left": [], "stood": []}})))
    assert head2 == "The session has not settled on a busiest strike yet."
    assert above2 == [] and below2 == [] and chip2 is None

def test_the_overview_never_writes_markup_and_never_doubles():
    """Two rules at once. The panel is written with textContent like the reading
    is; and it is refilled with replaceChildren, because a clear loop written
    against firstChild is a no-op in this DOM and the rows doubled on the second
    paint — which is every poll. The ladder made that second rule matter more:
    it is four elements a row now, not two."""
    day = dict(_day_block(), lists_from='<img src=x onerror="alert(1)">',
               joined=[1610, 1615], left=[[1595, "10:36"]], stood=[1600])
    scene = {"price": {"live_spot": 1608.20}, "scale": {"one_sigma_dollars": 40}, "day": day}
    got = _page(_board(scene))
    # the hostile string is allowed to be TEXT — that is what textContent is
    # for; what it may never be is markup, in this element or any other
    assert got["tdWhen"]["text"].endswith('alert(1)">')
    for region, el in got.items():
        if isinstance(el, dict):
            assert "onerror" not in el["html"], f"the day block reached {region}'s markup"
    first = [len(got[k]["kids"]) for k in ("acAbove", "acPx", "acBelow", "acNote")]
    assert first[0] and first[1] and first[2], "the fixture no longer paints a ladder"
    again = _page(_board(dict(scene, day=dict(day, lists_from="09:36"))),
                  steps="await run('paintToday(); paintToday();'); await settle(); return dump();")
    assert [len(again[k]["kids"]) for k in ("acAbove", "acPx", "acBelow", "acNote")] == first, \
        "the panel doubled on a repaint"

def test_no_dealer_behaviour_is_claimed_anywhere_on_the_phone():
    """The four sentences were copied byte-for-byte from the desktop's snkArrows,
    and the copying was never the problem — the sentences were. (The desktop's
    arrows were removed the same day; test_page_contract pins that side.) "Dealers sell
    the rallies here — it caps the move" states what hedging does to price, on a
    name where no damping or amplifying effect was found, and it was keyed on a
    sign that is "unknown" on 9.0% of scans. Removed 2026-09-10, with the rule
    that replaced them: say where the weight is, never what price will do."""
    for blob, where in ((_code_only(GLANCE), "glance.js"), (_code_only(PAGE), "page.js"),
                        (PHONE, "index.html")):
        for claim in ("Dealers buy", "Dealers sell", "Dealers must", "caps the move",
                      "holds price up", "moves speed up", "wallBehaviour", "gMech"):
            assert claim not in blob, f"{claim!r} is back in {where}"


def test_a_passed_wall_is_judged_against_the_price_on_screen():
    """The card and the chart both ask one question of the price the reader can
    SEE — the 5-second quote — never of the book's spot or the shipped sigma,
    which were measured against a price that has since moved. Replayed over 8
    sessions, price stood beyond a wall the card still showed on 2.7% of
    minutes, 5.8% on 09-10.

    One hue, one meaning: green is the call side. A call wall price has already
    passed sits BELOW price, which is not the call side any more, and it stays
    green until the next scan relabels it. So from the moment the price on
    screen passes it, its strike, bar and chart line go neutral and its label
    says why — the weight is still true, the side is not."""
    # a missing price or strike is checked on BOTH sides: in JS null compares as
    # 0, so without the guard a put wall with no price reads as passed
    cases = [["call", 1700, 1700.01], ["call", 1700, 1700], ["call", 1700, 1699.99],
             ["put", 1650, 1649.99], ["put", 1650, 1650], ["put", 1650, 1650.01],
             ["call", 1700, None], ["put", 1650, None], ["call", None, 1700],
             ["most", 1700, 1800]]
    got = _glance("""
      console.log(JSON.stringify({passed: D.map(a => g.wallPassed(...a)),
                                  shown: g.shownPrice({price:{live_spot:1690}}, {spot:1705}),
                                  book: g.shownPrice({price:{live_spot:1690}}, null)}));""", cases)
    assert got["passed"] == [True, False, False, True, False, False, False, False, False, False]
    # the price on screen is the live quote whenever there is one
    assert got["shown"] == {"v": 1705, "live": True}
    assert got["book"] == {"v": 1690, "live": False}
    # the book saw 1690, under the call wall; the quote on screen is 1705, over it
    scene = {"price": {"live_spot": 1690}, "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1700, "cluster_share_of_book_gamma_pp": 12}],
                       "put": [{"strike": 1650, "cluster_share_of_book_gamma_pp": 9}]}}

    def call_side(page):
        rows = [row[:2] for row in _card(page) if row[1].startswith("Call wall")]
        return rows, re.findall(r'class="p-(?:wall|tag|bar) (call|passed)\b', page["svg"]["html"])

    rows, marks = call_side(_page(_board(scene, live={"ticker": "SNDK", "spot": 1705})))
    assert rows == [("lv passed", "Call wall · Price passed it")]
    # THREE, not four: the gutter's rail bar went with the thickness gauge on
    # 2026-09-16, so what must go neutral together is the rule, the tag and
    # the plot-edge arrow.
    assert marks == ["passed"] * 3, "the rule, tag and arrow do not all go neutral"
    rows, marks = call_side(_page(_board(scene)))
    assert rows == [("lv call", "Call wall")] and marks == ["call"] * 3
    # and passed is neutral wherever it is drawn
    assert ".lv.passed .lv-k{color:var(--i-mute)}" in PHONE
    assert ".p-wall.passed{stroke:var(--rule-soft)}" in PHONE
    assert ".p-tag.passed{fill:var(--i-mute)}" in PHONE
    assert ".p-bar.passed{fill:var(--rule-soft)}" in PHONE


def test_the_banned_fields_reach_no_pixel(tmp_path, monkeypatch):
    """§14.17-18 of docs/phone-glance-spec.md: dealer_positioning (the rename
    of dealer_flow), breadth, momentum, regime.charm with its
    drifts_toward_strike, reading.magnitude_sigma — and the prose the page must
    never parse, frozen_do_not_cite and the magnet lead's vs_own_history word.
    Judged on what is painted rather than on which names the code spells: the
    real builder's scene is painted without them and again with each one
    planted where build_scene writes it, and the two paint alike."""
    payload = _built_payload(tmp_path, monkeypatch)
    said = {"ts": _BUILT_AT, "reading_ts": _BUILT_AT, "reading": {"read": "1600 holds the most contracts."}}
    clean = _page({"payload": payload, "now": _BUILT_AT, "reads": [said]})
    scene = _phone_scene(payload)
    scene["breadth"] = {"lopsidedness_0_is_even": 0.31, "vs_own_history": "widest this month"}
    scene["momentum"] = {"toward": 1750, "note": "building toward 1750"}
    scene["dealer_positioning"] = {"net_delta_bn": 4.2, "net_delta_change_30min_bn": -0.3}
    scene["regime"]["charm"] = {"drifts_toward_strike": 1750}
    scene["frozen_do_not_cite"] = ["magnet unchanged 120m"]
    scene["magnet"]["top_strike_lead_vs_own_history"] = "unusually wide"
    said["reading"]["magnitude_sigma"] = 1.7
    assert _page({"payload": payload, "now": _BUILT_AT, "reads": [said]}) == clean


def _code_only(js):
    """Strip comments first: the measured-history comments in these files cite
    retired names on purpose, and a pin that cannot tell prose from code
    teaches you to delete the history (the wallDistance lesson)."""
    lines = [l for l in js.splitlines() if not l.strip().startswith(("//", "*", "/*"))]
    return "\n".join(l.split("//")[0] for l in lines)


def test_the_scene_is_read_by_its_current_names(tmp_path, monkeypatch):
    """sr-7/sr-8 (2026-08-30) renamed every scene key and reshaped the magnet
    list; the phone was built against the old names and painted nothing while
    this file stayed green, because every pin here spelt the OLD names. The
    source of truth is build_scene (docs/sndk-payload-inventory.md). If a
    rename lands upstream, this is the test that must go red — so the names
    are checked at BOTH ends: the builder still ships each one (in the scene it
    actually builds, or, for the keys only some scans carry, in the builder's
    own source), and the real page paints every region of the real payload."""
    payload = _built_payload(tmp_path, monkeypatch)
    built = _phone_scene(payload)
    reader = Path(_reader().__file__).read_text()
    # checked WHERE the phone reads each one, not anywhere in the scene: the
    # builder also ships share_of_book_gamma_pp on structure.bands, so a rename
    # of the magnet's own key would still find the word somewhere
    where = {"regime_label": ("regime",), "session_date": ("clock",), "live_spot": ("price",),
             "vs_prior_close_pct": ("price",),
             "days_to_expiry": ("clock", "front_expiry"), "expiry_date": ("clock", "front_expiry"),
             "cluster_share_of_book_gamma_pp": ("walls", "call", 0),
             "share_of_book_gamma_pp": ("magnet", "top_strikes", 0)}
    for current, path in where.items():
        node = built
        for step in path:
            node = node[step] if isinstance(node, list) else (node or {}).get(step)
        assert isinstance(node, dict) and current in node, \
            f"the builder no longer ships {current} at {'.'.join(map(str, path))}"
    # a vwap, an empty side, a heavier wall further out: not on every scan, so
    # the builder's source must still write the key the tests above paint from
    for current in ("vwap_minus_live_spot_sigma", "_side_has_no_wall", "_heaviest_wall_behind_the_ladder"):
        assert f'"{current}"' in reader, f"sndk_read.py no longer writes {current}"
    # sr-8 moved `instrument` to the wrapper, and strikes-1 (09-05) moved the
    # walls and the magnet to the legacy Scene Payload: the page must read both
    # there or paint nothing
    got = _page({"payload": payload, "now": _BUILT_AT})
    assert got["ticker"]["text"] == payload["instrument"]
    assert got["expiry"]["text"] == "EXP FRI"                            # 2026-08-21
    assert got["regWord"]["text"] == built["regime"]["regime_label"].capitalize()
    assert got["px"]["text"] == f"{built['price']['live_spot']:,.2f}"
    assert got["chg"]["text"] == f"▲ {built['price']['vs_prior_close_pct']:.2f}%"
    for side in ("call", "put"):
        wall = built["walls"][side][0]
        row = next(r for r in _card(got) if r[1].startswith(side.capitalize() + " wall"))
        assert row[2]["lv-k"][0] == f"{wall['strike']:,.0f}"
        assert row[2]["lv-v"][0] == f"{wall['cluster_share_of_book_gamma_pp']:.1f}%"


def test_no_emoji_no_legend_no_greek():
    """Emoji are colour bitmaps: no theme token, cannot be tinted to mean a
    side, do not dim with the page. A legend is a confession that the marks do
    not read. And no Greek: the ruler is stated once, in English."""
    for blob in (PHONE, PAGE, GLANCE):
        assert not re.search(r"[\U0001F300-\U0001FAFF]", blob)
        assert not re.search(r"[Ͱ-Ͽ]", blob), "a Greek letter is in the phone's source"
    assert "class=\"key\"" not in PHONE
    got = _page(_board({"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 80.4}}))
    assert got["ruler"]["text"] == "TYPICAL MOVE $80"
    assert not re.search(r"[Ͱ-Ͽ\U0001F300-\U0001FAFF]", json.dumps(got, ensure_ascii=False))


def test_the_glance_itself_is_not_a_control():
    """AMENDED 2026-09-07, and again 2026-09-10. The rule was "nothing is
    tappable", and its purpose was that the READING must never be a control: a
    screen you poke is a screen you are working, and this one is read at arm's
    length in a second.

    That purpose survives verbatim. 09-07 permitted exactly ONE link, to the
    readings archive. 09-10 permits exactly ONE press-and-hold, on the
    three-levels card, because the user asked for the card to explain itself —
    and a card whose words (gamma, call wall, most contracts) need a paragraph
    each cannot carry those paragraphs at arm's length. What the hold opens is
    an EXPLANATION: nothing on the card changes by touching it, and it is
    dismissed by one button.

    Everything that would make the DATA interactive stays banned: no onclick,
    no pointer cursors, no tooltips, no second button, no second hold, and the
    one click listener on the page does nothing but close the sheet. Each count
    is exact. A second of anything means the rule has started eroding and this
    test should be argued with again rather than edited again."""
    for bad in ("cursor:pointer", "onclick", "title="):
        assert bad not in PHONE, bad
    links = re.findall(r"<a\s[^>]*>", PHONE)
    assert len(links) == 1, f"exactly one link is allowed on the glance, found {len(links)}: {links}"
    assert 'href="/m/thread.html"' in links[0], links[0]

    holds = re.findall(r"<[^>]*\bdata-hold\b[^>]*>", PHONE)
    assert len(holds) == 1 and 'id="levels"' in holds[0], holds
    dialogs = re.findall(r'role="dialog"', PHONE)
    assert len(dialogs) == 1
    buttons = re.findall(r"<button\b[^>]*>", PHONE)
    assert len(buttons) == 1, buttons
    assert "data-sheet-close" in buttons[0], "the one button must close the sheet"
    sheet = PHONE.split('id="sheet"')[1]
    assert "<button" in sheet, "the button lives outside the sheet"

    got = _page(_board({"price": {"live_spot": 1700}}), """
      const clicks = (listeners.document.click || []).concat(listeners.window.click || []);
      const before = JSON.stringify(dump());
      clicks.forEach(f => f({target: {closest: () => null}}));
      return {clicks: clicks.length, inert: JSON.stringify(dump()) === before};""")
    assert got == {"clicks": 1, "inert": True}, "a second click handler, or one that acts on the page"


def test_market_time_not_viewer_time():
    """The session is 09:30-16:00 in New York and the scene is stamped that
    way. Rendered locally on a Pacific machine the 12:12 scan reads 09:12 and
    the open reads 06:31."""
    got = _glance("""
      const scan = Date.parse('2026-08-19T16:12:00Z');
      console.log(JSON.stringify({viewer: new Date(scan).getHours(), scan: g.etTime(scan),
                                  open: g.etTime(new Date('2026-08-19T13:31:00Z')),
                                  evening: g.etToday(Date.parse('2026-09-10T02:30:00Z')),
                                  late: g.etToday(Date.parse('2026-09-10T05:30:00Z'))}));""",
                  tz="America/Los_Angeles")
    assert got["viewer"] == 9, "the viewer's clock is not Pacific; this proves nothing"
    assert got["scan"] == "12:12" and got["open"] == "09:31"
    # 22:30 in New York is still the 9th, though it is already the 10th in UTC
    assert got["evening"] == "2026-09-09"
    # 01:30 in New York is the 10th while the Pacific viewer is still on the 9th:
    # the one hour of the evening where a date that lost its zone reads wrong
    assert got["late"] == "2026-09-10", "the session date follows the viewer's clock"
    # the page puts those clocks on screen for the same Pacific viewer
    scene = {"clock": {"session_date": "2026-08-19"}, "price": {"live_spot": 1700, "vs_prior_close_pct": 2.0},
             "scale": {"one_sigma_dollars": 40}, "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}]}}
    scan = "2026-08-19T12:12:00-04:00"
    stale = _page(_board(scene, now="2026-08-19T13:00:00-04:00", payload={"row_ts": scan},
                         reads=[{"ts": scan, "reading_ts": scan, "reading": {"read": "Said at the scan."}}]),
                  tz="America/Los_Angeles")
    assert _right_foot(stale) == ["SCAN 12:12"]
    assert stale["lvWhen"]["text"] == "At the 12:12 scan"
    assert stale["rdLine"]["text"] == "12:12 · Said at the scan."
    # 22:30 on the 9th in Los Angeles is the 10th's session in New York: its change shows
    late = _page(_board(dict(scene, clock={"session_date": "2026-09-10"}), now="2026-09-10T01:30:00-04:00",
                        live={"ticker": "SNDK", "spot": 1734}), tz="America/Los_Angeles")
    assert late["chg"]["hidden"] is False, "the change % was gated on the viewer's date"


# --- amendments after the 2026-08-24 adversarial review --------------------
# 38 findings raised, 23 survived refutation, 14 work items. These pin the ones
# that changed behaviour, so a later "tidy" cannot walk them back.

def test_the_frozen_window_is_actually_frozen():
    """At every 5-second repaint the geometry is bit-identical and exactly one
    mark has moved, so a glance is a comparison rather than a fresh read. Only
    a new payload earns a new window.

    A fresh window seats price 5.36% inside its own edge, already within the
    12% re-anchor band, so without a travel gate the board re-solved on every
    quote — 296 of 300 ticks moved a rule. So the live quote is walked a
    quarter of a percent of the window at a time, one and a half windows up
    and back down: price is never painted outside its window, re-anchoring
    stays rare, a ten-cent wobble inside the band moves nothing, and a new
    payload still earns a new window."""
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}],
                       "put": [{"strike": 1680, "cluster_share_of_book_gamma_pp": 10}]}}
    got = _page(_board(scene, live={"ticker": "SNDK", "spot": 1700}), """
      const quote = async v => { NET.live = {ticker: 'SNDK', spot: v}; await run('loadSpot()'); return run('WIN'); };
      const start = run('WIN'), span = start.hi - start.lo;
      let win = start, anchors = 0, outside = 0, ticks = 0;
      for(const dir of [1, -1]){
        for(let i = 1; i <= 600; i++){
          const v = 1700 + dir * i * span * 0.0025, w = await quote(v);
          if(w !== win){ anchors++; win = w; }
          ticks++;
          if(v < w.lo || v > w.hi) outside++;
        }
        await quote(1700); await run('loadPayload()'); win = run('WIN');
      }
      const wallY = () => els.svg.innerHTML.match(/class="p-wall call"[^>]*y1="([\\d.]+)"/)[1];
      const inBand = await quote(start.hi - 0.08 * span), ys = new Set();
      let moved = 0;
      for(let i = 0; i < 50; i++){
        if(await quote(start.hi - 0.08 * span + (i % 2 ? 0.1 : -0.1)) !== inBand) moved++;
        ys.add(wallY());
      }
      NET.payload.scene.walls.call[0].strike = 1760;
      await run('loadPayload()');
      return {anchors, outside, ticks, wobble: {anchors: moved, wallYs: ys.size},
              followsPayload: run('WIN').hi >= 1760};""")
    assert got["outside"] == 0, "price was painted outside the window it is anchored in"
    assert got["anchors"] * 10 < got["ticks"], f"re-anchored on {got['anchors']} of {got['ticks']} ticks"
    assert got["wobble"] == {"anchors": 0, "wallYs": 1}, "a quote wobble in the band moved the board"
    assert got["followsPayload"], "a new payload kept the old window"


_PRICED = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40},
           "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}]}}


@pytest.mark.parametrize("width,scene,says", [
    (380, {"price": {"live_spot": 1700}}, "NO PRICE MEASURED"),
    (0, _PRICED, "CHART TOO NARROW"),
    (239, _PRICED, "CHART TOO NARROW"),
    (240, _PRICED, None),
], ids=["zero-span", "not-laid-out", "one-pixel-short", "wide-enough"])
def test_a_chart_it_cannot_draw_says_so_and_never_draws_nan(width, scene, says):
    """A zero span: with one_sigma_dollars absent the degenerate floor cannot
    fire, and one distinct core level gives a zero span. A browser silently
    falls back to 0 for each invalid length and renders garbage pinned to the
    top edge.

    A container too NARROW: width is the dimension that can still be zero — a
    card that has not laid out yet, or a hidden parent. The old guard measured
    HEIGHT, which is now a constant, and the old line clamped inline with
    Math.max(240, ...), which erased the very condition worth reporting."""
    svg = _page(_board(scene, width=width))["svg"]
    assert "NaN" not in json.dumps(svg)
    assert re.findall(r">(CHART TOO NARROW|NO PRICE MEASURED)<", svg["html"]) == ([says] if says else [])
    # sized by attribute, from the same numbers the viewBox carries
    assert svg["attrs"]["viewBox"] == f"0 0 {svg['attrs']['width']} {svg['attrs']['height']}"


def test_the_levels_card_asserts_no_direction():
    """"▲ NEXT ABOVE" / "▼ NEXT BELOW" came from the live price while the wall
    beside it came from a book up to minutes old, and the two could disagree on
    screen. The card now lists both walls and the most-contracts strike ORDERED
    BY PRICE, which says where each one sits without a word that can go stale.

    By price and never by kind: the most-contracts strike sat above the call
    wall on 10.9% of replayed scans and below the put wall on 1.4%, so a fixed
    call / most / put order would have drawn those upside down. (The above-call
    case is run in test_phone_levels; this runs the below-put one.)"""
    code = _code_only(PAGE) + _code_only(GLANCE)
    assert "NEXT ABOVE" not in code and "NEXT BELOW" not in code
    rows = _glance("""
      const walls = {call:[{strike:1720, cluster_share_of_book_gamma_pp:9}],
                     put:[{strike:1680, cluster_share_of_book_gamma_pp:8}]};
      const at = k => g.levelRows(walls, {strike:k, contracts:900}, null, 1700).map(r => [r.kind, r.strike]);
      console.log(JSON.stringify({below: at(1650), between: at(1700)}));""")
    # below the put wall: a fixed call / most / put order would draw it upside down
    assert rows["below"] == [["wall", 1720], ["wall", 1680], ["most", 1650]]
    # between them: the order the rows are assembled in (call, put, most) is not price order either
    assert rows["between"] == [["wall", 1720], ["most", 1700], ["wall", 1680]]


def test_the_clear_side_bracket_is_qualified_and_conditional():
    """call_side_has_no_wall means no CALL-SIGNED cluster above spot; a wrongly-signed
    pile there is dropped from both pools and the flag still fires — true on 79
    of 79 rows of the reference diary, over a cluster carrying 34.6% of book
    gamma. And a live tick can cross a wall of the other pool. Only === true
    draws it: a flag that is merely truthy is not a measurement."""
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 60},
             "magnet": {"top_strikes": [{"strike": 1780, "share_of_book_gamma_pp": 30}]},
             "walls": {"call_side_has_no_wall": True, "put": [{"strike": 1650, "cluster_share_of_book_gamma_pp": 9}]}}

    def words(**net):
        return re.findall(r">(NO (?:CALL|PUT) WALL (?:ABOVE|BELOW))<", _page(_board(scene, **net))["svg"]["html"])

    assert words() == ["NO CALL WALL ABOVE"]
    # a live tick under the put wall leaves a wall above price: the side is not empty as drawn
    assert words(live={"ticker": "SNDK", "spot": 1640}) == []
    scene["walls"]["call_side_has_no_wall"] = "true"
    assert words() == []


def test_an_absent_level_is_a_row_never_a_gap():
    """Law 1 on this card. A side flagged empty is a measured finding (no put
    wall on 32.2% of recent scans, every scan of 09-04 and 09-08), and it gets
    its own row saying so. No flag and no entry is no measurement, and says
    THAT — never a zero bar, never a missing row."""
    # the footer that used to carry the note is gone, and its bookkeeping with it
    assert "CLEAR_SAID" not in PAGE and "farSideNote" not in PAGE + GLANCE
    got = _glance("""
      console.log(JSON.stringify({
        empty: g.levelRows({call_side_has_no_wall:true, put_side_has_no_wall:true},
                           {strike:1700, contracts:12}, null, 1700).map(r => [r.side, r.kind, r.text || r.strike]),
        loose: g.levelRows({call_side_has_no_wall:'true', put_side_has_no_wall:1}, null, null, 1700)
                 .map(r => [r.side, r.text])}));""")
    # both measured-empty sides are rows, each on its own side of price
    assert got["empty"] == [["call", "absent", "None above price"], ["most", "most", 1700],
                            ["put", "absent", "None below price"]]
    # === true stays necessary: a flag that is merely truthy is not a measurement
    assert got["loose"] == [["call", "Not measured"], ["most", "Not measured"], ["put", "Not measured"]]


def test_the_tag_cap_respects_the_never_drop_tiers():
    """Without the tier a cap overflow could drop a heaviest_wall_behind_the_ladder, leaving the
    thickest stroke on the plot with its price nowhere on screen. Nine marks want a tag here —
    the price chip, both nearest walls, both heaviest walls behind them, both second walls, the
    lead magnet and VWAP — against a cap of seven."""
    scene = {"price": {"live_spot": 1700, "vwap_minus_live_spot_sigma": 0.3}, "scale": {"one_sigma_dollars": 100},
             "magnet": {"top_strikes": [{"strike": 1712, "share_of_book_gamma_pp": 30}]},
             "walls": {"call": [{"strike": 1740, "cluster_share_of_book_gamma_pp": 12},
                                {"strike": 1760, "cluster_share_of_book_gamma_pp": 5}],
                       "put": [{"strike": 1660, "cluster_share_of_book_gamma_pp": 10},
                               {"strike": 1640, "cluster_share_of_book_gamma_pp": 4}],
                       "call_heaviest_wall_behind_the_ladder": {"strike": 1780, "cluster_share_of_book_gamma_pp": 40},
                       "put_heaviest_wall_behind_the_ladder": {"strike": 1620, "cluster_share_of_book_gamma_pp": 30}}}
    page = _page(_board(scene))
    tags = [text for _, text in _svg_texts(page, "p-tag")]
    assert "1,780" in tags and "1,620" in tags, f"a heaviest wall behind the ladder lost its tag: {tags}"
    assert len(tags) + len(_svg_texts(page, "p-chiptx")) == 7


def test_a_dropped_request_does_not_blank_the_board():
    """visibilitychange fires loadPayload on wake — exactly when the radio has
    just reassociated — and a transport failure was byte-identical to an empty
    station."""
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}]}}
    got = _page(_board(scene), """
      const before = JSON.stringify(dump());
      NET.down = true; await run('loadPayload()');
      const dropped = JSON.stringify(dump()) === before;
      NET.down = false; NET.payload = {error: 'the station returned nothing'}; await run('loadPayload()');
      return {dropped, empty: JSON.stringify(dump()) === before};""")
    assert got == {"dropped": True, "empty": True}, "a failed request blanked a board holding a good payload"
    # with nothing to fall back on, the failure is said out loud
    cold = _page(_board(scene, down=True))
    assert cold["body"] == "failed" and cold["fail"]["hidden"] is False
    assert cold["fail1"]["text"] == "No SNDK scene yet."


def test_the_card_text_cannot_be_smeared_by_a_deficit():
    """The finding this test was written for, carried forward to the card that
    replaced the gate.

    .g-foot was the only gate child whose overflow:hidden zeroed its automatic
    minimum, so a 7px deficit in the old fixed-height column landed entirely on
    it and a sanctioned sentence rendered as an 11px slice of an 18px line. The
    guard stays on the property that CAUSED the smear rather than on the
    heights that delivered it: no zero automatic minimum, no nowrap sentence,
    no fixed height on the card."""
    for sel in (".lv-cap{", ".lv-none{", ".lv-n{"):
        b = _block(sel)
        assert b is not None, f"{sel} has no rule"
        flat = b.replace(" ", "")
        assert "overflow:hidden" not in flat, f"{sel} can be squeezed to nothing again"
        assert "white-space:nowrap" not in flat, f"{sel} is a nowrap sentence in a card that can shrink"
    card = _block(".levels{") or ""
    assert "height" not in card, "the card has a fixed height; the deficit comes back"


def test_the_height_budget_is_gone_rather_than_merely_unused():
    """Two tests used to live here: one recomputed FIXED from the region
    heights, one pinned the viewport measure sizeLadder chose its branch from.
    Both guarded a fixed-height column that no longer exists.

    Deleting them outright would leave nothing saying the budget must not come
    back — and it is exactly the kind of thing that gets reintroduced by
    someone trying to stop the page scrolling. So this asserts its ABSENCE.
    The layout-safety file guards what replaced it."""
    for gone in ("--r-mast", "--r-regime", "--r-gate", "--r-read", "--r-foot",
                 "max-height:700px"):
        assert gone not in PHONE, f"the fixed height budget is back: {gone}"
    assert "FIXED" not in PAGE, "sizeLadder is deriving a height again"


def test_the_row_label_wraps_rather_than_losing_its_last_tag():
    """A row label is a list of tags — "Put wall · Most contracts · Heaviest ·
    Price passed it" — and the LAST tag is the one that changes minute to
    minute. An ellipsis eats the end first, so a truncating label would drop
    "Price passed it" and leave a greyed strike with no word for why. It wraps."""
    side = _block(".lv-side{")
    assert side is not None
    flat = side.replace(" ", "")
    assert "text-overflow:ellipsis" not in flat and "white-space:nowrap" not in flat
    assert "min-width:0" in flat


def test_gminutes_cannot_print_sixty():
    """Math.round(m % 60) returns 60 for the last thirty seconds of every hour,
    and the chip repaints every 5s."""
    got = _glance("""
      const bad = [];
      for(let i = 0; i <= 60*24*20; i++){
        const m = i / 20, s = g.gMinutes(m);
        const hm = /^(\\d+)h(?: (\\d+)m)?$/.exec(s), mm = /^(\\d+)m$/.exec(s);
        if(s === 'just now' ? m >= 1 : hm ? +(hm[2] || 0) >= 60 : mm ? +mm[1] >= 60 : true) bad.push([m, s]);
      }
      console.log(JSON.stringify({bad: bad.slice(0, 5), edge: [59.49, 59.5, 119.5, 89.5].map(g.gMinutes)}));""")
    assert got["bad"] == [], got["bad"]
    assert got["edge"] == ["59m", "1h", "2h", "1h 30m"]


def test_the_named_edge_carries_the_weight_the_bug_cannot():
    """The marker the gate NAMES is heavier than the ones it does not. 700, not
    600: Roboto — the fallback whenever the webfont has not landed — ships no
    600 at all, so a requested 600 resolves upward to 700 and the emphasis
    silently collapses into the plain weight beside it."""
    # the nearest call wall pushed off the plot is the one named; the heavier pile behind it is not
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1800, "cluster_share_of_book_gamma_pp": 12}],
                       "put": [{"strike": 1680, "cluster_share_of_book_gamma_pp": 10}],
                       "call_heaviest_wall_behind_the_ladder": {"strike": 1900, "cluster_share_of_book_gamma_pp": 40}}}
    assert _svg_texts(_page(_board(scene)), "p-edge") == [("p-edge", "▲ 1,900 HEAVIEST"), ("p-edge lead", "▲ 1,800")]
    lead = _block(".p-edge.lead{")
    base = _block(".p-edge{")
    assert base is not None, ".p-edge has no rule"
    assert lead is not None, ".p-edge.lead has no rule"
    # WEIGHT, not just fill. Both rules used to be 700 and differed only in
    # colour, which makes the distinction contrast-only — and the stylesheet's
    # own law is that subordination is by size or weight, because a
    # low-contrast grey read outdoors is gone.
    import re as _re
    bw = _re.search(r"font:(\d+)", base)
    lw = _re.search(r"font-weight:(\d+)", lead)
    assert bw and lw, "the two edge rules no longer state a weight"
    assert int(lw.group(1)) > int(bw.group(1)), \
        "the named edge is no heavier than an unnamed one; the cue is contrast alone"
    assert "fill:var(--i)" in lead.replace(" ", "")


# --- the typeface, and the one header that makes it affordable -------------
# The phone downloads a 27 KB variable font. Everything else this server sends
# is `no-cache`, and it sends no ETag and no Last-Modified — so a revalidation
# cannot return 304 and a no-cache font is re-fetched in full on every app
# open, over a tunnel, on cell data. The filename's own content hash is what
# makes a year-long immutable cache safe instead of reckless.

def test_only_a_content_hashed_name_earns_an_immutable_cache():
    assert server._looks_hashed("pjs-153fc85b7029")          # 12 hex, the real one
    assert server._looks_hashed("x-0123456789abcdef")        # longer is fine
    assert not server._looks_hashed("pjs")                   # no hash at all
    assert not server._looks_hashed("pjs-153fc85b702")       # 11 hex, one short
    assert not server._looks_hashed("pjs-153fc85b7029g")     # g is not hex
    assert not server._looks_hashed("PlusJakartaSans-OFL")   # words, not a hash


def test_an_unhashed_font_falls_back_to_no_cache(tmp_path):
    """The failure worth having. An unhashed font served immutable can never be
    replaced — every phone that fetched it holds it for a year and no edit on
    the mini reaches them. Wasting bandwidth is the cheaper mistake.

    Judged on the headers the real _send_file sends: the rule is (woff2 AND
    hashed), not (woff2 OR hashed)."""
    shipped = sorted((server.STATIC / "m").glob("*.woff2"))
    assert shipped, "the phone's typeface is missing from static/m"
    h = _Wire()
    h._send_file(shipped[0])
    assert h.status == 200 and h.sent["Content-Type"] == "font/woff2"
    assert h.sent["Cache-Control"] == "public, max-age=31536000, immutable"   # one year

    unhashed = tmp_path / "PlusJakartaSans.woff2"
    unhashed.write_bytes(b"wOF2")
    h = _Wire()
    h._send_file(unhashed)
    assert h.sent["Cache-Control"] == "no-cache", "an unhashed font is cached forever"

    hashed_js = tmp_path / "glance-0123456789abcdef.js"
    hashed_js.write_text("0")
    h = _Wire()
    h._send_file(hashed_js)
    assert h.sent["Cache-Control"] == "no-cache", "a hash alone earned an immutable cache"

    h = _Wire()
    h._send_file(M / "index.html")
    assert h.sent["Cache-Control"] == "no-cache", "the phone page itself is cached"


def test_the_font_is_on_disk_and_named_by_its_own_bytes():
    import hashlib
    fonts = sorted((server.STATIC / "m").glob("*.woff2"))
    assert fonts, "the phone's typeface is missing from static/m"
    for f in fonts:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
        assert f.stem.endswith(digest), (
            f"{f.name} claims a hash its bytes do not match — "
            f"immutable caching would pin the wrong file for a year")
        assert f.read_bytes()[:4] == b"wOF2", f"{f.name} is not a woff2"


def test_the_font_ships_its_licence():
    """Plus Jakarta Sans is OFL, which permits redistribution and requires the
    licence to travel with the font."""
    assert (server.STATIC / "m" / "PlusJakartaSans-OFL.txt").is_file()


def test_the_two_phone_pages_share_one_palette():
    """The glance and the reads page each carry their own :root block, and 25
    tokens are declared in both. Duplicated bytes are not the risk — DRIFT is:
    somebody darkens --i-mute on one page and the other quietly disagrees.

    A shared stylesheet would cost more than it saves. This server sends
    Cache-Control: no-cache to everything that is not a content-hashed woff2,
    and sends no ETag, so a revalidation cannot come back 304 — a shared file
    would be re-fetched in full on every open, plus a render-blocking round
    trip before first paint on both pages. So the pages keep their own copies
    and this test guards the only thing the shared file was for."""
    import re

    def tokens(css):
        m = re.search(r"(?ms)^:root\{(.*?)^\}", css)
        assert m, "no :root block"
        return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", m.group(1)))

    a, b = tokens(PHONE), tokens(THREAD)
    shared = set(a) & set(b)
    assert len(shared) >= 20, f"the pages have stopped sharing a palette ({len(shared)} tokens)"
    drift = {k: (a[k].strip(), b[k].strip()) for k in shared if a[k].strip() != b[k].strip()}
    assert not drift, f"the two phone pages disagree about {drift}"


# --- weight as shade, and the marks that came with it ----------------------

def test_the_shade_is_a_spread_across_the_board_not_a_division():
    """REVERSED 2026-09-17, from "one darkness means one fact on every day".

    That scale divided each share by a fixed 13.0pp and it failed at both ends:
    the lightest strikes the scan measured came out at 0.054 opacity, which is a
    measurement drawn as an absence, and early in the session — when the
    heaviest share runs 18pp — everything above 13 flattened into one black.
    Dividing by the day's own heaviest fixes the top and not the bottom, and
    the divisor dilutes 35% between the open and the close, so a pile that never
    changed would appear to darken by half.

    Pinning BOTH ends answers both. The lightest measured strike takes the
    floor, the heaviest the ceiling, and the rest spread between by value — so
    what the band says is where the weight sits relative to the rest of the
    board, which is the question it exists to answer."""
    board = {"rows": [{"strike": 1500, "contracts_share_pp": 12.17},
                      {"strike": 1510, "contracts_share_pp": 2.32},
                      {"strike": 1520, "contracts_share_pp": 7.245}]}
    got = _glance("console.log(JSON.stringify(g.weightBands(D).map(b => [b.y, b.weight])));", board)
    assert dict(got) == {1500: 1, 1510: 0, 1520: pytest.approx(0.5)}
    # the same SHAPE on a board an order of magnitude lighter draws identically:
    # the mark reports rank and spread, and says nothing about absolute size
    light = {"rows": [{"strike": 1500, "contracts_share_pp": 1.217},
                      {"strike": 1510, "contracts_share_pp": 0.232},
                      {"strike": 1520, "contracts_share_pp": 0.7245}]}
    lit = _glance("console.log(JSON.stringify(g.weightBands(D).map(b => [b.y, b.weight])));", light)
    assert [k for k, _ in lit] == [k for k, _ in got]
    assert [w for _, w in lit] == pytest.approx([w for _, w in got])
    # nothing caps any more, because the heaviest IS the top of the scale
    assert "capped" not in json.dumps(got)
    # a board with no spread at all sits in the middle: neither "all heaviest"
    # nor "all lightest" is true of it
    flat = {"rows": [{"strike": k, "contracts_share_pp": 5} for k in (1500, 1510, 1520)]}
    assert [w for _, w in _glance(
        "console.log(JSON.stringify(g.weightBands(D).map(b => [b.y, b.weight])));", flat)] == [0.5, 0.5, 0.5]

def test_a_hole_in_the_strike_grid_stays_a_hole():
    """A strike's shade covers half a TYPICAL step, never half the gap to a
    distant neighbour.

    On 2026-09-16 the measured list ran 1545, 1550, then 1600. Extending each
    band to the midpoint smeared 1550 twenty-five dollars upward and painted
    shade over 1555-1575, where the scan measured no contracts at all — the
    chart inventing a pile out of the spacing between two real ones."""
    board = {"rows": [{"strike": 1540, "contracts_share_pp": 5},
                      {"strike": 1545, "contracts_share_pp": 5},
                      {"strike": 1550, "contracts_share_pp": 7},
                      {"strike": 1600, "contracts_share_pp": 11}]}
    bands = _glance("console.log(JSON.stringify(g.weightBands(D).map(b => [b.y, b.lo, b.hi])));",
                    board)
    top = dict((y, (lo, hi)) for y, lo, hi in bands)
    assert top[1550][1] == 1552.5 and top[1600][0] == 1597.5
    # nothing at all is painted across the empty middle of the gap
    assert not [1 for _, lo, hi in bands if lo < 1590 and hi > 1560]


def test_a_strike_the_scan_did_not_measure_gets_no_shade():
    """Honest-absent, on the mark that would be easiest to fake. A missing
    share is not a light band, and a zero is not a faint one."""
    board = {"rows": [{"strike": 1500, "contracts_share_pp": 10},
                      {"strike": 1510},
                      {"strike": 1520, "contracts_share_pp": None},
                      {"strike": 1530, "contracts_share_pp": 0},
                      {"strike": 1540, "contracts_share_pp": 4}]}
    got = _glance("console.log(JSON.stringify(g.weightBands(D).map(b => b.y)));", board)
    assert got == [1500, 1540]


def test_the_read_marks_come_from_the_payload_and_never_from_a_guess():
    """`reads_today` is a wrapper field, so a payload built before it existed
    draws no marks rather than marks reconstructed from whatever rows the
    journal tail happened to carry."""
    got = _glance("""console.log(JSON.stringify({
        good: g.readPoints(D.reads).map(p => p.s),
        none: g.readPoints(undefined),
        junk: g.readPoints([{ts: 'not a time', spot: 5}, {ts: D.reads[0].ts}])}));""",
                  {"reads": [{"ts": "2026-09-16T15:11:19-04:00", "spot": 1513.45},
                             {"ts": "2026-09-16T09:31:31-04:00", "spot": 1554.65}]})
    assert got["good"] == [1554.65, 1513.45]      # oldest first, whatever order it arrived in
    assert got["none"] == [] and got["junk"] == []


def test_a_wall_the_day_cannot_reach_does_not_anchor_the_window():
    """The window made room for the nearest wall on each side whatever the
    distance, subject only to the 1.75-sigma exile radius. On 2026-09-16 that
    radius was $115, the call wall sat 83 dollars above price, and the day's own
    $47.33 of movement drew inside 42.3% of the plot — 61 pixels of the 144 the
    price line has, with the rest held open for a level price never came near.

    The intent was right and the reach was not, so the test is the day's own
    room rather than a multiple of a typical day's. The wall is not dropped: it
    goes to the optional set, and the same board admits the put wall 17 dollars
    away (it fits) while refusing the call wall 83 away — which then becomes a
    named edge marker, so nothing reaches no pixel and no name."""
    scene = {"price": {"live_spot": 1517.0001, "session_high": 1560.5799,
                       "session_low": 1513.25, "vwap": 1534.58},
             "scale": {"one_sigma_dollars": 65.82,
                       "expected_move_today_asym": {"up_dollars": 15.43, "down_dollars": 13.99}},
             "walls": {"call": [{"strike": 1600, "cluster_share_of_book_gamma_pp": 1.67}],
                       "put": [{"strike": 1500, "cluster_share_of_book_gamma_pp": 20.44}]}}
    js = """
      const p = D.price, range = p.session_high - p.session_low;
      const w = g.solveWindow(g.coreLevels(D, p.live_spot, p.vwap, []),
                              g.optionalLevels(D, p.live_spot),
                              p.live_spot, D.scale.one_sigma_dollars, range);
      console.log(JSON.stringify({span: +(w.hi - w.lo).toFixed(2),
                                  share: +(range / (w.hi - w.lo) * 100).toFixed(1),
                                  admitted: w.admitted.map(l => l.y),
                                  refused: w.refused.map(l => l.y)}));"""
    got = _glance(js, scene)
    assert got["admitted"] == [1500] and got["refused"] == [1600]
    assert got["span"] == 67.85                 # was 112.00, anchored on 1600
    assert got["share"] == 69.8                 # was 42.3

    # NO DATUM, NO CHANGE. A scene that never measured the day's room keeps the
    # behaviour it had rather than having a distance invented for it.
    bare = dict(scene, scale={"one_sigma_dollars": 65.82})
    got = _glance(js, bare)
    assert got["admitted"] == [] and got["refused"] == []
    assert got["span"] == 112.0

    # and the rule is the DAY's room, not a fixed number of dollars: the same
    # board an hour before the close, when there is far less of it left, stops
    # anchoring the put wall it anchored at noon
    close = dict(scene, scale={"one_sigma_dollars": 65.82,
                               "expected_move_today_asym": {"up_dollars": 4.66, "down_dollars": 4.86}})
    got = _glance(js, close)
    assert got["admitted"] == [1500] and got["refused"] == [1600]


# --- the marks the respacing bought ----------------------------------------

_SHADE_SCENE = {
    "price": {"live_spot": 1517, "session_high": 1560.58, "session_low": 1513.25},
    "scale": {"one_sigma_dollars": 65.82,
              "expected_move_today_asym": {"up_dollars": 15.43, "down_dollars": 13.99}},
    "walls": {"call": [{"strike": 1600, "cluster_share_of_book_gamma_pp": 1.67}],
              "put": [{"strike": 1500, "cluster_share_of_book_gamma_pp": 20.44}]},
    "context": {"ranges": {"opening": {"high": 1560.58, "low": 1519.54}}},
    "strikes": {"rows": [{"strike": 1500, "contracts_share_pp": 12.17},
                         {"strike": 1510, "contracts_share_pp": 2.35},
                         {"strike": 1520, "contracts_share_pp": 4.74},
                         {"strike": 1530, "contracts_share_pp": 10.34},
                         {"strike": 1540, "contracts_share_pp": 7.07},
                         {"strike": 1550, "contracts_share_pp": 7.49},
                         {"strike": 1600, "contracts_share_pp": 11.39}]}}


def _shades(svg):
    return [float(o) for o in re.findall(r'<rect class="p-shade"[^>]*style="opacity:([\d.]+)', svg)]


def test_every_strike_the_scan_measured_shows_its_weight():
    """Weight was a rule's THICKNESS, so it could only be spent on the two or
    three levels that earn a rule. On the 2026-09-16 board seven strikes inside
    the window carried contracts and the chart drew two of them — the shelf from
    1,540 to 1,550 reached no pixel at all. Shade costs no rule."""
    svg = _page(_board(_SHADE_SCENE))["svg"]["html"]
    shades = _shades(svg)
    # 1,600 is outside the window, the other six are in it
    assert len(shades) == 6
    # the ends are pinned: the heaviest strike on the board takes the ceiling and
    # the lightest takes the floor, which is what keeps a measured strike visible
    assert max(shades) == pytest.approx(0.30) and min(shades) == pytest.approx(0.08)
    # and nothing is drawn so faint that a measurement reads as an absence
    assert all(v >= 0.08 for v in shades)
    # a strike the scan did not measure gets nothing, not a faint band
    bare = json.loads(json.dumps(_SHADE_SCENE))
    bare["strikes"]["rows"][3].pop("contracts_share_pp")
    assert len(_shades(_page(_board(bare))["svg"]["html"])) == 5
    # and an era that ships no rows draws no shade rather than an empty field
    none = json.loads(json.dumps(_SHADE_SCENE)); none.pop("strikes")
    assert _shades(_page(_board(none))["svg"]["html"]) == []


def test_the_opening_half_hour_draws_both_its_own_edges():
    """It had no mark at all: the reading named it in prose and the chart never
    showed where it was. Both edges or neither — one line is a level, and a
    level is not what this is."""
    svg = _page(_board(_SHADE_SCENE))["svg"]["html"]
    assert len(re.findall(r'<line class="p-orb"', svg)) == 2
    half = json.loads(json.dumps(_SHADE_SCENE))
    half["context"]["ranges"]["opening"]["low"] = None
    assert re.findall(r'<line class="p-orb"', _page(_board(half))["svg"]["html"]) == []


def test_the_read_marks_stop_where_the_record_does():
    """A read is drawn ON the price line, so it needs a line under it. The
    newest read can be newer than the last bar the sidecar wrote — 24 reads
    against 23 placeable ones on the live 2026-09-16 board — and a mark past
    the end of the record would sit on nothing and read as a price."""
    bars = [{"ts": "2026-09-10T09:%02d:00-04:00" % (30 + i), "close": 1520 + i, "volume": 100000}
            for i in range(10)]
    reads = [{"ts": "2026-09-10T09:31:00-04:00", "spot": 1521},
             {"ts": "2026-09-10T09:36:00-04:00", "spot": 1526},
             {"ts": "2026-09-10T11:00:00-04:00", "spot": 1540}]      # past the tape
    svg = _page(_board(_SHADE_SCENE, now="2026-09-10T09:39:00-04:00",
                       payload={"reads_today": reads}, bars=bars))["svg"]["html"]
    assert len(re.findall(r'<circle class="p-read"', svg)) == 2
    # no field at all: no marks, never marks rebuilt from the journal tail
    svg = _page(_board(_SHADE_SCENE, now="2026-09-10T09:39:00-04:00", bars=bars))["svg"]["html"]
    assert "p-read" not in svg


def test_the_volume_ribbon_is_whole_blocks_on_one_scale():
    """A part-block at the live edge is a smaller sample on the same gauge as a
    full one, which reads as a lull that is really an unfinished minute. And the
    scale is fixed across sessions: scaled to the day's own maximum, the opening
    block runs many times the median and most of the session draws under a
    pixel — 27 of 69 blocks on 2026-09-16, against 5 on the fixed scale."""
    bars = [{"ts": "2026-09-10T09:%02d:00-04:00" % (30 + i), "close": 1520,
             "volume": 200000 if i < 5 else 10000} for i in range(12)]
    svg = _page(_board(_SHADE_SCENE, now="2026-09-10T09:41:00-04:00", bars=bars))["svg"]["html"]
    # twelve bars, five to a block: two whole blocks and a part-block dropped
    blocks = re.findall(r'<rect class="p-vol"[^>]*height="([\d.]+)"', svg)
    assert len(blocks) == 2
    # the busy block is past the scale, capped, and the cap is marked
    assert blocks[0] == "10.0" and float(blocks[1]) < 10.0
    assert len(re.findall(r'<rect class="p-clip"', svg)) == 1


# --- where the activity is, as a map around price --------------------------

def test_the_activity_panel_names_the_strikes_it_counts():
    """It printed "Newly busy: 7 strikes / Gone quiet: 10 strikes" and threw away
    every strike and every time the builder had already written down. On the live
    2026-09-17 board at 10:45 that count hid the story it existed to tell:
    everything newly busy sat within $22 ABOVE price and everything that went
    quiet sat just below it.

    Nearest to price first, because distance is what decides whether a change
    matters — a strike that went quiet two hundred dollars away is not news —
    and both sides run high price to low, which is the order they are drawn in."""
    day = {"joined": [1530, 1610, 1615, 1620, 1630, 1640, 1700],
           "left": [[1450, "10:07"], [1520, "10:32"], [1595, "10:36"], [1625, "09:59"]],
           "stood": [1600, 1605, 1650]}
    got = _glance("console.log(JSON.stringify(g.activityRows(D.day, D.price, 5)));",
                  {"day": day, "price": 1608.2022})
    assert [r["y"] for r in got["above"]] == [1630, 1625, 1620, 1615, 1610]
    assert [r["y"] for r in got["below"]] == [1605, 1600, 1595, 1530, 1520]
    assert got["moreAbove"] == 3 and got["moreBelow"] == 1
    # every strike lands in a lane or a count — nothing is dropped in silence
    assert got["counts"] == {"new": 7, "gone": 4, "held": 3}
    assert len(got["above"]) + got["moreAbove"] + len(got["below"]) + got["moreBelow"] == 14
    # the time it went quiet rides with the row, not a separate lookup
    assert [r["at"] for r in got["below"] if r["state"] == "gone"] == ["10:36", "10:32"]


def test_a_strike_that_arrived_and_then_went_quiet_reads_as_gone():
    """It is in BOTH lists the builder writes, and the reader is standing in the
    present: it is gone now. Taking the joined list last would draw a strike that
    is off the board as the newest thing on it."""
    day = {"joined": [1610], "left": [[1610, "10:32"]], "stood": []}
    got = _glance("console.log(JSON.stringify(g.activityRows(D.day, D.price, 5)));",
                  {"day": day, "price": 1600})
    assert [[r["y"], r["state"], r["at"]] for r in got["above"]] == [[1610, "gone", "10:32"]]


def test_the_state_word_is_printed_once_per_run():
    """Five rows reading "got busy" down a column is the same word five times.
    The word marks where the kind CHANGES, so a block of one kind reads as one
    thing — which is what makes the shape visible at a glance."""
    day = {"joined": [1610, 1615, 1620], "left": [[1625, "09:59"]], "stood": [1605, 1600]}
    got = _glance("console.log(JSON.stringify(g.activityRows(D.day, D.price, 5)));",
                  {"day": day, "price": 1608})
    assert [[r["y"], r["first"]] for r in got["above"]] == \
        [[1625, True], [1620, True], [1615, False], [1610, False]]
    assert [[r["y"], r["first"]] for r in got["below"]] == [[1605, True], [1600, False]]


def test_the_activity_map_is_absent_rather_than_empty():
    """No day block — an older payload, or the session's first look — and no
    price to sort around are both absences, not empty maps. A shape with nothing
    in it reads as "measured, and nothing happened"."""
    got = _glance("""console.log(JSON.stringify({
        noDay: g.activityRows(null, 1600, 5),
        noPrice: g.activityRows({joined: [1610]}, null, 5),
        noStrikes: g.activityRows({joined: [], left: [], stood: []}, 1600, 5)}));""")
    assert got == {"noDay": None, "noPrice": None, "noStrikes": None}


def test_the_strikes_the_model_named_that_have_since_gone():
    """The one line on this card that says "what I told you earlier is gone".
    On 2026-09-17 the model named 1,500 at 09:31 and that strike is no longer in
    the book at all; nothing on the phone showed it."""
    got = _glance("console.log(JSON.stringify(g.namedGone(D)));",
                  {"named_off_list": [{"strike": 1500, "named_at": "09:31", "in_book": False},
                                      {"strike": 1700, "named_at": "09:41"},
                                      {"named_at": "10:00"}]})
    assert got == [{"y": 1500, "at": "09:31", "inBook": False},
                   {"y": 1700, "at": "09:41", "inBook": True}]


def test_the_notes_group_the_strikes_that_have_gone_rather_than_listing_them():
    """The named-and-gone list grows through the session — five by midday on
    2026-09-17 — and a line apiece would push the footnotes past the ladder they
    are a footnote to. Grouped by what happened, every strike is still named and
    the block cannot exceed two lines. The clock survives only where there is one
    of them, because the strike is the fact and the time is the detail."""
    def notes(named):
        day = dict(_day_block(), joined=[1610], left=[], stood=[], named_off_list=named)
        return _overview(_page(_board({"price": {"live_spot": 1608.20},
                                       "scale": {"one_sigma_dollars": 40}, "day": day})))[5]
    got = notes([{"strike": 1615, "named_at": "10:24"},
                 {"strike": 1700, "named_at": "09:41"},
                 {"strike": 1500, "named_at": "09:31", "in_book": False}])
    assert got[:2] == [("said", "1,615 and 1,700 were named earlier and are now off the list."),
                       ("said", "1,500 was named at 09:31 and is now out of the book.")]
    # one of a kind keeps its clock
    assert notes([{"strike": 1615, "named_at": "10:24"}])[0] == \
        ("said", "1,615 was named at 10:24 and is now off the list.")
    # three or more still read as a sentence, and none is dropped
    got = notes([{"strike": k, "named_at": "10:00"} for k in (1615, 1700, 1720)])
    assert got[0][1] == "1,615, 1,700 and 1,720 were named earlier and are now off the list."
    # nothing named and gone: no line at all, not an empty one
    assert not [t for _, t in notes([]) if "named" in t or "book" in t]
