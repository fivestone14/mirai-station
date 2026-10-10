"""The clock: time-of-day odds from prior SPX sessions, scored by the grader's own rules, and the blend."""
from __future__ import annotations

import json

import pytest

from conftest import FixedZones, at, bars_from_closes, flat_bars, make_row
from spx_jev import clock
from spx_jev.clock import blend, day_counts, odds, phase_of, premarket_odds
from spx_jev.lane import LIVE

NOW = at(12, 2, day="2026-09-18")


@pytest.fixture(autouse=True)
def _zones(fixed_zones):
    """Every read, grade and replay here runs on a fixture too short to size a flat zone: the zones are pinned (conftest.FixedZones)."""


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
    counts = day_counts(flat_bars(390, day="2026-09-10"), _rows("2026-09-10"), FixedZones(".", "2026-09-10"))
    assert sum(counts["next_30"]["lunch"].values()) == counts["next_30"]["lunch"]["flat"] > 0
    assert counts["next_30"]["morning"]["up"] == counts["next_30"]["morning"]["down"] == 0


def test_the_replayed_reads_are_graded_in_their_zones_and_carry_the_morning_anchor_for_the_baseline():
    """Price climbs a steady 0.5 point a minute; from noon the row sigma ratchets to 1000 points. Against its
    5.25-point zone every afternoon read still climbs 15 points in 30 minutes: up; the anchor rides beside it."""
    from spx_jev.row_adapter import labeller_row
    day = "2026-09-10"
    bars = bars_from_closes([7700.0 + 0.5 * i for i in range(390)], day=day)
    rows = [labeller_row(make_row(t, 7700.0 + 0.5 * m, sigma=75.0 if t < at(12, 0, day=day) else 1000.0, sigma_anchor=75.0))
            for m in range(0, 390, 5) if (t := at(9 + (30 + m) // 60, (30 + m) % 60, day=day))]
    model = FixedZones(".", day)
    counts = day_counts(bars, rows, model)
    assert counts["next_30"]["lunch"]["up"] == sum(counts["next_30"]["lunch"].values()) > 0
    assert {r["sigma"] for r in clock.replayed_reads(bars, rows, model)} == {75.0}


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


# ---- the settled-open odds (the premarket lane)

PREMARKET_NOW = at(9, 28, day="2026-09-18")


def _climbing(tmp_path, anchors: list[float]) -> dict:
    """One prior session per anchor from 2026-09-01: flat to the 09:34 bar, then half a point a minute, 5
    points by 09:44 and 15 by 10:04, its diary anchored at the given sigma."""
    (tmp_path / "reversion").mkdir(exist_ok=True)
    days = {}
    for k, sigma in enumerate(anchors, start=1):
        d = f"2026-09-{k:02d}"
        days[d] = bars_from_closes([7700.0] * 5 + [7700.0 + 0.5 * i for i in range(1, 386)], day=d)
        (tmp_path / "reversion" / f"{d}.jsonl").write_text(json.dumps(make_row(at(9, 31, day=d), 7700.0, sigma=sigma, sigma_anchor=sigma)) + "\n")
    return days


def test_the_settled_open_odds_need_ten_sessions_and_are_shrunk_toward_thirds(tmp_path):
    assert premarket_odds(tmp_path, _prior(tmp_path, 9), PREMARKET_NOW)["left_out"].startswith("only 9 prior sessions with an outcome")
    c = premarket_odds(tmp_path, _prior(tmp_path, 10), PREMARKET_NOW)
    assert c["phase"] == "settled_open" and c["sessions"] == 10
    assert c["by"]["open_30"] == {"probabilities": {"up": 0.0303, "down": 0.0303, "flat": 0.9394}, "n": 10} == c["by"]["open_10"]


def test_each_session_is_graded_from_its_settled_open_against_its_zones(tmp_path):
    """The same climb, 5 points by 09:44 and 15 by 10:04, is up at both marks against zones of 4 and 7 points, whatever
    the session's own sigma was."""
    c = premarket_odds(tmp_path, _climbing(tmp_path, [75.0] * 6 + [1000.0] * 4), PREMARKET_NOW)
    assert c["sessions"] == 10
    for qid in ("open_10", "open_30"):
        p = c["by"][qid]["probabilities"]
        assert (p["up"], p["flat"], p["down"]) == (round((10 + 1 / 3) / 11, 4), round((1 / 3) / 11, 4), round((1 / 3) / 11, 4))


def test_a_session_without_its_settled_open_bar_and_today_are_not_counted(tmp_path):
    prior = _prior(tmp_path, 10)
    prior["2026-09-03"] = [b for b in prior["2026-09-03"] if b["ts"] != at(9, 34, day="2026-09-03").isoformat()]
    assert premarket_odds(tmp_path, prior, PREMARKET_NOW)["left_out"].startswith("only 9 prior sessions")
    today = {**_prior(tmp_path, 10), "2026-09-18": flat_bars(390, day="2026-09-18")}
    assert premarket_odds(tmp_path, today, PREMARKET_NOW)["sessions"] == 10


def test_the_blend_lifts_the_primary_the_summary_names(tmp_path):
    c = premarket_odds(tmp_path, _prior(tmp_path, 10), PREMARKET_NOW)
    jev = {"up": 0.6, "flat": 0.2, "down": 0.1, "unsure": 0.1}
    hour = {"pick": "up", "probabilities": jev, "primary": "open_30",
            "by": {"open_10": {"pick": "flat", "probabilities": {"flat": 1.0}}, "open_30": {"pick": "up", "probabilities": jev}}}
    b = blend(hour, c)
    assert b["blend"]["used"] is True and b["blend"]["phase"] == "settled_open"
    assert b["probabilities"] == b["by"]["open_30"]["probabilities"] and b["jev"]["probabilities"] == jev
    assert blend({**hour, "by": {"open_10": hour["by"]["open_10"]}}, c)["blend"]["why"] == "JEV gave no probabilities for open_30, so its sum stands alone"


def _spiking(tmp_path, n: int, jump: float = 10.0) -> dict:
    """``n`` sessions at 7700 but for the bar before every tenth minute (09:31, 09:41, ...), which closes ``jump`` points
    up and hands its close to the next bar's open, so it is a real move, not a bad tick. Every read (09:32, 09:42, ...)
    ends its 30-minute window on one: up at the end price, 10 points against a flat zone of 5.25, but flat on the average,
    three such minutes in thirty sitting 1 point up against an edge of 3.11."""
    (tmp_path / "reversion").mkdir(exist_ok=True)
    days = {}
    for d in range(1, n + 1):
        day = f"2026-09-{d:02d}"
        days[day] = bars_from_closes([7700.0 + (jump if i % 10 == 1 else 0.0) for i in range(390)], day=day)
        (tmp_path / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(make_row(at(9 + (32 + m) // 60, (32 + m) % 60, day=day), 7700.0)) + "\n"
                                                                     for m in range(0, 380, 10)))
    return days


def test_the_average_price_odds_are_counted_on_the_average_price_alone(tmp_path, monkeypatch):
    """The same reads on the same sessions: every one up at the end price and flat on the average. The end-price odds
    say up and the average-price odds flat, each from its own file, and counting the one never reads, writes or moves
    the other."""
    out, prior = tmp_path / "spx_jev", _spiking(tmp_path, 10)
    end = odds(tmp_path, out, prior, NOW)
    kept = (out / clock.CACHE_NAME).read_bytes()
    avg = clock.integral_odds(tmp_path, out, prior, NOW)
    assert end["by"]["next_30"]["probabilities"]["up"] > 0.99     # all but the 15:32 read, whose window ends on the closing bar
    assert avg["phase"] == "lunch" and avg["sessions"] == 10 and set(avg["by"]) == {"average_30"}
    assert avg["by"]["average_30"]["probabilities"] == {"up": 0.0, "down": 0.0, "flat": 1.0}
    assert (out / clock.CACHE_NAME).read_bytes() == kept
    stored = json.loads((out / clock.INTEGRAL_CACHE_NAME).read_text())
    rule = json.loads(stored["rule"])
    assert rule["v"] == clock.INTEGRAL_RULE_VERSION and rule["box"][0] == "next_30" and len(stored["days"]) == 10
    assert all(sum(c["flat"] for c in d["counts"].values()) == sum(sum(c.values()) for c in d["counts"].values()) > 0
               for d in stored["days"].values())
    # a stored day is not counted again, and a missing end-price file changes nothing on the average price
    (out / clock.CACHE_NAME).unlink()
    monkeypatch.setattr(clock, "integral_reads", lambda *a, **k: pytest.fail("a stored day was counted again"))
    assert clock.integral_odds(tmp_path, out, prior, NOW) == avg and not (out / clock.CACHE_NAME).exists()


def test_the_average_price_odds_carry_every_part_of_the_day_for_the_phones_sheet(tmp_path):
    """The phone's time-of-day sheet (Will's layout of 09-29) draws each part of the day's odds and every prior session's
    counts: integral_odds hands them over as ``blocks``, each part shrunk as the blend's own part is, so the read's part
    reads the same odds the call was blended with. The blend itself never carries them: they ride the card alone, never
    a day's records."""
    avg = clock.integral_odds(tmp_path, tmp_path / "spx_jev", _spiking(tmp_path, 10), NOW)
    b = avg["blocks"]
    # every span a full stamp on the read's day, as every time on the card is (the phone draws it in the viewer's zone)
    assert [(p["phase"], p["from"], p["to"]) for p in b["phases"]] == [
        ("opening", at(9, 30, day="2026-09-18").isoformat(), at(10, 0, day="2026-09-18").isoformat()), ("morning", at(10, 0, day="2026-09-18").isoformat(), at(11, 0, day="2026-09-18").isoformat()),
        ("late_morning", at(11, 0, day="2026-09-18").isoformat(), at(12, 0, day="2026-09-18").isoformat()), ("lunch", at(12, 0, day="2026-09-18").isoformat(), at(14, 0, day="2026-09-18").isoformat()),
        ("afternoon", at(14, 0, day="2026-09-18").isoformat(), at(16, 0, day="2026-09-18").isoformat())]
    lunch = next(p for p in b["phases"] if p["phase"] == avg["phase"])
    assert {k: lunch[k] for k in ("probabilities", "n")} == avg["by"]["average_30"]
    assert [d["day"] for d in b["days"]] == [f"2026-09-{d:02d}" for d in range(10, 0, -1)] and all(d["counted"] for d in b["days"])
    assert all(set(d["counts"]) == {p[0] for p in clock.PHASES} for d in b["days"])
    blended = blend({"primary": "average_30", "by": {"average_30": {"pick": "up", "probabilities": {"up": 0.5, "down": 0.2, "flat": 0.3}}}}, avg)
    assert blended["blend"]["used"] is True and "blocks" not in blended["blend"] and "blocks" not in json.dumps(blended)


def test_a_session_short_of_graded_reads_is_listed_as_left_out_of_every_part():
    full = {p[0]: {"up": 1, "down": 1, "flat": 2} for p in clock.PHASES}
    thin = {p[0]: {"up": 0, "down": 0, "flat": 1 if p[0] == "lunch" else 0} for p in clock.PHASES}
    cache = {"2026-09-02": {"counts": full}, "2026-09-01": {"counts": thin}}
    b = clock._blocks(["2026-09-02", "2026-09-01"], ["2026-09-02"], cache, NOW)
    assert [(d["day"], d["counted"]) for d in b["days"]] == [("2026-09-02", True), ("2026-09-01", False)]
    assert b["days"][1]["counts"]["lunch"] == {"up": 0, "down": 0, "flat": 1}
    assert all(p["n"] == 4 for p in b["phases"])          # the left-out session counts in no part's odds


def test_the_average_price_odds_need_ten_sessions_and_a_full_day(tmp_path):
    out = tmp_path / "spx_jev"
    few = clock.integral_odds(tmp_path, out, _spiking(tmp_path, 9), NOW)
    assert few == {"left_out": "only 9 prior sessions with enough reads graded on the average price; its time-of-day odds need 10"}
    half = clock.integral_odds(tmp_path, out, _spiking(tmp_path, 10), at(12, 2, day="2026-11-27"))
    assert half["left_out"].startswith("a 13:00 half day")
