"""The opening lane's strip on the JEV phone page: it shows the tape lane's own card above the live
card only while that lane runs (today's read, no older than a read can be, inside the morning window),
polls it only inside that window, and never touches the live card. The rules are pure functions in
the page; they are lifted out by name and run in node, so the test holds what they do."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

M = Path(__file__).resolve().parents[1] / "static" / "m"
JEV = (M / "jev.html").read_text()
JS = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", JEV))
_NODE = shutil.which("node")


def _fn(name):
    """One top-level function of the page's script, from its `function name(` to the next definition."""
    m = re.search(r"\n  function %s\(.*?(?=\n  (?:function |var |//|[a-z]))" % re.escape(name), JS, re.S)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _consts():
    m = re.search(r"\n  var TAPE_WINDOW = .*?;", JS)
    assert m, "the strip's constants are gone"
    return m.group(0)


def _run(js, data):
    if not _NODE:
        pytest.skip("node is not installed")
    script = "const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + _consts() + _fn("hhmm") + js
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_strip_is_its_own_card_hidden_by_the_attribute_and_reads_the_lane_card():
    tag = re.search(r'<div class="([^"]*)" id="tape"([^>]*)>', JEV)
    assert tag and tag.group(1).split() == ["card", "dashed", "strip"] and "hidden" in tag.group(2)
    assert JEV.index('id="tape"') < JEV.index('id="main"'), "the strip sits above the live card"
    assert "var TAPE_URL = '/api/raw/file?root=state&path=jev/lanes/tape/latest.json';" in JS
    assert "fetch(TAPE_URL, {cache:'no-store'})" in JS, "the lane card must never come from a cache"
    # nothing sets a display on the strip's card, so the hidden attribute alone hides it
    css = re.search(r"(?s)<style>(.*?)</style>", JEV).group(1)
    for sel in (".card", ".strip", ".card.dashed"):
        for m in re.finditer(r"(?m)^%s\{([^}]*)\}" % re.escape(sel), css):
            assert "display:" not in m.group(1), f"{sel} sets a display, which beats hidden"
    assert "b.hidden = true" in _fn("hideTape") and "box.hidden = false" in _fn("paintTape")
    # the live card's age refresher must leave the strip's own chip alone
    assert "querySelectorAll('#main .lab .r')" in _fn("ages")
    assert "opening lane reads every 5 minutes" in JEV.split('<p class="foot">', 1)[1]


def test_the_strip_polls_the_lane_card_beside_the_live_one_and_after_each_five_minute_mark():
    assert "poll(); pollTape(); armMark(); armTapeMark();" in JS
    assert "setInterval(function(){ if(!document.hidden){ poll(); pollTape(); } }, POLL_MS);" in JS
    assert "TAPE_STEP_MIN - (t.m % TAPE_STEP_MIN)" in _fn("msToNextTapeMark")
    assert "if(!inTapeWindow()){ if(tape){ tape = null; } hideTape(); return; }" in _fn("pollTape"), \
        "outside the window the strip hides and the station is not asked"
    assert "j.data.lane === 'tape'" in _fn("pollTape"), "only the lane's own card is shown"


def test_the_window_and_the_next_read_follow_the_lanes_clock():
    consts = _consts()
    assert "TAPE_WINDOW = ['09:33', '10:40']" in consts and "TAPE_MAX_AGE_MIN = 8" in consts
    assert "TAPE_STEP_MIN = 5" in consts and "TAPE_FIRST = '09:35'" in consts and "TAPE_LAST = '10:30'" in consts
    js = _fn("inTapeWindow") + _fn("nextTapeRead") + """
      console.log(JSON.stringify(D.map(function(t){ return [inTapeWindow(t), nextTapeRead(t)]; })));"""
    times = [{"h": 9, "m": 32}, {"h": 9, "m": 33}, {"h": 9, "m": 34}, {"h": 9, "m": 40}, {"h": 9, "m": 57},
             {"h": 10, "m": 26}, {"h": 10, "m": 30}, {"h": 10, "m": 39}, {"h": 10, "m": 40}, {"h": 8, "m": 0}, {"h": 15, "m": 0}]
    got = _run(js, times)
    # 09:32 is outside the window (nothing is fetched), but the next read is still 09:35
    assert got == [[False, "09:35"], [True, "09:35"], [True, "09:35"], [True, "09:45"], [True, "10:00"],
                   [True, "10:30"], [True, None], [True, None], [False, None], [False, None], [False, None]]


def test_the_strip_shows_only_todays_fresh_lane_card():
    js = _fn("tapeShows") + """
      console.log(JSON.stringify(D.map(function(c){ return tapeShows(c.t, c.age, c.today); })));"""
    today = "2026-09-28"
    lane = {"lane": "tape", "row_ts": "2026-09-28T09:40:00-04:00"}
    cases = [
        {"t": lane, "age": 2, "today": today},                                             # this morning's read
        {"t": lane, "age": 8, "today": today},                                             # a late next read: still shown
        {"t": lane, "age": 9, "today": today},                                             # older than a read can be
        {"t": {"lane": "tape", "row_ts": "2026-09-25T10:30:00-04:00"}, "age": 3, "today": today},   # Friday's card on Monday
        {"t": {"row_ts": "2026-09-28T09:40:00-04:00"}, "age": 2, "today": today},          # not the lane's card
        {"t": lane, "age": None, "today": today},                                          # no readable stamp
        {"t": lane, "age": 2, "today": None},                                              # no market calendar on this phone
        {"t": None, "age": 2, "today": today},
    ]
    assert _run(js, cases) == [True, True, False, False, False, False, False, False]


def test_the_strip_paints_the_call_the_unit_the_bands_and_the_builders_sentences():
    body = _fn("paintTape")
    for piece in ("h.views", "'a big move '", "'one tape unit $'", "'flat within $'", "'big beyond $'",
                  "st.move_since_read || om['tape.move_since_read']", "st.range_since_read",
                  "'not sent: no key on the station'", "'fetch failed, showing the last read'", "never a call"):
        assert piece in body, piece
    assert "innerHTML" not in JS
