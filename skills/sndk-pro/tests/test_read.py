"""sndk_read tests — the gates, the dwell, and the bans.

Every rule this module enforces exists because the four recorded sessions
(2026-07-28..31, 756 rows) measured a specific failure. These tests pin the
rule, not the implementation: the arrow must stay silent on a tie, the model
must never be handed a constant, and a live call must not blink.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import sndk_read as SR

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 31, 10, 0, tzinfo=ET)


def mkrow(mass, up=2.0, dn=0.2, spot=1200.0, sigma=100.0, ts=None, **kw):
    """A diary row carrying only what the reader touches."""
    row = {
        "ts": (ts or T0).isoformat(), "ticker": "SNDK", "spot": spot,
        "sigma": sigma, "regime": kw.pop("regime", "trending"),
        "gamma_sign": kw.pop("gamma_sign", "negative"),
        "call_wall": kw.pop("call_wall", 1400.0),
        "put_wall": kw.pop("put_wall", 1000.0),
        "prior_close": kw.pop("prior_close", 1250.0),
        "gex_views": {"mass_by_strike": mass, "magnet": kw.pop("magnet", None),
                      "shove": {"shove_up_margin": up, "shove_down_margin": dn}},
        "profile_ladder": kw.pop("profile_ladder", {}),
    }
    row.update(kw)
    return row


# --- magnet_band: the tie must be visible ----------------------------------
def test_band_reports_top_three_and_gap():
    b = SR.magnet_band(mkrow([[1300, 50], [1100, 30], [1200, 20]]))
    assert [k for k, _ in b["top"]] == [1300, 1100, 1200]
    assert b["gap_pp"] == pytest.approx(20.0)
    assert b["tie"] is False


def test_band_flags_a_near_tie():
    """Median real gap is 3.87pp — under MAGNET_SEP_PP the band must say tie."""
    b = SR.magnet_band(mkrow([[1300, 104], [1100, 100], [1200, 96]]))
    assert b["gap_pp"] < SR.MAGNET_SEP_PP
    assert b["tie"] is True
    assert b["lo"] == 1100 and b["hi"] == 1300      # band spans both contenders


def test_band_reports_the_observation_window():
    """The strike window is spot-relative and moved 8.0%->23.4% within 07-31, so
    a 'magnet moved' can be the telescope, not the book. The window must ship."""
    b = SR.magnet_band(mkrow([[1000, 5], [1400, 9]]))
    assert b["in_window"] == [1000, 1400]


def test_band_empty_is_honest_not_zero():
    b = SR.magnet_band(mkrow([]))
    assert b["top"] == [] and b["gap_pp"] is None and b["tie"] is True


# --- the aggregator: silence is the default --------------------------------
def test_silent_when_no_strike_is_in_charge():
    a = SR.aggregate(mkrow([[1300, 104], [1100, 100]]), None, T0)
    assert a["dir"] is None and a["state"] == "silent"
    assert "no strike is in charge" in a["silent_because"]


def test_silent_when_the_book_is_too_flat():
    """Magnet separated but the mass is not lopsided — breadth vetoes."""
    a = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=1.0, dn=1.0), None, T0)
    assert a["dir"] is None
    assert "too flat" in a["silent_because"]


def test_direction_comes_from_the_magnet_alone():
    a = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1), None, T0)
    assert a["dir"] == "up"                      # 1300 sits above spot 1200
    src = [l for l in a["layers"] if l["name"] == "magnet"][0]
    assert src["role"] == "source" and src["dir"] == "up"


def test_shove_never_votes():
    """Shove and the magnet are the same gamma mass measured twice — replayed,
    they agreed 69/69. It may gate, it may never supply a direction."""
    a = SR.aggregate(mkrow([[1100, 60], [1300, 20]], up=2.0, dn=0.1), None, T0)
    sh = [l for l in a["layers"] if l["name"] == "shove"][0]
    assert sh["role"] == "breadth"
    assert sh["dir"] is None
    assert "dir_raw" not in sh                   # no smuggled second opinion
    assert a["dir"] == "down"                    # magnet 1100 is below spot


def test_every_layer_cites_a_checkable_number():
    a = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1), None, T0)
    for l in a["layers"]:
        assert l["cite"] and any(ch.isdigit() for ch in l["cite"])


# --- dwell: a live call must not blink -------------------------------------
def _prev(dir_, since, spot=1200.0):
    return {"ts": since.isoformat(), "spot": spot,
            "arrow": {"dir": dir_, "since": since.isoformat(), "run": 1,
                      "issued_spot": spot}}


def test_reversal_is_held_inside_the_dwell():
    prev = _prev("up", T0 - timedelta(minutes=5))
    a = SR.aggregate(mkrow([[1100, 60], [1300, 20]], up=2.0, dn=0.1),
                     prev, T0)
    assert a["dir"] == "up" and a["held_reversal"] is True
    assert a["fading"] and "only 5m old" in a["fading"]


def test_stand_down_is_also_held():
    """A reversal-only rule still let the arrow blink 20x in one session."""
    prev = _prev("up", T0 - timedelta(minutes=3))
    a = SR.aggregate(mkrow([[1300, 104], [1100, 100]]), prev, T0)   # gate fails
    assert a["dir"] == "up" and a["held_reversal"] is True
    assert a["state"] == "call" and a["silent_because"] is None


def test_reversal_allowed_once_the_dwell_expires():
    prev = _prev("up", T0 - timedelta(minutes=SR.MIN_DWELL_MIN + 1))
    a = SR.aggregate(mkrow([[1100, 60], [1300, 20]], up=2.0, dn=0.1),
                     prev, T0)
    assert a["dir"] == "down" and a["held_reversal"] is False
    assert a["run"] == 2                        # a new call increments the run


def test_ghost_is_written_when_a_call_ends():
    prev = _prev("down", T0 - timedelta(minutes=40), spot=1150.0)
    a = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1), prev, T0)
    assert a["dir"] == "up"                      # the down call just ended
    assert a["ghost"]["dir"] == "down"
    assert a["ghost"]["spot"] == 1150.0
    assert a["ghost"]["ended"] == T0.isoformat()


def test_ghost_is_never_the_live_arrow():
    """The first cut read prev_dir straight off the last row, so while a call was
    live the ghost was that same call's own origin — 25 of 26 rows on 07-30."""
    prev = _prev("up", T0 - timedelta(minutes=40))
    a = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1), prev, T0)
    assert a["dir"] == "up" and a["ghost"] is None


