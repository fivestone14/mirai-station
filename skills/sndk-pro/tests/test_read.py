"""sndk_read tests — the gates and the bans.

Every rule this module enforces exists because the four recorded sessions
(2026-07-28..31, 756 rows) measured a specific failure. These tests pin the
rule, not the implementation: the magnet must admit a tie, the model must
never be handed a constant, and a wake must be earned, not scheduled.
"""
import json
import re
from pathlib import Path
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

import sndk_read as SR
from synth import T0, _every_scene_shape, _last_call, reader_row as mkrow

ET = ZoneInfo("America/New_York")


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
    """A 30-minute read must not be claimed off five minutes of rows, and when
    it is claimed the reference is found BY TIMESTAMP — the row at the
    30-minute mark, not the oldest row and not a fixed count back, which a gap
    in the scan would silently shorten."""
    rows = [mkrow([[1300, 9]], spot=1200 + i, ts=T0 - timedelta(minutes=10 - i))
            for i in range(6)]
    assert "_ran_30m_sigma" not in SR.with_path(rows[-1], rows)
    # nineteen minutes of history is still not a read; twenty is
    short = [mkrow([[1300, 9]], spot=1200.0, ts=T0 - timedelta(minutes=19)),
             mkrow([[1300, 9]], spot=1230.0, ts=T0)]
    assert "_ran_30m_sigma" not in SR.with_path(short[-1], short)
    enough = [mkrow([[1300, 9]], spot=1200.0, ts=T0 - timedelta(minutes=20)),
              mkrow([[1300, 9]], spot=1230.0, ts=T0)]
    assert SR.with_path(enough[-1], enough)["_ran_30m_sigma"] == pytest.approx(0.30)
    # an hour of 2-minute rows with a ten-minute scan gap: the row at T0-30 is
    # spot 1215, so $15 over a $100 sigma. The oldest row would say 0.30 and
    # fifteen rows back across the gap would say 0.20.
    hour = [mkrow([[1300, 9]], spot=1200 + i, ts=T0 - timedelta(minutes=60 - 2 * i))
            for i in range(31) if not 20 <= i < 25]
    assert SR.with_path(hour[-1], hour)["_ran_30m_sigma"] == pytest.approx(0.15)


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


def _minutes(span, n=30, end=None):
    """strikes-3: `n` completed minute bars ending at `end` (T0), each `span`
    dollars from low to high — the minute record the move bar is counted in.
    The gate's bar is MOVE_MINUTES of these, so a span of 5 makes it $10."""
    end = end or T0
    return [{"ts": (end - timedelta(minutes=n - i)).isoformat(), "open": 1200.0,
             "high": 1200.0 + span, "low": 1200.0, "close": 1200.0, "volume": 10.0}
            for i in range(n)]


def test_first_scan_always_wakes():
    assert SR.should_wake(mkrow([[1300, 9]]), None, None, T0) == "first read"


def _books_at(offsets, *rows):
    """Books stamped at explicit minutes-before-T0, so a fixture can put them
    INSIDE the min-gap window. `_books` hard-codes T0-10/-9, which is older than
    any prev read the interrupt path cares about — and a book older than the last
    read is not a book _books_since will ever return."""
    out = []
    for off, r in zip(offsets, rows):
        r = dict(r)
        r["meta"] = dict(r.get("meta") or {},
                         book_asof=(T0 - timedelta(minutes=off)).isoformat())
        out.append(r)
    return out


def test_nothing_at_all_fires_inside_the_hard_floor():
    """A level being straddled tick after tick costs one read, not one a scan."""
    prev = _read(T0 - timedelta(minutes=SR.INTERRUPT_MIN_GAP_MIN - 1),
                 gamma_sign="negative")
    row = mkrow([[1300, 9]], gamma_sign="positive")
    two = _books_at([1.5, 1], mkrow([[1300, 9]], gamma_sign="positive"),
                    mkrow([[1300, 9]], gamma_sign="positive"))
    # the trigger itself is live — it is the clock that refuses it
    assert SR._wake_trigger(row, None, prev, T0, two) == "gamma sign flipped"
    assert SR.should_wake(row, None, prev, T0, two) is None


