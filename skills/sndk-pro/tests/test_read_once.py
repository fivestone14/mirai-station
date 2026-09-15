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
    assert row["era"] == "strikes-5" and row["payload"] == "strikes" and row["legacy_kept"] is True
    assert seen["doctrine"] is sndk_board.DOCTRINE_V2
    scene = json.loads(seen["prompt"].split("SCENE:\n", 1)[1])
    assert "strikes" in scene and "magnet" not in scene and "walls" not in scene
    assert row["reading"]["clusters"][0]["center"] == 1300.0
    assert row["reading"]["sides"]["above"]["heavy"] == 1300.0
    assert row["gate"]["magnet"] == 1300.0                 # the gate still reads the legacy scene
    lg = [json.loads(l) for l in (tmp / "sndk_legacy" / f"{day}.jsonl").read_text().splitlines()]
    assert len(lg) == 1 and lg[0]["era"] == "strikes-5" and lg[0]["magnet"] == 1300.0


def test_a_call_keeps_the_list_it_showed_and_the_next_read_uses_it(board_state, monkeypatch):
    """Review item #7. The row that spends a call keeps `strikes_sent`, the
    strikes the model was shown — never in the prompt itself — and the next
    read measures what joined and left against exactly that list. An errored
    call keeps it too: the next frame is anchored on that row regardless."""
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    seen = {}

    def fake(prompt, model, timeout=None, doctrine=None):
        seen["prompt"] = prompt
        return _v2_reply(), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    import sndk_board
    assert SR.read_once(now=NOW) == 0
    row = _rows(reads)[-1]
    shown = json.loads(seen["prompt"].split("SCENE:\n", 1)[1])["strikes"]
    assert row["strikes_sent"] == sndk_board.listed_strikes(shown) and row["strikes_sent"]
    assert "strikes_sent" not in seen["prompt"]
    assert "left_since_reference" not in shown          # the first read had no earlier list

    # the next read, an hour on: the last call showed 9999, which is not listed now
    later = NOW + timedelta(minutes=SR.HEARTBEAT_MIN + 1)
    dp = tmp / "sndk_reversion" / f"{day}.jsonl"
    dp.write_text(dp.read_text() + json.dumps(_diary_row_with_board(later, spot=1204.0)) + "\n")
    rs = _rows(reads)
    rs[-1]["strikes_sent"] = row["strikes_sent"] + [9999.0]
    reads.write_text("\n".join(json.dumps(r) for r in rs) + "\n")

    def broken(prompt, model, timeout=None, doctrine=None):
        seen["prompt"] = prompt
        return None, "timeout", 100.0, None
    monkeypatch.setattr(SR, "call_the_model", broken)
    assert SR.read_once(now=later) == 0
    nxt = json.loads(seen["prompt"].split("SCENE:\n", 1)[1])["strikes"]
    assert nxt["left_since_reference"] == [9999.0]
    errored = _rows(reads)[-1]
    assert errored["error"] == "timeout" and errored["strikes_sent"]


def test_a_list_shown_with_volume_withheld_is_marked_on_the_row(board_state, monkeypatch):
    """Review item #8: a list drawn while the book still carried yesterday's
    volume was picked on open interest alone, so the row says so and the next
    read does not diff a volume-drawn list against it."""
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    monkeypatch.setattr(SR, "call_the_model", lambda *a, **k: (_v2_reply(), None, 1.0, None))
    import sndk_board
    asof = _diary_row_with_board(NOW - timedelta(minutes=2))["meta"]["book_asof"]
    monkeypatch.setattr(sndk_board, "carried_books",
                        lambda rows, now: {asof: sndk_board.WITHHELD_CARRIED})
    assert SR.read_once(now=NOW) == 0
    row = _rows(reads)[-1]
    assert row["strikes_sent"] and row["strikes_sent_without_volume"] is True


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
    assert not (tmp / "sndk_side").exists()          # the side payload is not kept either
    assert not (tmp / "sndk_payloads").exists()      # nor the message it would have sent
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


# ------------------------------------------------------------- the bill (2026-09-10)
# The envelope below has the shape of a real `claude -p --output-format json`
# reply captured on 2026-09-10; the numbers are that probe's own.
_ENVELOPE = {
    "type": "result", "is_error": False, "result": '{"quiet": true}',
    "total_cost_usd": 0.0702238,
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 16136,
              "cache_read_input_tokens": 28129, "output_tokens": 5,
              "output_tokens_details": {"thinking_tokens": 0},
              "cache_creation": {"ephemeral_1h_input_tokens": 16136,
                                 "ephemeral_5m_input_tokens": 0}},
}


