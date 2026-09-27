"""The tape and flow family's labels over the station's own sources: the lob-flow collector's raw 0DTE tape."""
from __future__ import annotations

import gzip
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import DAY, at, flat_bars
from spx_jev.labels.tape_flow import build_tape_flow_labels

PRIOR_DAYS = ("2026-09-17", "2026-09-16", "2026-09-15", "2026-09-14", "2026-09-11", "2026-09-10")


def trade(t: datetime, right: str = "call", side: str = "mid", size: int = 1, condition: int = 18, strike: float = 7700.0) -> dict:
    """One tape line as the lob-flow collector writes it: a 5.00 quote, printed at the ask (bought), the bid
    (sold) or the mid."""
    price = {"bought": 5.1, "sold": 4.9, "mid": 5.0}[side]
    return {"ts_ms": int(t.timestamp() * 1000), "strike": strike, "right": right, "price": price, "size": size,
            "bid": 4.9, "ask": 5.1, "bid_size": 12, "ask_size": 9, "condition": condition}


def every_minute(day: str, until: datetime, **kw) -> list[dict]:
    """One trade 20 seconds into every minute from the open until ``until``."""
    out, t = [], at(9, 30, day, ss=20)
    while t < until:
        out.append(trade(t, **kw))
        t += timedelta(minutes=1)
    return out


