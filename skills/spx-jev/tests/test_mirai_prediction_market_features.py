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
    assert on_wednesday(rising, closes(120, 16.0, step=-0.01)) == "new high, VIX confirms"
    assert on_wednesday(rising, closes(120, 16.0, step=0.01)) == "new high, VIX diverges"
    assert on_wednesday(rising, [16.0] * 119 + [16.04]) == "new high, VIX confirms"                       # within 0.05 points: a tie confirms
    assert on_wednesday(closes(120, 7700.0, step=-0.5), closes(120, 16.0, step=0.01)) == "new low, VIX confirms"
    assert on_wednesday([7700.0] * 60 + [7690.0] + [7695.0] * 59, [16.0] * 120) is None                  # no new extreme: LEVELS-05's
    assert on_wednesday(closes(340, 7700.0, step=0.5), [16.0] * 340, "15:10") == "mechanical"               # the last hour
    assert ask("VOLATILITY-14", h, bars_from_closes(rising), today_ctx={"$VIX": ctx_bars(DAY, [16.0] * 120)}) == "mechanical"   # DAY is the third Friday
    second_friday = "2026-09-11"
    row_ts = f"{second_friday}T11:30:30-04:00"
    h.context_bars_by_day[second_friday] = {"$VIX": ctx_bars(second_friday, closes(120, 16.0, step=-0.01))}
    assert MARKET_ANSWERERS["VOLATILITY-14"](record(row_ts), bars_up_to(bars_from_closes(rising, day=second_friday), row_ts), h,
                                             second_friday, "11:30") == "new high, VIX confirms"     # only the monthly expiry Friday is mechanical
    before_labor_day = "2026-09-04"
    row_ts = f"{before_labor_day}T11:30:30-04:00"
    h.context_bars_by_day[before_labor_day] = {"$VIX": ctx_bars(before_labor_day, closes(120, 16.0, step=-0.01))}
    assert MARKET_ANSWERERS["VOLATILITY-14"](record(row_ts), bars_up_to(bars_from_closes(rising, day=before_labor_day), row_ts), h,
                                             before_labor_day, "11:30") == "mechanical"            # the session before a holiday


def test_a_new_extreme_beats_the_earlier_swings_extreme_not_the_bar_before():
    """The runner-up a minute before the new high is not the earlier extreme: the highest bar before the last 30 minutes is."""
    values = [10.0] * 20 + [15.0] + [11.0] * 69 + [12.0, 14.0, 16.0] + [13.0] * 27
    assert market.new_extreme_vs_prior_swing(values, 90) == (92, 20)
    assert market.new_extreme_vs_prior_swing(values[:92] + [14.5] + [13.0] * 27, 90) is None             # under the earlier swing's 15
    assert market.new_extreme_vs_prior_swing(values, 20) is None                                         # under 30 earlier bars


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
    assert ask("TREND-11", h, faded, today_ctx=ctx) == "up spike faded"
    down_faded = bars_from_closes([7700.0] * 95 + [7690.0] * 5 + [7702.0] * 20)
    assert ask("TREND-11", h, down_faded, today_ctx=ctx) == "down spike faded"


def test_trend_07_vwap_balance_words():
    h = history()
    _, ctx = feed_day(DAY, seed=0, extra={"SPY": closes(120, 770.0, step=0.05)})          # SPY climbs away from its VWAP all morning
    assert ask("TREND-07", h, bars_from_closes([7700.0] * 120), today_ctx=ctx) == "long above VWAP"
    _, ctx = feed_day(DAY, seed=0, extra={"SPY": closes(120, 770.0, step=-0.05)})
    assert ask("TREND-07", h, bars_from_closes([7700.0] * 120), today_ctx=ctx) == "long below VWAP"
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
    assert ask("VOLATILITY-08", h, bars_from_closes([7760.0] * 120), "11:30") is None             # the opening reads only (to 10:35)


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


def test_levels_10_passes_over_the_half_hours_own_high_and_a_level_at_price():
    """Rising into a new high: the high is behind price, not ahead of it; the next barrier is the round number."""
    rising = bars_from_closes(closes(120, 7700.0, step=0.3))
    view = market.DayView(DAY, rising, {})
    price = rising[-1]["close"]
    assert market.barrier_room(MarketHistory(), view, "11:30", SIGMA) == pytest.approx((7750.0 - price) / price)
    at_round = bars_from_closes(closes(119, 7700.0, step=0.42) + [7749.5])                    # 0.5 points under 7750: within 0.02 sigma
    prior = MarketHistory(spx_bars_by_day={PRIOR_DAYS[0]: bars_from_closes([7700.0] * 200 + [7760.0] + [7700.0] * 189, day=PRIOR_DAYS[0])})
    assert market.barrier_room(prior, market.DayView(DAY, at_round, {}), "11:30", SIGMA) == pytest.approx((7760.5 - 7749.5) / 7749.5)


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


