"""The market-context collector and its backfill, offline: the Schwab calls are stand-ins."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from conftest import DAY, at
from spx_jev import market_context, schwab
from spx_jev.market_context import (ALL_SYMBOLS, BAR_SYMBOLS, DERIVED_VOLD, NO_HISTORY, append_snapshot, backfill, derived_vold,
                                    snapshot)
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
    assert asked == [["$SPX", *(s for s in ALL_SYMBOLS if s not in BAR_SYMBOLS)]]          # the index, to price the rest against
    assert set(line["bars"]) == set(BAR_SYMBOLS) and line["bars"]["$TICK"]["close"] == 310.0
    assert line["quotes"]["XLK"] == {"last": 100.0, "close": 99.0, "volume": 5, "quote_time": 1} and "/ES" not in line["quotes"]
    path = append_snapshot(tmp_path, line)
    assert path.name == f"{DAY}.jsonl"
    mk = load_market_context(tmp_path, DAY)
    assert mk.last("XLK", at(10, 3)) == 100.0 and mk.last("$TICK", at(10, 3)) == 310.0 and mk.last("$SPX", at(10, 3)) == 100.0


def test_each_run_saves_every_finished_breadth_minute_not_yet_on_file(tmp_path, monkeypatch):
    """09-28: runs land about 70 s apart and one kept only its newest bar, so one breadth minute in six was never
    saved, nor any minute of a run that failed; the next run fetches them all and now keeps them."""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(schwab, "quotes", lambda symbols: {})
    served = [_bar(at(9, 57), 1.0), _bar(at(9, 58), 2.0), _bar(at(9, 59), 3.0), _bar(at(10, 0), 4.0), _bar(at(10, 1), 5.0)]
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: served)
    append_snapshot(tmp_path, snapshot(at(9, 58, ss=10)))
    line = snapshot(at(10, 2, ss=5), market_context.saved_bars(tmp_path, date.fromisoformat(DAY)))
    assert line["bars"]["$TICK"]["close"] == 5.0
    assert [minute["$TICK"]["close"] for minute in line["earlier"]] == [2.0, 3.0, 4.0]      # 09:57 was on file
    append_snapshot(tmp_path, line)
    lines = [json.loads(l) for l in (tmp_path / "spx_jev" / "context" / f"{DAY}.jsonl").read_text().splitlines()]
    assert [set(l) for l in lines[1:]] == [{"ts", "bars"}] * 3 + [{"ts", "quotes", "bars", "failed"}]
    assert all(l["ts"] == line["ts"] for l in lines[1:])
    mk = load_market_context(tmp_path, DAY)
    assert mk.between("$TICK", at(9, 57), at(10, 2)) == [1.0, 2.0, 3.0, 4.0, 5.0] == mk.between("$TRIN", at(9, 57), at(10, 2))
    append_snapshot(tmp_path, snapshot(at(10, 2, ss=50), market_context.saved_bars(tmp_path, date.fromisoformat(DAY))))
    assert "earlier" not in json.loads((tmp_path / "spx_jev" / "context" / f"{DAY}.jsonl").read_text().splitlines()[-1])
    assert len((tmp_path / "spx_jev" / "context" / f"{DAY}.jsonl").read_text().splitlines()) == 6


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


def test_a_breadth_symbol_served_no_bars_is_counted_failed_and_logged_on_its_own_dated_line(monkeypatch, capsys):
    """09-28: Schwab answered $ADD, $VOLD and $VOLSPD with no bars all day and the job said nothing."""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(schwab, "quotes", lambda symbols: {})
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [] if symbol in ("$ADD", "$VOLD") else [_bar(at(10, 0), 1.0)])
    line = snapshot(at(10, 1, ss=5))
    assert line["failed"] == ["$ADD: empty", "$VOLD: empty"] and "$TICK" in line["bars"]
    err = capsys.readouterr().err.splitlines()
    assert [l.split(" ", 1)[1] for l in err] == ["spx-jev-context :: $ADD returned no minute bars since 09:30 ET",
                                                "spx-jev-context :: $VOLD returned no minute bars since 09:30 ET"]
    assert all(datetime.fromisoformat(l.split(" ", 1)[0]).utcoffset() is not None for l in err)


def test_no_bars_before_the_first_minute_has_finished_is_not_a_failure(monkeypatch, capsys):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(schwab, "quotes", lambda symbols: {})
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [])
    assert snapshot(at(9, 30, ss=5))["failed"] == [] and capsys.readouterr().err == ""


def test_the_backfill_logs_each_symbol_served_no_bars_but_not_a_day_served_none(tmp_path, monkeypatch, capsys):
    _no_sleep(monkeypatch)

    def minute_bars(symbol, start, end):
        if start.date() == date(2026, 9, 17) or symbol == "$VOLSPD":
            return []
        return [_bar(start, 10.0)]
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    assert [p.stem for p in backfill(tmp_path, date(2026, 9, 17), date(2026, 9, 18))] == ["2026-09-18"]
    err = capsys.readouterr().err.splitlines()
    assert [l.split(" ", 1)[1] for l in err] == ["spx-jev-context :: $VOLSPD returned no minute bars for the 2026-09-18 session"]


def _volume(t, open_, close):
    return {"ts": t.isoformat(), "open": open_, "high": max(open_, close), "low": min(open_, close), "close": close, "volume": 0.0}


def test_derived_net_volume_is_what_schwab_served_as_vold_on_a_saved_minute():
    """2026-09-25 15:58: Schwab's $UVOL 286,648 and $DVOL 231,380 (thousands) beside its $VOLD 55,267,959 shares."""
    got = derived_vold({"$UVOL": _volume(at(15, 58), 278942.0, 286648.0), "$DVOL": _volume(at(15, 58), 213628.0, 231380.0)})
    assert got["derived"] == DERIVED_VOLD and got["ts"] == at(15, 58).isoformat()
    assert got["close"] == 55268000.0 and abs(got["close"] / 55267959.0 - 1) < 1e-5 and got["open"] == 65314000.0
    assert "high" not in got and "low" not in got          # a difference's extremes are not the extremes' difference


