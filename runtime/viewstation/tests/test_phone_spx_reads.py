"""The SPX reads page (static/m/reads.html), Will's design D of 2026-10-09: every graded 30-minute call a day at a
time, the newest day open, the others folded to their score and one square per call. The page's own functions run
in node against the stand-in DOM, as the forecast page's do, on rows copied from state/spx_jev/integral_grades.jsonl."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_phone_jev_calls import FAKE_DOM, _flat_text

M = Path(__file__).resolve().parents[1] / "static" / "m"
READS = (M / "reads.html").read_text()
JS = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", READS))
_NODE = shutil.which("node")
LA = "America/Los_Angeles"


def _fn(name):
    m = re.search(r"\n  function %s\(.*?(?=\n  (?:function |var |//|[a-z]))" % re.escape(name), JS, re.S)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _var(name):
    m = re.search(r"\n  var %s = .*?;" % re.escape(name), JS)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _run(js, data=None, tz=LA):
    if not _NODE:
        pytest.skip("node is not installed")
    text = "document.createTextNode = function(t){ var n = new Node('#text'); n._t = String(t); return n; };"
    fns = "".join(_fn(f) for f in ("viewerTime", "money", "signed", "cap", "callDays", "isRight", "rightOf", "dayName",
                                   "strip", "squares", "callRow", "dayCard", "summary"))
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FAKE_DOM + text + _var("DAYS_SUMMED") + _var("STRIP_W")
              + _var("NS") + "var VIEWER_FMT = {}, opened = {};" + "function svgEl(tag, attrs){ var e = new Node(tag);"
              " Object.keys(attrs).forEach(function(k){ e.attrs[k] = String(attrs[k]); }); return e; }" + fns + js)
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=20,
                         env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def grade(ts, horizon="next_30", **over):
    """A row as grade.py writes it (state/spx_jev/integral_grades.jsonl), trimmed to what the page reads."""
    row = {"row_ts": ts, "horizon": horizon, "graded": True, "from": 7750.0, "edge": 2.2, "g": 0.4,
           "best": {"points": 1.5, "minute": 9}, "worst": {"points": -1.1, "minute": 21}, "label": "flat", "pick": "flat",
           "verdict": "right"}
    return {**row, **over}


# the owner's table of 2026-10-09, its 10-08 15:00 and 14:31 rows, as the store holds them
OCT8_1500 = grade("2026-10-08T15:00:31.298177-04:00", **{"from": 7754.82, "edge": 2.24, "g": 2.75, "best": {"points": 11.05, "minute": 27},
                                                         "worst": {"points": -2.27, "minute": 8}, "label": "up", "verdict": "wrong"})
OCT8_1431 = grade("2026-10-08T14:31:03.112000-04:00", **{"from": 7756.79, "edge": 2.24, "g": -0.48, "best": {"points": 2.01, "minute": 3},
                                                         "worst": {"points": -3.6, "minute": 17}})


def test_the_calls_are_the_30_minute_windows_by_market_day_newest_first_a_regrade_keeping_its_last_row():
    rows = [grade("2026-10-07T15:29:00-04:00"), grade("2026-10-08T09:30:00-04:00", verdict="wrong"),
            grade("2026-10-08T09:30:00-04:00", horizon="next_60"),                   # the end-price hour is not a 30-minute call
            OCT8_1431, OCT8_1500, grade("2026-10-08T09:30:00-04:00", verdict="right"),   # graded again: the last row stands
            grade("2026-10-08T10:01:00-04:00", graded=False)]                        # not graded yet: left out
    got = _run("console.log(JSON.stringify(callDays(D.rows).map(function(d){ return [d.day, d.calls.map(function(c){"
               " return c.row_ts.slice(11, 16) + ' ' + c.verdict; }), rightOf(d.calls), dayName(d.day)]; })));", {"rows": rows})
    assert got == [["2026-10-08", ["15:00 wrong", "14:31 right", "09:30 right"], "2 of 3 right", "Thu 10-08"],
                   ["2026-10-07", ["15:29 right"], "1 of 1 right", "Wed 10-07"]]


def test_a_call_row_says_the_time_where_it_went_what_was_called_and_the_grade_and_opens_to_the_owners_numbers():
    """10-08 15:00 on the owner's table: price 7,754.82, flat ±2.2, low −2.3, average +2.8, high +11.1, went up, called
    flat, wrong. The time is the viewer's (12:00 in Los Angeles); the numbers wait behind a tap."""
    got = _run("var p = callRow(D.c); console.log(JSON.stringify([dump(p[0]), dump(p[1]), p[1].hidden]));", {"c": OCT8_1500})
    row, nums, hidden = got
    kids = row["kids"]
    assert [k["attrs"].get("class") for k in kids] == ["t", "w", None, "grade"] and kids[2]["tag"] == "svg"
    assert _flat_text(kids[0]) == "12:00" and _flat_text(kids[1]) == "Upcalled flat" and kids[1]["kids"][0]["attrs"]["class"] == "up"
    assert _flat_text(kids[3]) == "Wrong" and row["attrs"]["aria-expanded"] == "false"
    assert _flat_text(nums) == "Low −2.3 · average +2.8 · high +11.1 ptsCalled at 7,754.82 · flat within ±2.2"
    assert hidden is True
    right = _run("console.log(JSON.stringify(dump(callRow(D.c)[0].kids[3])));", {"c": OCT8_1431})
    assert right["attrs"]["class"] == "grade ok" and right["text"] == "Right"


