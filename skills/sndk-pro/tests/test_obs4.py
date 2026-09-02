"""obs-4 (2026-09-02) — the frame repairs, the day's boxes, the structure
block and the three-layer guard.

Every rule here pins a sentence the reader actually shipped on 09-01/09-02:
a $34 drop described as a held range, a call wall reported unchanged at 1600
while the walls block showed 1550, a put wall reported at 1500 beside "no put
wall", "favoring" walking past a guard that banned "favors", and "still under
the 1500 strike" at 1528.70 — plus the crossings study that found a crossing
to be a two-dollar event at a two-dollar noise floor, which is why boxes
break only past 0.05 sigma."""
import json
from datetime import timedelta

import pytest

import sndk_read as SR
from test_read import mkrow, _last_call, T0
from test_scene_v2 import rich_row, scene_of


def _rows(spots, minutes_ago, **kw):
    return [mkrow([[1300, 60], [1100, 20]], spot=v, ts=T0 - timedelta(minutes=m), **kw)
            for v, m in zip(spots, minutes_ago)]


def _tape(spots, step_min=2, sigma=100.0):
    start = T0 - timedelta(minutes=step_min * (len(spots) - 1))
    return [mkrow([[1300, 60]], spot=v, sigma=sigma,
                  ts=start + timedelta(minutes=i * step_min))
            for i, v in enumerate(spots)]


_CALLS_ONLY = ([[1240, 8.0], [1245, 7.0], [1300, 6.5]]
               + [[k, 0.1] for k in range(1105, 1300, 5) if k not in (1240, 1245)])


# --- bug 1: the since-window starts where price WAS -------------------------
def test_the_since_window_starts_where_price_was():
    """09-02 10:01: last read at 1557.53, then 1546, 1522, 1518, 1523. The
    block said held_between 1518-1546 and the model wrote "held". The range is
    seeded with spot_then, and the frame says it is a move."""
    lc = _last_call(minutes_ago=10, spot=1557.53)
    rows = _rows([1546.08, 1522.0, 1518.45, 1523.0], [8, 6, 4, 0], sigma=58.78)
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "price ran", False, T0)
    assert fr["held_between_since_last_read"] == {"low": 1518.45, "high": 1557.53}
    assert fr["spot_change_sigma"] == pytest.approx(-0.59, abs=0.01)
    assert fr["frame_is"] == "a move"


def test_a_small_drift_is_a_hold():
    lc = _last_call(minutes_ago=10, spot=1543.2)
    rows = _rows([1541.7], [0], sigma=100.0)
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "heartbeat", False, T0)
    assert fr["frame_is"] == "a hold"
    assert fr["spot_change_sigma"] == pytest.approx(-0.015, abs=0.006)


# --- bugs 2 and 3: like with like, and nothing invented ----------------------
def test_the_gate_freezes_the_ladders_wall_never_the_diary_scalar():
    """rich_row's ladder: call clusters 1240/1245 and 1300, puts 1100 and 1150.
    At spot 1490 the ladder has NO call wall above and 1150 below, whatever the
    diary's pre-cluster scalars say. A thin row with no surface at all keeps
    the scanner's own scalars — consistently, on both sides of every compare."""
    g = SR.state_for_next_wake(rich_row(spot=1490.0, call_wall=1500.0, put_wall=1000.0))
    assert "call_wall" not in g and g["put_wall"] == 1150.0
    thin = SR.state_for_next_wake(mkrow([[1300, 9]], spot=1200.0, call_wall=1400.0))
    assert thin["call_wall"] == 1400.0


def test_unchanged_walls_compare_ladder_to_ladder():
    """10:01: ladder 1550, diary 1600, block said "call_wall unchanged 1600".
    The then-wall is the gate's (the ladder's); the now-wall is the ladder's;
    the diary scalar never enters the comparison."""
    then = rich_row(spot=1200.0)                                   # ladder: 1240 / 1150
    lc = {"ts": (T0 - timedelta(minutes=20)).isoformat(), "wall_s": 1.0,
          "gate": SR.state_for_next_wake(then)}
    now = rich_row(spot=1200.0, call_wall=1600.0, put_wall=1000.0)   # the diary lies
    fr = SR.frame_since_last_read(now, [then, now], lc, "heartbeat", False, T0)
    assert fr["unchanged_since_then"]["call_wall"] == 1240.0
    assert fr["unchanged_since_then"]["put_wall"] == 1150.0
    assert "walls_absent_then_and_now" not in fr


