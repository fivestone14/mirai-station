"""The overnight futures store, offline: the night's window, trimming to the read, session tags, the
merge (idempotent, first save kept, bad bars flagged), the manifest's checks and the daily run's days."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from spx_jev import overnight, rolls, schwab
from spx_jev.overnight import SYMBOLS, Series, expected_open, holiday_night, night_window, session_of

ET = ZoneInfo("America/New_York")
TUESDAY = date(2026, 9, 22)


def t(day: str, hh: int, mm: int) -> datetime:
    return datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00").replace(tzinfo=ET)


def _bars(start: datetime, n: int, minutes: int = 1, price: float = 6700.0, skip=()) -> list[dict]:
    out = []
    for k in range(n):
        ts = start + timedelta(minutes=minutes * k)
        if ts not in skip:
            out.append({"ts": ts.isoformat(), "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 10.0})
    return out


@pytest.fixture
def served(monkeypatch):
    """Schwab stand-ins: ``served[(symbol, minutes)]`` is what each history call answers, whatever the end time."""
    answers: dict = {}
    calls: list = []
    monkeypatch.setattr(schwab, "CALL_SPACING_S", 0.0)

    def history(minutes):
        def call(symbol, start, end, extended_hours=False):
            calls.append((symbol, minutes, extended_hours))
            return [b for b in answers.get((symbol, minutes), []) if datetime.fromisoformat(b["ts"]) >= start]
        return call
    monkeypatch.setattr(schwab, "minute_bars", history(1))
    monkeypatch.setattr(schwab, "five_minute_bars", history(5))
    monkeypatch.setattr(schwab, "front_contracts", lambda roots: {"/ES": "/ESZ26", "/ZN": "/ZNZ26", "/BTC": "/BTCV26", "/MBT": "/MBTV26"})
    answers["calls"] = calls
    return answers


def _rows(state_dir, day):
    return [json.loads(line) for line in overnight.night_path(state_dir, day).read_text().splitlines()]


def _manifest(state_dir):
    return [json.loads(line) for line in overnight.manifest_path(state_dir).read_text().splitlines()]


def test_a_night_runs_from_the_prior_close_less_five_minutes_to_the_days_close():
    assert night_window(TUESDAY) == (t("2026-09-21", 15, 55), t("2026-09-22", 16, 0))
    assert night_window(date(2026, 9, 21))[0] == t("2026-09-18", 15, 55)                 # Monday's night holds the weekend
    assert night_window(date(2026, 11, 30))[0] == t("2026-11-27", 12, 55)                # after the Thanksgiving half day


def test_each_bar_is_tagged_with_its_session():
    tags = [session_of(t(*x), TUESDAY) for x in [("2026-09-21", 15, 57), ("2026-09-21", 16, 0), ("2026-09-22", 3, 59),
                                                ("2026-09-22", 4, 0), ("2026-09-22", 9, 29), ("2026-09-22", 9, 30)]]
    assert tags == ["regular", "overnight", "overnight", "premarket", "premarket", "regular"]


def test_holiday_nights_are_marked():
    assert holiday_night(date(2026, 9, 8)) and holiday_night(date(2026, 11, 30))       # Labor Day; after a half day
    assert not holiday_night(TUESDAY) and not holiday_night(date(2026, 9, 21))         # an ordinary weekend is no holiday


def test_trading_hours_follow_globex_and_bitcoin_round_the_clock():
    assert not expected_open("/ES", t("2026-09-21", 17, 30)) and expected_open("/ES", t("2026-09-21", 18, 0))
    assert not expected_open("/ES", t("2026-09-19", 12, 0)) and not expected_open("/ES", t("2026-09-20", 17, 59))
    assert expected_open("/MBT", t("2026-09-19", 12, 0)) and expected_open("/BTC", t("2026-09-21", 17, 30))
    assert not expected_open("/MBT", t("2026-09-19", 4, 0))                             # Saturday maintenance
    assert not expected_open("/MBT", t("2026-05-23", 12, 0))                            # before CME went round the clock


def test_a_save_keeps_only_the_nights_finished_bars_and_asks_for_extended_hours(tmp_path, served):
    served[("/ES", 1)] = _bars(t("2026-09-21", 15, 50), 1100)                           # 15:50 Monday to 10:09 Tuesday
    now = t("2026-09-22", 9, 26) + timedelta(seconds=30)
    lines = overnight.save_nights(tmp_path, [TUESDAY], now)
    es = [r for r in _rows(tmp_path, "2026-09-22") if r["symbol"] == "/ES"]
    assert es[0]["ts"] == t("2026-09-21", 15, 55).isoformat() and es[-1]["ts"] == t("2026-09-22", 9, 25).isoformat()
    assert all(extended for _, _, extended in served["calls"])
    assert {(s, m) for s, m, _ in served["calls"]} == {(s, m) for s in SYMBOLS for m in (1, 5)}
    row = es[0]
    assert (row["schema_version"], row["day"], row["symbol"], row["contract"], row["contract_from"], row["bar_minutes"],
            row["session"], row["source"], row["flags"]) == (1, "2026-09-22", "/ES", "/ESZ26", "quote", 1, "regular",
                                                             "schwab_price_history", [])
    assert row["saved_at"] == now.isoformat(timespec="seconds") and len(lines) == 1


def test_a_rerun_adds_nothing_and_a_later_read_adds_only_the_new_bars(tmp_path, served):
    served[("/ES", 1)] = _bars(t("2026-09-21", 15, 55), 1500)
    served[("/ES", 5)] = _bars(t("2026-09-21", 15, 55), 300, minutes=5)
    early = t("2026-09-22", 9, 26)
    overnight.save_nights(tmp_path, [TUESDAY], early)
    before = overnight.night_path(tmp_path, "2026-09-22").read_text()
    assert overnight.save_nights(tmp_path, [TUESDAY], early) == []                       # nothing new: file and manifest untouched
    assert overnight.night_path(tmp_path, "2026-09-22").read_text() == before and len(_manifest(tmp_path)) == 1
    late = overnight.save_nights(tmp_path, [TUESDAY], t("2026-09-22", 16, 20))
    rows = _rows(tmp_path, "2026-09-22")
    keys = [(r["symbol"], r["bar_minutes"], r["ts"]) for r in rows]
    assert len(keys) == len(set(keys)) and keys == sorted(keys, key=lambda k: (SYMBOLS.index(k[0]), k[1], datetime.fromisoformat(k[2])))
    es1 = late[0]["symbols"]["/ES"]["bars"]["1"]
    assert es1["already_saved"] == 1051 and es1["added"] == len([r for r in rows if r["bar_minutes"] == 1]) - 1051
    assert rows[-1]["ts"] == t("2026-09-22", 15, 55).isoformat() and rows[-1]["session"] == "regular"


def test_duplicates_and_disagreeing_refetches_are_counted_and_the_first_save_kept(tmp_path, served):
    bars = _bars(t("2026-09-21", 15, 55), 10)
    served[("/ES", 1)] = bars + [dict(bars[3])]                                         # Schwab answers one minute twice
    overnight.save_nights(tmp_path, [TUESDAY], t("2026-09-21", 16, 10))
    served[("/ES", 1)] = [dict(bars[0], close=1.0, low=0.5)] + bars[1:] + _bars(t("2026-09-21", 16, 5), 1)
    line = overnight.save_nights(tmp_path, [TUESDAY], t("2026-09-21", 16, 10))[0]
    es1 = line["symbols"]["/ES"]["bars"]["1"]
    assert (es1["disagreed"], es1["already_saved"], es1["added"]) == (1, 10, 1)
    assert _manifest(tmp_path)[0]["symbols"]["/ES"]["bars"]["1"]["duplicates"] == 1
    assert _rows(tmp_path, "2026-09-22")[0]["close"] == 6700.0                        # the first save stands


def test_a_bad_bar_is_kept_with_its_flags(tmp_path, served):
    bad = {"ts": t("2026-09-21", 16, 0).isoformat(), "open": 6700.0, "high": 6699.0, "low": 6698.0, "close": 0.0, "volume": -1.0}
    served[("/ES", 1)] = [bad]
    line = overnight.save_nights(tmp_path, [TUESDAY], t("2026-09-21", 16, 5))[0]
    (row,) = _rows(tmp_path, "2026-09-22")
    assert row["flags"] == ["ohlc_inconsistent", "nonpositive_price", "negative_volume"] and row["close"] == 0.0
    assert line["symbols"]["/ES"]["bars"]["1"]["flagged"] == {"ohlc_inconsistent": 1, "nonpositive_price": 1, "negative_volume": 1}


def test_the_manifest_counts_expected_bars_and_lists_the_gaps(tmp_path, served):
    start = t("2026-09-21", 15, 55)
    missing = {t("2026-09-21", 20, 0), t("2026-09-21", 20, 1), t("2026-09-21", 20, 2), t("2026-09-22", 1, 0)}
    served[("/ES", 1)] = _bars(start, 1500, skip=missing)
    served[("/MBT", 1)] = _bars(start, 60, price=84000.0)
    line = overnight.save_nights(tmp_path, [TUESDAY], t("2026-09-22", 9, 26))[0]
    es1 = line["symbols"]["/ES"]["bars"]["1"]
    # 15:55 to 09:26 is 1051 minutes, less the 60-minute halt at 17:00
    assert es1["expected"] == 991 and es1["bars"] == 1047 and es1["missing_minutes"] == 4
    assert es1["gaps"] == [[t("2026-09-21", 20, 0).isoformat(), 3], [t("2026-09-22", 1, 0).isoformat(), 1]]
    assert es1["outside_hours"] == 60 and es1["coverage"] == round(987 / 991, 4)       # Schwab's halt-hour bars are counted, not dropped
    assert es1["sessions"] == {"regular": 5, "overnight": 716, "premarket": 326} and es1["contracts"] == ["/ESZ26"]
    mbt = line["symbols"]["/MBT"]["bars"]["1"]
    assert mbt["expected"] == 1051 and mbt["missing_minutes"] == 991                     # bitcoin trades through the halt
    assert line["symbols"]["/ES"]["contract_quoted"] == "/ESZ26" and line["symbols"]["/ES"]["roll_pending"] is False
    assert line["window"] == [start.isoformat(), t("2026-09-22", 9, 26).isoformat()] and line["holiday_night"] is False


def test_contracts_come_from_the_roll_table_and_a_new_quote_is_a_pending_roll(tmp_path, served):
    table = rolls.merge(rolls.load(tmp_path), "/ES", {"rolls": [{"symbol": "/ES", "day": "2026-09-14", "at": "2026-09-13T18:00:00-04:00"}],
                                                      "rejected": [], "windows_without_roll": [], "typical_step": 0.01,
                                                      "unit": "percent"}, "2026-08-01", "/ESZ26")
    rolls.save(tmp_path / "spx_jev" / "overnight", table)
    served[("/ES", 1)] = _bars(t("2026-09-11", 15, 55), 4000)
    monday = date(2026, 9, 14)
    line = overnight.save_nights(tmp_path, [monday], t("2026-09-14", 9, 26))[0]
    rows = _rows(tmp_path, "2026-09-14")
    assert {r["contract"] for r in rows if r["ts"] < "2026-09-13T18"} == {"/ESU26"}
    assert {r["contract"] for r in rows if r["ts"] >= "2026-09-13T18"} == {"/ESZ26"}
    assert line["symbols"]["/ES"]["same_contract_through_night"] is False
    table["current"]["/ES"] = "/ESU26"                                                  # Schwab now quotes a contract the table has not reached
    assert rolls.pending(table, "/ES", "/ESZ26")


def test_the_daily_run_saves_the_night_in_progress_and_the_last_week():
    morning = overnight.days_to_save(t("2026-09-22", 9, 26))
    evening = overnight.days_to_save(t("2026-09-22", 16, 20))
    assert morning[-1] == TUESDAY and morning[0] == date(2026, 9, 15) and all(d.weekday() < 5 for d in morning)
    assert evening[-2:] == [TUESDAY, date(2026, 9, 23)]
    assert overnight.night_for(t("2026-09-19", 12, 0)) == date(2026, 9, 21)


def test_the_command_does_nothing_on_a_day_the_market_is_shut(tmp_path, served, monkeypatch, capsys):
    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return t("2026-09-20", 9, 26)
    monkeypatch.setattr(overnight, "datetime", Sunday)
    assert overnight.main(["--state-dir", str(tmp_path)]) == 0
    assert served["calls"] == [] and "not a market day" in capsys.readouterr().out


def test_the_backfill_saves_every_night_served_then_the_roll_table(tmp_path, served, monkeypatch):
    served[("/ES", 5)] = _bars(t("2026-09-16", 9, 30), 12 * 24 * 5, minutes=5)       # Wednesday to the next Monday
    served[("$SPX", 5)] = []
    monkeypatch.setattr(schwab, "five_minute_bars", lambda symbol, start, end, extended_hours=False:
                        [b for b in served.get((symbol, 5), []) if datetime.fromisoformat(b["ts"]) >= start])
    lines, table = overnight.backfill(tmp_path, t("2026-09-21", 9, 0))
    assert [l["day"] for l in lines] == ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]
    assert lines[0]["symbols"]["/ES"]["bars"]["5"]["history_from"] == t("2026-09-16", 9, 30).isoformat()
    assert overnight.rolls.table_path(tmp_path / "spx_jev" / "overnight").exists() and table["rolls"] == []
    assert overnight.backfill(tmp_path, t("2026-09-21", 9, 0))[0] == []              # resumable: a rerun adds nothing
    s = overnight.summary(lines)["/ES 5-min"]
    assert s["nights"] == 4 and s["earliest"] == t("2026-09-16", 9, 30).isoformat()