def test_ghost_persists_while_nothing_ends():
    """It used to survive exactly one row, which is the opposite of 'a bad call
    stays visible'. Now it holds until ANOTHER call completes."""
    prev = _prev("down", T0 - timedelta(minutes=40), spot=1150.0)
    ended = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1), prev, T0)
    assert ended["ghost"]["dir"] == "down"
    # two more scans on the SAME live call — nothing completes, ghost untouched
    cur = ended
    for k in (2, 4):
        cur = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1),
                           {"ts": T0.isoformat(), "spot": 1200.0, "arrow": cur},
                           T0 + timedelta(minutes=k))
        assert cur["dir"] == "up"
        assert cur["ghost"]["dir"] == "down" and cur["ghost"]["spot"] == 1150.0


def test_the_newest_completed_call_becomes_the_ghost():
    prev = _prev("down", T0 - timedelta(minutes=40), spot=1150.0)
    up = SR.aggregate(mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1), prev, T0)
    late = T0 + timedelta(minutes=SR.MIN_DWELL_MIN + 1)
    dead = SR.aggregate(mkrow([[1300, 104], [1100, 100]]),        # gate fails
                        {"ts": T0.isoformat(), "spot": 1200.0, "arrow": up}, late)
    assert dead["dir"] is None                    # the up call has now ended...
    assert dead["ghost"]["dir"] == "up"           # ...so IT is the ghost
    assert dead["ghost"]["ended"] == late.isoformat()


def test_a_held_reversal_does_not_end_a_call():
    """Dwell restored the direction, so nothing completed and no ghost is cut."""
    prev = _prev("up", T0 - timedelta(minutes=3))
    a = SR.aggregate(mkrow([[1100, 60], [1300, 20]], up=2.0, dn=0.1), prev, T0)
    assert a["held_reversal"] is True and a["ghost"] is None


def test_held_stand_down_says_stand_down():
    """`direction` was reassigned before the branch test, so 'stand-down' was
    unreachable and every held stand-down printed 'reversal held'."""
    prev = _prev("up", T0 - timedelta(minutes=3))
    a = SR.aggregate(mkrow([[1300, 104], [1100, 100]]), prev, T0)
    assert "stand-down held" in a["fading"]
    assert "reversal" not in a["fading"]


# --- the caution flag ------------------------------------------------------
def test_caution_when_price_already_ran_that_way():
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    row["_ran_30m_sigma"] = 0.5                 # ran UP, arrow points UP
    a = SR.aggregate(row, None, T0)
    assert a["dir"] == "up" and a["caution"] and "already ran" in a["caution"]


def test_no_caution_when_price_ran_the_other_way():
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    row["_ran_30m_sigma"] = -0.5
    assert SR.aggregate(row, None, T0)["caution"] is None


