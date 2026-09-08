"""The price track's only job is to land each reading in the right slice and to
be honest about the ones it never saw. Both are easy to get subtly wrong and
neither is visible on the screen — a track off by one slice still draws a
plausible line."""
import json

import pytest

from book_flow import track as T


@pytest.fixture
def diary(tmp_path, monkeypatch):
    def write(rows, day="2026-09-04"):
        p = tmp_path / f"{day}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows))
        monkeypatch.setattr(T, "DIARY", str(tmp_path / "{day}.jsonl"))
        return day
    return write


def row(hhmm, spot, vwap=None, ticker="SPX", day="2026-09-04"):
    r = {"ts": f"{day}T{hhmm}:00-04:00", "spot": spot, "ticker": ticker}
    if vwap is not None:
        r["vwap"] = vwap
    return r


STARTS = ["2026-09-04T09:30:00-04:00",
          "2026-09-04T09:35:00-04:00",
          "2026-09-04T09:40:00-04:00"]


def test_readings_land_in_their_own_slice(diary):
    day = diary([row("09:31", 100.0), row("09:36", 200.0), row("09:41", 300.0)])
    t = T.spot_track(day, "SPX", STARTS, 300)
    assert t["last"] == [100.0, 200.0, 300.0]


def test_lo_hi_is_the_range_inside_the_slice(diary):
    day = diary([row("09:30", 100.0), row("09:32", 107.0), row("09:34", 103.0)])
    t = T.spot_track(day, "SPX", STARTS, 300)
    assert (t["lo"][0], t["hi"][0], t["last"][0]) == (100.0, 107.0, 103.0)


def test_a_level_carries_forward_but_a_range_never_does(diary):
    """The gap is the whole point: price did not stop existing because no scan
    landed, but travel that was never observed must not be drawn."""
    day = diary([row("09:31", 100.0, vwap=99.0)])
    t = T.spot_track(day, "SPX", STARTS, 300)
    assert t["last"] == [100.0, 100.0, 100.0]
    assert t["vwap"] == [99.0, 99.0, 99.0]
    assert t["lo"] == [100.0, None, None]
    assert t["hi"] == [100.0, None, None]
    assert t["obs"] == 1


def test_each_symbol_reads_its_own_diary(tmp_path, monkeypatch):
    """The bug this pins was silent. SNDK's diary lives in state/sndk_reversion,
    not state/reversion, and reading the wrong one does not raise — the file
    parses, every row is rejected by the ticker filter, and the board simply
    draws no price line and says nothing about why."""
    for sub, tick, spot in (("reversion", "SPX", 7700.0),
                            ("sndk_reversion", "SNDK", 1600.0)):
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-09-04.jsonl").write_text(json.dumps(row("09:31", spot, ticker=tick)))
    monkeypatch.setattr(T, "DIARY", str(tmp_path / "{dir}" / "{day}.jsonl"))
    assert T.spot_track("2026-09-04", "SPX", STARTS, 300)["last"][0] == 7700.0
    assert T.spot_track("2026-09-04", "SNDK", STARTS, 300)["last"][0] == 1600.0
    assert T.spot_track("2026-09-04", "TSLA", STARTS, 300) is None


def test_no_diary_is_none_not_a_flat_line_at_zero(diary, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIARY", str(tmp_path / "nothing" / "{day}.jsonl"))
    assert T.spot_track("2026-09-04", "SPX", STARTS, 300) is None


def test_a_diary_of_the_wrong_symbol_is_not_blended_in(diary):
    day = diary([row("09:31", 100.0), row("09:36", 999.0, ticker="SNDK")])
    t = T.spot_track(day, "SPX", STARTS, 300)
    assert t["last"] == [100.0, 100.0, 100.0]     # carried, never the other symbol
    assert t["max"] == 100.0


def test_readings_before_the_board_opens_are_dropped(diary):
    day = diary([row("04:15", 55.0), row("09:31", 100.0)])
    t = T.spot_track(day, "SPX", STARTS, 300)
    assert t["lo"][0] == 100.0 and t["min"] == 55.0   # dropped from the board,
                                                      # still reported as seen
def test_a_torn_line_does_not_kill_the_track(diary, tmp_path, monkeypatch):
    p = tmp_path / "2026-09-04.jsonl"
    p.write_text(json.dumps(row("09:31", 100.0)) + "\n{\"ts\": \"2026-09-")
    monkeypatch.setattr(T, "DIARY", str(tmp_path / "{day}.jsonl"))
    t = T.spot_track("2026-09-04", "SPX", STARTS, 300)
    assert t["last"][0] == 100.0