def test_interrupts_have_their_own_budget():
    """2026-09-09. A gamma sign flipping does not become less material because
    it happened eight minutes after the last read — the standing sentence is now
    describing a board that no longer exists — so a material event breaks the
    floor. But past the interrupt cap the floor is absolute again, so a trending
    session cannot spend the whole day's reads before lunch. The ordinary
    cadence is untouched — that is the point of a separate budget."""
    prev = _read(T0 - timedelta(minutes=SR.MIN_GAP_MIN - 2), gamma_sign="negative")
    row = mkrow([[1300, 9]], gamma_sign="positive")
    two = _books_at([2, 1], mkrow([[1300, 9]], gamma_sign="positive"),
                    mkrow([[1300, 9]], gamma_sign="positive"))
    # a caller that passes no count leaves the budget untouched: it fails open
    assert SR.should_wake(row, None, prev, T0, two) == "gamma sign flipped"
    spent = SR.INTERRUPT_DAILY_CAP
    assert SR.should_wake(row, None, prev, T0, two, interrupts_today=spent) is None
    assert SR.should_wake(row, None, prev, T0, two,
                          interrupts_today=spent - 1) == "gamma sign flipped"
    # past the gap the interrupt budget is irrelevant — it only gates the floor
    old_prev = _read(T0 - timedelta(minutes=SR.MIN_GAP_MIN + 1), gamma_sign="negative")
    assert SR.should_wake(row, None, old_prev, T0, two,
                          interrupts_today=spent) == "gamma sign flipped"


def test_drift_never_interrupts_however_large():
    """Continuous drift stays outside the interrupt set on purpose: "how far is
    far enough inside ten minutes" is the argument the floor exists to end."""
    prev = _read(T0 - timedelta(minutes=SR.MIN_GAP_MIN - 2), gamma_flip=1000.0)
    row = mkrow([[1300, 9]], spot=1200.0,
                gamma_flip=1000.0 + SR.WAKE_FLIP_SIGMA * 100 * 3)
    two = _books_at([2, 1], row, row)
    assert SR._wake_trigger(row, None, prev, T0, two) == "flip moved"
    assert SR.should_wake(row, None, prev, T0, two) is None


@pytest.mark.parametrize("gap_min, travel, expected", [
    pytest.param(20, 11, "price ran", id="past-floor-over-the-bar"),
    pytest.param(20, 10, "price ran", id="past-floor-exactly-the-bar"),
    pytest.param(20, 9, None, id="past-floor-under-the-bar"),
    pytest.param(SR.MIN_GAP_MIN - 1, 15, None, id="inside-floor-over-the-bar"),
    pytest.param(SR.MIN_GAP_MIN + 1, 15, "price ran", id="same-travel-past-the-floor"),
    pytest.param(SR.MIN_GAP_MIN - 2, 19, None, id="inside-floor-under-double"),
    pytest.param(SR.MIN_GAP_MIN - 2, 20, "price ran", id="inside-floor-exactly-double"),
    pytest.param(SR.MIN_GAP_MIN - 2, 25, "price ran", id="inside-floor-past-double"),
])
def test_price_ran_wakes(gap_min, travel, expected):
    """Plain travel against the move bar, and the floor it has to clear.

    Past MIN_GAP_MIN travel at the bar is the ordinary trigger. Inside it the
    floor still binds on drift — travel past the bar is nothing at all there,
    which is the spam wk-1 closed — and "price ran", the most frequent wake
    there is, would defeat the floor on any trending day at the ordinary bar,
    so inside it the bar is doubled."""
    bars = _minutes(5)                              # the bar is $10, doubled $20
    prev = _read(T0 - timedelta(minutes=gap_min), spot=1200.0)
    row = mkrow([[1300, 9]], spot=1200.0 + travel)
    assert SR.should_wake(row, None, prev, T0, bars=bars) == expected


def test_the_move_bar_is_sized_to_the_hour_not_the_day():
    """Review item #6. The same $12 since the last read is nothing in a morning
    of $8 minutes and a move in an afternoon of $2 minutes — a fixed slice of
    the day's sigma called both the same, firing on 61% of opening stretches
    and 6% of afternoon ones."""
    prev = _read(T0 - timedelta(minutes=20), spot=1200.0)
    row = mkrow([[1300, 9]], spot=1212.0)
    assert SR.should_wake(row, None, prev, T0, bars=_minutes(8)) is None
    assert SR.should_wake(row, None, prev, T0, bars=_minutes(2)) == "price ran"
    assert SR.move_threshold(_minutes(2), T0) == pytest.approx(2 * SR.MOVE_MINUTES)


