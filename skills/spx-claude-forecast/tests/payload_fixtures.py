"""A small, offline stand-in for FrozenInputs so every payload block can be tested without the station's files.

The scene is a duck-typed stand-in for ``spx_jev.state_builder.Scene`` with the attributes the blocks read
(``row``, ``rows_today``, ``bars``, ``prior_bars``, ``market``, ``now``, ``sigma``, ``day``, ``spot``,
``minutes_since_open``, ``minutes_to_close``, ``options_tape``, ``state_dir``). ``labels`` carries
``state`` (sentences by group) and ``figures``. Build one with ``fake_inputs(...)`` and override what a
test needs. Real-store smoke tests sit beside these and skip when the station's state is absent.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from spx_claude_forecast.control import ET
from spx_claude_forecast.paths import ForecastPaths
from spx_claude_forecast.payload.frozen_inputs import FrozenInputs, slot_of

DAY = "2026-10-09"
SPOT = 7813.01
SIGMA = 75.31


def at(hh: int, mm: int, ss: int = 0, day: str = DAY) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


def flat_bars(day: str = DAY, until_hh: int = 14, until_mm: int = 29, price: float = SPOT, step: float = 0.0) -> list[dict]:
    """One-minute bars from 09:30 to ``until`` inclusive, each close ``step`` above the last."""
    bars, t, p = [], at(9, 30, day=day), price
    end = at(until_hh, until_mm, day=day)
    while t <= end:
        bars.append({"ts": t.isoformat(), "open": p, "high": p + 0.5, "low": p - 0.5, "close": p, "volume": 1000})
        t, p = t + timedelta(minutes=1), p + step
    return bars


def make_row(ts: datetime, spot: float = SPOT, sigma: float = SIGMA, **over) -> dict:
    row = {"ts": ts.isoformat(), "spot": spot, "sigma": sigma, "sigma_anchor": sigma, "sigma_live": sigma * 0.55,
           "vwap": spot - 15.0, "prior_close": spot - 47.0, "atm_iv": 8.44, "vix_ts": 0.833,
           "range_ruler": {"em_points": 21.0}, "gex_views": {"regime": "long_gamma", "flip": spot - 41.0},
           "dated_gex": None, "siege": None}
    row.update(over)
    return row


class FakeMarket:
    """``known[symbol]`` is a list of ``(datetime, value)``; ``last`` and ``first`` follow MarketContext."""

    def __init__(self, known: dict[str, list[tuple[datetime, float]]] | None = None) -> None:
        self.known = known or {}
        self.bars = {}

    def last(self, symbol: str, now: datetime, max_age_min: int | None = None):
        points = [(t, v) for t, v in self.known.get(symbol, []) if t <= now]
        if not points:
            return None
        t, v = points[-1]
        if max_age_min is not None and now - t > timedelta(minutes=max_age_min):
            return None
        return v

    def first(self, symbol: str):
        points = self.known.get(symbol, [])
        return points[0][1] if points else None

    def between(self, symbol: str, a: datetime, b: datetime):
        return [(t, v) for t, v in self.known.get(symbol, []) if a <= t <= b]

    def change(self, symbol: str, a: datetime, b: datetime):
        pts = self.between(symbol, a, b)
        return (pts[-1][1] - pts[0][1]) if len(pts) >= 2 else None

    def at(self, now: datetime):
        return {s: {"value": self.last(s, now), "known_at": now.isoformat()} for s in self.known}


def fake_inputs(tmp_path: Path, *, cut: datetime | None = None, bars: list[dict] | None = None, row: dict | None = None,
                prior_bars: dict[str, list[dict]] | None = None, market: FakeMarket | None = None,
                labels_state: dict | None = None, code_answers: dict | None = None, flat_zones=None,
                options_book: str | None = "native", dated_book: dict | None = None, spy_minute_volumes: dict | None = None, vix1d_prior_close: float | None = 10.23,
                daily_closes: dict | None = None, events=None, hour_record: dict | None = None,
                read_record: dict | None = None, **scene_over) -> FrozenInputs:
    cut = cut or at(14, 30, 12)
    bars = bars if bars is not None else flat_bars(until_hh=cut.hour, until_mm=max(cut.minute - 1, 0))
    row = row or make_row(cut)
    prior_bars = prior_bars if prior_bars is not None else {}
    close = at(16, 0, day=cut.date().isoformat())
    scene = SimpleNamespace(row=row, rows_today=[row], bars=bars, prior_bars=prior_bars, market=market, now=cut,
                            sigma=row["sigma"], day=cut.date().isoformat(), spot=row["spot"],
                            minutes_since_open=int((cut - at(9, 30, day=cut.date().isoformat())).total_seconds() // 60),
                            minutes_to_close=int((close - cut).total_seconds() // 60), options_tape=None,
                            state_dir=tmp_path / "state", prior_rulers={}, prior_markets={}, prior_day=None, night=[],
                            session_open=at(9, 30, day=cut.date().isoformat()), session_close=close)
    for k, v in scene_over.items():
        setattr(scene, k, v)
    labels = SimpleNamespace(state=labels_state or {}, figures={}, omitted={})
    anchor = SimpleNamespace(points=row["sigma"], source="anchor", estimated=False)
    paths = ForecastPaths(tmp_path / "state")
    return FrozenInputs(state_dir=tmp_path / "state", paths=paths, read_id=f"live:{row['ts']}", lane="live",
                        day=cut.date().isoformat(), row_ts=row["ts"], cut=cut, slot=slot_of(cut),
                        horizon_start=cut.replace(second=0, microsecond=0), scene=scene, labels=labels,
                        read_record=read_record, hour_record=hour_record, code_answers=code_answers or {},
                        flat_zones=flat_zones if flat_zones is not None else {"next_30": 3.41, "next_60": 4.89, "average_30": 2.0},
                        anchor=anchor, options_book=options_book, dated_book=dated_book, spy_minute_volumes=spy_minute_volumes,
                        vix1d_prior_close=vix1d_prior_close, daily_closes=daily_closes or {}, events=events, load_notes=[])


def station_state_dir() -> Path | None:
    """The real station state, for smoke tests that skip without it."""
    root = Path.home() / ".claude" / "plugins" / "mirai-station" / "state"
    return root if (root / "reversion").exists() else None