def test_the_bill_is_read_off_the_envelope_in_plain_names():
    """Every count lands under a plain name, and the 1-hour and 5-minute
    cache writes stay apart: a 5-minute write is the first sign the standing
    instructions have stopped being cheap."""
    assert SR.cost_of(_ENVELOPE) == {
        "input_tokens": 2, "cache_read_tokens": 28129, "cache_write_tokens": 16136,
        "cache_write_1h_tokens": 16136, "cache_write_5m_tokens": 0,
        "output_tokens": 5, "thinking_tokens": 0, "cost_usd": 0.0702238}


def test_an_envelope_without_a_bill_gives_nothing_not_an_empty_record():
    assert SR.cost_of({"result": "x"}) is None
    assert SR.cost_of(None) is None
    assert SR.cost_of({"usage": {"output_tokens": 7}}) == {"output_tokens": 7}


_REAL_CALL_THE_MODEL = SR.call_the_model


def test_call_the_model_keeps_the_bill(monkeypatch):
    class _Done:
        returncode, stderr = 0, ""
        stdout = json.dumps(_ENVELOPE)
    monkeypatch.setattr(SR.subprocess, "run", lambda *a, **k: _Done())
    # this test exercises call_the_model itself, so it takes the real function
    # back from the suite-wide guard — with the subprocess above stubbed, so
    # nothing is spent
    monkeypatch.setattr(SR, "call_the_model", _REAL_CALL_THE_MODEL)
    obj, err, _wall, _raw = SR.call_the_model("prompt", "model")
    assert obj == {"quiet": True} and err is None
    assert SR.LAST_COST["cache_read_tokens"] == 28129


def test_a_spent_call_files_its_bill_on_the_row(board_state, monkeypatch):
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    bill = SR.cost_of(_ENVELOPE)

    def fake(prompt, model, timeout=None, doctrine=None):
        monkeypatch.setattr(SR, "LAST_COST", bill)
        return _v2_reply(), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    SR.read_once(now=NOW)
    row = _rows(reads)[-1]
    assert row["cost"] == bill and "review_cost" not in row


def test_a_call_with_no_bill_leaves_no_cost_key(state, monkeypatch):
    tmp, reads_path = state
    reads_path.write_text(json.dumps(_call_row(NOW - timedelta(minutes=70))) + "\n")
    monkeypatch.setattr(SR, "call_the_model",
                        lambda *a, **k: (None, "timed out after 100s", 100.0, None))
    SR.read_once(now=NOW)
    assert "cost" not in _rows(reads_path)[-1]


# ---------------------------------------------------------------- strikes-4 (2026-09-14)
def test_a_spent_call_keeps_the_exact_message_it_sent(board_state, monkeypatch):
    """The payload a call sends is filed before it leaves, in its own file: the
    scene as an object, the rulebook once by hash, and a checksum proving the
    pair rebuilds the sent prompt byte for byte."""
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    seen = {}

    def fake(prompt, model, timeout=None, doctrine=None):
        seen["prompt"], seen["doctrine"] = prompt, doctrine
        seen["kept_before_the_answer"] = (tmp / "sndk_payloads" / f"{day}.jsonl").exists()
        return _v2_reply(), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    import sndk_board
    assert SR.read_once(now=NOW) == 0
    row = _rows(reads)[-1]
    kept = SR.read_payloads(day)
    assert seen["kept_before_the_answer"] and len(kept) == 1 and row["payload_kept"] is True
    rec = kept[0]
    assert rec["ts"] == row["ts"] and rec["era"] == row["era"] == SR.ERA and rec["wake"] == row["wake"]
    assert rec["payload"] == "strikes" and rec["model"] == SR.PINNED_MODEL
    assert SR.sent_prompt(rec) == seen["prompt"]
    assert rec["scene"] == json.loads(seen["prompt"].split("SCENE:\n", 1)[1])
    rules = tmp / "sndk_payloads" / "rules" / f"{rec['rules_sha']}.txt"
    assert rules.read_text(encoding="utf-8") == seen["doctrine"] == sndk_board.DOCTRINE_V2
    assert rec["builder_sha"]
    # a line edited after the fact no longer proves itself
    assert SR.sent_prompt({**rec, "scene": {**rec["scene"], "price": {}}}) is None


