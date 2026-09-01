"""sndk_read tests — the gates and the bans.

Every rule this module enforces exists because the four recorded sessions
(2026-07-28..31, 756 rows) measured a specific failure. These tests pin the
rule, not the implementation: the magnet must admit a tie, the model must
never be handed a constant, and a wake must be earned, not scheduled.
"""
import json
from pathlib import Path
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


# --- Lane A (the deterministic arrow) was removed with obs-3 ----------------
# Sixteen tests lived here: the magnet voter, the breadth veto, the dwell lock,
# the ghost afterimage, and the path caution. They died with aggregate() — see
# the tombstone in sndk_read.py for why the arrow itself was deleted.


def test_with_path_needs_real_history():
    """A 30-minute read must not be claimed off five minutes of rows."""
    rows = [mkrow([[1300, 9]], spot=1200 + i, ts=T0 - timedelta(minutes=10 - i))
            for i in range(6)]
    assert "_ran_30m_sigma" not in SR.with_path(rows[-1], rows)
    rows = [mkrow([[1300, 9]], spot=1200 + i, ts=T0 - timedelta(minutes=40 - i))
            for i in range(21)]
    assert "_ran_30m_sigma" in SR.with_path(rows[-1], rows)


# --- the wake gate ---------------------------------------------------------
def _read(ts, spot=1200.0, magnet=1300.0, **gate):
    """A READ row, which is what the gate actually receives as `prev`.

    Deliberately shaped like the real thing: a read row carries ts / spot /
    sigma / magnet_band and NOT call_wall, gamma_flip, atm_iv or gamma_sign.
    wk-1 adds the `gate` snapshot precisely because those are missing, so the
    fixture carries it too — a stub that quietly held the structural fields
    flat would hide the bug the snapshot exists to fix."""
    g = {"spot": spot, "magnet": magnet}
    g.update(gate)
    return {"ts": ts.isoformat(), "spot": spot,
            "magnet_band": {"reported": magnet}, "gate": g}


def _books(*rows):
    """Rows carrying distinct book stamps, so _books_since sees them apart."""
    out = []
    for i, r in enumerate(rows):
        r = dict(r)
        r["meta"] = dict(r.get("meta") or {},
                         book_asof=(T0 - timedelta(minutes=10 - i)).isoformat())
        out.append(r)
    return out


def test_first_scan_always_wakes():
    assert SR.should_wake(mkrow([[1300, 9]]), None, None, T0) == "first read"


def test_min_gap_holds_the_spam_down():
    prev = _read(T0 - timedelta(minutes=SR.MIN_GAP_MIN - 1))
    assert SR.should_wake(mkrow([[1300, 9]], spot=1400.0), None, prev, T0) is None


def test_price_ran_wakes():
    prev = _read(T0 - timedelta(minutes=20), spot=1200.0)
    row = mkrow([[1300, 9]], spot=1200 + SR.WAKE_SPOT_SIGMA * 100 + 1)
    assert SR.should_wake(row, None, prev, T0) == "price ran"
    quiet = mkrow([[1300, 9]], spot=1200 + SR.WAKE_SPOT_SIGMA * 100 - 1)
    assert SR.should_wake(quiet, None, prev, T0) is None


def test_a_discrete_flip_must_hold_for_two_books():
    """wk-1's anti-flicker rule, and the measurement behind it: on the recorded
    tape a changed gamma sign reverts one book later 37% of the time. One
    differing book is not evidence that anything moved."""
    prev = _read(T0 - timedelta(minutes=20), gamma_sign="negative")
    row = mkrow([[1300, 9]], gamma_sign="positive")
    one = _books(mkrow([[1300, 9]], gamma_sign="positive"))
    assert SR.should_wake(row, None, prev, T0, one) is None
    two = _books(mkrow([[1300, 9]], gamma_sign="positive"),
                 mkrow([[1300, 9]], gamma_sign="positive"))
    assert SR.should_wake(row, None, prev, T0, two) == "gamma sign flipped"
    # ...and a flip that flickers back does NOT count, however many books pass
    flicker = _books(mkrow([[1300, 9]], gamma_sign="positive"),
                     mkrow([[1300, 9]], gamma_sign="negative"))
    assert SR.should_wake(row, None, prev, T0, flicker) is None


