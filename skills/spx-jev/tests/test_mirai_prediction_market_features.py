"""The code feature builder's market answerers (code_features_market.py) on synthetic data: the feed's symbols, the
daily closes, the index weights, the calendar, the diary and the quote sweeps, each with its walk-forward boundary (a
bar at the read's minute, a close dated the read's day, weights dated after the day do not count), and the loaders'
tolerance of bad lines. No host state."""
from __future__ import annotations

import gzip
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import DAY, PRIOR_DAYS, at, bars_from_closes

from spx_jev.mirai_prediction import code_feature_inputs as inputs
from spx_jev.mirai_prediction import code_features_market as market
from spx_jev.mirai_prediction.code_features import MarketHistory, answer_code_features, bars_up_to, load_catalog
from spx_jev.mirai_prediction.code_features_market import MARKET_ANSWERERS

CATALOG = {q["id"]: q for q in load_catalog()}
SIGMA = 75.0
DAYS = ("2026-09-02",) + tuple(reversed(PRIOR_DAYS))     # oldest first: eleven sessions, so a measure needing the day before has ten


def record(row_ts: str, **more) -> dict:
    return {"read_id": f"live:{row_ts}", "lane": "live", "row_ts": row_ts, "sigma": SIGMA, "labels": {}, **more}