def test_a_quiet_read_keeps_no_payload(state):
    tmp, reads_path = state
    reads_path.write_text(json.dumps(_call_row(NOW - timedelta(minutes=10))) + "\n")
    SR.read_once(now=NOW)
    assert _rows(reads_path)[-1]["wake"] == "quiet"
    assert not (tmp / "sndk_payloads").exists()


def test_a_payload_that_cannot_be_kept_never_stops_the_call(board_state, monkeypatch, capsys):
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    (tmp / "blocked").write_text("a file where the folder should be")
    monkeypatch.setattr(SR, "_payloads_dir", lambda: tmp / "blocked")
    called = []

    def fake(prompt, model, timeout=None, doctrine=None):
        called.append(prompt)
        return _v2_reply(), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    assert SR.read_once(now=NOW) == 0
    row = _rows(reads)[-1]
    assert called and row["wall_s"] == 1.0 and "payload_kept" not in row
    assert "payload record skipped" in capsys.readouterr().out


def test_the_next_call_is_shown_the_sentence_the_last_one_left(board_state, monkeypatch):
    """#3: the model is handed what a reader last saw from it, with the clock
    it was written at, and the rulebook it came with is kept only once."""
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    prompts = []

    def fake(prompt, model, timeout=None, doctrine=None):
        prompts.append(prompt)
        return _v2_reply(), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    assert SR.read_once(now=NOW) == 0
    assert "said_then" not in prompts[0]                  # nothing said yet today
    rs = _rows(reads)
    rs[-1]["reading"]["read"] = "1300 holds the most contracts above and 1150 leads below."
    reads.write_text("\n".join(json.dumps(r) for r in rs) + "\n")

    later = NOW + timedelta(minutes=SR.HEARTBEAT_MIN + 1)
    dp = tmp / "sndk_reversion" / f"{day}.jsonl"
    dp.write_text(dp.read_text() + json.dumps(_diary_row_with_board(later, spot=1204.0)) + "\n")
    assert SR.read_once(now=later) == 0
    slr = json.loads(prompts[1].split("SCENE:\n", 1)[1])["context"]["since_last_read"]
    assert slr["said_then"] == "1300 holds the most contracts above and 1150 leads below."
    assert slr["said_at"] == NOW.strftime("%H:%M")
    assert len(SR.read_payloads(day)) == 2
    assert len(list((tmp / "sndk_payloads" / "rules").iterdir())) == 1


def test_a_sentence_the_guards_deleted_leaves_the_older_one_in_front_of_the_model(board_state, monkeypatch):
    """Review 2026-09-14, defect #1. The card keeps showing the last sentence that
    survived; a later call whose prose the guards emptied must not hide it from
    the model, and its clock is that sentence's, not the frame's."""
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    carried = dict(_call_row(NOW - timedelta(minutes=80), read="1300 holds the most contracts."),
                   ts=(NOW - timedelta(minutes=70)).isoformat(), wall_s=None, wake="quiet")
    reads.write_text(
        json.dumps(_call_row(NOW - timedelta(minutes=80), read="1300 holds the most contracts.")) + "\n" +
        # a quiet scan carrying it forward: its own clock is not the sentence's
        json.dumps(carried) + "\n" +
        json.dumps(_call_row(NOW - timedelta(minutes=65), read=None)) + "\n")
    prompts = []

    def fake(prompt, model, timeout=None, doctrine=None):
        prompts.append(prompt)
        return _v2_reply(), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    assert SR.read_once(now=NOW) == 0
    slr = json.loads(prompts[0].split("SCENE:\n", 1)[1])["context"]["since_last_read"]
    assert slr["said_then"] == "1300 holds the most contracts."
    assert slr["said_at"] == (NOW - timedelta(minutes=80)).strftime("%H:%M")
    assert slr["last_read_at"] == (NOW - timedelta(minutes=65)).strftime("%H:%M")


