"""Tests for lefteye_event_lens — THE EVENT LENS (record-only macro-print telemetry).

Pure logic + ONE read-only calendar read (tmp/injected here, never written).
Covers the KINDS allowlist + NFP-by-rule injection, both print shapes (intraday
FOMC statement, pre-market CPI/NFP), the raw surprise ratio, the same-source
crush flag (true / false / mixed), and the sigma fallbacks."""
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lefteye_event_lens as ev  # noqa: E402

ET = ZoneInfo("America/New_York")
FOMC_DAY = date(2026, 7, 29)     # seeded FOMC Wednesday, 14:00 ET
CPI_DAY = date(2026, 7, 14)      # seeded CPI Tuesday, 08:30 ET
NFP_DAY = date(2026, 8, 7)       # first Friday of Aug 2026 — NOT in the JSON
QUIET_DAY = date(2026, 7, 8)     # plain Wednesday, no prints

NOW_QUIET = datetime(2026, 7, 8, 11, 0, tzinfo=ET)
NOW_FOMC_PRE = datetime(2026, 7, 29, 13, 0, tzinfo=ET)
NOW_FOMC_POST = datetime(2026, 7, 29, 14, 20, tzinfo=ET)
NOW_CPI_MID = datetime(2026, 7, 14, 10, 30, tzinfo=ET)
NOW_CPI_EARLY = datetime(2026, 7, 14, 9, 45, tzinfo=ET)

FOMC_EV = {"date": "2026-07-29", "kind": "FOMC", "time_et": "14:00", "verified": True}
CPI_EV = {"date": "2026-07-14", "kind": "CPI", "time_et": "08:30", "verified": True}


def _dt(day, hm):
    return datetime(day.year, day.month, day.day, int(hm[:2]), int(hm[3:5]), tzinfo=ET)


def _row(hm, em_points=None, em_open=None, sigma=None, day=FOMC_DAY):
    """A diary row at HH:MM ET carrying a range_ruler and/or a flat sigma."""
    r = {"ticker": "SPX", "ts": _dt(day, hm).isoformat()}
    if em_points is not None or em_open is not None:
        rr = {}
        if em_points is not None:
            rr["em_points"] = em_points
        if em_open is not None:
            rr["em_open"] = em_open
        r["range_ruler"] = rr
    if sigma is not None:
        r["sigma"] = sigma
    return r


def _bars(seq, day=FOMC_DAY):
    """1-min OHLC bars from (HH:MM, open, high, low, close) or flat (HH:MM, px)."""
    out = []
    for tup in seq:
        if len(tup) == 2:
            hm, px = tup
            o = h = lo = c = px
        else:
            hm, o, h, lo, c = tup
        out.append({"ts": _dt(day, hm).isoformat(),
                    "open": o, "high": h, "low": lo, "close": c, "volume": 100})
    return out


@pytest.fixture(autouse=True)
def _fresh_calendar_cache():
    ev.reset_calendar_cache()
    yield
    ev.reset_calendar_cache()