def test_levels_12_compares_the_tests_mean_volume_per_bar_not_their_totals():
    """A four-bar latest test at 900 a bar against a three-bar first one at 1000: lighter per bar though heavier in total."""
    h = MarketHistory()
    base = [7700.0] * 30 + [7720.0] + [7708.0] * 59
    first_test = [7719.0, 7721.0, 7712.0, 7705.0, 7708.0]
    second_test = [7716.9, 7717.5, 7718.0, 7712.0]                                            # shallower, a milder rejection
    bars = bars_from_closes(base + first_test + [7708.0] * 10 + second_test + [7708.0] * 11, wick=0.5)
    spy = ctx_bars(DAY, [770.0] * 130, volume=1000.0)
    for b in spy:
        if "11:15" <= b["ts"][11:16] <= "11:18":
            b["volume"] = 900.0
    assert ask("LEVELS-12", h, bars, "11:30", today_ctx={"SPY": spy}) == "buyers tiring"
    still_testing = bars_from_closes(base + first_test + [7708.0] * 21 + second_test, wick=0.5)   # the latest test runs to the read
    assert ask("LEVELS-12", h, still_testing, "11:30", today_ctx={"SPY": [dict(b, volume=900.0 if b["ts"][11:16] >= "11:26" else 1000.0) for b in spy]}) == "buyers tiring"


# ---------------------------------------------------------------- the diary: walls and the pin

def diary(day: str, call_wall: float, put_wall: float, magnet: float, far: float = 0.0) -> list[dict]:
    """A day's diary rows: the near gamma walls at ``call_wall`` / ``put_wall``, the open-interest walls ``far`` points beyond."""
    return [{"ts": (at(9, 31, day) + timedelta(minutes=i)).isoformat(), "call_wall": call_wall + far, "put_wall": put_wall - far,
             "call_wall_tenor": call_wall + far, "put_wall_tenor": put_wall - far, "call_wall_gamma": call_wall, "put_wall_gamma": put_wall,
             "magnet": magnet, "atm_iv": 0.15, "gex_source": "native"} for i in range(390)]


