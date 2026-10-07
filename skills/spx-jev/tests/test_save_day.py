"""The after-close save: the market feed's full day and SPX's own, market days only, once closed, idempotent. No network."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from conftest import DAY, at, flat_bars
from spx_jev import bars, save_day, schwab
from spx_jev.market_context import ALL_SYMBOLS, NO_HISTORY
from spx_jev.save_day import days_to_save
from spx_jev.state_builder import load_bars, load_market_context


def _schwab(monkeypatch, calls):
    """Every symbol serves its full session of flat bars; each call is recorded as (symbol, day)."""
    monkeypatch.setattr(schwab, "CALL_SPACING_S", 0.0)

    def minute_bars(symbol, start, end):
        calls.append((symbol, start.date().isoformat()))
        return flat_bars(390, day=start.date().isoformat())
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    monkeypatch.setattr(schwab, "daily_bars", lambda symbol, start, end: [])     # the daily closes: test_daily_closes.py


def test_today_is_saved_only_once_it_has_closed_and_missed_market_days_are_caught_up():
    friday = date.fromisoformat(DAY)                                     # 2026-09-18
    before = days_to_save(at(16, 2), catch_up=7)
    after = days_to_save(at(16, 5), catch_up=7)
    assert friday not in before and after[-1] == friday
    assert after[0] == date(2026, 9, 11) and all(d.weekday() < 5 for d in after) and len(after) == 6
    assert days_to_save(at(12, 0, day="2026-09-20"), catch_up=0) == []  # a Sunday saves nothing


def test_a_day_saves_the_market_feed_whole_and_fills_a_short_spx_file_once(tmp_path, monkeypatch):
    calls = []
    _schwab(monkeypatch, calls)
    bars.append_day(tmp_path, DAY, flat_bars(200), at(16, 30))           # the bars feed stopped at 12:50
    r = save_day.save_day(tmp_path, date.fromisoformat(DAY), at(16, 30))
    assert r["context"] and r["spx_bars_added"] == 190
    assert sorted(s for s, _ in calls) == sorted([s for s in ALL_SYMBOLS if s not in NO_HISTORY] + ["SPX"])
    assert len(load_bars(tmp_path, DAY)) == 390
    minutes = (tmp_path / "spx_jev" / "context" / "bars" / f"{DAY}.jsonl").read_text().splitlines()
    assert len(minutes) == 390 and len(json.loads(minutes[0])["bars"]) == len(ALL_SYMBOLS) - len(NO_HISTORY)
    assert load_market_context(tmp_path, DAY).last("XLK", at(10, 0)) == 7700.0

    calls.clear()
    again = save_day.save_day(tmp_path, date.fromisoformat(DAY), at(16, 30))
    assert again == {"day": DAY, "context": None, "breadth": None, "spx_bars_added": 0} and calls == []   # everything on disk: no call at all


def test_a_failed_day_is_reported_and_costs_only_itself(tmp_path, monkeypatch, capsys):
    calls = []
    _schwab(monkeypatch, calls)
    served = schwab.minute_bars

    def flaky(symbol, start, end):
        if start.date() == date(2026, 9, 17):
            raise ConnectionError("no route")
        return served(symbol, start, end)
    monkeypatch.setattr(schwab, "minute_bars", flaky)
    monkeypatch.setattr(save_day, "days_to_save", lambda now: [date(2026, 9, 17), date(2026, 9, 18)])
    assert save_day.main(["--state-dir", str(tmp_path)]) == 1
    err = capsys.readouterr()
    assert "2026-09-17 failed: SaveFailed: SPX bars: ConnectionError" in err.err and "1 of 2 days saved" in err.out
    assert (tmp_path / "spx_jev" / "context" / "bars" / f"{DAY}.jsonl").exists()
    assert not (tmp_path / "spx_jev" / "context" / "bars" / "2026-09-17.jsonl").exists()


def test_a_run_with_nothing_finished_to_save_is_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(save_day, "days_to_save", lambda now: [])
    assert save_day.main(["--state-dir", str(tmp_path)]) == 0
    assert not (tmp_path / "spx_jev").exists()


def test_a_session_short_of_its_minutes_is_short_and_a_full_one_is_not(tmp_path):
    d = date.fromisoformat(DAY)
    assert save_day.session_bars_short(tmp_path, d)
    bars.append_day(tmp_path, DAY, flat_bars(390), at(16, 30) + timedelta(hours=1))
    assert not save_day.session_bars_short(tmp_path, d)


def test_a_market_symbol_schwab_refuses_still_leaves_spx_its_day(tmp_path, monkeypatch, capsys):
    calls = []
    _schwab(monkeypatch, calls)
    served = schwab.minute_bars

    def refuses_one(symbol, start, end):
        if symbol == "$VOLSPD":
            raise ConnectionError("400 refused")
        return served(symbol, start, end)
    monkeypatch.setattr(schwab, "minute_bars", refuses_one)
    monkeypatch.setattr(save_day, "days_to_save", lambda now: [date.fromisoformat(DAY)])
    bars.append_day(tmp_path, DAY, flat_bars(200), at(16, 30))
    assert save_day.main(["--state-dir", str(tmp_path)]) == 1
    assert "market bars: ConnectionError: 400 refused" in capsys.readouterr().err
    assert len(load_bars(tmp_path, DAY)) == 390


def test_a_later_run_asks_again_for_the_breadth_of_a_day_saved_on_its_own_evening(tmp_path, monkeypatch):
    """The day is saved at 16:30 with its breadth as Schwab serves it during the session; the next day's run asks for
    the breadth again (only the breadth), and flat bars that never go below zero are not put right, so the file stays."""
    calls = []
    _schwab(monkeypatch, calls)
    day = date.fromisoformat(DAY)
    save_day.save_day(tmp_path, day, at(16, 30))
    path = tmp_path / "spx_jev" / "context" / "bars" / f"{DAY}.jsonl"
    os.utime(path, (at(16, 30).timestamp(), at(16, 30).timestamp()))
    kept = path.read_text()
    calls.clear()
    r = save_day.save_day(tmp_path, day, at(16, 30) + timedelta(days=3))
    from spx_jev.market_context import SYMBOLS
    assert r["breadth"] is None and sorted(s for s, _ in calls) == sorted(SYMBOLS["breadth"]) and path.read_text() == kept
