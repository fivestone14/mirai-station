"""Offline fixtures: synthetic SNDK PRO rows, minute bars, side packets and a chain cache. No network, no host state."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ET = timezone(timedelta(hours=-4))
DAY = "2026-09-18"


def at(hh: int, mm: int, day: str = DAY, ss: int = 0) -> datetime:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=ET)


def bars_from_closes(closes: list[float], day: str = DAY, volume: float | list[float] = 1000.0,
                     wick: float = 0.5) -> list[dict]:
    """One bar per minute from 09:30, each closing at the given value."""
    out = []
    prev = closes[0]
    t = at(9, 30, day)
    for i, c in enumerate(closes):
        o = prev
        v = volume[i] if isinstance(volume, list) else volume
        out.append({"ts": (t + timedelta(minutes=i)).isoformat(), "open": o, "high": max(o, c) + wick,
                    "low": min(o, c) - wick, "close": c, "volume": v})
        prev = c
    return out


def flat_bars(n: int, price: float = 1700.0, day: str = DAY, volume: float | list[float] = 1000.0) -> list[dict]:
    return bars_from_closes([price] * n, day=day, volume=volume)


def make_row(ts: datetime, spot: float, sigma: float = 45.0, **over) -> dict:
    row = {
        "ts": ts.isoformat(), "ticker": "SNDK", "spot": spot, "sigma": sigma, "sigma_anchor": sigma, "prior_close": spot - 0.5 * sigma,
        "gamma_sign": "positive", "gamma_flip": None, "call_wall": spot + 0.5 * sigma * 2.4, "put_wall": spot - sigma,
        "call_wall_tenor": spot + 0.5 * sigma * 2.4, "put_wall_tenor": spot - 2 * sigma,
        "atm_iv": 0.41, "vwap": spot - 0.2 * sigma,
        "iv_skew": {"put_side_iv": 0.432, "call_side_iv": 0.431, "skew_pts": 0.1, "down_share": 0.5},
        "range_ruler": {"em_open": 34.0, "em_consumed": 0.8},
        "adaptive_em": {"down_share": 0.41},
        "dex_views": {"dex_above_spot": 0.2},
        "net_exposure": {"net_delta_total": 6.0e9, "prev_close_delta": 4.0e9, "prev_close_date": "2026-09-17"},
        "profile_ladder": {"state": "positive transition"},
        "gex_views": {"front_dte": 7, "gamma_above_spot": 3.0e8, "gamma_below_spot": 1.0e8,
                      "call_wall_gamma_share": 0.13, "put_wall_gamma_share": 0.06, "pin_top_share": 0.21,
                      "shove": {"shove_up_margin": 0.8, "shove_down_margin": -0.4},
                      "mass_by_strike": [[1650.0, 900.0], [1700.0, 8000.0], [1750.0, 9000.0], [1800.0, 4000.0], [1850.0, 600.0]],
                      "vol_gross_by_strike": [[1700.0, 5000], [1750.0, 3000], [1800.0, 1000], [1650.0, 500], [1850.0, 500]],
                      "vol_side_by_strike": [[1650.0, 300, 200], [1700.0, 3500, 1500], [1750.0, 2000, 1000], [1800.0, 400, 600], [1850.0, 300, 200]],
                      "oi_side_by_strike": [[1650.0, 400, 300], [1700.0, 2000, 1500], [1750.0, 1800, 1200], [1800.0, 900, 700], [1850.0, 300, 200]],
                      "vol_side_by_strike_next": [[1700.0, 400, 300], [1750.0, 300, 200]]},
        "meta": {"book_asof": ts.isoformat(), "expiries": [{"date": "2026-09-25", "dte": 7}]},
    }
    row.update(over)
    return row


def make_side_packet(bar: int, wall_price: float, wall_visits: int = 2, high_visits: int = 1,
                     rsi_state: str = "overbought", bars_in_state: int = 6, open_episode: bool = True) -> dict:
    return {
        "session": DAY,
        "levels": [
            {"id": f"lvl.wall_call.{wall_price:g}", "price": wall_price, "role": "wall_call", "visits": wall_visits, "interactions_covered_to_bar": bar},
            {"id": "lvl.session_high", "price": wall_price + 5, "role": "session_high", "visits": high_visits, "interactions_covered_to_bar": bar},
        ],
        "episodes": [{"id": "ep.rsi14", "subject_ref": "ind.rsi14", "state": rsi_state, "open": open_episode, "bars_in_state": bars_in_state, "to_bar": bar}],
        "readings": [{"id": "px.bar_range", "value": 3.0, "at_bar": bar, "unit": "points_abs"}],
    }


def make_chain_cache(now: datetime, spot: float, iv_front: float = 0.45, iv_next: float = 0.40,
                     bid: float = 40.0, ask: float = 41.6, bid_size: int = 30, ask_size: int = 20) -> dict:
    strike = round(spot / 5) * 5
    return {
        "ts": (now - timedelta(minutes=2)).isoformat(), "spot": spot, "expiries": ["2026-09-25", "2026-10-02"],
        "contracts": [
            {"right": "call", "strike": strike, "expiry": "2026-09-25", "iv": iv_front, "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size},
            {"right": "put", "strike": strike, "expiry": "2026-09-25", "iv": iv_front + 0.01, "bid": bid, "ask": ask, "bid_size": 10, "ask_size": 10},
            {"right": "call", "strike": strike, "expiry": "2026-10-02", "iv": iv_next, "bid": 50.0, "ask": 52.0, "bid_size": 5, "ask_size": 5},
        ],
    }


@pytest.fixture
def scene_factory():
    from sndk_jev.state_builder import Scene

    def make(now: datetime, bars: list[dict], row_over: dict | None = None, rows_before: list[dict] | None = None,
             prior_bars: dict | None = None, spot: float | None = None, news: dict | None = None,
             side_packet: dict | None = None, chain_cache: dict | None = None, prev_last_row: dict | None = None):
        spot = spot if spot is not None else float(bars[-1]["close"]) if bars else 1700.0
        row = make_row(now, spot, **(row_over or {}))
        rows = list(rows_before or []) + [row]
        done = [b for b in bars if datetime.fromisoformat(b["ts"]) + timedelta(minutes=1) <= now]
        return Scene(row=row, rows_today=rows, bars=done, prior_bars=prior_bars or {}, now=now,
                     sigma=float(row["sigma"]), news=news, side_packet=side_packet, chain_cache=chain_cache,
                     prev_last_row=prev_last_row)

    return make


@pytest.fixture
def full_scene(scene_factory):
    """A moment at which every label in the spec can be measured."""
    sigma = 45.0
    closes = [1700.0 + (i % 5) for i in range(150)] + [1704.0 + sigma * 0.4 * (i + 1) / 30 for i in range(30)]
    prior = {}
    for d in range(17, 7, -1):
        day = f"2026-09-{d:02d}"
        prior[day] = bars_from_closes([1690.0 + 3 * (i % 20) - 0.05 * i for i in range(390)], day=day)
    now = at(12, 30, ss=10)
    earlier = make_row(now - timedelta(minutes=30), 1702.0, atm_iv=0.39)
    earlier["meta"]["book_asof"] = earlier["ts"]
    news = {"headline": "h", "source": "wire", "excerpt": "e", "arrived": at(12, 20).isoformat(),
            "recent_headlines": [], "expectation": "consensus was flat"}
    spot = closes[-1]
    prev = make_row(at(15, 58, day="2026-09-17"), 1690.0)
    return scene_factory(now, bars_from_closes(closes), rows_before=[earlier], prior_bars=prior, news=news,
                         side_packet=make_side_packet(bar=170, wall_price=1750.0),
                         chain_cache=make_chain_cache(now, spot), prev_last_row=prev)
