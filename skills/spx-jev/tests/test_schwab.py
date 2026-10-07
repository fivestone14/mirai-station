"""The Schwab link, offline: a stand-in client records what each call asks for."""
from __future__ import annotations

from datetime import timezone
from types import SimpleNamespace

import pytest

from conftest import at
from spx_jev import bars, schwab


class _Answer:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class _Client:
    def __init__(self):
        self.asked = []

    def _history(self, name, **kw):
        self.asked.append((name, kw))
        return _Answer({"candles": [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 7, "datetime": 1790343000000}]})

    def get_price_history_every_minute(self, **kw):
        return self._history("minute", **kw)

    def get_price_history_every_five_minutes(self, **kw):
        return self._history("five_minutes", **kw)

    def get_price_history_every_day(self, **kw):
        return self._history("day", **kw)

    def get_quotes(self, symbols):
        self.asked.append(("quotes", list(symbols)))
        return _Answer({"/ESZ26": {"quote": {"lastPrice": 6700.0}}, "/ZNZ26": {"quote": {"lastPrice": 112.0}}})


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    fetcher = SimpleNamespace(_client=lambda: c, _schwab_symbol=lambda s: {"SPX": "$SPX"}.get(s, s),
                              _candle_to_bar=lambda k: {"ts": "2026-09-25T09:30:00-04:00", **{f: float(k[f]) for f in ("open", "high", "low", "close", "volume")}})
    monkeypatch.setattr(schwab, "_fetcher", lambda: fetcher)
    return c


def test_minute_bars_ask_for_regular_hours_unless_told_otherwise(client):
    schwab.minute_bars("SPX", at(9, 30), at(16, 0))
    schwab.minute_bars("/ES", at(9, 30), at(16, 0), extended_hours=True)
    (_, regular), (_, extended) = client.asked
    assert regular["need_extended_hours_data"] is False and extended["need_extended_hours_data"] is True
    assert regular["symbol"] == "$SPX" and regular["start_datetime"] == at(9, 30).astimezone(timezone.utc)


def test_the_existing_callers_still_ask_for_regular_hours(client):
    """bars.py and market_context.py call minute_bars without the flag: their requests are unchanged."""
    bars.fetch_session(at(9, 30).date())
    assert [(name, kw["need_extended_hours_data"]) for name, kw in client.asked] == [("minute", False)]


def test_five_minute_bars_use_the_five_minute_history(client):
    out = schwab.five_minute_bars("/ES", at(9, 30), at(16, 0), extended_hours=True)
    assert client.asked[0][0] == "five_minutes" and out[0]["close"] == 1.5


def test_daily_bars_use_the_daily_history_regular_hours_in_the_station_bar_shape(client):
    out = schwab.daily_bars("SPX", at(9, 30, day="2016-09-25"), at(16, 0))
    name, kw = client.asked[0]
    assert name == "day" and kw["symbol"] == "$SPX" and kw["need_extended_hours_data"] is False
    assert kw["start_datetime"] == at(9, 30, day="2016-09-25").astimezone(timezone.utc) and out[0]["close"] == 1.5


def test_front_contracts_name_each_root_by_the_key_schwab_answers_with(client):
    assert schwab.front_contracts(["/ES", "/ZN", "/BTC"]) == {"/ES": "/ESZ26", "/ZN": "/ZNZ26"}
    assert client.asked == [("quotes", ["/ES", "/ZN", "/BTC"])]
