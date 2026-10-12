"""The daily-closes store: one file per symbol rewritten whole from Schwab's daily history, sessions that have closed
only, read point in time. No network: the Schwab call is a stand-in."""
from __future__ import annotations

import json
from datetime import date

from conftest import DAY, at
from spx_jev import daily_closes, save_day, schwab
from spx_jev.daily_closes import SYMBOLS, closes_path, load, save, session_rows, write_symbol


def _daily(day: str, close: float) -> dict:
    """A daily bar as Schwab's history serves it: stamped in the small hours of its session's day."""
    return {"ts": at(1, 0, day=day).isoformat(), "open": close - 1, "high": close + 1, "low": close - 2, "close": close, "volume": 0.0}


SERVED = [_daily("2026-09-07", 6590.0),      # Labor Day: Schwab served a $VIX candle on it, a day with no session
          _daily("2026-09-16", 6600.0), _daily("2026-09-17", 6610.0), _daily(DAY, 6620.0), _daily("2026-09-21", 6630.0)]


def _schwab(monkeypatch, served=None, refuse: str | None = None):
    monkeypatch.setattr(schwab, "CALL_SPACING_S", 0.0)
    asked = []

    def daily_bars(symbol, start, end):
        asked.append(symbol)
        if symbol == refuse:
            raise ConnectionError("no route")
        return list(SERVED if served is None else served)
    monkeypatch.setattr(schwab, "daily_bars", daily_bars)
    return asked


def test_each_market_day_is_one_row_dated_by_its_bar_a_holiday_and_a_session_still_running_left_out():
    rows, off = session_rows(SERVED + [_daily("2026-09-17", 6611.0)], date.fromisoformat(DAY), at(16, 20))
    assert [r["day"] for r in rows] == ["2026-09-16", "2026-09-17", DAY] and off == ["2026-09-07"]   # 09-21 is after the day asked for
    assert rows[1]["close"] == 6611.0                                                # the later served bar of a day stands
    assert rows[2] == {"day": DAY, "ts": at(1, 0).isoformat(), "open": 6619.0, "high": 6621.0, "low": 6618.0, "close": 6620.0,
                       "volume": 0.0, "fetched_at": at(16, 20).isoformat()}


def test_a_winter_candle_stamped_in_the_small_hours_utc_is_filed_under_its_own_day_not_its_eve():
    """After the clocks go back, Schwab's 04:00 UTC stamp on today's candle is 23:00 the day before in New York: read
    by the New York date, today's row would overwrite yesterday's close."""
    winter = {**_daily("2026-11-02", 7000.0), "ts": "2026-11-02T04:00:00+00:00"}
    eve = {**_daily("2026-10-30", 6900.0), "ts": "2026-10-30T05:00:00+00:00"}
    rows, _ = session_rows([eve, winter], date(2026, 11, 2), at(16, 20, day="2026-11-02"))
    assert [(r["day"], r["close"]) for r in rows] == [("2026-10-30", 6900.0), ("2026-11-02", 7000.0)]


def test_a_symbols_file_is_written_whole_and_atomically_so_a_rerun_gives_the_same_file(tmp_path, monkeypatch, capsys):
    asked = _schwab(monkeypatch)
    r = save(tmp_path, date.fromisoformat(DAY), at(16, 20))
    assert r == {"saved": {s: 3 for s in SYMBOLS}, "failed": {}} and asked == list(SYMBOLS)
    assert "$SPX: 1 candles on days that are not market days left out (2026-09-07 to 2026-09-07)" in capsys.readouterr().out
    path = closes_path(tmp_path, "$SPX")
    first = path.read_text()
    assert path.name == "$SPX.jsonl" and len(first.splitlines()) == 3 and not list(path.parent.glob("*.tmp"))
    assert save(tmp_path, date.fromisoformat(DAY), at(16, 20)) == r and path.read_text() == first
    write_symbol(tmp_path, "$SPX", session_rows(SERVED, date(2026, 9, 21), at(16, 20, day="2026-09-21"))[0])
    assert [json.loads(l)["day"] for l in path.read_text().splitlines()] == ["2026-09-16", "2026-09-17", DAY, "2026-09-21"]


