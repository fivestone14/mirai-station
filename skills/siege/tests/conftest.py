"""Shared fixtures: everything on tmp_path, zero network, synthetic bars."""
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siege import engine  # noqa: E402
from siege.contracts import SiegeConfig  # noqa: E402
from siege.store import SiegeStore  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2026-07-07"          # a mid-week session, no events


def at(hh, mm, day=DAY):
    y, mo, d = (int(x) for x in day.split("-"))
    return datetime(y, mo, d, hh, mm, tzinfo=ET)


def make_bars(end_min, start_min=570, price=636.0, vol=1000,
              price_fn=None, vol_fn=None, flat=False, day=DAY):
    """Synthetic SPY 1-min bars [start_min, end_min] (ET minutes-from-midnight)."""
    bars = []
    for m in range(start_min, end_min + 1):
        p = price_fn(m) if price_fn else price
        v = vol_fn(m) if vol_fn else vol
        o, h, l, c = (p, p, p, p) if flat else (p, p + 0.2, p - 0.2, p)
        bars.append({"ts": f"{day}T{m // 60:02d}:{m % 60:02d}:00-04:00",
                     "open": o, "high": h, "low": l, "close": c, "volume": v})
    return bars


def seed_baseline(store, days=21, vol_of_day=lambda i: 1000):
    """A baseline warmed with `days` prior weekdays of constant minute volume
    vol_of_day(i) — the 'boring month' every touch window is judged against."""
    data = {"days": {}}
    d, i = date(2026, 7, 6), 0
    while i < days:
        if d.weekday() < 5:
            data["days"][d.isoformat()] = {str(m): vol_of_day(i)
                                           for m in range(570, 960)}
            i += 1
        d -= timedelta(days=1)
    store.write_baseline(data)


def run(store, hh, mm, spot, towers, bars, sigma=40.0, cfg=None, day=DAY):
    return engine.scan(store, now=at(hh, mm, day), spot=spot, sigma=sigma,
                       towers=towers, spy_bars=bars, cfg=cfg)


@pytest.fixture
def cfg():
    return SiegeConfig()


@pytest.fixture
def store(tmp_path):
    return SiegeStore(tmp_path / "siege")


@pytest.fixture
def bars_factory():
    return make_bars


@pytest.fixture
def baseline_seeder():
    return seed_baseline


@pytest.fixture
def scanner():
    return run


@pytest.fixture
def clock():
    return at
