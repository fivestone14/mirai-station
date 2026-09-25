"""The clock: time-of-day odds from prior sessions, scored by the grader's own rules, and the blend.

The documented intent these hold (clock.py's docstring, README step 4b): half JEV and half the clock;
at least 10 counted prior sessions, never today; a session counts only when about half its reads were
graded; every replayed read is scored exactly as the grader scores a sum (the row's spot and sigma,
the bands, the close and its grace); per-phase counts shrunk toward the whole day with weight 10; a
stored day is counted again when its files or the rule change; a half day gets no blend.
"""
from __future__ import annotations

import json

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row
from sndk_jev import clock, grade
from sndk_jev.clock import blend, day_counts, odds, phase_of


def _rows(day: str, sigma=50.0, spot=1700.0, minutes=range(0, 390, 5)) -> list[dict]:
    """A diary row every 5 minutes of the session, as the scanner writes them. ``sigma`` may be a
    function of the minute after the open."""
    return [make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=day), spot if not callable(spot) else spot(m),
                     sigma=sigma(m) if callable(sigma) else sigma) for m in minutes]


def _write(tmp_path, prior: dict, rows_for=None) -> "Path":
    (tmp_path / "sndk_reversion").mkdir(exist_ok=True)
    for d in prior:
        rows = rows_for(d) if rows_for else _rows(d)
        (tmp_path / "sndk_reversion" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return tmp_path


def _prior(n: int, bars_for=None) -> dict:
    days = [f"2026-09-{d:02d}" for d in range(1, n + 1)]
    return {d: (bars_for(d) if bars_for else flat_bars(390, day=d)) for d in days}


NOW = at(12, 2, day="2026-09-18")


def test_the_documented_constants():
    """Half and half, ten sessions, shrink weight ten: the numbers the pressure test declared."""
    assert clock.JEV_SHARE == 0.5 and clock.MIN_SESSIONS == 10 and clock.SHRINK == 10.0 and clock.MAX_SESSIONS == 20


def test_phases_follow_the_read_clock():
    assert phase_of(at(9, 32)) == "opening" and phase_of(at(10, 2)) == "morning" and phase_of(at(11, 32)) == "late_morning"
    assert phase_of(at(12, 2)) == "lunch" and phase_of(at(13, 59)) == "lunch" and phase_of(at(15, 32)) == "afternoon"


def test_every_replayed_read_is_scored_by_the_grader():
    """Each counted outcome is the band grade_one gives the same row: one scoring rule for the odds and the sums."""
    day = "2026-09-10"
    closes = [1700.0 + (0.5 if (i // 20) % 2 else -0.4) * (i % 20) for i in range(390)]
    bars = bars_from_closes(closes, day=day)
    rows = _rows(day, spot=lambda m: closes[max(0, m - 1)])
    counts = day_counts(bars, rows, clock.horizons())
    want = {q: {} for q in grade.HORIZONS}
    k, seen = -1, set()
    stamps = [r["ts"] for r in rows]
    t = at(9, 32, day=day)
    while t < at(16, 0, day=day):
        while k + 1 < len(rows) and stamps[k + 1] <= t.isoformat():
            k += 1
        if k >= 0 and rows[k]["ts"] not in seen:
            seen.add(rows[k]["ts"])
            g = grade.grade_one({**rows[k], "row_ts": rows[k]["ts"], "by": {q: {"pick": "flat", "probabilities": {}} for q in grade.HORIZONS}}, bars) or {}
            for q in grade.HORIZONS:
                if (g.get(q) or {}).get("band"):
                    ph = phase_of(clock.parse_ts(rows[k]["ts"]))
                    want[q].setdefault(ph, {}).setdefault(g[q]["band"], 0)
                    want[q][ph][g[q]["band"]] += 1
        t = t.replace(minute=t.minute) + clock.timedelta(minutes=10)
    got = {q: {ph: {o: n for o, n in c.items() if n} for ph, c in counts[q].items() if sum(c.values())} for q in counts}
    assert got == want


def test_the_moments_own_sigma_scales_the_move():
    """A 10-point climb in 30 minutes is up at sigma 50 (0.2) and flat at sigma 200 (0.05)."""
    day = "2026-09-10"
    closes = [1700.0 + i / 3.0 for i in range(390)]                              # 10 points every 30 minutes
    rows = _rows(day, spot=lambda m: closes[max(0, m - 1)], sigma=lambda m: 50.0 if m < 150 else 200.0)
    c30 = day_counts(bars_from_closes(closes, day=day, wick=0.0), rows, clock.horizons())["next_30"]
    assert c30["morning"]["up"] > 0 and c30["morning"]["flat"] == 0
    assert c30["afternoon"]["up"] == 0 and c30["afternoon"]["flat"] > 0


def test_a_read_needs_a_fresh_row():
    """No row within 10 minutes of a replayed minute, no read: a scanner outage is not a quiet read."""
    day = "2026-09-10"
    rows = _rows(day, minutes=[m for m in range(0, 390, 5) if not 150 <= m < 270])     # nothing from 12:00 to 14:00
    counts = day_counts(flat_bars(390, day=day), rows, clock.horizons())
    assert sum(counts["next_30"]["lunch"].values()) == 0                          # the 12:02 read is on the 11:55 row, a late-morning read
    assert sum(counts["next_30"]["morning"].values()) > 0


def test_a_quiet_session_is_all_flat_and_nothing_is_scored_past_the_close():
    day = "2026-09-10"
    c30 = day_counts(flat_bars(390, day=day), _rows(day), clock.horizons())["next_30"]
    assert all(v["up"] == 0 and v["down"] == 0 for v in c30.values())
    assert sum(c30["opening"].values()) == 3                                      # 09:32, 09:42, 09:52
    # afternoon reads run 14:02 .. 15:52; a 30-minute horizon is graded up to 15:32 (16:02 is inside the grace)
    assert sum(c30["afternoon"].values()) == len(range(14 * 60 + 2, 15 * 60 + 33, 10))


def test_too_few_counted_sessions_leave_the_blend_out(tmp_path):
    prior = _prior(9)
    o = odds(_write(tmp_path, prior), tmp_path / "jev", prior, NOW)
    assert "left_out" in o and "10" in o["left_out"]


def test_a_thin_day_is_not_counted_as_a_session(tmp_path):
    """Ten days with bars, one of them with rows for only its first half hour: nine counted sessions."""
    prior = _prior(10)
    thin = "2026-09-05"
    state = _write(tmp_path, prior, rows_for=lambda d: _rows(d, minutes=range(0, 30, 5)) if d == thin else _rows(d))
    o = odds(state, tmp_path / "jev", prior, NOW)
    assert "left_out" in o and "only 9" in o["left_out"]


def test_odds_are_prior_sessions_only_and_shrink_toward_the_whole_day(tmp_path):
    """Mornings that climb and afternoons that sit still: the morning odds lean up, the afternoon's flat,
    each shrunk toward the whole day by the documented weight of 10."""
    closes = [1700.0 + (0.8 * i if i < 90 else 72.0) for i in range(390)]
    def climbing_mornings(d):
        return bars_from_closes(closes, day=d, wick=0.0)
    def rows_for(d):
        return _rows(d, spot=lambda m: closes[max(0, m - 1)])
    prior = _prior(10, climbing_mornings)
    state = _write(tmp_path, prior, rows_for=rows_for)
    today = {"2026-09-18": bars_from_closes([1700.0 - i for i in range(390)], day="2026-09-18")}   # never counted
    o_morning = odds(state, tmp_path / "jev", {**prior, **today}, at(10, 2, day="2026-09-18"))
    o_after = odds(state, tmp_path / "jev", {**prior, **today}, at(14, 2, day="2026-09-18"))
    assert o_morning["sessions"] == 10
    assert o_morning["by"]["next_30"]["probabilities"]["up"] > 0.5 > o_after["by"]["next_30"]["probabilities"]["up"]
    counts = [day_counts(prior[d], rows_for(d), clock.horizons())["next_30"] for d in prior]
    here = {o: sum(c["morning"][o] for c in counts) for o in ("up", "down", "flat")}
    whole = {o: sum(c[p][o] for c in counts for p in c) for o in ("up", "down", "flat")}
    n_here, n_whole = sum(here.values()), sum(whole.values())
    for o in ("up", "down", "flat"):
        want = (here[o] + 10.0 * whole[o] / n_whole) / (n_here + 10.0)
        assert o_morning["by"]["next_30"]["probabilities"][o] == pytest.approx(want, abs=1e-4)


def test_a_stored_day_is_recounted_when_its_files_or_the_rule_change(tmp_path, monkeypatch):
    prior = _prior(10)
    state = _write(tmp_path, prior)
    calls = []
    real = clock.day_counts
    monkeypatch.setattr(clock, "day_counts", lambda *a, **k: calls.append(1) or real(*a, **k))
    odds(state, tmp_path / "jev", prior, NOW)
    assert len(calls) == 10
    odds(state, tmp_path / "jev", prior, NOW)
    assert len(calls) == 10                                                       # nothing changed: all from the cache
    extra = make_row(at(15, 59, day="2026-09-03"), 1700.0)
    with open(state / "sndk_reversion" / "2026-09-03.jsonl", "a") as f:
        f.write(json.dumps(extra) + "\n")                                         # a late row lands in one past day
    odds(state, tmp_path / "jev", prior, NOW)
    assert len(calls) == 11
    monkeypatch.setattr(clock, "RULE_VERSION", clock.RULE_VERSION + 1)
    odds(state, tmp_path / "jev", prior, NOW)
    assert len(calls) == 21                                                       # a new rule counts every day again


def test_a_failed_cache_write_never_costs_the_read(tmp_path, monkeypatch):
    prior = _prior(10)
    state = _write(tmp_path, prior)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(clock.tempfile, "mkstemp", boom)
    o = odds(state, tmp_path / "jev", prior, NOW)
    assert o["sessions"] == 10 and not (tmp_path / "jev" / clock.CACHE_NAME).exists()


def test_a_half_day_read_gets_no_blend(tmp_path):
    """The odds are counted on full sessions; a 12:32 read on a 13:00 close is not one of them."""
    prior = _prior(10)
    o = odds(_write(tmp_path, prior), tmp_path / "jev", prior, at(12, 32, day="2026-11-27"))
    assert "left_out" in o and "half day" in o["left_out"]


def test_blend_is_half_and_half_and_keeps_both_parts():
    jev_30 = {"pick": "flat", "probabilities": {"up": 0.1, "down": 0.1, "flat": 0.7, "unsure": 0.1}, "confidence": 0.6}
    hour = {**jev_30, "primary": "next_30", "by": {"next_30": jev_30}, "model": "m"}
    c = {"phase": "morning", "phase_words": "the morning", "sessions": 20,
         "by": {"next_30": {"probabilities": {"up": 0.4, "down": 0.3, "flat": 0.3}, "n": 100}}}
    b = blend(hour, c)
    assert b["probabilities"] == {"up": 0.25, "down": 0.2, "flat": 0.5, "unsure": 0.05}
    assert b["pick"] == "flat" and b["jev"]["probabilities"] == jev_30["probabilities"] and b["clock"]["n"] == 100
    assert b["by"]["next_30"]["blended"] is True and b["by"]["next_30"]["jev"]["confidence"] == 0.6 and b["confidence"] is None
    assert b["blend"]["used"] is True and b["blend"]["phase"] == "morning"


def test_the_blend_is_used_only_when_the_phones_sum_was_blended():
    """JEV gave the 60-minute sum but no probabilities for the 30: the 30 stands alone and the card says so."""
    hour = {"error": "no next_30 answer", "primary": "next_30",
            "by": {"next_30": {"pick": None, "probabilities": None}, "next_60": {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.4}}}}
    c = {"phase": "lunch", "phase_words": "lunch", "sessions": 12,
         "by": {q: {"probabilities": {"up": 0.2, "down": 0.2, "flat": 0.6}, "n": 50} for q in ("next_30", "next_60")}}
    b = blend(hour, c)
    assert b["blend"]["used"] is False and b["by"]["next_60"]["blended"] is True and "probabilities" not in b


def test_without_odds_jevs_sum_stands_and_says_why():
    hour = {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.4}, "primary": "next_30",
            "by": {"next_30": {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.4}}}}
    b = blend(hour, {"left_out": "only 3 prior sessions"})
    assert b["probabilities"] == hour["probabilities"] and b["blend"] == {"used": False, "why": "only 3 prior sessions"}
