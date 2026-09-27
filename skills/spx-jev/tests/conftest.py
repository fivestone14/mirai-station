"""Offline fixtures: synthetic SPX diary rows, minute bars and market context. No network, no host state."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ET = timezone(timedelta(hours=-4))
DAY = "2026-09-18"
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLC", "XLP", "XLU", "XLB", "XLRE")


def at(hh: int, mm: int, day: str = DAY, ss: int = 0) -> datetime:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=ET)


def bars_from_closes(closes: list[float], day: str = DAY, wick: float = 0.5) -> list[dict]:
    """One bar per minute from 09:30, each closing at the given value. SPX bars carry no volume."""
    out = []
    prev = closes[0]
    t = at(9, 30, day)
    for i, c in enumerate(closes):
        o = prev
        out.append({"ts": (t + timedelta(minutes=i)).isoformat(), "open": o, "high": max(o, c) + wick,
                    "low": min(o, c) - wick, "close": c, "volume": 0.0})
        prev = c
    return out


def flat_bars(n: int, price: float = 7700.0, day: str = DAY, wick: float = 0.5) -> list[dict]:
    return bars_from_closes([price] * n, day=day, wick=wick)


def make_row(ts: datetime, spot: float, sigma: float = 75.0, **over) -> dict:
    """One raw SPX diary row as the left-eye scanner writes it: the fields the labeller reads, and a
    few it does not (the adapter drops those)."""
    row = {
        "ts": ts.isoformat(), "ticker": "SPX", "spot": spot, "sigma": sigma, "sigma_anchor": sigma, "sigma_live": sigma,
        "prior_close": spot - 0.2 * sigma, "vwap": spot - 0.2 * sigma, "atm_iv": 0.155, "vix_ts": 0.83,
        "call_wall": spot + 0.5 * sigma, "put_wall": spot - sigma, "call_wall_tenor": spot + 0.5 * sigma, "put_wall_tenor": spot - 2 * sigma,
        "gamma_sign": "positive", "gamma_flip": spot - 10.0,
        "range_ruler": {"em_points": 16.4, "em_open": 22.0, "em_consumed": 0.8, "vol_carry": {"vix": 15.0, "vix3m": 18.0}},
        "adaptive_em": {"down_share": 0.52, "em_up_pts": 15.8},
        "dex_views": {"dex_above_spot": 0.43, "net_dex_by_strike": [[7700.0, 1.0]]},
        "profile_ladder": {"state": "positive", "gwc": 7750.0},
        "gex_views": {"gamma_above_spot": 480.0, "gamma_below_spot": 330.0, "call_wall_gamma_share": 0.07,
                      "put_wall_gamma_share": 0.04, "pin_top_share": 0.06, "magnet": 7700.0,
                      "shove": {"shove_up_margin": 1.4, "shove_down_margin": 0.4},
                      "vol_gross_by_strike": [[7650.0, 3000], [7700.0, 9000], [7725.0, 5000], [7750.0, 6000], [7800.0, 2000]],
                      "vol_side_by_strike": [[7650.0, 300, 2700], [7700.0, 4500, 4500], [7725.0, 3000, 2000], [7750.0, 4000, 2000], [7800.0, 1500, 500]],
                      "oi_side_by_strike": [[7650.0, 100, 900], [7700.0, 1000, 1500], [7725.0, 800, 700], [7750.0, 1200, 300], [7800.0, 400, 100]],
                      "regime": "long_gamma", "regime_tenor": "long_gamma", "regime_source": "0dte"},
        "dated_gex": {"staleness": "fresh", "as_of": ts.isoformat(), "bands": [
            {"expiry": "2026-10-16", "band": "monthly", "dte": 28, "gamma_mass": 6.0e10, "top_calls": [[8000.0, 1]]},
            {"expiry": "2026-12-31", "band": "quarter_end", "dte": 104, "gamma_mass": 2.0e10}]},
        "siege": {"verdict": "QUIET"}, "watchtower": {"read": "not the labeller's"},
    }
    row.update(over)
    return row


def context_line(ts: datetime, quotes: dict[str, float] | None = None, bars: dict[str, tuple[datetime, float]] | None = None) -> dict:
    """One market-context snapshot line as market_context.snapshot writes it."""
    return {"ts": ts.isoformat(timespec="seconds"),
            "quotes": {s: {"last": v, "close": v, "volume": 0, "quote_time": 0} for s, v in (quotes or {}).items()},
            "bars": {s: {"ts": t.isoformat(), "open": v, "high": v, "low": v, "close": v, "volume": 0.0} for s, (t, v) in (bars or {}).items()},
            "failed": []}


def write_state(root: Path, day: str, rows: list[dict], bars: list[dict], prior_days: dict[str, list[dict]] | None = None,
                context: list[dict] | None = None) -> Path:
    """A station state directory: the day's diary and live bars, prior sessions' saved bar files,
    and the day's market-context snapshots."""
    (root / "reversion" / "bars").mkdir(parents=True, exist_ok=True)
    (root / "spx_jev" / "bars").mkdir(parents=True, exist_ok=True)
    (root / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (root / "spx_jev" / "bars" / f"{day}.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bars))
    for d, pbars in (prior_days or {}).items():
        (root / "reversion" / "bars" / f"{d}-SPX.json").write_text(json.dumps(pbars))
    if context is not None:
        (root / "spx_jev" / "context").mkdir(parents=True, exist_ok=True)
        (root / "spx_jev" / "context" / f"{day}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in context))
    return root


def market_at(now: datetime):
    """A MarketContext with the breadth figures a full read needs: advance-decline, a tick reading each
    of the last 30 minutes (21 above zero), and every sector's open and latest value (8 of 11 up)."""
    from spx_jev.state_builder import MarketContext
    known = {"$ADD": [(now - timedelta(minutes=5), 326.0)],
             "$TICK": [(now - timedelta(minutes=30 - i), 150.0 if i % 10 < 7 else -120.0) for i in range(1, 31)]}
    for k, s in enumerate(SECTORS):
        known[s] = [(at(9, 31), 100.0), (now - timedelta(minutes=1), 101.0 if k < 8 else 99.0)]
    return MarketContext(known)


@pytest.fixture
def scene_factory():
    from spx_jev.row_adapter import labeller_row
    from spx_jev.state_builder import Scene

    def make(now: datetime, bars: list[dict], row_over: dict | None = None, rows_before: list[dict] | None = None,
             prior_bars: dict | None = None, spot: float | None = None, market=None,
             bar_clock: bool = False, last_read: datetime | None = None):
        spot = spot if spot is not None else float(bars[-1]["close"]) if bars else 7700.0
        row = labeller_row(make_row(now, spot, **(row_over or {})))
        rows = [labeller_row(r) for r in rows_before or []] + [row]
        done = [b for b in bars if datetime.fromisoformat(b["ts"]) + timedelta(minutes=1) <= now]
        scene = Scene(row=row, rows_today=rows, bars=done, prior_bars=prior_bars or {}, now=now,
                      sigma=float(row["sigma"]), market=market, bar_clock=bar_clock, last_read=last_read)
        if bar_clock:
            # as make_scene does on the bar clock: the unit for the read, ranked against the prior sessions
            from spx_jev.state_builder import tape_unit
            scene.unit = tape_unit(scene.bars, scene.sigma, now, scene.prior_bars)
        return scene

    return make


def prior_sessions(n: int = 10) -> dict[str, list[dict]]:
    """``n`` full prior sessions of bars, newest first, each ranging about 60 points."""
    out = {}
    for d in range(17, 17 - n, -1):
        day = f"2026-09-{d:02d}"
        out[day] = bars_from_closes([7690.0 + 3 * (i % 20) - 0.05 * i for i in range(390)], day=day)
    return out


@pytest.fixture
def full_scene(scene_factory):
    """A moment at which every built label can be measured."""
    sigma = 75.0
    closes = [7700.0 + (i % 5) for i in range(150)] + [7704.0 + sigma * 0.4 * (i + 1) / 30 for i in range(30)]
    now = at(12, 30, ss=10)
    earlier = make_row(now - timedelta(minutes=30), 7702.0, atm_iv=0.14)
    return scene_factory(now, bars_from_closes(closes), rows_before=[earlier], prior_bars=prior_sessions(), market=market_at(now))


@pytest.fixture
def lane_scene(full_scene):
    """The same moment read on the tape lane: on the bar clock, five minutes after an earlier read, with
    the unit; every stretch label can be measured."""
    from dataclasses import replace
    from spx_jev.state_builder import tape_unit
    return replace(full_scene, bar_clock=True, last_read=full_scene.now - timedelta(minutes=5),
                   unit=tape_unit(full_scene.bars, full_scene.sigma, full_scene.now, full_scene.prior_bars))
