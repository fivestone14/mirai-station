"""read_once end-to-end against a throwaway state dir — the night-crew
regressions that only show up at the row-writing layer (obs-3 QA, 2026-09-01).
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import sndk_read as SR

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=ET)   # a Friday, mid-session


def _diary_row(ts, spot=1200.0):
    return {"ts": ts.isoformat(), "ticker": "SNDK", "spot": spot,
            "sigma": 100.0, "regime": "trending", "gamma_sign": "negative",
            "call_wall": 1400.0, "put_wall": 1000.0, "prior_close": 1250.0,
            "gex_views": {"mass_by_strike": [[1300, 60], [1100, 20]],
                          "shove": {"shove_up_margin": 2.0,
                                    "shove_down_margin": 0.2}},
            "profile_ladder": {},
            "meta": {"book_asof": ts.isoformat()}}


def _call_row(ts, read="the heaviest strike sat at 1300 all morning"):
    row = _diary_row(ts)
    return {"ts": ts.isoformat(), "era": SR.ERA, "wake": "heartbeat",
            "spot": 1200.0, "sigma": 100.0, "wall_s": 5.0,
            "gate": SR.state_for_next_wake(row),
            "reading": ({"quiet": False, "read": read,
                         "points": [{"level": 1300.0, "note": "heaviest"}]}
                        if read else {"quiet": True, "abstain": "forced"}),
            "reading_ts": ts.isoformat()}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    (tmp_path / "sndk_reversion").mkdir()
    (tmp_path / "sndk_reads").mkdir()
    day = NOW.date().isoformat()
    dp = tmp_path / "sndk_reversion" / f"{day}.jsonl"
    dp.write_text("\n".join(
        json.dumps(_diary_row(NOW - timedelta(minutes=m), spot=1200.0 + m))
        for m in (8, 6, 4, 2)) + "\n")
    return tmp_path, tmp_path / "sndk_reads" / f"{day}.jsonl"


def _rows(reads_path):
    return [json.loads(l) for l in reads_path.read_text().splitlines()]


def test_the_cap_blocks_the_call_but_never_the_record(state, monkeypatch):
    """Hitting DAILY_CALL_CAP used to return before writing — the store went
    silent for the rest of the busiest kind of day. The row must land, stamped
    "capped", with the standing sentence carried forward."""
    tmp, reads_path = state
    reads_path.write_text("\n".join(
        json.dumps(_call_row(NOW - timedelta(minutes=300 - i * 9)))
        for i in range(SR.DAILY_CALL_CAP)) + "\n")

    def boom(*a, **k):
        raise AssertionError("capped run must never call the model")
    monkeypatch.setattr(SR, "call_the_model", boom)
    SR.read_once(now=NOW)
    last = _rows(reads_path)[-1]
    assert last["wake"] == "capped"
    assert last["wall_s"] is None
    assert last["reading"]["read"].startswith("the heaviest strike")


def test_carry_forward_skips_a_reading_with_no_prose(state):
    """`said` is the last row whose reading still HAS PROSE — a forced abstain
    whose sentence the gates fully deleted must not be carried as the standing
    sentence (carrying it blanked the screen exactly like the timeout bug)."""
    tmp, reads_path = state
    reads_path.write_text(
        json.dumps(_call_row(NOW - timedelta(minutes=20),
                             read="the sentence that should survive")) + "\n" +
        json.dumps(_call_row(NOW - timedelta(minutes=10), read=None)) + "\n")
    SR.read_once(now=NOW)   # gate quiet: last call 10m ago, nothing crossed
    last = _rows(reads_path)[-1]
    assert last["wake"] == "quiet"
    assert last["reading"]["read"] == "the sentence that should survive"


def test_a_failed_call_files_no_memory_and_keeps_the_raw_reply(state,
                                                               monkeypatch):
    """A timeout carries the old sentence forward for the SCREEN — but the
    memory store must not file that carry as a new slice, and the raw stdout
    must land on the row so the failure is diagnosable."""
    tmp, reads_path = state
    reads_path.write_text(
        json.dumps(_call_row(NOW - timedelta(minutes=70))) + "\n")
    monkeypatch.setattr(SR, "call_the_model",
                        lambda *a, **k: (None, "timed out after 100s",
                                         100.0, "half a JSON object {"))
    import sndk_rag
    filed = []
    monkeypatch.setattr(sndk_rag, "record_slice",
                        lambda *a, **k: filed.append(a))
    SR.read_once(now=NOW)   # 70m since last call -> heartbeat wake
    last = _rows(reads_path)[-1]
    assert last["error"] == "timed out after 100s"
    assert last["raw_reply"] == "half a JSON object {"
    assert last["reading"]["read"].startswith("the heaviest strike")
    assert filed == []


def test_first_read_after_an_era_change_says_so(state):
    """Rows from an older era exist for the day: the frame must say "first
    under the current rules", not "first read of the session" — the session
    plainly had reads."""
    row = _diary_row(NOW)
    old_era = dict(_call_row(NOW - timedelta(minutes=30)), era="obs-2")
    fr = SR.frame_since_last_read(row, [row], None, "first read", False, NOW,
                                  prior_rows_today=True)
    assert fr == {"first_read_under_current_rules": True}
    fr0 = SR.frame_since_last_read(row, [row], None, "first read", False, NOW,
                                   prior_rows_today=False)
    assert fr0 == {"first_read_of_session": True}
