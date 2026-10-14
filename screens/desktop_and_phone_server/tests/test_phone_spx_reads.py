"""The SPX results page (static/m/reads.html), Design B of 2026-10-14 (Will's design D of 2026-10-09 before it, at the
same address): every read the forecast page shows, graded, a day at a time, one lane per read on the day's axis, the
picked lane's calls listed under the lanes and its last five days under the list. The page's own functions run in
node against the stand-in DOM, as the forecast page's do, on rows copied from the station's grades files."""
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
    m = re.search(r"\n  var %s = .*?;" % re.escape(name), JS, re.S)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _run(js, data=None, tz=LA):
    """The page's builders in node at D.now (the real clock without one), with the grades files of D.grades (live, tape,
    premarket), the 30-minute card D.live and the day files of D.perDay."""
    if not _NODE:
        pytest.skip("node is not installed")
    fns = "".join(_fn(f) for f in ("svgEl", "viewerTime", "marketAt", "marketDay", "money", "level", "signed", "cap", "pct", "dayName", "callDays", "dayOf",
                                   "pickDays", "liveDay", "closeOf", "openOf", "offsetOf", "wentWords", "sideClass", "laneItems", "combinedItems", "top",
                                   "preItems", "directionItems", "claudePick", "claudeItems", "jevPick", "jevItems", "tally", "laneTotal", "dayScale",
                                   "laneSvg", "axisSvg", "callRow", "headLine", "squareKey", "fiveDays"))
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FIXED_NOW + FAKE_DOM
              + "Node.prototype.addEventListener = function(k, f){ this.on = f; };"
              + "".join(_var(v) for v in ("NS", "LANES", "COMBINED_FROM", "SQ", "SQ_CLASS", "TICK_GAP", "RESULT", "VIEWER_FMT"))
              + "var grades = D.grades || {}, live = D.live || null, perDay = D.perDay || {}, picked = D.picked || null, lane = D.lane || 'combined', open = {};"
              "var wanted = []; function want(d){ wanted.push(d); }" + fns + js)
    out = run_node(_NODE, script, input=json.dumps(data), capture_output=True, text=True, timeout=20,
                   env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def grade(ts, horizon="next_30", **over):
    """A row as grade.py writes it (the grades file of a lane), trimmed to what the page reads."""
    row = {"row_ts": ts, "horizon": horizon, "graded": True, "from": 7750.0, "edge": 2.2, "g": 0.4,
           "best": {"points": 1.5, "minute": 9}, "worst": {"points": -1.1, "minute": 21}, "label": "flat", "pick": "flat",
           "verdict": "right"}
    return {**row, **over}


# the owner's table of 2026-10-09, its 10-08 15:00 and 14:31 rows, as the store holds them
OCT8_1500 = grade("2026-10-08T15:00:31.298177-04:00", **{"from": 7754.82, "edge": 2.24, "g": 2.75, "best": {"points": 11.05, "minute": 27},
                                                         "worst": {"points": -2.27, "minute": 8}, "label": "up", "verdict": "wrong"})
OCT8_1431 = grade("2026-10-08T14:31:03.112000-04:00", **{"from": 7756.79, "edge": 2.24, "g": -0.48, "best": {"points": 2.01, "minute": 3},
                                                         "worst": {"points": -3.6, "minute": 17}})
# the opening lane's twelve calls of a morning, five minutes apart from 09:35, and the pre-market call's two checks
MORNING = [grade(f"2026-10-08T{h}:{m}:00-04:00", "next_10", verdict="right" if i % 4 == 1 else "wrong", label="down", pick="down")
           for i, (h, m) in enumerate([("09", "35"), ("09", "40"), ("09", "45"), ("09", "50"), ("09", "55"), ("10", "00"), ("10", "05"),
                                       ("10", "10"), ("10", "15"), ("10", "20"), ("10", "25"), ("10", "30")])]
PRE = [grade("2026-10-08T08:48:01-04:00", "open_10", verdict="right"), grade("2026-10-08T09:28:02-04:00", "open_10", verdict="wrong", label="up", pick="down"),
       grade("2026-10-08T09:28:02-04:00", "open_30", verdict="wrong", label="flat", pick="down")]


def test_the_calls_are_the_30_minute_windows_by_market_day_newest_day_first_each_days_calls_earliest_first():
    """Will, 2026-10-09: the days newest first, and in each day the calls from earlier to later; a read graded twice
    keeps its last row; the 60-minute grades are never a call on the page. The lanes read the same rows: the combined
    call the live lane's next_30, the opening calls the tape lane's next_10, the pre-market call its day's last read's two
    checks, drawn at 10 and 30 minutes after the settled open."""
    rows = [grade("2026-10-07T15:29:00-04:00"), grade("2026-10-08T09:30:00-04:00", verdict="wrong"),
            grade("2026-10-08T09:30:00-04:00", horizon="next_60"),                   # the end-price hour is not a 30-minute call
            OCT8_1431, OCT8_1500, grade("2026-10-08T09:30:00-04:00", verdict="right"),   # graded again: the last row stands
            grade("2026-10-08T10:01:00-04:00", graded=False)]                        # not graded yet: left out
    got = _run("console.log(JSON.stringify(callDays(D.rows, 'next_30').map(function(d){ return [d.day, d.calls.map(function(c){"
               " return c.row_ts.slice(11, 16) + ' ' + c.verdict; }), dayName(d.day)]; })));", {"rows": rows})
    assert got == [["2026-10-08", ["09:30 right", "14:31 right", "15:00 wrong"], "Thu 10-08"], ["2026-10-07", ["15:29 right"], "Wed 10-07"]]
    lanes = _run("console.log(JSON.stringify(['combined', 'opening', 'premarket'].map(function(l){ return laneItems(l, '2026-10-08').map(function(k){ return [viewerTime(k.t), k.state]; }); })));",
                 {"grades": {"live": rows, "tape": MORNING, "premarket": PRE}})
    assert lanes[0] == [["06:30", "right"], ["11:31", "right"], ["12:00", "wrong"]]
    assert len(lanes[1]) == 12 and lanes[1][:2] == [["06:35", "wrong"], ["06:40", "right"]]
    assert lanes[2] == [["06:45", "wrong"], ["07:05", "wrong"]]                       # 08:48's check is not the day's last read's
    assert "next_60" not in JS


def test_the_header_names_the_newest_day_and_says_when_it_is_done():
    """Design B's header (2026-10-14): the page's name on the left; on the right the newest day with graded calls, as the
    forecast page names its card's day (the market day, never the viewer's), and under it the zone the page's times are
    in with "day in progress" and the time until the day's close, "day done" after it."""
    js = "console.log(JSON.stringify(headLine(D.d, Date.now())));"
    grades = {"live": [grade("2026-10-09T10:02:31-04:00")]}
    assert _run(js, {"d": "2026-10-09", "grades": grades, "now": "2026-10-09T15:59:00-04:00"}) == ["Fri 10-09", "Times PDT · day in progress, 12:59"]
    assert _run(js, {"d": "2026-10-09", "grades": grades, "now": "2026-10-09T16:00:00-04:00"}) == ["Fri 10-09", "Times PDT · day done"]
    assert _run(js, {"d": "2026-10-09", "grades": grades, "now": "2026-10-10T20:00:00-04:00"}, TOKYO) == ["Fri 10-09", "Times GMT+9 · day done"]
    assert '<div class="hd-1"><span class="hd-l">SPX &middot; Results</span><span class="hd-r"><span id="hdR"></span><span class="hd-z" id="hdZ"></span></span></div>' in READS
    assert "flex-wrap:wrap" in re.search(r"\n\.hd-1\{([^}]*)\}", READS).group(1)      # at 320 the day wraps under the name, whole
    assert "<title>SPX · Results</title>" in READS


def test_a_call_row_says_the_time_where_it_went_what_was_called_and_the_grade_and_opens_to_the_owners_numbers():
    """10-08 15:00 on the owner's table: price 7,754.82, flat ±2.2, low −2.3, average +2.8, high +11.1, called flat, went
    up, wrong. The row says the call first and what happened after it (Design B: never the outcome before the pick); the
    time is the viewer's (12:00 in Los Angeles); the numbers wait behind a tap."""
    js = ("var row = callRow('combined', {t: Date.parse(D.c.row_ts), state: D.c.verdict, row: D.c, minutes: 30}), more = row.kids[4];"
          "var before = more.hidden; row.on(); console.log(JSON.stringify([dump(row), before, more.hidden, row.attrs['aria-expanded']]));")
    row, before, after, expanded = _run(js, {"c": OCT8_1500})
    kids = row["kids"]
    assert row["tag"] == "button" and [k["attrs"].get("class") for k in kids] == ["sq", "t", "w", "res dn", "more"]
    assert _flat_text(kids[1]) == "12:00" and _flat_text(kids[2]) == "Called Flat · went upaverage +2.8\u00a0pts, past ±\u00a02.2" and _flat_text(kids[3]) == "Wrong"
    assert [k["text"] for k in kids[4]["kids"]] == ["Called at 7,754.82 · flat band 7,752.6 to 7,757.1", "30-min average 7,757.57 (+2.8 pts)",
                                                    "Highest +11.1 pts (minute 27) · lowest −2.3 pts (minute 8)"]
    assert before is True and after is False and expanded == "true"
    right = _run("console.log(JSON.stringify(dump(callRow('combined', {t: 0, state: 'right', row: D.c, minutes: 30}).kids[3])));", {"c": OCT8_1431})
    assert right["attrs"]["class"] == "res" and right["text"] == "Right"
    # the other lanes' rows in their own words
    words = _run("console.log(JSON.stringify(D.k.map(function(k){ var r = callRow(k.lane, k.item); return [r.tag, r.kids[2].textContent, r.kids[3].textContent]; })));", {"k": [
        {"lane": "direction", "item": {"t": 0, "state": "stayed_flat", "dir": {"called": True, "side": "up", "result": "stayed_flat", "points": -9.1}}},
        {"lane": "direction", "item": {"t": 0, "state": "passed", "dir": {"called": False, "side": "down", "points": 0}}},
        {"lane": "claude", "item": {"t": 0, "state": "wrong", "claude": {"pick": "flat", "p": 50, "went": "up"}}},
        {"lane": "premarket", "item": {"t": 0, "state": "wrong", "row": PRE[1], "minutes": 10, "check": True}}]})
    assert words == [["div", "Called Up · stayed flatscore −9.1", "Flat"], ["div", "No call (leaned Down) · passedscore 0.0", "Passed"],
                     ["div", "Called Flat 50% · went up", "Wrong"], ["div", "10-min check · called Down · went upaverage +0.4\u00a0pts, inside ±\u00a02.2", "Wrong"]]


def test_a_day_folds_to_its_score_and_squares_oldest_first_and_opens_to_its_calls():
    """Design B's lanes: each lane's calls as squares on the day's axis at the time each call was made, its first hour
    zoomed in to half the width so the opening's twelve calls five minutes apart never overlap, the rest of the session
    to the close in the other half; the page's one key (filled right, rust wrong, outlined flat or passed, dashed open);
    every square 9px or more and inside the drawing."""
    js = ("var svg = laneSvg(292, laneItems('opening', '2026-10-08'), '2026-10-08', null);"
          "console.log(JSON.stringify(svg.kids.filter(function(k){ return k.tag === 'rect'; }).map(function(k){ return [k.attrs['class'], +k.attrs.x, +k.attrs.width]; })));")
    got = _run(js, {"grades": {"tape": MORNING}})
    assert len(got) == 12 and [g[0] for g in got[:4]] == ["sq-w", "sq-r", "sq-w", "sq-w"]
    xs = [g[1] for g in got]
    assert all(b - a >= 9 + 2 for a, b in zip(xs, xs[1:])) and xs[0] >= 0 and xs[-1] + 9 <= 146 + 9   # the first hour is the left half
    assert all(g[2] == 9 for g in got)
    shapes = _run("console.log(JSON.stringify(['right', 'wrong', 'passed', 'stayed_flat', 'open'].map(function(s){ var r = laneSvg(292, [{t: Date.parse('2026-10-08T11:00:00-04:00'), state: s}], '2026-10-08', null).kids.filter(function(k){ return k.tag === 'rect'; })[0]; return [r.attrs['class'], r.attrs.width]; })));",
                  {"grades": {"tape": MORNING}})
    assert shapes == [["sq-r", "9"], ["sq-w", "9"], ["sq-f", "7.5"], ["sq-f", "7.5"], ["sq-o", "7.5"]]
    for rule in (".lane-g .sq-r{fill:var(--fill)}", ".lane-g .sq-w{fill:var(--o-dn)}", ".lane-g .sq-o{fill:none;stroke:var(--o-up);"):
        assert rule in READS, rule
    axis = _run("console.log(JSON.stringify(axisSvg(292, '2026-10-08', Date.parse(D.now)).kids.filter(function(k){ return k.tag === 'text'; }).map(function(k){ return k.textContent; })));",
                {"grades": {"tape": MORNING}, "now": "2026-10-08T12:49:00-04:00"})
    assert axis[:3] == ["06:30", "07:00", "07:30"] and axis[-2:] == ["first hour, zoomed in", "now 09:49"] and "13:00" in axis
    assert "Tap a lane to list its calls." in JS and "'For comparison, not a call'" in JS


def test_the_pinned_summary_counts_the_last_five_days_beside_one_control_for_every_day():
    """Design B's last five days, under the picked lane's list (the pinned summary went): each day's score of the lane
    picked under the day's name, the picked day ringed, and their total with its share; a day whose files are still to
    come says so, and is asked for."""
    days = ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    rows = [grade(f"{d}T10:00:00-04:00", verdict=v) for d, vs in zip(days, (["right", "wrong"], ["wrong"], ["right"], ["right", "right"], ["wrong", "passed"])) for v in vs]
    rows = [{**r, "row_ts": r["row_ts"].replace("10:00", f"1{i % 10}:00")} for i, r in enumerate(rows)]
    js = "var box = fiveDays(D.lane, D.days); console.log(JSON.stringify([dump(box), wanted]));"
    box, wanted = _run(js, {"grades": {"live": rows}, "days": days, "picked": "2026-10-08", "lane": "combined"})
    head, row = box["kids"]
    assert _flat_text(head) == "Last 5 days · combined call4 of 7 · 57%"
    assert [(_flat_text(c), c["attrs"].get("class")) for c in row["kids"]] == [("Fri1 of 2", None), ("Mon0 of 1", None), ("Tue1 of 1", None), ("Wed2 of 2", None), ("Thu0 of 1", "on")]
    assert wanted == days
    jev, _ = _run(js, {"grades": {"live": rows}, "days": days, "picked": "2026-10-08", "lane": "jev"})
    assert _flat_text(jev["kids"][0]) == "Last 5 days · jev alone—" and [_flat_text(c) for c in jev["kids"][1]["kids"]][0] == "Fri…"
    assert ".d5 span.on{box-shadow:inset 0 0 0 1.5px var(--i)}" in READS and 'id="sum"' not in READS


def test_the_page_reads_the_grades_through_the_raw_route_and_keeps_an_opened_call_open():
    assert _var("GRADES_URL").strip() == "var GRADES_URL = '/api/store/file?name=JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON&lane=';"
    assert "var GRADE_LIMITS = {live: 2000, tape: 20000, premarket: 2000};" in JS
    for name in ("FORECASTS_BY_FORECASTER_READ_AND_HORIZON", "DIRECTION_LINE_GRADES_BY_READ", "CLAUDE_FORECAST_ANSWERS_BY_READ", "CLAUDE_FORECAST_OUTCOMES_BY_READ"):
        assert f"name={name}" in JS, name
    assert "var DAYS_SHOWN = 5, POLL_MS = 60000, FETCHES_AT_ONCE = 2;" in JS
    assert "while(running < FETCHES_AT_ONCE && queue.length)" in _fn("pump") and "gate(function()" in _fn("want")
    assert "open[id] = !open[id];" in _fn("callRow") and "more.hidden = !open[id];" in _fn("callRow")      # a call the reader opened stays open
    assert "'Fetch failed, showing the last results'" in _fn("draw") and "HASH[location.hash]" in JS
    assert '<script src="/m/sheet.js"></script>' not in READS


def test_the_tab_bar_lights_reads_and_every_page_links_it():
    """Two tabs since the redesign (2026-10-10): Forecast, the page at /m/, and Results (Design B's name for this page,
    2026-10-14, its address kept), lit here."""
    nav = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', READS).group(1)
    assert re.findall(r'<a class="tab" href="([^"]+)"', nav) == ["/m/"]
    assert nav.count('<span class="tab on" data-press>') == 1 and "<s>RESULTS</s>" in nav and "<s>FORECAST</s>" in nav
    assert "NOW" not in nav and "jev.html" not in nav
    tabs = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', (M / "jev-spx.html").read_text()).group(1)
    assert 'href="/m/reads.html"' in tabs and "thread.html" not in tabs


def test_the_page_fits_the_owners_360px_phone_and_keeps_the_house_rules():
    """At 360 (measured in headless Chrome with the shipped face, 2026-10-14) a call row's four columns fit the card:
    the square, the time, the words wrapping in their own column, and the result; a row, a lane, a day of the picker are
    the page's 48px tap minimum or more. The lanes' drawing is laid out at the card's measured width, its 11px words never
    scaled. No viewport unit, no innerHTML, and no class the stylesheet names that nothing writes."""
    card = 360 - 2 * 16 - 2 * 14
    cols = re.search(r"\.ls-h,\.ls\{display:grid;grid-template-columns:14px 40px minmax\(0,1fr\) 46px;gap:8px\}", READS)
    assert cols and card - (14 + 40 + 46 + 3 * 8) >= 150
    assert "min-height:var(--tap-min)" in re.search(r"\n\.ls\{([^}]*)\}", READS).group(1) and "min-height:58px" in re.search(r"\n\.lane\{([^}]*)\}", READS).group(1)
    assert "min-height:var(--tap-min)" in re.search(r"\n\.days button\{([^}]*)\}", READS).group(1)
    assert "Math.round(card.clientWidth || 312) - 20" in _fn("draw") and ".axis text{font-size:11px;" in READS
    assert "cursor:pointer" not in READS and not re.search(r"[\U0001F300-\U0001FAFF]", READS)
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", READS)))
    assert not re.search(r"\d(?:vh|dvh|svh|lvh|vw)\b", css)
    assert ".innerHTML" not in JS and not re.search(r"#[0-9A-Fa-f]{6}\b", JS)
    # press.js writes is-pressed; up and dn are a word's side, written from a call's label
    styled = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"url\([^)]*\)", "", css))) - {"is-pressed"}
    written = {c for cls in re.findall(r'class="([^"]+)"', READS.split("<body>", 1)[1]) for c in cls.split()}
    written |= {c for word in re.findall(r"'([a-z][a-z0-9 -]*)'", JS) for c in word.split()}
    assert styled <= written, styled - written