def test_a_wall_relabelling_is_not_an_event_but_a_crossing_is():
    """45% of wall relocations happen in a step where spot moved under 0.05
    sigma, and ~30% undo themselves one book later — the wall is chasing price,
    so waking on the relabel is waking on our own arithmetic. The crossing is
    measured against the wall that was true when it last SPOKE."""
    prev = _read(T0 - timedelta(minutes=20), spot=1200.0, call_wall=1400.0)
    # the wall moves a long way; price has not crossed anything
    assert SR.should_wake(mkrow([[1300, 9]], call_wall=1250.0), None,
                          prev, T0) is None
    # price crosses the wall the last reading actually spoke about
    crossed = mkrow([[1300, 9]], spot=1450.0, call_wall=1400.0)
    assert SR.should_wake(crossed, None, prev, T0) == "call wall crossed"


def test_regime_word_alone_no_longer_wakes():
    """`regime` is a word derived from the gamma sign at spot, so waking on it
    woke twice for one fact. The sign itself still wakes, with confirmation."""
    prev = _read(T0 - timedelta(minutes=20), gamma_sign="negative")
    row = mkrow([[1300, 9]], regime="pinning", gamma_sign="negative")
    prev_row = mkrow([[1300, 9]], regime="trending", gamma_sign="negative")
    assert SR.should_wake(row, prev_row, prev, T0) is None


def test_the_gate_reads_a_snapshot_because_prev_is_not_a_diary_row():
    """THE wk-1 REGRESSION TEST. `read_once` hands the gate the last row that
    SPENT A CALL, and those live in state/sndk_reads/, whose schema carries no
    call_wall, gamma_flip, atm_iv or gamma_sign. A gate reading those straight
    off `prev` sees None every time and silently degenerates into a clock —
    which is what the first draft of wk-1 did, and what should_wake's own
    docstring records happening once before.

    So: a read-shaped row WITHOUT the snapshot must not fire a structural wake,
    and the same row WITH it must."""
    bare = {"ts": (T0 - timedelta(minutes=20)).isoformat(), "spot": 1200.0,
            "magnet_band": {"reported": 1300.0}}
    crossed = mkrow([[1300, 9]], spot=1450.0, call_wall=1400.0)
    assert SR.should_wake(crossed, None, bare, T0) == "price ran"   # not the wall
    stamped = dict(bare, gate=SR.state_for_next_wake(mkrow([[1300, 9]], spot=1200.0,
                                                  call_wall=1400.0)))
    assert SR.should_wake(crossed, None, stamped, T0) == "call wall crossed"
    # and the snapshot must actually carry the structural fields
    g = SR.state_for_next_wake(mkrow([[1300, 9]], spot=1200.0, call_wall=1400.0,
                            gamma_sign="negative"))
    assert g["call_wall"] == 1400.0 and g["gamma_sign"] == "negative"


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
    # sr-5: the point became a spread — a single "typical" number taught the
    # model a ceiling (33/33 emitted magnitudes inside 0.06-0.20 vs p95 0.46).
    # sr-8 moved the 30-minute spread out of the payload into the doctrine.
    # obs-1 then deleted it from the doctrine too, and the reason is the whole
    # phase: the spread existed to CALIBRATE A FORECAST, and there is no longer
    # a forecast to calibrate. A magnitude table in an observation contract is
    # an invitation to predict.
    assert "move_30min_sigma_distribution" not in sc["scale"]
    assert "0.46" not in SR._DOCTRINE and "1.71" not in SR._DOCTRINE


def test_scene_names_the_frozen_fields_as_uncitable():
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    sc = SR.build_scene(row, SR.magnet_band(row),
                        [{"field": "magnet", "value": 1300, "for_min": 120}],
                        [row], T0)
    assert sc["frozen_do_not_cite"] == ["magnet unchanged 120m"]


def test_scene_carries_no_verdict_at_all():
    """sr-2 stripped the pre-baked arrow from the scene because a verdict
    anchors the read; obs-3 then deleted the arrow at the source, so the
    exclusion is now structural — there is no aggregate() left to exclude."""
    assert not hasattr(SR, "aggregate")
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    assert "arrow_already_decided" not in sc
    assert "arrow" not in json.dumps(sc)
    # obs-1: the doctrine no longer asks for a vector at all. What it must
    # still refuse to do is hand over a verdict, so that is what is checked.
    assert "You are not forecasting" in SR._DOCTRINE
    assert "direction vector" not in SR._DOCTRINE


# --- reply handling --------------------------------------------------------
def _scene_for_obs():
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    return SR.build_scene(row, SR.magnet_band(row), [], [row], T0)


def _obs_scene():
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    return SR.build_scene(row, SR.magnet_band(row), [], [row], T0)