def test_no_minute_record_means_travel_wakes_nothing():
    """An absent measurement never decides: with no minute bars there is no bar,
    so plain travel stays quiet however far it went — every other trigger and
    the heartbeat still fire."""
    prev = _read(T0 - timedelta(minutes=20), spot=1200.0)
    far = mkrow([[1300, 9]], spot=1500.0)
    assert SR.should_wake(far, None, prev, T0) is None
    assert SR.should_wake(far, None, prev, T0, bars=[]) is None
    # too few completed minutes in the window is no ruler either; exactly
    # enough is one
    assert SR.typical_minute(_minutes(5, n=SR.MINUTE_RULER_MIN_BARS - 1), T0) is None
    assert SR.typical_minute(_minutes(5, n=SR.MINUTE_RULER_MIN_BARS), T0) == 5.0
    # a flat tape has no ruler, so it is no licence to wake on travel either
    assert SR.typical_minute(_minutes(0), T0) is None
    assert SR.should_wake(far, None, prev, T0, bars=_minutes(0)) is None
    # a minute still running is a partial and never counts
    running = _minutes(5, n=SR.MINUTE_RULER_MIN_BARS - 1) + [
        {"ts": (T0 - timedelta(seconds=30)).isoformat(), "open": 1200.0,
         "high": 1205.0, "low": 1200.0, "close": 1200.0, "volume": 1.0}]
    assert SR.typical_minute(running, T0) is None
    # minutes older than the window do not count
    assert SR.typical_minute(_minutes(5, end=T0 - timedelta(minutes=45)), T0) is None
    old = _read(T0 - timedelta(minutes=SR.HEARTBEAT_MIN + 1), spot=1200.0)
    assert SR.should_wake(far, None, old, T0) == "heartbeat"


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
    # price crosses the wall the last reading actually spoke about, while the
    # live wall has already stepped aside above price, as it does on a crossing
    crossed = mkrow([[1300, 9]], spot=1450.0, call_wall=1500.0)
    assert SR.should_wake(crossed, None, prev, T0) == "call wall crossed"


def test_regime_word_alone_no_longer_wakes():
    """`regime` is a word derived from the gamma sign at spot, so waking on it
    woke twice for one fact. The sign itself still wakes, with confirmation."""
    prev = _read(T0 - timedelta(minutes=20), gamma_sign="negative", regime="trending")
    row = mkrow([[1300, 9]], regime="pinning", gamma_sign="negative")
    prev_row = mkrow([[1300, 9]], regime="trending", gamma_sign="negative")
    assert SR.should_wake(row, prev_row, prev, T0) is None
    # the new word has held for two fresh books and the last read's snapshot
    # carries the old one, so a word trigger on the scan clock OR the book
    # clock would fire here; only the unchanged sign keeps the gate asleep
    two = _books(mkrow([[1300, 9]], regime="pinning", gamma_sign="negative"),
                 mkrow([[1300, 9]], regime="pinning", gamma_sign="negative"))
    assert SR.should_wake(row, prev_row, prev, T0, two) is None


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
    bars = _minutes(5)
    assert SR.should_wake(crossed, None, bare, T0, bars=bars) == "price ran"   # not the wall
    stamped = dict(bare, gate=SR.state_for_next_wake(mkrow([[1300, 9]], spot=1200.0,
                                                  call_wall=1400.0)))
    assert SR.should_wake(crossed, None, stamped, T0, bars=bars) == "call wall crossed"
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
    # unchanged since the oldest row, 90 minutes before now
    assert fz["magnet"] == {"field": "magnet", "value": 1300.0, "for_min": 90}


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
    # the day's own path: its low and high, and the 30 minutes with_path
    # measured by timestamp (1215 at T0-30 to 1260 now, over a $100 sigma)
    assert sc["price"]["session_low"] == 1200.0
    assert sc["price"]["session_high"] == 1260.0
    assert sc["price"]["moved_last_30min_sigma"] == 0.45
    # sr-5: the point became a spread — a single "typical" number taught the
    # model a ceiling (33/33 emitted magnitudes inside 0.06-0.20 vs p95 0.46).
    # sr-8 moved the 30-minute spread out of the payload into the doctrine.
    # obs-1 then deleted it from the doctrine too, and the reason is the whole
    # phase: the spread existed to CALIBRATE A FORECAST, and there is no longer
    # a forecast to calibrate. A magnitude table in an observation contract is
    # an invitation to predict. The scale block is the ruler and nothing else,
    # so no such table can come back into it under another name.
    assert sc["scale"] == {"one_sigma_dollars": 100.0}
    assert "0.46" not in SR._DOCTRINE and "1.71" not in SR._DOCTRINE


def _nodes(x, path=()):
    """(key path, value) for every dict and every leaf under `x`, walking
    through lists, so a check can find a name wherever it is nested."""
    if isinstance(x, list):
        for v in x:
            yield from _nodes(v, path)
        return
    yield path, x
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _nodes(v, path + (k,))


