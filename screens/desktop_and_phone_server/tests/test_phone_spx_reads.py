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
    premarket), the 30-minute card D.live, the opening, pre-market and direction line cards D.tapeCard, D.preCard and
    D.dirCard, and the day files of D.perDay."""
    if not _NODE:
        pytest.skip("node is not installed")
    fns = "".join(_fn(f) for f in ("svgEl", "viewerTime", "marketAt", "marketDay", "money", "level", "signed", "cap", "pct", "dayName", "callDays", "dayOf",
                                   "pickDays", "liveDay", "closeOf", "openOf", "offsetOf", "wentWords", "sideClass", "laneItems", "combinedItems", "top",
                                   "runningCard", "openingItems", "openingWords", "preItems", "directionItems", "clOk", "clBase", "claudePick", "claudeItems", "jevPick",
                                   "jevItems", "tally", "laneTotal", "laneNote", "voices", "dayScale", "laneSvg", "axisSvg", "callRow", "headLine",
                                   "squareKey", "fiveDays"))
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FIXED_NOW + FAKE_DOM
              + "Node.prototype.addEventListener = function(k, f){ this.on = f; };"
              + "".join(_var(v) for v in ("NS", "LANES", "OPENING_END_MIN", "COMBINED_FROM", "SQ", "SQ_CLASS", "TICK_GAP", "RESULT", "VIEWER_FMT"))
              + "var grades = D.grades || {}, live = D.live || null, tapeCard = D.tapeCard || null, preCard = D.preCard || null, dirCard = D.dirCard || null, perDay = D.perDay || {};"
              "var picked = D.picked || null, lane = D.lane || 'combined', open = {};"
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


def test_every_lane_reads_its_calls_by_market_day_earliest_first_a_read_graded_twice_keeping_its_last_row():
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
    # every lane of Thu 10-08 from its rows, seen on Wed 10-14, as [time, state] with the lane's score and the line under
    # it: the direction line's call that stayed flat counts against it, its pass is left out and a read graded twice keeps
    # its last row; Claude's pick against its 30-minute outcome (the 60-minute one never), a tie going to up as the
    # service scores it, a failed answer scored on its starting odds and a read with neither left out, as the service
    # and the forecast page count them; JEV alone's own answer against the graded average's label, open on a read not
    # graded; the pre-market call regraded keeps its last row (S2)
    ts = {"09:30": "2026-10-08T09:30:00-04:00", "14:31": OCT8_1431["row_ts"], "15:00": OCT8_1500["row_ts"], "15:30": "2026-10-08T15:30:02-04:00"}
    day = {"direction": [{"day": "2026-10-08", "row_ts": ts["09:30"], "called": True, "side": "up", "result": "right", "points": 12.0},
                         {"day": "2026-10-08", "row_ts": ts["14:31"], "called": True, "side": "down", "result": "right", "points": 5.0},
                         {"day": "2026-10-08", "row_ts": ts["14:31"], "called": True, "side": "down", "result": "stayed_flat", "points": -9.0},
                         {"day": "2026-10-08", "row_ts": ts["15:00"], "called": False, "side": "down", "points": 0.0}],
           "answers": [{"line_type": "final_forecast", "read_id": "live:" + ts["09:30"], "trading_day": "2026-10-08",
                        "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 20.0, "flat_pct": 50.0, "down_pct": 30.0}}},
                       {"line_type": "final_forecast", "read_id": "live:" + ts["14:31"], "trading_day": "2026-10-08",
                        "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 40.0, "flat_pct": 20.0, "down_pct": 40.0}}},
                       {"line_type": "final_forecast", "read_id": "live:" + ts["15:00"], "trading_day": "2026-10-08",
                        "forecast": {"next_30_minutes": {"status": "failed", "up_pct": None, "flat_pct": None, "down_pct": None,
                                                         "base_rate_shown_to_claude": {"up_pct": 18.8, "flat_pct": 56.2, "down_pct": 25.0}}}},
                       {"line_type": "final_forecast", "read_id": "live:" + ts["15:30"], "trading_day": "2026-10-08",
                        "forecast": {"next_30_minutes": {"status": "rejected"}}},
                       {"line_type": "raw_answer", "read_id": "live:" + ts["15:30"], "trading_day": "2026-10-08"}],
           "outcomes": [{"read_id": "live:" + ts["09:30"], "horizon": "next_30_minutes", "result": {"status": "final", "direction": "flat"}},
                        {"read_id": "live:" + ts["09:30"], "horizon": "next_60_minutes", "result": {"status": "final", "direction": "up"}},
                        {"read_id": "live:" + ts["14:31"], "horizon": "next_30_minutes", "result": {"status": "final", "direction": "down"}},
                        {"read_id": "live:" + ts["15:00"], "horizon": "next_30_minutes", "result": {"status": "final", "direction": "flat"}},
                        {"read_id": "live:" + ts["15:30"], "horizon": "next_30_minutes", "result": {"status": "final", "direction": "flat"}}],
           "jev": [{"voice_name": "jev_own", "sum_id": "average_30", "row_ts": ts["09:30"], "voice_probs": {"down": 0.2, "flat": 0.5, "up": 0.3}},
                   {"voice_name": "jev_own", "sum_id": "average_30", "row_ts": ts["14:31"], "voice_probs": {"down": 0.5, "flat": 0.3, "up": 0.2}},
                   {"voice_name": "jev_own", "sum_id": "average_30", "row_ts": ts["15:00"], "voice_probs": {"down": 0.1, "flat": 0.3, "up": 0.6}},
                   {"voice_name": "jev_own", "sum_id": "average_30", "row_ts": ts["15:30"], "voice_probs": {"down": 0.3, "flat": 0.6, "up": 0.1}},
                   {"voice_name": "jev_corrected", "sum_id": "average_30", "row_ts": ts["14:31"], "voice_probs": {"down": 0.1, "flat": 0.8, "up": 0.1}},
                   {"voice_name": "jev_own", "sum_id": "average_60", "row_ts": ts["14:31"], "voice_probs": {"down": 0.1, "flat": 0.8, "up": 0.1}}]}
    regraded = PRE + [grade("2026-10-08T09:28:02-04:00", "open_30", verdict="right", label="down", pick="down")]
    lanes = _run("console.log(JSON.stringify(LANES.map(function(l){ var items = laneItems(l[0], '2026-10-08');"
                 " return [items.map(function(k){ return [viewerTime(k.t), k.state]; }), laneTotal(l[0], items, '2026-10-08'), laneNote(l, items)]; })))",
                 {"grades": {"live": rows, "tape": MORNING, "premarket": regraded}, "perDay": {"2026-10-08": day}, "now": "2026-10-14T12:00:00-04:00"})
    pre, opening, combined, direction, claude, jev = lanes
    assert pre == [[["06:45", "wrong"], ["07:05", "right"]], ["1 of 2", "checks at 06:45, 07:05"], "read 06:28 · called Down · JEV alone"]
    assert len(opening[0]) == 12 and opening[0][:2] == [["06:35", "wrong"], ["06:40", "right"]] and opening[1] == ["3 of 12", "done 07:40"]
    assert combined[:2] == [[["06:30", "right"], ["11:31", "right"], ["12:00", "wrong"]], ["2 of 3", None]]
    assert direction[:2] == [[["06:30", "right"], ["11:31", "stayed_flat"], ["12:00", "passed"]], ["1 of 2", "score +1.5 a call"]]
    assert claude[:2] == [[["06:30", "right"], ["11:31", "wrong"], ["12:00", "right"]], ["2 of 3", "scored after the close"]]
    assert jev[:2] == [[["06:30", "right"], ["11:31", "wrong"], ["12:00", "right"], ["12:30", "open"]], ["2 of 3", "1 open"]]
    # a lane with no call on a day gone by says so; on today it is still to come
    none = _run("console.log(JSON.stringify([laneTotal('direction', [], '2026-10-08'), laneTotal('direction', [], '2026-10-14')]))", {"now": "2026-10-14T12:00:00-04:00"})
    assert none == [["None that day", None], ["None yet", None]]
    # today at 09:58 (S6, S7): the opening run is on, its calls on the card the grades lack drawn open, and the pre-market
    # call names its read and its odds from today's card, its 30-minute check still open
    reads = [f"2026-10-14T{h}:{m}:00-04:00" for h, m in [("09", "35"), ("09", "40"), ("09", "45"), ("09", "50"), ("09", "55"), ("10", "00"), ("10", "05"),
                                                           ("10", "10"), ("10", "15"), ("10", "20"), ("10", "25"), ("10", "30")]]
    tape_card = {"row_ts": reads[4], "schedule": {"reads": reads, "looks_ahead_min": 10},
                 "calls": [{"read": reads[4], "mark": "2026-10-14T10:05:00-04:00", "pick": "down", "p": 0.7},
                           {"read": reads[3], "mark": "2026-10-14T10:00:00-04:00", "pick": "flat", "p": 0.55},
                           {"read": reads[2], "mark": "2026-10-14T09:55:00-04:00", "pick": "down", "p": 0.6}]}
    pre_card = {"day": "2026-10-14", "row_ts": "2026-10-14T09:28:02-04:00", "calls": [{"read": "2026-10-14T09:28:02-04:00", "pick": "down", "p": 0.68},
                                                                                     {"read": "2026-10-14T08:48:01-04:00", "pick": "up", "p": 0.51}]}
    today = {"grades": {"tape": [grade(r, "next_10", verdict="wrong") for r in reads[:3]],
                        "premarket": [grade("2026-10-14T09:28:02-04:00", "open_10", verdict="wrong", pick="down")]},
             "tapeCard": tape_card, "preCard": pre_card, "now": "2026-10-14T09:58:00-04:00"}
    js = ("console.log(JSON.stringify(['opening', 'premarket'].map(function(l){ var items = laneItems(l, '2026-10-14');"
          " return [items.map(function(k){ return [viewerTime(k.t), k.state]; }), laneTotal(l, items, '2026-10-14'), laneNote(LANES.filter(function(x){ return x[0] === l; })[0], items)]; })))")
    opening, pre = _run(js, today)
    assert opening[:2] == [[["06:35", "wrong"], ["06:40", "wrong"], ["06:45", "wrong"], ["06:50", "open"], ["06:55", "open"]], ["0 of 3", "running · 5 of 12"]]
    assert pre == [[["06:45", "wrong"], ["07:05", "open"]], ["0 of 1", "checks at 06:45, 07:05"], "read 06:28 · called Down 68% · JEV alone"]
    # before the first check is graded both are open on the card's read; after the last opening mark the run is done
    early = _run(js, {**today, "grades": {}, "now": "2026-10-14T09:40:00-04:00"})
    assert early[1][:2] == [[["06:45", "open"], ["07:05", "open"]], ["2 open", "checks at 06:45, 07:05"]]
    done = _run(js, {**today, "grades": {"tape": [grade(r, "next_10", verdict="right") for r in reads]}, "now": "2026-10-14T10:41:00-04:00"})
    assert done[0][1] == ["12 of 12", "done 07:40"]
    # the direction line today at 13:40, as the forecast page's scoreboard draws it: its graded reads, then the line card's
    # read, open until its grade lands, when it is the 30-minute card's newest read, on the side taken at the read (its
    # graded call, revision 0); its row says the call, or the lean of a read it made no call on, and when it is scored
    read = "2026-10-14T13:28:10.336004-04:00"
    live_card = {"row_ts": read, "marks": {"next_30": "2026-10-14T13:58:00-04:00"}}
    dir_card = {"read_id": "live:" + read, "row_ts": read, "day": "2026-10-14", "call": {"called": True, "side": "down", "p_up": 0.3, "revision": 1},
                "graded_call": {"called": True, "side": "up", "p_up": 0.58, "revision": 0}}
    dir_rows = [{"day": "2026-10-14", "row_ts": "2026-10-14T10:00:26.420624-04:00", "called": True, "side": "up", "result": "stayed_flat", "points": 0.0},
                {"day": "2026-10-14", "row_ts": "2026-10-14T12:31:11.426920-04:00", "called": True, "side": "up", "result": "right", "points": 10.16}]
    js = ("var items = laneItems('direction', D.d); console.log(JSON.stringify([items.map(function(k){ return [viewerTime(k.t), k.state]; }), laneTotal('direction', items, D.d),"
          " items.filter(function(k){ return k.open; }).map(function(k){ var r = callRow('direction', k); return [r.tag, r.kids[2].textContent, r.kids[3].textContent]; })]));")
    lined = {"d": "2026-10-14", "live": live_card, "dirCard": dir_card, "perDay": {"2026-10-14": {"direction": dir_rows}}, "now": "2026-10-14T13:40:00-04:00"}
    assert _run(js, lined) == [[["07:00", "stayed_flat"], ["09:31", "right"], ["10:28", "open"]], ["1 of 2", "score +5.1 a call"],
                               [["div", "Called Up 58% · openscored 10:58", "Open"]]]
    # the real card of 10-15 13:00, a read it made no call on
    passed = {**dir_card, "graded_call": {"called": False, "side": "up", "p_up": 0.5241, "revision": 0}}
    assert _run(js, {**lined, "dirCard": passed})[2] == [["div", "No call, leans Up 52% · openscored 10:58", "Open"]]
    # its grade landed; the line card still on the last read; the day gone by: no open square
    landed = dir_rows + [{"day": "2026-10-14", "row_ts": read, "called": True, "side": "up", "result": "wrong", "points": -12.0}]
    assert _run(js, {**lined, "perDay": {"2026-10-14": {"direction": landed}}})[0][-1] == ["10:28", "wrong"]
    assert _run(js, {**lined, "live": {**live_card, "row_ts": "2026-10-14T13:58:31-04:00"}})[0] == [["07:00", "stayed_flat"], ["09:31", "right"]]
    assert _run(js, {**lined, "now": "2026-10-15T09:00:00-04:00"})[0] == [["07:00", "stayed_flat"], ["09:31", "right"]]
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
        {"lane": "claude", "item": {"t": 0, "state": "right", "claude": {"pick": "flat", "p": 56.2, "went": "flat", "base": True}}},
        {"lane": "premarket", "item": {"t": 0, "state": "wrong", "row": PRE[1], "minutes": 10, "check": True}},
        {"lane": "premarket", "item": {"t": 0, "state": "open", "minutes": 30, "check": True, "open": {"pick": "down", "p": 0.68, "mark": "2026-10-14T10:05:00-04:00"}}},
        {"lane": "opening", "item": {"t": 0, "state": "open", "minutes": 10, "open": {"pick": "down", "p": 0.89, "mark": "2026-10-14T10:40:00-04:00"}}}]})
    assert words == [["div", "Called Up · stayed flatscore −9.1", "Flat"], ["div", "No call (leaned Down) · passedscore 0.0", "Passed"],
                     ["div", "Called Flat 50% · went up", "Wrong"], ["div", "Starting odds Flat 56% · stayed flatNo answer · scored on its starting odds", "Right"],
                     ["button", "10-min check · called Down · went upaverage +0.4\u00a0pts, inside ±\u00a02.2", "Wrong"],
                     ["div", "30-min check · called Down 68% · openscored 07:05", "Open"], ["div", "Called Down 89% · openscored 07:40", "Open"]]
    # a graded pre-market check opens in place as the others do (10-14's real checks, read 09:28 and measured from the
    # settled open): its call with today's odds, its window from the settled open, the flat band, the average and the
    # window's high and low; a past day's grades carry the pick alone; the two checks of a read open apart
    checks = [grade("2026-10-14T09:28:02-04:00", "open_10", **{"from": 7775.95, "edge": 2.28, "g": 3.63, "label": "up", "pick": "down", "verdict": "wrong",
                                                               "best": {"points": 8.79, "minute": 8}, "worst": {"points": -2.88, "minute": 1}}),
              grade("2026-10-14T09:28:02-04:00", "open_30", **{"from": 7775.95, "edge": 4.64, "g": -1.81, "label": "flat", "pick": "down", "verdict": "wrong",
                                                               "best": {"points": 11.8, "minute": 15}, "worst": {"points": -17.77, "minute": 25}})]
    pre_card = {"day": "2026-10-14", "row_ts": "2026-10-14T09:28:02-04:00", "calls": [{"read": "2026-10-14T09:28:02-04:00", "pick": "down", "p": 0.68}]}
    js = ("console.log(JSON.stringify(preItems(D.d).map(function(k){ var row = callRow('premarket', k), more = row.kids[4], shut = more.hidden; row.on();"
          " return [row.tag, row.attrs['data-k'], shut, more.hidden, more.kids.map(function(x){ return x.textContent; })]; })));")
    opened = _run(js, {"d": "2026-10-14", "grades": {"premarket": checks}, "preCard": pre_card, "now": "2026-10-14T12:00:00-04:00"})
    assert opened == [["button", "row premarket2026-10-14T09:28:02-04:00open_10", True, False,
                       ["Called Down 68% · window 06:35 to 06:45", "Settled open 7,775.95 · flat band 7,773.7 to 7,778.2", "10-min average 7,779.58 (+3.6 pts)",
                        "Highest +8.8 pts (minute 8) · lowest −2.9 pts (minute 1)"]],
                      ["button", "row premarket2026-10-14T09:28:02-04:00open_30", True, False,
                       ["Called Down 68% · window 06:35 to 07:05", "Settled open 7,775.95 · flat band 7,771.3 to 7,780.6", "30-min average 7,774.14 (−1.8 pts)",
                        "Highest +11.8 pts (minute 15) · lowest −17.8 pts (minute 25)"]]]
    past = _run(js, {"d": "2026-10-14", "grades": {"premarket": checks}, "now": "2026-10-16T12:00:00-04:00"}, TOKYO)
    assert [r[4][0] for r in past] == ["Called Down · window 22:35 to 22:45", "Called Down · window 22:35 to 23:05"]


def test_a_lane_draws_its_calls_as_squares_at_their_times_on_the_days_axis():
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


def test_the_last_five_days_count_the_picked_lane_under_its_list():
    """Design B's last five days, under the picked lane's list: each day's score of the lane picked under the day's name,
    the picked day ringed, and their total with its share, a line under them saying the days are the picked lane's; a
    day whose files are still to come says so, and is asked for."""
    days = ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    rows = [grade(f"{d}T10:00:00-04:00", verdict=v) for d, vs in zip(days, (["right", "wrong"], ["wrong"], ["right"], ["right", "right"], ["wrong", "passed"])) for v in vs]
    rows = [{**r, "row_ts": r["row_ts"].replace("10:00", f"1{i % 10}:00")} for i, r in enumerate(rows)]
    js = "var box = fiveDays(D.lane, D.days); console.log(JSON.stringify([dump(box), wanted]));"
    box, wanted = _run(js, {"grades": {"live": rows}, "days": days, "picked": "2026-10-08", "lane": "combined"})
    head, row, note = box["kids"]
    assert _flat_text(head) == "Last 5 days · combined4 of 7 · 57%"
    assert note["text"] == "Each lane has its own 5-day line; this one follows the lane you picked."
    assert [(_flat_text(c), c["attrs"].get("class")) for c in row["kids"]] == [("Fri1 of 2", None), ("Mon0 of 1", None), ("Tue1 of 1", None), ("Wed2 of 2", None), ("Thu0 of 1", "on")]
    assert wanted == days
    jev, _ = _run(js, {"grades": {"live": rows}, "days": days, "picked": "2026-10-08", "lane": "jev"})
    assert _flat_text(jev["kids"][0]) == "Last 5 days · jev alone—" and [_flat_text(c) for c in jev["kids"][1]["kids"]][0] == "Fri…"
    assert ".d5 span.on{box-shadow:inset 0 0 0 1.5px var(--i)}" in READS and 'id="sum"' not in READS


def test_the_page_reads_the_stores_by_name_asks_only_for_what_changed_and_keeps_an_opened_call_open():
    """The files through the station's by-name route, about a week of each grades file's newest rows (the review of
    2026-10-14: the whole history was 1.15 MB a minute over the tunnel). A file read once is asked for its last row alone
    and, when its count of rows grew, for the rows added alone (the last row in hand when one was); a count that fell, or
    a held last row the station could not parse (a line caught mid-write), reads the newest rows again. Never more than
    two at a time (the station's gate). Only "not found" is a day with nothing in it: a refused fetch (a 502 from the
    tunnel) keeps the lane unknown, says so on the header's line, and is asked again. A card is asked for only when it
    can have changed: the direction line's when today is picked, and again in the busy hours only while it lags the
    30-minute card's newest read."""
    assert _var("GRADES_URL").strip() == "var GRADES_URL = '/api/store/file?name=JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON&lane=';"
    assert "var GRADE_LIMITS = {live: 200, tape: 100, premarket: 40};" in JS
    for name in ("FORECASTS_BY_FORECASTER_READ_AND_HORIZON", "DIRECTION_LINE_GRADES_BY_READ", "CLAUDE_FORECAST_ANSWERS_BY_READ", "CLAUDE_FORECAST_OUTCOMES_BY_READ",
                 "JEV_LIVE_CARD", "JEV_OPENING_CARD", "JEV_PREMARKET_CARD", "DIRECTION_LINE_CARD"):
        assert f"name={name}" in JS, name
    assert "var DAYS_SHOWN = 5, POLL_MS = 60000, SLOW_MS = 300000, RETRY_MS = 20000, FETCHES_AT_ONCE = 2;" in JS
    assert "while(running < FETCHES_AT_ONCE && queue.length)" in _fn("pump") and "return gate(function()" in _fn("stored")
    js = (_var("DAY_URLS") + _var("DAY_LIMIT") + _var("DAYS_SHOWN") + _var("LANE_FILES") + _var("asked") + "var bad = {}, queue = [], running = 0;"
          + "".join(_fn(f) for f in ("stored", "gate", "pump", "fresh", "hold", "track", "want")) + """
var log = [], inflight = 0, most = 0, draws = 0, timers = [], realTimeout = setTimeout;
function draw(){ draws++; }
setTimeout = function(f, ms){ timers.push([ms, f]); };
function settle(){ return new Promise(function(r){ realTimeout(r, 30); }); }
fetch = function(url){
  var name = /name=([A-Z_]+)/.exec(url)[1] + (/day=([\\d-]+)/.exec(url) || ['', ''])[1], limit = +/limit=(\\d+)/.exec(url)[1], f = D.files[name];
  log.push(name + ' ' + limit); inflight++; most = Math.max(most, inflight);
  return new Promise(function(ok){ realTimeout(function(){ inflight--;
    if(f.status) return ok({ok: false, status: f.status, json: function(){ return Promise.reject(new Error('html')); }});
    ok({ok: true, status: 200, json: function(){ return Promise.resolve(f.missing ? {error: 'not found'} : {kind: 'jsonl', total: f.rows.length, rows: f.rows.slice(-limit)}); }});
  }, 5); });
};
(async function(){
  var g = '/api/store/file?name=G', out = {};
  out.first = await fresh('g', g, 200); out.same = await fresh('g', g, 200);
  D.files.G.rows.push({n: 3}); out.grew = await fresh('g', g, 200);
  D.files.G.rows.push({n: 4}, {n: 5}); out.two = await fresh('g', g, 200);
  D.files.G.rows = [{n: 9}]; out.fell = await fresh('g', g, 200);
  D.files.G.rows.push({_unparsed: '{"n": 1'}); out.cut = await fresh('g', g, 200);
  D.files.G.rows = [{n: 9}, {n: 10}, {n: 11}]; out.healed = await fresh('g', g, 200);
  out.gLog = log.splice(0);
  picked = '2026-10-13'; want(picked); await settle();
  var f = perDay[picked];
  out.day = [Object.keys(f).sort(), f.answers, laneItems('direction', picked), Object.keys(bad), asked[picked + 'direction'], timers.map(function(t){ return t[0]; })];
  out.dayLog = log.splice(0).sort();
  D.files['DIRECTION_LINE_GRADES_BY_READ2026-10-13'] = {rows: [{n: 1}]};
  timers[0][1](); await settle();
  out.retry = [f.direction, Object.keys(bad), log.splice(0)];
  out.most = most;
  console.log(JSON.stringify(out));
})();""")
    files = {"G": {"rows": [{"n": 1}, {"n": 2}]}, "FORECASTS_BY_FORECASTER_READ_AND_HORIZON2026-10-13": {"rows": [{"n": 1}]},
             "DIRECTION_LINE_GRADES_BY_READ2026-10-13": {"status": 502}, "CLAUDE_FORECAST_ANSWERS_BY_READ2026-10-13": {"missing": True},
             "CLAUDE_FORECAST_OUTCOMES_BY_READ2026-10-13": {"rows": []}}
    got = _run(js, {"files": files})
    assert got["first"] == [{"n": 1}, {"n": 2}] and got["same"] is None and got["grew"] == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert got["two"] == [{"n": i} for i in range(1, 6)] and got["fell"] == [{"n": 9}]
    assert got["cut"] == [{"n": 9}, {"_unparsed": '{"n": 1'}] and got["healed"] == [{"n": 9}, {"n": 10}, {"n": 11}]
    # read; probed; probed, its one new row in hand; probed and the two added asked for; probed and fell, read again;
    # probed, a line mid-write in hand; probed and read again past it
    assert got["gLog"] == ["G 200", "G 1", "G 1", "G 1", "G 2", "G 1", "G 200", "G 1", "G 1", "G 200"]
    # the 502 leaves the direction lane unknown (null, never an empty day), the header's line on, and a retry armed; the
    # missing answers file is a day with no answers
    assert got["day"] == [["answers", "jev", "outcomes"], [], None, ["2026-10-13direction"], "asking", [20000]]
    assert got["dayLog"] == sorted(f"{n}2026-10-13 1000" for n in ("FORECASTS_BY_FORECASTER_READ_AND_HORIZON", "DIRECTION_LINE_GRADES_BY_READ",
                                                                    "CLAUDE_FORECAST_ANSWERS_BY_READ", "CLAUDE_FORECAST_OUTCOMES_BY_READ"))
    assert got["retry"] == [[{"n": 1}], [], ["DIRECTION_LINE_GRADES_BY_READ2026-10-13 1000"]]
    assert got["most"] <= 2
    # the direction line's card: asked for the first time, never twice in a minute, then in the busy hours while it is
    # another read's than the 30-minute card's newest, never once it has caught up, after the busy hours or on a day
    # whose 30-minute card is not today's
    due = (_var("DAYS_SHOWN") + _var("BUSY_HOURS") + "".join(_fn(f) for f in ("cardDue", "pollEvery", "marketClock"))
           + "var cardAt = {}; console.log(JSON.stringify(D.at.map(function(x){ cardAt.dir = x[1] == null ? null : Date.parse(x[1]); live = x[2]; dirCard = x[3];"
           " NOW = Date.parse(x[0]); return cardDue('dir', Date.now()); })));")
    read, earlier = "2026-10-15T13:00:13.037889-04:00", "2026-10-15T12:31:39.611351-04:00"
    card = lambda ts: {"row_ts": ts}
    lags, caught = {"read_id": "live:" + earlier}, {"read_id": "live:" + read}
    at = [["2026-10-15T13:01:00-04:00", None, card(read), None], ["2026-10-15T13:01:30-04:00", "2026-10-15T13:01:00-04:00", card(read), lags],
          ["2026-10-15T13:02:00-04:00", "2026-10-15T13:01:00-04:00", card(read), lags], ["2026-10-15T13:02:00-04:00", "2026-10-15T13:01:00-04:00", card(read), caught],
          ["2026-10-15T17:40:00-04:00", "2026-10-15T13:01:00-04:00", card(read), lags], ["2026-10-16T10:00:00-04:00", "2026-10-15T13:01:00-04:00", card(read), lags]]
    assert _run(due, {"at": at, "now": at[0][0]}) == [True, False, True, False, False, False]
    assert "open[id] = !open[id];" in _fn("callRow") and "more.hidden = !open[id];" in _fn("callRow")      # a call the reader opened stays open
    assert "'Fetch failed, showing the last results'" in _fn("draw") and "HASH[location.hash]" in JS
    assert "history.replaceState(null, '', '#' + lane)" in _fn("draw") and "window.addEventListener('resize'" in JS
    assert '<script src="/m/sheet.js"></script>' not in READS


def test_the_tab_bar_lights_results_and_every_page_links_it():
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
    assert "Math.round(card.clientWidth || 312) - 32" in _fn("draw") and ".axis text{font-size:11px;" in READS
    assert "cursor:pointer" not in READS and not re.search(r"[\U0001F300-\U0001FAFF]", READS)
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", READS)))
    assert not re.search(r"\d(?:vh|dvh|svh|lvh|vw)\b", css)
    assert ".innerHTML" not in JS and not re.search(r"#[0-9A-Fa-f]{6}\b", JS)
    # press.js writes is-pressed; up and dn are a word's side, written from a call's label
    styled = set(re.findall(r"\.([a-z][a-z0-9-]*)", re.sub(r"url\([^)]*\)", "", css))) - {"is-pressed"}
    written = {c for cls in re.findall(r'class="([^"]+)"', READS.split("<body>", 1)[1]) for c in cls.split()}
    written |= {c for word in re.findall(r"'([a-z][a-z0-9 -]*)'", JS) for c in word.split()}
    assert styled <= written, styled - written
