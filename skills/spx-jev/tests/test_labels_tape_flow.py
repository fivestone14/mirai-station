"""The tape and flow family's labels over the station's own sources: the lob-flow collector's raw 0DTE tape. Every
verdict is a rank against the same minute on the prior sessions, which needs 10 of them."""
from __future__ import annotations

import gzip
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import DAY, at, flat_bars, make_row
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.labels.tape_flow import build_tape_flow_labels

PRIOR_DAYS = ("2026-09-17", "2026-09-16", "2026-09-15", "2026-09-14", "2026-09-11", "2026-09-10", "2026-09-09", "2026-09-08",
              "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01")


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


def write_tape(root: Path, day: str, lines: list[dict], packed: bool = False, late: list[dict] = ()) -> None:
    """The day's tape file, minute by minute but newest line first within each minute, as the collector's lines land
    out of time order; then the ``late`` lines, journaled after the rest."""
    folder = root / "lob_flow" / "raw" / day
    folder.mkdir(parents=True, exist_ok=True)
    journaled = sorted(lines, key=lambda x: (x["ts_ms"] // 60_000, -x["ts_ms"])) + list(late)
    text = "".join(json.dumps(x) + "\n" for x in journaled)
    text += '{"gap": true, "ts": "%sT11:00:00-04:00", "reason": "HTTPStatusError"}\n' % day
    if packed:
        with gzip.open(folder / "tape.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        (folder / "tape.jsonl").write_text(text)


def lean_day(day: str, calls: int, puts: int, until: datetime) -> list[dict]:
    """Every minute a call bought of ``calls`` lots and a put bought of ``puts`` lots."""
    return every_minute(day, until, side="bought", size=calls) + every_minute(day, until, right="put", side="bought", size=puts)


def tape_scene(scene_factory, root: Path, now: datetime, today: list[dict], prior: dict[str, list[dict]] | None = None,
               late: list[dict] = ()):
    write_tape(root, DAY, today, late=late)
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


def big(minute: int, right: str, side: str, size: int = 100, day: str = DAY, **kw) -> dict:
    return trade(at(9, minute, day, ss=40), right=right, side=side, size=size, **kw)


def big_day(day: str, bullish: int, size: int = 100) -> list[dict]:
    """A prior session's tape: a 1-lot trade every minute, and 5 trades of ``size`` lots between 09:51 and 09:55,
    ``bullish`` of them calls bought and the rest puts bought. Pooled over 12 sessions, the largest 0.1% of the
    single trades in 09:50-10:00 are the ``size``-lot ones."""
    return every_minute(day, at(16, 0, day)) + [big(51 + i, "call" if i < bullish else "put", "bought", size, day) for i in range(5)]


# the prior sessions' large trades lean 0%, 20%, 40%, 60%, 80% and 100% bullish, twice each
BIG_PRIOR = {d: big_day(d, k % 6) for k, d in enumerate(PRIOR_DAYS)}


@pytest.mark.parametrize("prints, sentence", [
    ([big(51, "call", "bought"), big(52, "call", "bought"), big(53, "put", "sold"), big(55, "call", "bought", 150),
      big(58, "put", "bought")],
     "5 single near-price 0DTE trades of 100 lots or more printed, the largest 0.1% of single trades in these minutes on the last "
     "12 sessions, at least the 5-trade minimum; 82% of their premium whose side could be told was calls bought or puts sold, "
     "higher than 10 of the last 12 sessions at this minute, top third: a bullish lean"),
    ([big(51, "put", "bought"), big(52, "put", "bought"), big(53, "call", "sold"), big(55, "put", "bought"), big(58, "put", "bought")],
     "5 single near-price 0DTE trades of 100 lots or more printed, the largest 0.1% of single trades in these minutes on the last "
     "12 sessions, at least the 5-trade minimum; 0% of their premium whose side could be told was calls bought or puts sold, "
     "higher than 0 of the last 12 sessions at this minute, bottom third: a bearish lean"),
    ([big(51, "put", "bought"), big(52, "put", "bought"), big(53, "call", "bought"), big(55, "call", "bought"), big(58, "call", "mid")],
     "5 single near-price 0DTE trades of 100 lots or more printed, the largest 0.1% of single trades in these minutes on the last "
     "12 sessions, at least the 5-trade minimum; 50% of their premium whose side could be told was calls bought or puts sold, "
     "higher than 6 of the last 12 sessions at this minute, middle third: no lean"),
    ([big(51, "call", "bought"), big(52, "call", "bought"), big(53, "call", "bought"), big(55, "call", "bought", 99)],
     "3 single near-price 0DTE trades of 100 lots or more printed, the largest 0.1% of single trades in these minutes on the last "
     "12 sessions, fewer than the 5-trade minimum"),
    ([big(51, "call", "mid"), big(52, "put", "mid"), big(53, "call", "mid"), big(55, "put", "mid"), big(58, "call", "mid")],
     "5 single near-price 0DTE trades of 100 lots or more printed, the largest 0.1% of single trades in these minutes on the last "
     "12 sessions, at least the 5-trade minimum; the side of none of their premium could be told: no lean"),
])
def test_big_prints_count_the_largest_single_trades_of_the_last_10_minutes_and_rank_their_lean(scene_factory, tmp_path, prints, sentence):
    around = [big(49, "call", "bought"),                                              # before the window
              big(56, "put", "bought", condition=130),                                # one leg of a package, no single trade
              trade(at(10, 0, ss=2), side="bought", size=500)]                        # the minute still running
    scene = tape_scene(scene_factory, tmp_path, NOW, every_minute(DAY, NOW) + prints + around, BIG_PRIOR)
    assert read(scene)[1]["options.big_prints_10"] == f"over the last 10 minutes {sentence}"


def test_a_large_trade_is_sized_by_the_prior_sessions_never_by_a_fixed_lot_count(scene_factory, tmp_path):
    prior = {d: big_day(d, k % 6, size=40) for k, d in enumerate(PRIOR_DAYS)}
    prints = [big(51 + i, "call", "bought", 45) for i in range(4)] + [big(57, "put", "bought", 40)]
    _, labels = read(tape_scene(scene_factory, tmp_path, NOW, every_minute(DAY, NOW) + prints, prior))
    assert labels["options.big_prints_10"] == (
        "over the last 10 minutes 5 single near-price 0DTE trades of 40 lots or more printed, the largest 0.1% of single trades in "
        "these minutes on the last 12 sessions, at least the 5-trade minimum; 82% of their premium whose side could be told was calls "
        "bought or puts sold, higher than 10 of the last 12 sessions at this minute, top third: a bullish lean")


def test_big_prints_need_ten_prior_sessions_to_size_a_large_trade_and_rank_its_lean(scene_factory, tmp_path):
    prints = [big(51 + i, "call", "bought") for i in range(5)]
    nine = {d: BIG_PRIOR[d] for d in PRIOR_DAYS[:9]}
    ls, _ = read(tape_scene(scene_factory, tmp_path / "nine", NOW, every_minute(DAY, NOW) + prints, nine))
    assert ls.omitted["options.big_prints_10"] == "its large-trade size needs 10 prior sessions with a whole lob-flow tape at this minute, have 9"
    few = {d: BIG_PRIOR[d] if k < 9 else every_minute(d, at(16, 0, d)) + [big(51, "call", "bought", 100, d)]     # three with one large trade
           for k, d in enumerate(PRIOR_DAYS)}
    ls, _ = read(tape_scene(scene_factory, tmp_path / "few", NOW, every_minute(DAY, NOW) + prints, few))
    assert ls.omitted["options.big_prints_10"] == "its rank needs 10 prior sessions with 5 or more large trades at this minute, have 9"


def test_a_trade_journaled_after_a_line_stamped_past_the_read_was_not_on_file_yet(scene_factory, tmp_path):
    prints = [big(51, "call", "bought"), big(52, "call", "bought"), big(53, "call", "bought"), big(54, "call", "bought"),
              big(55, "call", "bought")]
    catch_up = [trade(at(10, 0, ss=10)), big(57, "put", "bought"), big(58, "put", "bought")]   # printed before the read, journaled after
    scene = tape_scene(scene_factory, tmp_path, NOW, every_minute(DAY, NOW) + prints, BIG_PRIOR, late=catch_up)
    assert read(scene)[1]["options.big_prints_10"] == (
        "over the last 10 minutes 5 single near-price 0DTE trades of 100 lots or more printed, the largest 0.1% of single trades in "
        "these minutes on the last 12 sessions, at least the 5-trade minimum; 100% of their premium whose side could be told was calls "
        "bought or puts sold, higher than 10 of the last 12 sessions at this minute, top third: a bullish lean")


def at_mid(minute: int, right: str, bid: float, ask: float) -> dict:
    """A big print at the mid of a quote whose mid is not exact in floats."""
    return {**big(minute, right, "mid"), "price": round((bid + ask) / 2, 2), "bid": bid, "ask": ask}


def test_a_print_at_the_mid_has_no_side_whatever_the_float_rounding(scene_factory, tmp_path):
    prints = [big(51, "call", "bought"), big(52, "call", "bought"), big(53, "call", "bought"), big(54, "put", "bought"),
              big(55, "put", "bought"), at_mid(56, "call", 5.6, 5.8), at_mid(57, "put", 2.35, 2.45)]
    scene = tape_scene(scene_factory, tmp_path, NOW, every_minute(DAY, NOW) + prints, BIG_PRIOR)
    assert read(scene)[1]["options.big_prints_10"] == (
        "over the last 10 minutes 7 single near-price 0DTE trades of 100 lots or more printed, the largest 0.1% of single trades in "
        "these minutes on the last 12 sessions, at least the 5-trade minimum; 60% of their premium whose side could be told was calls "
        "bought or puts sold, higher than 6 of the last 12 sessions at this minute, middle third: no lean")


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


# the prior sessions trade 5 to 16 lots a minute, 2 to 13 of them calls bought and 3 puts bought: 40% to 81% bullish
LEAN_PRIOR = {d: lean_day(d, 2 + k, 3, at(16, 0, d)) for k, d in enumerate(PRIOR_DAYS)}


@pytest.mark.parametrize("size, beaten, pace, burst", [
    (15, 10, "in the top fifth", "in the top fifth"),
    (10, 5, "between the top and bottom fifths", "under the top fifth"),
    (1, 0, "in the bottom fifth", "under the top fifth"),
])
def test_premium_is_ranked_against_the_same_minutes_of_the_prior_sessions(scene_factory, tmp_path, size, beaten, pace, burst):
    later = [trade(at(10, 0, ss=30), side="sold", size=5000)]                         # after the read: never counts
    ls, labels = read(tape_scene(scene_factory, tmp_path, NOW, paced(size) + later, LEAN_PRIOR))
    rank = f"higher than {beaten} of the last 12 sessions at this minute"
    assert labels["options.premium_pace_30"] == f"near-price 0DTE premium traded in the last 30 minutes is {pace} for this half hour, {rank}"
    no_burst = "" if burst == "in the top fifth" else ", so it was no burst"
    assert labels["options.premium_burst_5m"] == (
        f"in the last 5 minutes near-price 0DTE premium traded was {burst} for these minutes, {rank}{no_burst}; "
        "100% of its premium whose side could be told was calls bought or puts sold, higher than 12 of the last 12 sessions "
        "at this minute, top third: a bullish lean")
    assert ls.gates["opening_premium_burst"] == (
        None if not no_burst else f"the last 5 minutes' near-price 0DTE premium is under the top fifth for these minutes, {rank}")


@pytest.mark.parametrize("calls, puts, sentence", [
    (5, 12, "29% of its premium whose side could be told was calls bought or puts sold, higher than 0 of the last 12 sessions "
            "at this minute, bottom third: a bearish lean"),
    (14, 8, "64% of its premium whose side could be told was calls bought or puts sold, higher than 4 of the last 12 sessions "
           "at this minute, middle third: no lean"),
])
def test_a_bursts_lean_is_ranked_against_the_same_minutes_lean_on_the_prior_sessions(scene_factory, tmp_path, calls, puts, sentence):
    ls, labels = read(tape_scene(scene_factory, tmp_path, NOW, lean_day(DAY, calls, puts, NOW), LEAN_PRIOR))
    assert labels["options.premium_burst_5m"] == (
        f"in the last 5 minutes near-price 0DTE premium traded was in the top fifth for these minutes, "
        f"higher than 12 of the last 12 sessions at this minute; {sentence}")
    assert ls.gates["opening_premium_burst"] is None


def test_a_multi_leg_trade_counts_as_premium_traded_but_has_no_side(scene_factory, tmp_path):
    packages = every_minute(DAY, NOW, side="sold", size=20, condition=130)             # legs of packages, at the bid
    _, labels = read(tape_scene(scene_factory, tmp_path, NOW, paced(1) + packages, LEAN_PRIOR))
    rank = "higher than 12 of the last 12 sessions at this minute"
    assert labels["options.premium_pace_30"] == f"near-price 0DTE premium traded in the last 30 minutes is in the top fifth for this half hour, {rank}"
    assert labels["options.premium_burst_5m"] == (
        f"in the last 5 minutes near-price 0DTE premium traded was in the top fifth for these minutes, {rank}; "
        f"100% of its premium whose side could be told was calls bought or puts sold, {rank}, top third: a bullish lean")


def test_premium_ranks_need_ten_prior_sessions_with_a_whole_tape_at_this_minute(scene_factory, tmp_path):
    prior = {d: paced(2, d, at(16, 0, d)) for d in PRIOR_DAYS[:9]}
    prior[PRIOR_DAYS[9]] = paced(2, PRIOR_DAYS[9], at(9, 45, PRIOR_DAYS[9]))        # its collector stopped before this minute
    ls, _ = read(tape_scene(scene_factory, tmp_path, NOW, paced(3), prior))
    assert ls.omitted["options.premium_pace_30"] == "its rank needs 10 prior sessions with a whole lob-flow tape at this minute, have 9"
    assert ls.gates["opening_premium_burst"] == "its rank needs 10 prior sessions with a whole lob-flow tape at this minute, have 9"


# ---- options.flow_lean_30

@pytest.mark.parametrize("calls, puts, sentence", [
    (12, 1, "leaned toward buying calls and selling puts by 0.42 of signed premium above its usual level for this half hour, "
            "higher than 12 of the last 12 sessions at this minute, at or past the 80% rank line"),
    (1, 6, "leaned toward buying puts and selling calls by 1.14 of signed premium below its usual level for this half hour, "
           "higher than 0 of the last 12 sessions at this minute, at or under the 20% rank line"),
    (7, 2, "leaned toward buying calls and selling puts by 0.13 of signed premium above its usual level for this half hour, "
           "higher than 9 of the last 12 sessions at this minute, between the 20% and 80% rank lines, no lean"),
])
def test_the_flow_lean_is_the_signed_premium_beyond_its_usual_level_for_the_half_hour(scene_factory, tmp_path, calls, puts, sentence):
    # the prior sessions lean -0.20 to +0.63: a usual level of +0.43
    _, labels = read(tape_scene(scene_factory, tmp_path, NOW, lean_day(DAY, calls, puts, NOW), LEAN_PRIOR))
    assert labels["options.flow_lean_30"] == f"over the last 30 minutes the near-price 0DTE tape {sentence}"


def test_the_flow_lean_is_omitted_when_no_side_could_be_told_or_without_ten_prior_sessions(scene_factory, tmp_path):
    ls, _ = read(tape_scene(scene_factory, tmp_path / "mid", NOW, every_minute(DAY, NOW)))                  # every trade at the mid
    assert ls.omitted["options.flow_lean_30"] == "the side of no near-price 0DTE trade in the last 30 minutes could be told"
    prior = {d: LEAN_PRIOR[d] for d in PRIOR_DAYS[:9]}
    ls, _ = read(tape_scene(scene_factory, tmp_path / "nine", NOW, lean_day(DAY, 5, 4, NOW), prior))
    assert ls.omitted["options.flow_lean_30"] == "its rank needs 10 prior sessions with a whole lob-flow tape at this minute, have 9"


# ---- options.call_put_shift_10m

BOOK = [[7650.0, 300, 2700], [7700.0, 4500, 4500], [7725.0, 3000, 2000], [7750.0, 4000, 2000], [7800.0, 1500, 500]]   # 13,300 calls, 11,700 puts


def traded(calls: int, puts: int, strike: float = 7700.0) -> dict:
    """The row's gex_views with ``calls`` and ``puts`` more contracts traded at ``strike`` than BOOK."""
    gv = make_row(NOW, 7700.0)["gex_views"]
    return {**gv, "vol_side_by_strike": [[k, c + calls, p + puts] if k == strike else [k, c, p] for k, c, p in BOOK]}


def write_diary(root: Path, day: str, rows: list[dict]) -> None:
    (root / "reversion").mkdir(parents=True, exist_ok=True)
    (root / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def shift_scene(scene_factory, root: Path, calls: int, puts: int, then: datetime = at(9, 50), prior_days: tuple[str, ...] = PRIOR_DAYS):
    for k, d in enumerate(prior_days):          # 850 new contracts in the 10 minutes to 10:00, 18% to 82% of them calls
        write_diary(root, d, [make_row(at(9, 50, d), 7700.0, gex_views=traded(0, 0)),
                              make_row(at(10, 0, d), 7700.0, gex_views=traded(150 + 50 * k, 700 - 50 * k)),
                              make_row(at(10, 1, d), 7700.0, gex_views=traded(90000, 0))])      # after the minute: never counts
    scene = scene_factory(NOW, flat_bars(30), row_over={"gex_views": traded(calls, puts)}, rows_before=[make_row(then, 7700.0)],
                          prior_bars={d: flat_bars(390, day=d) for d in PRIOR_DAYS})
    return replace(scene, state_dir=root)


@pytest.mark.parametrize("calls, puts, sentence", [
    (700, 300, "70% of new same-day option volume was calls, against 54% since the open: a 16-point swing to calls, "
               "higher than 9 of the last 12 sessions at this minute, top third: a shift to calls for this time; "
               "new volume was above the too-thin line for this time, so it is not too thin to judge"),
    (300, 700, "30% of new same-day option volume was calls, against 52% since the open: a 22-point swing to puts, "
               "higher than 3 of the last 12 sessions at this minute, bottom third: a shift to puts for this time; "
               "new volume was above the too-thin line for this time, so it is not too thin to judge"),
    (550, 450, "55% of new same-day option volume was calls, against 53% since the open: a 2-point swing to calls, "
               "higher than 7 of the last 12 sessions at this minute, middle third: no shift for this time; "
               "new volume was above the too-thin line for this time, so it is not too thin to judge"),
    (200, 100, "67% of new same-day option volume was calls, against 53% since the open: a 14-point swing to calls, "
               "higher than 9 of the last 12 sessions at this minute, top third: a shift to calls for this time; "
               "new volume was under the too-thin line for this time, higher than 0 of the last 12 sessions at this minute, too thin to judge"),
])
def test_the_swing_to_calls_of_the_last_10_minutes_new_volume_is_ranked_at_this_minute(scene_factory, tmp_path, calls, puts, sentence):
    _, labels = read(shift_scene(scene_factory, tmp_path, calls, puts))
    assert labels["options.call_put_shift_10m"] == f"in the last 10 minutes {sentence}"


def test_the_shift_is_omitted_without_a_row_10_minutes_back_or_ten_prior_diaries(scene_factory, tmp_path):
    ls, _ = read(shift_scene(scene_factory, tmp_path, 700, 300, then=at(9, 44)))          # the scanner paused
    assert ls.omitted["options.call_put_shift_10m"] == "no diary row with same-day volume by strike from 10 minutes ago and now"
    ls, _ = read(shift_scene(scene_factory, tmp_path, 700, 300, then=at(9, 51)))          # the day's first row, as at the open
    assert ls.omitted["options.call_put_shift_10m"] == ("the diary's first row with same-day volume by strike today came under 10 minutes ago: "
                                                         "no earlier volume to set the last 10 minutes against")
    ls, _ = read(replace(shift_scene(scene_factory, tmp_path, 700, 300), state_dir=None))
    assert ls.omitted["options.call_put_shift_10m"] == "no state folder to read the prior sessions' diaries from"
    ls, _ = read(shift_scene(scene_factory, tmp_path / "nine", 700, 300, prior_days=PRIOR_DAYS[:9]))
    assert ls.omitted["options.call_put_shift_10m"] == "its rank needs 10 prior sessions with diary rows at this minute and 10 minutes before, have 9"


# ---- options.quote_liquidity

def sweep(t: datetime, size: float | None, spread: float | None) -> dict:
    """One quote sweep line as the lob-flow collector writes it; a bucket with too few contracts carries nulls."""
    return {"ts": t.isoformat(), "buckets": {"d25_40": {"size": size, "bid_size": None if size is None else size / 2,
                                                         "ask_size": None if size is None else size / 2, "spread": spread, "n": 6},
                                              "d00_10": {"size": 700.0, "bid_size": 350.0, "ask_size": 350.0, "spread": 0.05, "n": 74}},
            "purge_hint": None, "revisions_per_s": 40.1}


def write_sweeps(root: Path, day: str, lines: list[dict], packed: bool = False) -> None:
    folder = root / "lob_flow" / "raw" / day
    folder.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(x) + "\n" for x in lines)
    if packed:
        with gzip.open(folder / "sweeps.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        (folder / "sweeps.jsonl").write_text(text)


def sweeps_until(day: str, until: datetime, size: float | None, spread: float | None) -> list[dict]:
    """A sweep 31 seconds into every minute from 09:31 until ``until``."""
    out, t = [], at(9, 31, day, ss=31)
    while t < until:
        out.append(sweep(t, size, spread))
        t += timedelta(minutes=1)
    return out


def quote_scene(scene_factory, root: Path, today: list[dict], prior_days: tuple[str, ...] = PRIOR_DAYS):
    write_sweeps(root, DAY, today + [sweep(at(10, 0, ss=31), 9000.0, 0.5)])          # after the read: never counts
    for k, d in enumerate(prior_days):                                                  # 100 to 320 contracts, 10 cents wide
        write_sweeps(root, d, sweeps_until(d, at(16, 0, d), 100.0 + 20 * k, 0.1), packed=k % 2 == 1)
    return replace(scene_factory(NOW, flat_bars(30), prior_bars={d: flat_bars(390, day=d) for d in PRIOR_DAYS}), state_dir=root)


@pytest.mark.parametrize("size, spread, prior_days, sentence", [
    (330.0, 0.1, PRIOR_DAYS, "10 cents wide, their usual width for this time, with a median 330 contracts displayed, a deep book for this time, "
                             "in the top fifth, higher than 12 of the last 12 sessions at this minute"),
    (150.0, 0.2, PRIOR_DAYS, "20 cents wide, wider than their usual 10 cents for this time, with a median 150 contracts displayed, "
                             "a middling book for this time, between the top and bottom fifths, higher than 3 of the last 12 sessions at this minute"),
    (110.0, 0.05, PRIOR_DAYS[:10], "5 cents wide, tighter than their usual 10 cents for this time, with a median 110 contracts displayed, "
                                   "a thin book for this time, in the bottom fifth, higher than 1 of the last 10 sessions at this minute"),
])
def test_quote_liquidity_ranks_the_depth_near_price_and_sets_the_width_against_its_usual(scene_factory, tmp_path, size, spread, prior_days, sentence):
    _, labels = read(quote_scene(scene_factory, tmp_path, sweeps_until(DAY, NOW, size, spread), prior_days))
    assert labels["options.quote_liquidity"] == f"over the last 5 minutes 25-40 delta same-day quotes have been {sentence}"


def test_quote_liquidity_is_omitted_when_the_sweeps_stop_thin_out_or_have_too_little_history(scene_factory, tmp_path):
    ls, _ = read(quote_scene(scene_factory, tmp_path / "stopped", sweeps_until(DAY, at(9, 56), 150.0, 0.1)))
    assert ls.omitted["options.quote_liquidity"] == "no sweep of the 0DTE quotes from the lob-flow collector in the last 3 minutes: the collector stopped"
    ls, _ = read(quote_scene(scene_factory, tmp_path / "empty", sweeps_until(DAY, NOW, None, None)))
    assert ls.omitted["options.quote_liquidity"] == "the collector's 25-40 delta bucket held too few quoted contracts over the last 5 minutes"
    ls, _ = read(quote_scene(scene_factory, tmp_path / "nine", sweeps_until(DAY, NOW, 150.0, 0.1), PRIOR_DAYS[:9]))
    assert ls.omitted["options.quote_liquidity"] == "its rank needs 10 prior sessions with lob-flow quote sweeps at this minute, have 9"
    ls, _ = read(replace(quote_scene(scene_factory, tmp_path / "none", []), now=at(10, 0, "2026-09-21")))
    assert ls.omitted["options.quote_liquidity"] == "no lob-flow quote sweeps for 2026-09-21 under lob_flow/raw: the collector did not run"


# ---- options.strike_defense and liquidity.spy_quote: the collector's record

def defense_line(t: datetime, *strikes: tuple[float, int, int]) -> dict:
    """One lob_flow reading as the collector writes it, with its refill test at each ``(strike, refilled, not refilled)``."""
    return {"ts": t.isoformat(), "engine": "lob_flow",
            "snapshot": {"regime": "long_gamma", "tilt": 0.01, "determinate_share": 0.6, "tape_trades": 327,
                         "defense": {str(k): {"strike": k, "n_events": n, "n_unrecovered": u, "per_right": {"call": f"{n} recovered / {u} not"},
                                              "refill_half_life_s": 0.4, "verdict": "defended"} for k, n, u in strikes},
                         "late_session": False, "source": "lob_flow"},
            "baseline_rows": [{"key": "size|d25_40|mid|0955|mid", "value": 150.0}]}


def spy_line(t: datetime, spread: float, size: float) -> dict:
    """One reading of SPY's quote from the collector's control."""
    return {"ts": t.isoformat(), "engine": "spy_depth",
            "baseline_rows": [{"key": "size|spy|mid|0955|mid", "value": size}, {"key": "spread|spy|mid|0955|mid", "value": spread}]}


def write_record(root: Path, day: str, lines: list[dict]) -> None:
    (root / "lob_flow" / "agg").mkdir(parents=True, exist_ok=True)
    (root / "lob_flow" / "agg" / f"{day}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))


def prior_record(day: str, k: int) -> list[dict]:
    """A prior session's record from 09:31 to 10:05, a reading a minute: SPY showing 400 + 40k shares 2 cents wide,
    and a contested strike 3(k+1) points above the flat 7700 (0.04 to 0.48 sigma), hit 20 times and refilled on 5 + k
    of them (25% to 80%)."""
    t0 = at(9, 31, day)
    return [line for i in range(35) for line in (spy_line(t0 + timedelta(minutes=i), 0.02, 400.0 + 40 * k),
                                                   defense_line(t0 + timedelta(minutes=i), (7700.0 + 3 * (k + 1), 5 + k, 15 - k)))]


def record_scene(scene_factory, root: Path, today: list[dict], prior_days: tuple[str, ...] = PRIOR_DAYS, morning: bool = True):
    write_record(root, DAY, today)
    for k, d in enumerate(prior_days):
        write_record(root, d, prior_record(d, k))
    scene = scene_factory(NOW, flat_bars(30), spot=7700.0, rows_before=[make_row(at(9, 31), 7700.0)] if morning else None,
                          prior_bars={d: flat_bars(390, day=d) for d in PRIOR_DAYS})
    return replace(scene, state_dir=root, prior_rulers={d: SigmaRuler(75.0, "anchor") for d in PRIOR_DAYS})


def readings(until: datetime, **per_minute) -> list[dict]:
    """The collector's two readings a minute from 09:31 until ``until``."""
    out, t = [], at(9, 31, ss=27)
    while t < until:
        out += [defense_line(t, *per_minute.get("strikes", ())), spy_line(t, per_minute.get("spread", 0.02), per_minute.get("size", 500.0))]
        t += timedelta(minutes=1)
    return out


@pytest.mark.parametrize("strikes, sentence", [
    ([(7709.0, 14, 2), (7701.0, 3, 1)],
     "0.12 sigma above price, farther than the nearest one on 2 of the last 12 sessions at this minute, bottom third: within reach; "
     "it was hit 16 times in the last 15 minutes, at least the 10-hit minimum; market makers refilled its quotes at price at some point "
     "in those 15 minutes on 14 of them (88%), higher than 12 of the last 12 sessions at this minute, at or above the middle: defended"),
    ([(7691.0, 5, 7), (7750.0, 40, 0)],
     "0.12 sigma below price, farther than the nearest one on 2 of the last 12 sessions at this minute, bottom third: within reach; "
     "it was hit 12 times in the last 15 minutes, at least the 10-hit minimum; market makers refilled its quotes at price at some point "
     "in those 15 minutes on 5 of them (42%), higher than 4 of the last 12 sessions at this minute, below the middle: abandoned"),
    ([(7718.0, 11, 9)],
     "0.24 sigma above price, farther than the nearest one on 5 of the last 12 sessions at this minute, middle third: within reach; "
     "it was hit 20 times in the last 15 minutes, at least the 10-hit minimum; market makers refilled its quotes at price at some point "
     "in those 15 minutes on 11 of them (55%), higher than 6 of the last 12 sessions at this minute, at or above the middle: defended"),
])
def test_strike_defense_ranks_the_nearest_contested_strikes_distance_and_refill_share(scene_factory, tmp_path, strikes, sentence):
    later = [defense_line(at(10, 0, ss=40), (7700.0, 90, 0))]                   # after the read: never counts
    _, labels = read(record_scene(scene_factory, tmp_path, readings(NOW, strikes=strikes) + later))
    assert labels["options.strike_defense"] == f"of the book's magnet and walls, the strikes the collector tests, the nearest where same-day quotes keep getting hit is {sentence}"


def test_strike_defense_is_omitted_out_of_reach_without_a_contested_strike_a_running_collector_or_ten_sessions(scene_factory, tmp_path):
    ls, _ = read(record_scene(scene_factory, tmp_path / "far", readings(NOW, strikes=[(7730.0, 30, 0), (7702.0, 5, 4)])))
    assert ls.omitted["options.strike_defense"] == ("the nearest of the book's magnet and walls hit 10 times or more is 0.40 sigma above price, "
                                                    "farther than the nearest one on 9 of the last 12 sessions at this minute, top third: out of reach")
    ls, _ = read(record_scene(scene_factory, tmp_path / "quiet", readings(NOW, strikes=[(7702.0, 5, 4)])))
    assert ls.omitted["options.strike_defense"] == "none of the book's magnet and walls, the strikes the collector tests, was hit 10 times or more in its last 15 minutes"
    ls, _ = read(record_scene(scene_factory, tmp_path / "stopped", readings(at(9, 56), strikes=[(7709.0, 14, 2)])))
    assert ls.omitted["options.strike_defense"] == "no lob-flow reading in the last 3 minutes: the collector stopped"
    ls, _ = read(record_scene(scene_factory, tmp_path / "nine", readings(NOW, strikes=[(7709.0, 14, 2)]), PRIOR_DAYS[:9]))
    assert ls.omitted["options.strike_defense"] == "its rank needs 10 prior sessions with a contested strike at this minute, have 9"
    ls, labels = read(record_scene(scene_factory, tmp_path / "late", readings(NOW, strikes=[(7709.0, 14, 2)]), morning=False))
    assert labels["options.strike_defense"].endswith("at or above the middle: defended; ruler estimated")


def test_strike_defense_sleeps_with_no_contested_strike_in_reach_but_not_on_a_data_gap(scene_factory, tmp_path):
    far, _ = read(record_scene(scene_factory, tmp_path / "far", readings(NOW, strikes=[(7730.0, 30, 0), (7702.0, 5, 4)])))
    quiet, _ = read(record_scene(scene_factory, tmp_path / "quiet", readings(NOW, strikes=[(7702.0, 5, 4)])))
    stopped, _ = read(record_scene(scene_factory, tmp_path / "stopped", readings(at(9, 56), strikes=[(7709.0, 14, 2)])))
    assert "options.strike_defense" in far.ended and "options.strike_defense" in quiet.ended
    assert "options.strike_defense" not in stopped.ended


@pytest.mark.parametrize("spread, size, prior_days, sentence", [
    (0.03, 300.0, PRIOR_DAYS, "3 cents, wider than on 12 of the last 12 sessions at this minute, top third: wide for this time; the size "
                              "showing at SPY's best bid and offer combined is in the bottom fifth for 10:00, higher than 0 of the last 12 sessions at this minute"),
    (0.01, 900.0, PRIOR_DAYS, "1 cent, wider than on 0 of the last 12 sessions at this minute, at the tight tick; the size showing at SPY's best "
                              "bid and offer combined is in the top fifth for 10:00, higher than 12 of the last 12 sessions at this minute"),
    (0.02, 520.0, PRIOR_DAYS[:10], "2 cents, wider than on 0 of the last 10 sessions at this minute, bottom third: its usual width for this time; "
                                   "the size showing at SPY's best bid and offer combined is between the bottom and top fifths for 10:00, "
                                   "higher than 3 of the last 10 sessions at this minute"),
])
def test_spy_quote_ranks_the_spread_in_whole_cents_and_the_size_at_this_minute(scene_factory, tmp_path, spread, size, prior_days, sentence):
    later = [spy_line(at(10, 0, ss=40), 0.5, 1.0)]                               # after the read: never counts
    _, labels = read(record_scene(scene_factory, tmp_path, readings(NOW, spread=spread, size=size) + later, prior_days))
    assert labels["liquidity.spy_quote"] == f"over the last 5 minutes SPY's quoted spread has been {sentence}"


def test_spy_quote_reads_the_newest_reading_already_a_5_minute_median(scene_factory, tmp_path):
    lines = readings(at(9, 59), size=300.0) + [spy_line(at(9, 59, ss=27), 0.020000000000095, 900.0)]
    _, labels = read(record_scene(scene_factory, tmp_path, lines))
    assert labels["liquidity.spy_quote"] == (
        "over the last 5 minutes SPY's quoted spread has been 2 cents, wider than on 0 of the last 12 sessions at this minute, "
        "bottom third: its usual width for this time; the size showing at SPY's best bid and offer "
        "combined is in the top fifth for 10:00, higher than 12 of the last 12 sessions at this minute")


def test_spy_quote_is_omitted_when_the_stream_stops_or_has_too_little_history(scene_factory, tmp_path):
    ls, _ = read(record_scene(scene_factory, tmp_path / "stopped", readings(at(9, 56))))
    assert ls.omitted["liquidity.spy_quote"] == "no SPY quote from the lob-flow collector in the last 3 minutes: its SPY stream stopped"
    ls, _ = read(record_scene(scene_factory, tmp_path / "nine", readings(NOW), PRIOR_DAYS[:9]))
    assert ls.omitted["liquidity.spy_quote"] == "its rank needs 10 prior sessions with the collector's SPY quote at this minute, have 9"
    ls, _ = read(replace(record_scene(scene_factory, tmp_path / "none", []), now=at(10, 0, "2026-09-21")))
    assert ls.omitted["liquidity.spy_quote"] == "no lob-flow record for 2026-09-21 under lob_flow/agg: the collector did not run"
    assert ls.omitted["options.strike_defense"] == "no lob-flow record for 2026-09-21 under lob_flow/agg: the collector did not run"


# ---- volume.spy_last30_share and volume.spy_pace_30: the siege box's SPY minute volumes

NOON = at(12, 0, ss=5)


def spy_minutes(morning: float, window: float, until: int = 960) -> dict[str, float]:
    """SPY volume by minute of day from 09:30: ``morning`` a minute until 11:30, ``window`` a minute after."""
    return {str(m): morning if m < 690 else window for m in range(570, until)}


def volume_scene(scene_factory, root: Path, today: dict[str, float], prior_days: tuple[str, ...] = PRIOR_DAYS, now: datetime = NOON):
    days = {d: spy_minutes(1000.0, 500.0 + 200 * k) for k, d in enumerate(prior_days)}   # 11.1% to 40.3% of the day in the half hour
    (root / "siege").mkdir(parents=True, exist_ok=True)
    (root / "siege" / "baseline.json").write_text(json.dumps({"days": {**days, DAY: today}}))
    scene = scene_factory(now, flat_bars(int((now - at(9, 30)).total_seconds() // 60)), prior_bars={d: flat_bars(390, day=d) for d in PRIOR_DAYS})
    return replace(scene, state_dir=root)


@pytest.mark.parametrize("window, share, pace", [
    (3000.0, "42.9% of today's volume in the last 30 minutes, higher than 12 of the last 12 sessions at this minute, top third: heavy for this time",
     "1.9 times its usual volume for this half hour, in the top fifth, higher than 12 of the last 12 sessions at this minute"),
    (1600.0, "28.6% of today's volume in the last 30 minutes, higher than 6 of the last 12 sessions at this minute, middle third: normal for this time",
     "1.0 times its usual volume for this half hour, between the top and bottom fifths, higher than 6 of the last 12 sessions at this minute"),
    (300.0, "7.0% of today's volume in the last 30 minutes, higher than 0 of the last 12 sessions at this minute, bottom third: light for this time",
     "0.2 times its usual volume for this half hour, in the bottom fifth, higher than 0 of the last 12 sessions at this minute"),
])
def test_spy_volume_is_set_against_the_same_half_hour_of_the_prior_sessions(scene_factory, tmp_path, window, share, pace):
    today = spy_minutes(1000.0, window, until=725)
    today["720"] = 900000.0                                   # the minute still running at the read: never counts
    _, labels = read(volume_scene(scene_factory, tmp_path, today))
    assert labels["volume.spy_last30_share"] == f"SPY traded {share}"
    assert labels["volume.spy_pace_30"] == f"SPY traded {pace}"


def test_spy_volume_is_omitted_without_its_minutes_a_running_feed_or_ten_sessions(scene_factory, tmp_path):
    ls, _ = read(replace(volume_scene(scene_factory, tmp_path / "none", spy_minutes(1000.0, 1000.0, 720)), state_dir=tmp_path / "empty"))
    assert ls.omitted["volume.spy_pace_30"] == "no SPY minute volumes at siege/baseline.json: the siege box has not run"
    ls, _ = read(volume_scene(scene_factory, tmp_path / "stopped", spy_minutes(1000.0, 1000.0, 714)))
    assert ls.omitted["volume.spy_last30_share"] == "no SPY minute volume from the siege box in the last 5 minutes: its feed stopped"
    holed = {m: v for m, v in spy_minutes(1000.0, 1000.0, 720).items() if not 690 <= int(m) < 706}
    ls, _ = read(volume_scene(scene_factory, tmp_path / "holed", holed))
    assert ls.omitted["volume.spy_pace_30"] == "the siege box has SPY volume for under half of the minutes since the open or of the last 30"
    ls, _ = read(volume_scene(scene_factory, tmp_path / "young", spy_minutes(1000.0, 1000.0, 590), now=at(9, 50, ss=5)))
    assert ls.omitted["volume.spy_last30_share"] == "the session is 20 minutes old, under the 30-minute window"
    ls, _ = read(volume_scene(scene_factory, tmp_path / "nine", spy_minutes(1000.0, 1000.0, 720), PRIOR_DAYS[:9]))
    assert ls.omitted["volume.spy_last30_share"] == "its rank needs 10 prior sessions with SPY minute volumes at this minute, have 9"
    assert ls.omitted["volume.spy_pace_30"] == "its rank needs 10 prior sessions with SPY minute volumes at this minute, have 9"
