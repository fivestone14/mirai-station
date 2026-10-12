"""Tests for lefteye_fetcher.live_spot's failure log.

No network: the Schwab client is a stub returning canned responses, and the
five-minute log throttle runs on a fake clock."""
import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lefteye_fetcher as lf  # noqa: E402

LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} "
                  r"live price for SPX failed: (.+)$")


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, result):
        self.result = result

    def get_quote(self, sym):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def spot_env(monkeypatch):
    """A stubbed client, a fake clock and a clean throttle; returns (client, clock)."""
    client = FakeClient(FakeResponse(401))
    clock = [1000.0]
    monkeypatch.setattr(lf, "_client", lambda: client)
    monkeypatch.setattr(lf, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(lf, "_spot_fail_logged_at", None)
    return client, clock


def _logged(capsys):
    return [ln for ln in capsys.readouterr().err.splitlines() if ln]


def test_a_failed_quote_logs_once_and_again_only_after_five_minutes(spot_env, capsys):
    _, clock = spot_env
    assert lf.live_spot("SPX") is None
    lines = _logged(capsys)
    assert len(lines) == 1
    assert LINE.match(lines[0]).group(1) == "HTTP 401"

    clock[0] += 299
    assert lf.live_spot("SPX") is None
    assert _logged(capsys) == []

    clock[0] += 2
    assert lf.live_spot("SPX") is None
    assert len(_logged(capsys)) == 1


def test_a_raised_error_is_logged_with_its_type(spot_env, capsys):
    client, _ = spot_env
    client.result = ConnectionError("reset by peer")
    assert lf.live_spot("SPX") is None
    lines = _logged(capsys)
    assert len(lines) == 1
    assert LINE.match(lines[0]).group(1) == "ConnectionError: reset by peer"


def test_a_good_quote_logs_nothing(spot_env, capsys):
    client, _ = spot_env
    client.result = FakeResponse(200, {"$SPX": {"quote": {"lastPrice": 6512.25}}})
    assert lf.live_spot("SPX") == 6512.25
    assert _logged(capsys) == []
