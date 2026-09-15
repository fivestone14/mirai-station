"""The page's self-refresh contract, pinned as strings so a later edit that slices
the script between two markers (it happened on 08-21: the hanging-indent rewrite
dropped the version poller) fails the suite instead of silently shipping a page
that can never show the refresh pill. Where a pinned piece is a pure function, or
a method that reads only what it is handed, it is lifted out of the page by name
and run in node instead, so the test holds what it does rather than how it is spelt."""
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import snapshot

PAGE = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
_NODE = shutil.which("node")


def _method(name):
    """The body of one method of the page's object literals: `name{` to the brace
    that closes it at two-space indent."""
    head = "\n  %s{" % name
    assert head in PAGE, f"{name} is gone from the page"
    return PAGE.split(head, 1)[1].split("\n  },", 1)[0]


def _run_js(js, data=None):
    """Run `js` in node with `D` parsed from `data`, and return the one JSON value
    it prints. Skips (after any source assertions the caller made first) when
    node is not installed."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = "const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + js
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data),
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _code_only(js):
    """The page without its comments, which quote retired wording on purpose."""
    lines = [l for l in js.splitlines() if not l.strip().startswith(("//", "*", "/*"))]
    return "\n".join(l.split("//")[0] for l in lines)


def _reader(monkeypatch):
    monkeypatch.syspath_prepend(str(snapshot._SNDK_PRO_DIR))
    import sndk_read
    return sndk_read


def _payload(tmp_path, monkeypatch):
    """What /api/sndk/payload serves the tab, built by the real builder from four
    scans two minutes apart, each re-serving a book two minutes older than itself."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SNDK_PAYLOAD", raising=False)
    last = datetime(2026, 8, 19, 13, 1, tzinfo=ZoneInfo("America/New_York"))
    rows = []
    for back in (6, 4, 2, 0):
        ts = last - timedelta(minutes=back)
        rows.append({"ticker": "SNDK", "ts": ts.isoformat(), "spot": 1586.2 - back, "sigma": 80.0,
                     "prior_close": 1554.5, "gamma_sign": "negative", "regime": "trending",
                     "gex_views": {"front_dte": 2, "magnet": 1600.0,
                                   "mass_by_strike": [[1600.0, 40.0], [1700.0, 30.0], [1500.0, 25.0]],
                                   "net_by_strike": [[1550.0, -3.0e6], [1600.0, -2.0e6], [1700.0, 4.0e6]]},
                     "meta": {"expiries": [{"date": "2026-08-21", "dte": 2}], "book_source": "disk_cache",
                              "book_asof": (ts - timedelta(minutes=2)).isoformat()}})
    diary = tmp_path / "sndk_reversion"
    diary.mkdir()
    (diary / "2026-08-19.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return snapshot.sndk_payload(last + timedelta(minutes=1))


def test_page_has_the_refresh_pill_and_its_poller():
    assert 'id="reload-bar"' in PAGE            # the pill
    assert 'id="ver-stamp"' in PAGE             # the visible "page HH:MM · checked HH:MM" stamp
    assert "fetch('/api/version'" in PAGE       # the poller asks the server
    assert "let seen=null" in PAGE              # ...and compares against the version it loaded with
    assert "setInterval(tick, 10000)" in PAGE   # every 10 s
    assert "visibilitychange" in PAGE           # and re-checks the moment the tab comes back


def test_page_has_the_payload_tab_wired():
    assert 'data-tab="payload"' in PAGE and 'id="p-payload"' in PAGE
    assert "const PAYLOAD = {" in PAGE
    assert "/api/sndk/payload?user=" in PAGE
    assert "PAYLOAD.show()" in PAGE             # switchTab mounts it


def test_step_seven_explains_the_wake_gate(tmp_path, monkeypatch):
    """The pipeline's STEP 7 is the only step most scans never reach, so it
    carries the gate in plain words — built from the server's numbers, and
    advertised, because a card that hides something has to look like it does.
    Run on the payload's own gate block: every gate the card reads is one the
    payload sends, and every number it quotes is the number it was sent."""
    assert "PAYLOAD.gateTip()" in PAGE          # the tooltip is rendered into the card
    assert 'class="cue"' in PAGE                # the hover/tap cue
    assert "PAYLOAD.tipOpen" in PAGE            # tap works where there is no hover (tablet)
    tip = _method("gateTip()")
    read = sorted(set(re.findall(r"\bg\.([a-z_]+)", tip)))
    gates = _payload(tmp_path, monkeypatch)["gates"]
    assert read and set(read) <= set(gates), \
        f"the card reads gates the payload does not send: {sorted(set(read) - set(gates))}"
    # numbers none of the card's fallbacks carry, so each one it quotes came off the payload
    sent = {k: 101 + i for i, k in enumerate(read)}
    card = _run_js("const PAYLOAD={data:D};PAYLOAD.gateTip=function(){%s};"
                   "console.log(JSON.stringify(PAYLOAD.gateTip()));" % tip, {"gates": sent})
    unquoted = sorted(k for k, v in sent.items() if not re.search(r"(?<![\d.])%d(?![\d.])" % v, card))
    assert unquoted == [], f"the card does not quote these gates: {unquoted}"


def test_memory_view_is_two_columns_with_a_spine():
    """The Memory redesign (08-22) has three pieces a later edit must not quietly drop:
    the left rail's tier switch (the <select> it replaced is gone), the spine that draws
    the session down the gutter, and the wake badge that says in one sentence why the
    model looked. A day-scoped ask must also keep carrying its date — the history CLI
    reads one file per session, so an ask with no date silently searches today."""
    assert 'id="mem-tiers"' in PAGE and 'class="mem-side"' in PAGE
    assert 'id="mem-tier"' not in PAGE           # the old <select> is retired
    assert "MEM.setTier(" in PAGE and "MEM.showDay(" in PAGE
    assert "drawSpine()" in PAGE and 'id="mem-spine"' in PAGE
    assert "MEM.WAKE" in PAGE and "'gate event'" in PAGE
    assert "'Short term'" in PAGE and "'Medium term'" in PAGE and "'Long term'" in PAGE
    assert "if(tier==='slices'){ const d=MEM.sel||MEM.newestDay(); if(d){ MEM.sel=d; p.date=d; } }" in PAGE

def test_a_chart_callout_can_never_reach_the_right_hand_readouts():
    """08-22, seen on the SNDK chart: the in-plot callout "Abs Gamma 3 \u00b7 1,590" printed hard
    against "Tipping point" in the value gutter \u2014 two unrelated facts fused into one run of
    text. Trusting the plot edge (lineEnd-4) was the mistake: the tick lane and the value
    column both start within a few pixels of it. Three things keep it from coming back and
    all three are pinned here, because a search rule is easy to reintroduce without them."""
    assert "gx1=lineEnd-px(12)" in PAGE                       # a real margin, not the bare edge
    assert "mark(gx1, gy0, gx0+nc*CW, gy1)" in PAGE           # ...and the strip past it is no-go
    assert "put.x=Math.max(gx0, Math.min(put.x, gx1-tw))" in PAGE   # ...and every branch is clamped

def test_the_manifest_link_carries_the_login():
    """08-23: the public front door asked for the password TWICE. One wall, two requests \u2014 the
    page carried the Basic-auth header, the manifest did not, because a manifest fetch is
    specified to omit credentials unless the link says otherwise, so Caddy 401'd it and the
    browser prompted again. Pinned: dropping the attribute brings the double prompt back."""
    assert 'rel="manifest"' in PAGE
    assert 'href="/manifest.webmanifest" crossorigin="use-credentials"' in PAGE


def test_mirai_caption_says_when_and_how_long_ago():
    """08-26: the thought-line caption carries the clock the read was issued at and how long
    ago, measured from reading_ts against the wall clock (the row's own reading_age_min froze
    between scans and all evening after the close). Four pieces a later edit must not drop:
    the stamp tspans, the in-place ticker on the spot poll, the hover card's live token fill,
    and the pure helpers both of them read from, which are run here on pinned ages."""
    assert 'class="snk-ago"' in PAGE and 'class="snk-when"' in PAGE   # the caption's second voice
    assert "data-since=" in PAGE                                       # the stamp the ticker/tip read
    assert "SNDK.tickAges();" in PAGE and "tickAges(){" in PAGE        # re-ticked on the spot poll
    assert "snkMinsSince(since, now)" in PAGE                          # age is measured, not read
    assert "{{ago}}¦m:{{pct}}¦f:{{left}}" in PAGE                      # the card's live tokens...
    assert "snkLapseFill(snkMinsSince(sinceEl.dataset.since))" in PAGE  # ...filled at hover time
    helpers = PAGE[PAGE.index("function snkMinsSince("):PAGE.index("function snkLapseRows(")]
    issued = datetime(2026, 9, 15, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    got = _run_js(helpers + "console.log(JSON.stringify({"
                  "since:[snkMinsSince(D.issued, D.later), snkMinsSince(D.issued, D.earlier),"
                  " snkMinsSince('not a time', D.later)],"
                  "fill:[12, 30, 45, null].map(snkLapseFill)}));",
                  {"issued": issued.isoformat(),
                   "later": int((issued + timedelta(minutes=12, seconds=59)).timestamp() * 1000),
                   "earlier": int((issued - timedelta(minutes=1)).timestamp() * 1000)})
    # whole minutes elapsed, never negative, and nothing for a stamp that is not a time
    assert got["since"] == [12, 0, None]
    young, closing, old, unknown = got["fill"]
    assert (young["ago"], young["pct"]) == ("12m ago", 40) and young["left"].startswith("18m of its 30-min window")
    assert closing["pct"] == 100 and "closes about now" in closing["left"]
    assert (old["ago"], old["pct"]) == ("45m ago", 100) and "closed 15m ago" in old["left"]
    assert unknown == {"ago": "", "pct": 0, "left": ""}


def test_freshness_box_covers_every_layer_and_ticks():
    """08-26: the chart carries a small per-layer freshness box under the ⓘ. Each layer
    prints the clock its data carries and a counter; the counter is a 1 s ticker that reads
    state only. Pinned: the mounter, the ticker on the poller lifecycle, the slow fetches'
    stamps, the chain's own clock on the model, and the info-modal entry."""
    assert "function snkMountFresh(" in PAGE and 'id="snk-fresh-lead"' in PAGE
    assert "SNDK.timers.fresh=setInterval(()=>SNDK.freshTick(), 1000)" in PAGE
    assert "clearInterval(SNDK.timers.fresh)" in PAGE                 # dies with the pollers
    for k in ("bars", "chain", "price", "read", "mirai", "path", "tape"):
        assert f"['{k}'," in PAGE                                      # one row per layer
    assert "SNDK.pathAt=Date.now()" in PAGE and "SNDK.tapeAt=Date.now()" in PAGE
    assert "bookAsof:(typeof meta.book_asof==='string')" in PAGE       # the chain's own stamp
    assert "['11','Freshness chip'," in PAGE                           # documented in the key
    assert "SNDK._freshEl=box" in PAGE and "getElementById('snk-tenors')" in PAGE   # lives in the tenors strip, node kept across rewrites
    assert "hour12:true, timeZone:PT" in PAGE                        # AM/PM clocks are Pacific here — the page's rule
    assert "at least 2× what the whole book turns over" in PAGE       # the ring's exact rule, in the key
    assert '<svg class="snki-g"' in PAGE                              # every entry carries its glyph


def _fresh_tick(cases):
    """SNDK.freshTick run on stand-in rows. A case gives the scan's and the book's
    age in minutes (`book` None: the scan carries no book stamp; `scan` None: no
    scan yet), whether staleInfo() calls the scan stale, and the stale-book
    minutes the payload sent (absent until the tab has fetched it). Every other
    layer carries a fresh stamp, so a row that wrongly takes the scan's verdict
    shows. Returns, per case, the rows marked stale, the box's own mark and the
    lead line."""
    js = """
const KEYS=['bars','chain','price','read','mirai','path','tape'];
const marks=s=>({toggle(c,on){ if(on) s.add(c); else s.delete(c); }, add(c){ s.add(c); }, remove(c){ s.delete(c); }});
const tick=new Function('document','window','SNDK','snkClock12','snkCount', %s);
console.log(JSON.stringify(D.map(c=>{
  const now=Date.now(), at=m=>m==null?null:new Date(now-m*60000).toISOString();
  const rows={}, lead={textContent:''}, boxMarks=new Set();
  for(const k of KEYS){ const s=new Set(), b={}, e={}; rows[k]={marks:s, classList:marks(s), querySelector:q=>q==='b'?b:e}; }
  const box={classList:marks(boxMarks),
             querySelector:q=>{ const m=q.match(/data-k="(\\w+)"/); return m?rows[m[1]]:q==='#snk-fresh-lead'?lead:null; }};
  const SNDK={model:c.scan==null?null:{ts:at(c.scan), bookAsof:at(c.book)}, read:{ts:at(1), reading_ts:at(1)},
              spot:{at:now, live:true}, pathAt:now, tapeAt:now, staleInfo:()=>c.scan==null?null:{stale:c.scan_stale}};
  const window=c.stale_book_min==null?{}:{SNDK_STALE_BOOK_MIN:c.stale_book_min};
  tick({getElementById:id=>id==='snk-fresh'?box:null}, window, SNDK, String, ms=>`${Math.round(ms/60000)}m`);
  return {stale:KEYS.filter(k=>rows[k].marks.has('stale')), box:boxMarks.has('stale'), lead:lead.textContent};
})));"""
    return _run_js(js % json.dumps(_method("freshTick()")), cases)


def test_the_chain_row_is_judged_on_the_books_own_clock(monkeypatch):
    """08-30: the chain row inherited `bars.stale`, which measures how old the
    SCAN is. The feed serves a disk cache on about half the scans, so a book
    2-4 minutes older than its own scan sat under a chip that said fresh because
    the scan was fresh — the layer with the longest lag was the one layer that
    could not report it. It carries its own stamp and its own ceiling now, and
    that ceiling is the reader's STALE_BOOK_MIN, or the payload's number once the
    tab has fetched it. Only `bars`, the row whose clock staleInfo() measures,
    takes the scan's verdict."""
    ceiling = _reader(monkeypatch).STALE_BOOK_MIN
    fresh_scan = {"scan": 1, "scan_stale": False}
    got = _fresh_tick([dict(fresh_scan, book=ceiling - 0.1),
                       dict(fresh_scan, book=ceiling + 0.1),
                       dict(fresh_scan, book=ceiling + 0.1, stale_book_min=ceiling + 2),
                       {"scan": ceiling - 1, "book": ceiling - 0.5, "scan_stale": True}])
    assert [g["stale"] for g in got] == [[], ["chain"], [], ["bars"]]


def test_the_oi_lanes_book_stamp_is_judged_on_the_books_own_clock():
    """08-30, the same fix in the second place it was wrong: the age printed
    at the head of the OI/volume lane took its colour from staleInfo(), which
    measures the SCAN clock. The book is never younger than the scan and lagged
    it by up to 3.35 min on the recorded tape, so there was a band where the
    FRESH chip called the book stale and the number printed right beside the
    bars that book drew stayed grey. The stamp reads meta.book_asof and applies
    the reader's own STALE_BOOK_MIN, exactly as the chip's chain row does.

    Sliced from the `const bkT` line so the paragraph above it — which names
    staleInfo() to explain what it replaced — cannot satisfy the last
    assertion."""
    assert "const bkT=(m&&m.bookAsof!=null)?+new Date(m.bookAsof):NaN;" in PAGE
    stamp = PAGE.split("const bkT=(m&&m.bookAsof!=null)?+new Date(m.bookAsof):NaN;")[1].split("// LINEAR LENGTH, SPLIT BY SIDE")[0]
    assert "const bkStale=(Date.now()-bkT)>((window.SNDK_STALE_BOOK_MIN||6)*60000);" in stamp
    assert "const fr={stale:bkStale};" in stamp          # the only fr in scope
    assert "staleInfo(" not in stamp                     # never the scan clock
    assert "?'--coral':'--ink-faint'" in stamp           # coral on the book's age
    assert 'data-name="Book age"' in stamp


def test_the_freshness_chip_leads_with_the_oldest_layer(monkeypatch):
    """A chip that reports the freshest layer is reporting the layer that cannot
    be wrong. The lead line and the box colour both take the option book, which
    is the oldest thing on the board on any cached scan; bars is the fallback
    for a board carrying no book stamp yet, and either one going stale marks the box."""
    ceiling = _reader(monkeypatch).STALE_BOOK_MIN
    got = _fresh_tick([{"scan": 1, "book": 3, "scan_stale": False},
                       {"scan": 1, "book": ceiling + 1, "scan_stale": False},
                       {"scan": ceiling - 1, "book": ceiling - 0.5, "scan_stale": True},
                       {"scan": 2, "book": None, "scan_stale": False},
                       {"scan": None}])
    assert got[0]["lead"] == "book 3m"                  # the book's age, not the scan's
    assert [(g["box"], g["lead"].split(" ")[0]) for g in got] == [
        (False, "book"), (True, "book"), (True, "book"), (False, "bars"), (False, "awaiting")]


def test_the_stale_floor_is_the_readers_own_ceiling(monkeypatch):
    """08-30: the floor was a hardcoded 12 minutes, on the reasoning that a
    weekly-tenor scanner breathes slower than the SPX 0DTE loop. But the scan
    cadence is ~2 minutes, so 3*median is ~6 and the 12-minute floor was the
    binding constraint on every ordinary day — a scanner that died at 10:00 kept
    a live chip until 10:12.

    Six is not an arbitrary replacement for twelve. It is sndk_read's own
    STALE_BOOK_MIN, the age at which the reader itself stops spending a model
    call on the book, and PAYLOAD.render() lifts it off the payload's gate
    block — so the chip cannot quote a ceiling the reader has stopped enforcing.
    Until the tab has fetched the payload the page uses a number of its own, so
    every such fallback is held to the reader's constant, and staleInfo() is run
    on scans a minute apart (the floor binds), three minutes apart (three
    intervals bind) and with the payload's number in place.
    test_sndk_payload pins the same join from the server's end."""
    R = _reader(monkeypatch)
    assert set(re.findall(r"window\.SNDK_STALE_BOOK_MIN\|\|([\d.]+)", PAGE)) == {f"{R.STALE_BOOK_MIN:g}"}
    assert set(re.findall(r"window\.SNDK_BARS_STALE_MIN\|\|([\d.]+)", PAGE)) == {f"{R.BAR_RECORD_STALE_MIN:g}"}
    assert "if(d.gates&&d.gates.stale_book_min!=null) window.SNDK_STALE_BOOK_MIN=d.gates.stale_book_min;" in PAGE
    floor = R.STALE_BOOK_MIN
    got = _run_js("""
const window={}, ET='', clockIn=()=>'00:00:00', SNDK={};
SNDK.staleInfo=function(){%s};
const now=Date.now(), at=m=>new Date(now-m*60000).toISOString();
console.log(JSON.stringify(D.map(c=>{
  SNDK.rows=[3, 2, 1, 0].map(i=>({ts:at(c.age+i*c.every)}));
  SNDK.model=SNDK.rows[3];
  if(c.payload_min==null) delete window.SNDK_STALE_BOOK_MIN; else window.SNDK_STALE_BOOK_MIN=c.payload_min;
  return SNDK.staleInfo().stale;
})));""" % _method("staleInfo()"), [
        {"every": 1, "age": floor - 0.1}, {"every": 1, "age": floor + 0.1},
        {"every": 3, "age": 9 - 0.1}, {"every": 3, "age": 9 + 0.1},
        {"every": 1, "age": floor + 0.1, "payload_min": floor + 2}])
    assert got == [False, True, False, True, False]


def _provlines(scenes):
    """PAYLOAD.provLines, lifted out of the page and run on each scene."""
    return _run_js("function provLines(scene){%s}\nconsole.log(JSON.stringify(D.map(s=>provLines(s))));"
                   % _method("provLines(scene)"), scenes)


def test_the_payload_tab_says_the_clocks_before_the_json(tmp_path, monkeypatch):
    """sr-7 (08-30): the scene's provenance is all inside the JSON, but the JSON
    is ~3,000 characters and nobody discounts a number for an age they had to go
    looking for. Header lines answer it first — how old the quote is, how old the
    BOOK the structure came from is, and which night's open interest every
    number rests on — and a block the freshness gate deleted gets a coral line
    of its own, because an absence is the one thing a JSON dump cannot show you.

    Run on the payload the tab really fetches (the Strikes Payload, and the
    legacy scene beside it, which carries the cadence line) and on a null scene,
    where every field degrades to — or ? and no number is fabricated."""
    assert "+PAYLOAD.provLines(d.scene)" in PAGE      # rendered, not merely defined
    d = _payload(tmp_path, monkeypatch)
    scene, legacy = d["scene"], d["legacy"]["scene"]
    ds, book = scene["data_sources"], scene["data_sources"]["options_book"]
    # spot_feed became ALARM-ONLY (a153e39): its absence means the usual feed, never "spot from ?"
    usual = json.loads(json.dumps(scene))
    usual["data_sources"].pop("spot_feed", None)
    usual["data_sources"]["options_book"]["is_repeat_of_previous_scan"] = True
    usual["data_sources"]["open_interest"] = {"prior_session_date": "2026-08-18"}
    usual["freshness_rules"] = {"blocks_dropped_this_scan": [
        {"block": "strikes", "source": "options_book", "age_min": 12.0, "max_age_min": 6.0}]}
    once = json.loads(json.dumps(legacy))
    once["data_sources"]["scans_so_far_today"] = 1
    once["data_sources"]["options_book"]["distinct_books_so_far_today"] = 1
    strikes, usual_out, old, once_out, nothing = _provlines([scene, usual, legacy, once, None])

    head = strikes.splitlines()[0]
    assert f"scan {ds['scan_taken_at'][11:19]} ET" in head
    assert f"book {book['measured_at'][11:19]} ET ({book['age_min']:g}m old, {book['served_from']}" in head
    assert "REPEAT" not in head and "DROPPED" not in strikes
    table = scene["strikes"]
    assert f"// {table['strikes_in_window']} strikes in reach, {len(table['rows'])} rows listed" in strikes
    lines = usual_out.splitlines()
    assert "spot from schwab_quote" in lines[0] and "REPEAT of the previous scan" in lines[0]
    assert "struck at the 2026-08-18 close" in lines[1]
    dropped = [line for line in lines if "DROPPED before the model saw it: strikes" in line]
    assert len(dropped) == 1 and "coral" in dropped[0]
    # the cadence line: two counts, singular when a count is one (the 08-30 reshape)
    counts = legacy["data_sources"]
    assert (f"{counts['options_book']['distinct_books_so_far_today']} distinct books across "
            f"{counts['scans_so_far_today']} scans today") in old
    assert "1 distinct book across 1 scan today" in once_out
    assert "spot from ?" in nothing and not re.search(r"\d", nothing)


def test_every_provlines_helper_escapes(tmp_path, monkeypatch):
    """provLines builds innerHTML, and it once applied its escaper to ten fields
    and missed two: `t` (the clock) and `age` (the minutes) interpolated raw.
    Neither was reachable — both carry isoformat strings and rounded floats the
    local scanner produced, and `t` slices to eight characters besides — but
    reaching for an escaper and missing a path is exactly how the comment-form
    hole got into this page, so it was closed rather than argued about.

    Run on the real Strikes Payload and legacy scene with every string and number
    in them replaced by markup, the optional lines switched on, and a dropped
    block made of markup, so a helper added later that interpolates raw is a red
    test: the only tags that come back are the <span>s provLines writes itself."""
    d = _payload(tmp_path, monkeypatch)
    hostile = "<b>&" * 12        # every slice of it, a clock's eight characters included, opens a tag

    def poison(node):
        if isinstance(node, dict):
            return {k: poison(v) for k, v in node.items()}
        if isinstance(node, list):
            return [poison(v) for v in node]
        return node if node is None or isinstance(node, bool) else hostile

    scenes = [poison(d["scene"]), poison(d["legacy"]["scene"])]
    for scene in scenes:
        scene["data_sources"]["options_book"]["is_repeat_of_previous_scan"] = True
        scene["data_sources"]["open_interest"] = {"prior_session_date": hostile, "strikes_compared_today": hostile,
                                                  "measured_unchanged_so_far_today": True}
        scene["freshness_rules"] = {"blocks_dropped_this_scan": [
            dict.fromkeys(("block", "source", "age_min", "max_age_min"), hostile)]}
    for html in _provlines(scenes):
        assert "DROPPED" in html and "REPEAT" in html
        text = re.sub(r'<span class="p"(?: style="[^"]*")?>|</span>', "", html)
        assert "<" not in text and ">" not in text, html
        assert not re.search(r"&(?!amp;|lt;|gt;)", text), html


def test_the_read_pops_in_the_headers_air():
    """09-02: the latest reading also stands in the open as a translucent card in the header's
    one piece of measured air (below the mic card, out to the header's right edge, down to the
    voice column's bottom). Pinned: the node is a SIBLING of the repainted strips — never a child
    of #snk-vox, whose innerHTML paintDrawers rewrites — it is placed from live rects inside the
    header and re-placed when the chart resizes, and its age is re-ticked in place on the spot
    poll like the caption's. Run on a stand-in element: it pops only on a new reading_ts, so a
    quiet scan re-carrying the same sentence does not make it jump, and dismissal is remembered
    per reading, so a closed card stays closed until the model says something new."""
    assert '<div class="snk-vox" id="snk-vox"></div>\n        <div class="snk-pop" id="snk-pop" hidden></div>' in PAGE
    assert "SNDK.paintPop();" in PAGE                                 # from paintDrawers, every paintSide
    assert "SNDK.buildChart(); SNDK.placePop();" in PAGE              # re-placed on resize
    assert "getBoundingClientRect()" in _method("placePop()")         # placed from live rects...
    assert ".snk-head{position:relative;" in PAGE                     # ...measured inside the header
    assert "pop.dataset.since" in PAGE and "pop.classList.toggle('aged', pm>10)" in PAGE
    assert "function snkPopBox(" in PAGE                              # the builder the stand-in below stands in for
    assert "openPop(on){" in PAGE                                     # click = drawer-style overlay
    js = """
const marks=new Set(); let pops=0;
const el={hidden:true, innerHTML:'', offsetWidth:0,
  set className(v){ marks.clear(); v.split(' ').filter(Boolean).forEach(c=>marks.add(c)); },
  classList:{add(c){ if(c==='pop') pops++; marks.add(c); }, remove(c){ marks.delete(c); },
             toggle(c,on){ if(on) marks.add(c); else marks.delete(c); }, contains:c=>marks.has(c)},
  setAttribute(){}, removeAttribute(){}};
const document={getElementById:id=>id==='snk-pop'?el:null};
const kept={}, localStorage={getItem:k=>k in kept?kept[k]:null, setItem:(k,v)=>{ kept[k]=String(v); }};
const snkPopBox=rd=>rd?{html:rd.said, cls:'watch', ageM:1, since:rd.reading_ts}:null;
const SNDK={popTs:null, read:null, placePop(){}, paintPop(){%s}, dismissPop(){%s}};
console.log(JSON.stringify(D.map(step=>{
  if(step==='dismiss') SNDK.dismissPop(); else { SNDK.read=step; SNDK.paintPop(); }
  return {shown:!el.hidden, pops, said:el.innerHTML};
})));""" % (_method("paintPop()"), _method("dismissPop()"))
    got = _run_js(js, [
        {"ts": "10:00", "reading_ts": "A", "said": "first"},
        {"ts": "10:02", "reading_ts": "A", "said": "first"},     # a quiet scan carrying the same reading
        {"ts": "10:04", "reading_ts": "B", "said": "second"},
        "dismiss",
        {"ts": "10:06", "reading_ts": "B", "said": "second"},
        {"ts": "10:08", "reading_ts": "C", "said": "third"},
    ])
    assert [(g["shown"], g["pops"], g["said"]) for g in got] == [
        (True, 1, "first"), (True, 1, "first"), (True, 2, "second"),
        (False, 2, "second"), (False, 2, "second"), (True, 3, "third")]


def test_the_payload_tab_carries_the_schema_card():
    """09-02: a Schema button on the Payload tab opens one card drawing the whole SNDK-PRO
    pipeline — every file and what it does in plain words. The content is DATA so this test
    can pin the file names it cites against the tree, and the strip beside it no longer
    credits a vendor the SNDK chain never came from.

    09-04: redrawn. Six rows of identical boxes with one chevron between each pair said
    "everything above feeds everything below", which is not what happens. Components now
    declare a kind and every connection is its own line, so the data is _PL_NODES plus
    _PL_EDGES and this test pins that every edge endpoint actually resolves."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    assert 'id="pl-schema"' in PAGE and "function openPlSchemaModal(" in PAGE
    assert ">Pipeline Architecture</button>" in PAGE                 # renamed 09-02
    assert PAGE.index('id="pl-schema"') < PAGE.index('id="pl-seg"')    # ...and sits left of the segments
    # 09-04 redesign: the content is still DATA, now split into components and the
    # connections between them, so this test can pin both against the tree.
    assert "const _PL_NODES=[" in PAGE and "const _PL_EDGES=[" in PAGE
    assert ".modal-card.plsch{" in PAGE
    import re as _re
    _n0 = PAGE.index("const _PL_NODES=["); _n1 = PAGE.index("const _PL_EDGES=[")
    _nodes = _re.findall(r"^\s*\['([a-z0-9]+)',\s*'([^']+)',\s*'([a-z]+)'",
                         PAGE[_n0:_n1], _re.M)
    assert len(_nodes) >= 15, len(_nodes)
    # THE PROPERTY THE CARD NOW RESTS ON: kind and stage are one-to-one. That is
    # what let the legend go — the stage heading IS the definition, so a reader
    # does not learn a colour code before seeing anything to attach it to. If a
    # stage ever mixes kinds the heading starts lying and the legend has to
    # come back, so it is pinned here rather than left as an accident.
    _by_stage = {}
    for _id, _stage, _kind in _nodes:
        _by_stage.setdefault(_stage, set()).add(_kind)
    for _stage, _kinds in _by_stage.items():
        assert len(_kinds) == 1, (_stage, sorted(_kinds))
    # ...and every stage explains itself, in a line, where the reader meets it
    _d0 = PAGE.index("const _PL_STAGE_DOC={")
    _docs = set(_re.findall(r"'([A-Z][a-z]+)':", PAGE[_d0:PAGE.index("};", _d0)]))
    assert _docs == set(_by_stage), (sorted(_docs), sorted(_by_stage))
    # EVERY edge endpoint resolves to a component. A dangling id draws no line
    # and says nothing about it — the silent miss this build keeps hitting.
    _ids = {n[0] for n in _nodes}
    _edges = _re.findall(r"\['([a-z0-9]+)','([a-z0-9]+)'",
                         PAGE[_n1:PAGE.index("const _PL_STAGE_DOC={")])
    assert _edges, "no edges parsed"
    _flat = {x for pair in _edges for x in pair}
    assert _flat <= _ids, sorted(_flat - _ids)
    # the wiring shown in words is DERIVED from those same edges, never typed,
    # so the sentence and the line can never disagree
    assert "function _plWires(" in PAGE and "_PL_EDGES.filter(" in PAGE
    assert "Fed by" in PAGE and "Feeds" in PAGE
    # below this width the grid collapses, every card shares an x, and twenty
    # edges become one stroke — the words carry it there instead
    assert "const _PL_LINES_MIN_WIDTH=" in PAGE
    assert "window.innerWidth<_PL_LINES_MIN_WIDTH" in PAGE
    # Esc and the backdrop close the modal without touching the X, so the
    # resize listener and the observer have to come off in closeModal
    assert "function _plTeardown(" in PAGE
    assert PAGE.index("_plTeardown();") > PAGE.index("function closeModal(")  # called from it
    # the side packet is part of the picture, not a footnote to it
    assert "sndk_side.build_side()" in PAGE and "state/sndk_side" in PAGE
    for rel in ("skills/sndk-pro/sndk_hunter.py", "skills/sndk-pro/sndk_views.py",
                "skills/sndk-pro/sndk_bars.py", "skills/sndk-pro/sndk_read.py",
                "skills/sndk-pro/sndk_rag.py", "runtime/watch/intraday/sndk_deadman.py",
                "runtime/launchd/com.mirai-station.sndk-bars.plist"):
        assert (root / rel).exists(), rel
        assert rel.split("/")[-1].replace(".plist", "") in PAGE, rel
    assert "ThetaData supplies this week" not in PAGE       # the SNDK chain is the market-data server's
    assert "state/sndk_bars" in PAGE
    # 09-02 verification pass (three agents against the code): the packet is built every
    # scan, the guards are named
    assert "Every scan: the newest snapshot" in PAGE
    assert "forecast language is deleted before anyone sees it" in PAGE
    # ...the pager watches the scanner and the reader, memory is not the reading model's to search
    assert "the scanner goes quiet, or when the reader stops writing rows while the scanner still runs" in PAGE
    assert "the reading model has no tools and cannot reach it" in PAGE
    # 09-04: said in plain English now — the assertion is that the Diary card
    # states what a row actually holds, not that it uses the word "arrays"
    assert "eight numbers for every strike" in PAGE
    assert "ivb=n(g.iv_median_books,5)" in PAGE


def test_the_payload_tab_carries_the_diary_view():
    """09-02: a third segment shows the newest diary row beside the schema of that row —
    every key in plain words, checked live against the row (absent keys dim, arrays with
    their length, a dot on the keys the reader reads). The memory view stopped filing
    forecast-era slices as quiet: they are counted apart and drawn hatched."""
    assert 'data-view="diary"' in PAGE and 'id="pl-diary"' in PAGE
    assert "async diary(){" in PAGE and "paintDiary(day, total, row, sib){" in PAGE
    assert "const _DIARY_SCHEMA=[" in PAGE and "'gex_views.net_by_strike'" in PAGE
    assert "if(PAYLOAD.view!=='payload') return;" in PAGE          # the JSON pane is the payload view's
    assert "legacy(m){ return !!m && ('vector' in m) && !('quiet' in m); }" in PAGE
    assert "seg('l',lg)" in PAGE and ".mem-days .rhy i.l{" in PAGE
    assert "['Since last read', esc(m.frame_is)]" in PAGE


def test_sndk_pro_is_no_longer_labelled_beta():
    """09-02: the SNDK tab left beta. Nothing the page draws calls anything beta, in any
    wording or place: the one "beta" left in its code is the SPX hedge rail's spot-vol beta,
    a measured quantity, and `snk-beta` survives only as a class name."""
    code = _code_only(PAGE)
    labels = [code[max(0, m.start() - 20):m.end() + 8]
              for m in re.finditer(r"(?i)(?<![\w-])beta(?![\w-])", code)
              if not code[max(0, m.start() - 9):m.start()].endswith("spot-vol ")]
    assert labels == [], f"the page calls something beta: {labels}"


def test_the_rail_is_two_instrument_groups():
    """09-02: eight flat tabs read as one list and it was hard to tell SPX from SNDK. The rail
    is two groups with a header each; the group holding the active tab is always open, a
    header click opens the other and shows its last tab. Every tab still exists, inside its
    group."""
    assert 'data-grp="spx"' in PAGE and 'data-grp="sndk"' in PAGE
    assert PAGE.count('class="grp-hd"') == 2
    assert "function setRailGroup(" in PAGE and "function syncRailGroups(" in PAGE
    assert "setRailGroup(g.dataset.grp, !!on);" in PAGE          # an accordion: one group open, the active one
    assert "\\u203a" not in PAGE                            # the chevron is the character, not an escape
    for tab in ("map", "layers", "diary", "heads", "replay", "dict", "sndk", "payload"):
        assert f'data-tab="{tab}"' in PAGE, tab
    spx = PAGE.index('data-grp="spx"'); sndk = PAGE.index('data-grp="sndk"')
    assert spx < PAGE.index('data-tab="dict"') < sndk < PAGE.index('data-tab="payload"')
    assert "SPX·0DTE</span>" not in PAGE                 # the footer glyph no longer names one instrument


def test_the_station_opens_on_sndk_for_now():
    """09-02, TEMPORARY by the user's word: the page boots into the SNDK map. One constant
    carries it so restoring the SPX default is a one-word change."""
    assert "const DEFAULT_TAB='sndk';" in PAGE
    assert "if(DEFAULT_TAB!=='map') switchTab(DEFAULT_TAB);" in PAGE


def test_the_shading_baseline_is_the_opening_minute_not_the_first_scan():
    """08-22 shaded the day against S.pts[0] — this tab's first SCAN, which lands
    wherever the watcher's clock fell after the bell. On 09-04 that was 09:30:12 at
    1598.79 against a true 09:30 open of 1586.00, and the band between them was
    painted red on a day price never traded below its open. The baseline now comes
    off the bar sidecar, and the word "open" is spent only when the reading really
    sits on bar 0."""
    assert "async pullDay()" in PAGE
    assert "path=sndk_side/${day}.jsonl&limit=1" in PAGE
    assert "pick('px.session_open')" in PAGE
    assert "SNDK.pullDay();" in PAGE                        # pollPath drives it
    assert "_op.bar===0" in PAGE                            # ...and only bar 0 earns the word
    assert "const pOpen=openTrue?_op.p:S.pts[0].p;" in PAGE  # else the scan still stands
    assert "${openTrue?'open':'first scan'}" in PAGE         # and the caption says which
    assert "+x.bar_index===0" in PAGE                        # the cited-record fallback, bar 0 only


def test_the_session_extremes_chip_reads_the_bars_not_the_scans():
    """The chip reported the highest/lowest per-scan SPOT. A scan is a glance every
    ~2 minutes: on 09-04 it printed a $1,598.79 low while price traded $1,581.00 in
    the 09:31 minute, between two glances. It now folds in the sidecar's completed
    minutes the way sndk_read has since obs-5, so the chip can only be conservative,
    never inventive."""
    assert "pick('px.session_high')" in PAGE and "pick('px.session_low')" in PAGE
    assert "sHi=Math.max(sHi, _dp.high.p)" in PAGE
    assert "sLo=Math.min(sLo, _dp.low.p)" in PAGE
    # ONE binding, hoisted to the top of buildChart. The chip runs ~140 lines before the
    # shading baseline, so a `_dp` declared beside the baseline threw a ReferenceError in
    # the chip — caught by a headless render, not by a human looking at the page.
    assert PAGE.index("const _dp=(SNDK.dayPx&&S&&S.t0") < PAGE.index("sLo=Math.min(sLo, _dp.low.p)")
    # the extremes get NO cited-record fallback — a sampled bar is not the day's high
    body = PAGE.split("async pullDay()")[1].split("\n  },")[0]
    assert "bar_index===0" in body and body.count("bar_index") == 1, \
        "the bar-record fallback must stay scoped to the open"


def test_the_gamma_zero_axis_never_leaves_the_middle():
    """It used to pin itself to whichever edge a one-sided ladder left empty, so
    the bars could spend the whole lane. On 09-04 SNDK ran 1586 to 1740, carried
    the last negative strike out of the plotted window at 11:55, and the axis
    jumped to the left edge mid-session with every bar silently rescaled. The
    axis is the chart's fixed landmark; it stays in the middle."""
    assert "const xZ=x0+Math.round((lineEnd-x0)/2)" in PAGE   # const, not let — nothing reassigns it
    assert "xZ=lineEnd-6" not in PAGE                         # the old right pin
    assert "xZ=x0+6" not in PAGE                              # the old left pin
    for gone in ("hasPos", "hasNeg", "sideFloor"):
        assert gone not in PAGE, f"{gone} outlived the layout rule it existed for"


def test_the_payload_tab_carries_the_payload_dropdown_and_the_replay():
    """strikes-1 (09-05): the payload segment is a dropdown of the three payloads and is
    not a tab; the header says "sent" only over the document the model reads; copy()
    resolves the scene when the reader is reverted; the Pipeline Architecture modal embeds
    the replay page; the diary schema marks what became legacy."""
    assert 'id="pl-which"' in PAGE and 'value="strikes"' in PAGE and 'value="gate"' in PAGE
    # the legacy scene is the last option, under its own group label, in dimmer ink
    assert PAGE.index('value="strikes"') < PAGE.index('value="gate"') < PAGE.index('value="scene"')
    assert '<optgroup label="Legacy" class="legacy"><option value="scene" class="legacy">' in PAGE
    assert "which.classList.toggle('legacy', which.value==='scene')" in PAGE
    assert '<span role="tablist" aria-label="Side, Memory or Diary">' in PAGE
    assert "if(b.tagName==='BUTTON') b.setAttribute('aria-selected'" in PAGE
    assert "which.addEventListener('pointerdown'" in PAGE and "which.addEventListener('focus'" not in PAGE
    assert "+(doc===d.scene?`<span class=\"p\">// user message" in PAGE
    assert "(d.legacy&&d.legacy.scene)||(d.payload==='scene'?d.scene:null)" in PAGE
    assert 'src="/pipeline.html?user=${encodeURIComponent(PAYLOAD.USER)}&embed=1"' in PAGE
    assert "e.data.mirai==='close-modal'" in PAGE
    assert "Legacy payload only" in PAGE and "'gex_views.oi_side_by_strike_next'" in PAGE
    assert "sndk_board.py" in PAGE and "sndk_regions.py" in PAGE
    from pathlib import Path
    pipe = Path(__file__).resolve().parents[1] / "static" / "pipeline.html"
    assert pipe.exists()
    # the regions rule left the prompt on 2026-09-05: the diagram routes its answer to the
    # Gate Payload, never to the observer or the Strikes Payload, and says so
    P = pipe.read_text()
    assert "e_memo_shadow" in P and "e_memo_obs" not in P
    assert "not shipped since 2026-09-05" in P and "S.memo.dest = 'model'" in P
    assert "regions rule behind the scene, kept in the Gate Payload" in PAGE


def test_the_sndk_chart_claims_nothing_about_what_dealers_do():
    """snkArrows put one triangle on the strongest cluster with a card reading
    "Dealers buy the dips here — it holds price up" and its three siblings,
    from an ASSUMED gamma sign. The reader's doctrine forbids the model that
    claim (never say what dealers are doing, never say hedging damps or
    amplifies) and docs/sndk-plan.md records it as measured absent on SNDK.
    Removed 2026-09-10 with the phone's copies of the same four sentences.

    Held as the claim, not as those four sentences: no code from the SNDK
    chart's first function on says dealers buy, sell, hedge, damp, amplify,
    cushion, defend, push, pull or absorb, under any wording. The SPX map's
    MM-action arrows in gxPaint sit above that line, in a different book, and
    are not covered here."""
    sndk = _code_only(PAGE[PAGE.index("function snk"):])
    claim = re.compile(r"(?i)\bdealers?(?:[\s-]+(?:must|will|would|then|here))*[\s-]+"
                       r"(?:buy|sell|hedg|damp|amplif|cushion|defend|push|pull|absorb|action)")
    said = [sndk[max(0, m.start() - 30):m.end() + 20] for m in claim.finditer(sndk)]
    assert said == [], f"the SNDK chart says what dealers do: {said}"
    assert "snkArrows" not in sndk and 'id="snk-arrows"' not in sndk, \
        "the dealer-action arrows are back on the SNDK chart"


def test_the_sndk_chart_says_where_the_weight_is_not_what_price_does():
    """Reworded 2026-09-10, after the dealer arrows went: every caption, card,
    chip and key entry on the SNDK chart that said what price or dealers would
    do now says where the option weight sits. Each string below was on the
    chart; none may come back. The SPX map's own vocabulary is not covered, so
    the scan starts at the SNDK chart's code (its key is the first SNDK block
    that says any of these) and reads code only — the comments quote the
    retired wording on purpose, in both comment styles."""
    import re as _re
    sndk = PAGE[PAGE.index("function snkLegend(){"):]
    sndk = _re.sub(r"(?s)/\*.*?\*/", "", sndk)
    code = "\n".join(l.split("//")[0] for l in sndk.splitlines()
                     if not l.strip().startswith("//"))
    for gone in ("'Moves amplify'", "'Moves dampen'", "Dealers amplify moves",
                 "Dealers cushion moves", "Jumpy — moves amplify", "Calm — moves dampen",
                 "'Pull → '", "txt:'calmer ↑'", "txt:'faster ↓'", "txt:'resistance'",
                 "txt:'support'", "txt:'thin · fast'", "txt:'price magnet'",
                 "The price the market is being pulled toward.", "The book is tugging price",
                 "fakeouts live in here", "little holds price", "price travels fast",
                 "Calmer above it, faster below it.", "where price is pulled",
                 "above: dealers dampen", "often settles late", "stalls a rally",
                 "cushions a drop", "Below · faster", "Above · calmer",
                 "decay tilt", "a place the pull points to", "a place the pull eases toward",
                 "hedging pressure eases toward", "the comet is gravity"):
        assert gone not in code, f"retired wording is back on the SNDK chart: {gone}"
    # and what replaced the two most-read pieces is there
    assert "txt:'call side ↑'" in code and "txt:'put side ↓'" in code
    assert "'Gamma here'" in code


def test_the_minute_price_log_has_a_stale_alarm_and_its_poller():
    """09-14: the minute price log feeds the board's day high and low, its price
    ranges and every breakout, and a stalled log was silent everywhere — its job
    writes health.json "so a watcher can see it breathing" and nothing watched.
    Pinned as strings, like every contract in this file."""
    assert "async pollBars(){" in PAGE
    assert "path=sndk_bars/health.json" in PAGE                 # the error text
    assert "path=sndk_bars/${day}.jsonl&limit=1" in PAGE        # the record the board reads
    assert "SNDK.pollRead(); SNDK.pollTape(); SNDK.pollBars();" in PAGE   # resume starts it
    assert "clearTimeout(SNDK.timers.bars);" in PAGE            # hide stops it
    assert "MINUTE LOG STALE" in PAGE
    assert "feedPill+SNDK.barsPill()+srcPill" in PAGE           # on the strip beside FEED LOST
    assert "window.SNDK_BARS_STALE_MIN=d.gates.bars_stale_min" in PAGE   # the reader's number