def test_scene_carries_no_verdict_at_all(tmp_path):
    """sr-2 stripped the pre-baked arrow from the scene because a verdict
    anchors the read; obs-3 then deleted the arrow at the source. A verdict can
    come back under any name, so every scene shape the builder writes is walked
    for a key that names one."""
    verdict = re.compile(r"arrow|vector|verdict|bias|decided|(?:^|_)(?:tie|lean|dir|direction)(?:$|_)")
    scenes = _every_scene_shape(tmp_path)
    named = {path for scene in scenes for path, _ in _nodes(scene)
             if path and verdict.search(path[-1])}
    # `direction` in one place only: regime.vol_trend.direction says implied vol
    # is rising or falling, a measurement rather than a call on price
    assert named <= {("regime", "vol_trend", "direction")}
    assert not any(re.search(r"\barrow", json.dumps(scene)) for scene in scenes)
    # obs-1: the doctrine no longer asks for a vector at all. What it must
    # still refuse to do is hand over a verdict, so that is what is checked.
    assert "You are not forecasting" in SR._DOCTRINE
    assert "direction vector" not in SR._DOCTRINE


# --- reply handling --------------------------------------------------------
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


def test_context_states_facts_and_never_verdicts(monkeypatch):
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
    # THE VERDICT HALF needs closed sessions to rank against, and the tmp state
    # dir holds none — so a real scene alone still left `vs_prior_sessions`
    # absent and this half dead. The history is handed in: six closed days of
    # magnet lead and five of lopsidedness, both at PCTL_MIN_SESSIONS or over.
    monkeypatch.setattr(SR, "_prior_sessions", lambda today: {
        "magnet_gap_pp_days": [10.0, 20.0, 30.0, 40.0, 60.0, 70.0],
        "lopsidedness_days": [0.95, 0.96, 0.97, 0.98, 0.99]})
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    assert sc["magnet"]["top_strike_lead_pp"] == 50.0
    assert sc["breadth"]["lopsidedness_0_is_even"] == 0.9
    ranks = (SR.session_context(sc, rows, T0) or {}).get("vs_prior_sessions")
    # a rank states its n, and a value under every prior day says so plainly
    assert ranks == {"top_strike_lead_pp": "higher than 4 of the 6 prior sessions",
                     "lopsidedness": "lower than all 5 prior sessions"}
    for v in ranks.values():
        assert not SR.banned_words(v), v
        assert "unusual" not in v and "extreme" not in v
    # under the session floor there is no rank at all, never a guess
    monkeypatch.setattr(SR, "_prior_sessions", lambda today: {
        "magnet_gap_pp_days": [10.0] * (SR.PCTL_MIN_SESSIONS - 1)})
    assert "vs_prior_sessions" not in (SR.session_context(sc, rows, T0) or {})


def test_the_history_of_closed_sessions_carries_across_new_year():
    """A session file is found by the shape of its name, never by its year. With
    "2026-*.jsonl" every session from 2027-01-01 on dropped out of the ranks'
    history and the open-interest date froze on the last day of 2026."""
    diary = SR._diary_dir()
    diary.mkdir(parents=True, exist_ok=True)
    sessions = ["2026-12-30", "2026-12-31", "2027-01-04"]
    for day in sessions:
        (diary / f"{day}.jsonl").write_text(json.dumps(
            mkrow([[1300, 60], [1100, 20]], ts=T0.replace(
                year=int(day[:4]), month=int(day[5:7]), day=int(day[8:])))) + "\n")
    # files that are not one session's diary, all sorting before the next session
    for name in ("2026-12-31-copy.jsonl", "2027-01-04.jsonl.bak", "0-scratch.jsonl"):
        (diary / name).write_text(json.dumps(mkrow([[1300, 60]])) + "\n")
    today = "2027-01-05"
    assert SR._prior_sessions(today)["sessions"] == sessions
    assert SR._prior_session_date(today) == sessions[-1]
    ranged = SR._prior_sessions_range(today)
    assert (ranged["sessions"], ranged["from"], ranged["to"]) == (3, sessions[0], sessions[-1])