def test_the_model_reads_the_whole_board_and_picks_its_own_levels():
    """obs-2 GAVE THE REASONING BACK. obs-1b had Python nominate what was
    unusual and left the model narrating a shortlist; judging what matters on a
    board IS the reasoning, and the latency that appeared to justify moving it
    into Python turned out to be a global effortLevel setting. Nothing is
    pre-selected now. What is still checked is what can be checked without
    constraining thought."""
    sc = _obs_scene()
    strike = sc["magnet"]["top_strikes"][0]["strike"]
    ok = SR.check_reading_against_scene(
        {"quiet": False, "read": "One strike carries most of the board.",
         "points": [{"level": strike, "note": "heaviest strike"}]}, sc)
    assert ok["quiet"] is False
    assert ok["points"] == [{"level": strike, "note": "heaviest strike"}]
    assert ok["read"].startswith("One strike")


def test_a_level_that_is_not_on_the_board_is_deleted():
    """The one invention that matters most: a price nothing measured sends a
    reader somewhere the board never spoke about."""
    sc = _obs_scene()
    out = SR.check_reading_against_scene(
        {"quiet": False, "read": "Watch this one.",
         "points": [{"level": 4242.0, "note": "invented"}]}, sc)
    assert not out.get("points")
    assert any(d.startswith("level_not_on_the_board")
               for d in out["dropped_observations"])


def test_a_number_in_the_prose_must_be_on_the_board():
    """obs-2 checks prose against every number in the SCENE rather than a
    shortlist, because the model now reads all of it. A human rounding is not
    invention — the doctrine says talk like a person."""
    sc = _obs_scene()
    strike = sc["magnet"]["top_strikes"][0]["strike"]
    ok = SR.check_reading_against_scene(
        {"quiet": False, "read": f"The heaviest strike is {strike:g}."}, sc)
    assert "read" in ok
    drift = SR.check_reading_against_scene(
        {"quiet": False, "read": "The heaviest strike is 4242."}, sc)
    assert "read" not in drift
    assert any(d.startswith("read_number_not_on_the_board")
               for d in drift["dropped_observations"])


def test_forecast_and_causal_words_delete_the_prose():
    """The causal ban is correctness, not caution: a wall relabels to follow
    price with probability 1.000 on a crossing, so a causal verb about one is
    false by measurement.

    POSITIONAL words are not banned. "no call wall to the upside" means nothing
    above price; banning it deleted an otherwise good live sentence over the
    second word."""
    sc = _obs_scene()
    for prose in ("Price will rally from here.",
                  "The board is heavy because dealers are defending it.",
                  "1500 is acting as resistance."):
        out = SR.check_reading_against_scene({"quiet": False, "read": prose}, sc)
        assert "read" not in out, prose
        assert any(d.startswith("read_banned:")
                   for d in out["dropped_observations"]), prose
    fine = SR.check_reading_against_scene(
        {"quiet": False,
         "read": "No call wall to the upside, and the heavier cluster sits lower down."}, sc)
    assert "read" in fine


def test_chosen_quiet_and_forced_quiet_are_counted_apart():
    """The distinction IS the audit: a model that looked and found nothing is
    doing its job, one whose every claim was deleted is not, and a single flag
    cannot tell you which happened."""
    sc = _obs_scene()
    chosen = SR.check_reading_against_scene({"quiet": True}, sc)
    assert chosen["abstain"] == "chosen" and "dropped_observations" not in chosen
    forced = SR.check_reading_against_scene(
        {"quiet": False, "read": "Price will rally."}, sc)
    assert forced["quiet"] is True and forced["abstain"] == "forced"


def test_the_shadow_guard_rides_the_row_and_never_rejects():
    """The standing house rule for a new guard: run it in the shadow first, so
    the rate is known before it is allowed to reject anything."""
    assert SR.LEXICON_ENFORCE is False
    sc = _obs_scene()
    r = SR.check_reading_against_scene(
        {"quiet": False,
         "read": "Dealers are buying the heaviest strike right now."}, sc)
    assert r["stale_language_flags"] == ["are buying", "right now"]
    assert "read" in r                    # shadow only — it rejects nothing
    clean = SR.check_reading_against_scene(
        {"quiet": False, "read": "One strike carries most of the board."}, sc)
    assert "stale_language_flags" not in clean


