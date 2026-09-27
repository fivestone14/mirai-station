"""The market-context collector and its backfill, offline: the Schwab calls are stand-ins."""
from __future__ import annotations

import json
from datetime import date, timedelta

from conftest import DAY, at
from spx_jev import market_context, schwab
from spx_jev.market_context import ALL_SYMBOLS, BAR_SYMBOLS, NO_HISTORY, append_snapshot, backfill, snapshot
from spx_jev.state_builder import load_market_context


def _bar(t, close):
    return {"ts": t.isoformat(), "open": close, "high": close, "low": close, "close": close, "volume": 0.0}


def _no_sleep(monkeypatch):
    monkeypatch.setattr(schwab, "CALL_SPACING_S", 0.0)


def test_a_snapshot_quotes_what_schwab_quotes_and_takes_a_finished_bar_for_breadth(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    asked = []

    def quotes(symbols):
        asked.append(list(symbols))
        return {s: {"lastPrice": 100.0, "closePrice": 99.0, "totalVolume": 5, "quoteTime": 1} for s in symbols if s != "/ES"}

    def minute_bars(symbol, start, end):
        return [_bar(at(10, 0), 300.0), _bar(at(10, 1), 310.0), _bar(at(10, 2), 999.0)]   # the 10:02 bar is still running
    monkeypatch.setattr(schwab, "quotes", quotes)
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    line = snapshot(at(10, 2, ss=40))
    assert asked == [[s for s in ALL_SYMBOLS if s not in BAR_SYMBOLS]]
    assert set(line["bars"]) == set(BAR_SYMBOLS) and line["bars"]["$TICK"]["close"] == 310.0
    assert line["quotes"]["XLK"] == {"last": 100.0, "close": 99.0, "volume": 5, "quote_time": 1} and "/ES" not in line["quotes"]
    path = append_snapshot(tmp_path, line)
    assert path.name == f"{DAY}.jsonl"
    mk = load_market_context(tmp_path, DAY)
    assert mk.last("XLK", at(10, 3)) == 100.0 and mk.last("$TICK", at(10, 3)) == 310.0


def test_a_failed_call_costs_only_what_it_would_have_fetched(monkeypatch):
    _no_sleep(monkeypatch)

    def quotes(symbols):
        raise TimeoutError("slow")

    def minute_bars(symbol, start, end):
        if symbol == "$ADD":
            raise ConnectionError("no route")
        return [_bar(at(10, 0), 1.0)]
    monkeypatch.setattr(schwab, "quotes", quotes)
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    line = snapshot(at(10, 1, ss=5))
    assert line["quotes"] == {} and "$ADD" not in line["bars"] and "$TICK" in line["bars"]
    assert line["failed"] == ["quotes: TimeoutError", "$ADD: ConnectionError"]


def test_the_backfill_writes_each_past_session_whole_skips_what_is_on_disk_and_quiet_days(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def minute_bars(symbol, start, end):
        calls.append((symbol, start.date()))
        if start.date() == date(2026, 9, 17):
            return []                                                    # a day Schwab has nothing for
        return [_bar(start, 10.0), _bar(start + timedelta(minutes=1), 11.0), _bar(end, 12.0)]   # the bar at the close is outside
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    written = backfill(tmp_path, date(2026, 9, 17), date(2026, 9, 20))   # Thursday to Sunday
    assert [p.stem for p in written] == ["2026-09-18"]
    assert {d for _, d in calls} == {date(2026, 9, 17), date(2026, 9, 18)}   # the weekend is never asked
    assert not any(s in NO_HISTORY for s, _ in calls)
    lines = [json.loads(l) for l in written[0].read_text().splitlines()]
    assert [l["ts"] for l in lines] == [at(9, 31).isoformat(), at(9, 32).isoformat()] and len(lines[0]["bars"]) == len(ALL_SYMBOLS) - len(NO_HISTORY)
    calls.clear()
    assert backfill(tmp_path, date(2026, 9, 18), date(2026, 9, 18)) == [] and calls == []
    mk = load_market_context(tmp_path, "2026-09-18")
    assert mk.first("XLK") == 10.0 and mk.last("XLK", at(9, 32)) == 11.0


def test_the_command_never_touches_a_state_dir_it_was_not_given(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(schwab, "quotes", lambda symbols: {})
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [])
    assert market_context.main(["--state-dir", str(tmp_path)]) == 1          # nothing came back: a failed run
    assert list((tmp_path / "spx_jev" / "context").glob("*.jsonl"))          # the empty snapshot is still on the record


def test_bitcoin_futures_are_quoted_and_read_under_their_root(tmp_path, monkeypatch):
    """/MBT, the bitcoin labels' session feed, answers under its front contract and is read back as /MBT."""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(schwab, "quotes", lambda symbols: {("/MBTV26" if s == "/MBT" else s): {"lastPrice": 85000.0} for s in symbols})
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [])
    line = snapshot(at(10, 2, ss=40))
    assert "/MBT" in ALL_SYMBOLS and line["quotes"]["/MBTV26"]["last"] == 85000.0
    append_snapshot(tmp_path, line)
    assert load_market_context(tmp_path, DAY).last("/MBT", at(10, 3)) == 85000.0