def test_a_rounded_number_is_the_boards_number_said_out_loud():
    """review item #15: "1800 adding over 11,000 contracts" against a board
    holding 11,006 was six away from the only slack the gate had, so it was
    logged as an invented number — and an invented number deletes the WHOLE
    reading, not the sentence. A spoken integer carries its precision in its
    trailing zeros, so the board's figure is rounded the way the sentence
    rounds; a tenth of a percent keeps that honest at every size."""
    board = {11006.0, 1595.0, 4496965.0, 63.52, 1700.0}

    # said the way a person says it
    for tok in ("11,000", "11000", "11,010", "4,500,000", "1700"):
        assert SR.spoken_as_rounded(float(tok.replace(",", "")), tok, board), tok

    # still inventions: too far for the rounding claimed, or no board figure
    for tok in ("12,500", "1600", "10,000", "2,000"):
        assert not SR.spoken_as_rounded(float(tok.replace(",", "")), tok, board), tok

    # a number that claims no rounding gets none of this
    assert SR._spoken_step("63.5") is None and SR._spoken_step("1595") is None
    assert SR._spoken_step("11,000") == 1000 and SR._spoken_step("1,750") == 10

    # and end to end, through the checker the model's answer meets
    scene = {"price": {"live_spot": 1700.0}, "strikes": {"rows": [{"strike": 1700.0, "vol_added_in_series": 11006}]}}
    ok = SR.check_reading_against_scene(
        {"read": "1700 added over 11,000 contracts in the series.", "points": []}, scene)
    assert ok.get("read") and not [d for d in ok.get("dropped_observations") or [] if "number" in d]
    bad = SR.check_reading_against_scene(
        {"read": "1700 added over 12,500 contracts in the series.", "points": []}, scene)
    assert not bad.get("read") and any("number_not_on_the_board:12,500" in d
                                       for d in bad.get("dropped_observations") or [])


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
def _returned_literals(fn):
    """Every plain string `fn` can return, read off its source. An f-string's
    pieces are skipped — the wall crossings are built from one and are listed
    by hand in the test below."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    found = set()
    for ret in ast.walk(tree):
        if not isinstance(ret, ast.Return) or ret.value is None:
            continue
        in_fstring = {id(n) for j in ast.walk(ret.value)
                      if isinstance(j, ast.JoinedStr) for n in ast.walk(j)}
        found |= {n.value for n in ast.walk(ret.value)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and id(n) not in in_fstring}
    return found


def test_every_wake_reason_translates_and_every_translation_is_speakable():
    """The RAW wake reason is never handed to the model — "pin moved" baited it
    into echoing "pin", a banned word, and the whole read died in testing. So
    the table must cover every reason the gate can produce, and every value
    must pass the lexicon.

    A hand-written list alone could never notice a NEW reason, which is the
    failure that matters: the frame would silently say "routine check". So the
    gate's own source is read for every reason it returns."""
    reasons = {"first read", "price ran", "pin moved", "gamma sign flipped",
               "crossed flip", "call wall crossed", "put wall crossed",
               "iv moved", "flip moved", "heartbeat",
               "listed strike crossed", "volume leader changed"}
    gate = _returned_literals(SR._wake_trigger) | _returned_literals(SR.should_wake)
    assert {"first read", "heartbeat", "price ran"} <= gate, gate   # the scan works
    assert gate <= reasons, f"a wake reason with no translation: {gate - reasons}"
    assert set(SR.INTERRUPT_WAKES) <= reasons
    for r in reasons:
        assert r in SR._WAKE_WORDS, r
    for phrase in SR._WAKE_WORDS.values():
        assert SR.banned_words(phrase) == [], phrase


def test_a_box_may_be_given_a_floor_but_a_strike_may_not():
    """strikes-3: the boxes ship a low and a high, and the English for those two
    edges is floor and ceiling. The live 09-11 10:04 reading lost every one of
    its sentences to "the opening box's floor" — a name for an edge that was on
    the board, claiming nothing. Bound to a box the word is a name; loose it is
    still the assertion that price will hold there."""
    allowed = (
        "the session low printed at 1624, right at the opening box's floor",
        "price came back to the box's ceiling and went through it",
        "1624 is the floor of the opening box",
        "it closed under the ceiling of that range at 15:12",
        "the in-force band's floor is 1697.01",
        "the day's range's ceiling gave way at 13:58",
    )
    for phrase in allowed:
        assert SR.banned_words(phrase) == [], phrase

    still_banned = (
        "1700 is the floor here",
        "the 1750 ceiling",
        "a floor at 1624",
        "price found its floor",
        "the strike's floor held",
        "a ceiling on the move",
    )
    for phrase in still_banned:
        assert SR.banned_words(phrase), phrase

    # the mask covers the edge's name and NOTHING else in the sentence
    assert SR.banned_words(
        "the opening box's floor is 1624 and price should hold it") == ["should"]


def test_the_iv_trigger_needs_a_full_window_before_it_may_fire():
    """It compared a MEDIAN against a single raw reading, and did not require
    the median to have anything in it. Measured over the recorded sessions, the
    "5-book median" was 3 books in 20 of the 34 fires and 2 in 5 — so on the
    noisiest day the trigger spent 14 of 25 calls, every one pinned to the
    MIN_GAP_MIN floor. A short window is not a licence to fire; it is a reason
    to wait."""
    n = SR.WAKE_IV_MEDIAN_BOOKS
    spoke = 0.40
    moved = spoke + SR.WAKE_IV_PP / 100.0 * 2      # a move twice the threshold
    prev = _read(T0 - timedelta(minutes=20), atm_iv=spoke)

    def books(ivs):
        return _books_at([10 - i for i in range(len(ivs))],
                         *[mkrow([[1300, 9]], atm_iv=v) for v in ivs])

    # every book agrees vol moved, but there are not enough of them to count
    short = books([moved] * (n - 1))
    assert SR.should_wake(short[-1], None, prev, T0, short) is None
    full = books([moved] * n)
    assert SR.should_wake(full[-1], None, prev, T0, full) == "iv moved"
    # and it is the median that is compared: one spiking book in a full window
    # is sensor noise, not a move
    spike = books([spoke] * (n - 1) + [moved])
    assert SR.should_wake(spike[-1], None, prev, T0, spike) is None


