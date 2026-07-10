"""options_feed — client hygiene + feed parsing/cursors with a faked transport."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lob_flow.options_feed import (McpClient, OptionsQuoteFeedMcp,
                                   OptionsTapeFeedMcp, _to_ms)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 7, 10, 30, tzinfo=ET)


class FakeClient:
    """Stands in for McpClient: records the code, returns a canned result."""

    def __init__(self, result):
        self.result = result
        self.codes = []

    def run(self, code):
        self.codes.append(code)
        return self.result


def test_mcp_client_refuses_http():
    with pytest.raises(RuntimeError, match="https"):
        McpClient("http://insecure", lambda: "tok")


def test_sweep_parses_rows_and_spot():
    fake = FakeClient({"spot": 6301.0, "rows": [
        {"right": "call", "strike": 6300.0, "bid": 10.0, "ask": 10.2,
         "bid_size": 40, "ask_size": 60, "delta": 0.5},
        {"right": "put", "strike": None},                       # junk row skipped
    ]})
    spot, rows = OptionsQuoteFeedMcp(fake).sweep("SPXW", "2026-07-07", NOW)
    assert spot == 6301.0 and len(rows) == 1
    q = rows[0]
    assert q.bid_size == 40 and q.ask_size == 60 and q.delta == 0.5
    assert "options_chain" in fake.codes[0]                     # one round trip
    assert "bid_size" in fake.codes[0]                          # sizes requested


def test_sweep_handles_empty_and_none():
    assert OptionsQuoteFeedMcp(FakeClient(None)).sweep("SPXW", "d", NOW) == (None, [])
    spot, rows = OptionsQuoteFeedMcp(FakeClient({"spot": 6301.0, "rows": []})).sweep(
        "SPXW", "d", NOW)
    assert spot == 6301.0 and rows == []


def test_tape_cursor_dedupes_and_advances():
    t1, t2 = 1751899800000, 1751899860000
    fake = FakeClient({"contracts": {"6300.0|call": [
        {"ts": t1, "price": 10.1, "size": 5, "bid": 10.0, "ask": 10.2,
         "bid_size": 40, "ask_size": 60},
        {"ts": t2, "price": 10.2, "size": 7, "bid": 10.0, "ask": 10.2,
         "bid_size": 40, "ask_size": 53},
    ]}})
    feed = OptionsTapeFeedMcp(fake)
    evs, cur = feed.trades_since("SPXW", "2026-07-07", [(6300.0, "call")],
                                 {"day": "2026-07-07", "contracts": {}})
    assert [e.ts_ms for e in evs] == [t1, t2]
    assert cur["contracts"]["6300.0|call"] == {"ms": t2, "n": 1}
    # same payload again with the advanced cursor -> everything deduped
    evs2, cur2 = feed.trades_since("SPXW", "2026-07-07", [(6300.0, "call")], cur)
    assert evs2 == [] and cur2["contracts"]["6300.0|call"] == {"ms": t2, "n": 1}


def test_tape_same_millisecond_new_print_not_dropped():
    """0DTE bursts print several trades in one millisecond; a NEW same-ms
    print arriving in the next poll must survive the cursor dedupe."""
    t = 1751899800000
    first = FakeClient({"contracts": {"6300.0|call": [
        {"ts": t, "price": 10.1, "size": 5, "bid": 10.0, "ask": 10.2}]}})
    feed = OptionsTapeFeedMcp(first)
    evs, cur = feed.trades_since("SPXW", "2026-07-07", [(6300.0, "call")],
                                 {"day": "2026-07-07", "contracts": {}})
    assert len(evs) == 1 and cur["contracts"]["6300.0|call"]["n"] == 1
    # next poll re-pulls the second: the seen print PLUS a new same-ms one
    second = FakeClient({"contracts": {"6300.0|call": [
        {"ts": t, "price": 10.1, "size": 5, "bid": 10.0, "ask": 10.2},
        {"ts": t, "price": 10.2, "size": 9, "bid": 10.0, "ask": 10.2}]}})
    evs2, cur2 = OptionsTapeFeedMcp(second).trades_since(
        "SPXW", "2026-07-07", [(6300.0, "call")], cur)
    assert len(evs2) == 1 and evs2[0].size == 9               # only the new one
    assert cur2["contracts"]["6300.0|call"] == {"ms": t, "n": 2}


def test_tape_accepts_legacy_int_cursor():
    t = 1751899800000
    fake = FakeClient({"contracts": {"6300.0|call": [
        {"ts": t, "price": 10.1, "size": 5, "bid": 10.0, "ask": 10.2},
        {"ts": t + 1, "price": 10.2, "size": 9, "bid": 10.0, "ask": 10.2}]}})
    evs, _ = OptionsTapeFeedMcp(fake).trades_since(
        "SPXW", "2026-07-07", [(6300.0, "call")],
        {"day": "2026-07-07", "contracts": {"6300.0|call": t}})   # old int form
    assert [e.ts_ms for e in evs] == [t + 1]                  # at-ms skipped, next kept


def test_tape_uses_date_from_date_to():
    """The live tool REQUIRES date_from/date_to — a bare 'date' param made it
    raise per contract, silently emptying the whole first session's tape."""
    fake = FakeClient({"contracts": {}})
    OptionsTapeFeedMcp(fake).trades_since(
        "SPXW", "2026-07-06", [(6300.0, "call")],
        {"day": "2026-07-06", "contracts": {}})
    assert "'date_from'" in fake.codes[0] and "'date_to'" in fake.codes[0]
    assert "'date':" not in fake.codes[0]


def test_tape_requests_start_time_from_cursor():
    fake = FakeClient({"contracts": {}})
    OptionsTapeFeedMcp(fake).trades_since(
        "SPXW", "2026-07-07", [(6300.0, "call")],
        {"day": "2026-07-07", "contracts": {"6300.0|call": 1751899800000}})
    assert "'start'" in fake.codes[0]                           # incremental pull


def test_tape_skips_junk_trades():
    fake = FakeClient({"contracts": {"6300.0|call": [
        {"ts": None, "price": 10.1, "size": 5},                 # no timestamp
        {"ts": 1751899800000, "price": None, "size": 5},        # no price
        {"ts": 1751899800001, "price": 10.1, "size": 0},        # zero size
    ], "garbage": []}})
    evs, _ = OptionsTapeFeedMcp(fake).trades_since(
        "SPXW", "2026-07-07", [(6300.0, "call")],
        {"day": "2026-07-07", "contracts": {}})
    assert evs == []


def test_to_ms_accepts_epoch_and_iso():
    assert _to_ms(1751899800000) == 1751899800000
    assert _to_ms(1751899800.0) == 1751899800000                # epoch seconds
    assert _to_ms("2026-07-07T10:30:00-04:00") == int(
        NOW.timestamp() * 1000)
    assert _to_ms(None) is None and _to_ms("junk") is None


def test_tape_pulls_in_small_batches():
    """~46 focus contracts in ONE server call blows the read timeout and dies
    silently — the puller must chunk."""
    fake = FakeClient({"contracts": {}})
    contracts = [(6000.0 + 5 * i, "call") for i in range(14)]
    OptionsTapeFeedMcp(fake).trades_since(
        "SPXW", "2026-07-06", contracts, {"day": "2026-07-06", "contracts": {}})
    assert len(fake.codes) == 3                               # 6 + 6 + 2