def test_with_path_needs_real_history():
    """A 30-minute read must not be claimed off five minutes of rows."""
    rows = [mkrow([[1300, 9]], spot=1200 + i, ts=T0 - timedelta(minutes=10 - i))
            for i in range(6)]
    assert "_ran_30m_sigma" not in SR.with_path(rows[-1], rows)
    rows = [mkrow([[1300, 9]], spot=1200 + i, ts=T0 - timedelta(minutes=40 - i))
            for i in range(21)]
    assert "_ran_30m_sigma" in SR.with_path(rows[-1], rows)


# --- the wake gate ---------------------------------------------------------
def _read(ts, spot=1200.0, magnet=1300.0):
    return {"ts": ts.isoformat(), "spot": spot,
            "magnet_band": {"reported": magnet}, "arrow": {"dir": None}}


def test_first_scan_always_wakes():
    assert SR.should_wake(mkrow([[1300, 9]]), None, None, T0) == "first read"


def test_min_gap_holds_the_spam_down():
    prev = _read(T0 - timedelta(minutes=SR.MIN_GAP_MIN - 1))
    assert SR.should_wake(mkrow([[1300, 9]], spot=1400.0), None, prev, T0) is None


def test_price_ran_wakes():
    prev = _read(T0 - timedelta(minutes=20), spot=1200.0)
    row = mkrow([[1300, 9]], spot=1200 + SR.WAKE_PRICE_SIGMA * 100 + 1)
    assert SR.should_wake(row, None, prev, T0) == "price ran"


def test_magnet_move_wakes_on_sndk_scale():
    """The SPX threshold (0.5σ) sits ~4x above SNDK's real p90 and starves."""
    prev = _read(T0 - timedelta(minutes=20), magnet=1300.0)
    row = mkrow([[1300, 9]], magnet=1300 + SR.WAKE_MAGNET_SIGMA * 100 + 1)
    assert SR.should_wake(row, None, prev, T0) == "magnet moved"
    assert SR.WAKE_MAGNET_SIGMA < 0.5


def test_regime_change_wakes():
    prev = _read(T0 - timedelta(minutes=20))
    row = mkrow([[1300, 9]], regime="pinning")
    prev_row = mkrow([[1300, 9]], regime="trending")
    assert SR.should_wake(row, prev_row, prev, T0) == "regime changed"


def test_wall_cross_wakes_only_when_it_crosses_spot():
    prev = _read(T0 - timedelta(minutes=20))
    moved_far = mkrow([[1300, 9]], call_wall=1450.0)
    assert SR.should_wake(moved_far, mkrow([[1300, 9]], call_wall=1400.0),
                          prev, T0) is None       # both still above spot
    crossed = mkrow([[1300, 9]], call_wall=1100.0)
    assert SR.should_wake(crossed, mkrow([[1300, 9]], call_wall=1400.0),
                          prev, T0) == "call wall crossed"


def test_heartbeat_is_the_last_resort():
    prev = _read(T0 - timedelta(minutes=SR.HEARTBEAT_MIN + 1))
    assert SR.should_wake(mkrow([[1300, 9]]), None, prev, T0) == "heartbeat"
    near = _read(T0 - timedelta(minutes=SR.HEARTBEAT_MIN - 5))
    assert SR.should_wake(mkrow([[1300, 9]]), None, near, T0) is None


# --- frozen fields ---------------------------------------------------------
def test_frozen_fields_report_age():
    rows = [mkrow([[1300, 9]], magnet=1300.0,
                  ts=T0 - timedelta(minutes=90 - i * 2)) for i in range(45)]
    fz = {f["field"]: f for f in SR.frozen_fields(rows, T0)}
    assert fz["magnet"]["for_min"] >= SR.FROZEN_MIN
    assert fz["magnet"]["value"] == 1300.0


def test_recently_changed_field_is_not_frozen():
    # the change lands 12 min back — inside FROZEN_MIN, so it is still news
    rows = [mkrow([[1300, 9]], magnet=1300.0 if i > 38 else 1100.0,
                  ts=T0 - timedelta(minutes=90 - i * 2)) for i in range(45)]
    assert "magnet" not in {f["field"] for f in SR.frozen_fields(rows, T0)}


# --- the scene: constants may never reach the model ------------------------
def test_scene_carries_no_inadmissible_field():
    """dex_word alone is the identical bearish-reading string on 756/756 rows —
    a constant that reads like a signal manufactures conviction for free."""
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    row["dex_views"] = {"dex_word": "dealers_need_to_SELL_on_rallies",
                        "dex_flow_word": None}
    row["gex_views"]["pin_contested"] = False
    row["gex_views"]["pin_basis"] = "open_interest"
    blob = json.dumps(SR.build_scene(row, SR.magnet_band(row), [], [row], T0))
    for banned in SR.INADMISSIBLE:
        assert banned not in blob
    assert "SELL_on_rallies" not in blob