def test_a_wall_crossed_wake_yields_a_frozen_crossing():
    """Pins the frame's straddle test to the wake gate's: if the gate said a
    wall was crossed, the frame must name the frozen level it was measured
    against — otherwise the wake reason and the block contradict each other."""
    lc = _last_call(spot=1490.0, call_wall=1500.0)
    row = mkrow([[1300, 60], [1100, 20]], spot=1520.0)
    # the wake reason comes from the real gate on the same last call, so the
    # two straddle tests are checked against each other, not against a string
    wake = SR.should_wake(row, None, lc, T0)
    assert wake == "call wall crossed"
    fr = SR.frame_since_last_read(row, [row], lc, wake, False, T0)
    assert fr["crossed_since_then"] == [
        {"level": 1500.0, "was_labelled_then": "nearest_call_wall",
         "price_went": "up"}]
    assert fr["spot_change_dollars"] == 30.0
    # the words come from the table, and the table's words for this reason must
    # themselves say a level was crossed, or the frame contradicts its own block
    assert fr["why_this_read"] == SR._WAKE_WORDS[wake] == "price crossed a level from the last read"


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
    rather than garnish.

    obs-4: the last call sits at 08:57, two minutes BEFORE the first scan. The
    day's boxes now carry their own clock strings (`formed_over: "08:59-…"`),
    so a last call on the same minute as the first scan put "59" on the bare
    board through a different door and the sentence survived without the
    frame — which is not what this test measures."""
    lc = _last_call(minutes_ago=63, spot=1543.2)
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


def test_the_session_range_anchor_is_gone_and_stays_gone():
    """The whole-day range anchor shipped and was measured LYING: with two
    clocks in the frame the model fused them — "held between X and Y since
    <the session anchor>" — and 8 of 11 live quiet reads paired the since-last-
    read range with the wrong timestamp. The frame now carries exactly one
    range (`held_between_since_last_read`) and exactly one clock it may pair
    with (`last_read_at`), whatever a second one would be called. This test
    memorialises the deletion."""
    rows = [mkrow([[1300, 60], [1100, 20]], spot=v,
                  ts=T0 - timedelta(minutes=m))
            for v, m in ((1500.0, 90), (1530.0, 75), (1510.0, 50), (1512.0, 0))]
    lc = _last_call(minutes_ago=50, spot=1510.0)
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "heartbeat", False, T0,
                                  bars=_minutes(5))
    clocks = {path for path, v in _nodes(fr)
              if isinstance(v, str) and re.search(r"\d{1,2}:\d{2}", v)}
    ranges = {path for path, v in _nodes(fr)
              if isinstance(v, dict) and {"low", "high"} <= set(v)}
    assert clocks == {("last_read_at",)}
    assert ranges == {("held_between_since_last_read",)}
    # a second range need not be a low/high pair to be one
    assert {k for k in fr if "range" in k or "held" in k} == {"held_between_since_last_read"}
    # ...and that one range starts at the last read, never the session's 1500-1530
    assert fr["held_between_since_last_read"] == {"low": 1510.0, "high": 1512.0}


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
    # 0.8 away: inside a dollar, outside the old half-unit, so this is the
    # case that tells the two tolerances apart
    sc["context"] = {"moved_pct": 63.2}
    out = SR.check_reading_against_scene(
        {"quiet": True, "read": "The measure sits near 64 on the day."}, sc)
    assert "read" in out and not out.get("dropped_observations")
    # and the slack is a dollar, not more: 1.8 away is still an invention
    far = SR.check_reading_against_scene(
        {"quiet": True, "read": "The measure sits near 65 on the day."}, sc)
    assert "read" not in far
    assert "read_number_not_on_the_board:65" in far["dropped_observations"]


def test_lexicon_enforce_is_wired_to_a_real_drop(monkeypatch):
    """The standing house rule for a new guard: run it in the shadow first, so
    the rate is known before it is allowed to reject anything. And the sr-7
    switch was once wired to NOTHING — flipping it on changed no behaviour — so
    enforcement must drop the read and say so in dropped, the way a banned
    verb does: the prose goes, a level still worth watching stays."""
    assert SR.LEXICON_ENFORCE is False
    sc = _obs_scene()
    slipping = {"quiet": False,
                "read": "Dealers are buying the heaviest strike right now."}
    shadow = SR.check_reading_against_scene(dict(slipping), sc)
    assert shadow["stale_language_flags"] == ["are buying", "right now"]
    assert "read" in shadow                            # shadow only — it rejects nothing
    clean = SR.check_reading_against_scene(
        {"quiet": False, "read": "One strike carries most of the board."}, sc)
    assert "stale_language_flags" not in clean
    monkeypatch.setattr(SR, "LEXICON_ENFORCE", True)
    hard = SR.check_reading_against_scene(dict(slipping), sc)
    assert "read" not in hard
    assert hard["abstain"] == "forced"
    assert any(d.startswith("read_stale_tense:")
               for d in hard["dropped_observations"])
    strike = sc["magnet"]["top_strikes"][0]["strike"]
    point = {"level": strike, "note": "heaviest strike"}
    kept = SR.check_reading_against_scene(dict(slipping, points=[point]), sc)
    assert "read" not in kept and kept["points"] == [point]
    assert kept["quiet"] is False and "abstain" not in kept


def test_unknown_gamma_sign_never_wakes_the_flip():
    """"unknown" held for two books is agreement about ignorance, not a flip —
    and a flip INTO unknown is a data gap, not an event."""
    for blank in ("unknown", "unmeasured", "", None):
        assert SR._known_sign(blank) is None, blank
    assert SR._known_sign("negative") == "negative"
    # through the gate: a measured sign, then two books that could not measure one
    prev = _read(T0 - timedelta(minutes=20), gamma_sign="negative")
    blind = _books(mkrow([[1300, 9]], gamma_sign="unknown"),
                   mkrow([[1300, 9]], gamma_sign="unknown"))
    assert SR.should_wake(mkrow([[1300, 9]], gamma_sign="unknown"), None,
                          prev, T0, blind) is None
    # a last read that could not measure one keeps no sign in its snapshot, so
    # two measured books after it are a first measurement, not a flip
    snap = SR.state_for_next_wake(mkrow([[1300, 9]], gamma_sign="unknown"))
    assert "gamma_sign" not in snap
    unmeasured = {"ts": (T0 - timedelta(minutes=20)).isoformat(), "spot": 1200.0,
                  "gate": snap}
    seen = mkrow([[1300, 9]], gamma_sign="positive")
    two = _books(mkrow([[1300, 9]], gamma_sign="positive"),
                 mkrow([[1300, 9]], gamma_sign="positive"))
    assert SR.should_wake(seen, None, unmeasured, T0, two) is None
    # ...while the same two books after a measured sign are the flip
    assert SR.should_wake(seen, None, prev, T0, two) == "gamma sign flipped"


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
    # the call wall it left, and nothing else: the put wall at 1000 was never near
    assert fr["crossed_since_then"] == [
        {"level": 1400.0, "was_labelled_then": "nearest_call_wall", "price_went": "up"}]


# ---------------------------------------------------------------- strikes-5 (2026-09-15)
def _volrow(leader, spot=1200.0, ts=None):
    """A row whose volume-today leader is `leader` (1200 or 1300), with a full
    board so the table's own ranking can run over it."""
    oi = {1200.0: (300, 150), 1300.0: (900, 450), 1250.0: (50, 25)}   # 1300 leads contracts whoever leads volume
    vol = {1200.0: (90, 40), 1300.0: (10, 5), 1250.0: (1, 1)} if leader == 1200.0 else \
          {1200.0: (10, 5), 1300.0: (90, 40), 1250.0: (1, 1)}
    gv = {"magnet": 1300.0, "mass_by_strike": [[k, c + p] for k, (c, p) in sorted(oi.items())],
          "oi_side_by_strike": [[k, c, p] for k, (c, p) in sorted(oi.items())],
          "vol_side_by_strike": [[k, c, p] for k, (c, p) in sorted(vol.items())],
          "net_by_strike": [[1200.0, -2.0], [1250.0, 1.0], [1300.0, 5.0]],
          "shove": {"shove_up_margin": 2.0, "shove_down_margin": 0.2}}
    return mkrow([[1300, 9]], spot=spot, ts=ts, gex_views=gv,
                 meta={"chain_spot": spot, "book_source": "pull", "spot_source": "schwab_quote"})


