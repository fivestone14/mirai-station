"""schwab_options_feed — the Schwab sweep and the tape rebuilt from stream rows. No network."""
from datetime import datetime
from zoneinfo import ZoneInfo

from lob_flow import daemon
from lob_flow.schwab_options_feed import (BRIDGE_GAP_S, TAPE_SOURCE,
                                          OptionsQuoteFeedSchwab,
                                          OptionsTapeFeedStream)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 11, 10, 30, tzinfo=ET)
DAY = "2026-11-11"
CALL = "SPXW  261111C07600000"
PUT = "SPXW  261111P07600000"


class Clock:
    def __init__(self, start=1_794_412_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def ms(self):
        return int(self.now * 1000)


def row(symbol=CALL, **fields):
    base = {"symbol": symbol, "bid": None, "ask": None, "bid_size": None, "ask_size": None,
            "last_price": None, "last_size": None, "total_volume": None, "trade_ms": None,
            "ts": NOW.isoformat()}
    base.update(fields)
    return base


def drain(feed):
    events, _ = feed.trades_since("SPXW", DAY, [(7600.0, "call")], {"day": DAY, "contracts": {}})
    return events


# -- the sweep -------------------------------------------------------------

def test_sweep_keeps_the_root_the_day_and_the_strike_window():
    book = {"spot": 7600.0, "contracts": [
        {"root": "SPXW", "expiry": DAY, "right": "call", "strike": 7600.0, "bid": 11.7, "ask": 11.8,
         "bid_size": 37, "ask_size": 4, "delta": 0.52},
        {"root": "SPXW", "expiry": DAY, "right": "put", "strike": 7000.0, "bid": 0.05, "ask": 0.1,
         "bid_size": 1, "ask_size": 1, "delta": -0.01},                       # outside ±3%
        {"root": "SPX", "expiry": DAY, "right": "call", "strike": 7600.0, "bid": 1, "ask": 2,
         "bid_size": 1, "ask_size": 1, "delta": 0.5},                         # the monthly root
        {"root": "SPXW", "expiry": "2026-11-12", "right": "call", "strike": 7600.0, "bid": 1,
         "ask": 2, "bid_size": 1, "ask_size": 1, "delta": 0.5},               # another day
    ]}
    asked = []
    feed = OptionsQuoteFeedSchwab(lambda expiry: asked.append(expiry) or book)
    spot, rows = feed.sweep("SPXW", DAY, NOW)
    assert asked == [DAY] and spot == 7600.0
    assert [(r.strike, r.right, r.bid_size, r.delta) for r in rows] == [(7600.0, "call", 37, 0.52)]
    assert rows[0].ts == NOW.isoformat() and rows[0].root == "SPXW" and rows[0].expiry == DAY


def test_sweep_with_no_book_is_an_empty_sweep():
    assert OptionsQuoteFeedSchwab(lambda expiry: None).sweep("SPXW", DAY, NOW) == (None, [])
    assert OptionsQuoteFeedSchwab(lambda expiry: {"spot": None, "contracts": []}).sweep(
        "SPXW", DAY, NOW) == (None, [])


# -- the rebuilt tape ------------------------------------------------------

def test_first_total_is_a_baseline_then_a_rise_is_a_print():
    clock = Clock()
    feed = OptionsTapeFeedStream(clock)
    feed.on_stream_reset()
    feed.on_stream_quote(row(bid=11.7, ask=11.8, bid_size=37, ask_size=4, last_price=11.6,
                             total_volume=37000, trade_ms=clock.ms()))
    assert drain(feed) == []                       # the day's volume so far is not one print
    clock.now += 2
    feed.on_stream_quote(row(last_price=11.8, last_size=5, total_volume=37005, trade_ms=clock.ms()))
    (trade,) = drain(feed)
    assert (trade.strike, trade.right, trade.price, trade.size) == (7600.0, "call", 11.8, 5)
    assert (trade.bid, trade.ask, trade.bid_size, trade.ask_size) == (11.7, 11.8, 37, 4)
    assert trade.ts_ms == clock.ms() and trade.src == TAPE_SOURCE and trade.condition is None
    assert drain(feed) == []                       # handed over once


def test_the_print_meets_the_quote_that_stood_before_its_row():
    clock = Clock()
    feed = OptionsTapeFeedStream(clock)
    feed.on_stream_quote(row(bid=10.0, ask=10.2, bid_size=5, ask_size=6, total_volume=100,
                             last_price=10.1))
    feed.on_stream_quote(row(bid=10.4, ask=10.6, last_price=10.2, total_volume=110,
                             trade_ms=clock.ms()))   # the book moved in the same row
    (trade,) = drain(feed)
    assert (trade.bid, trade.ask) == (10.0, 10.2) and trade.price == 10.2 and trade.size == 10


def test_a_row_without_a_trade_time_is_stamped_on_arrival_not_with_an_older_print():
    clock = Clock()
    feed = OptionsTapeFeedStream(clock)
    feed.on_stream_quote(row(bid=1.0, ask=1.1, total_volume=10, last_price=1.0, trade_ms=clock.ms()))
    clock.now += 300
    feed.on_stream_quote(row(total_volume=12))
    (trade,) = drain(feed)
    assert trade.ts_ms == clock.ms() and trade.price == 1.0 and trade.size == 2


def test_quote_only_rows_and_unknown_symbols_make_no_trades():
    feed = OptionsTapeFeedStream(Clock())
    feed.on_stream_quote(row(bid=1.0, ask=1.1, total_volume=10, last_price=1.0))
    feed.on_stream_quote(row(bid=1.05))
    feed.on_stream_quote(row(symbol="SPY", total_volume=5, last_price=1.0))
    feed.on_stream_quote(row(total_volume=10))      # no rise
    feed.on_stream_quote(row(total_volume=4))       # a total that fell is a re-base, not a print
    assert drain(feed) == []


def test_a_short_reconnect_is_bridged_and_a_long_one_is_counted_as_missed():
    clock = Clock()
    feed = OptionsTapeFeedStream(clock)
    feed.on_stream_reset()
    feed.on_stream_quote(row(bid=1.0, ask=1.1, total_volume=100, last_price=1.0))
    feed.on_stream_reset()                          # drift re-subscribe
    clock.now += 5
    feed.on_stream_quote(row(total_volume=130, last_price=1.1, trade_ms=clock.ms()))
    (bridged,) = drain(feed)
    assert bridged.size == 30 and feed.missed_volume == 0
    feed.on_stream_reset()                          # a real outage
    clock.now += BRIDGE_GAP_S + 60
    feed.on_stream_quote(row(total_volume=500, last_price=1.2, trade_ms=clock.ms()))
    assert drain(feed) == [] and feed.missed_volume == 370
    clock.now += 1
    feed.on_stream_quote(row(total_volume=503, last_price=1.2, trade_ms=clock.ms()))
    assert [t.size for t in drain(feed)] == [3]     # live again from the new base


def test_trades_come_back_oldest_first_and_move_the_cursor():
    clock = Clock()
    feed = OptionsTapeFeedStream(clock)
    for symbol in (CALL, PUT):
        feed.on_stream_quote(row(symbol=symbol, bid=1.0, ask=1.1, total_volume=1, last_price=1.0))
    late, early = clock.ms() + 900, clock.ms() + 100
    clock.now += 1
    feed.on_stream_quote(row(symbol=CALL, total_volume=2, last_price=1.0, trade_ms=late))
    feed.on_stream_quote(row(symbol=PUT, total_volume=3, last_price=1.1, trade_ms=early))
    events, cursor = feed.trades_since("SPXW", DAY, [], {"day": DAY, "contracts": {}})
    assert [(e.right, e.ts_ms) for e in events] == [("put", early), ("call", late)]
    assert cursor == {"day": DAY, "contracts": {"7600.0|put": {"ms": early, "n": 1},
                                                "7600.0|call": {"ms": late, "n": 1}}}


# -- the collector's side of the wiring -------------------------------------

def test_only_book_rows_count_as_quote_revisions_and_every_row_reaches_the_tape():
    mem = daemon._Mem()
    feed = OptionsTapeFeedStream(Clock())
    daemon._on_option_quote(mem, row(bid=1.0, ask=1.1, total_volume=10, last_price=1.0), feed)
    daemon._on_option_quote(mem, row(total_volume=14, last_price=1.1), feed)     # trade only
    assert mem.option_revisions == 1
    assert [t.size for t in drain(feed)] == [4]
    daemon._on_option_quote(mem, row(ask=1.2))       # no tape feed given: as before
    assert mem.option_revisions == 2


def test_the_journal_row_names_its_source_only_when_rebuilt():
    from lob_flow.contracts import TradeEvent
    true_tape = TradeEvent(ts_ms=1, strike=7600.0, right="call", price=1.0, size=1,
                           bid=0.9, ask=1.0, bid_size=1, ask_size=1, condition=18)
    assert "src" not in daemon._tape_row(true_tape)
    rebuilt = TradeEvent(ts_ms=1, strike=7600.0, right="call", price=1.0, size=1,
                         bid=0.9, ask=1.0, bid_size=1, ask_size=1, src=TAPE_SOURCE)
    assert daemon._tape_row(rebuilt)["src"] == TAPE_SOURCE


def test_a_row_carries_the_size_of_its_newest_single_print():
    clock = Clock()
    feed = OptionsTapeFeedStream(clock=clock)
    feed.on_stream_quote(row(bid=1.0, ask=1.2, total_volume=100))
    clock.now += 1
    feed.on_stream_quote(row(last_price=1.2, last_size=60, total_volume=160))   # one print of 60
    clock.now += 1
    feed.on_stream_quote(row(last_price=1.1, last_size=3, total_volume=180))    # 20 merged, last 3
    clock.now += 1
    feed.on_stream_quote(row(last_price=1.1, total_volume=183))                 # LAST_SIZE unchanged: 3
    clock.now += 1
    feed.on_stream_quote(row(last_price=1.1, last_size=500, total_volume=185))  # capped at the row
    sizes = [(e.size, e.last_size) for e in drain(feed)]
    assert sizes == [(60, 60), (20, 3), (3, 3), (2, 2)]


def test_the_journal_writes_last_size_only_when_known():
    from lob_flow.contracts import TradeEvent
    bare = TradeEvent(ts_ms=1, strike=7600.0, right="call", price=1.2, size=5, bid=1.0, ask=1.2,
                      bid_size=1, ask_size=1)
    assert "last_size" not in daemon._tape_row(bare)
    rebuilt = TradeEvent(ts_ms=1, strike=7600.0, right="call", price=1.2, size=5, bid=1.0, ask=1.2,
                         bid_size=1, ask_size=1, src=TAPE_SOURCE, last_size=2)
    assert daemon._tape_row(rebuilt)["last_size"] == 2
