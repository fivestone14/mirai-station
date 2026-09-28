"""The Schwab feeds' log lines: every line dated with its offset, and a failed call named with Schwab's reply,
tokens scrubbed. Offline: the replies are stand-ins, and authlib's own client runs on a fake transport."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from conftest import DAY, at
from spx_jev import bars, feed_log, market_context, schwab

STAMP = re.compile(r"^(\S+) (spx-jev-\w+) :: ")
TOKEN = "I0.b2F1dGgyLmJkYy5zY2h3YWIuY29t.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789@"


def _stamped(line: str) -> str:
    """The job named on a log line whose time parses as ISO with an offset."""
    m = STAMP.match(line)
    assert m, line
    assert datetime.fromisoformat(m.group(1)).utcoffset() is not None
    return m.group(2)


class _Reply:
    def __init__(self, status_code: int, text: str):
        self.status_code, self.text = status_code, text


class _HTTPStatusError(Exception):
    """httpx's shape: the reply rides on the error."""
    def __init__(self, reply):
        super().__init__(f"Client error '{reply.status_code}'")
        self.response = reply


def test_a_token_in_schwab_s_reply_never_reaches_the_log():
    text = feed_log.scrub(f'{{"access_token": "{TOKEN}", "refresh_token":"r-short", "note": "Bearer {TOKEN}"}} {TOKEN} ok')
    assert TOKEN not in text and "r-short" not in text and text.endswith(" ok")
    assert feed_log.scrub('{"error_code": "E42", "error": "invalid_request"}') == '{"error_code": "E42", "error": "invalid_request"}'


def test_an_http_error_is_named_with_schwab_s_status_and_body():
    e = _HTTPStatusError(_Reply(429, f'{{"errors": [{{"status": 429, "title": "Too Many Requests", "token": "{TOKEN}"}}]}}'))
    got = feed_log.failure(e)
    assert got.startswith("_HTTPStatusError: Client error '429'; Schwab HTTP 429: ")
    assert "Too Many Requests" in got and TOKEN not in got


def test_a_plain_failure_is_its_type_and_message():
    assert feed_log.failure(ConnectionError("no route")) == "ConnectionError: no route"


def test_a_failed_token_renewal_is_named_with_the_reply_authlib_drops():
    httpx = pytest.importorskip("httpx")
    oauth = pytest.importorskip("authlib.integrations.httpx_client")

    def token_server(request):
        return httpx.Response(400, json={"error": "invalid_request", "error_description": "Bad Request",
                                         "refresh_token": TOKEN, "trace": "abc-123"})
    client = oauth.OAuth2Client(client_id="app", client_secret="secret", transport=httpx.MockTransport(token_server),
                                token_endpoint="https://api.schwabapi.com/v1/oauth/token",
                                token={"access_token": "a", "refresh_token": "r", "token_type": "Bearer", "expires_at": 1})
    with pytest.raises(Exception) as caught:
        client.get("https://api.schwabapi.com/marketdata/v1/quotes")
    got = feed_log.failure(caught.value)
    assert got.startswith("OAuthError: invalid_request: Bad Request; Schwab HTTP 400: ")
    assert '"trace":"abc-123"' in got and TOKEN not in got


def test_a_timeout_after_a_good_quote_batch_is_not_named_with_that_batch_s_answer(monkeypatch):
    monkeypatch.setattr(schwab, "CALL_SPACING_S", 0.0)

    class _Client:
        calls = 0

        def get_quotes(self, batch):
            _Client.calls += 1
            if _Client.calls > 1:
                raise TimeoutError("read timed out")
            reply = _Reply(200, '{"SPY":{"quote":{"lastPrice":1}}}')
            reply.raise_for_status = lambda: None
            reply.json = lambda: {"SPY": {"quote": {"lastPrice": 1}}}
            return reply

    class _Fetcher:
        @staticmethod
        def _client():
            return _Client()
    monkeypatch.setattr(schwab, "_fetcher", lambda: _Fetcher)
    with pytest.raises(TimeoutError) as caught:
        schwab.quotes([f"S{i}" for i in range(schwab.QUOTE_BATCH + 1)])
    assert feed_log.failure(caught.value) == "TimeoutError: read timed out"


def test_a_failed_bars_run_is_dated_and_names_schwab_s_reply(tmp_path, monkeypatch, capsys):
    def broken(*a, **k):
        raise _HTTPStatusError(_Reply(503, "upstream down"))
    monkeypatch.setattr(schwab, "minute_bars", broken)
    assert bars.run(tmp_path, DAY, now=at(10, 0)) == 1
    err = capsys.readouterr().err.strip()
    assert _stamped(err) == "spx-jev-bars" and err.endswith(f"fetch failed for {DAY}: _HTTPStatusError: Client error '503'; Schwab HTTP 503: upstream down")


def test_a_good_bars_run_is_dated(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(schwab, "minute_bars", lambda *a, **k: [])
    assert bars.run(tmp_path, DAY, now=at(10, 0)) == 0
    assert _stamped(capsys.readouterr().out.strip()) == "spx-jev-bars"


def test_every_context_line_is_dated_and_each_failure_names_its_reply(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(schwab, "CALL_SPACING_S", 0.0)

    def quotes(symbols):
        raise _HTTPStatusError(_Reply(401, "expired"))
    monkeypatch.setattr(schwab, "quotes", quotes)
    monkeypatch.setattr(schwab, "minute_bars", lambda *a, **k: [])
    assert market_context.main(["--state-dir", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert _stamped(out.strip()) == "spx-jev-context" and "failed quotes: _HTTPStatusError" in out
    assert [_stamped(l) for l in err.splitlines()] == ["spx-jev-context"]
    assert err.strip().endswith("quotes failed: _HTTPStatusError: Client error '401'; Schwab HTTP 401: expired")