def test_a_paused_wake_keeps_nothing_and_a_timed_out_call_keeps_what_it_sent(board_state, monkeypatch):
    tmp, reads, day = board_state
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    control = tmp / "sndk_reads" / "control.json"
    control.write_text(json.dumps({"reasoning": False}))
    reads.write_text(json.dumps(_call_row(NOW - timedelta(minutes=70))) + "\n")

    def refuse(*a, **k):
        raise AssertionError("a paused wake must not call the model")
    monkeypatch.setattr(SR, "call_the_model", refuse)
    assert SR.read_once(now=NOW) == 0
    assert _rows(reads)[-1].get("paused") is True
    assert not (tmp / "sndk_payloads").exists()

    control.write_text(json.dumps({"reasoning": True}))
    seen = {}

    def timed_out(prompt, model, timeout=None, doctrine=None):
        seen["prompt"] = prompt
        return None, "timeout", 180.0, None
    monkeypatch.setattr(SR, "call_the_model", timed_out)
    assert SR.read_once(now=NOW) == 0
    row = _rows(reads)[-1]
    assert row["error"] == "timeout" and row["payload_kept"] is True
    kept = SR.read_payloads(day)
    assert len(kept) == 1 and SR.sent_prompt(kept[0]) == seen["prompt"]


def test_the_scene_payload_keeps_its_own_rulebook(state, monkeypatch):
    tmp, reads_path = state
    monkeypatch.setenv("SNDK_PAYLOAD", "scene")
    reads_path.write_text(json.dumps(_call_row(NOW - timedelta(minutes=70))) + "\n")
    seen = {}

    def fake(prompt, model, timeout=None, doctrine=None):
        seen["prompt"], seen["doctrine"] = prompt, doctrine
        return None, "timeout", 100.0, None
    monkeypatch.setattr(SR, "call_the_model", fake)
    SR.read_once(now=NOW)
    rec = SR.read_payloads(NOW.date().isoformat())[-1]
    assert seen["doctrine"] is None and rec["payload"] == "scene"
    assert SR.sent_prompt(rec) == seen["prompt"]
    rules = tmp / "sndk_payloads" / "rules" / f"{rec['rules_sha']}.txt"
    assert rules.read_text(encoding="utf-8") == SR._DOCTRINE


def test_code_version_reads_a_clone_packed_refs_and_a_worktree(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / "plugin"
    skill = root / "skills" / "sndk-pro"
    skill.mkdir(parents=True)
    for name in SR._BUILDER_SOURCES:
        (skill / name).write_text(f"# {name}\n")
    monkeypatch.setattr(SR, "_SKILL_DIR", skill)
    git = root / ".git"
    (git / "refs" / "heads" / "feat").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/feat/x\n")
    (git / "refs" / "heads" / "feat" / "x").write_text("ab" * 20 + "\n")
    v = SR._code_version()
    assert v["commit"] == "ab" * 6 and v["branch"] == "feat/x" and len(v["builder_sha"]) == 12

    (git / "refs" / "heads" / "feat" / "x").unlink()
    (git / "packed-refs").write_text("# pack-refs with: peeled\n" + "cd" * 20 + " refs/heads/feat/x\n")
    assert SR._code_version()["commit"] == "cd" * 6

    # a worktree: .git is a file naming its gitdir relatively; HEAD there is detached
    shutil.move(str(git), str(tmp_path / "main.git"))
    wt = tmp_path / "main.git" / "worktrees" / "wt"
    wt.mkdir(parents=True)
    (wt / "HEAD").write_text("ef" * 20 + "\n")
    (wt / "commondir").write_text("../..\n")
    (root / ".git").write_text("gitdir: ../main.git/worktrees/wt\n")
    v = SR._code_version()
    assert v["commit"] == "ef" * 6 and "branch" not in v

    before = v["builder_sha"]
    (skill / "sndk_board.py").write_text("# edited and not committed\n")
    assert SR._code_version()["builder_sha"] != before


def test_a_prompt_without_a_scene_rides_as_text_and_a_short_rulebook_is_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    day = NOW.date().isoformat()
    SR.keep_payload(day, NOW, "no scene marker in this prompt", "the rulebook", era="x")
    rec = SR.read_payloads(day)[-1]
    assert rec["prompt"] == "no scene marker in this prompt" and "scene" not in rec
    assert SR.sent_prompt(rec) == "no scene marker in this prompt"
    rules = tmp_path / "sndk_payloads" / "rules" / f"{rec['rules_sha']}.txt"
    rules.write_text("")                                  # what a crash mid-write could leave
    SR.keep_payload(day, NOW, "again", "the rulebook", era="x")
    assert rules.read_text(encoding="utf-8") == "the rulebook"
    assert not list(rules.parent.glob("*.tmp"))
