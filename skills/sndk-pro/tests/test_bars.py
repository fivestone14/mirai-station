"""obs-5 (2026-09-02) — the minute-bar SIDECAR and the reader that reads it.

Measured before it was built (23 sessions against Schwab 1-min bars): the
2-minute scans put the session extremes a median 0.10σ (~$7) short, the
opening box ~$8 too narrow, and missed or invented one box break in three.
These tests pin the writer's rules (completed minutes only, idempotent,
self-healing) and the reader's (wicks win when present, the label says which
witness was used, absence falls back and never fabricates)."""
import json
from datetime import datetime, timedelta

import pytest

import sndk_bars as SB
import sndk_read as SR
from test_read import mkrow, T0
from test_scene_v2 import rich_row

DAY = T0.date().isoformat()


def _bar(ts, lo, hi, o=None, c=None, v=100.0):
    return {"ts": ts.isoformat(), "open": o if o is not None else lo, "high": hi,
            "low": lo, "close": c if c is not None else hi, "volume": v}


def _session(start, minutes, lo=1500.0, hi=1500.0):
    return [_bar(start + timedelta(minutes=i), lo, hi) for i in range(minutes)]


# --- the writer ---------------------------------------------------------------
def test_only_completed_minutes_land():
    """The running minute's bar is a partial — its high and low are still being
    written — so it must never land."""
    start = T0 - timedelta(minutes=3)
    bars = _session(start, 4)                      # minutes T0-3 .. T0
    s = SB.write_day(DAY, bars, T0)
    assert s["appended"] == 3 and s["bars_on_disk"] == 3
    on_disk = SB.read_bars(DAY)
    assert on_disk[-1]["ts"] == (T0 - timedelta(minutes=1)).isoformat()


def test_a_rerun_appends_nothing_and_a_gap_is_healed():
    """Schwab returns the whole session, so a run after any gap writes the gap
    and a run with nothing new writes nothing — the file is never duplicated."""
    start = T0 - timedelta(minutes=10)
    first = _session(start, 4)                     # T0-10 .. T0-7
    SB.write_day(DAY, first, T0)
    again = SB.write_day(DAY, first, T0)
    assert again["appended"] == 0 and again["bars_on_disk"] == 4
    whole = _session(start, 11)                    # the full stretch, T0-10 .. T0
    healed = SB.write_day(DAY, whole, T0)
    assert healed["appended"] == 6                 # T0-6 .. T0-1; T0 is running
    assert healed["bars_on_disk"] == 10
    assert [b["ts"] for b in SB.read_bars(DAY)] == [
        (start + timedelta(minutes=i)).isoformat() for i in range(10)]
    assert json.loads(SB.health_path().read_text())["bars_on_disk"] == 10


def test_a_torn_line_is_skipped_and_a_later_duplicate_wins():
    p = SB.bars_path(DAY)
    p.parent.mkdir(parents=True, exist_ok=True)
    t = (T0 - timedelta(minutes=5)).isoformat()
    p.write_text(json.dumps(_bar(T0 - timedelta(minutes=5), 1500, 1510)) + "\n"
                 + "{torn\n"
                 + json.dumps(_bar(T0 - timedelta(minutes=5), 1500, 1512)) + "\n")
    bars = SB.read_bars(DAY)
    assert len(bars) == 1 and bars[0]["high"] == 1512.0 and bars[0]["ts"] == t
    assert SB.read_bars("1999-01-01") == []


def test_the_run_survives_a_failed_fetch(monkeypatch):
    def boom(day, ticker=SB.TICKER):
        raise RuntimeError("schwab down")
    monkeypatch.setattr(SB, "fetch_session", boom)
    assert SB.run(DAY, now=T0) == 1
    h = json.loads(SB.health_path().read_text())
    assert h["appended"] == 0 and "schwab down" in h["error"]
    assert not SB.bars_path(DAY).exists()


# --- the reader ----------------------------------------------------------------
def _tape(spots, step_min=2, sigma=100.0):
    start = T0 - timedelta(minutes=step_min * (len(spots) - 1))
    return [mkrow([[1300, 60]], spot=v, sigma=sigma,
                  ts=start + timedelta(minutes=i * step_min))
            for i, v in enumerate(spots)]


def test_without_a_bars_file_the_reader_falls_back_and_says_so():
    rows = _tape([1500] * 16 + [1520, 1522])
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    assert sc["price"]["extremes_from"] == "scans_every_2_min"
    assert sc["context"]["ranges"]["measured_from"] == "scans_every_2_min"
    assert "minute_bars" not in sc["data_sources"]
    assert (sc["price"]["session_low"], sc["price"]["session_high"]) == (1500.0, 1522.0)