def test_a_volume_leader_change_wakes_after_two_books_and_needs_a_reference():
    """strikes-5. The strike leading today's volume changed since the last call
    and held two books; one book is flicker, and a last call that measured no
    leader (a withheld morning book) is not a reference to change from."""
    prev = _read(T0 - timedelta(minutes=20), volume_leader=1300.0)
    row = _volrow(1200.0)
    two = _books(_volrow(1200.0), _volrow(1200.0))
    assert SR.should_wake(row, None, prev, T0, two) == "volume leader changed"
    assert SR.should_wake(row, None, prev, T0, _books(_volrow(1200.0))) is None
    assert SR.should_wake(row, None, prev, T0, _books(_volrow(1200.0), _volrow(1300.0))) is None
    assert SR.should_wake(row, None, _read(T0 - timedelta(minutes=20)), T0, two) is None
    # never inside the floor: it is not an interrupt
    assert SR.should_wake(row, None, _read(T0 - timedelta(minutes=5), volume_leader=1300.0),
                          T0, _books_at([4, 1], _volrow(1200.0), _volrow(1200.0))) is None
    # ...and the snapshot the next gate reads carries the leader
    assert SR.state_for_next_wake(row)["volume_leader"] == 1200.0
    assert "volume_leader" not in SR.state_for_next_wake(mkrow([[1300, 9]]))   # no volume measured