def test_a_side_the_ladder_found_empty_is_said_to_be_empty_not_numbered():
    """09-01 12:33: "1500 put wall unchanged" beside "no put wall". No rung,
    no number: the block says the side was empty at both readings."""
    then = rich_row(spot=1200.0, nbs=_CALLS_ONLY, put_wall=1100.0)
    lc = {"ts": (T0 - timedelta(minutes=20)).isoformat(), "wall_s": 1.0,
          "gate": SR.state_for_next_wake(then)}
    assert "put_wall" not in lc["gate"]
    now = rich_row(spot=1200.0, nbs=_CALLS_ONLY, put_wall=1100.0)
    fr = SR.frame_since_last_read(now, [then, now], lc, "heartbeat", False, T0)
    assert fr["walls_absent_then_and_now"] == ["put"]
    assert "put_wall" not in fr.get("unchanged_since_then", {})


def test_a_walls_block_the_freshness_gate_deleted_freezes_nothing():
    """The frame may only name what the model was shown: a scene whose walls
    block was dropped for age hands the gate no wall at all, not the diary's."""
    r = rich_row(spot=1200.0, call_wall=1600.0)
    g = SR.state_for_next_wake(r, scene={"price": {"live_spot": 1200.0}})
    assert "call_wall" not in g and "put_wall" not in g


# --- the day's boxes ---------------------------------------------------------
def test_the_opening_box_forms_over_thirty_minutes_then_freezes():
    rows = _tape([1500 + (i % 3) * 2 for i in range(16)])      # 30 min, 1500-1504
    rb = SR.ranges_block(rows, T0, 100.0, "2026-07-31")
    assert rb["opening"]["low"] == 1500.0 and rb["opening"]["high"] == 1504.0
    assert rb["opening"]["status"] == "held so far"
    assert rb["in_force"]["is_the_opening_box"] is True
    assert rb["in_force"]["live_spot_is"] == "inside"
    assert "breaks_today" not in rb


def test_the_opening_box_is_still_forming_for_the_first_half_hour():
    rb = SR.ranges_block(_tape([1500, 1502, 1498]), T0, 100.0, "2026-07-31")
    assert rb["opening"]["still_forming"] is True
    assert rb["opening"]["low"] == 1498.0 and rb["opening"]["high"] == 1502.0
    assert rb["in_force"]["still_forming"] is True


def test_a_break_retires_the_opening_box_and_a_new_box_forms():
    """Range-bound inside a box price left is not a claim anyone should be
    handed: the break is recorded with its clock and direction, the opening
    box says it broke, and the box in force is the one forming since."""
    rows = _tape([1500] * 16 + [1520, 1522, 1524])              # +$20 = 0.2 sigma
    rb = SR.ranges_block(rows, T0, 100.0, "2026-07-31")
    b = rb["breaks_today"]["breaks"][0]
    assert rb["breaks_today"]["count"] == 1 and b["went"] == "up"
    assert b["box_low"] == 1500.0 and b["box_high"] == 1500.0
    assert rb["opening"]["status"] == f"broke up at {b['at']}"
    assert rb["in_force"]["still_forming"] is True
    assert (rb["in_force"]["low"], rb["in_force"]["high"]) == (1520.0, 1524.0)
    assert rb["in_force"]["replaced_a_box_broken_at"] == b["at"]


def test_a_poke_inside_the_noise_floor_is_not_a_break():
    """The median 2-minute wobble is $2.12; a $3 poke past a box is the tape
    breathing, not a break. Past 0.05 sigma ($5 here) it is."""
    quiet = SR.ranges_block(_tape([1500] * 16 + [1503]), T0, 100.0, "2026-07-31")
    assert "breaks_today" not in quiet
    assert quiet["in_force"]["live_spot_is"] == "just above"
    broke = SR.ranges_block(_tape([1500] * 16 + [1506]), T0, 100.0, "2026-07-31")
    assert broke["breaks_today"]["count"] == 1