def test_context_states_facts_and_never_verdicts():
    """obs-2's division of labour: Python supplies only what the model cannot
    see from one snapshot — a rank against closed sessions, and what moved since
    the last book. It never says whether any of it matters."""
    rows = [mkrow([[1300, 60], [1100, 20]], gamma_sign="negative",
                  ts=T0 - timedelta(minutes=4),
                  meta={"book_asof": (T0 - timedelta(minutes=4)).isoformat()}),
            mkrow([[1300, 60], [1100, 20]], gamma_sign="positive", ts=T0,
                  meta={"book_asof": T0.isoformat()})]
    ctx = SR.session_context({}, rows, T0) or {}
    ch = ctx.get("changed_since_last_book") or {}
    assert ch.get("gamma_sign") == {"was": "negative", "now": "positive"}
    # THE VERDICT HALF, ON A SCENE THAT CAN ACTUALLY PRODUCE A RANK. Passing an
    # empty scene made `vs_prior_sessions` absent, so the loop below ran zero
    # times and the half of this test its name promises was dead code.
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    ranks = (SR.session_context(sc, rows, T0) or {}).get("vs_prior_sessions")
    if ranks:                       # absent under PCTL_MIN_SESSIONS, which is fine
        for v in ranks.values():
            assert not SR.banned_words(v), v
            assert "unusual" not in v and "extreme" not in v
            assert "of the" in v and "sessions" in v   # a rank states its n


def test_a_transition_out_of_not_measured_is_not_a_change():
    """The first live obs-2 scene reported `gamma_sign: unknown -> positive`.
    A book that had not been read yet is not a flip, and telling the model it is
    invites a sentence about something that never happened."""
    rows = [mkrow([[1300, 60], [1100, 20]], gamma_sign="unknown",
                  ts=T0 - timedelta(minutes=4),
                  meta={"book_asof": (T0 - timedelta(minutes=4)).isoformat()}),
            mkrow([[1300, 60], [1100, 20]], gamma_sign="positive", ts=T0,
                  meta={"book_asof": T0.isoformat()})]
    ctx = SR.session_context({}, rows, T0) or {}
    assert "gamma_sign" not in (ctx.get("changed_since_last_book") or {})


# --- obs-3: the since-last-read frame ---------------------------------------
def _last_call(minutes_ago=47, **gate_kw):
    r = mkrow([[1300, 60], [1100, 20]], **gate_kw)
    return {"ts": (T0 - timedelta(minutes=minutes_ago)).isoformat(),
            "wall_s": 9.9, "gate": SR.state_for_next_wake(r)}


def test_every_wake_reason_translates_and_every_translation_is_speakable():
    """The RAW wake reason is never handed to the model — "pin moved" baited it
    into echoing "pin", a banned word, and the whole read died in testing. So
    the table must cover every reason the gate can produce, and every value
    must pass the lexicon."""
    reasons = ("first read", "price ran", "pin moved", "gamma sign flipped",
               "crossed flip", "call wall crossed", "put wall crossed",
               "iv moved", "flip moved", "heartbeat")
    for r in reasons:
        assert r in SR._WAKE_WORDS, r
    for phrase in SR._WAKE_WORDS.values():
        assert SR.banned_words(phrase) == [], phrase


def test_a_wall_crossed_wake_yields_a_frozen_crossing():
    """Pins the frame's straddle test to the wake gate's: if the gate said a
    wall was crossed, the frame must name the frozen level it was measured
    against — otherwise the wake reason and the block contradict each other."""
    lc = _last_call(spot=1490.0, call_wall=1500.0)
    row = mkrow([[1300, 60], [1100, 20]], spot=1520.0)
    fr = SR.frame_since_last_read(row, [row], lc, "call wall crossed", False, T0)
    assert fr["crossed_since_then"] == [
        {"level": 1500.0, "was_labelled_then": "nearest_call_wall",
         "price_went": "up"}]
    assert fr["spot_change_dollars"] == 30.0
    assert fr["why_this_read"] == "price crossed a level from the last read"