def closes(n: int, start: float, step: float = 0.0, noise: float = 0.0, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        p += step + rng.uniform(-noise, noise)
        out.append(p)
    return out


def ctx_bars(day: str, values: list[float], volume: float = 1000.0, wick: float = 0.1) -> list[dict]:
    return [{**b, "volume": volume} for b in bars_from_closes(values, day=day, wick=wick)]


def feed_day(day: str, seed: int, n: int = 390, spx_step: float = 0.0, extra: dict[str, list[float]] | None = None) -> tuple[list[dict], dict[str, list[dict]]]:
    """One session: SPX bars that wander, and every symbol the answerers read moving with its own noise."""
    spx = bars_from_closes(closes(n, 7700.0, step=spx_step, noise=1.0, seed=seed), day=day)
    symbols = {"SPY": 770.0, "QQQ": 600.0, "IWM": 230.0, "/ZN": 110.0, "HYG": 80.0, "USO": 75.0, "GLD": 300.0, "$VIX": 15.0,
               "$VIX9D": 13.5, "$ADVN": 1500.0, "$DECN": 1300.0, "KRE": 60.0, "XHB": 100.0, "XLE": 90.0, "SPXL": 200.0,
               "TQQQ": 90.0, "SPXS": 5.0, "SQQQ": 20.0, "NVDA": 180.0, "MSFT": 500.0, "AAPL": 250.0, "AMZN": 220.0,
               "GOOGL": 250.0, "META": 700.0, "AVGO": 350.0, "TSLA": 400.0, "BRK/B": 500.0}
    context = {s: ctx_bars(day, closes(n, p, noise=p * 0.0005, seed=seed * 100 + i), volume=1000.0 + 100.0 * ((i + seed) % 7))
               for i, (s, p) in enumerate(symbols.items())}
    for s, values in (extra or {}).items():
        context[s] = ctx_bars(day, values)
    return spx, context


def carried(h: MarketHistory, extra: dict[str, list[float]] | None = None, n: int = 120) -> tuple[list[dict], dict[str, list[dict]]]:
    """A session where every symbol sits at its prior session's last close: no move anywhere, unless ``extra`` moves one."""
    prior = max(d for d in h.context_bars_by_day if d < DAY)
    spx = bars_from_closes([float(h.spx_bars_by_day[prior][-1]["close"])] * n)
    ctx = {s: ctx_bars(DAY, [float(bars[-1]["close"])] * n) for s, bars in h.context_bars_by_day[prior].items()}
    for s, values in (extra or {}).items():
        ctx[s] = ctx_bars(DAY, values)
    return spx, ctx


def history(**over) -> MarketHistory:
    spx_by_day, ctx_by_day = {}, {}
    for i, d in enumerate(DAYS):
        spx_by_day[d], ctx_by_day[d] = feed_day(d, seed=i + 1)
    return MarketHistory(spx_bars_by_day=spx_by_day, context_bars_by_day=ctx_by_day, **over)


def ask(qid: str, h: MarketHistory, today_spx: list[dict], hhmm: str = "11:30", today_ctx: dict | None = None, **more):
    row_ts = f"{DAY}T{hhmm}:30-04:00"
    if today_ctx is not None:
        h.context_bars_by_day[DAY] = today_ctx
    rec = record(row_ts, **more)
    return MARKET_ANSWERERS[qid](rec, bars_up_to(today_spx, row_ts), h, DAY, hhmm)


# ---------------------------------------------------------------- the series and the cut at the read

def test_cut_at_keeps_only_bars_finished_by_the_read():
    bars = ctx_bars(DAY, [1.0] * 5)
    assert len(market.cut_at(bars, f"{DAY}T09:33:00-04:00")) == 3 and len(market.cut_at(bars, f"{DAY}T09:32:59-04:00")) == 2
    assert [b["ts"] for b in market.cut_at(bars, f"{DAY}T09:33:00-04:00")] == [b["ts"] for b in bars_up_to(bars, f"{DAY}T09:33:00-04:00")]


def test_series_close_before_window_and_stale_price():
    s = market.Series(ctx_bars(DAY, [float(i) for i in range(60)]))      # 09:30 .. 10:29
    assert s.close_before("10:00") == 29.0 and len(s.window("10:00", 30)) == 30 and s.move_pct("10:00", 10) == pytest.approx(29.0 / 19.0 - 1)
    assert s.close_before("09:30") is None and s.close_before("10:40") is None              # nothing yet; the last bar is stale


# ---------------------------------------------------------------- the SPX path questions

def test_trend_04_clean_or_choppy_and_the_opening_window():
    h = history()
    assert ask("TREND-04", h, bars_from_closes(closes(130, 7700.0, step=0.8))) == "clean"
    assert ask("TREND-04", h, bars_from_closes([7700.0 + (i % 2) * 3.0 for i in range(130)])) == "choppy"
    assert ask("TREND-04", h, bars_from_closes(closes(31, 7700.0, step=0.8)), "10:00") == "clean"                   # since 09:35
    assert ask("TREND-04", MarketHistory(), bars_from_closes(closes(130, 7700.0, step=0.8))) is None             # no history to rank against


def test_volatility_04_compressed_or_expanded():
    h = history()
    quiet_then_wide = [7700.0 + (i % 2) * 0.2 for i in range(90)] + [7700.0 + (i % 2) * 8.0 for i in range(30)]
    assert ask("VOLATILITY-04", h, bars_from_closes(quiet_then_wide, wick=0.05)) == "expanded"
    wide_then_quiet = [7700.0] * 60 + [7700.0 + (i % 2) * 8.0 for i in range(30)] + [7708.0 - (i % 2) * 0.2 for i in range(30)]
    assert ask("VOLATILITY-04", h, bars_from_closes(wide_then_quiet, wick=0.05)) == "compressed"
    assert ask("VOLATILITY-04", h, bars_from_closes(quiet_then_wide[:50], wick=0.05), "10:20") is None            # under an hour of bars


def test_volatility_14_new_extreme_against_vix_and_mechanical_causes(tmp_path):
    wednesday = "2026-09-16"
    h = MarketHistory(calendar_path=calendar(tmp_path, []))

    def on_wednesday(spx: list[float], vix: list[float], hhmm: str = "11:30"):
        row_ts = f"{wednesday}T{hhmm}:30-04:00"
        h.context_bars_by_day[wednesday] = {"$VIX": ctx_bars(wednesday, vix)}
        return MARKET_ANSWERERS["VOLATILITY-14"](record(row_ts), bars_up_to(bars_from_closes(spx, day=wednesday), row_ts), h, wednesday, hhmm)
    rising = closes(120, 7700.0, step=0.5)
    assert on_wednesday(rising, closes(120, 16.0, step=-0.01)) == "new high, vol confirms"
    assert on_wednesday(rising, closes(120, 16.0, step=0.01)) == "new high, vol disagrees"
    assert on_wednesday(closes(120, 7700.0, step=-0.5), closes(120, 16.0, step=0.01)) == "new low, vol confirms"
    assert on_wednesday([7700.0] * 60 + [7690.0] + [7695.0] * 59, [16.0] * 120) == "no new extreme"
    assert on_wednesday(closes(340, 7700.0, step=0.5), [16.0] * 340, "15:10") == "mechanical"               # the last hour
    assert ask("VOLATILITY-14", h, bars_from_closes(rising), today_ctx={"$VIX": ctx_bars(DAY, [16.0] * 120)}) == "mechanical"   # DAY is a Friday


def test_trend_11_volume_spike_held_or_faded():
    h = history()
    spy = closes(120, 770.0)
    quiet = ctx_bars(DAY, spy, volume=1000.0)
    _, ctx = feed_day(DAY, seed=0)
    ctx["SPY"] = quiet
    flat = bars_from_closes([7700.0] * 120)
    assert ask("TREND-11", h, flat, today_ctx=ctx) == "none"
    spike = [dict(b, volume=50000.0 if "11:05" <= b["ts"][11:16] <= "11:09" else 1000.0) for b in quiet]
    ctx["SPY"] = spike
    up = bars_from_closes([7700.0] * 95 + [7710.0] * 5 + [7712.0] * 20)                     # the spike's 5 minutes rise and hold
    assert ask("TREND-11", h, up, today_ctx=ctx) == "up spike held"
    faded = bars_from_closes([7700.0] * 95 + [7710.0] * 5 + [7698.0] * 20)
    assert ask("TREND-11", h, faded, today_ctx=ctx) == "spike faded"


def test_trend_07_vwap_balance_words():
    h = history()
    _, ctx = feed_day(DAY, seed=0, extra={"SPY": closes(120, 770.0, step=0.05)})          # SPY climbs away from its VWAP all morning
    assert ask("TREND-07", h, bars_from_closes([7700.0] * 120), today_ctx=ctx) == "long one-sided"
    _, ctx = feed_day(DAY, seed=0, extra={"SPY": [770.0 + 0.02 * ((i % 2) * 2 - 1) for i in range(120)]})
    assert ask("TREND-07", h, bars_from_closes([7700.0] * 120), today_ctx=ctx) == "orbiting"


# ---------------------------------------------------------------- VIX, the curve, the gap, the daily closes

def test_volatility_12_inverted_calm_or_normal_and_the_bar_at_the_read_does_not_count():
    h = history()
    _, ctx = feed_day(DAY, seed=0, extra={"$VIX": [15.0] * 121, "$VIX9D": [13.0] * 120 + [16.5]})
    assert ask("VOLATILITY-12", h, [], "11:30", today_ctx=ctx) == "calm"                   # the 11:30 bar is not finished at 11:30:30
    _, ctx = feed_day(DAY, seed=0, extra={"$VIX": [15.0] * 121, "$VIX9D": [13.0] * 119 + [16.5] * 2})
    assert ask("VOLATILITY-12", h, [], "11:30", today_ctx=ctx) == "inverted"
    _, ctx = feed_day(DAY, seed=0, extra={"$VIX": [15.0] * 121, "$VIX9D": [13.8] * 121})
    assert ask("VOLATILITY-12", h, [], "11:30", today_ctx=ctx) == "normal"


def test_volatility_07_opening_vix_richer_or_cheaper_on_opening_reads_only():
    h = history()
    opened = bars_from_closes([7700.0] * 60)
    _, rich = feed_day(DAY, seed=0, extra={"$VIX": [18.0] * 60})
    _, cheap = feed_day(DAY, seed=0, extra={"$VIX": [12.0] * 60})
    assert ask("VOLATILITY-07", h, opened, "10:00", today_ctx=rich) == "richer"
    assert ask("VOLATILITY-07", h, opened, "10:00", today_ctx=cheap) == "cheaper"
    assert ask("VOLATILITY-07", h, opened, "11:00", today_ctx=rich) is None


def test_volatility_08_gap_in_implied_units_against_60_sessions():
    h = history()
    days = [(date.fromisoformat(DAYS[0]) - timedelta(days=i)).isoformat() for i in range(70, 0, -1)]
    for i, d in enumerate(days):
        h.spx_bars_by_day[d] = bars_from_closes(closes(390, 7700.0 + (i % 3) * 2.0, noise=0.5, seed=i), day=d)
    vix_rows = [{"day": d, "close": 16.0} for d in sorted(h.spx_bars_by_day)]
    h.daily_closes = {"$VIX": vix_rows + [{"day": DAY, "close": 40.0}]}                   # the read's day's close must not count
    assert ask("VOLATILITY-08", h, bars_from_closes([7760.0] * 10), "09:40") == "large up"
    assert ask("VOLATILITY-08", h, bars_from_closes([7640.0] * 10), "09:40") == "large down"
    prior_close = h.spx_bars_by_day[max(d for d in h.spx_bars_by_day if d < DAY)][-1]["close"]
    assert ask("VOLATILITY-08", h, bars_from_closes([prior_close] * 10), "09:40") == "small"


def test_volatility_17_day_range_regime_ignores_a_close_dated_the_read_day():
    rows = [{"day": (date.fromisoformat(DAY) - timedelta(days=90 - i)).isoformat(), "high": 7750.0 + (i % 4), "low": 7650.0, "close": 7700.0} for i in range(90)]
    quiet = MarketHistory(daily_closes={"$SPX": rows[:-5] + [dict(r, high=7660.0) for r in rows[-5:]]})
    wide = MarketHistory(daily_closes={"$SPX": rows[:-5] + [dict(r, high=7900.0) for r in rows[-5:]]})
    assert ask("VOLATILITY-17", quiet, []) == "quiet" and ask("VOLATILITY-17", wide, []) == "wide"
    spoiled = MarketHistory(daily_closes={"$SPX": quiet.daily_closes["$SPX"] + [{"day": DAY, "high": 9000.0, "low": 7000.0, "close": 7700.0}]})
    assert ask("VOLATILITY-17", spoiled, []) == "quiet"
    assert ask("VOLATILITY-17", MarketHistory(daily_closes={"$SPX": rows[:40]}), []) is None


# ---------------------------------------------------------------- levels and the overnight

def night(day: str, low: float, high: float) -> list[dict]:
    start = at(18, 0, day) - timedelta(days=1)
    return [{"ts": (start + timedelta(minutes=i)).isoformat(), "open": low, "high": high, "low": low, "close": (low + high) / 2} for i in range(900)]


def test_levels_07_es_against_the_overnight_range_with_the_roll_guard():
    h = MarketHistory(es_night_bars_by_day={DAY: night(DAY, 7680.0, 7720.0)})
    assert ask("LEVELS-07", h, [], market_context={"/ES": {"value": 7730.0}}) == "above overnight high"
    assert ask("LEVELS-07", h, [], market_context={"/ES": {"value": 7700.0}}) == "inside overnight range"
    assert ask("LEVELS-07", h, [], market_context={"/ES": {"value": 7670.0}}) == "below overnight low"
    assert ask("LEVELS-07", h, [], market_context={"/ES": {"value": 7950.0}}) is None                 # another contract
    assert ask("LEVELS-07", MarketHistory(), [], market_context={"/ES": {"value": 7700.0}}) is None


def test_levels_10_room_to_the_next_barrier():
    h = history()
    rising = bars_from_closes(closes(120, 7700.0, step=0.3))                                 # at 7736, the next round number 7750
    assert ask("LEVELS-10", h, rising) in ("close", "normal", "far")
    assert ask("LEVELS-10", h, bars_from_closes([7700.0] * 120)) is None                    # no move, no direction


def test_levels_12_tested_edge_and_who_is_tiring():
    h = MarketHistory()                                                                       # no prior session: today's edges only
    ctx = {"SPY": ctx_bars(DAY, [770.0] * 130, volume=1000.0)}
    base = [7700.0] * 30 + [7720.0] + [7708.0] * 59                                           # the session high, 7720.5, set at 10:00
    first_test = [7719.0, 7721.0, 7712.0, 7705.0, 7708.0]                                     # through the edge, a 16-point rejection
    quiet = [7708.0] * 10
    second_test = [7716.9, 7718.0, 7700.0, 7690.0, 7685.0]                                    # shallower, a 33-point rejection
    tiring = bars_from_closes(base + first_test + quiet + second_test + [7708.0] * 10, wick=0.5)
    assert ask("LEVELS-12", h, tiring, "11:30", today_ctx=ctx) == "buyers tiring"
    assert ask("LEVELS-12", h, bars_from_closes(base + [7700.0 + 0.1 * (i % 2) for i in range(30)], wick=0.1), today_ctx=ctx) == "no tested edge"


# ---------------------------------------------------------------- the diary: walls and the pin

def diary(day: str, call_wall: float, put_wall: float, magnet: float) -> list[dict]:
    return [{"ts": (at(9, 31, day) + timedelta(minutes=i)).isoformat(), "call_wall": call_wall, "put_wall": put_wall,
             "call_wall_tenor": call_wall, "put_wall_tenor": put_wall, "magnet": magnet, "atm_iv": 0.15} for i in range(390)]


def test_options_04_wall_touch_held_or_broke():
    h = MarketHistory(diary_rows=diary(DAY, 7750.0, 7650.0, 7700.0))
    touched_held = bars_from_closes([7700.0] * 100 + [7748.0] * 5 + [7730.0] * 15, wick=0.5)
    assert ask("OPTIONS-04", h, touched_held) == "held at call wall"
    assert ask("OPTIONS-04", h, bars_from_closes([7700.0] * 100 + [7748.0] * 5 + [7760.0] * 15, wick=0.5)) == "broke call wall"
    assert ask("OPTIONS-04", h, bars_from_closes([7700.0] * 100 + [7652.0] * 5 + [7640.0] * 15, wick=0.5)) == "broke put wall"
    assert ask("OPTIONS-04", h, bars_from_closes([7700.0] * 120, wick=0.5)) == "no touch"
    assert ask("OPTIONS-04", MarketHistory(), bars_from_closes([7700.0] * 120)) is None


def test_options_06_pin_holding_stretching_or_broke_loose():
    h = MarketHistory(diary_rows=diary(DAY, 7800.0, 7600.0, 7700.0))
    orbit = bars_from_closes([7700.0 + 2.0 * ((i // 5) % 2 * 2 - 1) for i in range(120)])
    assert ask("OPTIONS-06", h, orbit) == "holding"
    stretched = bars_from_closes([7700.0 + 2.0 * ((i // 5) % 2 * 2 - 1) for i in range(100)] + [7700.0 + 0.5 * i for i in range(20)])
    assert ask("OPTIONS-06", h, stretched) == "stretching"
    assert ask("OPTIONS-06", h, bars_from_closes([7700.0 + 2.0 * ((i // 5) % 2 * 2 - 1) for i in range(100)] + [7760.0] * 20)) == "broke loose"
    assert ask("OPTIONS-06", h, bars_from_closes(closes(120, 7700.0, step=0.5))) == "not pinned"


def test_options_08_quote_width_against_the_same_minute():
    sweeps = {d: [((at(9, 31, d) + timedelta(minutes=i)).isoformat(), 0.10 + 0.01 * (k % 5)) for i in range(390)] for k, d in enumerate(DAYS)}
    today = [((at(9, 31, DAY) + timedelta(minutes=i)).isoformat(), 0.30) for i in range(120)]
    h = MarketHistory(quote_sweeps_by_day={**sweeps, DAY: today})
    assert ask("OPTIONS-08", h, []) == "very wide"
    h.quote_sweeps_by_day[DAY] = [(ts, 0.05) for ts, _ in today]
    assert ask("OPTIONS-08", h, []) == "tight"
    assert ask("OPTIONS-08", MarketHistory(quote_sweeps_by_day={DAY: today}), []) is None


# ---------------------------------------------------------------- breadth, the heavyweights and the weights' date

WEIGHTS = {"NVDA": 0.078, "MSFT": 0.066, "AAPL": 0.062, "AMZN": 0.039, "GOOGL": 0.025, "GOOG": 0.02, "META": 0.029, "AVGO": 0.026,
           "TSLA": 0.019, "BRK/B": 0.016}


def weights_doc(as_of: str) -> dict:
    return {"entries": [{"as_of": as_of, "weights": WEIGHTS}]}


def test_megacaps_take_the_weights_dated_on_or_before_the_day():
    assert market.megacaps(MarketHistory(index_weights=weights_doc(DAY)), DAY)[0] == ("NVDA", 0.078)
    assert dict(market.megacaps(MarketHistory(index_weights=weights_doc("2026-09-01")), DAY))["GOOGL"] == pytest.approx(0.045)
    assert market.megacaps(MarketHistory(index_weights=weights_doc("2026-09-19")), DAY) is None
    assert market.megacaps(MarketHistory(), DAY) is None


def test_breadth_07_largest_pull_beyond_spx():
    h = history(index_weights=weights_doc(DAY))
    flat = bars_from_closes([7700.0] * 120)
    _, calm = feed_day(DAY, seed=0)
    assert ask("BREADTH-07", h, flat, today_ctx=calm) == "small"
    _, shock = feed_day(DAY, seed=0, extra={"NVDA": [180.0] * 90 + [180.0 + 0.4 * i for i in range(30)]})
    assert ask("BREADTH-07", h, flat, today_ctx=shock) == "large up, alone"
    assert ask("BREADTH-07", history(index_weights=weights_doc("2026-09-19")), flat, today_ctx=shock) is None


def test_breadth_08_and_09_leader_fading_and_in_play():
    h = history(index_weights=weights_doc(DAY))
    day_up = bars_from_closes(closes(120, 7700.0, step=0.1))
    _, ctx = feed_day(DAY, seed=0, extra={"NVDA": [180.0 + 0.2 * i for i in range(90)] + [197.8 - 0.3 * i for i in range(30)]})
    assert ask("BREADTH-08", h, day_up, today_ctx=ctx) == "fading"
    _, ctx = feed_day(DAY, seed=0, extra={"NVDA": [180.0 + 0.2 * i for i in range(120)]})
    assert ask("BREADTH-08", h, day_up, today_ctx=ctx) == "kept leading"
    spx_flat, ctx_flat = carried(h)
    assert ask("BREADTH-09", h, spx_flat, today_ctx=ctx_flat) == "none in play"
    _, gapped = feed_day(DAY, seed=0, extra={"NVDA": [200.0] * 120})
    assert ask("BREADTH-09", h, bars_from_closes([7700.0] * 120), today_ctx=gapped) in ("in play, rest followed", "in play, rest did not")


def test_breadth_02_and_03_advance_decline_beyond_the_day_move_and_the_new_extreme():
    h = history(index_weights=weights_doc(DAY))
    flat = bars_from_closes([7700.0] * 120)
    strong = {"$ADVN": {"value": 2800.0}, "$DECN": {"value": 100.0}}
    weak = {"$ADVN": {"value": 100.0}, "$DECN": {"value": 2800.0}}
    assert ask("BREADTH-02", h, flat, market_context=strong) == "stronger" and ask("BREADTH-02", h, flat, market_context=weak) == "weaker"
    assert ask("BREADTH-02", h, flat) is None
    rising = bars_from_closes(closes(120, 7700.0, step=0.5))
    _, confirm = feed_day(DAY, seed=0, extra={"$ADVN": [1000.0 + 10.0 * i for i in range(120)], "$DECN": [1000.0] * 120,
                                              "IWM": [230.0 + 0.1 * i for i in range(120)], "NVDA": [180.0 + 0.1 * i for i in range(120)]})
    assert ask("BREADTH-03", h, rising, today_ctx=confirm) in ("all confirm", "some confirm")
    assert ask("BREADTH-03", h, flat, today_ctx=confirm) == "no new extreme"


# ---------------------------------------------------------------- flow

def test_flow_02_04_07_and_sentiment_02_on_spy_volume_and_the_funds():
    h = history()
    _, ctx = feed_day(DAY, seed=0)
    up = bars_from_closes(closes(120, 7700.0, step=0.5))
    ctx["SPY"] = ctx_bars(DAY, closes(120, 770.0, step=0.05), volume=1000.0)
    assert ask("FLOW-02", h, up, today_ctx=ctx) == "confirmed"                            # every minute's volume is with the move
    ctx["SPY"] = ctx_bars(DAY, closes(120, 770.0), volume=1.0)
    assert ask("FLOW-04", h, up, today_ctx=ctx) == "easy travel on light volume"
    ctx["SPY"] = ctx_bars(DAY, closes(120, 770.0), volume=1e6)
    assert ask("FLOW-04", h, up, today_ctx=ctx) == "heavy with little travel"
    _, same = feed_day(DAY, seed=0, extra={s: closes(120, p, step=p * 0.001) for s, p in (("SPY", 770.0), ("QQQ", 600.0), ("IWM", 230.0))})
    assert ask("FLOW-07", h, up, today_ctx=same) == "same way"
    _, conflict = feed_day(DAY, seed=0, extra={"SPY": closes(120, 770.0, step=0.77), "QQQ": closes(120, 600.0, step=-0.6), "IWM": closes(120, 230.0, step=0.23)})
    assert ask("FLOW-07", h, up, today_ctx=conflict) == "in conflict"
    _, bulls = feed_day(DAY, seed=0, extra={"SPXL": [200.0] * 120})
    bulls["SPXL"] = ctx_bars(DAY, [200.0] * 120, volume=1e6)
    assert ask("SENTIMENT-02", h, bars_from_closes([7700.0] * 120), today_ctx=bulls) == "high"


def test_flow_06_volume_profile_nodes():
    h = history()
    minutes = [(7700.0, 150), (7710.0, 45), (7720.0, 30), (7730.0, 25), (7740.0, 40), (7750.0, 100)]     # nodes at 7700 and 7750
    for d in DAYS[-5:]:
        h.spx_bars_by_day[d] = bars_from_closes([p for p, n in minutes for _ in range(n)], day=d)
        h.context_bars_by_day[d]["SPY"] = ctx_bars(d, [770.0] * 390, volume=1000.0)
    assert ask("FLOW-06", h, bars_from_closes([7701.0] * 120)) == "high-volume node"
    assert ask("FLOW-06", h, bars_from_closes([7731.0] * 120)) == "low-volume node, next node above"
    assert ask("FLOW-06", h, bars_from_closes([7711.0] * 120)) == "between, next node below"


# ---------------------------------------------------------------- macro

def test_macro_01_02_04_05_06_07_on_the_outside_markets():
    h = history()
    flat = bars_from_closes([7700.0] * 120)
    _, risk_off = feed_day(DAY, seed=0, extra={"/ZN": [112.0] * 120, "GLD": [306.0] * 120, "HYG": [78.0] * 120})
    assert ask("MACRO-04", h, flat, today_ctx=risk_off) == "risk-off"
    _, risk_on = feed_day(DAY, seed=0, extra={"/ZN": [108.0] * 120, "GLD": [294.0] * 120, "HYG": [82.0] * 120})
    assert ask("MACRO-04", h, flat, today_ctx=risk_on) == "risk-on"
    spx_flat, ctx_flat = carried(h)
    assert ask("MACRO-04", h, spx_flat, today_ctx=ctx_flat) == "quiet"
    _, confirm = feed_day(DAY, seed=0, extra={"/ZN": [112.0] * 120, "XHB": [104.0] * 120, "IWM": [236.0] * 120, "KRE": [57.0] * 120})
    assert ask("MACRO-05", h, flat, today_ctx=confirm) == "confirm"
    _, contradict = feed_day(DAY, seed=0, extra={"/ZN": [112.0] * 120, "XHB": [96.0] * 120, "IWM": [224.0] * 120, "KRE": [63.0] * 120})
    assert ask("MACRO-05", h, flat, today_ctx=contradict) == "contradict"
    assert ask("MACRO-05", h, spx_flat, today_ctx=ctx_flat) == "nothing"
    assert ask("MACRO-02", h, flat, today_ctx=risk_off) in ("up", "down", "nowhere")
    _, busy = feed_day(DAY, seed=0)
    busy["/ZN"] = ctx_bars(DAY, [110.0] * 120, volume=1e6)
    assert ask("MACRO-07", h, flat, today_ctx=busy) == "busy"
    busy["/ZN"] = ctx_bars(DAY, [110.0] * 120, volume=1.0)
    assert ask("MACRO-07", h, flat, today_ctx=busy) == "quiet"
    spx_path = closes(120, 7700.0, noise=2.0, seed=7)
    _, linked = feed_day(DAY, seed=0, extra={"/ZN": [110.0 * (1 + (p - 7700.0) / 7700.0) for p in spx_path]})
    assert ask("MACRO-06", h, bars_from_closes(spx_path), today_ctx=linked) == "together, stronger than usual"
    _, opposite = feed_day(DAY, seed=0, extra={"/ZN": [110.0 * (1 - (p - 7700.0) / 7700.0) for p in spx_path]})
    assert ask("MACRO-06", h, bars_from_closes(spx_path), today_ctx=opposite) == "opposite, stronger than usual"
    assert ask("MACRO-01", h, flat, today_ctx=feed_day(DAY, seed=0)[1]) in ("up", "down", "no clear lean")


# ---------------------------------------------------------------- the calendar

def calendar(tmp_path: Path, rows: list[dict], covers_from: str = "2026-09-01", covers_through: str = "2026-12-31") -> str:
    """A calendar file under its own name: events._load caches a calendar by path."""
    path = tmp_path / f"events_{len(list(tmp_path.glob('events_*.json')))}.json"
    path.write_text(json.dumps({"covers_from": covers_from, "pre_open_covers_from": covers_from, "covers_through": covers_through, "events": rows}))
    return str(path)


def test_events_01_due_under_way_just_out_or_nothing(tmp_path):
    rows = [{"date": DAY, "time_et": "12:00", "kind": "FOMC", "tier": 1}, {"date": DAY, "time_et": "10:00", "kind": "ISM_SERVICES", "tier": "data_10am"},
            {"date": DAY, "time_et": "13:00", "end_et": "14:00", "kind": "FED_GOVERNOR_SPEECH", "tier": "fed_speaker"}]
    h = MarketHistory(calendar_path=calendar(tmp_path, rows))
    assert ask("EVENTS-01", h, [], "11:30") == "due within 60 minutes, tier 1"
    assert ask("EVENTS-01", h, [], "09:35") == "due within 60 minutes, lower tier"
    assert ask("EVENTS-01", h, [], "10:05") == "just out"
    assert ask("EVENTS-01", h, [], "13:30") == "under way"
    assert ask("EVENTS-01", h, [], "15:00") == "nothing"
    assert ask("EVENTS-01", MarketHistory(calendar_path=calendar(tmp_path, rows, covers_through="2026-09-17")), [], "11:30") is None


def test_events_02_fixed_tier_map(tmp_path):
    eve = date.fromisoformat(DAY) - timedelta(days=1)
    rows = [{"date": DAY, "time_et": "08:30", "kind": "CPI", "tier": "pre_open"}]
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, rows)), []) == "high-tier day"
    rows = [{"date": "2026-09-21", "time_et": "08:30", "kind": "JOBS", "tier": "pre_open"}]                 # the next trading day after Friday 09-18
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, rows)), []) == "eve of a high-tier day"
    rows = [{"date": eve.isoformat(), "time_et": "14:00", "kind": "FOMC", "tier": 1}]
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, rows)), []) == "day after a high-tier day"
    rows = [{"date": DAY, "time_et": "10:00", "kind": "ISM_SERVICES", "tier": "data_10am"}]
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, rows)), []) == "medium-tier day"
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, [])), []) == "ordinary"
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, [], covers_from="2026-10-01")), []) is None