def test_a_carried_book_ranks_no_volume_leader(monkeypatch):
    """strikes-5: a book whose volume is the prior session's (item #8) confirms
    nothing and records no reference — the morning's first books say who led
    YESTERDAY, and a wake on that would be a wake on a stale count."""
    import sndk_board
    prev = _read(T0 - timedelta(minutes=20), volume_leader=1300.0)
    row = _volrow(1200.0)
    two = _books(_volrow(1200.0), _volrow(1200.0))
    newest = two[-1]["meta"]["book_asof"]
    monkeypatch.setattr(sndk_board, "carried_books", lambda rows, now: {newest: "prior session's count"})
    assert SR.should_wake(row, None, prev, T0, two) is None
    monkeypatch.setattr(sndk_board, "carried_books", lambda rows, now: {row.get("meta", {}).get("book_asof"): "x"})
    row_c = dict(row, meta={**row["meta"], "book_asof": T0.isoformat()})
    monkeypatch.setattr(sndk_board, "carried_books", lambda rows, now: {T0.isoformat(): "prior session's count"})
    assert "volume_leader" not in SR.state_for_next_wake(row_c, None, [row_c], T0)


def test_a_listed_crossing_is_judged_in_the_sigma_of_the_call_it_was_shown_at():
    """strikes-5: the side a strike was on at the last call is read with THAT
    call's sigma, so a strike inside the at-band then is on no side, and a
    wide ruler then cannot be re-read with today's narrow one."""
    prev = _read(T0 - timedelta(minutes=20), spot=1240.0, sigma=400.0)
    prev["strikes_sent"] = [1250.0]
    assert SR.should_wake(mkrow([[1300, 9]], spot=1300.0), None, prev, T0) is None


def test_a_listed_strike_crossing_wakes_only_when_nothing_else_would():
    """strikes-5. Price on the other side of a strike the last call was shown
    (`strikes_sent`) wakes the model; a wall crossing or plain travel keeps its
    own reason, and a call that kept no list wakes nothing this way."""
    prev = _read(T0 - timedelta(minutes=20), spot=1200.0, call_wall=1400.0)
    prev["strikes_sent"] = [1150.0, 1250.0]
    crossed = mkrow([[1300, 9]], spot=1260.0)
    assert SR.should_wake(crossed, None, prev, T0) == "listed strike crossed"
    assert SR.should_wake(mkrow([[1300, 9]], spot=1240.0), None, prev, T0) is None
    # the table's own side rule: a strike inside the at-band on either end is on no side yet
    assert SR.should_wake(mkrow([[1300, 9]], spot=1252.0), None, prev, T0) is None
    assert SR.should_wake(crossed, None, _read(T0 - timedelta(minutes=20), spot=1248.0, strikes_sent=None) | {"strikes_sent": [1250.0]}, T0) is None
    bare = _read(T0 - timedelta(minutes=20), spot=1200.0, call_wall=1400.0)
    assert SR.should_wake(crossed, None, bare, T0) is None
    # every wake that fired before still fires first, under its own name
    assert SR.should_wake(crossed, None, prev, T0, bars=_minutes(5)) == "price ran"
    wall = mkrow([[1300, 9]], spot=1450.0, call_wall=1400.0)
    assert SR.should_wake(wall, None, prev, T0) == "call wall crossed"
    # and not inside the floor
    recent = _read(T0 - timedelta(minutes=5), spot=1200.0)
    recent["strikes_sent"] = [1250.0]
    assert SR.should_wake(crossed, None, recent, T0) is None