def test_example_a_survives_only_because_the_frame_ships():
    """The user's crossing sentence, proven both ways through the REAL gates.
    The frozen level is not a price on today's board — the wall relabelled the
    moment price crossed it — so without the frame the point dies as
    level_not_on_the_board and the row logs a FALSE forced abstain."""
    lc = _last_call(spot=1490.0, call_wall=1497.0)     # off today's strike grid
    row = mkrow([[1300, 60], [1100, 20]], spot=1520.0)
    rows = [mkrow([[1300, 60], [1100, 20]], spot=1490.0,
                  ts=T0 - timedelta(minutes=30)), row]
    fr = SR.frame_since_last_read(row, rows, lc, "call wall crossed", False, T0)
    sc = SR.build_scene(row, SR.magnet_band(row), [], rows, T0,
                        since_last_read=fr)
    reply = {"quiet": False,
             "read": "Price moved up just through 1497 since the last read, "
                     "the call wall as it stood then.",
             "points": [{"level": 1497.0,
                         "note": "crossed level, the wall as it stood at the last read"}]}
    ok = SR.check_reading_against_scene(reply, sc)
    assert "read" in ok and len(ok["points"]) == 1
    bare = SR.build_scene(row, SR.magnet_band(row), [], rows, T0)
    dead = SR.check_reading_against_scene(reply, bare)
    assert dead["quiet"] is True and dead["abstain"] == "forced"
    assert any(d.startswith("level_not_on_the_board")
               for d in dead["dropped_observations"])


def test_example_b_survives_only_because_the_frame_ships():
    """The honest nothing-changed sentence: the since-window range is not a
    number on today's board, so without the block the read is deleted as
    invented — which is precisely the failure that made this block load-bearing
    rather than garnish."""
    lc = _last_call(minutes_ago=61, spot=1543.2)
    rows = [mkrow([[1300, 60], [1100, 20]], spot=v,
                  ts=T0 - timedelta(minutes=m))
            for v, m in ((1543.2, 61), (1538.8, 40), (1546.1, 20), (1541.7, 0))]
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "heartbeat", False, T0)
    assert fr["held_between_since_last_read"] == {"low": 1538.8, "high": 1546.1}
    assert fr["nothing_crossed_since_then"] is True
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0,
                        since_last_read=fr)
    reply = {"quiet": True,
             "read": "Price has held between 1538.8 and 1546.1 since the "
                     f"{fr['last_read_at']} read; nothing on the board moved."}
    ok = SR.check_reading_against_scene(reply, sc)
    assert ok["read"].startswith("Price has held") and ok["abstain"] == "chosen"
    bare = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    dead = SR.check_reading_against_scene(reply, bare)
    assert "read" not in dead


def test_first_read_has_no_frame_and_says_so():
    row = mkrow([[1300, 60], [1100, 20]])
    fr = SR.frame_since_last_read(row, [row], None, "first read", False, T0)
    assert fr == {"first_read_of_session": True}


def test_the_session_range_anchor_is_gone_and_stays_gone():
    """The whole-day range anchor shipped and was measured LYING: with two
    clocks in the frame the model fused them — "held between X and Y since
    <the session anchor>" — and 8 of 11 live quiet reads paired the since-last-
    read range with the wrong timestamp. The frame now carries exactly one
    range (`held_between_since_last_read`) and exactly one clock it may pair
    with (`last_read_at`). This test memorialises the deletion."""
    rows = [mkrow([[1300, 60], [1100, 20]], spot=v,
                  ts=T0 - timedelta(minutes=m))
            for v, m in ((1500.0, 90), (1530.0, 75), (1510.0, 50), (1512.0, 0))]
    lc = _last_call(minutes_ago=50, spot=1510.0)
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "heartbeat", False, T0)
    assert "session_range_unbroken_since" not in fr
    assert "range_since" not in fr
    assert set(k for k in fr if "range" in k or "held" in k) <= {
        "held_between_since_last_read"}


def test_build_scene_without_the_kwarg_is_unchanged():
    row = mkrow([[1300, 60], [1100, 20]])
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    assert "since_last_read" not in (sc.get("context") or {})


# --- the night-crew regressions (obs-3 QA pass, 2026-09-01) -----------------
def _scene_for(rows, fr=None):
    return SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0,
                          since_last_read=fr)


def test_comma_numbers_in_scene_strings_are_quotable():
    """"1,893 distinct books" in the scene's own prose: float("1,893") raised,
    the walker skipped it, and a verbatim quote of the board's own string was
    deleted as invented."""
    row = mkrow([[1300, 60], [1100, 20]])
    row["breadth_note"] = "1,893 distinct books traded"
    sc = _scene_for([row])
    sc["context"] = {"note": "1,893 distinct books traded"}
    assert 1893.0 in SR.numbers_on_the_board(sc)
    out = SR.check_reading_against_scene(
        {"quiet": True, "read": "About 1893 distinct books traded; "
                                "nothing else moved."}, sc)
    assert out.get("read", "").startswith("About 1893")


