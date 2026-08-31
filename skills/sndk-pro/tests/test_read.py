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
def _read(ts, spot=1200.0, magnet=1300.0, **gate):
    """A READ row, which is what the gate actually receives as `prev`.

    Deliberately shaped like the real thing: a read row carries ts / spot /
    sigma / arrow / magnet_band and NOT call_wall, gamma_flip, atm_iv or
    gamma_sign. wk-1 adds the `gate` snapshot precisely because those are
    missing, so the fixture carries it too — a stub that quietly held the
    structural fields flat would hide the bug the snapshot exists to fix."""
    g = {"spot": spot, "magnet": magnet}
    g.update(gate)
    return {"ts": ts.isoformat(), "spot": spot,
            "magnet_band": {"reported": magnet}, "arrow": {"dir": None},
            "gate": g}


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
            "magnet_band": {"reported": 1300.0}, "arrow": {"dir": None}}
    crossed = mkrow([[1300, 9]], spot=1450.0, call_wall=1400.0)
    assert SR.should_wake(crossed, None, bare, T0) == "price ran"   # not the wall
    stamped = dict(bare, gate=SR.gate_state(mkrow([[1300, 9]], spot=1200.0,
                                                  call_wall=1400.0)))
    assert SR.should_wake(crossed, None, stamped, T0) == "call wall crossed"
    # and the snapshot must actually carry the structural fields
    g = SR.gate_state(mkrow([[1300, 9]], spot=1200.0, call_wall=1400.0,
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
    """sr-2: the pre-baked arrow anchored the model and is GONE from the scene.
    The deterministic arrow still computes and still draws — it just never
    enters the model's evidence."""
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    a = SR.aggregate(row, None, T0)
    assert a["dir"] == "up"                     # the arrow itself still works
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


def test_an_observation_survives_only_when_its_pointer_resolves():
    """GATE 2, the contract's whole basis. The model may say anything it likes;
    what reaches the row is what could be checked against the scene."""
    sc = _scene_for_obs()
    top = sc["magnet"]["top_strikes"][0]["share_of_book_gamma_pp"]
    good = SR._validate_observation({"quiet": False, "notable": [
        {"what": "The heaviest strike holds most of the board's gamma.",
         "paths": ["/magnet/top_strikes/0/share_of_book_gamma_pp"],
         "values": [top]}]}, sc)
    assert good["quiet"] is False and len(good["notable"]) == 1
    # a pointer that goes nowhere
    bad = SR._validate_observation({"quiet": False, "notable": [
        {"what": "x", "paths": ["/magnet/not_a_field"], "values": [1]}]}, sc)
    assert bad["quiet"] is True and bad["abstain"] == "forced"
    assert "pointer_did_not_resolve" in bad["dropped_observations"]
    # a pointer that resolves to a DIFFERENT number than the one claimed
    wrong = SR._validate_observation({"quiet": False, "notable": [
        {"what": "x", "paths": ["/magnet/top_strikes/0/share_of_book_gamma_pp"],
         "values": [top + 5]}]}, sc)
    assert wrong["quiet"] is True
    assert "value_did_not_match" in wrong["dropped_observations"]


def test_forecast_and_causal_words_delete_the_observation():
    """GATE 4. The causal ban is correctness, not caution: walls relabel to
    follow price 100% of the time on a crossing, so a causal verb about one is
    false by measurement."""
    sc = _scene_for_obs()
    top = sc["magnet"]["top_strikes"][0]["share_of_book_gamma_pp"]
    for prose in ("Price will rally toward the heaviest strike.",
                  "The board is heavy here because dealers are defending it.",
                  "The call wall is the level to watch."):
        out = SR._validate_observation({"quiet": False, "notable": [
            {"what": prose,
             "paths": ["/magnet/top_strikes/0/share_of_book_gamma_pp"],
             "values": [top]}]}, sc)
        assert out["quiet"] is True, prose
        assert any(d.startswith("banned:") for d in out["dropped_observations"])


def test_staleness_bites_a_change_claim_and_spares_an_extremity_claim():
    """GATE 3, narrowed after it deleted a good observation on the first real
    scene it met. `frozen_do_not_cite` probes the DIARY's coarse scalar while
    the model cites leaves underneath it, and sr-8 measured those disagreeing:
    where the list said "magnet unchanged", the magnet's own gamma share had
    changed on 309 of 315 occasions. So the rule bites only on a claim that
    something MOVED. "This level has stood all day and is the heaviest in 24
    sessions" is not stale news, it is the point."""
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    frozen = [{"field": "magnet", "value": 1300.0, "for_min": 387}]
    sc = SR.build_scene(row, SR.magnet_band(row), frozen, [row], T0)
    assert sc["frozen_do_not_cite"] == ["magnet unchanged 387m"]
    top = sc["magnet"]["top_strikes"][0]["share_of_book_gamma_pp"]
    ob = {"what": "The heaviest strike carries most of the board.",
          "paths": ["/magnet/top_strikes/0/share_of_book_gamma_pp"],
          "values": [top]}
    keep = SR._validate_observation(
        {"quiet": False, "notable": [dict(ob, novelty="extreme_vs_own_history")]}, sc)
    assert keep["quiet"] is False and len(keep["notable"]) == 1
    drop = SR._validate_observation(
        {"quiet": False, "notable": [dict(ob, novelty="changed_this_scan")]}, sc)
    assert drop["quiet"] is True
    assert "change_claimed_on_a_frozen_field" in drop["dropped_observations"]


def test_the_human_sentence_goes_when_its_evidence_does():
    """`say` is prose ABOUT the observations. If they were all deleted it is
    describing evidence the reader cannot see — the exact failure the contract
    exists to prevent — so it leaves with them."""
    sc = _scene_for_obs()
    out = SR._validate_observation(
        {"quiet": False, "say": "Something is happening on the board.",
         "notable": [{"what": "x", "paths": ["/nope"], "values": [1]}]}, sc)
    assert "say" not in out and out["quiet"] is True


def test_chosen_quiet_and_forced_quiet_are_counted_apart():
    """GATE 5, and the distinction IS the audit: a model that looked and found
    nothing is doing its job, a model whose every claim was deleted is not, and
    a single `quiet` flag cannot tell you which happened."""
    sc = _scene_for_obs()
    chosen = SR._validate_observation(
        {"quiet": True, "quiet_because": "Everything sits mid-range.",
         "notable": []}, sc)
    assert chosen["abstain"] == "chosen" and "dropped_observations" not in chosen
    forced = SR._validate_observation({"quiet": False, "notable": [
        {"what": "x", "paths": ["/nope"], "values": [1]}]}, sc)
    assert forced["abstain"] == "forced"


def test_the_human_sentence_may_not_carry_a_number_the_gates_did_not_verify():
    """`say` is the part a person reads, so it is the part most worth drifting.
    Every number in it must appear in a surviving observation's values."""
    sc = _scene_for_obs()
    top = sc["magnet"]["top_strikes"][0]["share_of_book_gamma_pp"]
    ok = SR._validate_observation({"quiet": False,
        "say": f"The heaviest strike carries {top} percent of the board.",
        "notable": [{"what": "heaviest strike",
                     "paths": ["/magnet/top_strikes/0/share_of_book_gamma_pp"],
                     "values": [top]}]}, sc)
    assert "say" in ok
    drift = SR._validate_observation({"quiet": False,
        "say": "The heaviest strike carries 87.4 percent of the board.",
        "notable": [{"what": "heaviest strike",
                     "paths": ["/magnet/top_strikes/0/share_of_book_gamma_pp"],
                     "values": [top]}]}, sc)
    assert "say" not in drift
    assert "say_number_not_in_evidence" in drift["dropped_observations"]


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


# --- sr-6: the pulse check ---------------------------------------------------
def test_stale_book_never_wakes_the_model(tmp_path, monkeypatch):
    """A dead scanner used to read as a quiet tape, and the heartbeat would
    then spend a call narrating the same frozen board every 45 minutes."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    monkeypatch.setattr(SR, "_ask", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("model call on a stale book")))
    day = T0.date().isoformat()
    (tmp_path / "sndk_reversion").mkdir(parents=True)
    (tmp_path / "sndk_reads").mkdir(parents=True)
    diary = tmp_path / "sndk_reversion" / f"{day}.jsonl"
    reads = tmp_path / "sndk_reads" / f"{day}.jsonl"
    # newest row is 20 minutes old — the scanner has missed ~9 ticks
    row = mkrow([[1300, 104], [1100, 100]], ts=T0 - timedelta(minutes=20))
    diary.write_text(json.dumps(row) + "\n")
    # the last CALL is 50 minutes back, so the heartbeat wake WOULD fire
    reads.write_text(json.dumps({
        "ts": (T0 - timedelta(minutes=50)).isoformat(), "era": SR.ERA,
        "spot": 1200.0, "arrow": {"dir": None, "layers": []},
        "reading": {"line": "old"}, "reading_ts":
        (T0 - timedelta(minutes=50)).isoformat(), "wall_s": 1.0}) + "\n")
    SR.read_once(now=T0, force=False)
    last = SR._read_jsonl(reads)[-1]
    assert last["wake"] == "stale_book"          # an outage never pools with quiet
    assert last["book_age_min"] == 20
    assert last["wall_s"] is None                # and never cost a call
    assert last["reading"] == {"line": "old"}    # sentence carried, not re-made


_EMPTY_BAND = {"top": [], "gap_pp": None, "tie": None, "lo": None, "hi": None,
               "reported": None, "in_window": None}


def test_fresh_book_stamps_its_age_and_scene_carries_it():
    """sr-7 moved this age off clock and onto the measurement it describes:
    clock is the session calendar, data_sources holds the three disagreeing
    clocks. It is also measured off meta.book_asof now — the feed re-serves a
    cached book on about half of all scans, so a scan-clock age read 0-1 minute
    on a book that was 2-4 minutes old."""
    row = mkrow([[1300, 104], [1100, 100]], ts=T0 - timedelta(minutes=2))
    row["meta"] = {"book_asof": (T0 - timedelta(minutes=5)).isoformat()}
    sc = SR.build_scene(row, _EMPTY_BAND, [], [], T0)
    assert sc["data_sources"]["options_book"]["age_min"] == 5   # the BOOK clock
    assert "book_age_min" not in sc["clock"]

    # no book stamp (early fixtures, pre-row_v4 rows) → the scan clock stands
    # in, and says so: a scan is never older than the book it carries, so the
    # fallback can only understate
    bare = SR.build_scene(mkrow([[1300, 104], [1100, 100]],
                                ts=T0 - timedelta(minutes=2)),
                          _EMPTY_BAND, [], [], T0)["data_sources"]["options_book"]
    assert bare["age_min"] == 2
    assert bare["measured_at_is_fallback_scan_clock"] is True


# --- sr-7: ONE age function, so the two gates cannot disagree ----------------
@pytest.mark.parametrize("age_s, age_min", [(365.2, 6.1), (400.0, 6.7),
                                            (419.9, 7.0)])
def test_the_wake_gate_and_the_payload_gate_round_the_same_book(
        age_s, age_min, tmp_path, monkeypatch):
    """sr-7 shipped two age functions for an hour: read_once floored
    (`int(secs // 60)`) while build_scene rounded, so a book aged in
    [6.05, 7.00) minutes cleared the wake gate and failed the payload gate —
    the model would have been asked for a direction and a magnitude in sigma
    with no sigma ruler, no price and no book in the scene. It happened once in
    3,409 recorded August scans (2026-08-24 12:18:15, 365.2s), on a quiet scan
    that spent no call. A comment two lines from the constant claimed the two
    gates "can never disagree"; the only way to make that true is for there to
    be one function, so this walks the whole band that used to split them."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    monkeypatch.setattr(SR, "_ask", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("model call on a book the payload gate would empty")))
    day = T0.date().isoformat()
    (tmp_path / "sndk_reversion").mkdir(parents=True)
    (tmp_path / "sndk_reads").mkdir(parents=True)
    diary = tmp_path / "sndk_reversion" / f"{day}.jsonl"
    reads = tmp_path / "sndk_reads" / f"{day}.jsonl"
    # the scan clock is 2 minutes old and healthy-looking; the BOOK it carries
    # is the one sitting in the band — the exact cached-feed shape that made
    # the two roundings reachable in the first place
    row = mkrow([[1300, 104], [1100, 100]], ts=T0 - timedelta(minutes=2))
    row["meta"] = {"book_asof": (T0 - timedelta(seconds=age_s)).isoformat()}
    diary.write_text(json.dumps(row) + "\n")

    SR.read_once(now=T0, force=False)
    last = SR._read_jsonl(reads)[-1]
    assert last["book_age_min"] == age_min       # rounded, not floored
    assert last["scan_age_min"] == 2.0
    assert last["wake"] == "stale_book"          # ...and the wake gate agrees
    assert last["wall_s"] is None

    # the same book, through the payload's own gate at the same instant
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    dropped = sc["freshness_rules"]["blocks_dropped_this_scan"]
    assert {d["age_min"] for d in dropped} == {age_min}
    # sr-8: `instrument` went to the doctrine, which opens on it
    assert list(sc) == ["data_sources", "clock", "freshness_rules"]


def test_the_two_gates_agree_a_book_just_inside_the_ceiling_is_fine():
    """The other edge of the same band: 6.0 minutes exactly is not stale, and
    a gate that refused it would be refusing the ordinary cached scan."""
    row = mkrow([[1300, 104], [1100, 100]], ts=T0 - timedelta(minutes=2))
    row["meta"] = {"book_asof": (T0 - timedelta(seconds=362.9)).isoformat()}
    assert SR._age_min(SR._book_asof(row), T0) == 6.0
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    assert sc["freshness_rules"]["blocks_dropped_this_scan"] == []
    assert "magnet" in sc and "price" in sc


# --- sr-7: the present-tense lexicon, running in the shadow ------------------
def test_present_tense_narration_of_a_standing_field_is_flagged():
    """The scene labels every OI-derived block "as of last night's close", and
    a label is not a control — the doctrine already told the model the book was
    N minutes stale and no reading ever discounted anything for it. A hit needs
    BOTH halves: a noun that names a standing surface AND a verb that puts it
    in the present. Either alone is ordinary prose.

    obs-1 retargeted this at the fields that now exist. Pointed at `line`,
    `breaks_if` and `cited` it read the empty string on every call and reported
    a clean sheet forever, which is worse than no guard because the zero looks
    like evidence."""
    flags = SR._stale_language_flags(
        {"say": "dealers are buying the 1500 wall right now"})
    assert flags == ["are buying", "right now"]          # sorted, deduped
    # a standing noun with no live verb — the sentence the scene wants
    assert SR._stale_language_flags(
        {"say": "the 1240 call wall sits 0.4 sigma above price"}) == []
    # a live verb about something that really is live
    assert SR._stale_language_flags(
        {"say": "price is building a base right now"}) == []
    # every field the model writes is read, not just the headline
    assert SR._stale_language_flags(
        {"notable": [{"what": "gamma is piling up at 1500"}]}) == ["is piling"]
    assert SR._stale_language_flags(
        {"quiet_because": "the wall is building"}) == ["is building"]


def _retired_lexicon_shadow_test():
    """The standing house rule for a new guard: run it in the shadow first. The
    flag rides the read row so the rate is known before LEXICON_ENFORCE is
    allowed to reject anything — a guard that has never fired is not evidence
    that nothing is wrong, and one that fires on 40% of readings is not a guard
    either. Measured on the recorded history: 17 of 391 distinct sentences."""
    assert SR.LEXICON_ENFORCE is False
    r = SR._validate_reading({"vector": "up", "magnitude_sigma": 0.12,
                              "line": "dealers are buying the 1500 wall right now",
                              "breaks_if": "a close back under 1240",
                              "cited": "walls.call[0]"})
    assert r["stale_language_flags"] == ["are buying", "right now"]
    assert r["line"].startswith("dealers are buying")   # prose survives...
    assert r["breaks_if"] and r["cited"]                # ...and so does the rest
    assert r["vector"] == "up" and r["magnitude_sigma"] == 0.12
    # a clean reading carries no key at all — absence is the scene's own word
    clean = SR._validate_reading({"vector": "none", "line": "price sits mid-band"})
    assert "stale_language_flags" not in clean