def write_tape(root: Path, day: str, lines: list[dict], packed: bool = False) -> None:
    """The day's tape file, newest line first: the collector's lines land out of time order."""
    folder = root / "lob_flow" / "raw" / day
    folder.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(x) + "\n" for x in reversed(lines))
    text += '{"gap": true, "ts": "%sT11:00:00-04:00", "reason": "HTTPStatusError"}\n' % day
    if packed:
        with gzip.open(folder / "tape.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        (folder / "tape.jsonl").write_text(text)


def lean_day(day: str, calls: int, puts: int, until: datetime) -> list[dict]:
    """Every minute a call bought of ``calls`` lots and a put bought of ``puts`` lots."""
    return every_minute(day, until, side="bought", size=calls) + every_minute(day, until, right="put", side="bought", size=puts)


def tape_scene(scene_factory, root: Path, now: datetime, today: list[dict], prior: dict[str, list[dict]] | None = None):
    write_tape(root, DAY, today)
    for k, (day, lines) in enumerate((prior or {}).items()):
        write_tape(root, day, lines, packed=k % 2 == 0)
    scene = scene_factory(now, flat_bars(int((now - at(9, 30)).total_seconds() // 60)),
                          prior_bars={d: flat_bars(390, day=d) for d in PRIOR_DAYS})
    return replace(scene, state_dir=root)


def read(scene):
    ls = build_tape_flow_labels(scene)
    return ls, {f"{g}.{k}": v for g, labels in ls.state.items() for k, v in labels.items()}


# ---- options.big_prints_10

NOW = at(10, 0, ss=5)


def big(minute: int, right: str, side: str, size: int = 100, **kw) -> dict:
    return trade(at(9, minute, ss=40), right=right, side=side, size=size, **kw)


@pytest.mark.parametrize("prints, sentence", [
    ([big(51, "call", "bought"), big(52, "call", "bought"), big(53, "put", "sold"), big(55, "call", "bought", 150),
      big(58, "put", "bought")],
     "over the last 10 minutes 5 single 0DTE trades of 100 lots or more printed, at least the 5-trade minimum; "
     "82% of their premium whose side could be told was calls bought or puts sold, past the 65% lean line"),
    ([big(51, "put", "bought"), big(52, "put", "bought"), big(53, "call", "sold"), big(55, "put", "bought"), big(58, "call", "bought")],
     "over the last 10 minutes 5 single 0DTE trades of 100 lots or more printed, at least the 5-trade minimum; "
     "80% of their premium whose side could be told was puts bought or calls sold, past the 65% lean line"),
    ([big(51, "put", "bought"), big(52, "put", "bought"), big(53, "call", "bought"), big(55, "call", "bought"), big(58, "call", "mid")],
     "over the last 10 minutes 5 single 0DTE trades of 100 lots or more printed, at least the 5-trade minimum; "
     "neither side passed the 65% lean line: 50% of their premium whose side could be told was calls bought or puts sold "
     "and 50% puts bought or calls sold"),
    ([big(51, "call", "bought"), big(52, "call", "bought"), big(53, "call", "bought"), big(55, "call", "bought", 99)],
     "over the last 10 minutes 3 single 0DTE trades of 100 lots or more printed, fewer than the 5-trade minimum"),
])
def test_big_prints_count_the_single_large_trades_of_the_last_10_minutes_and_their_lean(scene_factory, tmp_path, prints, sentence):
    around = [big(49, "call", "bought"),                                              # before the window
              big(56, "put", "bought", condition=130),                                # one leg of a package, no single trade
              trade(at(10, 0, ss=2), side="bought", size=500)]                        # the minute still running
    scene = tape_scene(scene_factory, tmp_path, NOW, every_minute(DAY, NOW) + prints + around)
    assert read(scene)[1]["options.big_prints_10"] == sentence


def test_the_tape_labels_are_omitted_without_a_tape_or_a_running_collector(scene_factory, tmp_path):
    scene = tape_scene(scene_factory, tmp_path, NOW, every_minute(DAY, NOW))
    ls, _ = read(replace(scene, state_dir=None))
    assert ls.omitted["options.big_prints_10"] == "no state folder to read the lob-flow collector's 0DTE tape from"
    assert ls.gates["opening_premium_burst"] == "no state folder to read the lob-flow collector's 0DTE tape from"
    ls, _ = read(replace(scene, now=at(10, 0, "2026-09-21")))
    assert ls.omitted["options.premium_pace_30"] == "no lob-flow tape for 2026-09-21 under lob_flow/raw: the collector did not run"
    stopped = tape_scene(scene_factory, tmp_path, NOW, every_minute(DAY, at(9, 57)))
    ls, _ = read(stopped)
    assert ls.omitted["options.flow_lean_30"] == "no 0DTE trade in the lob-flow tape in the last 3 minutes: the collector stopped"


def test_a_window_is_omitted_when_the_session_is_younger_or_the_tape_has_a_hole(scene_factory, tmp_path):
    ls, _ = read(tape_scene(scene_factory, tmp_path, at(9, 35, ss=5), every_minute(DAY, at(9, 36))))
    assert ls.omitted["options.big_prints_10"] == "the session is 5 minutes old, under the 10-minute window"
    holed = [x for x in every_minute(DAY, NOW) if not x["ts_ms"] == int(at(9, 53, ss=20).timestamp() * 1000)]
    ls, _ = read(tape_scene(scene_factory, tmp_path, NOW, holed))
    assert ls.omitted["options.big_prints_10"] == "the lob-flow tape has a minute with no trade in the last 10 minutes: the collector missed it"


# ---- options.premium_burst_5m, options.premium_pace_30 and the opening burst gate

def paced(size: int, day: str = DAY, until: datetime = NOW) -> list[dict]:
    return every_minute(day, until, side="bought", size=size)


@pytest.mark.parametrize("size, beaten, pace, burst", [
    (9, 6, "in the top fifth", "in the top fifth"),
    (4, 2, "between the bottom and top fifths", "under the top fifth"),
    (1, 0, "in the bottom fifth", "under the top fifth"),
])
def test_premium_is_ranked_against_the_same_minutes_of_the_prior_sessions(scene_factory, tmp_path, size, beaten, pace, burst):
    prior = {d: paced(2 + k, d, at(16, 0, d)) for k, d in enumerate(PRIOR_DAYS)}      # 2 to 7 lots a minute
    later = [trade(at(10, 0, ss=30), side="sold", size=5000)]                         # after the read: never counted
    ls, labels = read(tape_scene(scene_factory, tmp_path, NOW, paced(size) + later, prior))
    rank = f"higher than {beaten} of the last 6 sessions at this minute"
    assert labels["options.premium_pace_30"] == f"0DTE premium traded in the last 30 minutes is {pace} for this half hour, {rank}"
    no_burst = "" if burst == "in the top fifth" else ", so it was no burst"
    assert labels["options.premium_burst_5m"] == (
        f"in the last 5 minutes 0DTE premium traded was {burst} for these minutes, {rank}{no_burst}; "
        "100% of its premium whose side could be told was calls bought or puts sold, past the 65% lean line")
    assert ls.gates["opening_premium_burst"] == (
        None if not no_burst else f"the last 5 minutes' 0DTE premium is under the top fifth for these minutes, {rank}")


def test_premium_ranks_need_five_prior_sessions_with_a_whole_tape_at_this_minute(scene_factory, tmp_path):
    prior = {d: paced(2, d, at(16, 0, d)) for d in PRIOR_DAYS[:4]}
    prior[PRIOR_DAYS[4]] = paced(2, PRIOR_DAYS[4], at(9, 45, PRIOR_DAYS[4]))        # its collector stopped before this minute
    ls, _ = read(tape_scene(scene_factory, tmp_path, NOW, paced(3), prior))
    assert ls.omitted["options.premium_pace_30"] == "needs 5 prior sessions with a lob-flow tape at this minute, have 4"
    assert ls.gates["opening_premium_burst"] == "needs 5 prior sessions with a lob-flow tape at this minute, have 4"


# ---- options.flow_lean_30

@pytest.mark.parametrize("calls, puts, sentence", [
    (10, 3, "leaned toward buying calls and selling puts by 0.47 of signed premium above its usual level for this half hour, "
            "higher than 6 of the last 6 sessions at this minute, at or past the 80% rank line"),
    (1, 6, "leaned toward buying puts and selling calls by 0.79 of signed premium below its usual level for this half hour, "
           "higher than 0 of the last 6 sessions at this minute, at or under the 20% rank line"),
    (5, 4, "leaned toward buying calls and selling puts by 0.04 of signed premium above its usual level for this half hour, "
           "higher than 3 of the last 6 sessions at this minute, between the 20% and 80% rank lines, no lean"),
])
def test_the_flow_lean_is_the_signed_premium_beyond_its_usual_level_for_the_half_hour(scene_factory, tmp_path, calls, puts, sentence):
    # the prior sessions lean -0.50, -0.20, 0.00, +0.14, +0.25 and +0.33: a usual level of +0.07
    prior = {d: lean_day(d, 1 + k, 3, at(16, 0, d)) for k, d in enumerate(PRIOR_DAYS)}
    _, labels = read(tape_scene(scene_factory, tmp_path, NOW, lean_day(DAY, calls, puts, NOW), prior))
    assert labels["options.flow_lean_30"] == f"over the last 30 minutes the 0DTE tape {sentence}"