def test_net_volume_is_not_derived_from_up_and_down_volume_out_of_thousands_or_from_two_minutes():
    """2026-09-28 09:30: Schwab's $UVOL came back 1,671,133,184, thousands of times any saved session's."""
    assert derived_vold({"$UVOL": _volume(at(9, 30), 1671133184.0, 1671133184.0), "$DVOL": _volume(at(9, 30), 405729728.0, 405729728.0)}) is None
    assert derived_vold({"$UVOL": _volume(at(10, 0), 100.0, 110.0), "$DVOL": _volume(at(10, 1), 90.0, 95.0)}) is None
    assert derived_vold({"$UVOL": _volume(at(10, 0), 100.0, 110.0)}) is None


def test_a_snapshot_without_schwab_s_vold_saves_it_derived_and_the_labeller_reads_it(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(schwab, "quotes", lambda symbols: {})
    served = {"$UVOL": 30000.0, "$DVOL": 20000.0}
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [] if symbol == "$VOLD" else [_bar(at(10, 0), served.get(symbol, 1.0))])
    line = snapshot(at(10, 1, ss=5))
    assert "$VOLD: empty" in line["failed"] and line["bars"]["$VOLD"]["derived"] == DERIVED_VOLD
    append_snapshot(tmp_path, line)
    assert load_market_context(tmp_path, DAY).last("$VOLD", at(10, 1)) == 10000000.0


def test_schwab_s_own_vold_is_never_replaced_and_out_of_thousands_volume_is_logged_not_derived(monkeypatch, capsys):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(schwab, "quotes", lambda symbols: {})
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [_bar(at(10, 0), 5.0 if symbol == "$VOLD" else 1.0)])
    assert snapshot(at(10, 1, ss=5))["bars"]["$VOLD"] == _bar(at(10, 0), 5.0)
    served = {"$UVOL": 4418356224.0, "$DVOL": 7794704896.0}
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [] if symbol == "$VOLD" else [_bar(at(10, 0), served.get(symbol, 1.0))])
    capsys.readouterr()
    assert "$VOLD" not in snapshot(at(10, 1, ss=5))["bars"]
    assert capsys.readouterr().err.splitlines()[-1].endswith(
        "$VOLD not derived at 10:01 ET: $UVOL and $DVOL are not one minute's bars in thousands of shares")


def test_the_backfill_never_asks_a_market_holiday(tmp_path, monkeypatch):
    """Labor Day 2026-09-07: Schwab served /ES minute bars, so the backfill wrote a session file for a day the market
    was shut. It asks only trading days."""
    _no_sleep(monkeypatch)
    calls = []

    def minute_bars(symbol, start, end):
        calls.append(start.date())
        return [_bar(start, 10.0)]
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    written = backfill(tmp_path, date(2026, 9, 4), date(2026, 9, 8))                # Friday to the Tuesday after Labor Day
    assert [p.stem for p in written] == ["2026-09-04", "2026-09-08"] and date(2026, 9, 7) not in calls


def test_the_backfill_derives_each_minute_s_net_volume_when_schwab_served_none(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    served = {"$UVOL": (30000.0, 31000.0), "$DVOL": (20000.0, 20500.0)}

    def minute_bars(symbol, start, end):
        if symbol == "$VOLD":
            return []
        return [_bar(start + timedelta(minutes=i), v) for i, v in enumerate(served.get(symbol, (1.0, 1.0)))]
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    [path] = backfill(tmp_path, date(2026, 9, 18), date(2026, 9, 18))
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert [l["bars"]["$VOLD"]["close"] for l in lines] == [10000000.0, 10500000.0]
    assert all(l["bars"]["$VOLD"]["derived"] == DERIVED_VOLD for l in lines)
    assert load_market_context(tmp_path, "2026-09-18").last("$VOLD", at(9, 32)) == 10500000.0


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