def test_whole_numbers_get_a_dollar_of_rounding_slack():
    """"about 64" against a measured 63.52 is rounded speech, not invention —
    the old half-unit integer tolerance deleted true sentences."""
    row = mkrow([[1300, 60], [1100, 20]])
    sc = _scene_for([row])
    sc["context"] = {"moved_pct": 63.52}
    out = SR.check_reading_against_scene(
        {"quiet": True, "read": "The measure sits near 64 on the day."}, sc)
    assert "read" in out and not out.get("dropped_observations")


def test_banned_inflections_do_not_walk_through_the_gate():
    """Live QA: "pinned", "pulled", "leaning" escaped while their stems were
    banned."""
    row = mkrow([[1300, 60], [1100, 20]])
    sc = _scene_for([row])
    for verb in ("pinned", "pushed", "pulled", "rallied", "leaning"):
        out = SR.check_reading_against_scene(
            {"quiet": False, "read": f"Price got {verb} into the close.",
             "points": []}, sc)
        assert "read" not in out, verb
        assert any("read_banned" in d
                   for d in out.get("dropped_observations", [])), verb


def test_lexicon_enforce_is_wired_to_a_real_drop(monkeypatch):
    """The sr-7 switch was wired to NOTHING — flipping it on changed no
    behaviour. Enforcement must drop the read and say so in dropped."""
    row = mkrow([[1300, 60], [1100, 20]])
    sc = _scene_for([row])
    slipping = {"quiet": True,
                "read": "The magnet is building right now under price."}
    shadow = SR.check_reading_against_scene(dict(slipping), sc)
    assert shadow.get("stale_language_flags")          # recorded…
    assert "read" in shadow                            # …but not enforced
    monkeypatch.setattr(SR, "LEXICON_ENFORCE", True)
    hard = SR.check_reading_against_scene(dict(slipping), sc)
    assert "read" not in hard
    assert hard["abstain"] == "forced"
    assert any(d.startswith("read_stale_tense:")
               for d in hard["dropped_observations"])


def test_unknown_gamma_sign_never_wakes_the_flip():
    """"unknown" held for two books is agreement about ignorance, not a flip —
    and a flip INTO unknown is a data gap, not an event."""
    assert SR._known_sign("unknown") is None
    assert SR._known_sign("unmeasured") is None
    assert SR._known_sign("") is None
    assert SR._known_sign(None) is None
    assert SR._known_sign("negative") == "negative"


def test_crossed_flip_wake_suppresses_the_nothing_crossed_flag():
    """The flip is not in the frozen-level loop, so on a "crossed flip" wake
    the loop finds nothing and stamped "nothing_crossed_since_then" onto a row
    whose own wake reason says something crossed."""
    lc = _last_call(minutes_ago=30, spot=1543.2)
    rows = [mkrow([[1300, 60], [1100, 20]], spot=v,
                  ts=T0 - timedelta(minutes=m))
            for v, m in ((1543.2, 30), (1544.0, 0))]
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "crossed flip",
                                  False, T0)
    assert "nothing_crossed_since_then" not in fr
    quiet = SR.frame_since_last_read(rows[-1], rows, lc, "heartbeat",
                                     False, T0)
    assert quiet.get("nothing_crossed_since_then") is True


def test_an_empty_gate_asserts_nothing_about_crossings():
    """tested == 0: a last call whose gate carries no levels must not claim
    "nothing crossed" — it measured nothing."""
    lc = {"ts": (T0 - timedelta(minutes=30)).isoformat(), "wall_s": 9.9,
          "gate": {"spot": 1543.2}}
    rows = [mkrow([[1300, 60], [1100, 20]], spot=v,
                  ts=T0 - timedelta(minutes=m))
            for v, m in ((1543.2, 30), (1544.0, 0))]
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "heartbeat", False, T0)
    assert "nothing_crossed_since_then" not in fr


def test_price_parked_on_a_level_then_leaving_counts_as_a_crossing():
    """spot_then AT the frozen level: the strict sign product is zero, and the
    departure was invisible to the old test."""
    lc = _last_call(minutes_ago=30, spot=1400.0)   # parked ON the call wall
    rows = [mkrow([[1300, 60], [1100, 20]], spot=v,
                  ts=T0 - timedelta(minutes=m))
            for v, m in ((1400.0, 30), (1409.0, 0))]
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "heartbeat", False, T0)
    crossed = fr.get("crossed_since_then") or []
    assert any(c["level"] == 1400.0 and c["price_went"] == "up"
               for c in crossed)
