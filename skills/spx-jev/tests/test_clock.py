"""The clock: time-of-day odds from prior SPX sessions, scored by the grader's own rules, and the blend."""
from __future__ import annotations

import json

from conftest import at, bars_from_closes, flat_bars, make_row
from spx_jev import clock
from spx_jev.clock import blend, day_counts, odds, phase_of
from spx_jev.lane import LIVE

NOW = at(12, 2, day="2026-09-18")


def _rows(day: str) -> list[dict]:
    from spx_jev.row_adapter import labeller_row
    return [labeller_row(make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=day), 7700.0)) for m in range(0, 390, 5)]


def _prior(tmp_path, n: int) -> dict:
    (tmp_path / "reversion").mkdir(exist_ok=True)
    days = {f"2026-09-{d:02d}": flat_bars(390, day=f"2026-09-{d:02d}") for d in range(1, n + 1)}
    for d in days:
        (tmp_path / "reversion" / f"{d}.jsonl").write_text("".join(json.dumps(make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=d), 7700.0)) + "\n"
                                                                   for m in range(0, 390, 5)))
    return days


def test_the_documented_constants():
    assert clock.JEV_SHARE == 0.5 and clock.MIN_SESSIONS == 10 and clock.SHRINK == 10.0 and clock.MAX_SESSIONS == 20


def test_phases_follow_the_read_clock():
    assert phase_of(at(9, 32)) == "opening" and phase_of(at(10, 2)) == "morning" and phase_of(at(12, 2)) == "lunch"
    assert phase_of(at(15, 32)) == "afternoon"


def test_a_flat_day_counts_every_graded_read_as_flat():
    counts = day_counts(flat_bars(390, day="2026-09-10"), _rows("2026-09-10"), LIVE.horizons)
    assert sum(counts["next_30"]["lunch"].values()) == counts["next_30"]["lunch"]["flat"] > 0
    assert counts["next_30"]["morning"]["up"] == counts["next_30"]["morning"]["down"] == 0


def test_the_replayed_reads_are_graded_in_the_morning_anchor_not_the_ratchet():
    """Price climbs a steady 0.5 point a minute; from noon the row sigma ratchets to 1000 points. On
    the morning anchor every afternoon read still climbs 15 points in 30 minutes, 0.2 sigma: up."""
    from spx_jev.row_adapter import labeller_row
    day = "2026-09-10"
    bars = bars_from_closes([7700.0 + 0.5 * i for i in range(390)], day=day)
    rows = [labeller_row(make_row(t, 7700.0 + 0.5 * m, sigma=75.0 if t < at(12, 0, day=day) else 1000.0, sigma_anchor=75.0))
            for m in range(0, 390, 5) if (t := at(9 + (30 + m) // 60, (30 + m) % 60, day=day))]
    counts = day_counts(bars, rows, LIVE.horizons)
    assert counts["next_30"]["lunch"]["up"] == sum(counts["next_30"]["lunch"].values()) > 0
    assert {r["sigma"] for r in clock.replayed_reads(bars, rows, LIVE.horizons)} == {75.0}


def test_the_odds_need_ten_sessions_and_then_blend_half_and_half(tmp_path):
    out = tmp_path / "spx_jev"
    few = odds(tmp_path, out, _prior(tmp_path, 9), NOW)
    assert few["left_out"].startswith("only 9 prior sessions")
    c = odds(tmp_path, out, _prior(tmp_path, 10), NOW)
    assert c["phase"] == "lunch" and c["sessions"] == 10 and c["by"]["next_30"]["probabilities"]["flat"] == 1.0
    assert (out / clock.CACHE_NAME).is_file()
    hour = {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.05, "unsure": 0.05},
            "by": {"next_30": {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.05, "unsure": 0.05}}}}
    b = blend(hour, c)
    assert b["blend"]["used"] is True and b["probabilities"]["flat"] == 0.65 and b["probabilities"]["unsure"] == 0.025
    assert b["jev"]["pick"] == "up" and b["pick"] == "flat"



def test_a_day_is_counted_again_when_its_market_context_changes(tmp_path, monkeypatch):
    """The VIX an estimated anchor falls back on is read from the day's context files, so a stored day
    whose context file appears or changes is recounted, and the others are not."""
    out, prior = tmp_path / "spx_jev", _prior(tmp_path, 10)
    odds(tmp_path, out, prior, NOW)
    counted = []
    monkeypatch.setattr(clock, "day_counts", lambda bars, *rest: counted.append(bars[0]["ts"][:10]) or {})
    odds(tmp_path, out, prior, NOW)
    assert counted == []
    (tmp_path / "spx_jev" / "context").mkdir(parents=True)
    (tmp_path / "spx_jev" / "context" / "2026-09-03.jsonl").write_text("{}\n")
    odds(tmp_path, out, prior, NOW)
    assert counted == ["2026-09-03"]

def test_a_half_day_gets_no_blend(tmp_path):
    c = odds(tmp_path, tmp_path / "spx_jev", {}, at(12, 2, day="2026-11-27"))
    assert c["left_out"].startswith("a 13:00 half day")
    assert blend({"by": {"next_30": {"pick": "up", "probabilities": {"up": 1.0}}}}, c)["blend"]["used"] is False