def test_the_prior_sessions_range_rides_its_own_clock():
    """A multi-day range can hold while today's box breaks, and the block
    states both without joining them."""
    diary = SR._diary_dir()
    diary.mkdir(parents=True, exist_ok=True)
    for d, lo, hi in (("2026-07-29", 1400.0, 1480.0), ("2026-07-30", 1450.0, 1530.0)):
        (diary / f"{d}.jsonl").write_text("\n".join(
            json.dumps({"ts": f"{d}T10:00:00-04:00", "ticker": "SNDK", "spot": v})
            for v in (lo, hi)) + "\n")
    rb = SR.ranges_block(_tape([1500] * 16 + [1560, 1562]), T0, 100.0, "2026-07-31")
    ps = rb["prior_sessions"]
    assert (ps["sessions"], ps["from"], ps["to"]) == (2, "2026-07-29", "2026-07-30")
    assert (ps["low"], ps["high"]) == (1400.0, 1530.0)
    assert ps["live_spot_is"] == "above" and ps["today_traded_beyond_it"] == "above"
    assert ps["todays_opening_box_is"] == "inside"
    inside = SR.ranges_block(_tape([1500] * 16 + [1520, 1522]), T0, 100.0, "2026-07-31")
    ps2 = inside["prior_sessions"]
    assert ps2["live_spot_is"] == "inside" and "today_traded_beyond_it" not in ps2
    assert inside["breaks_today"]["count"] == 1          # today's box broke anyway


def test_box_edges_ride_in_context_and_may_be_pointed_at():
    rows = _tape([1500] * 16 + [1520, 1522])
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    rg = sc["context"]["ranges"]
    at = rg["breaks_today"]["breaks"][0]["at"]
    ok = SR.check_reading_against_scene(
        {"quiet": False,
         "read": f"Price moved up out of the opening box at {at} and is now near 1522.",
         "points": [{"level": 1500.0, "note": "the opening box's edge"}]}, sc)
    assert ok["read"].startswith("Price moved")
    assert ok["points"] == [{"level": 1500.0, "note": "the opening box's edge"}]


# --- the structure block: where the weight sits, and nothing more -------------
def test_structure_says_where_the_weight_sits_and_nothing_else():
    sc = scene_of(rich_row())                    # spot 1200: band 1240-1245 above
    st = sc["structure"]
    band = st["bands"][0]
    assert (band["low"], band["high"], band["side"]) == (1240.0, 1245.0, "above")
    assert 30 < band["share_of_book_gamma_pp"] < 50
    assert [a["side"] for a in st["air"]] == ["above", "below"]
    assert st["air"][0] == {"side": "above", "from": 1200.0, "to": 1240.0}
    assert st["air"][1] == {"side": "below", "from": 1150.0, "to": 1200.0}
    assert 0 < st["weight_above_spot_pp"] < 100
    assert "structure" in SR.PRESENT_TENSE_FORBIDDEN_FOR
    assert "structure" in SR.BUILT_FROM[SR.OI_SNAPSHOT]
    for word in ("attract", "pull", "overpower", "magnet"):
        assert word not in json.dumps(st)


def test_structure_is_absent_not_empty_without_a_surface():
    assert SR.structure_block(mkrow([[1300, 9]]), 1200.0) is None
    assert "structure" not in SR.build_scene(mkrow([[1300, 9]]), SR.magnet_band(mkrow([[1300, 9]])), [],
                                             [mkrow([[1300, 9]])], T0)


# --- bug 4: the three-layer guard -------------------------------------------
def test_inflections_of_a_banned_stem_are_banned():
    for text in ("favoring the upside", "targeting 1500", "expected to hold",
                 "the board is pinning", "leaning higher", "squeezing higher",
                 "rallying into the close", "biased up", "supported at 1465"):
        assert SR.banned_words(text), text
    for text in ("the heaviest strike on the board", "price sits just under 1500",
                 "nothing crossed since the last read", "open air above 1600",
                 "the 1500 strike holds the most gamma"):
        assert SR.banned_words(text) == [], text


def test_judgement_words_die_like_forecasts():
    for text in ("a strong wall at 1600", "1465 is the floor", "the ceiling at 1600",
                 "price is due for a move", "the board looks healthy",
                 "a fragile setup", "the odds of a run", "look for 1550"):
        assert SR.banned_words(text), text


def test_a_backwards_above_or_under_is_caught_against_the_live_spot():
    assert SR.position_slips("still under the 1500 strike", 1528.7) == ["under 1500"]
    assert SR.position_slips("price sits above 1500", 1528.7) == []
    assert SR.position_slips("price sits above 1550", 1528.7) == ["above 1550"]
    assert SR.position_slips("no wall sits above 1600", 1528.7) == []     # about walls
    assert SR.position_slips("the 1600 strike sits above price", 1528.7) == []
    assert SR.position_slips("still under the 1500 strike", None) == []