def test_events_03_follow_through_after_the_latest_release(tmp_path):
    rows = [{"date": DAY, "time_et": "10:00", "kind": "ISM_SERVICES", "tier": "data_10am"}]
    h = MarketHistory(calendar_path=calendar(tmp_path, rows))
    before, reaction = [7700.0] * 30, [7700.0 + 1.0 * i for i in range(1, 16)]              # 10:00 .. 10:14 rise 15 points
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7725.0] * 50), "11:05") == "extended"
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7714.0] * 50), "11:05") == "held"
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7704.0] * 50), "11:05") == "faded"
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7690.0] * 50), "11:05") == "reversed"
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction), "10:10") == "no release"           # its first 15 minutes are not over
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7725.0] * 150), "12:30") == "no release"   # digested


def test_events_05_rates_surprise_against_prior_releases(tmp_path):
    rows = [{"date": d, "time_et": "10:00", "kind": "ISM_SERVICES", "tier": "data_10am"} for d in DAYS + (DAY,)]
    h = history(calendar_path=calendar(tmp_path, rows))
    _, big = feed_day(DAY, seed=0, extra={"/ZN": [110.0] * 30 + [109.0] * 90})
    assert ask("EVENTS-05", h, bars_from_closes([7700.0] * 120), "10:20", today_ctx=big) == "large, yields up"
    _, small = feed_day(DAY, seed=0, extra={"/ZN": [110.0] * 120})
    assert ask("EVENTS-05", h, bars_from_closes([7700.0] * 120), "10:20", today_ctx=small) == "small"
    assert ask("EVENTS-05", h, bars_from_closes([7700.0] * 120), "10:05", today_ctx=small) == "no release"