def test_the_picture_puts_the_window_against_the_call_on_one_scale_and_rests_a_big_move_at_the_end():
    """On a 104-wide strip, 32 points either side: the call's tick in the middle, the flat band a box, low to high a
    line, the graded average a dot in where-it-went's colour; a move past 32 points rests at the end."""
    got = _run("console.log(JSON.stringify([dump(strip(D.a)), dump(strip(D.b))]));",
               {"a": OCT8_1500, "b": grade("2026-10-08T12:31:00-04:00", g=-40.0, worst={"points": -55.0}, best={"points": 2.8}, label="down")})
    a, b = got
    line, box, whisker, tick, dot = a["kids"]
    per = (52 - 5) / 32
    assert float(box["attrs"]["x"]) == pytest.approx(52 - 2.24 * per, abs=0.06) and float(box["attrs"]["width"]) == pytest.approx(2 * 2.24 * per, abs=0.06)
    assert float(whisker["attrs"]["x1"]) == pytest.approx(52 - 2.27 * per, abs=0.06) and float(whisker["attrs"]["x2"]) == pytest.approx(52 + 11.05 * per, abs=0.06)
    assert tick["attrs"]["x1"] == "52" and float(dot["attrs"]["cx"]) == pytest.approx(52 + 2.75 * per, abs=0.06) and dot["attrs"]["fill"] == "#2F44B8"
    assert a["attrs"]["aria-label"] == "Low −2.3, average +2.8, high +11.1 points; flat within 2.2"
    assert b["kids"][2]["attrs"]["x1"] == "4.0" and b["kids"][4]["attrs"]["cx"] == "4.0" and b["kids"][4]["attrs"]["fill"] == "#A8492F"


def test_a_day_folds_to_its_score_and_squares_oldest_first_and_opens_to_its_calls():
    d = {"day": "2026-10-08", "calls": [OCT8_1500, OCT8_1431, grade("2026-10-08T09:30:00-04:00", verdict="wrong", label="up")]}
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


def test_the_summary_counts_the_last_five_days():
    days = [{"day": f"2026-10-0{n}", "calls": [grade(f"2026-10-0{n}T10:00:00-04:00", verdict=v) for v in vs]}
            for n, vs in ((9, ["right", "wrong"]), (8, ["wrong"]), (7, ["right"]), (6, ["right"]), (5, ["wrong"]), (2, ["right"]))]
    got = _run("console.log(JSON.stringify(dump(summary(D.days))));", {"days": days})
    assert _flat_text(got["kids"][0]) == "Last 5 days3 of 6 right"                    # 10-02 is the sixth day: left out
    assert _flat_text(got["kids"][1]) == "A filled square is a right call, oldest on the left. Tap a day for its calls."


def test_the_page_reads_the_grades_through_the_raw_route_and_keeps_an_opened_call_open():
    assert _var("GRADES_URL").strip() == "var GRADES_URL = '/api/raw/file?root=state&path=spx_jev/integral_grades.jsonl&limit=400';"
    assert _var("DAYS_SUMMED").strip() == "var DAYS_SUMMED = 5, SCALE_PTS = 32, POLL_MS = 60000;"
    assert "if(key === drawn && !failed) return;" in _fn("poll")                       # the same list leaves the page alone
    assert "d.day in opened ? opened[d.day] : i === 0" in _fn("draw")                 # the newest day opens, a hand choice stays
    assert "Fetch failed, showing the last list" in _fn("draw")


def test_the_tab_bar_lights_reads_and_every_page_links_it():
    nav = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', READS).group(1)
    assert re.findall(r'<a class="tab" href="([^"]+)"', nav) == ["/m/", "/m/jev.html"]
    assert nav.count('<span class="tab on">') == 1 and "<s>READS</s>" in nav and "<s>FORECAST</s>" in nav
    for page in ("index.html", "jev.html", "jev-spx.html"):
        tabs = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', (M / page).read_text()).group(1)
        assert 'href="/m/reads.html"' in tabs and "thread.html" not in tabs, page


def test_the_page_fits_the_owners_360px_phone_and_keeps_the_house_rules():
    """At 360 a call row's four columns fit the card: the time, the words (\"Down\" over \"called down\", about 70px
    at 11.5px), the 104-wide picture and the grade chip, with 8px between."""
    card = 360 - 2 * 16 - 2 * 14
    cols = re.search(r"\.call\{display:grid;grid-template-columns:40px minmax\(0,1fr\) 104px 52px;gap:8px;", READS)
    assert cols and card - (40 + 104 + 52 + 3 * 8) >= 75
    assert "cursor:pointer" not in READS and not re.search(r"[\U0001F300-\U0001FAFF]", READS)
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", READS)))
    assert not re.search(r"\d(?:vh|dvh|svh|lvh|vw)\b", css)
    assert ".innerHTML" not in JS