# ------------------------------------------------------------ calendar + selection
class TestCalendarAndSelection:
    def test_non_event_day_returns_none(self):
        # events exist in the calendar, just not TODAY -> the key stays absent
        out = ev.read(NOW_QUIET, 6300.0, 6290.0,
                      _bars([("10:00", 6300.0)], day=QUIET_DAY),
                      todays_rows=[_row("10:00", em_points=9.0, day=QUIET_DAY)],
                      events=[FOMC_EV, CPI_EV])
        assert out is None

    def test_missing_or_corrupt_calendar_fails_open_readonly(self, tmp_path, monkeypatch):
        missing = tmp_path / "calendar.json"
        assert ev.load_calendar(missing) == []
        assert not missing.exists()                     # loader NEVER seeds/writes
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not json!!")
        assert ev.load_calendar(corrupt) == []
        assert corrupt.read_text() == "{not json!!"     # file untouched
        # read() through the default path, missing file, on a REAL event date -> None
        monkeypatch.setattr(ev, "CALENDAR_PATH", tmp_path / "nope.json")
        ev.reset_calendar_cache()
        assert ev.read(NOW_FOMC_POST, 6300.0, 6290.0, []) is None
        assert not (tmp_path / "nope.json").exists()

    def test_multi_event_day_fomc_wins_and_flagged(self):
        evs = [{"date": "2026-07-29", "kind": "CPI", "time_et": "08:30"}, FOMC_EV]
        out = ev.read(NOW_FOMC_PRE, 6300.0, 6290.0, [],
                      todays_rows=[_row("12:45", em_points=8.0)], events=evs)
        assert out["event_kind"] == "FOMC"              # rank 3 beats rank 2
        assert out["event_time_et"] == "14:00"
        assert out["phase"] == "pre_event"
        assert "multi_event" in out["quality"]

    def test_nfp_injected_by_rule_and_opex_filtered(self):
        seeded = [{"date": "2026-08-07", "kind": "OPEX", "time_et": None}]
        evs = ev.events_for(NFP_DAY, seeded)
        assert [e["kind"] for e in evs] == ["NFP"]      # injected; OPEX dropped
        assert evs[0]["time_et"] == "08:30"
        # dedupe on kind: a seeded (holiday-shift) NFP row is never doubled
        evs2 = ev.events_for(NFP_DAY, seeded + [
            {"date": "2026-08-07", "kind": "NFP", "time_et": "08:30"}])
        assert [e["kind"] for e in evs2] == ["NFP"]
        # read() sees the injected print; nulls elsewhere still emit the phase
        out = ev.read(datetime(2026, 8, 7, 9, 0, tzinfo=ET), None, None, [],
                      events=seeded)
        assert out is not None
        assert out["event_kind"] == "NFP" and out["phase"] == "post_event"


# ----------------------------------------------------------------- FOMC intraday
class TestFomcIntraday:
    ROWS = [_row("10:30", em_points=12.0, sigma=11.0),
            _row("12:45", em_points=8.0, sigma=7.5)]

    def test_pre_event_scan_freezes_budget_only(self):
        out = ev.read(NOW_FOMC_PRE, 6300.0, 6280.0, _bars([("12:59", 6300.0)]),
                      todays_rows=self.ROWS, events=[FOMC_EV])
        assert out["phase"] == "pre_event"
        assert out["em_pre_pts"] == 8.0                 # LAST pre-14:00 row, em_points
        assert out["realized_post_pts"] is None
        assert out["surprise_ratio"] is None
        assert out["crush_flag"] is None
        assert out["quality"] == "ok"

    def test_post_event_surprise_ratio_from_bars(self):
        bars = _bars([("13:50", 6299.0), ("13:59", 6300.0), ("14:05", 6309.0),
                      ("14:10", 6311.0), ("14:15", 6312.0), ("14:19", 6320.0)])
        out = ev.read(NOW_FOMC_POST, 6320.0, 6280.0, bars,
                      todays_rows=self.ROWS, events=[FOMC_EV])
        assert out["phase"] == "post_event"
        # |close(14:15) - close(13:59)| — the 14:19 bar must NOT leak in
        assert out["realized_post_pts"] == 12.0
        assert out["surprise_ratio"] == round(abs(6312.0 - 6300.0) / 8.0, 3)
        assert out["surprise_ratio"] == 1.5
        assert out["surprise_ratio"] > 1.0              # budget-eating print

    def test_crush_true_false_and_mixed_none(self):
        bars = _bars([("13:59", 6300.0), ("14:15", 6312.0)])
        pre = [_row("13:50", em_points=10.0)]
        crushed = pre + [_row("14:16", em_points=8.0)]       # 0.80x < 0.85
        out = ev.read(NOW_FOMC_POST, 6312.0, 6280.0, bars,
                      todays_rows=crushed, events=[FOMC_EV])
        assert out["crush_flag"] is True
        soft = pre + [_row("14:16", em_points=9.5)]          # 0.95x >= 0.85
        out = ev.read(NOW_FOMC_POST, 6312.0, 6280.0, bars,
                      todays_rows=soft, events=[FOMC_EV])
        assert out["crush_flag"] is False
        mixed = pre + [_row("14:16", sigma=8.0)]             # em vs sigma: no verdict
        out = ev.read(NOW_FOMC_POST, 6312.0, 6280.0, bars,
                      todays_rows=mixed, events=[FOMC_EV])
        assert out["crush_flag"] is None