def test_options_04_wall_touch_held_or_broke():
    h = MarketHistory(diary_rows=diary(DAY, 7750.0, 7650.0, 7700.0, far=100.0))      # the open-interest walls 100 points out: never read
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
    back = bars_from_closes([7700.0 + 2.0 * ((i // 5) % 2 * 2 - 1) for i in range(100)] + [7700.0 + 0.5 * i for i in range(19)] + [7702.5])
    assert ask("OPTIONS-06", h, back) == "holding"                       # wide earlier in the swing, but 2.5 points off the pin now
    assert ask("OPTIONS-06", h, bars_from_closes([7700.0 + 2.0 * ((i // 5) % 2 * 2 - 1) for i in range(100)] + [7760.0] * 20)) == "broke loose"
    assert ask("OPTIONS-06", h, bars_from_closes(closes(120, 7700.0, step=0.5))) == "not pinned"


def book_row(ts: str, spot: float, mass: list[list[float]], source: str = "native", sigma: float = SIGMA) -> dict:
    return {"ts": ts, "spot": spot, "sigma": sigma, "mass_by_strike": mass, "gex_source": source}


def test_options_02_ranks_the_gamma_within_one_30_minute_move_of_spot_against_the_same_clock():
    """75 sigma scales to a 20.8-point half hour: 7690 and 7710 are near 7700, 7725 is not."""
    today = [book_row(f"{DAY}T11:30:05-04:00", 7700.0, [[7690.0, 30.0], [7710.0, 50.0], [7725.0, 20.0]])]          # 80% near
    asked = []

    def prior_row(d, clock):
        asked.append(clock)
        k = DAYS.index(d)
        return book_row(f"{d}T11:30:00-04:00", 7700.0, [[7700.0, 10.0 + 4 * k], [7740.0, 90.0 - 4 * k]])            # 10% to 50% near
    h = history(diary_rows=today, prior_diary_row_at=prior_row)
    assert ask("OPTIONS-02", h, []) == "high" and set(asked) == {"11:30:30"}
    assert market.near_gamma_share(today[0]) == pytest.approx(0.8)
    low = history(diary_rows=[book_row(f"{DAY}T11:30:05-04:00", 7700.0, [[7700.0, 5.0], [7760.0, 95.0]])], prior_diary_row_at=prior_row)
    assert ask("OPTIONS-02", low, []) == "low"
    stand_ins = history(diary_rows=today, prior_diary_row_at=lambda d, clock: {**prior_row(d, clock), "gex_source": "spy_proxy×10.03"} if d > DAYS[1] else prior_row(d, clock))
    assert ask("OPTIONS-02", stand_ins, []) is None                       # a stand-in session never ranks: two native left
    assert ask("OPTIONS-02", history(diary_rows=today), []) is None       # no prior diary on hand


def walls_row(ts, call: float, put: float) -> dict:
    return {"ts": ts, "call_wall_gamma": call, "put_wall_gamma": put, "gex_source": "native"}


@pytest.mark.parametrize("now, answer", [
    ((7753.0, 7648.0), "stand still"),            # each under one 5-point strike step: stayed put
    ((7760.0, 7660.0), "both moved up"),
    ((7740.0, 7640.0), "both moved down"),
    ((7760.0, 7652.0), "spread out"),             # the put side stayed put
    ((7745.0, 7655.0), "pulled in"),
])
def test_options_11_the_heaviest_strikes_against_30_minutes_ago_with_the_one_strike_rule(now, answer):
    rows = [walls_row((at(10, 50) + timedelta(minutes=i)).isoformat(), *((7750.0, 7650.0) if i < 25 else now)) for i in range(41)]
    assert ask("OPTIONS-11", MarketHistory(diary_rows=rows), [], "11:30") == answer


def test_options_11_needs_a_row_from_30_minutes_ago():
    rows = [walls_row(at(10, 40).isoformat(), 7750.0, 7650.0), walls_row(at(11, 30).isoformat(), 7760.0, 7660.0)]
    assert ask("OPTIONS-11", MarketHistory(diary_rows=rows), [], "11:30") is None          # 10:40 is past the 5-minute slack


def defense_record(t, *strikes: tuple[float, int, int]):
    """A collector record with one refill-test reading at ``t``: each (strike, refilled, not refilled)."""
    from spx_jev.labels.options_flow import CollectorRecord
    block = {f"k{i}": {"strike": k, "n_events": refilled, "n_unrecovered": lost} for i, (k, refilled, lost) in enumerate(strikes)}
    return CollectorRecord([(t, block)], [])


def test_options_05_the_nearest_contested_strike_within_a_30_minute_move_defended_or_abandoned():
    prior = {d: defense_record(at(11, 30, d), (7710.0, 1 + k % 4, 5)) for k, d in enumerate(DAYS)}       # refill shares 1/6 to 4/9

    def asked(*strikes, t=at(11, 30)):
        h = history(collector_records={**prior, DAY: defense_record(t, *strikes)})
        return ask("OPTIONS-05", h, [], spot=7700.0)
    assert asked((7712.0, 3, 1)) == "defended above"                     # 3 hits is enough; 12 points is inside the 20.8-point reach
    assert asked((7690.0, 0, 4)) == "abandoned below"
    assert asked((7690.0, 0, 4), (7680.0, 9, 0)) == "abandoned below"    # the nearest contested strike speaks
    assert asked((7725.0, 3, 1)) is None                                 # out of reach
    assert asked((7712.0, 1, 1)) is None                                 # 2 hits: not contested
    assert asked((7712.0, 3, 1), t=at(11, 26)) is None                   # the collector stopped 4 minutes before the read
    assert ask("OPTIONS-05", history(collector_records={DAY: defense_record(at(11, 30), (7712.0, 3, 1))}), [], spot=7700.0) is None   # nothing to rank against


def test_options_08_quote_width_against_the_same_minute_and_bucket():
    sweeps = {d: [((at(9, 31, d) + timedelta(minutes=i)).isoformat(), 0.10 + 0.01 * (k % 5), "d25_40") for i in range(390)] for k, d in enumerate(DAYS)}
    today = [((at(9, 31, DAY) + timedelta(minutes=i)).isoformat(), 0.30, "d25_40") for i in range(120)]
    h = MarketHistory(quote_sweeps_by_day={**sweeps, DAY: today})
    assert ask("OPTIONS-08", h, []) == "very wide"
    h.quote_sweeps_by_day[DAY] = [(ts, 0.05, "d25_40") for ts, _, _ in today]
    assert ask("OPTIONS-08", h, []) == "tight"
    h.quote_sweeps_by_day[DAY] = [(ts, 0.05, "d10_25") for ts, _, _ in today]
    assert ask("OPTIONS-08", h, []) is None                              # no prior session quoted the stand-in bucket at this minute
    assert ask("OPTIONS-08", MarketHistory(quote_sweeps_by_day={DAY: today}), []) is None


def test_options_08_counts_a_tie_with_the_usual_tick_as_half():
    """Whole ticks: on a 0.10 day against priors mostly at 0.10 a strict rank would read tight; ties count half."""
    sweeps = {d: [((at(9, 31, d) + timedelta(minutes=i)).isoformat(), 0.20 if k == 0 else 0.10, "d25_40") for i in range(390)] for k, d in enumerate(DAYS)}
    today = [((at(9, 31, DAY) + timedelta(minutes=i)).isoformat(), 0.10, "d25_40") for i in range(120)]
    assert ask("OPTIONS-08", MarketHistory(quote_sweeps_by_day={**sweeps, DAY: today}), []) == "normal"


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
    assert ask("BREADTH-07", h, flat, today_ctx=shock) == "large up"
    assert ask("BREADTH-07", history(index_weights=weights_doc("2026-09-19")), flat, today_ctx=shock) is None


def test_breadth_09_heavyweight_in_play():
    h = history(index_weights=weights_doc(DAY))
    spx_flat, ctx_flat = carried(h)
    assert ask("BREADTH-09", h, spx_flat, today_ctx=ctx_flat) == "none in play"
    _, gapped = feed_day(DAY, seed=0, extra={"NVDA": [200.0] * 120})
    assert ask("BREADTH-09", h, bars_from_closes([7700.0] * 120), today_ctx=gapped) in ("in play up, rest followed", "in play up, rest did not")
    _, sunk = feed_day(DAY, seed=0, extra={"NVDA": [160.0] * 120})
    assert ask("BREADTH-09", h, bars_from_closes([7700.0] * 120), today_ctx=sunk).startswith("in play down, ")
    row_ts = f"{DAY}T11:30:30-04:00"
    name, pull, rest = market.heavyweight_pull(record(row_ts), bars_up_to(bars_from_closes([7700.0] * 120), row_ts), h, DAY, "11:30")
    assert name == "NVDA" and pull < 0 and isinstance(rest, float)            # what judgment.py names in its label


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
    assert ask("BREADTH-03", h, rising, today_ctx=confirm) == "new high, confirmed"
    _, against = feed_day(DAY, seed=0, extra={"$ADVN": [2000.0 - 10.0 * i for i in range(120)], "$DECN": [1000.0] * 120,
                                              "IWM": [230.0 - 0.1 * i for i in range(120)], "NVDA": [180.0 - 0.1 * i for i in range(120)]})
    assert ask("BREADTH-03", h, rising, today_ctx=against) == "new high, not confirmed"
    assert ask("BREADTH-03", h, flat, today_ctx=confirm) is None                     # no new extreme: LEVELS-05's


# ---------------------------------------------------------------- flow

def test_flow_02_04_07_and_sentiment_02_on_spy_volume_and_the_funds():
    h = history()
    _, ctx = feed_day(DAY, seed=0)
    up = bars_from_closes(closes(120, 7700.0, step=0.5))
    ctx["SPY"] = ctx_bars(DAY, closes(120, 770.0, step=0.05), volume=1000.0)
    assert ask("FLOW-02", h, up, today_ctx=ctx) == "confirmed up"                         # every minute's volume is with the move
    ctx["SPY"] = ctx_bars(DAY, closes(120, 770.0), volume=1.0)
    assert ask("FLOW-04", h, up, today_ctx=ctx) == "easy travel on light volume"
    ctx["SPY"] = ctx_bars(DAY, closes(120, 770.0), volume=1e6)
    assert ask("FLOW-04", h, up, today_ctx=ctx) == "heavy with little travel"
    ctx["SPY"] = [dict(b, volume=1e6 if i % 2 else 0.0) for i, b in enumerate(ctx["SPY"])]          # volume on half the minutes: a feed hole
    assert ask("FLOW-04", h, up, today_ctx=ctx) is None
    h.today_quote_bars = {s: ctx_bars(DAY, closes(120, p, step=p * 0.001)) for s, p in (("SPY", 770.0), ("QQQ", 600.0), ("IWM", 230.0))}
    assert ask("FLOW-07", h, up, today_ctx=feed_day(DAY, seed=0)[1]) == "same way up"         # the day's quotes, not its bars
    h.today_quote_bars = {"SPY": ctx_bars(DAY, closes(120, 770.0, step=0.77)), "QQQ": ctx_bars(DAY, closes(120, 600.0, step=-0.6)),
                          "IWM": ctx_bars(DAY, closes(120, 230.0, step=0.23))}
    assert ask("FLOW-07", h, up, today_ctx=feed_day(DAY, seed=0)[1]) == "in conflict"
    h.today_quote_bars = {}
    assert ask("FLOW-07", h, up, today_ctx=feed_day(DAY, seed=0)[1]) is None
    _, bulls = feed_day(DAY, seed=0, extra={"SPXL": [200.0] * 120})
    bulls["SPXL"] = ctx_bars(DAY, [200.0] * 120, volume=1e6)
    assert ask("SENTIMENT-02", h, bars_from_closes([7700.0] * 120), today_ctx=bulls) == "high"
    h.today_context_from_quotes = True                                                         # a live read: the funds' volume is snapshots
    assert ask("SENTIMENT-02", h, bars_from_closes([7700.0] * 120), today_ctx=bulls) is None


def test_flow_06_volume_profile_nodes():
    h = history()
    minutes = [(7700.0, 150), (7710.0, 45), (7720.0, 30), (7730.0, 25), (7740.0, 40), (7750.0, 100)]     # nodes at 7700 and 7750
    for d in DAYS[-5:]:
        h.spx_bars_by_day[d] = bars_from_closes([p for p, n in minutes for _ in range(n)], day=d)
        h.context_bars_by_day[d]["SPY"] = ctx_bars(d, [770.0] * 390, volume=1000.0)
    assert ask("FLOW-06", h, bars_from_closes([7701.0] * 120)) == "at a node"
    assert ask("FLOW-06", h, bars_from_closes([7731.0] * 120)) == "node above"
    assert ask("FLOW-06", h, bars_from_closes([7711.0] * 120)) == "node below"
    assert ask("FLOW-06", h, bars_from_closes([7726.0] * 120)) == "at a node"                 # 25 points to either node: a tie
    assert ask("FLOW-06", h, bars_from_closes([7761.0] * 120)) == "outside the profile, above"
    assert ask("FLOW-06", h, bars_from_closes([7690.0] * 120)) == "outside the profile, below"


# ---------------------------------------------------------------- macro

def test_macro_01_02_07_on_the_outside_markets():
    h = history()
    flat = bars_from_closes([7700.0] * 120)
    _, risk_off = feed_day(DAY, seed=0, extra={"/ZN": [112.0] * 120, "GLD": [306.0] * 120, "HYG": [78.0] * 120})
    assert ask("MACRO-02", h, flat, today_ctx=risk_off) in ("far up", "up", "down", "far down", "nowhere")
    spx_flat, ctx_flat = carried(h)
    assert ask("MACRO-02", h, spx_flat, today_ctx=ctx_flat) == "nowhere"
    _, busy = feed_day(DAY, seed=0)
    busy["/ZN"] = ctx_bars(DAY, [110.0] * 120, volume=1e6)
    assert ask("MACRO-07", h, flat, today_ctx=busy) == "busy"
    busy["/ZN"] = ctx_bars(DAY, [110.0] * 120, volume=1.0)
    assert ask("MACRO-07", h, flat, today_ctx=busy) == "quiet"
    assert ask("MACRO-01", h, flat, today_ctx=feed_day(DAY, seed=0)[1]) in ("up", "down", "no clear lean")


# ---------------------------------------------------------------- the calendar

def calendar(tmp_path: Path, rows: list[dict], covers_from: str = "2026-09-01", covers_through: str = "2026-12-31") -> str:
    """A calendar file under its own name: events._load caches a calendar by path."""
    path = tmp_path / f"events_{len(list(tmp_path.glob('events_*.json')))}.json"
    path.write_text(json.dumps({"covers_from": covers_from, "pre_open_covers_from": covers_from, "covers_through": covers_through, "events": rows}))
    return str(path)


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
    # one neighbour past the calendar: it could be a high-tier day, so the read is not medium or ordinary
    rows = [{"date": DAY, "time_et": "10:00", "kind": "ISM_SERVICES", "tier": "data_10am"}]
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, rows, covers_through=DAY)), []) is None
    assert ask("EVENTS-02", MarketHistory(calendar_path=calendar(tmp_path, rows, covers_from=DAY)), []) is None


PREMARKET = [{"row_ts": f"{DAY}T09:28:00-04:00", "lane": "premarket", "sigma": 60.0}]      # the normal-day sigma, as the premarket lane stamps it


def test_events_03_follow_through_after_the_latest_release(tmp_path):
    rows = [{"date": DAY, "time_et": "10:00", "kind": "ISM_SERVICES", "tier": "data_10am"},
            {"date": DAY, "time_et": "10:30", "end_et": "10:45", "kind": "FED_GOVERNOR_SPEECH", "tier": "fed_speaker"}]
    h = MarketHistory(calendar_path=calendar(tmp_path, rows), premarket_reads=PREMARKET)
    before, reaction = [7700.0] * 30, [7700.0 + 1.0 * i for i in range(1, 16)]              # 10:00 .. 10:14 rise 15 points
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7725.0] * 50), "11:05") == "up then extended"   # 10 points past: > 0.05 x 60
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7717.0] * 50), "11:05") == "up then held/faded"  # 2 points past: < 3
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7704.0] * 50), "11:05") == "up then held/faded"
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7690.0] * 50), "11:05") == "reversed down"
    falling = [7700.0 - 1.0 * i for i in range(1, 16)]
    assert ask("EVENTS-03", h, bars_from_closes(before + falling + [7710.0] * 50), "11:05") == "reversed up"
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction), "10:10") == "no release"           # its first 15 minutes are not over
    assert ask("EVENTS-03", h, bars_from_closes(before + reaction + [7725.0] * 150), "12:30") == "no release"   # digested; Fed remarks are no release
    assert ask("EVENTS-03", MarketHistory(calendar_path=h.calendar_path), bars_from_closes(before + reaction + [7725.0] * 50), "11:05") is None   # no ruler