def test_the_wicks_win_for_the_extremes_and_the_opening_box():
    """The scans saw 1500-1504; the minutes between them wicked to 1490 and
    1515. The extremes and the opening box come from the wicks, labelled."""
    rows = _tape([1500 + (i % 3) * 2 for i in range(16)])          # 30 min of scans
    start = rows[0]["ts"]
    t0 = datetime.fromisoformat(start)
    bars = [_bar(t0 + timedelta(minutes=i), 1500.0, 1504.0) for i in range(31)]
    bars[7] = _bar(t0 + timedelta(minutes=7), 1490.0, 1504.0)       # a wick down
    bars[20] = _bar(t0 + timedelta(minutes=20), 1500.0, 1515.0)     # a wick up
    for b in bars:
        SB.write_day(DAY, [b], T0 + timedelta(minutes=5))
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    assert sc["price"]["extremes_from"] == "1_minute_bars"
    assert (sc["price"]["session_low"], sc["price"]["session_high"]) == (1490.0, 1515.0)
    rg = sc["context"]["ranges"]
    assert rg["measured_from"] == "1_minute_bars"
    assert (rg["opening"]["low"], rg["opening"]["high"]) == (1490.0, 1515.0)
    mb = sc["data_sources"]["minute_bars"]
    assert mb["bars_so_far_today"] == 31 and mb["last_bar_at"] == bars[30]["ts"]


def test_a_break_the_scans_stepped_over_is_seen_by_the_wick():
    """One box break in three was missed or invented on scans alone. A wick
    beyond the frozen box by more than 0.05 sigma breaks it even when every
    2-minute spot stayed inside."""
    rows = _tape([1500] * 20)                                         # scans never leave 1500
    t0 = datetime.fromisoformat(rows[0]["ts"])
    bars = [_bar(t0 + timedelta(minutes=i), 1500.0, 1500.0) for i in range(39)]
    bars[35] = _bar(t0 + timedelta(minutes=35), 1500.0, 1509.0)      # +$9 wick, box is $0 wide
    SB.write_day(DAY, bars, T0 + timedelta(minutes=5))
    rb = SR.ranges_block(rows, T0, 100.0, DAY, bars=SB.read_bars(DAY))
    assert rb["breaks_today"]["count"] == 1
    assert rb["breaks_today"]["breaks"][0]["went"] == "up"
    assert rb["opening"]["status"].startswith("broke up at ")
    # and the same tape on scans alone sees nothing — the difference the sidecar exists for
    assert "breaks_today" not in SR.ranges_block(rows, T0, 100.0, DAY)


def test_the_live_spot_still_decides_where_price_sits():
    """The newest diary spot is folded in after the last completed bar, so the
    live position is never older than the bars and never a wick."""
    rows = _tape([1500] * 16 + [1503])
    t0 = datetime.fromisoformat(rows[0]["ts"])
    bars = [_bar(t0 + timedelta(minutes=i), 1499.0, 1501.0) for i in range(31)]
    SB.write_day(DAY, bars, T0)
    rb = SR.ranges_block(rows, T0, 100.0, DAY, bars=SB.read_bars(DAY))
    assert rb["in_force"]["live_spot_is"] == "just above"           # 1503 vs box high 1501
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    assert sc["price"]["session_high"] == 1503.0                     # the live print counts


def test_prior_sessions_prefer_a_full_bars_file_and_say_when_they_mixed():
    diary = SR._diary_dir()
    diary.mkdir(parents=True, exist_ok=True)
    for d, lo, hi in (("2026-07-29", 1400.0, 1480.0), ("2026-07-30", 1450.0, 1530.0)):
        (diary / f"{d}.jsonl").write_text("\n".join(
            json.dumps({"ts": f"{d}T10:00:00-04:00", "ticker": "SNDK", "spot": v})
            for v in (lo, hi)) + "\n")
    # 07-30 has a FULL bars file whose wicks reach past the scans; 07-29 has a thin one
    full_start = datetime.fromisoformat("2026-07-30T09:30:00-04:00")
    full = [_bar(full_start + timedelta(minutes=i), 1445.0, 1540.0) for i in range(390)]
    SB.write_day("2026-07-30", full, T0)
    thin = [_bar(datetime.fromisoformat("2026-07-29T09:30:00-04:00") + timedelta(minutes=i),
                 1300.0, 1600.0) for i in range(20)]                    # thin: not trusted
    SB.write_day("2026-07-29", thin, T0)
    ps = SR._prior_sessions_range("2026-07-31")
    assert (ps["low"], ps["high"]) == (1400.0, 1540.0)              # scans for 29, wicks for 30
    assert ps["measured_from"] == "mixed" and ps["sessions"] == 2


def test_the_doctrine_names_the_witness_and_the_era_moved():
    assert "`price.extremes_from`" in SR._DOCTRINE and "`measured_from`" in SR._DOCTRINE
    assert "`minute_bars`" in SR._DOCTRINE
    assert SR.ERA == "obs-5"