# ---------------------------------------------------------------- the month turn

def test_session_02_month_turn_from_the_daily_closes():
    assert market.month_position(date(2026, 9, 18)) == (13, 8) and market.month_position(date(2026, 9, 30)) == (21, 0)   # Labor Day out
    assert market.month_position(date(2026, 10, 1)) == (1, 21)
    rows_spx, rows_tlt = [], []
    d = date(2024, 1, 1)
    while d < date(2026, 10, 1):
        if d.weekday() < 5:
            months = (d.year - 2024) * 12 + d.month
            rows_spx.append({"day": d.isoformat(), "close": 100.0 * (1.01 ** months) * (1 + 0.1 * (d.month == 9 and d.year == 2026) * d.day / 30)})
            rows_tlt.append({"day": d.isoformat(), "close": 100.0})
        d += timedelta(days=1)
    h = MarketHistory(daily_closes={"$SPX": rows_spx, "TLT": rows_tlt})
    assert MARKET_ANSWERERS["SESSION-02"](record("2026-09-30T11:00:00-04:00"), [], h, "2026-09-30", "11:00") == "sell big"
    assert MARKET_ANSWERERS["SESSION-02"](record("2026-10-01T11:00:00-04:00"), [], h, "2026-10-01", "11:00") == "new-month inflow"
    assert MARKET_ANSWERERS["SESSION-02"](record(f"{DAY}T11:00:00-04:00"), [], h, DAY, "11:00") is None


