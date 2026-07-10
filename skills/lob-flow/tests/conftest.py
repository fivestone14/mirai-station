"""Shared fixtures: everything on tmp_path, zero network anywhere."""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lob_flow.baselines import BaselineStore, key_of, session_block, tod_bin, vix_band  # noqa: E402
from lob_flow.contracts import LobConfig, QuoteRow, TradeEvent  # noqa: E402
from lob_flow.io_state import StateDir  # noqa: E402

ET = ZoneInfo("America/New_York")


@pytest.fixture
def cfg():
    return LobConfig()


@pytest.fixture
def sd(tmp_path):
    return StateDir(tmp_path / "lob")


@pytest.fixture
def now_et():
    """A mid-session weekday moment (Tue 2026-07-07 10:30 ET, no events)."""
    return datetime(2026, 7, 7, 10, 30, tzinfo=ET)


def make_quote(strike=6300.0, right="call", bid=10.0, ask=10.2,
               bid_size=50, ask_size=50, delta=0.5, ts="2026-07-07T10:30:00-04:00",
               expiry="2026-07-07"):
    return QuoteRow(root="SPXW", expiry=expiry, right=right, strike=strike,
                    bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
                    delta=delta, ts=ts)


def make_trade(ts_ms=0, strike=6300.0, right="call", price=10.1, size=10,
               bid=10.0, ask=10.2, bid_size=50, ask_size=50):
    return TradeEvent(ts_ms=ts_ms, strike=strike, right=right, price=price,
                      size=size, bid=bid, ask=ask, bid_size=bid_size,
                      ask_size=ask_size)


@pytest.fixture
def quote_factory():
    return make_quote


@pytest.fixture
def trade_factory():
    return make_trade


def seeded_store(path, cfg, now, keys_values: dict, days=None):
    """A BaselineStore warmed with `days` clean weekdays of identical values —
    the 'boring month' every anomaly is judged against."""
    days = days if days is not None else cfg.baseline_days
    store = BaselineStore(path, cfg)
    d = now.date() - timedelta(days=1)
    added = 0
    while added < days:
        if d.weekday() < 5:
            store.fold_day(d.isoformat(), dict(keys_values), clean=True)
            added += 1
        d -= timedelta(days=1)
    store.save()
    return store


@pytest.fixture
def store_seeder():
    return seeded_store


def chain_keys(cfg, now, metric, value, buckets=None, band="na"):
    """{key: value} across all delta buckets for now's slot."""
    blk = session_block(now.hour * 60 + now.minute, cfg)
    tod = tod_bin(now)
    buckets = buckets or [b for b, _, _ in cfg.delta_buckets]
    return {key_of(metric, b, blk, tod, band): value for b in buckets}


@pytest.fixture
def chain_key_builder():
    return chain_keys