def test_scene_hands_over_the_path_the_old_design_omitted():
    rows = [mkrow([[1300, 60], [1100, 20]], spot=1200 + i * 3,
                  ts=T0 - timedelta(minutes=40 - i * 2)) for i in range(21)]
    row = SR.with_path(rows[-1], rows)
    sc = SR.build_scene(row, SR.magnet_band(row), [], rows, T0)
    assert sc["price"]["session_low"] is not None
    assert sc["price"]["session_high"] is not None
    assert sc["scale"]["typical_30min_move_sigma"] == 0.08


def test_scene_names_the_frozen_fields_as_uncitable():
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    sc = SR.build_scene(row, SR.magnet_band(row),
                        [{"field": "magnet", "value": 1300, "for_min": 120}],
                        [row], T0)
    assert sc["frozen_do_not_cite"] == ["magnet unchanged 120m"]


def test_scene_carries_no_verdict_at_all():
    """sr-2: the pre-baked arrow anchored the model and is GONE from the scene.
    The deterministic arrow still computes and still draws — it just never
    enters the model's evidence."""
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    a = SR.aggregate(row, None, T0)
    assert a["dir"] == "up"                     # the arrow itself still works
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    assert "arrow_already_decided" not in sc
    assert "arrow" not in json.dumps(sc)
    assert "infer a direction vector" in SR._DOCTRINE


# --- reply handling --------------------------------------------------------
def test_reading_validation_keeps_schema_and_drops_junk():
    """sr-2: vector + magnitude are the model's own call now — but only the
    schema's fields survive, a malformed vector is dropped never guessed, and
    the magnitude is clamped sane."""
    validate = SR._validate_reading
    ok = validate({"vector": "up", "magnitude_sigma": 0.12, "line": "x",
                   "breaks_if": "y", "cited": "z", "conviction": 0.9})
    assert ok["vector"] == "up" and ok["magnitude_sigma"] == 0.12
    assert "conviction" not in ok
    bad = validate({"vector": "sideways-ish", "magnitude_sigma": 9, "line": "x"})
    assert "vector" not in bad and "magnitude_sigma" not in bad
    none = validate({"vector": "none", "magnitude_sigma": 0.4, "line": "x"})
    assert none["vector"] == "none" and "magnitude_sigma" not in none
    huge = validate({"vector": "down", "magnitude_sigma": 99, "line": "x"})
    assert huge["magnitude_sigma"] == 3.0


def test_extract_json_finds_the_object_in_a_wrapped_reply():
    assert SR._extract_json('prose {"line":"a"} tail') == {"line": "a"}
    assert SR._extract_json("no json here") is None


# --- the reading's own age (it used to reset on every carry-forward) -------
def test_reading_age_measures_from_when_it_was_written(tmp_path, monkeypatch):
    """A sentence written at 10:00 and carried forward all day reported ~2m old
    on every quiet scan, so the card's >10m dim could never fire."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    # read_once's RTH gate reads the WALL clock, not `now`, so a replayed
    # timestamp would always bail as "market closed"
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    # belt and braces: no test may ever spend a real `claude -p` call
    monkeypatch.setattr(SR, "_ask", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("model call during test")))
    day = T0.date().isoformat()
    (tmp_path / "sndk_reversion").mkdir(parents=True)
    (tmp_path / "sndk_reads").mkdir(parents=True)
    diary = tmp_path / "sndk_reversion" / f"{day}.jsonl"
    reads = tmp_path / "sndk_reads" / f"{day}.jsonl"
    # a row the gate will find dull, so read_once takes the carry-forward path
    row = mkrow([[1300, 104], [1100, 100]], ts=T0)
    diary.write_text(json.dumps(row) + "\n")
    # THE TWO CLOCKS ARE THE WHOLE POINT: the last row landed 3 minutes ago (so
    # the wake gate stays asleep — no heartbeat, inside the min-gap), but the
    # SENTENCE it carries was written 47 minutes ago.
    written = T0 - timedelta(minutes=47)
    reads.write_text(json.dumps({
        "ts": (T0 - timedelta(minutes=3)).isoformat(), "era": SR.ERA, "spot": 1200.0,
        "arrow": {"dir": None, "layers": []},
        "reading": {"line": "old sentence"},
        "reading_ts": written.isoformat(), "wall_s": 1.0}) + "\n")
    SR.read_once(now=T0, force=False)
    last = SR._read_jsonl(reads)[-1]
    assert last["reading"]["line"] == "old sentence"      # carried forward
    assert last["reading_ts"] == written.isoformat()      # stamp travels with it
    assert last["reading_age_min"] == 47                  # NOT the scan gap