# ---------------------------------------------------------------- the whole read, and the needs_new_feed questions

def test_every_market_answer_is_in_its_options_and_the_unfed_questions_are_silent():
    h = history(index_weights=weights_doc(DAY))
    spx, ctx = feed_day(DAY, seed=0, spx_step=0.2)
    h.context_bars_by_day[DAY] = ctx
    row_ts = f"{DAY}T11:30:30-04:00"
    answers = answer_code_features(record(row_ts, market_context={"$ADVN": {"value": 2000.0}, "$DECN": {"value": 900.0}}), bars_up_to(spx, row_ts), h)
    assert all(a is None or a in CATALOG[q]["options"] for q, a in answers.items())
    assert answers["OPTIONS-12"] is None and answers["FLOW-09"] is None
    assert sum(answers[q] is not None for q in MARKET_ANSWERERS) >= 20


# ---------------------------------------------------------------- the loaders

def test_context_bars_loader_skips_bad_lines_and_bad_bars(tmp_path):
    folder = tmp_path / "spx_jev" / "context" / "bars"
    folder.mkdir(parents=True)
    good = {"ts": "2026-10-06T09:31:00-04:00", "bars": {"SPY": {"ts": "2026-10-06T09:30:00-04:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                                                       "QQQ": {"ts": "2026-10-06T09:30:00-04:00", "open": "x"}}}
    (folder / "2026-10-06.jsonl").write_text("not json\n" + json.dumps(good) + "\n" + json.dumps({"ts": "t", "bars": "no"}) + "\n")
    bars = inputs.load_context_bars(tmp_path, "2026-10-06")
    assert list(bars) == ["SPY"] and bars["SPY"][0]["volume"] == 10.0
    assert inputs.load_context_bars_by_day(tmp_path, "2026-10-07") == {"2026-10-06": bars}
    assert inputs.load_context_bars_by_day(tmp_path, "2026-10-06") == {}                    # the day itself is not a prior session


def test_quotes_as_bars_carry_the_minute_volume_from_the_cumulative_count(tmp_path):
    folder = tmp_path / "spx_jev" / "context"
    folder.mkdir(parents=True)
    lines = [{"ts": "2026-10-06T09:31:05-04:00", "quotes": {"SPY": {"last": 770.0, "volume": 1000}}, "bars": {"$ADVN": {"ts": "2026-10-06T09:30:00-04:00", "open": 1, "high": 1, "low": 1, "close": 1200.0, "volume": 0}}},
             {"ts": "2026-10-06T09:32:10-04:00", "quotes": {"SPY": {"last": 770.5, "volume": 1600}, "/ZN": {"last": None}}, "bars": {}},
             {"ts": "bad"}, {"ts": "2026-10-06T09:33:10-04:00", "quotes": {"SPY": {"last": 770.2, "volume": 1500}}, "bars": {}}]
    (folder / "2026-10-06.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    bars = inputs.load_todays_context_bars(tmp_path, "2026-10-06")
    assert [b["volume"] for b in bars["SPY"]] == [0.0, 600.0, 0.0] and bars["$ADVN"][0]["close"] == 1200.0 and "/ZN" not in bars


def test_diary_and_sweeps_loaders_tolerate_bad_lines(tmp_path):
    (tmp_path / "reversion").mkdir()
    rows = [{"ts": "2026-10-06T09:31:00-04:00", "call_wall": 7750.0, "gex_views": {"magnet": 7700.0}}, {"no": "ts"}, {"ts": "2026-10-06T09:32:00-04:00", "gex_views": None}]
    (tmp_path / "reversion" / "2026-10-06.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    diary = inputs.load_diary_rows(tmp_path, "2026-10-06")
    assert [r["magnet"] for r in diary] == [7700.0, None] and diary[0]["call_wall"] == 7750.0 and diary[1]["put_wall"] is None
    raw = tmp_path / "lob_flow" / "raw"
    (raw / "2026-10-05").mkdir(parents=True)
    (raw / "2026-10-06").mkdir()
    sweep = {"ts": "2026-10-06T10:00:03-04:00", "buckets": {"d25_40": {"spread": 0.1, "size": 200}}}
    (raw / "2026-10-06" / "sweeps.jsonl").write_text(json.dumps(sweep) + "\n{bad\n" + json.dumps({"gap": True, "ts": "2026-10-06T10:01:03-04:00"}) + "\n")
    with gzip.open(raw / "2026-10-05" / "sweeps.jsonl.gz", "wt") as f:
        f.write(json.dumps({**sweep, "ts": "2026-10-05T10:00:03-04:00"}) + "\n")
    assert inputs.load_quote_sweeps(tmp_path, "2026-10-06") == [("2026-10-06T10:00:03-04:00", 0.1)]
    assert list(inputs.load_quote_sweeps_by_day(tmp_path, "2026-10-06")) == ["2026-10-05"]


def test_market_history_caches_the_prior_sessions_for_the_day_and_reads_the_day_fresh(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(inputs, "load_context_bars_by_day", lambda *a, **k: calls.append("prior") or {"2026-10-05": {"SPY": []}})
    monkeypatch.setattr(inputs, "load_todays_context_bars", lambda *a, **k: calls.append("today") or {"SPY": [{"ts": "2026-10-06T09:30:00-04:00", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}]})
    inputs._PRIOR_SESSIONS_CACHE.clear()
    first = inputs.load_market_history(tmp_path, "live", "2026-10-06")
    second = inputs.load_market_history(tmp_path, "live", "2026-10-06")
    assert calls.count("prior") == 1 and calls.count("today") == 2
    assert set(first.context_bars_by_day) == set(second.context_bars_by_day) == {"2026-10-05", "2026-10-06"}
    assert all(rows == [] for rows in first.daily_closes.values()) and first.index_weights is None and first.diary_rows == []
    inputs._PRIOR_SESSIONS_CACHE.clear()


# ---------------------------------------------------------------- live quotes and saved bars measure the same thing

def as_quotes(bars: list[dict]) -> list[dict]:
    """The same session as the live quotes give it: one price a snapshot, no high or low of its own."""
    return [dict(b, open=b["close"], high=b["close"], low=b["close"]) for b in bars]


def test_trend_07_and_breadth_03_read_closes_only_so_live_quotes_match_saved_bars():
    h = history(index_weights=weights_doc(DAY))
    orbit = [770.0 + 0.02 * ((i % 2) * 2 - 1) for i in range(120)]
    _, ctx = feed_day(DAY, seed=0, extra={"SPY": orbit})
    ctx["SPY"] = [dict(b, high=b["close"] + 5.0, low=b["close"] - 5.0) for b in ctx["SPY"]]     # wide wicks that must not count
    saved = ask("TREND-07", h, bars_from_closes([7700.0] * 120), today_ctx=ctx)
    live = ask("TREND-07", h, bars_from_closes([7700.0] * 120), today_ctx={s: as_quotes(b) for s, b in ctx.items()})
    assert saved == live == "orbiting"
    rising = bars_from_closes(closes(120, 7700.0, step=0.5))
    _, ctx = feed_day(DAY, seed=0, extra={"$ADVN": [1000.0 + 10.0 * i for i in range(120)], "$DECN": [1000.0] * 120,
                                          "IWM": [230.0 + 0.1 * i for i in range(119)] + [242.0],
                                          **{s: [p + 0.001 * p * i for i in range(120)] for s, p in (("NVDA", 180.0), ("MSFT", 500.0), ("AAPL", 250.0), ("AMZN", 220.0),
                                                                                                     ("GOOGL", 250.0), ("META", 700.0), ("AVGO", 350.0), ("TSLA", 400.0))}})
    ctx["IWM"] = [dict(b, high=b["close"] + 3.0) for b in ctx["IWM"]]                              # a wick above every close
    saved = ask("BREADTH-03", h, rising, today_ctx=ctx)
    live = ask("BREADTH-03", h, rising, today_ctx={s: as_quotes(b) for s, b in ctx.items()})
    assert saved == live == "all confirm"


def test_breadth_02_and_03_use_the_derived_advance_decline_only():
    h = history(index_weights=weights_doc(DAY))
    flat = bars_from_closes([7700.0] * 120)
    wrong = {"$ADD": {"value": 2800.0}, "$TICK": {"value": 900.0}, "$TRIN": {"value": 0.3}, "$UVOL": {"value": 9e9}, "$DVOL": {"value": 1.0},
             "$VOLD": {"value": 9e9}, "$VOLSPD": {"value": 9e9}}
    assert ask("BREADTH-02", h, flat, market_context=wrong) is None
    assert ask("BREADTH-02", h, flat, market_context={**wrong, "$ADVN": {"value": 2800.0}, "$DECN": {"value": 100.0}}) == "stronger"
    rising = bars_from_closes(closes(120, 7700.0, step=0.5))
    _, ctx = feed_day(DAY, seed=0, extra={"$ADD": [1000.0 + 10.0 * i for i in range(120)]})
    for s in ("$ADVN", "$DECN"):
        ctx.pop(s)
    assert ask("BREADTH-03", h, rising, today_ctx=ctx) is None                                  # no derived line, no answer
