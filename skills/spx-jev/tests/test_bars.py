"""The live-bars sidecar: finished minutes only, appended once, the session by the clock. No network."""
from __future__ import annotations

import json
from datetime import date

from conftest import DAY, at, flat_bars
from spx_jev import bars, schwab
from spx_jev.state_builder import load_bars


def test_only_finished_minutes_land_and_a_rerun_appends_nothing(tmp_path):
    session = flat_bars(40)                                              # 09:30 .. 10:09
    assert bars.append_day(tmp_path, DAY, session, at(10, 0, ss=30)) == 30   # 09:30 .. 09:59 have finished
    assert bars.append_day(tmp_path, DAY, session, at(10, 0, ss=30)) == 0
    assert bars.append_day(tmp_path, DAY, session, at(10, 10)) == 10
    on_disk = [json.loads(l) for l in bars.bars_path(tmp_path, DAY).read_text().splitlines()]
    assert len(on_disk) == 40 and on_disk[0]["ts"] == at(9, 30).isoformat().replace("-04:00", "-04:00")
    assert len(load_bars(tmp_path, DAY)) == 40                           # the labeller reads what the sidecar wrote


def test_the_session_is_cut_by_the_clock(monkeypatch):
    asked = {}

    def minute_bars(symbol, start, end):
        asked.update(symbol=symbol, start=start, end=end)
        return flat_bars(391) + [{**flat_bars(1)[0], "ts": at(9, 29).isoformat()}]
    monkeypatch.setattr(schwab, "minute_bars", minute_bars)
    got = bars.fetch_session(date.fromisoformat(DAY))
    assert asked["symbol"] == "SPX" and asked["start"].hour == 9 and asked["start"].minute == 30
    assert len(got) == 390 and got[-1]["ts"] == at(15, 59).isoformat()     # 16:00 and 09:29 are outside the session


def test_a_failed_fetch_is_a_skipped_run(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise ConnectionError("no route")
    monkeypatch.setattr(schwab, "minute_bars", broken)
    assert bars.run(tmp_path, DAY, now=at(10, 0)) == 1
    assert not bars.bars_path(tmp_path, DAY).exists()
