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


# ---------------------------------------------------------------- strikes-1 (2026-09-05)
def _diary_row_with_board(ts, spot=1200.0):
    """A diary row carrying the per-strike surfaces the Strikes Payload reads."""
    row = _diary_row(ts, spot)
    grid = [1100.0, 1150.0, 1200.0, 1250.0, 1300.0]
    oi = {1100.0: (20, 10), 1150.0: (30, 15), 1200.0: (40, 20), 1250.0: (60, 30), 1300.0: (600, 300)}
    row["gex_views"].update({
        "magnet": 1300.0,
        "oi_side_by_strike": [[k, c, p] for k, (c, p) in oi.items()],
        "vol_side_by_strike": [[k, c // 10, p // 10] for k, (c, p) in oi.items()],
        "net_by_strike": [[k, float(c + p)] for k, (c, p) in oi.items()],
        "mass_by_strike": [[k, c + p + c // 10 + p // 10] for k, (c, p) in oi.items()],
    })
    row["meta"]["chain_spot"] = spot
    return row


@pytest.fixture
def board_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    (tmp_path / "sndk_reversion").mkdir()
    (tmp_path / "sndk_reads").mkdir()
    day = NOW.date().isoformat()
    (tmp_path / "sndk_reversion" / f"{day}.jsonl").write_text("\n".join(
        json.dumps(_diary_row_with_board(NOW - timedelta(minutes=m), spot=1200.0 + m))
        for m in (8, 6, 4, 2)) + "\n")
    return tmp_path, tmp_path / "sndk_reads" / f"{day}.jsonl", day


def _v2_reply(**over):
    obj = {"quiet": False,
           "read": "Price has held between 1200 and 1208 since your last read. 1300 holds the most contracts above and 1150 leads below.",
           "sides": {"above": {"heavy": 1300.0, "leads_on": ["contracts"]}, "below": {"heavy": 1150.0, "leads_on": []}},
           "clusters": [{"strikes": [1300.0], "center": 1300.0, "rank": 1, "change": "stable"}],
           "resolved": [], "points": [{"level": 1300.0, "note": "most contracts"}], "absent": []}
    obj.update(over)
    return obj


def test_strikes_mode_end_to_end(board_state, monkeypatch):
    """The first live tick in strikes mode: the model gets the Strikes Payload
    and DOCTRINE_V2, the row says so, the reading carries the v2 keys, and the
    Gate Payload lands in its own file."""
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    seen = {}

    def fake(prompt, model, timeout=None, doctrine=None):
        seen["prompt"], seen["doctrine"] = prompt, doctrine
        return _v2_reply(), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    import sndk_board
    assert SR.read_once(now=NOW) == 0
    row = _rows(reads)[-1]
    assert row["era"] == "strikes-1" and row["payload"] == "strikes" and row["legacy_kept"] is True
    assert seen["doctrine"] is sndk_board.DOCTRINE_V2
    scene = json.loads(seen["prompt"].split("SCENE:\n", 1)[1])
    assert "strikes" in scene and "magnet" not in scene and "walls" not in scene
    assert row["reading"]["clusters"][0]["center"] == 1300.0
    assert row["reading"]["sides"]["above"]["heavy"] == 1300.0
    assert row["gate"]["magnet"] == 1300.0                 # the gate still reads the legacy scene
    lg = [json.loads(l) for l in (tmp / "sndk_legacy" / f"{day}.jsonl").read_text().splitlines()]
    assert len(lg) == 1 and lg[0]["era"] == "strikes-1" and lg[0]["magnet"] == 1300.0


def test_board_failure_falls_back_to_the_scene_payload(board_state, monkeypatch, capsys):
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    import sndk_board
    monkeypatch.setattr(sndk_board, "build_scene_v2", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    seen = {}

    def fake(prompt, model, timeout=None, doctrine=None):
        seen["doctrine"] = doctrine
        return {"quiet": True, "read": "Nothing stood out.", "points": []}, None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    assert SR.read_once(now=NOW) == 0
    row = _rows(reads)[-1]
    assert row["payload"] == "scene" and "legacy_kept" not in row
    assert seen["doctrine"] is None                        # the live doctrine, by default
    assert "strikes payload failed" in capsys.readouterr().out
    assert not (tmp / "sndk_legacy").exists()


def test_the_control_file_switch_and_its_malformed_shapes(board_state, monkeypatch):
    tmp, reads, day = board_state
    monkeypatch.delenv("SNDK_PAYLOAD", raising=False)
    ctl = tmp / "sndk_reads" / "control.json"
    ctl.write_text(json.dumps({"reasoning": True, "payload": "scene"}))
    assert SR.payload_mode() == "scene" and SR.active_era() == "obs-5"
    for bad in ('', 'null', '[]', '"scene"', '{"payload": 7}', '{"payload": "Scene"}', '{"payload": null}', '{', '{"reasoning": true}'):
        ctl.write_text(bad)
        assert SR.payload_mode() == SR.PAYLOAD_DEFAULT, bad
    ctl.unlink()
    assert SR.payload_mode() == SR.PAYLOAD_DEFAULT
    monkeypatch.setenv("SNDK_PAYLOAD", "scene")
    assert SR.payload_mode() == "scene"                    # the environment wins
    monkeypatch.setattr(SR, "call_the_model",
                        lambda prompt, model, timeout=None, doctrine=None: ({"quiet": True, "read": "Quiet.", "points": []}, None, 1.0, None))
    assert SR.read_once(now=NOW) == 0
    assert _rows(reads)[-1]["era"] == "obs-5"


def test_a_dry_run_in_strikes_mode_writes_nothing(board_state, monkeypatch, capsys):
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    monkeypatch.setattr(SR, "call_the_model", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call on a dry run")))
    assert SR.read_once(now=NOW, force=True, dry=True) == 0
    assert not reads.exists() and not (tmp / "sndk_legacy").exists()
    out = capsys.readouterr().out
    assert '"payload": "strikes"' in out and '"strikes"' in out.split("--- scene handed to the model ---")[1]


def test_the_call_cap_and_gap_span_both_eras(board_state, monkeypatch):
    """Flipping the switch mid-day must not restart the budget: 30 legacy-era
    calls already today mean the strikes-era tick records, and does not call."""
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    rows = []
    for i in range(SR.DAILY_CALL_CAP):
        r = _call_row(NOW - timedelta(minutes=300 - i * 9))
        r["era"] = SR.LEGACY_ERA
        rows.append(json.dumps(r))
    reads.write_text("\n".join(rows) + "\n")
    monkeypatch.setattr(SR, "call_the_model", lambda *a, **k: (_ for _ in ()).throw(AssertionError("capped")))
    assert SR.read_once(now=NOW) == 0
    assert _rows(reads)[-1]["wake"] == "capped"
