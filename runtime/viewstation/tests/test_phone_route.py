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


def _ago(minutes):
    """The stamp `minutes` before _NOW, or None for no stamp at all."""
    return None if minutes is None else (datetime.fromisoformat(_NOW) - timedelta(minutes=minutes)).isoformat()


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
run(fs.readFileSync(path.join(M, 'sheet.js'), 'utf8'));
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
    """Run the REAL page.js, over the real glance.js and sheet.js, in node
    against a stand-in DOM and a station that answers from `net` —
    {"payload", "now"}, and optionally "live" (/api/spot), "reads", "diary",
    "bars" (the raw file rows), "width" (the ladder's measured width) and
    "down" (nothing answers) — and return what `steps` returns.

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


# The gates the phone's word tests add to the reader's own _BANNED_RE and
# _POS_RE, one copy of each, so a word added to a gate reaches every card the
# gate guards.
# No dealer and nothing a dealer does, in any wording:
_DEALER = re.compile(r"(?i)\bdealers?\b|hedg|damp|amplif|cushion|defend|\bpush|\bpull|absorb")
# nothing that reaches forward or grades how often:
_AHEAD = re.compile(r"(?i)\b(?:will|would|could|might|may|shall|going to|tends?|usually|"
                    r"often|mostly|most|majority|likely|chance|odds|expect\w*)\b")
# the levels sheet's denylist, the textbook claims measured false on SNDK:
_SHEET_CLAIMS = ("buy dips", "sell rallies", "pinned", "settle at", "settles at", "bounce",
                 "break through", "a third of the time", "coin flip", "caps the", "holds price up",
                 "speed up")
_EMOJI_OR_GREEK = re.compile(r"[\U0001F300-\U0001FAFF]|[Ͱ-Ͽ]")


def test_the_phone_draws_no_regime_word(tmp_path, monkeypatch):
    """The row under the price led with the regime word until 2026-09-18. It
    was classify_regime()'s vote of four reads (reversion_lens.py) — gamma
    sign, range against the expected move, variance ratio, VIX term structure
    — calling "pinning" or "trending" only when two agree. On SNDK the last two
    are never fed (0 of 185 scans on 2026-09-17), so the word read "Neutral"
    because too few reads voted, not because anything measured a neutral
    market; and "Regime not measured" when the label was missing. The owner
    removed it.

    Before that its gloss read "walls hold" / "walls give way" off the gamma
    sign: a claim that hedging damps or speeds a move, which the model is
    forbidden to write (sndk_read.py's doctrine) and docs/sndk-plan.md records
    as measured absent on SNDK. Neither the word nor the sign reaches a pixel
    now. The builder still ships both, so the real page is painted from the
    real builder's scene under every label and every sign, and all of them
    paint alike. The row keeps its one figure, the usual day's move, and folds
    away when there is none rather than leaving an empty band."""
    code = _code_only(GLANCE) + _code_only(PAGE)
    for gone in ("walls hold", "walls give way", "Regime not measured", "regime_label"):
        assert gone not in code, gone
    for gone in ('id="regWord"', 'id="regGloss"'):
        assert gone not in PHONE, gone
    payload = _built_payload(tmp_path, monkeypatch)
    built = _phone_scene(payload)["regime"]
    assert built["gamma_sign"] == "negative" and built["regime_label"] == "trending", \
        "the builder no longer ships the label and the sign this test varies"
    variants = [("regime_label", v) for v in ("pinning", "neutral", None)] \
             + [("gamma_sign", v) for v in ("positive", "unknown", None)]
    first = _page({"payload": payload, "now": _BUILT_AT})
    for key, value in variants:
        p = json.loads(json.dumps(payload, default=str))
        regime = _phone_scene(p)["regime"]
        if value is None:
            regime.pop(key)
        else:
            regime[key] = value
        assert _page({"payload": p, "now": _BUILT_AT}) == first, \
            f"the page paints {key}={value!r} differently"
    painted = json.dumps(first).lower()
    for word in ("trending", "pinning", "neutral", "regime"):
        assert word not in painted, f"{word!r} reached the page"
    assert first["ruler"]["text"].startswith("USUAL DAY MOVE $")
    assert first["dayMove"]["hidden"] is False
    # no day's move: the row folds away rather than holding an empty band
    bare = _page(_board({"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": None}}))
    assert bare["ruler"]["text"] == "" and bare["dayMove"]["hidden"] is True
    # the footer names what every mark is, rather than caveating a claim
    assert "not a forecast of where price goes" in first["foot"]["text"]


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
    # on the chart both walls draw the same rule whatever their share — the one
    # with 15% and the one with none at all. 2.0, not 1.6, since 2026-09-18:
    # both are NEAREST walls, which is all a wall rule's width may say
    assert sorted(re.findall(r'<line class="p-wall (\w+)"[^>]*stroke-width:([\d.]+)', svg)) == \
        [("call", "2.0"), ("put", "2.0")]
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
    assert edges == ["▲ 1,900 BIGGEST PILE", "▲ 1,850", "▲ 1,800", "▼ 1,690 BIGGEST PILE"]
    # a refused level on the strike of a wall already ruled is not named twice
    day["walls"]["put"].append({"strike": 1690, "cluster_share_of_book_gamma_pp": 5})
    day["walls"]["put_heaviest_wall_behind_the_ladder"] = {"strike": 1680, "cluster_share_of_book_gamma_pp": 20}
    edges = [text for _, text in _svg_texts(_page(_board(day)), "p-edge")]
    assert edges == ["▲ 1,900 BIGGEST PILE", "▲ 1,850", "▲ 1,800", "▼ 1,690"]


def test_the_magnet_never_shares_a_gauge_with_anything_else():
    """top_strikes shares are a fraction of mass_by_strike; a wall's share is a
    fraction of net_by_strike; the bars behind both count the contracts traded
    today (the shade before them, until 2026-09-18, was a share of the
    contracts on the board). Different quantities, and no two may share a gauge.

    The wall's rail bar used to carry the second of those as a LENGTH beside a
    rule carrying it as a THICKNESS. Both went on 2026-09-16 when weight became
    shade, so the gutter now holds marks that say WHICH level a row is — a
    diamond for the magnet — and never how much. The traded bars are lengths,
    and they live inside the plot, not in the gutter."""
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
    # and every wall rule is the same weight, whatever its share. Both are
    # nearest here, so both 2.0; a second wall is run in
    # test_no_mark_on_the_plot_is_eaten_by_the_shade_behind_it
    widths = set(re.findall(r'<line class="p-wall \w+"[^>]*stroke-width:([\d.]+)', svg))
    assert widths == {"2.0"}, "a wall rule is drawing its share as thickness again"

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


def test_a_magnet_on_a_walls_strike_is_one_rule_that_says_both():
    """A magnet on a wall's strike is ONE rule. They share a strike on 49 of 66
    scans over 2026-09-15..17 (74%, INK-SPEC.md 1.4), and two rules on one row
    is one the reader cannot see: a 2.0px wall over a 2.2px magnet leaves a
    0.10px sliver of amber. mergeLevels already folded the magnet into the wall,
    and the one rule it left said nothing of the magnet: it drew the wall solid,
    and the long dash, the thing that says "busiest strike", was gone.

    The rule keeps the wall's colour for the side and takes the magnet's 6 3
    dash (SIDE-SPEC.md 3, ruling 5). Where the two sit at different prices,
    as on 17 of 26 scans of 09-17, each keeps a rule of its own."""
    scene = {"price": {"live_spot": 1517}, "scale": {"one_sigma_dollars": 65.82},
             "magnet": {"top_strikes": [{"strike": 1500, "share_of_book_gamma_pp": 11.5},
                                        {"strike": 1530, "share_of_book_gamma_pp": 9.78}]},
             "walls": {"call": [{"strike": 1560, "cluster_share_of_book_gamma_pp": 4}],
                       "put": [{"strike": 1500, "cluster_share_of_book_gamma_pp": 20.44}]}}

    def rules(sc):
        svg = _page(_board(sc))["svg"]["html"]
        return sorted((c, float(y)) for c, y in re.findall(r'<line class="(p-(?:wall|mag|magrun)[^"]*)"[^>]*y1="([\d.]+)"', svg))

    shared = rules(scene)
    # 1,500: the put wall and the lead magnet, one rule, dashed; 1,530 the runner; 1,560 the call wall
    assert [c for c, _ in shared] == ["p-magrun", "p-wall call", "p-wall put mag"]
    assert len({y for _, y in shared}) == 3, "two rules share a row"
    # the magnet's own dash, and nothing else: the colour and the width stay the wall's
    mag_dash = re.search(r"stroke-dasharray:[^;]+", _css_rule(".p-mag")).group(0)
    assert _css_rule(".p-wall.mag").rstrip(";") == mag_dash
    # a magnet a strike away from the wall keeps an amber rule of its own, and the wall stays solid
    apart = json.loads(json.dumps(scene))
    apart["magnet"]["top_strikes"][0]["strike"] = 1505
    assert [c for c, _ in rules(apart)] == ["p-mag", "p-magrun", "p-wall call", "p-wall put"]


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
    assert (old["fresh"]["text"], old["fresh"]["cls"]) == ("SCAN 3 HR OLD", "fresh bad")
    unstamped = _page(_board(scene, payload={"row_ts": None}))
    assert (unstamped["fresh"]["text"], unstamped["fresh"]["cls"]) == ("SCAN AGE UNKNOWN", "fresh bad")


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
    live = _page(_board(scene, payload={"row_ts": "2026-09-10T10:59:00-04:00"}))
    assert _right_foot(live) == ["2 HR 58 MIN LEFT"] and live["ldWhen"]["text"] == "AS OF 10:59"
    # two hours stale: the axis ends at the scan, which the card's head names
    stale = _page(_board(scene, payload={"row_ts": "2026-09-10T09:00:00-04:00"}))
    assert _right_foot(stale) == ["09:00"] and stale["ldWhen"]["text"] == "AS OF 09:00"


def test_the_chart_cards_head_names_what_it_shows_and_when():
    """The card was headed "Today", and "SCAN 15:11" sat at the right end of
    the axis with nothing on the head to match it (WORDS-SPEC #8, #9, #12). The
    head now says what the chart is, the day's price, and at its right the scan
    it was drawn from, so a stale axis ends on a bare clock and a live one keeps
    its countdown. No stamp, no clock: the head says nothing rather than guess
    one. It is written before the chart decides whether it can draw, so a card
    too narrow for the plot still says which scan it would have shown."""
    card = PHONE.split('<section class="card">')[1].split("</section>")[0]
    assert '<div class="lab">Price today<span class="r" id="ldWhen"></span></div>' in card
    scene = {"clock": {"minutes_to_close": 178}, "price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}]}}
    unstamped = _page(_board(scene, payload={"row_ts": None}))
    assert unstamped["ldWhen"]["text"] == "" and _right_foot(unstamped) == []
    narrow = _page(_board(scene, payload={"row_ts": "2026-09-10T10:59:00-04:00"}, width=200))
    assert "too-narrow" in narrow["svg"]["cls"] and narrow["ldWhen"]["text"] == "AS OF 10:59"


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
    assert old["rdAge"]["text"] == "4 HR 11 MIN AGO"
    assert old["rdLine"]["text"] == "06:49 · The old sentence."
    none = _page(_board(scene))
    assert none["rdLine"]["text"] == "No reading yet today." and none["rdAge"]["text"] == ""


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
    # --i-mute, not --rule-soft, since 2026-09-18: the rule crosses the shade,
    # and --rule-soft fails 3:1 over any band darker than 0.05
    assert ".p-wall.passed{stroke:var(--i-mute)}" in PHONE
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
    where = {"session_date": ("clock",), "live_spot": ("price",),
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
    assert got["expiry"]["text"] == "OPTIONS END FRI"                    # 2026-08-21
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
    assert got["ruler"]["text"] == "USUAL DAY MOVE $80"
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
    assert _right_foot(stale) == ["12:12"] and stale["ldWhen"]["text"] == "AS OF 12:12"
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


def test_a_side_measured_empty_is_said_on_the_card_and_not_in_the_plot():
    """NO CALL WALL ABOVE / NO PUT WALL BELOW came off the chart on 2026-09-18,
    by the owner's choice: the text stacked in the plot's upper left goes. The
    thin bracket down the plot's left edge that scoped the word goes with it,
    because without its word it is an unexplained mark.

    The finding stays on the screen. A side measured empty keeps its own row
    on the levels card, "None above price", which
    test_an_absent_level_is_a_row_never_a_gap holds for the qualified flag.
    This is the board that used to draw the word: the flag is set and nothing
    of the other pool sits above price."""
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 60},
             "magnet": {"top_strikes": [{"strike": 1780, "share_of_book_gamma_pp": 30}]},
             "walls": {"call_side_has_no_wall": True, "put_side_has_no_wall": True}}
    page = _page(_board(scene))
    svg = page["svg"]["html"]
    assert "p-brk" not in svg and "p-word" not in svg
    assert not re.search(r"(?i)\bno (?:call|put|big)\b", svg), "a clear-side word is back in the plot"
    rows = {label: kids for _, label, kids in _card(page)}
    assert rows["Call wall"] == {"lv-none": ("None above price", None)}
    assert rows["Put wall"] == {"lv-none": ("None below price", None)}
    # gone, not switched off: the chart no longer reads the flag at all
    assert "_side_has_no_wall" not in _code_only(PAGE)
    assert "clearRow" not in PAGE and ".p-brk" not in PHONE


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
    and the chip repaints every 5s. Every form spells its unit (WORDS-SPEC #5):
    "1m" read as a month as easily as a minute."""
    got = _glance("""
      const bad = [];
      for(let i = 0; i <= 60*24*20; i++){
        const m = i / 20, s = g.gMinutes(m);
        const hm = /^(\\d+) hr(?: (\\d+) min)?$/.exec(s), mm = /^(\\d+) min$/.exec(s);
        if(s === 'just now' ? m >= 1 : hm ? +(hm[2] || 0) >= 60 : mm ? +mm[1] >= 60 : true) bad.push([m, s]);
      }
      console.log(JSON.stringify({bad: bad.slice(0, 5), edge: [59.49, 59.5, 119.5, 89.5].map(g.gMinutes)}));""")
    assert got["bad"] == [], got["bad"]
    assert got["edge"] == ["59 min", "1 hr", "2 hr", "1 hr 30 min"]


def test_every_age_on_the_screen_says_what_it_is_the_age_of():
    """gMinutes feeds four places, and "LAST SCAN 1M" and the reading card's
    "14M" read as months as easily as minutes (WORDS-SPEC #5, #31). Each caller
    now spells the unit and says what the age is of: the masthead's pill is the
    scan's age, or the book's while the payload is live; the reading card says
    how long ago; the chart's foot counts down what is left of the session; the
    line under a withdrawn price says how long ago that price was."""
    scene = {"clock": {"minutes_to_close": 135}, "price": {"live_spot": 1700},
             "scale": {"one_sigma_dollars": 40},
             "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}]}}
    def paint(minutes, read_min=14, board=scene, **payload):
        said = [{"ts": _NOW, "reading_ts": _ago(read_min), "reading": {"read": "Said."}}]
        return _page(_board(board, payload={"row_ts": _ago(minutes), **payload}, reads=said))

    live = paint(1, as_of="live")
    assert (live["fresh"]["text"], live["fresh"]["cls"]) == ("BOOK 1 MIN OLD", "fresh")
    assert live["rdAge"]["text"] == "14 MIN AGO"
    assert _right_foot(live) == ["2 HR 15 MIN LEFT"]
    scan = paint(14, read_min=0.2)
    assert (scan["fresh"]["text"], scan["fresh"]["cls"]) == ("SCAN 14 MIN OLD", "fresh warn")
    assert scan["rdAge"]["text"] == "JUST NOW"
    assert paint(0.5, as_of="live")["fresh"]["text"] == "JUST SCANNED"
    assert paint(None)["fresh"]["text"] == "SCAN AGE UNKNOWN"
    # no quote and past the heartbeat: the price is withdrawn and its line says when it was
    gone = paint(180)
    assert gone["px"]["text"] == "—" and gone["lastscan"]["text"] == "LAST SCAN 1,700.00 · 3 HR AGO"
    assert _right_foot(paint(1, board=dict(scene, clock={"minutes_to_close": 48}), as_of="live")) == ["48 MIN LEFT"]
    for got in (live, scan, gone):
        for region in ("fresh", "rdAge", "lastscan"):
            assert not re.search(r"\d[MH]\b", got[region]["text"]), got[region]["text"]


def test_the_pill_gives_up_its_subject_before_the_expiry_leaves_the_row():
    """The masthead's first row holds the ticker, the expiry and the pill, and
    a narrow phone cannot hold all three whole: the spec's pair runs 13px past
    a 320px phone, and on an expiry day "OPTIONS END TODAY" beside the live dot
    leaves the owner's 360px phone 2.93px short of "BOOK 1 MIN OLD". The
    fallback is the one WORDS-SPEC 9.1 measured, the age without its subject.
    It is chosen on the row as laid out, because the widths turn on the
    weekday, the dot and the age, whose figures are proportional in the pill:
    a rule on the phone's width alone would strip the subject from every
    expiry day at 375 or overrun on the day it misjudged. Only when the bare
    age still does not fit does the expiry leave the row, whole, for a line
    under the ticker; that is the stylesheet's wrap, pinned in
    test_phone_layout_safety.

    The row is laid out by hand here: the expiry drops under the ticker
    whenever the pill is longer than a limit, which is what a narrower phone
    does. A layout that measured nothing keeps the words whole."""
    scene = {"clock": {"front_expiry": {"days_to_expiry": 0, "expiry_date": "2026-09-10"}},
             "price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40}}
    steps = """
      const first = els.fresh.textContent;
      const lay = (limit, empty) => {
        els.ticker.getBoundingClientRect = () => ({top: 0, bottom: 11, height: 11});
        els.expiry.getBoundingClientRect = () => els.fresh.textContent.length <= limit ? {top: 0, bottom: 11, height: 11}
                                               : empty ? {top: 18, bottom: 18, height: 0} : {top: 18, bottom: 29, height: 11};
        run('paintMast(state())');
        return els.fresh.textContent;
      };
      return {first, expiry: els.expiry.textContent, whole: lay(99), bare: lay(11), neither: lay(0),
              empty: lay(0, true)};"""
    for row_ts, as_of, said, bare in ((_ago(1), "live", "BOOK 1 MIN OLD", "1 MIN AGO"),
                                      (_ago(9), "last scan", "SCAN 9 MIN OLD", "9 MIN AGO"),
                                      (_ago(0.5), "live", "JUST SCANNED", "JUST NOW"),
                                      (None, "last scan", "SCAN AGE UNKNOWN", "AGE UNKNOWN")):
        got = _page(_board(scene, payload={"row_ts": row_ts, "as_of": as_of}), steps)
        assert got["expiry"] == "OPTIONS END TODAY"
        assert got["first"] == got["whole"] == said
        assert got["bare"] == got["neither"] == bare, "the pill kept its subject in a row too narrow for it"
        # an expiry with nothing in it has no line to leave
        assert got["empty"] == said


def _plain_words():
    """Every string the plain-words pass (WORDS-SPEC.md) put on the main
    screen, read off what the page paints across the states that print them:
    live and last-scan, just scanned, age unknown, withdrawn, an expiry day, a
    week with no expiry date, a day with no reading, a wall named past the
    edge, and the activity card's notes on one claim and on several."""
    said = [{"ts": _NOW, "reading_ts": _ago(14), "reading": {"read": "Said."}}]
    words = set()
    for fe, minutes, as_of, reads in (({"days_to_expiry": 0, "expiry_date": "2026-09-10"}, 1, "live", said),
                                      ({"days_to_expiry": 1, "expiry_date": "2026-09-11"}, 14, "last scan", []),
                                      ({"days_to_expiry": 3}, 0.5, "live", said),
                                      ({"days_to_expiry": 1}, None, "last scan", said),
                                      ({"days_to_expiry": 1, "expiry_date": "2026-09-11"}, 180, "last scan", said)):
        scene = {"clock": {"minutes_to_close": 135, "front_expiry": fe}, "price": {"live_spot": 1700},
                 "scale": {"one_sigma_dollars": 40},
                 "walls": {"call": [{"strike": 1720, "cluster_share_of_book_gamma_pp": 12}],
                           "call_heaviest_wall_behind_the_ladder": {"strike": 1900,
                                                                    "cluster_share_of_book_gamma_pp": 40}}}
        got = _page(_board(scene, payload={"row_ts": _ago(minutes), "as_of": as_of}, reads=reads))
        words |= {got[k]["text"] for k in ("expiry", "fresh", "ruler", "rdAge", "lastscan", "ldWhen")}
        words |= set(_right_foot(got)) | ({got["rdLine"]["text"]} if not reads else set())
        words |= {t for _, t in _svg_texts(got, "p-edge")}
    card = PHONE.split('<section class="card">')[1].split("</section>")[0]
    words |= {t.strip() for t in re.split(r"<[^>]+>", card) if t.strip()}
    for claims, pace in (([{"strike": 1700, "now": "changed"}], 1.37),
                         ([{"strike": 1700, "now": "changed"}, {"strike": 1600, "now": "off_list"}], 0.8),
                         ([], 1.0)):
        day = dict(_day_block(), earlier_claims=[{"said_at": "13:11", "claims": claims}],
                   volume_in_reach_vs_same_time_prior_sessions=pace)
        got = _page(_board({"price": {"live_spot": 1608.20}, "scale": {"one_sigma_dollars": 40}, "day": day}))
        words |= {t for _, t in _overview(got)[5]}
    return words - {""}


def test_the_plain_words_pass_the_laws():
    """The words this pass changed go through the same gates as every other
    word on the phone: the reader's own _BANNED_RE (its forecast, causal and
    judgement lists, _BANNED_FORECAST and _BANNED_JUDGEMENT among them, with
    their inflections), its position gate, no dealer and nothing a dealer does,
    and no Greek letter or emoji. Not the half-hour card's options-vocabulary
    gate: that one is scoped to a card about price alone, and the masthead's
    expiry is a fact about the options that cannot be said without the noun
    (WORDS-SPEC 9.3, the owner's call)."""
    R = _reader()
    words = _plain_words()
    for need in ("OPTIONS END TODAY", "OPTIONS END FRI", "OPTIONS END IN 3 DAYS", "BOOK 1 MIN OLD",
                 "SCAN 14 MIN OLD", "JUST SCANNED", "SCAN AGE UNKNOWN", "USUAL DAY MOVE $40", "14 MIN AGO",
                 "No reading yet today.", "2 HR 15 MIN LEFT", "LAST SCAN 1,700.00 · 3 HR AGO",
                 "Price today", "AS OF 10:59", "10:46", "▲ 1,900 BIGGEST PILE",
                 "One thing it said earlier no longer applies.", "2 things it said earlier no longer apply.",
                 "Trading is busier than usual for this time of day.",
                 "Trading is quieter than usual for this time of day.",
                 "Trading is about usual for this time of day."):
        assert need in words, f"the gates never saw {need!r}"
    for s in sorted(words):
        assert not R._BANNED_RE.search(s), f"{s!r} trips the reader's word gate"
        assert not R._POS_RE.search(s), f"{s!r} places price against a number"
        assert not _DEALER.search(s), f"{s!r} speaks of dealers"
        assert not _EMOJI_OR_GREEK.search(s), s


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
    assert _svg_texts(_page(_board(scene)), "p-edge") == [("p-edge", "▲ 1,900 BIGGEST PILE"), ("p-edge lead", "▲ 1,800")]
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


def _css_rules(html):
    """{selector: declarations} for every rule in the page's stylesheet, media
    blocks included, with comments and whitespace out. A selector written twice
    keeps its first rule: the second is a @media block's override of it."""
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", html)))
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        out.setdefault(re.sub(r"\s+", " ", sel).strip(), re.sub(r"\s+", "", body))
    return out


def test_the_two_phone_pages_draw_one_sheet():
    """Both pages open an explainer sheet: the glance's on the three levels,
    the reads page's on faster, steady and slower (2026-09-18). What the sheet
    does is one script, sheet.js. What it looks like is one set of rules held
    in two stylesheets, for the palette's reason above: a shared stylesheet
    would be a render-blocking re-fetch in full on every open of both pages.

    So every sheet rule the two pages both carry must be the same rule, and
    the core of it must be on both — the sheet capped at 86% of the measured
    height and scrolling, not clipping, past it; not selectable; the one
    close button. Each page keeps what only it needs: the glance its side
    colours and its conditional item, the reads page its figure and its
    sources line."""
    a, b = _css_rules(PHONE), _css_rules(THREAD)
    core = {".scrim", ".sheet", "body.sheet-open .scrim", "body.sheet-open .sheet", ".sh-grab",
            ".sh-h", ".sh-item", ".sh-item:first-of-type", ".sh-term", ".sh-term b", ".sh-term span",
            ".sh-item p", ".sh-item p + p", ".sh-caveat", ".sh-caveat b", ".sh-close",
            ".sh-close:focus-visible", ".sheet,body.sheet-open .sheet"}
    assert core <= set(a) and core <= set(b), core - (set(a) & set(b))
    sheet = [s for s in set(a) & set(b) if s.startswith((".scrim", ".sheet", "body.sheet-open", ".sh-"))]
    drift = {s: (a[s], b[s]) for s in sheet if a[s] != b[s]}
    assert not drift, f"the two sheets are drawn differently: {drift}"
    assert "max-height:calc(var(--app-h)*.86);overflow-y:auto" in b[".sheet"]
    assert "user-select:none" in b[".sheet"]




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

# The 2026-09-16 15:10:21 board's levels, and its fifteen strike rows as the
# phone's payload carried them: the contracts share, which the shade drew until
# 2026-09-18, and the calls and puts traded today, which the bars draw now.
_SCENE_0916 = {
    "price": {"live_spot": 1517, "session_high": 1560.58, "session_low": 1513.25},
    "scale": {"one_sigma_dollars": 65.82,
              "expected_move_today_asym": {"up_dollars": 15.43, "down_dollars": 13.99}},
    "walls": {"call": [{"strike": 1600, "cluster_share_of_book_gamma_pp": 1.67}],
              "put": [{"strike": 1500, "cluster_share_of_book_gamma_pp": 20.44}]},
    "context": {"ranges": {"opening": {"high": 1560.58, "low": 1519.54}}},
    "strikes": {"rows": [{"strike": k, "contracts_share_pp": sh, "vol_calls": vc, "vol_puts": vp}
                         for k, sh, vc, vp in (
                             (1500, 12.17, 1118, 3861), (1600, 11.39, 5053, 1203),
                             (1530, 10.34, 3824, 3632), (1550, 7.49, 2535, 1481),
                             (1540, 7.07, 2743, 2270), (1450, 6.81, 63, 1285),
                             (1520, 4.74, 1282, 1596), (1430, 3.91, 6, 351),
                             (1495, 3.01, 125, 2161), (1490, 2.66, 14, 1316),
                             (1510, 2.35, 199, 615), (1545, 2.32, 978, 450),
                             (1470, 1.93, 110, 510), (1480, 1.64, 74, 513),
                             (1605, 1.34, 126, 16))]}}


def test_the_plot_draws_no_shade_behind_the_line():
    """REMOVED 2026-09-18 (INK-SPEC.md 1.2). Eight bands shaded the strikes'
    share of contracts, 0.08 to 0.22 opacity. They spanned 0.127 of opacity, so
    at a 0.020 step a reader can tell apart they drew five levels, not eight:
    1,510 and 1,545 differed by 0.0004. The one band that did read, the
    darkest, sat on the put wall's strike, which the chart already ruled and
    tagged, on 24 of 24 scans of 2026-09-16. The plot's background goes back
    to the card, and every mark over it clears its floor on the card.

    Pinned on the board that drew six bands, with the share still on every
    row, so the field is there to draw from and nothing draws it."""
    assert all(r.get("contracts_share_pp") for r in _SCENE_0916["strikes"]["rows"])
    svg = _page(_board(_SCENE_0916))["svg"]["html"]
    assert "p-shade" not in svg and 'style="opacity:' not in svg
    assert "weightBands" not in GLANCE + PAGE and "SHADE_" not in PAGE
    assert ".p-shade" not in PHONE


def test_no_dot_rides_on_the_price_line():
    """REMOVED 2026-09-18 (INK-SPEC.md 1.1). A dot sat on the line at every
    model call, placed from the payload's `reads_today`. On the 15:11 board of
    2026-09-16 that was 22 of the plot's 39 marks, and 19 of the 22 carried the
    wake reason "price ran": the line they sat on. They were the one mark that
    grew through the day (1 at the open, 24 at the close) and the darkest thing
    on the line, so they read as lumps in it. The reading under the chart says
    when the model looked and why, in words.

    The payload stopped sending `reads_today` too, since nothing read it. It is
    pinned here with the field present anyway, and every read inside the tape:
    a station not restarted since still sends it."""
    bars = [{"ts": "2026-09-10T09:%02d:00-04:00" % (30 + i), "close": 1520 + i, "volume": 100000}
            for i in range(10)]
    reads = [{"ts": "2026-09-10T09:31:00-04:00", "spot": 1521},
             {"ts": "2026-09-10T09:36:00-04:00", "spot": 1526}]
    svg = _page(_board(_SCENE_0916, now="2026-09-10T09:39:00-04:00",
                       payload={"reads_today": reads}, bars=bars))["svg"]["html"]
    assert "p-path" in svg and "<circle" in svg                 # the line, and the live dot on it
    assert re.findall(r'<circle class="([^"]+)"', svg) == ["p-halo", "p-dot"]
    assert "p-read" not in svg and "readPoints" not in GLANCE + PAGE
    assert ".p-read" not in PHONE


# --- where contracts traded today, behind the price line (2026-09-18) -------
# The owner's option B (ALT-BEHIND-SPEC.md): a bar per strike in the window,
# grown from the plot's right edge under every other mark, its length the
# contracts traded there today, and the busiest one's count printed at its end.

def _traded(svg):
    """The bars top first as (x, y, width, height), their end ticks' x, and the
    count as (x, baseline, text)."""
    bars = sorted((tuple(float(v) for v in m) for m in re.findall(
        r'<rect class="p-traded" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)),
        key=lambda b: b[1])
    ends = [float(x) for x in re.findall(r'<line class="p-tradedend" x1="([\d.]+)"', svg)]
    num = [(float(x), float(y), t) for x, y, t in
           re.findall(r'<text class="p-tradednum" x="([\d.]+)" y="([\d.]+)">([^<]*)<', svg)]
    return bars, ends, num


def _count_w(text):
    """The width of the count on the longest bar, 11px/600, as figW prices it."""
    return _glance("console.log(JSON.stringify(g.figW(D, 11, 600)));", text)


def _ring(svg):
    """The centre of the live dot's ring, (x, y)."""
    return tuple(float(v) for v in re.search(r'<circle class="p-halo" cx="([\d.]+)" cy="([\d.]+)"', svg).groups())


def test_each_bar_is_as_long_as_its_strike_traded_today():
    """One quantity, and one a reader can say out loud: the contracts that
    changed hands at the strike today, calls and puts together — "1,530 traded
    7,456 today". Its LENGTH says it, never its darkness: the shade's opacity
    ran eight strikes into five greys. So every bar is one solid fill, the
    busiest strike in view runs 40% of the plot's width and every other bar is
    that fraction of it by count, and each one grows from the plot's right edge,
    beside "now". Calls and puts are not split: their two fills measured 1.028:1
    apart, and a red-tinted bar under the red put wall reads as the wall.

    The 2026-09-16 15:10:21 board, bar for bar, as ALT-BEHIND-SPEC.md 3.1
    tabled it at both phones: seven strikes in the window, 1,500 to 1,550."""
    traded = {1550: 4016, 1545: 1428, 1540: 5013, 1530: 7456, 1520: 2878, 1510: 814, 1500: 4979}
    table = {343: [61.6, 21.9, 76.9, 114.4, 44.2, 12.5, 76.4], 288: [49.8, 17.7, 62.1, 92.4, 35.7, 10.1, 61.7]}
    for cw, want in table.items():
        svg = _page(_board(_SCENE_0916, width=cw))["svg"]["html"]
        box = _chart_box(svg)
        plot_r = box["plot_l"] + box["plot_w"]
        bars, ends, _ = _traded(svg)
        assert [w for _, _, w, _ in bars] == pytest.approx(want, abs=0.1), cw
        # from the right edge, the longest 40% of the plot, every other by count
        assert all(x + w == pytest.approx(plot_r) for x, _, w, _ in bars)
        assert max(w for _, _, w, _ in bars) == pytest.approx(0.40 * box["plot_w"], abs=0.1)
        for (_, _, w, _), n in zip(bars, traded.values()):
            assert w == pytest.approx(0.40 * box["plot_w"] * n / 7456, abs=0.1)
        # the end is marked, where the length is read
        assert ends == pytest.approx([x + 0.6 for x, _, _, _ in bars], abs=0.05)
    # one fill, no opacity, nothing that splits a bar by side
    assert _css_rule(".p-traded") == "fill:var(--band-edge)"
    assert not re.search(r'<rect class="p-traded"[^>]*(?:style|opacity|fill)', svg)
    # the scale is the board's own: double every count and nothing moves
    doubled = json.loads(json.dumps(_SCENE_0916))
    for r in doubled["strikes"]["rows"]:
        r["vol_calls"] *= 2; r["vol_puts"] *= 2
    assert _traded(_page(_board(doubled, width=288))["svg"]["html"])[0] == bars


def test_a_bar_is_thick_by_the_strike_pitch_and_never_closes_the_gap():
    """The lane's fixed 8px bar left 0.25px between 1,540, 1,545 and 1,550 on
    a 112px plot and the three read as one block (SIDE-SPEC.md 4c). A bar is
    70% of the tightest strike pitch in view, never more than 8px, so 30% of
    the pitch stays white between two neighbours on any plot and any window.
    ALT-BEHIND-SPEC.md floored it at 3px; that floor would close the gap to
    nothing on a window wide enough to put $5 strikes 3px apart, so it is not
    here. A bar that would cross the plot's edge is dropped, never clipped: a
    clipped bar's middle is not its strike's price."""
    rows = {"rows": [{"strike": k, "vol_calls": 100, "vol_puts": 100} for k in range(1500, 1555, 5)]}
    got = _glance("""console.log(JSON.stringify(D.cases.map(([lo, hi, top, bottom]) => {
        const t = g.tradedBars(D.rows, lo, hi, top, bottom);
        return t && {h: t.h, ys: t.bars.map(b => b.y), vs: t.bars.map(b => b.v)}; })));""",
                  {"rows": rows, "cases": [[1496.37, 1564.22, 22, 166],     # the 15:10 board, 144px
                                           [1496.37, 1564.22, 22, 134],     # the same on 112px
                                           [1400.0, 1640.0, 22, 166],       # a $240 window
                                           [1540.0, 1550.0, 22, 166],       # a $10 window, a 72px pitch
                                           [1499.0, 1551.0, 22, 166]]})     # 1,500 and 1,550 at the edges
    for t in got[:3]:
        pitch = min(b - a for a, b in zip(t["ys"], t["ys"][1:]))
        assert t["h"] == pytest.approx(min(8, 0.7 * pitch))
        assert pitch - t["h"] >= 0.3 * pitch - 1e-9, "two neighbouring bars read as one"
    assert got[0]["h"] == pytest.approx(7.43, abs=0.01)           # 3.18px of white
    assert got[1]["h"] == pytest.approx(5.78, abs=0.01)           # 2.48px, where the lane had 0.25
    assert got[2]["h"] == pytest.approx(2.1, abs=0.01)            # 0.9px, a third of a 3px pitch
    assert got[3]["h"] == 8
    # 1,500 and 1,550 sit less than half a bar inside the plot: no bar, not half of one
    assert got[4]["vs"] == [1545, 1540, 1535, 1530, 1525, 1520, 1515, 1510, 1505]


def test_the_price_line_runs_on_a_channel_of_card():
    """Over a bar the line measures 3.64:1 against the fill, and 3.27:1 at a 2x
    screen's worst crossing, where antialiasing darkens the pixel beside it
    (ALT-BEHIND-SPEC.md 4.2). So the line runs over an edge of the card 1px
    wider on each side, drawn under it: invisible on the bare card, and over a
    bar the line is read against the card, 5.00:1. The order is the spec's:
    the bars and their ends, the opening range, the edge, the line — every mark
    but the bars' own ends above the bars."""
    tape = [{"ts": "2026-09-10T%02d:%02d:00-04:00" % divmod(570 + i, 60), "close": 1560 - i * 0.6,
             "volume": 30000} for i in range(70)]
    svg = _page(_board(_SCENE_0916, now="2026-09-10T10:40:00-04:00", bars=tape))["svg"]["html"]
    clipped = re.search(r'<g clip-path="url\(#pc\)">(.*?)</g>', svg).group(1)
    order = re.findall(r'<\w+ class="(p-[\w-]+)', clipped)
    first = lambda c: order.index(c)
    assert first("p-traded") == 0 and first("p-tradedend") < first("p-orb") < first("p-casing") < first("p-path")
    edge = re.search(r'<polyline class="p-casing" points="([^"]*)"', svg).group(1)
    assert edge == re.search(r'<polyline class="p-path" points="([^"]*)"', svg).group(1)
    width = lambda sel: float(re.search(r"stroke-width:([\d.]+)", _css_rule(sel)).group(1))
    assert width(".p-casing") == pytest.approx(width(".p-path") + 2)
    case = _css_rule(".p-casing")
    assert "stroke:var(--s)" in case and "stroke-linejoin:round" in case and "fill:none" in case
    tok = _tokens()
    assert _contrast(tok["--path"], tok["--s"]) >= 4.5            # on the channel, 5.00
    assert _contrast(tok["--path"], tok["--band-edge"]) >= 3.0    # on the fill, if the channel were gone
    # no line, no edge: an empty tape draws neither
    bare = _page(_board({"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 40}}))["svg"]["html"]
    assert 'class="p-casing"' not in bare and 'class="p-path"' not in bare


def test_the_longest_bar_carries_its_count():
    """The bars' one number, kept by the owner's choice. Their scale is per
    scan, so a full-length bar was 7,456 at 15:10 and 3,264 at 11:01, and only
    this says which. 11px/600 in the darkest ink, 4px past the longest bar's
    end on the card side, where a bar chart puts its value, with a card halo:
    the busiest strike is usually one the chart already rules (a rule or dash
    crosses the count on 38 of ALT-BEHIND-SPEC's 46 scan and phone pairs), and
    the halo cuts that rule for the count's width rather than the rule cutting
    the figures. Only if the end would put it on the live dot's ring does it
    move inside the bar, at its root."""
    for cw in (343, 288):
        svg = _page(_board(_SCENE_0916, width=cw))["svg"]["html"]
        bars, _, num = _traded(svg)
        x, y, w, h = max(bars, key=lambda b: b[2])
        assert num == [(pytest.approx(x - 4, abs=0.05), pytest.approx(y + h / 2 + 3.96, abs=0.1), "7,456")]
    rule = _css_rule(".p-tradednum")
    for need in ("font:60011px/1var(--sans)", "fill:var(--i)", "paint-order:stroke", "stroke:var(--s)", "text-anchor:end"):
        assert need in rule, need
    # the live quote sat on 1,530 at 12:20, mid-plot, where the count would go:
    # it moves inside the bar at its root, off the ring
    bars_ = [{"ts": "2026-09-10T%02d:%02d:00-04:00" % divmod(570 + i, 60), "close": 1530, "volume": 30000}
             for i in range(0, 340, 5)]
    got = _page(_board(_SCENE_0916, now="2026-09-10T15:10:00-04:00", bars=bars_,
                       live={"ticker": "SNDK", "spot": 1530, "ts": "2026-09-10T12:20:00-04:00"}))
    svg = got["svg"]["html"]
    box = _chart_box(svg)
    cx, cy = _ring(svg)
    (nx, by, text), = _traded(svg)[2]
    w = _count_w(text)
    tip = min(x for x, _, _, _ in _traded(svg)[0])
    assert text == "7,456" and abs(cy - (by - 3.96)) < 3 and tip - 4 - w < cx + 9 and cx - 9 < tip - 4, \
        "the ring no longer sits where the count would go; this proves nothing"
    assert nx == pytest.approx(box["plot_l"] + box["plot_w"] - 4)
    assert nx - w > cx + 9, "the count sits on the ring"


@pytest.mark.parametrize("phone", [320, 360, 375, 412])
def test_the_bars_and_their_count_stay_in_the_plot(phone):
    """At every phone, 360 the owner's own: every bar inside the plot, and the
    count inside it too, left of the gutter where every other number on the
    chart is printed, and below the edge rows over the plot. Measured in WebKit
    off the shipped face over every scan of 2026-09-15..17 at these widths: no
    text touches another and nothing leaves the chart."""
    cw = phone - 32                                  # the ladder bleeds into the card's padding
    svg = _page(_board(_SCENE_0916, width=cw))["svg"]["html"]
    box = _chart_box(svg)
    top, height = box["plot_t"], box["plot_h"]
    plot_l, plot_r = box["plot_l"], box["plot_l"] + box["plot_w"]
    bars, _, num = _traded(svg)
    assert len(bars) == 7
    for x, y, w, h in bars:
        assert plot_l <= x and x + w <= plot_r + 0.05 and top <= y and y + h <= top + height + 0.05
    (x, by, text), = num
    w = _count_w(text)
    assert plot_l + 2 <= x - w and x <= plot_r - 2 < box["chip_x"]
    edges = [float(y) for y in re.findall(r'<text class="p-edge[^"]*" x="[\d.]+" y="([\d.]+)"', svg)]
    assert all(by - 0.72 * 11 > e + 0.2 * 11 for e in edges if e < top)


def test_no_volume_for_today_draws_no_bars():
    """Honest-absent, on the layer that would be easiest to fake. The day's
    first book still carries the prior session's counts, and the builder then
    withholds both volume columns on every row (09:30 and 09:32 on 2026-09-16
    and 09-17, 09:32 to 09:42 on 09-15): no bar, no count, never a bar of
    yesterday's. One row missing one column gets no bar, never a zero-length
    stub. A strike that traded nothing is a measurement, and it keeps its end
    at the plot's edge where a strike with no datum has nothing. The shade's
    no-datum tests pinned the same law until it went."""
    def drawn(scene):
        return _traded(_page(_board(scene))["svg"]["html"])

    carried = json.loads(json.dumps(_SCENE_0916))
    for r in carried["strikes"]["rows"]:
        del r["vol_calls"], r["vol_puts"]
    assert drawn(carried) == ([], [], [])
    none = json.loads(json.dumps(_SCENE_0916)); none.pop("strikes")
    assert drawn(none) == ([], [], [])
    # 1,530, the busiest, loses its puts: it has no bar, and 1,540 is the busiest drawn
    one = json.loads(json.dumps(_SCENE_0916))
    next(r for r in one["strikes"]["rows"] if r["strike"] == 1530)["vol_puts"] = None
    bars, ends, num = drawn(one)
    assert len(bars) == len(ends) == 6 and [t for _, _, t in num] == ["5,013"]
    # 1,510 traded nothing: no fill, and its end still marks it
    zero = json.loads(json.dumps(_SCENE_0916))
    r1510 = next(r for r in zero["strikes"]["rows"] if r["strike"] == 1510)
    r1510["vol_calls"] = r1510["vol_puts"] = 0
    bars, ends, _ = drawn(zero)
    svg = _page(_board(zero))["svg"]["html"]
    plot_r = _chart_box(svg)["plot_l"] + _chart_box(svg)["plot_w"]
    assert len(bars) == 7 and min(w for _, _, w, _ in bars) == 0
    assert max(ends) == pytest.approx(plot_r - 0.6)
    got = _glance("console.log(JSON.stringify([g.tradedBars(D, 1400, 1600, 20, 160), "
                  "g.tradedBars({rows: []}, 1400, 1600, 20, 160), g.tradedBars(null, 1400, 1600, 20, 160)]));",
                  carried["strikes"])
    assert got == [None, None, None]


# --- where new contracts arrived (2026-09-18) --------------------------------
# CHANGE-SPEC.md, on the revamped chart: a price area's share of the contracts
# newly traded across the whole board in the last two books, against its own
# median share earlier in the window. Where the plot shows the area: four
# corners, a tab in the gutter, the word. Where it cannot: a row at the edge.

# The 15:10:21 board's own series, in _SCENE_0916's row order: 1,490 and 1,600
# both took a lift there, and the window (1,496-1,564) holds neither.
_FLOW_1510 = {1500: [13, 68, 51, 52, 183, 64, 143, 60, 99, 84, 52],
              1600: [29, 326, 55, 66, 75, 170, 38, 53, 175, 189, 140],
              1530: [74, 139, 117, 97, 112, 105, 102, 129, 166, 134, 180],
              1550: [16, 85, 45, 45, 86, 120, 54, 81, 43, 30, 28],
              1540: [53, 71, 61, 84, 173, 60, 81, 59, 94, 113, 71],
              1450: [4, 30, 23, 23, None, None, None, 30, 114, 19, 58],
              1520: [138, 104, 58, 181, 27, 19, 14, 26, 83, 82, 48],
              1430: [None] * 11,
              1495: [100, 111, 2, 1, 3, 5, 5, 49, 6, 2, 0],
              1490: [1, 5, 3, 201, 8, 105, 105, 206, 8, 116, 113],
              1510: [7, 16, 28, 31, 66, 7, 7, 13, 47, 12, 31],
              1545: [2, 63, 5, 20, 58, 89, 30, 14, 22, 6, 7],
              1470: [4, 27, 2, 1, 102, 5, 7, 1, 4, 0, 11],
              1480: [7, 11, 8, 5, 36, 16, 25, 3, 59, 9, 118],
              1605: [0, 1, 0, 5, 3, 1, 1, 4, 13, 1, 1]}
_FRAMES_1510 = {"books_in_series": 12, "book_times": ["14:22", "14:27", "14:31", "14:35", "14:39", "14:43",
                                                     "14:47", "14:51", "14:55", "15:00", "15:04", "15:08"]}


def _flowing(scene, flow, frames):
    """`scene` with each row's vol_added_per_book from `flow` and the window's frames."""
    out = json.loads(json.dumps(scene))
    for r in out["strikes"]["rows"]:
        r["vol_added_per_book"] = flow[r["strike"]]
    out["frames"] = frames
    return out


# 11:01:12 on 2026-09-16, the scan whose area the plot CAN show: 1,530 and
# 1,540 took 63.0% of the board's 760 new contracts against 29.1% earlier.
_SCENE_1101 = {
    "price": {"live_spot": 1541.75, "session_high": 1560.58, "session_low": 1519.54},
    "scale": {"one_sigma_dollars": 56.74,
              "expected_move_today_asym": {"up_dollars": 33.58, "down_dollars": 33.52}},
    "walls": {"call": [{"strike": 1605, "cluster_share_of_book_gamma_pp": 5.8}],
              "put": [{"strike": 1500, "cluster_share_of_book_gamma_pp": 21.1},
                      {"strike": 1450, "cluster_share_of_book_gamma_pp": 19.1}]},
    "magnet": {"top_strikes": [{"strike": 1500, "share_of_book_gamma_pp": 14.09},
                               {"strike": 1600, "share_of_book_gamma_pp": 12.95},
                               {"strike": 1530, "share_of_book_gamma_pp": 8.97}]},
    "context": {"ranges": {"opening": {"high": 1560.58, "low": 1519.54}}},
    "frames": {"books_in_series": 12, "book_times": ["10:15", "10:20", "10:24", "10:28", "10:32", "10:36",
                                                     "10:40", "10:44", "10:48", "10:52", "10:57", "11:01"]},
    "strikes": {"rows": [{"strike": k, "vol_calls": vc, "vol_puts": vp, "vol_added_per_book": s}
                         for k, vc, vp, s in (
                             (1500, 536, 1409, [99, 141, 23, 27, 44, 34, 44, 41, 172, 20, 15]),
                             (1600, 2369, 925, [185, 98, 20, 122, 17, 73, 39, 29, 18, 74, 4]),
                             (1530, 1655, 1609, [101, 57, 17, 20, 3, 24, 89, 99, 77, 170, 33]),
                             (1550, 1208, 970, [122, 131, 112, 77, 67, 38, 146, 29, 57, 29, 24]),
                             (1540, 1385, 1459, [341, 157, 120, 52, 59, 60, 99, 49, 36, 170, 106]),
                             (1520, 693, 668, [37, 38, 23, 2, 34, 3, 54, 101, 7, 10, 9]),
                             (1570, 262, 34, [12, 15, 7, 7, 1, 15, 4, 6, 3, 1, 1]),
                             (1580, 172, 63, [5, 12, 8, 3, 3, 1, 5, 2, 1, 5, 5]),
                             (1510, 91, 225, [13, 14, 6, 2, 11, 0, 19, 6, 3, 3, 14]),
                             (1475, 50, 1059, [5, 8, 0, 1, 0, 1, 2, 1, 2, 1, 0]),
                             (1545, 504, 217, [78, 68, 43, 40, 19, 22, 15, 11, 2, 6, 8]),
                             (1470, 103, 130, [4, 5, 4, 1, 0, 1, 8, 16, 15, 22, 1]),
                             (1460, 17, 497, [101, 1, 11, 10, 2, 0, 101, 203, 2, 7, 0]),
                             (1490, 8, 143, [4, 2, 3, 1, 3, 6, 7, 11, 7, 7, 4]),
                             (1605, 71, 13, [0, 0, 10, 0, 0, 0, 3, 0, 0, 1, 0]),
                             (1480, 21, 151, [1, 0, 1, 16, 0, 11, 7, 2, 0, 6, 4]))]}}


def _new_marks(svg):
    """The change marks as drawn: each corner as (x, y of its arm, the arm's
    other end, the leg's end), the tabs as (x, y, width, height), the word as
    (x, baseline, text), and the edge rows' texts that name new contracts."""
    corners = [tuple(float(v) for v in m) for m in re.findall(
        r'<path class="p-new" d="M([\d.]+),([\d.]+) L[\d.]+,([\d.]+) L([\d.]+),[\d.]+"/>', svg)]
    corners = [(x, arm, end, leg) for x, leg, arm, end in corners]
    tabs = [tuple(float(v) for v in m) for m in re.findall(
        r'<rect class="p-newtab" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)]
    word = [(float(x), float(y), t) for x, y, t in
            re.findall(r'<text class="p-newword" x="([\d.]+)" y="([\d.]+)">([^<]*)<', svg)]
    rows = [t for _, t in _svg_texts({"svg": {"html": svg}}, "p-edge") if "NEW CONTRACTS" in t]
    return corners, tabs, word, rows


def _new(strikes, frames):
    return _glance("console.log(JSON.stringify(g.newContracts(D.strikes, D.frames)));",
                   {"strikes": strikes, "frames": frames})


def test_new_contracts_are_a_share_of_the_boards_newest_against_its_own_earlier():
    """Size is not change: the bars already say how much traded all day, and
    they say it whether the pile grew in the last nine minutes or at 09:34.
    The measure is flow. A strike's share of the contracts newly traded across
    the WHOLE board in the last two books, against its own median share of each
    earlier book in the window; the lift is the difference, in points. Both are
    contracts. The board is the denominator, so a burst or a lull across every
    strike moves both terms alike and flags nothing: doubling every strike's
    last two books leaves every share where it was.

    CHANGE-SPEC.md 3.3's rows for two real boards of 2026-09-16: at 11:01:12
    1,530 and 1,540 went from 29.1% to 63.0% of 760 new contracts, one area;
    at 15:10:21 1,490 went from 0.9% to 13.8% and 1,600 from 8.1% to 19.9% of
    1,655, the bigger lift first."""
    r = lambda a: (a["strikes"], round(a["base"], 1), round(a["share"], 1), round(a["lift"], 1), a["n"], a["board"])
    got = _new(_SCENE_1101["strikes"], _SCENE_1101["frames"])
    assert [r(a) for a in got["areas"]] == [([1530, 1540], 29.1, 63.0, 34.0, 479, 760)] and got["more"] == 0
    flow = _flowing(_SCENE_0916, _FLOW_1510, _FRAMES_1510)
    got = _new(flow["strikes"], flow["frames"])
    assert [r(a) for a in got["areas"]] == [([1490], 0.9, 13.8, 13.0, 229, 1655), ([1600], 8.1, 19.9, 11.8, 329, 1655)]
    # an area spans half the way to its listed neighbours: 1,485 below 1,490 (1,480
    # is listed), 1,492.5 above it (1,495 is)
    assert (got["areas"][0]["lo"], got["areas"][0]["hi"]) == (1485, 1492.5)
    burst = json.loads(json.dumps(flow))
    for row in burst["strikes"]["rows"]:
        row["vol_added_per_book"] = [v if v is None or i < 9 else 2 * v for i, v in enumerate(row["vol_added_per_book"])]
    again = _new(burst["strikes"], burst["frames"])
    assert [(a["strikes"], round(a["share"], 6), round(a["base"], 6)) for a in again["areas"]] == \
        [(a["strikes"], round(a["share"], 6), round(a["base"], 6)) for a in got["areas"]]


def _lift_board(tail, base=20, bg=(50, 50, 50, 50), books=12, series=None):
    """A board of `books` books: four far strikes trading `bg` a book each, and
    1,400 trading `base` a book, then `tail` a book in the last two."""
    nd = books - 1
    rows = [{"strike": k, "vol_added_per_book": [v] * nd} for k, v in zip((1300, 1320, 1340, 1360), bg)]
    rows.append({"strike": 1400, "vol_added_per_book": series or [base] * (nd - 2) + [tail] * 2})
    return {"strikes": {"rows": rows}, "frames": {"books_in_series": books}}


def test_each_threshold_is_where_the_spec_put_it():
    """CHANGE-SPEC.md 3, each floor proved on the two sides of its line, with
    the others passed. LIFT: 6 points of the board's new contracts, near the
    90th percentile of all lifts (the median is +0.4). SHARE: and a tenth of
    them now. FLOOR: and 60 real contracts at the strike. BOARD: on a board
    that added 250 in the two books, or a big share of nothing is not change
    (12:44:07 would have lit 1,520 for 56 contracts). BASE: four earlier books
    of the strike's own, or there is no baseline to be lifted from, so a
    window of fewer than seven books measures nothing."""
    def flagged(**kw):
        got = _new(**_lift_board(**kw))
        return bool(got) and [a["strikes"] for a in got["areas"]] == [[1400]]
    # lift: 20/220 = 9.09% before; 36/236 = 15.25% (+6.16) flags, 35/235 = 14.89% (+5.80) does not
    assert flagged(tail=36) and not flagged(tail=35)
    # share: 3/303 = 0.99% before, on a 300-a-book board; 34/334 = 10.18% flags, 33/333 = 9.91% does not
    assert flagged(tail=34, base=3, bg=(75,) * 4) and not flagged(tail=33, base=3, bg=(75,) * 4)
    # floor: 5/205 = 2.44% before; 30 a book is 60 contracts and 13.04%, 29 is 58 and 12.66%
    assert flagged(tail=30, base=5) and not flagged(tail=29, base=5)
    # board: 2 x (30 + 95) = 250 flags, 2 x (30 + 94) = 248 does not, at 24% of it
    assert flagged(tail=30, base=5, bg=(24, 24, 24, 23)) and not flagged(tail=30, base=5, bg=(24, 24, 23, 23))
    # base: four earlier books of its own measure it, three do not, however big the move
    missing = [None] * 5
    assert flagged(tail=80, series=missing + [5] * 4 + [80, 80])
    assert not flagged(tail=80, series=missing + [None] + [5] * 3 + [80, 80])
    # a window of six books gives no strike four earlier ones; seven does
    assert _new(**_lift_board(tail=80, books=6)) is None
    assert _new(**_lift_board(tail=80, books=7))["areas"][0]["strikes"] == [1400]


def test_a_strike_from_almost_nothing_is_measured_as_a_difference():
    """The small-base trap, CHANGE-SPEC.md 4. The lift is a difference in
    points, not a ratio: a ratio has no ceiling, so the day's biggest number
    would belong to the strike with the smallest denominator. 1,490 at 15:10:21
    took 0.86% of the board's new contracts and then 13.8%, 229 contracts: a
    real arrival, flagged. A strike going from 1 a book to 25 is 25 times over
    but 7.7% of a 300-a-book board: not flagged. And a book a strike was not
    listed in is MISSING, not a book at 0%: a strike listed in four of the nine
    earlier books at 20% has a baseline of 20%, where zeros would have made it
    0% and its 20% now a lift of 20 points."""
    flow = _flowing(_SCENE_0916, _FLOW_1510, _FRAMES_1510)
    a = _new(flow["strikes"], flow["frames"])["areas"][0]
    assert a["strikes"] == [1490] and a["base"] < 1 and a["n"] == 229 and a["lift"] > 6
    # 1,605 went from about 1 a book to 14 in the last two: many times over, a sliver of the board
    assert all(1605 not in x["strikes"] for x in _new(flow["strikes"], flow["frames"])["areas"])
    assert _new(**_lift_board(tail=25, base=1, bg=(75,) * 4)) is None
    # listed in 4 of 9 earlier books at 20% of the board (50 of 250), then 20% still
    assert _new(**_lift_board(tail=50, series=[None] * 5 + [50] * 4 + [50, 50])) is None


def test_neighbouring_strikes_are_one_area():
    """1,500, 1,510 and 1,520 lighting together at 15:33:04 is one thing
    happening, and 1,500 to 1,520 taking 54.7% against 24.5% is a truer
    sentence than three claims. Flagged strikes ten points apart or less merge,
    their contracts, shares and baselines summed; fifteen apart they are two
    areas. The area runs half the way to the listed strikes beyond its ends."""
    def board(ks):
        rows = [{"strike": k, "vol_added_per_book": [50] * 11} for k in (1300, 1310, 1320, 1330)]
        rows += [{"strike": k, "vol_added_per_book": [10] * 9 + [60, 60]} for k in ks]
        rows += [{"strike": k, "vol_added_per_book": [10] * 11} for k in (1390, 1420)]
        return {"strikes": {"rows": rows}, "frames": {"books_in_series": 12}}
    got = _new(**board([1400, 1410]))
    (a,) = got["areas"]
    assert a["strikes"] == [1400, 1410] and a["n"] == 240 and (a["lo"], a["hi"]) == (1395, 1415)
    assert a["share"] == pytest.approx(2 * 100 * 120 / (4 * 100 + 2 * 120 + 2 * 20))
    got = _new(**board([1400, 1415]))
    assert [x["strikes"] for x in got["areas"]] == [[1400], [1415]]


def test_two_areas_at_most_and_the_rest_counted():
    """Four highlights on a 144px plot would be no highlight at all. The two
    biggest lifts are kept, the biggest first, and the rest are counted, never
    dropped: the chart says how many more. On the 24 scans of 2026-09-16 the
    model read, the merge kept every board to two; on the phone's 514 boards of
    09-15..17, 18 had a third."""
    rows = [{"strike": k, "vol_added_per_book": [50] * 11} for k in (1300, 1320, 1340, 1360)]
    for k, now in ((1400, 60), (1450, 90), (1500, 75)):
        rows.append({"strike": k, "vol_added_per_book": [10] * 9 + [now, now]})
    got = _new({"rows": rows}, {"books_in_series": 12})
    assert [a["strikes"] for a in got["areas"]] == [[1450], [1500]] and got["more"] == 1
    assert got["areas"][0]["lift"] > got["areas"][1]["lift"]


def test_nothing_is_marked_when_nothing_moved():
    """Honest-absent. No window of books, no strike rows, no series, a series
    that is not the window's length, a board too quiet to count or nothing
    lifted far enough: nothing is returned, and the chart draws no corner, no
    tab, no word and no row, and keeps the plot the height it had."""
    flow = _flowing(_SCENE_0916, _FLOW_1510, _FRAMES_1510)
    short = json.loads(json.dumps(flow))
    for row in short["strikes"]["rows"]:
        row["vol_added_per_book"] = row["vol_added_per_book"][1:]
    for strikes, frames in ((flow["strikes"], None), (None, flow["frames"]), ({"rows": []}, flow["frames"]),
                            (_SCENE_0916["strikes"], flow["frames"]), (short["strikes"], short["frames"]),
                            (_lift_board(tail=30, base=5, bg=(24, 24, 23, 23))["strikes"], {"books_in_series": 12}),
                            (_lift_board(tail=20)["strikes"], {"books_in_series": 12})):
        assert _new(strikes, frames) is None
    bare = _page(_board(_SCENE_0916))["svg"]["html"]
    for scene in (short, dict(flow, frames=None)):
        svg = _page(_board(scene))["svg"]["html"]
        assert _new_marks(svg) == ([], [], [], [])
        assert re.search(r'<clipPath id="pc">.*?</clipPath>', svg).group(0) == \
            re.search(r'<clipPath id="pc">.*?</clipPath>', bare).group(0)


def test_the_day_flags_what_the_spec_measured():
    """The measure over the phone's own boards at the 24 scans of 2026-09-16
    the model read (new_contracts_2026-09-16.json beside this file): nothing on
    10 of them (42%), one area on 9 (38%), two on 5 (21%), never three, 19
    areas in all, CHANGE-SPEC.md 3.1 and 3.3. The flag rate is the design: two
    or three lit on every scan would claim that 13 to 20% of the board is
    always changing (3.2). Over every scan the phone drew on 09-15..17 (514)
    it came out 26% none, 45% one, 30% two, 18 with a third counted."""
    day = json.loads((Path(__file__).parent / "new_contracts_2026-09-16.json").read_text())["boards"]
    got = _glance("""console.log(JSON.stringify(D.map(([t, nb, rows]) => {
        const r = g.newContracts({rows: rows.map(([strike, s]) => ({strike, vol_added_per_book: s}))},
                                 {books_in_series: nb});
        return [t, r ? r.areas.map(a => [a.strikes, Math.round(a.share * 10) / 10]) : [], r ? r.more : 0]; })));""", day)
    counts = [len(a) for _, a, _ in got]
    assert [counts.count(n) for n in (0, 1, 2)] == [10, 9, 5] and sum(counts) == 19
    assert all(more == 0 for _, _, more in got)
    by = {t: a for t, a, _ in got}
    assert by["11:01:12"] == [[[1530, 1540], 63.0]]
    assert by["15:33:04"] == [[[1500, 1510, 1520], 54.7], [[1470], 16.9]]
    assert by["11:38:13"] == [[[1500], 27.5], [[1550], 18.9]]         # the thinnest: 64 contracts
    assert by["12:44:07"] == [] and by["12:17:20"] == []              # boards of 196 and 219


def test_an_area_the_plot_shows_gets_its_corners_its_tab_and_its_word():
    """Four corners, not two lines across the area: two full-width lines read
    as a channel, and a channel promises price does something between them
    (CHANGE-SPEC.md 5.2). Each corner a 16px arm and a 5.5px leg, 4px inside
    the plot, off the wall bugs; the right-hand arm beside the live dot's ring
    stops 10px short of its centre. The arms are the area's own edges, half the
    way to the next strike, moved outward only as far as keeps them 2.5px off
    a rule's ink. The word is on the biggest area, from 6px inside the plot, in
    the tallest stretch of its box clear of the rules; the tab stands in the
    gutter's mark column and stops 3px short of the price chip, which it would
    otherwise read as the stem of. All in --i, the one ink no hue has claimed,
    and the word in 11px type with the card's halo.

    11:01:12 on 2026-09-16, 1,525 to 1,542.5 in a window of $41: at every
    phone."""
    for cw in (288, 328, 343, 380):
        svg = _page(_board(_SCENE_1101, width=cw))["svg"]["html"]
        box = _chart_box(svg)
        plot_l, plot_r = box["plot_l"], box["plot_l"] + box["plot_w"]
        top, height = box["plot_t"], box["plot_h"]
        corners, tabs, word, rows = _new_marks(svg)
        assert len(corners) == 4 and rows == []
        arms = sorted({arm for _, arm, _, _ in corners})
        t, b = arms
        # the plot's own scale, read off two marks at known prices: the live
        # dot at 1,541.75 and the put wall's rule at 1,500
        cx, cy = _ring(svg)
        wall = float(re.search(r'<line class="p-wall put[^"]*"[^>]*y1="([\d.]+)"', svg).group(1))
        y = lambda v: cy + (1541.75 - v) * (wall - cy) / 41.75
        for x, arm, end, leg in corners:
            assert abs(end - x) == 16 and abs(leg - arm) == pytest.approx(5.5, abs=0.1)
            assert plot_l <= min(x, end) and max(x, end) <= plot_r and top <= arm <= top + height
            if x < end:
                assert x == plot_l + 4
            else:
                assert x == plot_r - 4 or (x == pytest.approx(cx - 10) and abs(arm - cy) < 10 + 5.5)
        assert len({x for x, _, _, _ in corners}) == 3, "the arm beside the ring did not stop short of it"
        # each rule's ink: an inline width, or its class's own (the price rule and
        # the opening range 1, the lead magnet 2.2)
        rule_ys = [(float(v), float(w) if w else {"p-mag": 2.2}.get(c, 1.0)) for c, v, w in re.findall(
            r'<line class="(p-(?:wall|mag|magrun|prule|orb))[^"]*"[^>]*y1="([\d.]+)"[^>]*?(?:stroke-width:([\d.]+))?"?/>', svg)]
        for arm in arms:
            assert min(abs(arm - v) - w / 2 - 0.75 for v, w in rule_ys) >= 2.45, "an arm doubles a rule"
        # the area's own edges, each moved outward 4px at most
        assert y(1542.5) - 4.1 <= t <= y(1542.5) + 0.1 and y(1525) - 0.1 <= b <= y(1525) + 4.1
        # the word, inside its box, clear of the count
        (wx, by, text), = word
        assert text == "NEW CONTRACTS 63%" and wx == plot_l + 6 and t < by - 8.4 and by + 0.2 < b
        (nx, ny, _), = _traded(svg)[2]
        nw = _count_w("3,264")
        apart = max(nx - nw - (wx + 121.42), ny - 8 - (by + 0.2), by - 8.4 - (ny + 2.2))
        assert apart >= 5.4, "the count sits on the word"
        # the tab, in the chip's column, off the chip
        chip_y = float(re.search(r'<rect class="p-chip" x="[\d.]+" y="([\d.]+)"', svg).group(1))
        for x, ty, w, h in tabs:
            assert x == box["chip_x"] and w == 4 and t <= ty and ty + h <= b + 0.05
            assert ty >= chip_y + 18 + 3 or ty + h <= chip_y - 3
        assert tabs, "the area reaches below the chip; its tab should show there"
    assert _css_rule(".p-new") == "fill:none;stroke:var(--i);stroke-width:1.5;stroke-linecap:butt;stroke-linejoin:miter"
    assert _css_rule(".p-newtab") == "fill:var(--i)"
    word = _css_rule(".p-newword")
    for need in ("font:50011px/1var(--sans)", "fill:var(--i)", "paint-order:stroke", "stroke:var(--s)"):
        assert need in word, need


def test_an_area_the_plot_cannot_show_is_named_at_its_edge():
    """The window is solved from price, the session and the walls, and the
    change is wherever contracts trade. At 15:10:21 the two areas, 1,490 and
    1,600, lie outside a window of 1,496 to 1,564, and widening it to fetch them
    would flatten the tape to 55% of its height. So each is named in the edge
    stack on its side, the way a refused wall is, with a plain arrow where a
    level has a solid one, and nothing is drawn in the plot. A row of new
    contracts sits 16px from its neighbour, not 13: two 11px rows at 13 leave
    2.88px of white. The rows are in price order, and the plot gives up exactly
    what the rows take; the bars on it still keep 30% of their pitch white."""
    flow = _flowing(_SCENE_0916, _FLOW_1510, _FRAMES_1510)
    for cw in (288, 343):
        before = _page(_board(_SCENE_0916, width=cw))["svg"]["html"]
        svg = _page(_board(flow, width=cw))["svg"]["html"]
        corners, tabs, word, rows = _new_marks(svg)
        assert (corners, tabs, word) == ([], [], [])
        assert rows == ["↑ 1,600 NEW CONTRACTS", "↓ 1,490 NEW CONTRACTS"]
        edges = [(float(yy), t) for yy, t in re.findall(r'<text class="p-edge[^"]*" x="[\d.]+" y="([\d.]+)">([^<]*)<', svg)]
        assert [t for _, t in edges] == ["▲ 1,600", "↑ 1,600 NEW CONTRACTS", "↓ 1,490 NEW CONTRACTS"]
        assert edges[1][0] - edges[0][0] == 16
        assert _chart_box(before)["plot_h"] - _chart_box(svg)["plot_h"] == 16 + 13
        bars, _, _ = _traded(svg)
        pitch = min(b[1] - a[1] for a, b in zip(bars, bars[1:]))
        assert pitch - bars[0][3] >= 0.3 * pitch - 0.1
    # Three areas and none in the window: the third, past the cap, is counted on
    # the biggest one's row, since no word is drawn to carry it; and below the
    # window the rows stand in price order, 1,440 nearer the plot than the wall
    # at 1,300 it was named after.
    rows = [{"strike": k, "vol_added_per_book": [50] * 11} for k in (1300, 1320, 1340, 1360)]
    for k, now in ((1400, 60), (1440, 90), (1700, 75)):
        rows.append({"strike": k, "vol_added_per_book": [10] * 9 + [now, now]})
    far = {"price": {"live_spot": 1560}, "scale": {"one_sigma_dollars": 40}, "strikes": {"rows": rows},
           "walls": {"put": [{"strike": 1300, "cluster_share_of_book_gamma_pp": 10}]},
           "frames": {"books_in_series": 12}}
    assert _svg_texts(_page(_board(far)), "p-edge") == [
        ("p-edge", "↑ 1,700 NEW CONTRACTS"), ("p-edge", "↓ 1,440 NEW CONTRACTS · 1 MORE"), ("p-edge lead", "▼ 1,300")]
    # An area the window shows less than 15% of is a sliver at its edge, not a
    # place: 1,585 runs 1,582.5 to 1,587.5, and the window ends at 1,582.93.
    rows = [{"strike": k, "vol_added_per_book": [50] * 11} for k in (1300, 1320, 1340, 1360, 1580, 1590)]
    rows.append({"strike": 1585, "vol_added_per_book": [10] * 9 + [90, 90]})
    edge = {"price": {"live_spot": 1560, "session_high": 1580.5, "session_low": 1540}, "scale": {"one_sigma_dollars": 40},
            "strikes": {"rows": rows}, "frames": {"books_in_series": 12}}
    corners, _, _, named = _new_marks(_page(_board(edge))["svg"]["html"])
    assert corners == [] and named == ["↑ 1,585 NEW CONTRACTS"]


def test_the_word_never_sits_on_other_text_or_a_bar():
    """Where the busiest strike is the one that changed, the word and the
    longest bar's count want one row, and a count stacked under the word reads
    as the number of new contracts. The count then moves inside its bar, past
    the end, if that leaves 12px beside the word; otherwise the word keeps
    5.5px above or below it, what the chart's two closest labels keep. The word
    never crosses a bar or its end, and never the live dot's ring; with no room
    left in its box it sits just outside it, below and then above.

    11:01:12 again, with 1,530, the busiest strike, inside the box: at 320,
    360 and 375 the count moves; at 412 it is already clear. Then the same
    board with a third area counted, so the word runs to "· 1 MORE" (170px):
    at 320 and 360 the count cannot move 12px clear of it and stays, and the
    word, which would cross the longest bars, leaves its box."""
    busy = json.loads(json.dumps(_SCENE_1101))
    for r in busy["strikes"]["rows"]:
        if r["strike"] in (1605, 1460):
            r["vol_added_per_book"] = r["vol_added_per_book"][:9] + [60 if r["strike"] == 1605 else 55] * 2
    for scene, cw, moved, inside in ((_SCENE_1101, 288, True, True), (_SCENE_1101, 328, True, True),
                                     (_SCENE_1101, 343, True, True), (_SCENE_1101, 380, False, True),
                                     (busy, 288, False, False), (busy, 328, False, False),
                                     (busy, 343, False, True), (busy, 380, True, True)):
        svg = _page(_board(scene, width=cw))["svg"]["html"]
        bars, ends, ((nx, ny, text),) = _traded(svg)
        corners, _, ((wx, by, word),), _ = _new_marks(svg)
        ww = 121.42 + (48.65 if word.endswith("MORE") else 0)
        tip = min(x for x, _, _, _ in bars)
        nw = _count_w(text)
        assert (nx == pytest.approx(tip + 4 + nw, abs=0.05)) is moved, (cw, word)
        # 5.5, less the tenth both baselines are rounded to
        assert nx - nw >= wx + ww + 12 or ny - 8 >= by + 0.2 + 5.4 or by - 8.4 >= ny + 2.2 + 5.4, (cw, word)
        for x, yy, w, h in bars:
            assert x >= wx + ww or yy >= by + 0.2 or yy + h <= by - 8.4, "the word crosses a bar"
        t, b = min(c[1] for c in corners[:4]), max(c[1] for c in corners[:4])
        assert (t < by - 8.4 and by + 0.2 < b) is inside, (cw, word)
        assert inside or by - 8.4 > b, "out of its box, the word goes below it first"
    # And where the box has a stretch clear of every rule, the word takes it and
    # does not lie across a rule: with the runner moved to 1,534, the box's
    # middle, the word sits in the stretch below it.
    mid = json.loads(json.dumps(_SCENE_1101))
    mid["magnet"]["top_strikes"][2]["strike"] = 1534
    for cw in (288, 343):
        svg = _page(_board(mid, width=cw))["svg"]["html"]
        (_, by, _), = _new_marks(svg)[2]
        runner = float(re.search(r'<line class="p-magrun"[^>]*y1="([\d.]+)"', svg).group(1))
        assert by - 8.4 > runner + 1 or by + 0.2 < runner - 1, "the word lies across the runner"
    # And the live dot's ring. A quote older than the tape sits mid-plot: at
    # 09:50 on 1,533, under the word's run and in its box, the word keeps 2px
    # off the ring's 9 either way, here by leaving the box.
    tape = [{"ts": "2026-09-16T%02d:%02d:00-04:00" % divmod(570 + i, 60), "close": 1545 - i % 7, "volume": 30000}
            for i in range(92)]
    for cw in (288, 343):
        svg = _page(_board(_SCENE_1101, now="2026-09-16T11:02:00-04:00", bars=tape, width=cw,
                           live={"ticker": "SNDK", "spot": 1533, "ts": "2026-09-16T09:50:00-04:00"}))["svg"]["html"]
        cx, cy = _ring(svg)
        corners, _, ((wx, by, _),), _ = _new_marks(svg)
        t, b = min(c[1] for c in corners), max(c[1] for c in corners)
        assert wx < cx - 9 < wx + 121.42 and t < cy < b, "the ring no longer sits in the word's way; this proves nothing"
        assert by - 8.4 >= cy + 11 or by + 0.2 <= cy - 11, "the word sits on the ring"


def test_the_arms_keep_off_the_rules_they_would_double():
    """newBox, on its own. A one-strike area is a few pixels tall, so the box
    grows about its middle to 13px, not the spec's 11: at 11 the word left its
    box on 110 to 120 of the 335 boxed boards of 2026-09-15..17, and at 13 on
    0 to 14. It stays inside the plot. An arm keeps 2.5px off every rule's ink,
    since an arm on a rule reads as the rule doubled: it moves OUTWARD for it,
    4px at most, and comes in over the area it marks only at twice the cost,
    or where the plot's edge stops the outward move. Where no move of 4px
    clears, it keeps the nearer rule as far off as it can: 72 of the 1,532
    arms on those boards."""
    got = _glance("""console.log(JSON.stringify(D.map(([t, b, inks, top, bottom]) =>
        g.newBox(t, b, inks, top, bottom))));""", [
        [100, 102, [], 20, 180],                    # a 2px area grows to 13 about its middle
        [20, 24, [], 21, 180],                      # ...inside the plot
        [10, 60, [], 21, 180],                      # an area past the plot's top is cut at it
        [100, 140, [[101, 1]], 20, 180],            # a 2px wall 1px inside the top arm: out 3.25
        [100, 140, [[99.75, 0.5]], 20, 180],        # clear 4 out or 3.5 in: in costs 7, so out
        [23, 60, [[21.5, 0.5]], 21, 180],           # the edge stops the move out: in 2.25
        [100, 140, [[98, 0.5], [105, 0.5]], 20, 180]])   # nothing clears: 2.25 off both
    assert [(x["t"], x["b"]) for x in got] == [(94.5, 107.5), (21, 34), (21, 60), (96.75, 140),
                                               (96, 140), (25.25, 60), (101.5, 140)]


def test_the_word_takes_the_clearest_room_its_box_has():
    """wordRow, on its own. First the tallest stretch of the box clear of the
    rules and of what the word may never touch, the word centred in it and a
    pixel of air off each rule's ink: a word beside a rule reads as the box's,
    one across it as the rule's. With no such stretch, it lies across a rule,
    in the stretch clear of text, bars and the ring nearest the box's middle.
    With none of those, just outside the box, below and then above; and with
    no room anywhere, nothing, rather than a word on other text."""
    got = _glance("""console.log(JSON.stringify(D.map(([t, b, soft, hard, top, bottom]) =>
        g.wordRow(t, b, soft, hard, top, bottom))));""", [
        [100, 140, [[113.5, 114.5], [121.5, 122.5]], [], 20, 180],   # three stretches: the tallest
        [100, 121, [[110.5, 111.5]], [], 20, 180],    # 8.75 above the ink, 7.75 with its pixel of air
        [100, 140, [[100, 140]], [[115, 125]], 20, 180],   # rules everywhere, a bar mid-box
        [100, 140, [], [[100, 140]], 20, 180],        # no room in the box
        [100, 140, [], [[100, 160]], 80, 160],        # ...nor below it
        [100, 140, [], [[80, 160]], 80, 160]])        # ...nor above
    assert got == [134.975, 114.6, 114.8, 150.15, 98.05, None]


def test_the_words_of_new_contracts_say_what_happened_and_nothing_ahead():
    """The word states a measurement already made: this share of the contracts
    traded in the last two books went here. No verb with a subject, no actor,
    no direction, nothing about what price will do there; a highlight at a
    price can read as "price will turn here", which is why it is corners and
    not a channel. Every form the chart prints goes through the reader's own
    gates (forecast, causal and judgement words with their inflections, and
    the position gate), the dealer pattern, the forward-looking words, and the
    Greek and emoji checks."""
    R = _reader()
    flow = _flowing(_SCENE_0916, _FLOW_1510, _FRAMES_1510)
    said = set(_new_marks(_page(_board(flow))["svg"]["html"])[3])
    said |= {t for _, _, t in _new_marks(_page(_board(_SCENE_1101))["svg"]["html"])[2]}
    rows = [{"strike": k, "vol_added_per_book": [50] * 11} for k in (1300, 1320, 1340, 1360)]
    for k, now in ((1500, 60), (1540, 90), (1580, 75)):
        rows.append({"strike": k, "vol_added_per_book": [10] * 9 + [now, now]})
    near = {"price": {"live_spot": 1540}, "scale": {"one_sigma_dollars": 80}, "strikes": {"rows": rows},
            "frames": {"books_in_series": 12}}
    said |= {t for _, _, t in _new_marks(_page(_board(near))["svg"]["html"])[2]}
    assert {"↑ 1,600 NEW CONTRACTS", "↓ 1,490 NEW CONTRACTS", "NEW CONTRACTS 63%"} <= said
    assert any(t.endswith("· 1 MORE") for t in said), said
    for s in said:
        assert not R._BANNED_RE.search(s), f"{s!r} trips the reader's word gate"
        assert not R._POS_RE.search(s), f"{s!r} places price against a number"
        assert not _DEALER.search(s) and not _AHEAD.search(s), s
        assert not _EMOJI_OR_GREEK.search(s), s


def test_the_opening_half_hour_draws_both_its_own_edges():
    """It had no mark at all: the reading named it in prose and the chart never
    showed where it was. Both edges or neither — one line is a level, and a
    level is not what this is."""
    svg = _page(_board(_SCENE_0916))["svg"]["html"]
    assert len(re.findall(r'<line class="p-orb"', svg)) == 2
    half = json.loads(json.dumps(_SCENE_0916))
    half["context"]["ranges"]["opening"]["low"] = None
    assert re.findall(r'<line class="p-orb"', _page(_board(half))["svg"]["html"]) == []


def test_the_volume_ribbon_is_whole_blocks_on_one_scale():
    """A part-block at the live edge is a smaller sample on the same gauge as a
    full one, which reads as a lull that is really an unfinished minute. And the
    scale is fixed across sessions: scaled to the day's own maximum, the opening
    block runs many times the median and most of the session draws under a
    pixel — 27 of 69 blocks on 2026-09-16, against 5 on the fixed scale."""
    bars = [{"ts": "2026-09-10T09:%02d:00-04:00" % (30 + i), "close": 1520,
             "volume": 200000 if i < 5 else 10000} for i in range(12)]
    svg = _page(_board(_SCENE_0916, now="2026-09-10T09:41:00-04:00", bars=bars))["svg"]["html"]
    # twelve bars, five to a block: two whole blocks and a part-block dropped
    blocks = re.findall(r'<rect class="p-vol"[^>]*height="([\d.]+)"', svg)
    assert len(blocks) == 2
    # the busy block is past the scale, capped, and the cap is marked
    assert blocks[0] == "10.0" and float(blocks[1]) < 10.0
    assert len(re.findall(r'<rect class="p-clip"', svg)) == 1


# --- the chart's width, its ink, and a ruler of prices (2026-09-18) --------
# On a 375px phone the chart was 311px wide and its plot 247: 64 pixels went on
# padding before the chart began and 56 on a gutter sized for one board. Every
# mark over the shade was knocked down until the shade ate it. And the gutter
# named two prices on a board with seven round ones in view.

def _css_rule(sel):
    """The glance's declaration block for exactly `sel`, wherever on its line
    the rule sits — several chart rules share a line — or None."""
    return _css_rules(PHONE).get(sel)


def _contrast(a, b):
    """WCAG contrast of two sRGB triples, 0-255."""
    def lum(c):
        c = [v / 255 for v in c]
        c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _tokens():
    """The glance's colour tokens, off its :root block, as sRGB triples."""
    root = re.search(r"(?ms)^:root\{(.*?)^\}", PHONE).group(1)
    return {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
            for k, v in re.findall(r"(--[a-z0-9-]+):(#[0-9A-Fa-f]{6})", root)}


def _chart_box(svg):
    clip = re.search(r'<clipPath id="pc"><rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)
    chip = re.search(r'<rect class="p-chip" x="([\d.]+)"[^>]*width="([\d.]+)"', svg)
    return {"plot_l": float(clip.group(1)), "plot_t": float(clip.group(2)),
            "plot_w": float(clip.group(3)), "plot_h": float(clip.group(4)),
            "chip_x": float(chip.group(1)), "chip_w": float(chip.group(2)),
            "num_x": float(re.search(r'<text class="p-chiptx" x="([\d.]+)"', svg).group(1)),
            "num": re.search(r'<text class="p-chiptx"[^>]*>([^<]*)<', svg).group(1),
            "right": {float(x) for x in re.findall(r'<text class="p-(?:tag|scale|edge)[^"]*" x="([\d.]+)"', svg)}}


def test_the_plot_takes_the_width_the_margins_were_spending():
    """Three changes, all margin and no proportion, so the gain is the same +39px
    at every phone. The ladder bleeds out of the card's 16px padding (+32), the
    dead margin left of the plot goes from 8 to 4, and the gutter is exactly as
    wide as the widest number it holds — 53 on a $1,500 board, not a literal 56.
    On the 2026-09-16 board the plot goes from 247 to 286px on a 375px phone and
    from 192 to 231 on a 320px one.

    The gutter follows the NUMBERS, not the phone: a $9 board stops paying for
    a $1,500 board's figures, and a $12,345 board gets the room its extra digit
    needs rather than printing into the plot."""
    for cw, plot in ((343, 286), (288, 231)):          # the ladder on a 375 and a 320 phone
        b = _chart_box(_page(_board(_SCENE_0916, width=cw))["svg"]["html"])
        assert b["plot_l"] == 4 and b["plot_w"] == plot
        # the chip spans the gutter, from the mark column to the tags' edge,
        # and every tag, rung and edge name ends on that edge
        assert b["chip_x"] == 4 + plot + 6 and b["chip_x"] + b["chip_w"] == cw - 3
        assert b["right"] == {cw - 3} and b["num_x"] == cw - 3 - 5

    def fits(b):
        # the chip's number keeps 5px either side, measured off the shipped face
        w = _glance("console.log(JSON.stringify(g.figW(D, 12, 700)));", b["num"])
        return b["chip_w"] - 5 - w >= 5

    assert fits(b)
    for spot, sigma, walls, gutter in ((9.4, 0.4, (9.5, 9.0), 43), (12345, 200, (12400, 12300), 60)):
        scene = {"price": {"live_spot": spot}, "scale": {"one_sigma_dollars": sigma},
                 "walls": {"call": [{"strike": walls[0]}], "put": [{"strike": walls[1]}]}}
        b = _chart_box(_page(_board(scene, width=343))["svg"]["html"])
        assert b["plot_w"] == 343 - 4 - gutter, b
        assert fits(b), b


def test_the_price_ruler_names_the_silence_between_the_tags():
    """The gutter named two prices on the 2026-09-16 board, the 1,517 chip and
    the 1,500 wall, beside a price path drawn against a scale nobody could read.
    The gutter is already a column of prices, so the round ones go into it in
    small grey between the named ones — and never into a crowd: a rung is
    dropped where a tag already names its height, or where it would sit within
    17px of a tag's row. The magnet runner at 1,530 drew an amber rule nothing
    on the chart named; the ruler names it without adding a row to the tags."""
    def ruler(scene, cw=343):
        svg = _page(_board(scene, width=cw))["svg"]["html"]
        return (svg, re.findall(r'<text class="p-scale" x="([\d.]+)" y="([\d.]+)">([^<]*)</text>', svg),
                re.findall(r'<line class="p-stick" x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)"', svg))

    svg, labels, ticks = ruler(_SCENE_0916)
    # 1,500 is the wall's own tag, 1,520 the chip's rung, 1,510 crowds the chip
    assert [t for _, _, t in labels] == ["1,530", "1,540", "1,550", "1,560"]
    # each carries a tick in the mark column, at its own height
    assert {(x1, x2) for x1, _, x2 in ticks} == {("296", "300")}
    assert [float(y) for _, y, _ in ticks] == [pytest.approx(float(y) - 3.5, abs=0.11) for _, y, _ in labels]
    # the scale is the board's, not the phone's: the same rungs at the same heights at 320
    assert [(y, t) for _, y, t in ruler(_SCENE_0916, 288)[1]] == [(y, t) for _, y, t in labels]

    # seven tags, the most the solver keeps: no rung lands inside the stack
    crowded = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 100},
               "magnet": {"top_strikes": [{"strike": 1712, "share_of_book_gamma_pp": 30}]},
               "walls": {"call": [{"strike": 1740, "cluster_share_of_book_gamma_pp": 12},
                                  {"strike": 1760, "cluster_share_of_book_gamma_pp": 5}],
                         "put": [{"strike": 1660, "cluster_share_of_book_gamma_pp": 10},
                                 {"strike": 1640, "cluster_share_of_book_gamma_pp": 4}],
                         "call_heaviest_wall_behind_the_ladder": {"strike": 1780, "cluster_share_of_book_gamma_pp": 40},
                         "put_heaviest_wall_behind_the_ladder": {"strike": 1620, "cluster_share_of_book_gamma_pp": 30}}}
    for scene in (_SCENE_0916, crowded):
        svg, labels, _ = ruler(scene)
        rows = [float(y) - 4.5 for y in re.findall(r'<text class="p-(?:tag|chiptx)[^"]*" x="[\d.]+" y="([\d.]+)"', svg)]
        assert len(rows) >= 2
        for _, y, t in labels:
            # 17, less the 0.1px both heights are rounded to on the way out
            assert min(abs(float(y) - 3.5 - r) for r in rows) >= 16.9, f"{t} crowds a tag"
    # no price, no window, no scale
    assert "p-scale" not in _page(_board({"price": {"live_spot": 1700}}))["svg"]["html"]


def test_the_ruler_counts_in_round_steps_and_prints_them_exactly():
    """The finest round step — 1, 2 or 5 times a power of ten — whose pitch two
    11px numbers can sit at, so the reader counts in something they already
    count in, on any board. The step decides the decimals, so every label is the
    exact price of its rung and no two repeat. The spans are real: SNDK's
    2026-09-16 board, its $37.9 and $16.2 boards of 09-15, SPX on 2026-06-25 at
    4.8 times the price, and the arithmetic of a $10 board.

    2.5 is not a step. At whole dollars it prints 1,502.5 as "1,503", a label
    fifty cents wrong, and on a $5 strike grid it never lands on a strike."""
    cases = [[67.85, 144, 1496.37], [37.9, 144, 1600.1], [16.2, 144, 1510.3],
             [39.70, 131, 7341.77], [1.20, 144, 9.03], [0.35, 144, 9.61]]
    got = _glance("""console.log(JSON.stringify({
        runs: D.map(([span, h, lo]) => [g.axisStep(span, h), g.priceTicks(lo, lo + span, 0, h, [])]),
        none: [g.axisStep(0, 144), g.priceTicks(5, 5, 0, 144, [])]}));""", cases)
    # 20px between rungs since the ruler went to 11px (2026-09-18), where 18
    # held two 10px numbers. The $37.9 board of 09-15 pays for it: $10 where it
    # took $5, three rungs where it drew seven (SIDE-SPEC.md 6a).
    assert [(a["step"], a["dp"]) for a, _ in got["runs"]] == \
        [(10, 0), (10, 0), (5, 0), (10, 0), (0.2, 1), (0.05, 2)]
    for _, ticks in got["runs"]:
        labels = [t["label"] for t in ticks]
        assert len(labels) >= 3 and len(set(labels)) == len(labels), labels
        for t in ticks:
            assert float(t["label"].replace(",", "")) == pytest.approx(t["v"], abs=1e-9), t
        ys = [t["y"] for t in ticks]
        assert all(abs(b - a) >= 20 for a, b in zip(ys, ys[1:])), labels
    # no span is no step and no rungs, never a guess at one
    assert got["none"] == [None, []]


def test_no_mark_on_the_plot_is_eaten_by_what_is_behind_it():
    """The palette passed and the marks did not. Every stroke over the plot was
    drawn in a token that clears its floor on the card and was then knocked down,
    by stroke-opacity or by the 0.30 wash of the heaviest shade band beneath it,
    until it did not: the price rule measured 1.24:1 where a stroke needs 3, a
    second wall 1.49, the lightest magnet runner 1.18. The plot's own backdrop
    was eating the marks it exists to sit under.

    So every rule over the plot is drawn in its hue's -ink step at full
    strength, and what opacity carried moves to width, the one channel with no
    contrast cost — nearest or not for a wall, a runner's weight for the magnet
    — and never the share, which is not this rule's to say. The shade itself
    went on 2026-09-18 (test_the_plot_draws_no_shade_behind_the_line). What
    sits behind the marks now is the card and, where contracts traded, a bar
    of --band-edge, the darker of the two; every mark is measured on the bar.
    The bars stay the quietest ink on the plot, and their ends, read against
    the card beside them, clear a mark's 3:1."""
    tok = _tokens()
    bar = tok[re.search(r"fill:var\((--[a-z-]+)\)", _css_rule(".p-traded")).group(1)]
    assert _contrast(bar, tok["--s"]) < _contrast(tok["--path"], tok["--s"]), "a bar outranks the price line"
    end = tok[re.search(r"stroke:var\((--[a-z-]+)\)", _css_rule(".p-tradedend")).group(1)]
    assert _contrast(end, tok["--s"]) >= 3.0
    behind = bar
    for sel in (".p-path", ".p-orb", ".p-wall.call", ".p-wall.put", ".p-wall.passed",
                ".p-mag", ".p-magrun", ".p-prule", ".p-halo"):
        rule = _css_rule(sel)
        assert rule is not None, f"{sel} has no rule"
        assert "opacity" not in rule, f"{sel} is knocked down by opacity"
        ratio = _contrast(tok[re.search(r"stroke:var\((--[a-z-]+)\)", rule).group(1)], behind)
        assert ratio >= 3.0, f"{sel} measures {ratio:.2f}:1 over what is behind it"
    # the chart's most-read number is text on its own chip, and text needs 4.5
    fill = lambda sel: tok[re.search(r"fill:var\((--[a-z-]+)\)", _css_rule(sel)).group(1)]
    assert _contrast(fill(".p-chiptx"), fill(".p-chip")) >= 4.5

    # the second call wall is the heaviest pile on the board and still draws
    # thinner than the nearest one, and the heavier runner draws wider
    scene = {"price": {"live_spot": 1700}, "scale": {"one_sigma_dollars": 100},
             "magnet": {"top_strikes": [{"strike": 1712, "share_of_book_gamma_pp": 30},
                                        {"strike": 1690, "share_of_book_gamma_pp": 24},
                                        {"strike": 1672, "share_of_book_gamma_pp": 6}]},
             "walls": {"call": [{"strike": 1740, "cluster_share_of_book_gamma_pp": 8},
                                {"strike": 1760, "cluster_share_of_book_gamma_pp": 30}],
                       "put": [{"strike": 1660, "cluster_share_of_book_gamma_pp": 10}]}}
    svg = _page(_board(scene))["svg"]["html"]
    assert "stroke-opacity" not in svg
    walls = sorted((float(y), w) for y, w in
                   re.findall(r'<line class="p-wall \w+"[^>]*y1="([\d.]+)"[^>]*stroke-width:([\d.]+)', svg))
    assert [w for _, w in walls] == ["1.2", "2.0", "2.0"]          # 1,760 then 1,740 and 1,660
    runners = sorted((float(y), float(w)) for y, w in
                     re.findall(r'<line class="p-magrun"[^>]*y1="([\d.]+)"[^>]*stroke-width:([\d.]+)', svg))
    assert len(runners) == 2 and runners[0][1] > runners[1][1]    # 1,690 above 1,672
    assert all(1.0 <= w <= 1.9 for _, w in runners)
    # and the lead is wider than any runner can be, from its own rule
    assert "style" not in re.search(r'<line class="p-mag"[^>]*>', svg).group(0)
    assert "stroke-width:2.2" in _css_rule(".p-mag")


def test_the_bled_chart_keeps_its_ink_off_the_cards_corners():
    """The ladder runs to within 2px of the card's edge now, so the card's
    rounded corners are the ink's neighbours. The lowest ink on the chart is the
    axis feet, and it must sit above where the bottom corners start to curve —
    3px above on the shipped numbers — or a clock face paints across the
    rounding and out onto the ground. Nothing is drawn outside the chart's own
    box at either phone width."""
    card = _block(".card{").replace(" ", "")
    pad = int(re.search(r"padding:(\d+)px", card).group(1))
    radius = int(re.search(r"border-radius:(\d+)px", card).group(1))
    # the 2026-09-16 board: a refused wall named under the plot, the call side
    # measured empty, a tape for the feet to name, and a scan old enough to say
    # so. The empty side put a word in the plot until 2026-09-18; it is pinned
    # absent in test_a_side_measured_empty_is_said_on_the_card_and_not_in_the_plot
    scene = dict(_SCENE_0916, walls={"call_side_has_no_wall": True,
                                      "put": [{"strike": 1500, "cluster_share_of_book_gamma_pp": 20.44},
                                              {"strike": 1450, "cluster_share_of_book_gamma_pp": 22.3}]})
    bars = [{"ts": "2026-09-10T%02d:%02d:00-04:00" % divmod(570 + i, 60), "close": 1560 - i * 0.6,
             "volume": 30000} for i in range(70)]
    for cw in (343, 288):
        svg = _page(_board(scene, payload={"row_ts": "2026-09-10T10:40:00-04:00"},
                           bars=bars, width=cw))["svg"]
        html, h = svg["html"], float(svg["attrs"]["height"])
        texts = re.findall(r'<text class="([^"]*)" x="[\d.]+" y="([\d.]+)"[^>]*>([^<]*)<', html)
        assert {t for c, _, t in texts if c == "p-axis"} == {"09:30", "10:40"}
        assert "▼ 1,450" in html
        # a comma descends a fifth of an em below the baseline; nothing else here does
        lowest = max(float(y) + (0.2 * 12 if "," in t else 0) for _, y, t in texts)
        assert (h - lowest) + pad > radius, f"the lowest ink enters the card's corner at {cw}"
        xs = [float(x) for x in re.findall(r'\b(?:x|x1|x2|cx)="(-?[\d.]+)"', html)]
        for pts in re.findall(r'\b(?:d|points)="([^"]*)"', html):
            xs += [float(x) for x in re.findall(r'(-?[\d.]+),-?[\d.]+', pts)]
        assert min(xs) >= 0 and max(xs) <= cw, f"ink leaves the chart's box at {cw}"


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


def test_the_notes_say_what_happened_in_words_a_reader_owns():
    """WORDS-SPEC #28, #29. "One call it made earlier no longer holds." carried
    two misreads in one line: a call is a contract before it is a claim, and
    holds is a position before it is a fact. "Trading quieter than usual for
    this hour." hid the comparison, which is today against the same clock
    minute of recent sessions. A claim that changed or went off the list is
    counted; one that holds is not, and no count is no line."""
    def notes(claims, pace):
        day = dict(_day_block(), earlier_claims=[{"said_at": "13:11", "claims": claims}],
                   volume_in_reach_vs_same_time_prior_sessions=pace)
        if pace is None:
            del day["volume_in_reach_vs_same_time_prior_sessions"]
        return _overview(_page(_board({"price": {"live_spot": 1608.20},
                                       "scale": {"one_sigma_dollars": 40}, "day": day})))[5]
    got = notes([{"strike": 1700, "now": "changed"}, {"strike": 1600, "now": "holds"}], 1.37)
    assert got == [("said", "One thing it said earlier no longer applies."),
                   ("board", "Trading is busier than usual for this time of day.")]
    got = notes([{"strike": 1700, "now": "changed"}, {"strike": 1650, "now": "off_list"},
                 {"strike": 1600, "now": "changed"}, {"strike": 1550, "now": "holds"}], 0.8)
    assert got[0] == ("said", "3 things it said earlier no longer apply.")
    assert got[-1] == ("board", "Trading is quieter than usual for this time of day.")
    assert notes([], 1.0)[-1] == ("board", "Trading is about usual for this time of day.")
    assert notes([], 1.25)[-1] == ("board", "Trading is busier than usual for this time of day.")
    quiet = notes([{"strike": 1600, "now": "holds"}], None)
    assert not [t for _, t in quiet if "said earlier" in t or t.startswith("Trading")]


def test_the_price_chip_ends_on_the_strikes_figure_column():
    """The chip's integer ends where every strike above and below it ends, at
    the figure column's right edge: that shared edge is what makes the price
    read as a rung of the ladder. Tabular, a four-digit price's integer is
    35.84px at 13px/700 whatever its digits, and the chip held it behind 11px
    of padding with a 35px floor, so on every four-digit price it ended 0.84px
    past the strikes. The widths are the shipped face's, from figW.

    With tabular figures the chip is one width for every price the instrument
    trades at, so the price rule can start exactly 10px after it. The figure
    column is activityGrid's first, and it is one width on every card, because
    the chip does not move with the card."""
    pad_l, pad_r = (int(v) for v in re.search(r"padding:0 (\d+)px 0 (\d+)px", _block(".ac-chip{")).groups()[::-1])
    floor = float(re.search(r"min-width:([\d.]+)px", _css_rule(".ac-chip .int")).group(1))
    rule_l = float(re.search(r"left:([\d.]+)px", _css_rule(".ac-px::before")).group(1))
    w = _glance("console.log(JSON.stringify(D.map(a => g.figW(...a))));",
                [["1,517", 13, 700], ["9,999", 13, 700], [".00", 13, 700]])
    cols = _glance("console.log(JSON.stringify(D.map(w => g.activityGrid(w, 148.62).cols[0])));",
                   [256, 296, 311, 348])
    col = cols[2]
    assert set(cols) == {col}, "the figure column moves with the card, and the chip does not"
    assert w[0] == w[1], "the price's width depends on its digits again"
    assert pad_l + floor == col, "the chip's integer does not end on the figure column"
    assert w[0] <= floor, "a four-digit price overruns the floor that holds it to the column"
    assert rule_l - (pad_l + floor + w[2] + pad_r) == pytest.approx(10, abs=0.05)


# --- how busy each strike has been -----------------------------------------
# The 2026-09-16 15:11 board as the scan carried it: the ladder's day block and,
# for every strike the ladder shows, the strike table's open interest, volume
# and per-tally series, with the tally times they run between.
_BOARD_1511 = {
    "price": {"live_spot": 1517.0001}, "scale": {"one_sigma_dollars": 65.82},
    "day": {"lists_from": "09:34",
            "stood": [1470, 1480, 1490, 1500, 1510, 1520, 1530, 1540, 1550, 1600, 1605],
            "joined": [1430, 1450, 1495, 1545],
            "left": [[1460, "15:00"], [1475, "14:39"], [1485, "09:38"], [1555, "14:47"],
                     [1560, "14:47"], [1565, "09:38"], [1570, "14:47"], [1580, "12:19"],
                     [1610, "15:04"], [1640, "14:43"]]},
    "frames": {"book_times": ["14:22", "14:27", "14:31", "14:35", "14:39", "14:43", "14:47",
                              "14:51", "14:55", "15:00", "15:04", "15:08"]},
    "strikes": {"rows": [
        {"strike": s, "oi_calls": oc, "oi_puts": op, "vol_calls": vc, "vol_puts": vp,
         "vol_added_per_book": ser}
        for s, oc, op, vc, vp, ser in (
            (1550, 1427, 1071, 2535, 1481, [16, 85, 45, 45, 86, 120, 54, 81, 43, 30, 28]),
            (1545, 195, 396, 978, 450, [2, 63, 5, 20, 58, 89, 30, 14, 22, 6, 7]),
            (1540, 565, 571, 2743, 2270, [53, 71, 61, 84, 173, 60, 81, 59, 94, 113, 71]),
            (1530, 654, 889, 3824, 3632, [74, 139, 117, 97, 112, 105, 102, 129, 166, 134, 180]),
            (1520, 500, 750, 1282, 1596, [138, 104, 58, 181, 27, 19, 14, 26, 83, 82, 48]),
            (1510, 407, 821, 199, 615, [7, 16, 28, 31, 66, 7, 7, 13, 47, 12, 31]),
            (1500, 1699, 3911, 1118, 3861, [13, 68, 51, 52, 183, 64, 143, 60, 99, 84, 52]),
            (1495, 133, 198, 125, 2161, [100, 111, 2, 1, 3, 5, 5, 49, 6, 2, 0]),
            (1490, 295, 693, 14, 1316, [1, 5, 3, 201, 8, 105, 105, 206, 8, 116, 113]))]}}


def _ac_cells(el):
    """[(figure, multiple, gauge fill width, clipped, last cell, time)] for each
    strike row on one side of the ladder, and (count, head or scale) for a
    count row, as painted."""
    out = []
    for row in el["kids"]:
        by = {k["cls"].split()[0]: k for k in row["kids"]}
        if "ac-more" in by:
            extra = by.get("ac-head") or by.get("ac-scale")
            out.append((by["ac-more"]["text"], extra and extra["text"]))
            continue
        g = by.get("ac-gauge")
        out.append((by["ac-k"]["text"], by.get("ac-m", {}).get("text"),
                    g and g["kids"][0]["style"].get("width"), g and "over" in g["cls"].split(),
                    by.get("ac-tr", {}).get("text"), by.get("ac-time", {}).get("text")))
    return out


def test_each_strike_says_how_many_times_over_its_pile_has_traded():
    """The ladder named ten strikes and said which of three things happened to
    each, and never how much. The multiple is everything traded at the strike
    today against the contracts already standing there at last night's close,
    calls and puts summed on both sides: 1,530 traded 7,456 against 1,543, 4.8
    times over. The pile does not move during the day, so the number cannot
    drift on its own denominator.

    Under 500 standing the multiple mostly measures the smallness of the pile
    (1,495: 2,286 against 331, 6.9 times), and the row says so. No pile, or no
    volume measured — the day's first scan has open interest and no volume
    columns — is no multiple, never a zero."""
    rows = {r["strike"]: r for r in _BOARD_1511["strikes"]["rows"]}
    got = _glance("""console.log(JSON.stringify({
        t: D.rows.map(g.turnover), thin: g.THIN_PILE,
        fmt: [0.0634, 0.0999, 0.66, 4.8321, 9.949, 9.95, 12.4, 0, null, -1].map(g.gTimes)}));""",
                  {"rows": [rows[1530], rows[1495], {"strike": 1700, "oi_calls": 0, "oi_puts": 0,
                                                     "vol_calls": 40, "vol_puts": 2},
                            {"strike": 1700, "oi_calls": 900, "oi_puts": 200},
                            {"strike": 1700, "oi_calls": 900, "vol_calls": 45},
                            None]})
    assert got["t"][0] == {"mult": pytest.approx(7456 / 1543), "pile": 1543}
    assert got["t"][1] == {"mult": pytest.approx(2286 / 331), "pile": 331}
    assert got["t"][1]["pile"] < got["thin"] <= got["t"][0]["pile"]
    assert got["t"][2] is None and got["t"][3] is None and got["t"][5] is None
    # a side with no volume is a side that traded nothing, when the other side did
    assert got["t"][4] == {"mult": pytest.approx(0.05), "pile": 900}
    # two places under a tenth, one through single figures, none from ten up
    assert got["fmt"] == ["0.06×", "0.10×", "0.7×", "4.8×", "9.9×", "10×", "12×", "0.00×", None, None]


def test_the_gauge_rides_one_fixed_scale_and_marks_its_clip():
    """Full is five turns of the pile on every scan and every day, because a
    per-scan maximum fills the busiest row every time and destroys the
    comparison: at 5 the cap takes 7.6% of the 288 ladder rows on disk and one
    turn sits a fifth of the way along. Past full the bar fills its track and
    says it was clipped. Anything traded draws at least a sliver; nothing
    traded is a measured zero and draws no fill."""
    got = _glance("console.log(JSON.stringify({full: g.FULL_TURNOVER, bars: D.map(g.turnoverBar)}));",
                  [1, 2.5, 5, 5.0001, 6.906, 0.0634, 0, None, -1])
    assert got["full"] == 5
    assert got["bars"] == [{"pct": 20, "over": False}, {"pct": 50, "over": False},
                           {"pct": 100, "over": False}, {"pct": 100, "over": True},
                           {"pct": 100, "over": True}, {"pct": 2, "over": False},
                           {"pct": 0, "over": False}, None, None]


def test_the_ladder_carries_the_multiple_and_its_gauge():
    """The 2026-09-16 15:11 board, painted. Every row with a pile measured
    carries its multiple and its gauge, in the row's own state, and its last
    cell; the count row below the ladder carries the gauge's scale. 1,495 is
    both guards at once — its bar is at the cap and its last cell says why the
    number is easy. 1,485 went quiet at 09:38, is not in the strike table, and
    its row is its time and nothing else. (Which head the row above carries is
    test_the_pace_column_takes_the_head_row's.)"""
    got = _page(_board(_BOARD_1511))
    above = _ac_cells(got["acAbove"])
    assert above[0][0] == "+9" and above[1:] == [
        ("1,550", "1.6×", "32.2%", False, "slower", None),
        ("1,545", "2.4×", "48.3%", False, "slower", None),
        ("1,540", "4.4×", "88.3%", False, "steady", None),
        ("1,530", "4.8×", "96.6%", False, "faster", None),
        ("1,520", "2.3×", "46.0%", False, "slower", None)]
    assert _ac_cells(got["acBelow"]) == [
        ("1,510", "0.7×", "13.3%", False, "steady", None),
        ("1,500", "0.9×", "17.8%", False, "steady", None),
        ("1,495", "6.9×", "100.0%", True, "small pile", None),
        ("1,490", "1.3×", "26.9%", False, "faster", None),
        ("1,485", None, None, None, None, "09:38"),
        ("+6", "1×5×")]
    # the rows are unchanged in every other way
    assert [r[:3] for r in _ac_rows(got["acBelow"])][:3] == \
        [("held", "1,510", "busy all day"), ("held", "1,500", None), ("new", "1,495", "got busy")]


def test_a_strike_with_nothing_measured_carries_no_multiple_and_no_track():
    """Honest-absent, three ways. A strike that has gone quiet keeps its time
    and takes no measure even if the strike table somehow carries it — the time
    wins the cell, as `gone` wins the row. A strike the table does not carry
    gets no multiple, no gauge and no track, because an empty track reads as
    zero. And a ladder with nothing measured on it gets no head and no scale:
    a label over an empty column labels an absence."""
    rows = [dict(r) for r in _BOARD_1511["strikes"]["rows"] if r["strike"] != 1540]
    rows.append({"strike": 1485, "oi_calls": 900, "oi_puts": 900, "vol_calls": 90, "vol_puts": 9})
    got = _page(_board(dict(_BOARD_1511, strikes={"rows": rows})))
    above, below = _ac_cells(got["acAbove"]), _ac_cells(got["acBelow"])
    assert above[3] == ("1,540", None, None, None, None, None)
    assert below[4] == ("1,485", None, None, None, None, "09:38")
    cells = {k["cls"].split()[0] for row in got["acAbove"]["kids"] + got["acBelow"]["kids"]
             for k in row["kids"]}
    for bare in (dict(_BOARD_1511, strikes=None),
                 dict(_BOARD_1511, strikes={"rows": [{"strike": r["strike"], "oi_calls": r["oi_calls"],
                                                      "oi_puts": r["oi_puts"]}
                                                     for r in _BOARD_1511["strikes"]["rows"]]})):
        got = _page(_board(bare))
        kinds = {k["cls"].split()[0] for row in got["acAbove"]["kids"] + got["acBelow"]["kids"]
                 for k in row["kids"]}
        assert kinds == {"ac-more", "ac-k", "ac-dot", "ac-word", "ac-time"}, kinds
    # and with one measured row, the column is labelled
    assert {"ac-head", "ac-scale", "ac-m", "ac-gauge"} <= cells


# --- how fast each strike is trading now ------------------------------------

def _per_tally(ser):
    """The mean-per-tally ratio the design's trend() computed, kept here only
    to show a case where it and the per-minute pace part company."""
    rec, ear = [v for v in ser[-4:] if v is not None], [v for v in ser[:-4] if v is not None]
    return (sum(rec) / len(rec)) / (sum(ear) / len(ear))


def test_the_pace_is_contracts_a_minute_now_against_earlier():
    """What faster, steady and slower mean, exactly as the explainer sheet says
    it: contracts a minute over the last 4 stretches between tallies against
    the stretches before them — 4 against 7 on a full series. 1,530 at 15:11
    traded 746 contracts in the 29 minutes from 14:22 to 14:51 and 609 in the
    17 to 15:08: 25.7 a minute, then 35.8, 1.39 times as fast, so faster.
    1.25 and 0.80 are one step either way, and a ratio on the line takes it."""
    rows = {r["strike"]: r for r in _BOARD_1511["strikes"]["rows"]}
    bt = _BOARD_1511["frames"]["book_times"]
    got = _glance("console.log(JSON.stringify(D.map(([s, t]) => g.pace(s, t))));",
                  [[rows[1530]["vol_added_per_book"], bt], [rows[1540]["vol_added_per_book"], bt],
                   [rows[1550]["vol_added_per_book"], bt],
                   # 30 a minute for 28 minutes, then exactly 1.25 and exactly 0.80 of it
                   [[120] * 7 + [150] * 4, ["10:%02d" % (i * 4) for i in range(12)]],
                   [[120] * 7 + [96] * 4, ["10:%02d" % (i * 4) for i in range(12)]]])
    assert got[0] == {"before": pytest.approx(746 / 29), "now": pytest.approx(609 / 17), "word": "faster"}
    assert (round(got[0]["before"]), round(got[0]["now"])) == (26, 36)       # the sheet's figures
    assert got[1]["word"] == "steady" and got[2]["word"] == "slower"
    assert [p["word"] for p in got[3:]] == ["faster", "slower"]


def test_a_stalled_scanner_is_read_per_minute_not_per_tally():
    """The departure from the design's trend(), and the case it exists for.
    The tallies are four minutes apart until the scanner stalls; then one entry
    holds everything since the stall. On 2026-09-15 at 13:24 the last gap ran
    108 minutes, and 1,500's 974 contracts in it made the last four tallies
    average four times the seven before — per tally, faster. Per minute it was
    trading at half its earlier pace, which is what the sheet teaches the
    reader the word means. The word is the per-minute one."""
    ser = [80, 26, 57, 74, 158, 25, 22, 8, 65, 10, 974]
    bt = ["10:54", "10:58", "11:02", "11:06", "11:10", "11:14", "11:18", "11:22",
          "11:27", "11:31", "11:35", "13:23"]
    assert _per_tally(ser) >= 1.25                      # what counting tallies would say
    got = _glance("console.log(JSON.stringify(g.pace(D[0], D[1])));", [ser, bt])
    assert got["word"] == "slower"
    assert got["before"] == pytest.approx(442 / 28) and got["now"] == pytest.approx(1057 / 121)
    # and the same row on the painted ladder
    day = {"stood": [1500], "joined": [], "left": []}
    scene = {"price": {"live_spot": 1512.0}, "scale": {"one_sigma_dollars": 60}, "day": day,
             "frames": {"book_times": bt},
             "strikes": {"rows": [{"strike": 1500, "oi_calls": 1700, "oi_puts": 3391,
                                   "vol_calls": 1200, "vol_puts": 2232, "vol_added_per_book": ser}]}}
    assert _ac_cells(_page(_board(scene))["acBelow"]) == \
        [("1,500", "0.7×", "13.5%", False, "slower", None)]


def test_the_pace_is_absent_when_the_series_cannot_carry_it():
    """Two causes, one form: nothing in the cell, not an empty element. Early in
    a session there are not 3 stretches either side of the cut yet — the first
    word can come at the 8th tally of the day, 7 stretches — and a strike that
    traded under 150 contracts across the series is inside the counting noise.
    A stretch the strike was not measured across drops out with its minutes; a
    stretch that cannot be timed, a series that does not match its tally times,
    and an earlier pace of nothing are no answer."""
    t = ["10:%02d" % (i * 4) for i in range(12)]
    cases = [
        ([30] * 7, t[:8]),                                     # 8th tally: the first word
        ([30] * 6, t[:7]),                                     # 7th: not yet
        ([10] * 7 + [11, 11, 10, 11], t),                      # 113 contracts: under the floor
        ([14] * 7 + [13, 13, 13, 13], t),                      # 150 exactly: said
        ([30, None, None, None, None, None, 30, 30, 30, 30, 30], t),   # 2 measured before the cut
        ([30, None, None, None, 30, 30, 30, 90, None, 90, 90], t),   # 3 each side: said
        ([30] * 11, t[:11]),                                   # series and times disagree
        ([30] * 11, t[:5] + ["late"] + t[6:]),                 # a time that cannot be read
        ([30] * 11, t[:5] + [t[4]] + t[6:]),                   # a stretch of no minutes
        ([0] * 7 + [60] * 4, t),                               # nothing traded earlier
        (None, t), ([30] * 11, None)]
    got = _glance("console.log(JSON.stringify(D.map(([s, b]) => { const p = g.pace(s, b); return p && p.word; })));",
                  cases)
    assert got == ["steady", None, None, "steady", None, "faster", None, None, None, None, None, None]
    # an unmeasured stretch drops out with its minutes: the last four hold 270
    # contracts over the three measured 4-minute stretches, 22.5 a minute, not
    # 270 over 16
    p = _glance("console.log(JSON.stringify(g.pace(D[0], D[1])));", list(cases[5]))
    assert p["before"] == pytest.approx(120 / 16) and p["now"] == pytest.approx(270 / 12)


def test_each_row_says_faster_steady_or_slower_and_nothing_it_cannot():
    """The three forms of the last cell on a painted ladder. A thin pile says
    so and outranks its own pace (1,495 traded 331 over and was slowing: the
    card says small pile). A pile of 500 or more with too little traded to call
    gets no last cell at all — the gauge still ends where it always ends, so
    the row does not look cut off. And nothing in the cell names price."""
    rows = [dict(r) for r in _BOARD_1511["strikes"]["rows"]]
    quiet = next(r for r in rows if r["strike"] == 1510)
    quiet["vol_added_per_book"] = [7, 16, 28, 1, 6, 7, 7, 13, 7, 12, 3]       # 107 contracts
    got = _page(_board(dict(_BOARD_1511, strikes={"rows": rows})))
    below = _ac_cells(got["acBelow"])
    assert below[0] == ("1,510", "0.7×", "13.3%", False, None, None)
    assert below[2][4] == "small pile"
    lasts = {k["text"] for row in got["acAbove"]["kids"] + got["acBelow"]["kids"]
             for k in row["kids"] if k["cls"] == "ac-tr"}
    assert lasts == {"faster", "steady", "slower", "small pile"}
    assert "" not in lasts
    for word in ("price", "up", "down", "rise", "fall", "higher", "lower"):
        assert not any(word in t.split() for t in lasts)


def test_the_pace_column_takes_the_head_row():
    """The head row holds one head: "trading now against earlier" (148.62px) and
    "× what was already there" (138.14) are 298.78 side by side in a row with
    152.03 free. It goes to the pace column, because its subject is what stops
    "slower" beside a strike price reading as the price — so the multiple's unit
    is no longer on the card. That is an OPEN DECISION FOR THE OWNER
    (RATE-SPEC 11.3); this test and AC_HEAD in page.js are the whole of it.

    The head labels the words under it, so with no word on the ladder — before
    the 8th tally of the day — there is no head, while the multiples, gauges
    and scale are drawn as before."""
    got = _page(_board(_BOARD_1511))
    assert _ac_cells(got["acAbove"])[0] == ("+9", "trading now against earlier")
    assert "what was already there" not in json.dumps(got)
    early = dict(_BOARD_1511, frames={"book_times": _BOARD_1511["frames"]["book_times"][:7]},
                 strikes={"rows": [dict(r, vol_added_per_book=r["vol_added_per_book"][:6])
                                   for r in _BOARD_1511["strikes"]["rows"]]})
    got = _page(_board(early))
    above, below = _ac_cells(got["acAbove"]), _ac_cells(got["acBelow"])
    assert above[0] == ("+9", None) and below[-1] == ("+6", "1×5×")
    assert [r[4] for r in above[1:] + below[:-1]] == [None] * 7 + ["small pile", None, None]
    assert all(r[1] for r in above[1:] + below[:4])

    # with no strike further above there is no count row, and a word with no
    # head over it would read as the price: the head keeps a row of its own
    near = dict(_BOARD_1511, day=dict(_BOARD_1511["day"], stood=[1490, 1500, 1510, 1520, 1530],
                                      left=[[1485, "09:38"]]))
    top = _page(_board(near))["acAbove"]["kids"]
    assert [k["cls"] for k in top[0]["kids"]] == ["ac-head alone"]
    assert top[0]["kids"][0]["text"] == "trading now against earlier"
    assert [row["kids"][0]["text"] for row in top[1:]] == ["1,545", "1,530", "1,520"]


# --- the ladder on a narrower card ------------------------------------------
# The six columns were drawn for a 375px phone. Below it the reflow is interim —
# the owner has not decided how the grid should behave there — and it is the
# most conservative one that keeps every word legible (glance.js, activityGrid).

def test_a_narrower_card_gives_up_width_in_order():
    """Down from the 311px content box the card was drawn for, the ladder gives
    up width in one order and stops as soon as the row fits: first the gauge's
    length, by exactly what the row is short, to its floor; then the two gaps
    beside it, toward the card's 6px unit; and only then the gauge itself, so
    the multiple says how many times over on its own. Every width from 360 of
    content down to 240 is walked, a quarter pixel at a time, so a change that
    tightens the gaps while the gauge still has length to give, or drops the
    gauge while the gaps still have room, fails here.

    The head moves on its own rule, because no column can make room for it:
    right-aligned to the card's edge, it closes on "further above" by every
    pixel the card loses. It keeps the card's 12 from it at its own size, then
    a pixel smaller, and under that it takes a row of its own."""
    got = _glance("""const W = [];
        for(let w = 360; w >= 240; w -= 0.25) W.push(w);
        console.log(JSON.stringify({floor: g.GRID_TRACK_MIN,
            at: W.map(w => [w, g.activityGrid(w, 148.62)]),
            bad: [0, -5, null, NaN, undefined].map(w => g.activityGrid(w, 148.62))}));""")
    floor, last = got["floor"], "small pile"
    fits = lambda w, gap, track: w - 148 - 33 - gap - (track + gap if track else 0) - 52.28 >= 0
    prev = None
    for w, grid in got["at"]:
        cols, track, head = grid["cols"], grid["track"], grid["head"]
        gap = cols[3] - 33
        assert cols[:3] == [46, 24, 78], f"the figure, the dot or the word column moved at {w}"
        assert all(isinstance(c, int) and c >= 0 for c in cols), f"a track is not a whole length at {w}"
        if w >= 303.28:                  # the drawn row already fits: nothing moves
            assert (cols, track) == ([46, 24, 78, 45, 58], 46), f"{w}"
        if w >= 245.28:                  # down to where the row can fit at all, it does
            assert sum(cols) + 52.28 <= w, f"{last!r} runs past the card at {w}"
        if track:
            assert cols[4] == track + gap and floor <= track <= 46
            assert gap == 12 or track == floor, f"the gaps closed while the gauge could give at {w}"
            assert 6 <= gap <= 12
        else:
            assert not fits(w, 6, floor), f"the gauge went while the gaps could still give at {w}"
        if prev:
            assert (track or 0) <= (prev["track"] or 0), f"the gauge grew as the card narrowed at {w}"
        beside = w - 70 - 76.73
        assert head == ("beside" if beside - 148.62 >= 12 else
                        "smaller" if beside - 148.62 * 11 / 12 >= 12 else "alone"), f"{w}"
        prev = grid

    at = dict((w, grid) for w, grid in got["at"])
    # the owner's own phone: the gauge gives 8px and nothing else in the row moves
    assert at[296] == {"cols": [46, 24, 78, 45, 50], "track": 38, "head": "smaller"}
    # the smallest phone the page is built for: no gauge, and the words keep
    # their place against the card's edge instead of closing on the number
    assert at[256] == {"cols": [46, 24, 78, 45, 10], "track": None, "head": "alone"}
    # a card that has not laid out yet still gets whole, finite tracks
    for grid in got["bad"]:
        assert grid["track"] is None and all(isinstance(c, int) for c in grid["cols"])


def test_the_ladder_is_laid_out_on_the_cards_own_width():
    """The page measures the card and hands the width to activityGrid; this is
    that it uses the answer. Both halves of the ladder take one template, so the
    columns run straight through the price row, and every gauge and the scale
    under them take the same length. On the owner's 360px phone the track is 38
    and the head a pixel smaller beside "further above". On a 320 the gauge and
    its scale are gone and every row still carries its multiple and its word, and
    the head takes the row above the count, out of the ladder's top margin, so
    the card is no taller."""
    def gauges(got):
        return [k for side in ("acAbove", "acBelow") for row in got[side]["kids"]
                for k in row["kids"] if k["cls"].split()[0] in ("ac-gauge", "ac-scale")]

    got = _page(_board(_BOARD_1511, width=311))
    for side in ("acAbove", "acBelow"):
        assert got[side]["style"]["gridTemplateColumns"] == "46px 24px 78px 45px 58px auto"
    assert {k["style"]["width"] for k in gauges(got)} == {"46px"} and len(gauges(got)) == 10
    assert [k["cls"] for k in got["acAbove"]["kids"][0]["kids"]][-1] == "ac-head beside"
    assert "lift" not in got["acAbove"]["cls"]

    got = _page(_board(_BOARD_1511, width=296))
    for side in ("acAbove", "acBelow"):
        assert got[side]["style"]["gridTemplateColumns"] == "46px 24px 78px 45px 50px auto"
    assert {k["style"]["width"] for k in gauges(got)} == {"38px"} and len(gauges(got)) == 10
    top = got["acAbove"]["kids"][0]
    assert [(k["cls"], k["text"]) for k in top["kids"]] == [
        ("ac-more", "+9"), ("ac-word", "further above"), ("ac-head smaller", "trading now against earlier")]
    assert "lift" not in got["acAbove"]["cls"]

    got = _page(_board(_BOARD_1511, width=256))
    for side in ("acAbove", "acBelow"):
        assert got[side]["style"]["gridTemplateColumns"] == "46px 24px 78px 45px 10px auto"
    assert gauges(got) == []
    rows = got["acAbove"]["kids"]
    assert [(k["cls"], k["text"]) for k in rows[0]["kids"]] == [("ac-head alone", "trading now against earlier")]
    above, below = _ac_cells({"kids": rows[1:]}), _ac_cells(got["acBelow"])
    assert above[0] == ("+9", None) and below[-1] == ("+6", None)
    assert [(r[1], r[4]) for r in above[1:] + below[:4]] == [
        ("1.6×", "slower"), ("2.4×", "slower"), ("4.4×", "steady"), ("4.8×", "faster"), ("2.3×", "slower"),
        ("0.7×", "steady"), ("0.9×", "steady"), ("6.9×", "small pile"), ("1.3×", "faster")]
    assert "lift" in got["acAbove"]["cls"].split()
    # 18px of head row for the 18px of margin it takes: the card keeps its height
    assert len(rows) == len(_page(_board(_BOARD_1511, width=311))["acAbove"]["kids"]) + 1
    assert _css_rule(".ac-rows.top") == "margin-top:18px"
    assert _css_rule(".ac-rows.top.lift") == "margin-top:0"


# --- the reads page's explainer: what faster, steady and slower mean ---------
# Static markup on thread.html, working one real strike. Every figure in it is
# typed, so each is recomputed here by the code that prints the words it
# explains, and every word goes through the station's gates.

def _reads_sheet():
    """The reads page's sheet as a reader sees it: the title, each item's
    paragraphs and the value beside its term, the figure's words and its
    label, the caveat, the sources line, the close button, and the label of
    the button that opens it."""
    import html as _html

    def txt(s):
        return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    block = THREAD.split('<div class="sheet" id="sheet"')[1].split("\n</div>\n")[0]
    items = {}
    for item in re.findall(r'(?s)<div class="sh-item">(.*?)\n  </div>', block):
        term = re.search(r"<b>(.*?)</b>(?:<span>(.*?)</span>)?", item)
        items[term.group(1)] = {"value": term.group(2),
                                "paras": [txt(p) for p in re.findall(r"(?s)<p>(.*?)</p>", item)]}
    fig = re.search(r'(?s)<div class="sh-fig".*?\n    </div>', block).group(0)
    return {"title": txt(re.search(r'id="shTitle">(.*?)</div>', block).group(1)),
            "items": items,
            "figure": [txt(t) for _, t in re.findall(r"<([sb])>(.*?)</\1>", fig)],
            "figure_label": re.search(r'aria-label="([^"]*)"', fig).group(1),
            "caveat": txt(re.search(r'(?s)<div class="sh-caveat">(.*?)</div>', block).group(1)),
            "src": txt(re.search(r'(?s)<p class="sh-src">(.*?)</p>', block).group(1)),
            "close": txt(re.search(r'(?s)<button class="sh-close"[^>]*>(.*?)</button>', block).group(1)),
            "button": txt(re.search(r'(?s)<button class="why".*?<s>(.*?)</s>', THREAD).group(1))}


# 1,530 on 2026-09-16 at the two scans either side of the day's first pace
# word, as the station built them: at 10:01 the day had had 7 tallies, and its
# 8th came at 10:03.
_FIRST_WORD_0916 = {
    "10:01": ([411, 453, 236, 221, 263, 157],
              ["09:34", "09:38", "09:43", "09:47", "09:51", "09:55", "09:59"]),
    "10:03": ([411, 453, 236, 221, 263, 157, 128],
              ["09:34", "09:38", "09:43", "09:47", "09:51", "09:55", "09:59", "10:03"]),
}


def test_the_reads_page_sheet_works_the_example_the_code_computes():
    """The sheet on the reads page answers "faster at what?" on one real
    strike, and every figure in it is typed into static markup. So each is
    recomputed here, off the 2026-09-16 15:11 board, by the functions that
    print the words it explains, and the markup has to say what they return.

    1,530 traded 746 contracts in the 29 minutes to 14:51 and 609 in the 17
    after: 25.7 a minute, then 35.8, 1.39 times, faster. It had traded 7,456
    against the 1,543 standing at the last close: 4.8×. The earlier bar is
    71.81% of the later. The sources line names the split and the thresholds
    pace() uses. And the first marks that day came with its 8th tally, at
    10:03, not at 10:04, which was the model's reading of that scan: at 10:01
    no series was long enough for a word, and at 10:03 1,530's was."""
    sheet = _reads_sheet()
    rows = {r["strike"]: r for r in _BOARD_1511["strikes"]["rows"]}
    got = _glance("""const t = g.turnover(D.row);
      console.log(JSON.stringify({p: g.pace(D.row.vol_added_per_book, D.bt), x: g.gTimes(t.mult),
                                  first: D.first.map(([s, b]) => g.pace(s, b))}));""",
                  {"row": rows[1530], "bt": _BOARD_1511["frames"]["book_times"],
                   "first": list(_FIRST_WORD_0916.values())})
    p, x = got["p"], got["x"]
    before, now = round(p["before"]), round(p["now"])
    assert (before, now, p["word"], x) == (26, 36, "faster", "4.8×")        # what the ladder printed
    assert sheet["items"]["Strike 1,530, 16 September"]["paras"] == [
        f"Earlier that afternoon about {before} contracts a minute changed hands there. Over the last "
        f"stretch, {now} — {p['now'] / p['before']:.1f} times as much, so it read {p['word']}."]
    assert sheet["figure"] == ["earlier that afternoon", f"{before} a minute", "the last stretch", f"{now} a minute"]
    assert f"{before} contracts a minute" in sheet["figure_label"] and f"{now} a minute" in sheet["figure_label"]
    was = re.search(r"--was:([\d.]+)%", _css_rules(THREAD)[".sh-fig"]).group(1)
    assert float(was) == pytest.approx(p["before"] / p["now"] * 100, abs=0.005)
    assert sheet["items"]["The number beside it"]["value"] == x

    recent, least, fast, slow = re.search(
        r"const PACE_RECENT=(\d+), PACE_MIN=(\d+), PACE_FLOOR=\d+, PACE_FAST=([\d.]+), PACE_SLOW=([\d.]+);",
        GLANCE).groups()
    recent, least = int(recent), int(least)
    stretches = len(_BOARD_1511["frames"]["book_times"]) - 1
    assert (f"the last {recent} tallies against the {stretches - recent} before them, at least "
            f"{['one', 'two', 'three', 'four'][least - 1]} each side. {fast} times up reads faster, "
            f"{slow} and under slower.") in sheet["src"]
    assert "Figures above: strike 1,530 at 15:11 on 16 September 2026." in sheet["src"]

    (early, times), (_, first_times) = _FIRST_WORD_0916.values()
    assert len(times) - 1 < recent + least <= len(first_times) - 1      # stretches: too few, then enough
    assert got["first"][0] is None and got["first"][1]["word"]
    assert (f"On 16 September the first marks appeared at {first_times[-1]}."
            in sheet["items"]["When nothing is marked"]["paras"][0])


def test_every_word_on_the_reads_page_sheet_passes_the_laws():
    """The sheet explains a word that sits beside a strike price, which is
    exactly where a reader takes speed for a claim about price. So every
    string on it, and on the button that opens it, goes through the station's
    own gates: the reader's _BANNED_RE (forecast, causal and judgement words
    and their inflections — "which way the price will move" failed it on
    "will", and the sheet says "moves"), its position gate, the levels sheet's
    denylist, no dealer and nothing a dealer does, no Greek letter or Greek
    word, nothing that reaches forward, and no frequency: "About one strike in
    three." was cut because three counts of it gave 19%, 31% and 41%.

    Not the half-hour card's options or rate gates: this sheet exists to teach
    what a strike is, and its figures are rates."""
    R = _reader()
    s = _reads_sheet()
    words = [s["title"], s["figure_label"], s["caveat"], s["src"], s["close"], s["button"], *s["figure"]]
    for term, item in s["items"].items():
        words += [term, *item["paras"]] + ([item["value"]] if item["value"] else [])
    assert len(s["items"]) == 5 and s["caveat"].startswith("Busier, not going anywhere.")
    greek = re.compile(r"(?i)\b(?:gamma|gex|delta|vanna|charm|vega|theta)\b")
    often = re.compile(r"(?i)\b(?:one|two|three|four|\d+) (?:\w+ )?in (?:two|three|four|five|ten|\d+)\b"
                       r"|%|\bout of\b|\bper ?cent\b")
    for w in words:
        assert not R._BANNED_RE.search(w), f"{w!r} trips the reader's word gate"
        assert not R._POS_RE.search(w), f"{w!r} places price against a number"
        assert not any(d in w.lower() for d in _SHEET_CLAIMS), f"{w!r} makes a claim the sheet may not"
        assert not _DEALER.search(w), f"{w!r} speaks of dealers"
        assert not greek.search(w) and not _EMOJI_OR_GREEK.search(w), f"{w!r} puts Greek on the surface"
        assert not _AHEAD.search(w), f"{w!r} reaches forward"
        assert not often.search(w), f"{w!r} claims how often"


def test_nothing_on_the_reads_page_sheet_is_drawn_in_the_price_colour():
    """Blue is price on these screens and nothing else, and a reader arrives
    at this sheet from a page where it is. A pace is not a price, so the sheet
    and its figure carry emphasis by value, weight and position: the earlier
    stretch in --rule-soft, the later in --i, the cut in the card itself. The
    only blue that can appear is the keyboard focus ring every control on both
    pages draws, which a finger never brings up."""
    price = re.compile(r"(?i)--px-|#2F44B8|#5470E4|#E2E4F5")
    sheet = {s: d for s, d in _css_rules(THREAD).items() if re.search(r"\.sheet|\.sh-|\.scrim", s)}
    painted = {s: d for s, d in sheet.items() if ":focus-visible" not in s and price.search(d)}
    assert not painted, f"the sheet is drawn in the price colour: {painted}"
    assert "background:var(--rule-soft)" in sheet[".sh-fig .was i"]
    assert "background:var(--i)" in sheet[".sh-fig .now::before,.sh-fig .now::after"]
    block = THREAD.split('<div class="sheet" id="sheet"')[1].split("\n</div>\n")[0]
    assert not price.search(block) and "style=" not in block and 'class="n"' not in block


# --- THE LAST HALF HOUR, and apart from it the record ------------------------

def _hh_scene(moved, ruler, scan):
    return {"price": {"live_spot": 1517.0, "moved_last_30min_sigma": moved},
            "scale": {"one_sigma_dollars": ruler}, "data_sources": {"scan_taken_at": scan}}


# SPLIT-SPEC.md's four boards as the phone receives them: the three scene
# fields the card reads, off state/sndk_payloads, and the record
# snapshot._earlier_half_hours builds from the store for that session.
_HH_REC_0916 = {"sessions": 33, "first": "2026-07-28", "last": "2026-09-15",
                "half_hours": 403, "pairs": 368, "usual_sigma": 0.09299722333520812,
                "bigger": {"n": 179, "other_way": 109, "same_way": 70},
                "no_bigger": {"n": 189, "other_way": 90, "same_way": 99}}
_HH_REC_0917 = {"sessions": 34, "first": "2026-07-28", "last": "2026-09-16",
                "half_hours": 416, "pairs": 380, "usual_sigma": 0.09320564582004712,
                "bigger": {"n": 185, "other_way": 114, "same_way": 71},
                "no_bigger": {"n": 195, "other_way": 95, "same_way": 100}}
_HH_BOARDS = {
    "A": [_hh_scene(-0.29, 65.82, "2026-09-16T15:10:21.661774-04:00"), _HH_REC_0916],
    "B": [_hh_scene(-0.09, 53.71, "2026-09-17T11:36:35.011383-04:00"), _HH_REC_0917],
    "C": [_hh_scene(0.35, 60.74, "2026-09-17T10:38:57.466814-04:00"), _HH_REC_0917],
    "D": [_hh_scene(0.0, 53.71, "2026-09-17T11:09:47.587370-04:00"), _HH_REC_0917],
}
_HH_NEXT = "half hours. Here is what came next each time:"


def _half_hours(cases, tz="America/Los_Angeles"):
    """{name: halfHour(scene, record)} off the real glance.js, for {name:
    [scene, record]}. On a Pacific clock by default, the station's own."""
    return _glance("const o={};for(const k in D) o[k]=g.halfHour(D[k][0], D[k][1]);"
                   "console.log(JSON.stringify(o));", cases, tz=tz)


def _hh_card(got):
    """The card as page.js painted it, or None when it is hidden."""
    if got["hh"]["hidden"]:
        return None
    return {"since": got["hhSince"]["text"], "say": [k["text"] for k in got["hhSay"]["kids"]],
            "span": got["hhSpan"]["text"], "set": [k["text"] for k in got["hhSet"]["kids"]],
            "bar": [(k["cls"], k["style"]) for k in got["hhBar"]["kids"]],
            "out": [k["text"] for k in got["hhOut"]["kids"]]}


def test_the_last_half_hour_is_the_boards_own_move_set_apart_from_the_record():
    """SPLIT-SPEC.md's board A, 2026-09-16 at 15:11, painted whole: the half hour
    on screen under THE LAST HALF HOUR and its clock, the record under EARLIER
    HALF HOURS and its span, the bar split 109 to 70, the captions naming the
    direction.

    The move is the scene's own, -0.29 of a $65.82 day, $19.09 — not the minute
    bars', whose 14:40 and 15:10 closes, on the station beside it, are $26.74
    apart. The two are different clocks and neither is wrong; the spec put the
    card on the scene's (GAMMA-CARD-SPEC.md 10.1), and a change of clock has to
    be a decision here rather than a drift. The clock is market time on a
    Pacific phone."""
    scene, rec = _HH_BOARDS["A"]
    bars = [{"ts": "2026-09-16T14:40:00-04:00", "open": 1535.36, "high": 1540.9, "low": 1535.0,
             "close": 1540.1, "volume": 17340.0},
            {"ts": "2026-09-16T15:10:00-04:00", "open": 1518.195, "high": 1519.1329, "low": 1513.25,
             "close": 1513.36, "volume": 22176.0}]
    got = _page(_board(scene, now="2026-09-16T15:11:19-04:00",
                       payload={"earlier_half_hours": rec}, bars=bars), tz="America/Los_Angeles")
    assert _hh_card(got) == {
        "since": "Since 14:40",
        "say": ["Down $19 in the last half hour.", "A usual half hour on this stock is $6."],
        "span": "33 trading days",
        "set": ["That was bigger than usual. So were 179 earlier", _HH_NEXT],
        "bar": [("", {"flexGrow": "109"}), ("", {"flexGrow": "70"})],
        "out": ["109 went back up", "70 kept going down"]}


def test_the_captions_name_the_direction_the_sentence_printed():
    """The captions used to say "went the other way" and "went the same way",
    which name no subject. They now name the direction the sentence two lines up
    printed, decided by the same field's sign: down, so the other way is back
    up; up, so it is back down. Exactly flat has no direction, and the abstract
    words come back. It is a renaming of the record's buckets, not a recount:
    the counts are the same whichever words they carry.

    Every line of SPLIT-SPEC.md 2's table, on all four of its real boards,
    and -0.0, which is flat too."""
    got = _half_hours(dict(_HH_BOARDS, E=[_hh_scene(-0.0, 53.71, "2026-09-17T11:09:47-04:00"),
                                          _HH_REC_0917]))
    say = {k: v["say"] for k, v in got.items()}
    assert say == {"A": ["Down $19 in the last half hour.", "A usual half hour on this stock is $6."],
                   "B": ["Down $5 in the last half hour.", "A usual half hour on this stock is $5."],
                   "C": ["Up $21 in the last half hour.", "A usual half hour on this stock is $6."],
                   "D": ["Flat $0 in the last half hour.", "A usual half hour on this stock is $5."],
                   "E": ["Flat $0 in the last half hour.", "A usual half hour on this stock is $5."]}
    assert {k: (v["since"], v["span"]) for k, v in got.items()} == {
        "A": ("14:40", "33 trading days"), "B": ("11:06", "34 trading days"),
        "C": ("10:08", "34 trading days"), "D": ("10:39", "34 trading days"),
        "E": ("10:39", "34 trading days")}
    assert {k: v["set"][0] for k, v in got.items()} == {
        "A": "That was bigger than usual. So were 179 earlier",
        "B": "That was no bigger than usual. So were 195 earlier",
        "C": "That was bigger than usual. So were 185 earlier",
        "D": "That was no bigger than usual. So were 195 earlier",
        "E": "That was no bigger than usual. So were 195 earlier"}
    assert {v["set"][1] for v in got.values()} == {_HH_NEXT}
    assert {k: [(o["n"], o["words"]) for o in v["out"]] for k, v in got.items()} == {
        "A": [(109, "went back up"), (70, "kept going down")],
        "B": [(95, "went back up"), (100, "kept going down")],
        "C": [(114, "went back down"), (71, "kept going up")],
        "D": [(95, "went the other way"), (100, "went the same way")],
        "E": [(95, "went the other way"), (100, "went the same way")]}


def test_bigger_than_usual_is_the_cut_the_record_was_split_on():
    """"That was bigger than usual" is the record's own cut said in words: a half
    hour at least as big as the median is bigger, and its counts are the pairs
    that opened at least that big. So the sentence cannot disagree with the
    counts under it. A move exactly at the median is bigger; a cent of a sigma
    under it is not, whichever way it went."""
    rec = dict(_HH_REC_0917, usual_sigma=0.09)
    scan = "2026-09-17T11:36:35-04:00"
    got = _half_hours({"at": [_hh_scene(-0.09, 53.71, scan), rec],
                       "up_at": [_hh_scene(0.09, 53.71, scan), rec],
                       "under": [_hh_scene(-0.08, 53.71, scan), rec],
                       "up_under": [_hh_scene(0.08, 53.71, scan), rec]})
    assert {k: (v["set"][0], [o["n"] for o in v["out"]]) for k, v in got.items()} == {
        "at": ("That was bigger than usual. So were 185 earlier", [114, 71]),
        "up_at": ("That was bigger than usual. So were 185 earlier", [114, 71]),
        "under": ("That was no bigger than usual. So were 195 earlier", [95, 100]),
        "up_under": ("That was no bigger than usual. So were 195 earlier", [95, 100])}


def test_the_card_is_absent_rather_than_half_drawn():
    """Every line hangs on the move, its clock, the ruler and the record, so the
    card is whole or not there. Not there when the diary has no half-hour move;
    when the scan has no clock; when there is no ruler; when the payload
    carries no record, because it predates the field or there was nothing to
    count; with fewer than two earlier sessions or two half hours on this side
    of usual, because every sentence on the card is plural; when the two counts
    do not add up to the half hours they split.

    And not before the session has had a half hour. The reader measures its
    "30-minute" move off the session's first scan from 20 minutes in: at 09:51
    on 2026-09-16 the scene read -0.39 across 20.7 minutes, and the card would
    have said SINCE 09:21, a time before the open. From a 10:00 scan it is
    SINCE 09:30 and the card is drawn."""
    scene, rec = _HH_BOARDS["A"]
    at = lambda t: dict(scene, data_sources={"scan_taken_at": t})
    cases = {
        "the whole card": [scene, rec],
        "no move": [dict(scene, price={"live_spot": 1517.0, "moved_last_30min_sigma": None}), rec],
        "no move field": [dict(scene, price={"live_spot": 1517.0}), rec],
        "no clock": [dict(scene, data_sources={}), rec],
        "a clock that is no time": [at("15:10"), rec],
        "twenty minutes in": [dict(at("2026-09-16T09:51:14-04:00"),
                                   price={"moved_last_30min_sigma": -0.39}), rec],
        "a second short": [at("2026-09-16T09:59:59-04:00"), rec],
        "half an hour in": [at("2026-09-16T10:00:00-04:00"), rec],
        "no ruler": [dict(scene, scale={"one_sigma_dollars": None}), rec],
        "a zero ruler": [dict(scene, scale={"one_sigma_dollars": 0}), rec],
        "no record": [scene, None],
        "an empty record": [scene, {}],
        "no usual half hour": [scene, dict(rec, usual_sigma=0)],
        "one earlier session": [scene, dict(rec, sessions=1)],
        "one half hour to count": [scene, dict(rec, bigger={"n": 1, "other_way": 1, "same_way": 0})],
        "no count on this side": [scene, {k: v for k, v in rec.items() if k != "bigger"}],
        "counts that do not add up": [scene, dict(rec, bigger={"n": 179, "other_way": 109, "same_way": 69})],
    }
    got = _half_hours(cases)
    drawn = {k for k, v in got.items() if v is not None}
    assert drawn == {"the whole card", "half an hour in"}
    assert got["half an hour in"]["since"] == "09:30"

    # painted: a payload without the field draws no card, and a card that was
    # drawn goes when the next payload has no move — a repaint takes it away
    assert _hh_card(_page(_board(scene))) is None
    got = _page(_board(scene, payload={"earlier_half_hours": rec}), """
      const before = dump();
      NET.payload = JSON.parse(JSON.stringify(NET.payload));
      NET.payload.scene.price.moved_last_30min_sigma = null;
      await run('loadPayload()'); await settle();
      return {before, after: dump()};""")
    assert _hh_card(got["before"]) is not None and _hh_card(got["after"]) is None


def test_the_two_outcomes_differ_in_length_and_nothing_else():
    """The one rule the card will not trade away. The two segments are the same
    element with the same class, the same height, fill and radius, and differ
    only in how far they grow, which is the count; the two counts are the same
    element in the same type. The order is fixed, other way then same way, even
    where the second is the larger (board B, 95 then 100): sorted, the larger
    count would lead on every board. No rule in the stylesheet reaches one
    segment or one caption and not the other.

    A count of zero is a finding and its caption prints, but it draws no
    segment: the stylesheet's 8px floor would give it a bar for nothing."""
    scene, rec = _HH_BOARDS["B"]
    got = _page(_board(scene, payload={"earlier_half_hours": rec}))
    card = _hh_card(got)
    assert card["bar"] == [("", {"flexGrow": "95"}), ("", {"flexGrow": "100"})]
    assert card["out"] == ["95 went back up", "100 kept going down"]
    counts = [cap["kids"][0] for cap in got["hhOut"]["kids"]]
    assert [c["cls"] for c in counts] == ["", ""] and [c["style"] for c in counts] == [{}, {}]

    zero = dict(rec, no_bigger={"n": 195, "other_way": 0, "same_way": 195})
    card = _hh_card(_page(_board(scene, payload={"earlier_half_hours": zero})))
    assert card["bar"] == [("", {"flexGrow": "195"})]
    assert card["out"] == ["0 went back up", "195 kept going down"]

    css = re.sub(r"(?s)/\*.*?\*/", "", PHONE)
    sels = {s.strip() for block in re.findall(r"([^{}]+)\{[^{}]*\}", css)
            for s in block.split(",") if re.search(r"\.hh-(?:bar|out)\b", s)}
    assert sels == {".hh-bar", ".hh-bar i", ".hh-out", ".hh-out b"}, \
        f"a rule can style one outcome apart from the other: {sorted(sels)}"
    assert "background:var(--i-body)" in _css_rule(".hh-bar i")
    assert "flex:1 1 0" in _block(".hh-bar i{"), "the segments no longer grow from a basis of 0"


def test_every_word_on_the_half_hour_card_passes_the_stations_gates():
    """This card sits beside a price and its history, which is exactly where a
    reader takes a record for a forecast. So every string it can print — the
    markup's fixed words and every line halfHour returns, on the four real
    boards and on a move each way either side of usual — goes through the gates
    SPLIT-SPEC.md 5 ran by hand (the mockup's filters.py), read off what the
    card draws rather than a list that can drift from it:

      1. the reader's own _BANNED_RE: forecast, causal and judgement words with
         their inflections — rebound, reversal, lean, forecast among them;
      2. its position gate, _POS_RE: no sentence placing price above or under a
         number;
      3. the explainer sheet's denylist;
      4. the regime words;
      5. no dealer and nothing a dealer does, in any wording;
      6. no options vocabulary: nothing here needs a strike or a contract;
      7. no emoji, no Greek;
      8. no rate: no percentage, decimal, "out of" or "1 in 3";
      9. nothing that grades the split or reaches forward: no "usually", "more
         often", "most", "likely", "will", "would". The reader may draw that
         inference; the card may not hand it to him."""
    R = _reader()
    section = re.search(r'(?s)<section class="card hh".*?</section>', PHONE).group(0)
    words = {t.strip() for t in re.split(r"<[^>]+>", section) if t.strip()}
    assert words == {"The last half hour", "Earlier half hours",
                     "Nothing past that next half hour was measured."}
    scan = "2026-09-16T15:10:21-04:00"
    cases = dict(_HH_BOARDS, **{f"{d} {s}": [_hh_scene(m, 65.82, scan), _HH_REC_0916]
                                for d, s, m in (("up", "bigger", 0.35), ("up", "no bigger", 0.05),
                                                ("down", "bigger", -0.29), ("down", "no bigger", -0.05))})
    for card in _half_hours(cases).values():
        words |= {"Since " + card["since"], card["span"], *card["say"], *card["set"]}
        words |= {o["words"] for o in card["out"]} | {f'{o["n"]} {o["words"]}' for o in card["out"]}
    for need in ("went back up", "went back down", "kept going up", "kept going down",
                 "went the other way", "went the same way",
                 "That was bigger than usual. So were 179 earlier",
                 "That was no bigger than usual. So were 189 earlier"):
        assert need in words, f"the gates never saw {need!r}"

    options = re.compile(r"(?i)strike|gamma|open interest|expir|premium|delta|implied|option|"
                         r"contract|vega|theta")
    rate = re.compile(r"(?i)%|\bout of\b|\bper ?cent\b|\d\.\d|\b\d+ in \d+\b")
    for s in sorted(words):
        assert not R._BANNED_RE.search(s), f"{s!r} trips the reader's word gate"
        assert not R._POS_RE.search(s), f"{s!r} places price against a number"
        assert not any(w in s.lower() for w in _SHEET_CLAIMS), f"{s!r} makes a claim the sheet may not"
        assert not any(w in s.lower() for w in ("walls hold", "walls give way")), s
        assert not _DEALER.search(s), f"{s!r} speaks of dealers"
        assert not options.search(s), f"{s!r} needs options vocabulary"
        assert not _EMOJI_OR_GREEK.search(s), s
        assert not rate.search(s), f"{s!r} states a rate"
        assert not _AHEAD.search(s), f"{s!r} grades the split or reaches forward"


def test_the_card_sits_between_the_reading_and_the_three_levels():
    """Its place on the screen, per SPLIT-SPEC.md and the whole-screen sheet:
    after What it means, before The three levels, moving neither. It is on the
    card shell every other card uses, and hidden in the markup, so the first
    frame draws no card until a payload says there is one to draw."""
    at = {k: PHONE.index(k) for k in ('<section class="read">', '<section class="card hh" id="hh" hidden>',
                                      '<section class="card levels" id="levels"')}
    assert list(at) == sorted(at, key=at.get)
    paint = PAGE.split("function paintAll(){")[1].split("\n}")[0]
    assert paint.index("paintRead();") < paint.index("paintHalf(st);") < paint.index("paintFoot(st);")


def test_the_move_goes_back_into_dollars_on_the_ruler_it_was_measured_on():
    """with_path divides the half hour's travel by the diary row's own sigma,
    which is the glance's scene's one_sigma_dollars — the ruler the regime row
    already prints. The Strikes Payload clamps its ruler to the day's anchor on
    an expiry afternoon, and times that ruler the move would come back short:
    here $17 for a $19 half hour."""
    scene, rec = _HH_BOARDS["A"]
    strikes = dict(scene, scale={"one_sigma_dollars": 60.0})
    got = _page(_board(strikes, payload={"legacy": {"scene": scene}, "earlier_half_hours": rec}))
    assert _hh_card(got)["say"][0] == "Down $19 in the last half hour."
    assert got["ruler"]["text"] == "USUAL DAY MOVE $66"
