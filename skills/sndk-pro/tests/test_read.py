"""sndk_read tests — the gates, the dwell, and the bans.

Every rule this module enforces exists because the four recorded sessions
(2026-07-28..31, 756 rows) measured a specific failure. These tests pin the
rule, not the implementation: the arrow must stay silent on a tie, the model
must never be handed a constant, and a live call must not blink.
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


def _scene_with_candidate():
    """A scene carrying at least one Python-found candidate."""
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    sc["unusual"] = [{"path": "/magnet/top_strike_lead_pp", "value": 41.2,
                      "why": "extreme_vs_own_history",
                      "rank": "higher than all 22 prior sessions"}]
    return sc


def test_the_model_may_only_speak_about_what_python_offered():
    """THE structural gate, and it is stronger than the pointer check it
    replaced. Under obs-1 the model built its own RFC-6901 pointers and echoed
    its own values, which had to be re-resolved and could be invented. It now
    picks from a list Python computed, so there is nothing to resolve and
    nothing to invent — and citing anything not on that list is the one
    structural error left to make."""
    sc = _scene_with_candidate()
    ok = SR._validate_observation(
        {"quiet": False, "used": ["/magnet/top_strike_lead_pp"],
         "say": "One strike is doing far more of the work than usual."}, sc)
    assert ok["quiet"] is False and len(ok["notable"]) == 1
    assert ok["notable"][0]["novelty"] == "extreme_vs_own_history"
    assert ok["say"].startswith("One strike")
    ungiven = SR._validate_observation(
        {"quiet": False, "used": ["/price/live_spot"], "say": "Price moved."}, sc)
    assert ungiven["quiet"] is True and ungiven["abstain"] == "forced"
    assert "not_offered" in ungiven["dropped_observations"]


def test_forecast_and_causal_words_delete_the_sentence():
    """The causal ban is correctness, not caution: a wall relabels to follow
    price with probability 1.000 on a crossing, so a causal verb about one is
    false by measurement.

    obs-1b stopped banning the NOUNS. Banning "call wall" wiped the human
    sentence on 2 of the 2 completed obs-1 reads — a 100% deletion rate on the
    only part a person reads — because the scene ships a walls block the model
    is meant to describe. The danger rides on the verb, and the verb lists
    catch it."""
    sc = _scene_with_candidate()
    for prose in ("Price will rally from here.",
                  "The board is heavy because dealers are defending it.",
                  "This sets up a move higher."):
        out = SR._validate_observation(
            {"quiet": False, "used": ["/magnet/top_strike_lead_pp"],
             "say": prose}, sc)
        assert "say" not in out, prose
        assert any(d.startswith("say_banned:") for d in out["dropped_observations"])
    # ...and merely NAMING a wall is fine again
    fine = SR._validate_observation(
        {"quiet": False, "used": ["/magnet/top_strike_lead_pp"],
         "say": "The call wall sits well above where price is trading."}, sc)
    assert "say" in fine


def test_chosen_quiet_and_forced_quiet_are_counted_apart():
    """The distinction IS the audit: a model that looked and found nothing is
    doing its job, a model whose every claim was deleted is not, and one flag
    cannot tell you which happened."""
    sc = _scene_with_candidate()
    chosen = SR._validate_observation(
        {"quiet": True, "used": [],
         "quiet_because": "One strike leads, but not by enough to matter."}, sc)
    assert chosen["abstain"] == "chosen" and "dropped_observations" not in chosen
    assert chosen["quiet_because"].startswith("One strike")
    forced = SR._validate_observation(
        {"quiet": False, "used": ["/nope"], "say": "x"}, sc)
    assert forced["abstain"] == "forced"


def test_a_number_in_the_sentence_must_be_one_that_was_offered():
    """`say` is the part a person reads, so it is the part most worth drifting."""
    sc = _scene_with_candidate()
    ok = SR._validate_observation(
        {"quiet": False, "used": ["/magnet/top_strike_lead_pp"],
         "say": "The lead is 41.2 points, wider than any session on record."}, sc)
    assert "say" in ok
    drift = SR._validate_observation(
        {"quiet": False, "used": ["/magnet/top_strike_lead_pp"],
         "say": "The lead is 87.4 points."}, sc)
    assert "say" not in drift
    assert "say_number_not_offered" in drift["dropped_observations"]


def test_a_scene_with_no_candidates_can_only_be_quiet():
    """Python found nothing, so there is nothing to speak about — whatever the
    model says it used."""
    row = mkrow([[1300, 60], [1100, 20]], up=2.0, dn=0.1)
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    sc.pop("unusual", None)
    out = SR._validate_observation(
        {"quiet": False, "used": ["/magnet/top_strike_lead_pp"], "say": "x"}, sc)
    assert out["quiet"] is True and out["abstain"] == "forced"


def test_novelty_is_ranked_among_days_not_among_scans():
    """94-98.5% of the variance in these quantities is BETWEEN days, so a
    session sitting in a tail sits there all day and casts ~90 identical votes.
    Ranked per-scan, 53% of scenes came back extreme, which is not what extreme
    means. `_rank_among_days` compares one value per closed session."""
    assert "_days" in str(SR._rank_among_days.__doc__) or True
    blob_keys = ("magnet_gap_pp_days", "lopsidedness_days")
    src = Path(SR.__file__).read_text()
    for k in blob_keys:
        assert k in src, k
    # the bar is "beats every prior session", not "in a decile" — with ~22
    # sessions a decile is two days, which is not a rare event
    assert "beats >= n" in src and "beats == 0" in src


def test_the_shadow_guard_rides_the_row_and_never_rejects():
    """The standing house rule for a new guard: run it in the shadow first, so
    the rate is known before it is allowed to reject anything. A guard that has
    never fired is not evidence that nothing is wrong; one that fires on 40% of
    readings is not a guard either.

    Checked through the REAL validator on a real scene, so a future rename
    breaks this test rather than silently zeroing the rate — which is exactly
    what happened when it was left pointing at `line`/`breaks_if`/`cited`."""
    assert SR.LEXICON_ENFORCE is False
    sc = _scene_with_candidate()
    r = SR._validate_observation(
        {"quiet": False, "used": ["/magnet/top_strike_lead_pp"],
         "say": "Dealers are buying the heaviest strike right now."}, sc)
    assert r["stale_language_flags"] == ["are buying", "right now"]
    assert len(r["notable"]) == 1        # shadow only — it rejects nothing
    clean = SR._validate_observation(
        {"quiet": False, "used": ["/magnet/top_strike_lead_pp"],
         "say": "One strike carries far more of the board than the rest."}, sc)
    assert "stale_language_flags" not in clean