def test_events_03_a_release_at_the_open_starts_from_the_first_bars_open(tmp_path):
    rows = [{"date": DAY, "time_et": "09:30", "kind": "ISM_SERVICES", "tier": "data_10am"}]
    h = MarketHistory(calendar_path=calendar(tmp_path, rows), premarket_reads=PREMARKET)
    bars = bars_from_closes([7710.0 + 1.0 * i for i in range(15)] + [7740.0] * 30)
    bars[0]["open"] = 7700.0
    assert ask("EVENTS-03", h, bars, "10:15") == "up then extended"


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
    crash = MarketHistory(daily_closes={"$SPX": [dict(r, close=50.0) if r["day"] == "2026-09-29" else r for r in rows_spx], "TLT": rows_tlt})
    both = [MARKET_ANSWERERS["SESSION-02"](record(f"{d}T11:00:00-04:00"), [], crash, d, "11:00") for d in ("2026-09-29", "2026-09-30")]
    assert both == ["sell big", "sell big"]          # the month-end window's gap is measured once, through 09-28: 09-29's close does not move it
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
    assert set(answers) == set(CATALOG) and not {"OPTIONS-12", "FLOW-09", "MACRO-04", "EVENTS-05", "BREADTH-11"} & set(answers)
    assert sum(answers[q] is not None for q in MARKET_ANSWERERS) >= 18


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
    bars = inputs.load_context_quotes_as_bars(tmp_path, "2026-10-06")
    assert [b["volume"] for b in bars["SPY"]] == [0.0, 600.0, 0.0] and bars["$ADVN"][0]["close"] == 1200.0 and "/ZN" not in bars