def test_a_read_sees_the_sessions_before_its_day_only(tmp_path, monkeypatch):
    _schwab(monkeypatch)
    save(tmp_path, date(2026, 9, 21), at(16, 20, day="2026-09-21"))
    assert [r["day"] for r in load(tmp_path, "$VIX", DAY)] == ["2026-09-16", "2026-09-17"]      # DAY's own close is not known on DAY
    assert [r["day"] for r in load(tmp_path, "$VIX", "2026-09-22")] == ["2026-09-16", "2026-09-17", DAY, "2026-09-21"]
    assert load(tmp_path, "TLT", "2026-09-16") == [] and load(tmp_path, "GLD", DAY) == []     # nothing before, or never saved


def test_a_symbol_whose_call_fails_or_comes_back_empty_keeps_its_file_and_costs_no_other(tmp_path, monkeypatch, capsys):
    _schwab(monkeypatch)
    save(tmp_path, date.fromisoformat(DAY), at(16, 20))
    kept = closes_path(tmp_path, "TLT").read_text()
    _schwab(monkeypatch, refuse="TLT")
    r = save(tmp_path, date(2026, 9, 21), at(16, 20, day="2026-09-21"))
    assert r["failed"] == {"TLT": "ConnectionError"} and set(r["saved"]) == set(SYMBOLS) - {"TLT"}
    assert closes_path(tmp_path, "TLT").read_text() == kept and len(load(tmp_path, "$SPX", "2026-09-22")) == 4
    assert "TLT daily closes failed: ConnectionError" in capsys.readouterr().err
    torn = SERVED + [{"ts": None, "close": 1.0}]
    monkeypatch.setattr(schwab, "daily_bars", lambda symbol, start, end: torn if symbol == "$VIX" else SERVED)
    r = save(tmp_path, date(2026, 9, 21), at(16, 20, day="2026-09-21"))
    assert r["failed"] == {"$VIX": "TypeError"} and set(r["saved"]) == set(SYMBOLS) - {"$VIX"}        # one bar not in shape: its symbol alone
    assert "$VIX daily closes failed: TypeError" in capsys.readouterr().err and len(load(tmp_path, "$VIX", "2026-09-22")) == 4   # kept as it was
    _schwab(monkeypatch, served=[])
    kept = closes_path(tmp_path, "TLT").read_text()
    r = save(tmp_path, date(2026, 9, 21), at(16, 20, day="2026-09-21"))
    assert r == {"saved": {}, "failed": {s: "no daily bars" for s in SYMBOLS}} and closes_path(tmp_path, "TLT").read_text() == kept


def test_the_day_saver_runs_the_step_after_its_days_and_a_failed_symbol_fails_the_run_not_the_days(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(schwab, "minute_bars", lambda symbol, start, end: [])
    monkeypatch.setattr(save_day, "days_to_save", lambda now: [date.fromisoformat(DAY)])
    _schwab(monkeypatch)
    assert save_day.main(["--state-dir", str(tmp_path)]) == 0
    assert "daily closes $SPX 4 sessions" in capsys.readouterr().out            # every served session has closed by now
    assert save_day.last_closed(at(16, 5)) == date.fromisoformat(DAY) and save_day.last_closed(at(16, 2)) == date(2026, 9, 17)
    _schwab(monkeypatch, refuse="$VIX9D")
    assert save_day.main(["--state-dir", str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert "daily closes of $VIX9D not saved: ConnectionError" in out.err and "1 of 1 days saved" in out.out

    def blows_up(*a, **k):
        raise RuntimeError("the vault is locked")
    monkeypatch.setattr(daily_closes, "save", blows_up)
    assert save_day.main(["--state-dir", str(tmp_path)]) == 1                            # never raises into the job
    assert "daily closes failed: RuntimeError: the vault is locked" in capsys.readouterr().err


def test_the_command_by_hand_names_each_symbols_count(tmp_path, monkeypatch, capsys):
    _schwab(monkeypatch)
    assert daily_closes.main(["--state-dir", str(tmp_path), "--through", "2026-09-17"]) == 0
    out = capsys.readouterr().out
    assert all(f"{s}: 2 sessions through 2026-09-17" in out for s in SYMBOLS)
    assert load(tmp_path, "$TNX", "2026-09-30")[-1]["day"] == "2026-09-17"