def test_the_position_check_runs_inside_the_gate():
    sc = scene_of(rich_row(spot=1250.0))         # 1200 is a magnet strike
    r = SR.check_reading_against_scene(
        {"quiet": False, "read": "Price is still under the 1200 strike."}, sc)
    assert "read" not in r
    assert "read_position_contradicts_spot:under 1200" in r["dropped_observations"]
    r2 = SR.check_reading_against_scene(
        {"quiet": False, "read": "Price is now above the 1200 strike."}, sc)
    assert r2["read"].startswith("Price is now above")


def test_the_semantic_reviewer_deletes_a_forecast_in_plain_words():
    """The word list cannot see "looks ready to run"; the reviewer can, and a
    hit deletes the text the way a banned word does — recorded, never silent."""
    sc = scene_of(rich_row())
    judge = lambda texts: ["looks ready to run" if "ready" in t else None for t in texts]
    r = SR.check_reading_against_scene(
        {"quiet": False,
         "read": "The 1300 strike holds the most gamma and price looks ready to run at it.",
         "points": [{"level": 1300.0, "note": "heaviest strike on the board"}]},
        sc, judge=judge)
    assert "read" not in r
    assert r["points"] == [{"level": 1300.0, "note": "heaviest strike on the board"}]
    assert "read_semantic_forecast:looks ready to run" in r["dropped_observations"]


def test_a_reviewer_that_cannot_answer_deletes_nothing():
    sc = scene_of(rich_row())
    r = SR.check_reading_against_scene(
        {"quiet": False, "read": "The 1300 strike holds the most gamma on the board.",
         "points": [{"level": 1300.0, "note": "heaviest strike"}]},
        sc, judge=lambda texts: None)
    assert r["read"].startswith("The 1300") and len(r["points"]) == 1
    assert "semantic_guard_skipped" in r["dropped_observations"]


def test_the_reviewer_parses_the_cli_envelope_and_fails_open(monkeypatch):
    class Ok:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"result": json.dumps({"verdicts": [
            {"i": 0, "forecast": False, "phrase": ""},
            {"i": 1, "forecast": True, "phrase": "should bounce"}]})})
    monkeypatch.setattr(SR.subprocess, "run", lambda *a, **k: Ok())
    assert SR.semantic_review(["a", "b"]) == [None, "should bounce"]

    def boom(*a, **k):
        raise SR.subprocess.TimeoutExpired("claude", 1)
    monkeypatch.setattr(SR.subprocess, "run", boom)
    assert SR.semantic_review(["a"]) is None
    assert SR.semantic_review([]) == []


def test_the_doctrine_publishes_the_judgement_list_and_the_two_checks():
    for w in sorted(SR._BANNED_JUDGEMENT):
        assert w in SR._DOCTRINE
    assert "Every inflection counts" in SR._DOCTRINE
    assert "second reviewer" in SR._DOCTRINE
    assert "`frame_is`" in SR._DOCTRINE and "`context.ranges`" in SR._DOCTRINE
    assert SR.ERA >= "obs-4"          # obs-5 (the bar sidecar) rides on top of these rules


def test_the_memory_slice_remembers_the_frame_the_box_and_the_witness():
    """09-02: the slice carries frame_is, the opening box's status and which witness the
    extremes came from — the columns a later pattern search over boxes will need."""
    import sndk_rag
    rows = _tape([1500] * 16 + [1520, 1522])
    lc = _last_call(minutes_ago=10, spot=1500.0)
    fr = SR.frame_since_last_read(rows[-1], rows, lc, "price ran", False, T0)
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0, since_last_read=fr)
    out = {"wake": "price ran", "era": SR.ERA, "magnet_band": SR.magnet_band(rows[-1]),
           "reading": {"quiet": True, "read": "Price moved up out of the opening box.", "abstain": "chosen"}}
    sndk_rag.record_slice(rows[-1], out, sc, T0)
    rec = json.loads(sndk_rag._slices_path(T0.date().isoformat()).read_text().splitlines()[-1])
    m = rec["meta"]
    assert m["frame_is"] == "a move"
    assert m["opening_box"].startswith("broke up at ")
    assert m["extremes_from"] == "scans_every_2_min"
