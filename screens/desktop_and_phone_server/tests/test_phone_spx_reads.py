"""The SPX reads page (static/m/reads.html), Will's design D of 2026-10-09 drawn to his board Final-Reads of 2026-10-10:
every graded 30-minute call a day at a time, the newest day open with its price line, the others folded to their score
and one square per call. The page's own functions run in node against the stand-in DOM, as the forecast page's do, on
rows copied from state/spx_jev/integral_grades.jsonl."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from node_run import run_node
from test_phone_jev_spx import FAKE_DOM, FIXED_NOW, _flat_text

M = Path(__file__).resolve().parents[1] / "static" / "m"
READS = (M / "reads.html").read_text()
JS = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", READS))
_NODE = shutil.which("node")
LA, TOKYO = "America/Los_Angeles", "Asia/Tokyo"


def _fn(name):
    m = re.search(r"\n  function %s\(.*?(?=\n  (?:function |var |//|[a-z]))" % re.escape(name), JS, re.S)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _var(name):
    m = re.search(r"\n  var %s = .*?;" % re.escape(name), JS)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _run(js, data=None, tz=LA):
    """The page's builders in node at D.now (the real clock without one), the bars of any day answered from D.bars and
    each day asked for noted in ``asked`` (a test of the page's own barsOf declares it again after this stand-in)."""
    if not _NODE:
        pytest.skip("node is not installed")
    text = ("document.createTextNode = function(t){ var n = new Node('#text'); n._t = String(t); return n; };"
            # a text's box as Chrome measures the page's 11px words: 6.03 a letter (its figures are tabular), 11 over the
            # baseline and 13.42 tall; a word with no room is taken out again
            "Node.prototype.getBBox = function(){ return {x: 0, y: -11, width: this.textContent.length * 6.03, height: 13.42}; };"
            "Node.prototype.removeChild = function(n){ this.kids.splice(this.kids.indexOf(n), 1); return n; };")
    fns = "".join(_fn(f) for f in ("svgEl", "viewerTime", "marketAt", "money", "signed", "cap", "callDays", "isRight", "score", "rightOf", "share",
                                   "dayName", "headLine", "strip", "squares", "dayLine", "callRow", "dayCard", "summary", "anyOpen"))
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FIXED_NOW + FAKE_DOM + text + _var("DAYS_SUMMED") + _var("STRIP_W")
              + _var("LINE_W") + _var("NS") + "var VIEWER_FMT = {}, opened = D.opened || {}, days = D.days || null, lines = {}, asked = [];"
              "function barsOf(d){ asked.push(d.day); return Promise.resolve(D.bars || null); }" + fns + js)
    out = run_node(_NODE, script, input=json.dumps(data), capture_output=True, text=True, timeout=20,
                   env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def grade(ts, horizon="next_30", **over):
    """A row as grade.py writes it (state/spx_jev/integral_grades.jsonl), trimmed to what the page reads."""
    row = {"row_ts": ts, "horizon": horizon, "graded": True, "from": 7750.0, "edge": 2.2, "g": 0.4,
           "best": {"points": 1.5, "minute": 9}, "worst": {"points": -1.1, "minute": 21}, "label": "flat", "pick": "flat",
           "verdict": "right"}
    return {**row, **over}


def bar(hhmm, close, day="2026-10-09"):
    """A one-minute SPX bar as the bars job writes it (state/spx_jev/bars/<day>.jsonl), trimmed to what the line reads."""
    return {"ts": f"{day}T{hhmm}:00-04:00", "close": close}


# the owner's table of 2026-10-09, its 10-08 15:00 and 14:31 rows, as the store holds them
OCT8_1500 = grade("2026-10-08T15:00:31.298177-04:00", **{"from": 7754.82, "edge": 2.24, "g": 2.75, "best": {"points": 11.05, "minute": 27},
                                                         "worst": {"points": -2.27, "minute": 8}, "label": "up", "verdict": "wrong"})
OCT8_1431 = grade("2026-10-08T14:31:03.112000-04:00", **{"from": 7756.79, "edge": 2.24, "g": -0.48, "best": {"points": 2.01, "minute": 3},
                                                         "worst": {"points": -3.6, "minute": 17}})
# the board's day as five of its bars: the open, the low (09:48, 7,779.34), midday, the high (14:27, 7,814.33), the last
BOARD_BARS = [bar("09:30", 7790.0), bar("09:48", 7779.34), bar("12:00", 7800.0), bar("14:27", 7814.33), bar("15:59", 7811.0)]


def test_the_calls_are_the_30_minute_windows_by_market_day_newest_day_first_each_days_calls_earliest_first():
    """Will, 2026-10-09: the days newest first, and in each day the calls from earlier to later; a read graded twice
    keeps its last row."""
    rows = [grade("2026-10-07T15:29:00-04:00"), grade("2026-10-08T09:30:00-04:00", verdict="wrong"),
            grade("2026-10-08T09:30:00-04:00", horizon="next_60"),                   # the end-price hour is not a 30-minute call
            OCT8_1431, OCT8_1500, grade("2026-10-08T09:30:00-04:00", verdict="right"),   # graded again: the last row stands
            grade("2026-10-08T10:01:00-04:00", graded=False)]                        # not graded yet: left out
    got = _run("console.log(JSON.stringify(callDays(D.rows).map(function(d){ return [d.day, d.calls.map(function(c){"
               " return c.row_ts.slice(11, 16) + ' ' + c.verdict; }), rightOf(d.calls), dayName(d.day)]; })));", {"rows": rows})
    assert got == [["2026-10-08", ["09:30 right", "14:31 right", "15:00 wrong"], "2 of 3 right", "Thu 10-08"],
                   ["2026-10-07", ["15:29 right"], "1 of 1 right", "Wed 10-07"]]


def test_the_header_names_the_newest_day_and_says_when_it_is_done():
    """Final-Reads (2026-10-10): the header's right side is the newest day with graded calls, as the forecast page names
    its card's day (the market day, never the viewer's), and "day done" once that day's close has passed on the stamp's
    own offset; through the session the day stands alone."""
    d = {"day": "2026-10-09", "calls": [grade("2026-10-09T10:02:31-04:00"), grade("2026-10-09T15:31:56-04:00")]}
    js = "console.log(JSON.stringify(headLine(D.d)));"
    assert _run(js, {"d": d, "now": "2026-10-09T15:59:00-04:00"}) == "Fri 10-09"
    assert _run(js, {"d": d, "now": "2026-10-09T16:00:00-04:00"}) == "Fri 10-09 · day done"
    assert _run(js, {"d": d, "now": "2026-10-10T20:00:00-04:00"}, TOKYO) == "Fri 10-09 · day done"    # Sunday in Tokyo, Friday's day
    assert "$('hdR').textContent = days.length ? headLine(days[0]) : '';" in _fn("draw")
    assert '<div class="hd-1"><span class="hd-l">SPX &middot; Reads</span><span class="hd-r" id="hdR"></span></div>' in READS
    assert ".hd-r{margin-left:auto;letter-spacing:.12em;color:var(--i-mute);white-space:nowrap;font-variant-numeric:tabular-nums}" in READS
    assert "flex-wrap:wrap" in re.search(r"\n\.hd-1\{([^}]*)\}", READS).group(1)      # at 320 the day wraps under the name, whole


def test_a_call_row_says_the_time_where_it_went_what_was_called_and_the_grade_and_opens_to_the_owners_numbers():
    """10-08 15:00 on the owner's table: price 7,754.82, flat ±2.2, low −2.3, average +2.8, high +11.1, went up, called
    flat, wrong. The time is the viewer's (12:00 in Los Angeles); the numbers wait behind a tap."""
    got = _run("var p = callRow(D.c); console.log(JSON.stringify([dump(p[0]), dump(p[1]), p[1].hidden]));", {"c": OCT8_1500})
    row, nums, hidden = got
    kids = row["kids"]
    assert [k["attrs"].get("class") for k in kids] == ["t", "w", "strip", "grade"] and kids[2]["tag"] == "svg"
    assert _flat_text(kids[0]) == "12:00" and _flat_text(kids[1]) == "Upcalled flat" and kids[1]["kids"][0]["attrs"]["class"] == "up"
    assert _flat_text(kids[3]) == "Wrong" and row["attrs"]["aria-expanded"] == "false"
    assert _flat_text(nums) == "Low −2.3 · average +2.8 · high +11.1 ptsCalled at 7,754.82 · flat within ±2.2"
    assert hidden is True
    right = _run("console.log(JSON.stringify(dump(callRow(D.c)[0].kids[3])));", {"c": OCT8_1431})
    assert right["attrs"]["class"] == "grade ok" and right["text"] == "Right"


def test_the_picture_puts_the_window_against_the_call_on_one_scale_and_rests_a_big_move_at_the_end():
    """On a 104-wide strip, 32 points either side: the call's tick in the middle, the flat band a box, low to high a
    line, the graded average a dot in where-it-went's colour; a move past 32 points rests at the end. Every ink on it is
    the page's own token through a class (Final-Reads, 2026-10-10): the band the flat colour the odds and the chart use,
    never a colour written into the script."""
    got = _run("console.log(JSON.stringify([dump(strip(D.a)), dump(strip(D.b))]));",
               {"a": OCT8_1500, "b": grade("2026-10-08T12:31:00-04:00", g=-40.0, worst={"points": -55.0}, best={"points": 2.8}, label="down")})
    a, b = got
    line, box, whisker, tick, dot = a["kids"]
    assert a["attrs"]["class"] == "strip" and [k["attrs"]["class"] for k in a["kids"]] == ["ax", "band", "span", "at", "dot up"]
    per = (52 - 5) / 32
    assert float(box["attrs"]["x"]) == pytest.approx(52 - 2.24 * per, abs=0.06) and float(box["attrs"]["width"]) == pytest.approx(2 * 2.24 * per, abs=0.06)
    assert float(whisker["attrs"]["x1"]) == pytest.approx(52 - 2.27 * per, abs=0.06) and float(whisker["attrs"]["x2"]) == pytest.approx(52 + 11.05 * per, abs=0.06)
    assert tick["attrs"]["x1"] == "52" and float(dot["attrs"]["cx"]) == pytest.approx(52 + 2.75 * per, abs=0.06)
    assert a["attrs"]["aria-label"] == "Low −2.3, average +2.8, high +11.1 points; flat within 2.2"
    assert b["kids"][2]["attrs"]["x1"] == "4.0" and b["kids"][4]["attrs"]["cx"] == "4.0" and b["kids"][4]["attrs"]["class"] == "dot down"
    assert _run("console.log(JSON.stringify(strip(D.c).kids[4].attrs['class']));", {"c": OCT8_1431}) == "dot"
    assert "#" not in _fn("strip") and "--o-flat-zone" not in READS
    for rule in (".strip .band{fill:var(--o-fl)}", ".strip .ax{stroke:var(--s2);stroke-width:1}", ".strip .span{stroke:var(--i-mute);",
                 ".strip .at{stroke:var(--i);stroke-width:1.2}", ".strip .dot{fill:var(--i);stroke:var(--s);stroke-width:1.5}",
                 ".strip .dot.up{fill:var(--o-up)} .strip .dot.down{fill:var(--o-dn)}"):
        assert rule in READS, rule


def test_a_day_folds_to_its_score_and_squares_oldest_first_and_opens_to_its_calls():
    d = {"day": "2026-10-08", "calls": [grade("2026-10-08T09:30:00-04:00", verdict="wrong", label="up"), OCT8_1431, OCT8_1500]}
    got = _run("var s = dayCard(D.d, D.open); console.log(JSON.stringify([dump(s), s.kids[2].hidden]));", {"d": d, "open": False})
    card, hidden = got
    head, sq, calls = card["kids"]
    assert _flat_text(head) == "Thu 10-081 of 3 right" and head["attrs"]["aria-expanded"] == "false" and hidden is True
    assert [k["attrs"].get("class") for k in sq["kids"]] == [None, "ok", None]       # 09:30 wrong, 14:31 right, 15:00 wrong
    assert sq["attrs"]["aria-label"] == "1 of 3 right, oldest first"
    assert len(calls["kids"]) == 6 and [k["attrs"]["class"] for k in calls["kids"][::2]] == ["call"] * 3
    opened = _run("var s = dayCard(D.d, true); console.log(JSON.stringify([s.kids[0].attrs['aria-expanded'], s.kids[2].hidden]));",
                  {"d": d, "open": True})
    assert opened == ["true", False]


def test_an_open_day_draws_its_price_with_a_dot_at_each_call_and_the_wrong_ones_ringed():
    """Final-Reads (2026-10-10): over an open day's calls the day's closes from the open to the close (the feet, in the
    viewer's zone) with the low on the axis, a dot at each call's read minute (its bar's close, or the last bar's before
    it), a wrong call's ringed and named where its words, measured as drawn, keep off the line, the dots and the words
    placed before (reads-1): to its right, or to its left near the close. The line is laid out at the card's measured
    width (300 on the owner's 360 phone, 260 on a 320 one), as the forecast page's chart is, so its words keep 11px at
    every width, and goes into the card over its calls before its words are placed. The bars are asked for the first
    time a day is open, once per card; a folded day never asks, and a day with under two bars draws no line."""
    d = {"day": "2026-10-09", "calls": [grade("2026-10-09T10:02:31-04:00"), grade("2026-10-09T12:31:03-04:00", verdict="wrong", label="up"),
                                        grade("2026-10-09T14:30:40-04:00", verdict="wrong", label="down")]}
    card = "var card = el('section', 'card'), list = card.appendChild(el('div', 'calls'));"
    svg, none = _run(card + "console.log(JSON.stringify([dump(dayLine(D.d, D.bars, 300, list)), dayLine(D.d, D.bars.slice(0, 1), 300, list)]));", {"d": d, "bars": BOARD_BARS})
    assert none is None
    assert svg["attrs"]["class"] == "price" and svg["attrs"]["viewBox"] == "0 0 300 56" and svg["attrs"]["role"] == "img"
    assert svg["attrs"]["aria-label"] == "The day’s price from the 06:30 open to the 13:00 close, a dot at each of the 3 calls, the 2 wrong calls ringed: 09:31, 11:30"
    kids = svg["kids"]
    assert (kids[0]["tag"], kids[0]["attrs"]["class"], kids[0]["attrs"]["y1"], kids[0]["attrs"]["x2"]) == ("line", "ax", "42", "298")
    pts = kids[1]["attrs"]["points"].split(" ")
    assert kids[1]["tag"] == "polyline" and pts == ["2.0,31.6", "15.7,42.0", "115.8,21.9", "227.4,8.0", "297.2,11.2"]
    dots = [(k["attrs"]["class"], k["attrs"]["cx"], k["attrs"]["cy"], k["attrs"]["r"]) for k in kids if k["tag"] == "circle"]
    assert dots == [("dot", "15.7", "42.0", "2.2"), ("dot wrong", "115.8", "21.9", "4.2"), ("dot wrong", "227.4", "8.0", "4.2")]
    texts = [(k["text"], k["attrs"].get("x"), k["attrs"].get("y"), k["attrs"].get("text-anchor"), k["attrs"].get("class")) for k in kids if k["tag"] == "text"]
    # each name's x is where its measured words start: the 11:30 call's 66.3 end 8 left of its ring at 227.4
    assert texts == [("OPEN 06:30", "2", "54", None, "foot"), ("CLOSE 13:00", "298", "54", "end", "foot"),
                     ("09:31 wrong", "123.8", "31.9", None, None), ("11:30 wrong", "153.1", "18.0", None, None)]
    tokyo = _run(card + "console.log(JSON.stringify(dayLine(D.d, D.bars, 300, list).kids.filter(function(k){ return k.tag === 'text'; }).map(function(k){ return k.textContent; })));",
                 {"d": d, "bars": BOARD_BARS}, TOKYO)
    assert tokyo == ["OPEN 22:30", "CLOSE 05:00", "01:31 wrong", "03:30 wrong"]
    # at 260 the line ends at 258 and the 11:30 call's words, which no longer fit to its right, sit to its left
    narrow = _run(card + "var s = dayLine(D.d, D.bars, 260, list); console.log(JSON.stringify([s.attrs.viewBox, s.kids[0].attrs.x2, s.kids[1].attrs.points.split(' ').pop(),"
                  " s.kids.filter(function(k){ return k.tag === 'text'; }).map(function(k){ return [k.textContent, k.attrs.x]; }), card.kids.map(function(k){ return k.tag; })]));",
                  {"d": d, "bars": BOARD_BARS})
    assert narrow == ["0 0 260 56", "258", "257.3,11.2", [["OPEN 06:30", "2"], ["CLOSE 13:00", "258"], ["09:31 wrong", "108.5"], ["11:30 wrong", "122.6"]], ["svg", "div"]]
    # a folded day asks for nothing; an open one asks once, at the head's width (the card's, which has no sides of its own,
    # 300 for a card not laid out) and puts the line in over its calls when it comes
    got = _run("var a = dayCard(D.d, false), b = dayCard(D.d, true); var askedNow = asked.slice();"
               "setImmediate(function(){ console.log(JSON.stringify([askedNow, asked, a.kids.map(function(k){ return k.tag + '.' + (k.attrs['class'] || ''); }),"
               " b.kids.map(function(k){ return k.tag + '.' + (k.attrs['class'] || ''); }), b.kids[2].attrs.viewBox])); });", {"d": d, "bars": BOARD_BARS})
    assert got == [["2026-10-09"], ["2026-10-09"], ["button.dh", "div.sq", "div.calls"], ["button.dh", "div.sq", "svg.price", "div.calls"], "0 0 300 56"]
    assert "dayLine(d, rows, head.clientWidth || LINE_W, list)" in _fn("dayCard") and re.search(r"\n\.dh\{[^}]*;margin:-12px 0 0;padding:12px 0 0;border:0;", READS)
    bare = _run("var b = dayCard(D.d, true); setImmediate(function(){ console.log(JSON.stringify(b.kids.length)); });", {"d": d, "bars": None})
    assert bare == 3
    assert _var("BARS_URL").strip() == "var BARS_URL = '/api/store/file?name=SPX_PRICE_BARS_BY_MINUTE&day=';"
    assert "fetch(BARS_URL + day + '&limit=420'" in _fn("barsOf")
    assert ".price{display:block;width:100%;height:auto;margin-top:8px;font-family:var(--sans)}" in READS and ".price text{font-size:11px;" in READS


def test_a_running_days_bars_are_asked_for_on_every_draw_and_a_done_days_once():
    """A page left open through the session (the MacBook's tab) draws the open day again on every new grade: while the
    day runs its bars are asked for again each time, so the line grows and each new call's dot lands on its own minute;
    once the close's last bar has landed (16:05, where the forecast page's bars hours end) the day's bars are asked for
    once and kept. A fetch that failed is never kept."""
    d = {"day": "2026-10-09", "calls": [grade("2026-10-09T10:02:31-04:00")]}
    js = ("var fetched = 0, fails = D.fails || 0; function fetch(){ fetched++; return fails-- > 0 ? Promise.reject(new Error('down')) :"
          " Promise.resolve({ok: true, json: function(){ return Promise.resolve({kind: 'jsonl', rows: D.bars}); }}); }"
          + _var("BARS_URL") + _var("barsByDay") + _fn("barsOf")
          + "function tick(){ return new Promise(function(r){ setImmediate(r); }); }"
          "(async function(){ var out = []; for(var i = 0; i < 3; i++){ var c = dayCard(D.d, true); await tick(); await tick(); out.push([fetched, c.kids.length]); }"
          " console.log(JSON.stringify(out)); })();")
    assert _run(js, {"d": d, "bars": BOARD_BARS, "now": "2026-10-09T16:04:59-04:00"}) == [[1, 4], [2, 4], [3, 4]]
    assert _run(js, {"d": d, "bars": BOARD_BARS, "now": "2026-10-09T16:05:00-04:00"}) == [[1, 4], [1, 4], [1, 4]]
    assert _run(js, {"d": d, "bars": BOARD_BARS, "now": "2026-10-10T09:00:00-04:00", "fails": 1}) == [[1, 3], [2, 4], [2, 4]]
    # a station that answered but not with the rows (an error status, a file of another kind) is no more kept than a
    # fetch that failed: only a done day's rows are
    refused = js.replace("fails-- > 0 ? Promise.reject(new Error('down')) :", "fails-- > 0 ? Promise.resolve({ok: false}) :")
    assert _run(refused, {"d": d, "bars": BOARD_BARS, "now": "2026-10-10T09:00:00-04:00", "fails": 1}) == [[1, 3], [2, 4], [2, 4]]
    assert _run(js, {"d": d, "bars": None, "now": "2026-10-10T09:00:00-04:00"}) == [[1, 3], [2, 3], [3, 3]]
    assert "done = Date.now() >= marketAt(d.calls[0].row_ts, '16:05')" in _fn("barsOf") and "if(done) barsByDay[day] = bars;" in _fn("barsOf")
    assert ".then(function(rows){ if(!rows) delete barsByDay[day]; return rows; });" in _fn("barsOf")


def test_the_pinned_summary_counts_the_last_five_days_beside_one_control_for_every_day():
    """The summary rides the sticky header so it is always in view: the score with its share (Final-Reads: "26 of 59
    right · 44%"), its button collapses every day and says Open all once every day is folded (Will, 2026-10-09), and
    under them the five days oldest on the left, each its right calls as a bar and its score, the newest in ink."""
    days = [{"day": f"2026-10-0{n}", "calls": [grade(f"2026-10-0{n}T10:00:00-04:00", verdict=v) for v in vs]}
            for n, vs in ((9, ["right", "wrong"]), (8, ["wrong"]), (7, ["right"]), (6, ["right"]), (5, ["wrong"]), (2, ["right"]))]
    js = ("var box = el('section'); summary(box, D.days);"
          "console.log(JSON.stringify([dump(box), box.hidden, box.kids[2].kids.map(function(c){ return c.kids[1].kids[0].style.width; })]));")
    some, shown, widths = _run(js, {"days": days, "opened": {"2026-10-09": True}})
    left, btn, byday = some["kids"]
    assert _flat_text(left) == "Last 5 days3 of 6 right · 50%" and shown is False          # 10-02 is the sixth day: left out
    assert left["kids"][1]["attrs"]["class"] == "sum-v" and left["kids"][1]["kids"][0]["tag"] == "span"
    assert btn["tag"] == "button" and btn["attrs"]["class"] == "all" and _flat_text(btn) == "Collapse all"
    assert byday["attrs"] == {"class": "byday", "role": "img", "aria-label": "By day: Mon 0 of 1, Tue 1 of 1, Wed 1 of 1, Thu 0 of 1, Fri 1 of 2"}
    assert [(_flat_text(c), c["attrs"].get("class")) for c in byday["kids"]] == [("Mon0 of 1", None), ("Tue1 of 1", None), ("Wed1 of 1", None), ("Thu0 of 1", None), ("Fri1 of 2", "now")]
    assert [[k["tag"] + "." + k["attrs"].get("class", "") for k in c["kids"]] for c in byday["kids"]] == [["b.", "i.bar", "span."]] * 5
    assert widths == ["0%", "100%", "100%", "0%", "50%"]
    none, _, _ = _run(js, {"days": days, "opened": {d["day"]: False for d in days}})
    assert _flat_text(none["kids"][1]) == "Open all"
    assert '<section class="card sum" id="sum" hidden></section>\n</header>' in READS     # inside the sticky header, after the tabs
    assert ".hd{position:sticky;top:0;" in READS and "main{display:flex;flex-direction:column;gap:6px}" in READS
    assert ".byday .now b,.byday .now span{color:var(--i)}" in READS and ".byday .bar i{display:block;height:5px;border-radius:3px;background:var(--fill)}" in READS


def test_the_page_reads_the_grades_through_the_raw_route_and_keeps_an_opened_call_open():
    assert _var("GRADES_URL").strip() == "var GRADES_URL = '/api/store/file?name=JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON&lane=live&limit=400';"
    assert _var("DAYS_SUMMED").strip() == "var DAYS_SUMMED = 5, SCALE_PTS = 32, POLL_MS = 60000;"
    assert "if(key === drawn && !failed){ days.forEach(function(d){ if(opened[d.day]) lines[d.day](); }); return; }" in _fn("poll")   # the same list leaves the page alone
    assert "if(!(d.day in opened)) opened[d.day] = i === 0;" in _fn("draw")           # the newest day opens, a hand choice stays
    assert "days.forEach(function(d){ opened[d.day] = open; }); draw();" in _fn("summary")
    assert "Fetch failed, showing the last list" in _fn("draw")


def test_the_tab_bar_lights_reads_and_every_page_links_it():
    """Two tabs since the redesign (2026-10-10): Forecast, the page at /m/, and Reads, lit here."""
    nav = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', READS).group(1)
    assert re.findall(r'<a class="tab" href="([^"]+)"', nav) == ["/m/"]
    assert nav.count('<span class="tab on" data-press>') == 1 and "<s>READS</s>" in nav and "<s>FORECAST</s>" in nav
    assert "NOW" not in nav and "jev.html" not in nav
    tabs = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', (M / "jev-spx.html").read_text()).group(1)
    assert 'href="/m/reads.html"' in tabs and "thread.html" not in tabs


def test_the_page_fits_the_owners_360px_phone_and_keeps_the_house_rules():
    """At 360 a call row's four columns fit the card: the time, the words (\"Down\" over \"called down\", about 70px
    at 11.5px), the 104-wide picture and the grade chip, with 8px between; a row and the summary's control are the
    page's 48px tap minimum, seen whole (Final-Reads), not a smaller box with a hidden reach. No viewport unit, no
    innerHTML, and no class the stylesheet names that nothing writes."""
    card = 360 - 2 * 16 - 2 * 14
    cols = re.search(r"\.call\{display:grid;grid-template-columns:minmax\(40px,max-content\) minmax\(0,1fr\) 104px 52px;gap:8px;align-items:center;width:100%;min-height:var\(--tap-min\);", READS)
    assert cols and card - (40 + 104 + 52 + 3 * 8) >= 75
    assert re.search(r"\n\.all\{flex:none;min-height:var\(--tap-min\);", READS) and ".all::after" not in READS
    assert "cursor:pointer" not in READS and not re.search(r"[\U0001F300-\U0001FAFF]", READS)
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", READS)))
    assert not re.search(r"\d(?:vh|dvh|svh|lvh|vw)\b", css)
    assert ".innerHTML" not in JS
    # press.js writes is-pressed; up, down and flat are a call's label, written from the row; the font's file is no class
    styled = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"url\([^)]*\)", "", css))) - {"is-pressed", "up", "down", "flat"}
    written = {c for cls in re.findall(r'class="([^"]+)"', READS.split("<body>", 1)[1]) for c in cls.split()}
    written |= {c for word in re.findall(r"'([a-z][a-z0-9 -]*)'", JS) for c in word.split()}
    assert styled <= written, styled - written