def test_diary_and_sweeps_loaders_tolerate_bad_lines(tmp_path):
    (tmp_path / "reversion").mkdir()
    rows = [{"ts": "2026-10-06T09:31:00-04:00", "call_wall": 7750.0, "gex_source": "spy_proxy×10.0361",
             "gex_views": {"magnet": 7700.0, "call_wall_gamma": 7725.0, "put_wall_gamma": 7720.0}}, {"no": "ts"}, {"ts": "2026-10-06T09:32:00-04:00", "gex_views": None}]
    (tmp_path / "reversion" / "2026-10-06.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    diary = inputs.load_diary_rows(tmp_path, "2026-10-06")
    assert [r["magnet"] for r in diary] == [7700.0, None] and diary[0]["call_wall"] == 7750.0 and diary[1]["put_wall"] is None
    assert (diary[0]["call_wall_gamma"], diary[0]["put_wall_gamma"], diary[0]["gex_source"]) == (7725.0, 7720.0, "spy_proxy×10.0361")
    raw = tmp_path / "lob_flow" / "raw"
    (raw / "2026-10-05").mkdir(parents=True)
    (raw / "2026-10-06").mkdir()
    sweep = {"ts": "2026-10-06T10:00:03-04:00", "buckets": {"d25_40": {"spread": 0.1, "size": 200}}}
    (raw / "2026-10-06" / "sweeps.jsonl").write_text(json.dumps(sweep) + "\n{bad\n" + json.dumps({"gap": True, "ts": "2026-10-06T10:01:03-04:00"}) + "\n")
    with gzip.open(raw / "2026-10-05" / "sweeps.jsonl.gz", "wt") as f:
        f.write(json.dumps({**sweep, "ts": "2026-10-05T10:00:03-04:00"}) + "\n")
    assert inputs.load_quote_sweeps(tmp_path, "2026-10-06") == [("2026-10-06T10:00:03-04:00", 0.1, "d25_40")]
    assert list(inputs.load_quote_sweeps_by_day(tmp_path, "2026-10-06")) == ["2026-10-05"]


def test_a_prior_sessions_diary_row_is_the_newest_by_the_clock_within_the_slack(tmp_path):
    (tmp_path / "reversion").mkdir()
    day = "2026-10-05"
    rows = [{"ts": f"{day}T{hm}:04-04:00", "spot": 7700.0 + i, "sigma": 70.0, "gex_source": "native",
             "gex_views": {"magnet": 7700.0, "mass_by_strike": [[7700.0, 1.0]], "net_by_strike": [[7700.0, 9.0]]}}
            for i, hm in enumerate(("11:00", "11:29", "11:40"))]
    (tmp_path / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = inputs.diary_row_at(tmp_path, day, "11:30:30")
    assert got["spot"] == 7701.0 and got["mass_by_strike"] == [[7700.0, 1.0]] and got["sigma"] == 70.0 and "net_by_strike" not in got
    assert inputs.diary_row_at(tmp_path, day, "11:36:00") is None        # the 11:29 row is more than 5 minutes old by then
    assert inputs.diary_row_at(tmp_path, "2026-10-02", "11:30:30") is None
    inputs._PRIOR_SESSIONS_CACHE.clear()
    h = inputs.load_market_history(tmp_path, "live", day)
    assert h.prior_diary_row_at(day, "11:30:30") is None and h.diary_rows[1]["spot"] == 7701.0     # the read's own day is its rows, cut at the read
    assert inputs.load_market_history(tmp_path, "live", "2026-10-06").prior_diary_row_at(day, "11:30:30")["spot"] == 7701.0
    inputs._PRIOR_SESSIONS_CACHE.clear()


def test_the_collector_records_are_the_trailing_sessions_before_the_day(tmp_path):
    folder = tmp_path / "lob_flow" / "agg"
    folder.mkdir(parents=True)
    line = {"engine": "lob_flow", "snapshot": {"defense": {"k": {"strike": 7700.0, "n_events": 1, "n_unrecovered": 2}}}}
    for day in ("2026-10-01", "2026-10-02", "2026-10-05"):
        (folder / f"{day}.jsonl").write_text(json.dumps({**line, "ts": f"{day}T11:00:00-04:00"}) + "\n")
    assert list(inputs.load_collector_records(tmp_path, "2026-10-05")) == ["2026-10-01", "2026-10-02"]
    assert list(inputs.load_collector_records(tmp_path, "2026-10-05", sessions=1)) == ["2026-10-02"]


def test_market_history_caches_the_prior_sessions_for_the_day_and_reads_the_day_fresh(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(inputs, "load_context_bars_by_day", lambda *a, **k: calls.append("prior") or {"2026-10-05": {"SPY": []}})
    monkeypatch.setattr(inputs, "load_context_quotes_as_bars", lambda *a, **k: calls.append("today") or {"SPY": [{"ts": "2026-10-06T09:30:00-04:00", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}]})
    inputs._PRIOR_SESSIONS_CACHE.clear()
    first = inputs.load_market_history(tmp_path, "live", "2026-10-06")
    second = inputs.load_market_history(tmp_path, "live", "2026-10-06")
    assert calls.count("prior") == 1 and calls.count("today") == 2
    assert first.today_context_from_quotes and first.today_quote_bars == {"SPY": first.context_bars_by_day["2026-10-06"]["SPY"]}
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
    assert saved == live == "new high, confirmed"


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


def test_the_option_spread_falls_back_to_the_next_delta_bucket_when_the_near_one_is_empty(tmp_path):
    import json
    from spx_jev.mirai_prediction import code_feature_inputs as inputs
    folder = tmp_path / inputs.SWEEPS_SUBDIR / "2026-10-06"
    folder.mkdir(parents=True)
    rows = [{"ts": "2026-10-06T13:00:00-04:00", "buckets": {"d25_40": {"spread": 1.5}, "d10_25": {"spread": 0.9}}},
            {"ts": "2026-10-06T14:00:00-04:00", "buckets": {"d25_40": {}, "d10_25": {"spread": 0.8}}},
            {"ts": "2026-10-06T15:00:00-04:00", "buckets": {"d00_10": {"spread": 0.3}}},
            {"ts": "2026-10-06T15:30:00-04:00", "buckets": {}}]
    (folder / "sweeps.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert [(s, b) for _, s, b in inputs.load_quote_sweeps(tmp_path, "2026-10-06")] == [(1.5, "d25_40"), (0.8, "d10_25"), (0.3, "d00_10")]


# ---------------------------------------------------------------- VOLATILITY-18: a compressed range, coiled or resting

WIDE_THEN_QUIET = [7700.0] * 60 + [7700.0 + (i % 2) * 8.0 for i in range(30)] + [7708.0 - (i % 2) * 0.2 for i in range(30)]
IV_FLAT = {"iv": {"trend_30min": "over the last 30 minutes at-the-money implied volatility stayed within the 0.8 vol point flat band, changing +0.1 vol points"}}
IV_RISING = {"iv": {"trend_30min": "over the last 30 minutes at-the-money implied volatility rose 1.20 vol points, more than the 0.8 point flat band"}}


def test_volatility_18_coiled_or_resting_only_when_the_range_is_compressed(tmp_path):
    quiet = bars_from_closes(WIDE_THEN_QUIET, wick=0.05)
    h = history(calendar_path=calendar(tmp_path, []))
    assert ask("VOLATILITY-04", h, quiet) == "compressed"
    assert ask("VOLATILITY-18", h, quiet, labels=IV_FLAT) == "resting"                        # nothing underneath
    assert ask("VOLATILITY-18", h, quiet, labels=IV_RISING) == "coiled"                       # implied volatility trending
    fomc = history(calendar_path=calendar(tmp_path, [{"date": DAY, "time_et": "12:15", "kind": "FOMC", "tier": 1}]))
    assert ask("VOLATILITY-18", fomc, quiet, labels=IV_FLAT) == "coiled"                      # the Fed's decision within the hour
    later = history(calendar_path=calendar(tmp_path, [{"date": DAY, "time_et": "14:00", "kind": "FOMC", "tier": 1}]))
    assert ask("VOLATILITY-18", later, quiet, labels=IV_FLAT) == "resting"                    # 150 minutes away is not near
    data = history(calendar_path=calendar(tmp_path, [{"date": DAY, "time_et": "12:00", "kind": "ISM_SERVICES", "tier": "data_10am"}]))
    assert ask("VOLATILITY-18", data, quiet, labels=IV_FLAT) == "resting"                     # only the ruler-loading events count
    assert ask("VOLATILITY-18", h, bars_from_closes([7700.0] * 90 + [7700.0 + (i % 2) * 8.0 for i in range(30)], wick=0.05), labels=IV_RISING) is None
    uncovered = history(calendar_path=calendar(tmp_path, [], covers_from="2026-10-01"))
    assert ask("VOLATILITY-18", uncovered, quiet) is None                                     # nothing could be measured: no calendar, no label


def test_volatility_18_reads_the_ten_year_yields_move_against_the_same_minute(tmp_path):
    quiet = bars_from_closes(WIDE_THEN_QUIET, wick=0.05)
    h = history(calendar_path=calendar(tmp_path, []))
    for k, d in enumerate(DAYS):
        h.context_bars_by_day[d]["$TNX"] = ctx_bars(d, [41.0 + 0.02 * k] * 390)               # moves of 0 to 0.2 since each prior close
    h.daily_closes = {"$TNX": [{"day": (date.fromisoformat(DAYS[0]) - timedelta(days=1)).isoformat(), "close": 41.0}]
                      + [{"day": d, "close": 41.0} for d in DAYS]}
    _, ctx = feed_day(DAY, seed=0, extra={"$TNX": [43.0] * 120})                             # 20 basis points: the top third
    assert ask("VOLATILITY-18", h, quiet, today_ctx=ctx, labels=IV_FLAT) == "coiled"
    _, ctx = feed_day(DAY, seed=0, extra={"$TNX": [41.0] * 120})
    assert ask("VOLATILITY-18", h, quiet, today_ctx=ctx, labels=IV_FLAT) == "resting"


# ---------------------------------------------------------------- the day's SPY volume, and the night's 1-minute bars

def test_the_days_spy_volume_comes_from_the_siege_box_minute_by_minute(tmp_path):
    (tmp_path / "siege").mkdir()
    (tmp_path / "siege" / "baseline.json").write_text(json.dumps({"days": {"2026-10-06": {"601": 500, "602": 700, "603": 0}}}))
    minutes = inputs.load_spy_minute_volumes(tmp_path, "2026-10-06")
    assert minutes == {601: 500.0, 602: 700.0, 603: 0.0} and inputs.load_spy_minute_volumes(tmp_path, "2026-10-07") == {}
    quote = lambda hh, mm, ss, last, volume: {"ts": f"2026-10-06T{hh}:{mm}:{ss}-04:00", "open": last, "high": last, "low": last, "close": last, "volume": volume}
    today = {"SPY": [quote("10", "00", "40", 770.0, 9999.0), quote("10", "01", "50", 771.0, 1.0), quote("10", "03", "05", 772.0, 5.0)],
             "QQQ": [quote("10", "00", "40", 600.0, 3.0)]}
    out = inputs.with_siege_spy_volume(today, minutes, "2026-10-06", from_quotes=True)
    assert [(b["ts"], b["close"], b["volume"]) for b in out["SPY"]] == [("2026-10-06T10:01:00-04:00", 771.0, 500.0),
                                                                        ("2026-10-06T10:02:00-04:00", 771.0, 700.0),
                                                                        ("2026-10-06T10:03:00-04:00", 772.0, 0.0)]
    assert out["QQQ"] == today["QQQ"]
    assert [b["volume"] for b in inputs.with_siege_spy_volume(today, {}, "2026-10-06", from_quotes=True)["SPY"]] == [0.0, 0.0, 0.0]
    assert inputs.with_siege_spy_volume(today, {}, "2026-10-06", from_quotes=False) == today        # a saved day keeps its bars


def test_the_night_loader_keeps_the_one_minute_bars_only(tmp_path):
    folder = tmp_path / "spx_jev" / "overnight"
    folder.mkdir(parents=True)
    rows = [{"ts": "2026-10-05T18:00:00-04:00", "symbol": "/ES", "session": "overnight", "bar_minutes": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"ts": "2026-10-05T18:00:00-04:00", "symbol": "/ES", "session": "overnight", "bar_minutes": 5, "open": 1, "high": 9, "low": 0.1, "close": 1.5},
            {"ts": "2026-10-05T15:59:00-04:00", "symbol": "/ES", "session": "regular", "bar_minutes": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]
    (folder / "2026-10-06.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert [b["high"] for b in inputs.load_night_bars(tmp_path, "2026-10-06")] == [2.0]


def test_trend_07_reads_the_same_spy_closes_live_and_after_the_day_is_saved(tmp_path):
    """Live the day's SPY is its quotes; once saved, its bars, a few cents apart near VWAP. Both paths take the quotes' prices
    and the siege box's volume, so TREND-07 answers the same (10-07 11:59, 14:00 and 14:31 had flipped)."""
    day = DAY
    path = tuple(770.0 + 0.03 * ((i // 7) % 2 * 2 - 1) for i in range(120))               # SPY swinging a few cents round its VWAP
    lines = [{"ts": f"{day}T{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}:40-04:00", "quotes": {"SPY": {"last": p, "volume": 1000 * (i + 1)}}}
             for i, p in enumerate(path)]
    ctx = tmp_path / "spx_jev" / "context"
    (ctx / "bars").mkdir(parents=True)
    (ctx / f"{day}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    (tmp_path / "siege").mkdir()
    (tmp_path / "siege" / "baseline.json").write_text(json.dumps({"days": {day: {str(570 + i): 1000 for i in range(120)}}}))
    inputs._PRIOR_SESSIONS_CACHE.clear()
    live = inputs.load_market_history(tmp_path, "live", day).context_bars_by_day[day]["SPY"]
    saved = [{"ts": f"{day}T{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}:00-04:00", "bars": {"SPY": {"ts": f"{day}T{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}:00-04:00",
              "open": p, "high": p, "low": p, "close": p - 0.04, "volume": 1000}}} for i, p in enumerate(path)]
    (ctx / "bars" / f"{day}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in saved))
    after = inputs.load_market_history(tmp_path, "live", day).context_bars_by_day[day]["SPY"]
    inputs._PRIOR_SESSIONS_CACHE.clear()
    assert live == after and live[0]["volume"] == 1000.0
    h = history()
    answers = [ask("TREND-07", h, bars_from_closes([7700.0] * 120), today_ctx={**feed_day(DAY, seed=0)[1], "SPY": spy}) for spy in (live, after)]
    assert answers[0] == answers[1] and answers[0] is not None