# ------------------------------------------------------------- CPI/NFP pre-market
class TestCpiPremarket:
    # 12 one-min bars 09:30-09:41; first open 6280, max high 6295, min low 6272
    BARS = _bars([("09:30", 6280.0, 6285.0, 6278.0, 6282.0),
                  ("09:31", 6282.0, 6288.0, 6279.0, 6284.0),
                  ("09:32", 6284.0, 6290.0, 6280.0, 6286.0),
                  ("09:33", 6286.0, 6291.0, 6281.0, 6287.0),
                  ("09:34", 6287.0, 6295.0, 6283.0, 6290.0),
                  ("09:35", 6290.0, 6293.0, 6284.0, 6288.0),
                  ("09:36", 6288.0, 6290.0, 6280.0, 6283.0),
                  ("09:37", 6283.0, 6286.0, 6272.0, 6275.0),
                  ("09:38", 6275.0, 6282.0, 6274.0, 6280.0),
                  ("09:39", 6280.0, 6284.0, 6276.0, 6282.0),
                  ("09:40", 6282.0, 6287.0, 6278.0, 6285.0),
                  ("09:41", 6285.0, 6289.0, 6280.0, 6286.0)], day=CPI_DAY)

    def test_cpi_gap_plus_discovery_vs_em_open(self):
        rows = [_row("09:35", em_open=30.0, sigma=25.0, day=CPI_DAY),
                _row("10:05", em_open=28.0, day=CPI_DAY)]
        out = ev.read(NOW_CPI_MID, 6285.0, 6270.0, self.BARS,
                      todays_rows=rows, events=[CPI_EV])
        assert out["phase"] == "post_event"             # whole session is aftermath
        assert out["em_pre_pts"] == 30.0                # EARLIEST range_ruler carrier
        # |open 6280 - prior_close 6270| + (6295 - 6272) = 10 + 23
        assert out["realized_post_pts"] == 33.0
        assert out["surprise_ratio"] == 1.1
        assert out["crush_flag"] is None                # v1: no pre-print budget exists
        assert "em_post_print" in out["quality"]        # documented denominator bias

    def test_cpi_realized_waits_for_ten_oclock(self):
        rows = [_row("09:35", em_open=30.0, day=CPI_DAY)]
        out = ev.read(NOW_CPI_EARLY, 6285.0, 6270.0, self.BARS,
                      todays_rows=rows, events=[CPI_EV])
        assert out["phase"] == "post_event"
        assert out["realized_post_pts"] is None         # window closes at 10:00, not before
        assert out["surprise_ratio"] is None
        assert out["em_pre_pts"] == 30.0
        assert "em_post_print" in out["quality"]


# ------------------------------------------------------------------- rounding
class TestRealizedRounding:
    def test_intraday_and_premarket_realized_rounded_2dp(self):
        # float-noise closes/highs must not write 12.100000000000364 to the diary
        bars = _bars([("13:59", 6300.03), ("14:15", 6312.1)])
        out = ev.read(NOW_FOMC_POST, 6312.1, 6280.0, bars,
                      todays_rows=[_row("13:50", em_points=10.0)], events=[FOMC_EV])
        assert out["realized_post_pts"] == 12.07
        pre = [(f"09:{30 + i:02d}", 6280.07, 6295.13, 6272.02, 6281.0)
               for i in range(12)]
        out = ev.read(NOW_CPI_MID, 6285.0, 6270.01, _bars(pre, day=CPI_DAY),
                      todays_rows=[_row("09:35", em_open=30.0, day=CPI_DAY)],
                      events=[CPI_EV])
        # |6280.07-6270.01| + (6295.13-6272.02) = 10.06 + 23.11 = 33.17
        assert out["realized_post_pts"] == 33.17


# ------------------------------------------------------------------ fallbacks
class TestSigmaFallback:
    def test_no_range_ruler_anywhere_still_prices_ratio(self):
        rows = [_row("13:50", sigma=9.0), _row("14:16", sigma=7.0)]
        bars = _bars([("13:59", 6300.0), ("14:15", 6313.5)])
        out = ev.read(NOW_FOMC_POST, 6313.5, 6280.0, bars,
                      todays_rows=rows, events=[FOMC_EV])
        assert "sigma_fallback" in out["quality"]
        assert out["em_pre_pts"] == 9.0
        assert out["realized_post_pts"] == 13.5
        assert out["surprise_ratio"] == 1.5             # sigma vs sigma: comparable
        assert out["crush_flag"] is True                # 7/9 = 0.78 same-source crush
