"""The tape lane's stretch labels: the move since the last read in tape units against the sum's own
cuts, the range, travel and direction flips placed against the same minutes on the prior sessions in
thirds, the round trip, the unit's own third on the sum's context line, and the lane doc that reads them."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from conftest import DAY, at, bars_from_closes, make_row
from sndk_jev import service
from sndk_jev.ask import build_requests, load_questions, paths_in
from sndk_jev.hour import band_of, load_hour_doc, unit_line
from sndk_jev.lane import TAPE
from sndk_jev.service import last_read_of, run_once
from sndk_jev.state_builder import (TAPE_BIG_UNITS, TAPE_FLAT_UNITS, TAPE_LABELS, build_state, rank_at_slot, tape_unit,
                                    third_band, units_of)

SIGMA = 45.0
HELD_UNIT = 0.4 * SIGMA          # 18.0 before 09:45: held 6.3, big 12.6
LANE_DOC = TAPE.questions
LOGGED_ONLY = ("tape.travel_since_read", "tape.flips_since_read", "tape.round_trip")


def _prior(wicks: list[float], hole_in: str | None = None) -> dict[str, list[dict]]:
    """One flat prior session per wick: the range of any 5-minute window is twice the wick, travel and
    flips are zero. ``hole_in`` drops the 09:37 bar of that session."""
    out = {}
    for k, w in enumerate(wicks):
        day = (date(2026, 9, 17) - timedelta(days=k)).isoformat()
        bars = bars_from_closes([1700.0] * 390, day=day, wick=w)
        if day == hole_in:
            bars = [b for b in bars if not b["ts"].endswith("T09:37:00-04:00")]
        out[day] = bars
    return out


def _window(delta: float = 0.0, wick: float = 0.5, closes: list[float] | None = None, drop_minute: int | None = None) -> list[dict]:
    """Today's bars 09:30 to 09:39: five flat minutes, then the stretch 09:35 to 09:39 ending ``delta``
    above where it started (or the given closes)."""
    window = closes if closes is not None else [1700.0] * 4 + [1700.0 + delta]
    bars = bars_from_closes([1700.0] * 5 + window, wick=wick)
    if drop_minute is not None:
        bars = [b for b in bars if not b["ts"].endswith(f"T09:{drop_minute:02d}:00-04:00")]
    return bars


def _lane(scene_factory, bars, prior, last_read=at(9, 35), now=at(9, 40)):
    return scene_factory(now, bars, prior_bars=prior, bar_clock=True, last_read=last_read)


def _tape(scene):
    state, omitted = build_state(scene)
    return state.get("tape", {}), {k: v for k, v in omitted.items() if k.startswith("tape.")}


@pytest.mark.parametrize("delta, verdict, answer, words", [
    (0.0, "held", "held", "level with it, 0.00 of a tape unit ($18), within the 0.35 cut"),
    (2.0, "held", "held", "$2.0 above it, 0.11 of a tape unit ($18), within the 0.35 cut"),
    (9.0, "rose small", "rose_small", "$9.0 above it, 0.50 of a tape unit ($18), more than the 0.35 cut and no more than 0.7"),
    (15.0, "rose big", "rose_big", "$15.0 above it, 0.83 of a tape unit ($18), more than the 0.7 cut"),
    (-9.0, "fell small", "fell_small", "$9.0 below it, 0.50 of a tape unit ($18), more than the 0.35 cut and no more than 0.7"),
    (-15.0, "fell big", "fell_big", "$15.0 below it, 0.83 of a tape unit ($18), more than the 0.7 cut"),
])
def test_the_move_since_the_last_read_in_tape_units_against_the_sums_cuts(scene_factory, delta, verdict, answer, words):
    """The unit is held at 0.4 sigma before 09:45, so a $9 move is half a unit: small. The sentence
    carries the dollars, the units, the unit itself, the cut passed and the verdict, and ends on the
    words the lane doc's options quote."""
    scene = _lane(scene_factory, _window(delta), _prior([1.0] * 5))
    assert scene.unit["unit_dollars"] == HELD_UNIT and scene.unit["source"] == "held" and "rank" not in scene.unit
    tape, omitted = _tape(scene)
    assert tape["move_since_read"] == f"since the last read, 5 minutes ago, price ended {words}, so it {verdict}"
    assert "tape.move_since_read" not in omitted
    state, _ = build_state(scene)
    # the builder's own verdict, what the A/B harness and the doc test read
    from sndk_jev.state_builder import _run
    assert _run(scene).verdicts["tape.move_since_read"] == answer
    assert (TAPE_FLAT_UNITS, TAPE_BIG_UNITS) == (0.35, 0.7)


def test_the_days_first_read_measures_from_the_open_and_has_no_move(scene_factory):
    scene = _lane(scene_factory, _window(9.0), _prior([1.0] * 5), last_read=None, now=at(9, 35))
    tape, omitted = _tape(scene)
    assert omitted["tape.move_since_read"] == "the day's first read: no earlier read today to measure from"
    assert omitted["tape.round_trip"] == "needs the move since the last read"
    assert tape["range_since_read"].startswith("the range since the open, 5 minutes ago, is $")
    assert "since the open, 5 minutes ago" in tape["travel_since_read"] and "since the open, 5 minutes ago" in tape["flips_since_read"]


def test_the_live_lane_writes_no_tape_labels_and_omits_none(scene_factory):
    scene = scene_factory(at(9, 40), _window(9.0), prior_bars=_prior([1.0] * 5))
    assert not scene.bar_clock and scene.unit is None and scene.last_read is None
    state, omitted = build_state(scene)
    assert "tape" not in state and not any(k.startswith("tape.") for k in omitted)
    assert state["context"]["horizon"] == "the next 30 minutes" and "tape unit" not in state["context"]["units"]
    lane = _lane(scene_factory, _window(9.0), _prior([1.0] * 5))
    lane.horizon = "the next 10 minutes"
    lane_state, _ = build_state(lane)
    assert lane_state["context"]["horizon"] == "the next 10 minutes" and lane_state["context"]["units"].endswith("the labels that use it state it")


@pytest.mark.parametrize("wick, band, beat, answer", [(20.0, "top third", 19, "wide"), (10.0, "middle third", 9, "usual"), (0.5, "bottom third", 0, "narrow")])
def test_the_range_is_placed_against_the_same_minutes_on_the_prior_sessions_in_thirds(scene_factory, wick, band, beat, answer):
    """Twenty flat prior sessions whose 09:35 to 09:39 range runs 2, 4, ... 40 dollars; today's range
    is twice the wick. The sentence says the third and the count, band first, as volume.now does."""
    scene = _lane(scene_factory, _window(0.0, wick=wick), _prior(list(range(1, 21))))
    tape, omitted = _tape(scene)
    assert tape["range_since_read"] == (f"the range since the last read, 5 minutes ago, is ${2 * wick:.1f}, {units_of(2 * wick / HELD_UNIT, HELD_UNIT)}, "
                                        f"in the {band} for this minute, higher than {beat} of 20 prior sessions")
    from sndk_jev.state_builder import _run
    assert _run(scene).verdicts["tape.range_since_read"] == answer
    assert not omitted


def test_travel_and_flips_are_counted_from_the_close_before_the_window_and_ranked_the_same_way(scene_factory):
    prior = _prior(list(range(1, 21)))                                      # flat: travel 0, flips 0 on every prior session
    still = _lane(scene_factory, _window(0.0), prior)
    tape, _ = _tape(still)
    assert tape["travel_since_read"] == "price travelled $0.0 since the last read, 5 minutes ago, 0.00 of a tape unit ($18), in the bottom third for this minute, higher than 0 of 20 prior sessions"
    assert tape["flips_since_read"] == "the 1-minute direction never changed since the last read, 5 minutes ago, in the bottom third for this minute, higher than 0 of 20 prior sessions"
    zigzag = _lane(scene_factory, _window(closes=[1702.0, 1700.0, 1702.0, 1700.0, 1702.0]), prior)   # steps +2 -2 +2 -2 +2 from the 09:34 close
    tape, _ = _tape(zigzag)
    assert tape["travel_since_read"].startswith("price travelled $10.0 since the last read, 5 minutes ago, 0.56 of a tape unit ($18), in the top third for this minute, higher than 20 of 20")
    assert tape["flips_since_read"] == "the 1-minute direction changed 4 times since the last read, 5 minutes ago, in the top third for this minute, higher than 20 of 20 prior sessions"
    once = _lane(scene_factory, _window(closes=[1702.0, 1704.0, 1703.0, 1702.0, 1701.0]), prior)      # up, up, then down: one change
    assert _tape(once)[0]["flips_since_read"].startswith("the 1-minute direction changed once since the last read")
    from sndk_jev.state_builder import _run
    v = _run(zigzag).verdicts
    assert (v["tape.travel_since_read"], v["tape.flips_since_read"]) == ("churning", "many")
    v = _run(still).verdicts
    assert (v["tape.travel_since_read"], v["tape.flips_since_read"]) == ("clean", "few")


def test_under_five_prior_sessions_the_ranks_are_omitted_but_the_move_is_not(scene_factory):
    scene = _lane(scene_factory, _window(9.0), _prior([1.0] * 4))
    tape, omitted = _tape(scene)
    assert tape["move_since_read"].endswith("so it rose small") and set(tape) == {"move_since_read"}
    for key in ("range_since_read", "travel_since_read", "flips_since_read"):
        assert omitted[f"tape.{key}"] == "needs 5 prior sessions of bars at these minutes, have 4"
    assert omitted["tape.round_trip"] == "needs the range since the last read placed against the prior sessions"


def test_a_prior_session_with_a_hole_in_the_window_leaves_the_baseline(scene_factory):
    scene = _lane(scene_factory, _window(0.0, wick=20.0), _prior([1.0] * 6, hole_in="2026-09-15"))
    tape, omitted = _tape(scene)
    assert tape["range_since_read"].endswith("in the top third for this minute, higher than 5 of 5 prior sessions")
    assert not omitted


def test_a_hole_in_todays_window_omits_the_ranks_and_keeps_the_move(scene_factory):
    scene = _lane(scene_factory, _window(9.0, drop_minute=37), _prior([1.0] * 5))
    tape, omitted = _tape(scene)
    assert set(tape) == {"move_since_read"} and tape["move_since_read"].endswith("so it rose small")
    for key in TAPE_LABELS[1:]:
        assert omitted[f"tape.{key}"] == "bars missing since the last read, 5 minutes ago: 4 of 5 minutes"


def test_no_bars_since_the_last_read_or_no_unit_omits_everything(scene_factory):
    bars = bars_from_closes([1700.0] * 5)                                   # 09:30 .. 09:34 only
    scene = _lane(scene_factory, bars, _prior([1.0] * 5), last_read=at(9, 35), now=at(9, 40))
    tape, omitted = _tape(scene)
    assert tape == {} and all(omitted[f"tape.{k}"] == "no finished bars since the last read, 5 minutes ago" for k in TAPE_LABELS)
    stopped = _lane(scene_factory, bars_from_closes([1700.0] * 30), _prior([1.0] * 5), last_read=at(10, 5), now=at(10, 10))
    assert stopped.unit is None                                              # the newest slice has no bars: ruler() gives no unit
    stopped.bars = bars_from_closes([1700.0] * 36)                           # bars again, but the unit was measured without them
    tape, omitted = _tape(stopped)
    assert tape == {} and all(omitted[f"tape.{k}"] == "no tape unit this read: the bars have stopped" for k in TAPE_LABELS)


def test_the_round_trip_is_a_wide_stretch_that_ended_where_it_started(scene_factory):
    prior = _prior(list(range(1, 21)))
    yes = _lane(scene_factory, _window(0.0, wick=20.0), prior)                # range $40, top third; ended level
    tape, _ = _tape(yes)
    assert tape["round_trip"] == ("a round trip: the range since the last read, 5 minutes ago, is in the top third for this minute "
                                  "yet price ended within 0.35 of a tape unit of the last read")
    moved = _lane(scene_factory, _window(9.0, wick=20.0), prior)               # wide, but it went somewhere
    assert _tape(moved)[0]["round_trip"] == ("not a round trip: the range since the last read, 5 minutes ago, is in the top third for this minute "
                                             "and price ended 0.50 of a tape unit above the last read")
    quiet = _lane(scene_factory, _window(0.0), prior)                          # level, but nothing happened
    assert _tape(quiet)[0]["round_trip"] == ("not a round trip: the range since the last read, 5 minutes ago, is in the bottom third for this minute "
                                             "and price ended within 0.35 of a tape unit of the last read")
    from sndk_jev.state_builder import _run
    assert _run(yes).verdicts["tape.round_trip"] == "true" and _run(moved).verdicts["tape.round_trip"] == "false"


def test_rank_at_slot_and_the_thirds():
    assert rank_at_slot(5.0, [1.0, 2.0, 3.0, 4.0]) is None                     # four sessions is not a history
    assert rank_at_slot(5.0, [1.0, 2.0, 3.0, 4.0, 6.0]) == {"band": "top third", "higher_than": 4, "of": 5}
    assert rank_at_slot(2.5, [1.0, 2.0, 3.0, 4.0, 6.0]) == {"band": "middle third", "higher_than": 2, "of": 5}
    assert rank_at_slot(0.0, [1.0, 2.0, 3.0, 4.0, 6.0]) == {"band": "bottom third", "higher_than": 0, "of": 5}
    assert rank_at_slot(3.0, [3.0] * 5)["higher_than"] == 0                    # equal is not higher
    assert [third_band(x) for x in (0.0, 1 / 3, 0.5, 2 / 3, 0.7, 1.0)] == ["bottom third", "middle third", "middle third", "middle third", "top third", "top third"]


def test_the_units_own_third_rides_on_the_sums_context_line():
    """From 09:45 the unit is measured, so it can be placed against the prior sessions' units at the
    same minute; held before 09:45, every day's unit is the same number and there is nothing to rank."""
    prior = _prior(list(range(1, 21)))                                          # prior units 2, 4, ... 40 at any minute
    today = bars_from_closes([1700.0] * 30, wick=5.0)                           # every slice ranges $10: unit 10, measured
    unit = tape_unit(today, SIGMA, at(10, 0), prior)
    assert unit["source"] == "tape" and unit["unit_dollars"] == 10.0
    assert unit["rank"] == {"band": "bottom third", "higher_than": 4, "of": 20}
    doc = load_hour_doc(lane=TAPE)
    assert unit_line(unit, band_of(unit, doc, "next_10")) == ("one tape unit is $10, in the bottom third for this minute, wider than 4 of 20 prior sessions; "
                                                             "flat is within $4 either way (0.35 of a unit); small is $4 to $7; big is more than $7 (0.7 of a unit)")
    assert "rank" not in tape_unit(today[:10], SIGMA, at(9, 40), prior)         # held
    assert "rank" not in tape_unit(today, SIGMA, at(10, 0), _prior([1.0] * 4))  # too few sessions
    wide = tape_unit(bars_from_closes([1700.0] * 30, wick=25.0), SIGMA, at(10, 0), prior)
    assert wide["rank"] == {"band": "top third", "higher_than": 20, "of": 20}
    from sndk_jev.hour import hour_request
    req = hour_request({"q": "a sentence"}, lane=TAPE, ruler=unit)
    assert "in the bottom third for this minute" in req["state"]["context"]["unit"]


# ---- the lane doc

def test_the_lane_doc_reads_the_move_and_the_range_and_quotes_the_builders_words(scene_factory):
    doc = load_questions(LANE_DOC)
    tape = next(g for g in doc["groups"] if g["id"] == "tape")
    assert tape["reads"] == ["context", "tape.move_since_read", "tape.range_since_read"]
    move, rng = tape["questions"]["move_since_read"], tape["questions"]["range_since_read"]
    assert move["status"] == "live" and rng["status"] == "live"
    assert list(move["criteria"]) == ["rose_big", "rose_small", "held", "fell_small", "fell_big", "unsure"]
    assert list(rng["criteria"]) == ["wide", "usual", "narrow", "unsure"]
    # the sum's own words never appear in the move's options or criteria
    for text in list(move["criteria"]) + list(move["criteria"].values()) + [move["instructions"]]:
        assert not any(w in text for w in ("up_big", "up_small", "down_small", "down_big", "flat"))
    # every option's ending phrase is the one the builder writes for that band
    for delta, opt in ((15.0, "rose_big"), (9.0, "rose_small"), (0.0, "held"), (-9.0, "fell_small"), (-15.0, "fell_big")):
        sentence = _tape(_lane(scene_factory, _window(delta), _prior([1.0] * 5)))[0]["move_since_read"]
        phrase = f"so it {opt.replace('_', ' ')}"
        assert sentence.endswith(phrase) and f"'{phrase}'" in move["criteria"][opt]
    for opt, band in (("wide", "top third"), ("usual", "middle third"), ("narrow", "bottom third")):
        assert f"in the {band} for this minute" in rng["criteria"][opt]
    hour = load_hour_doc(lane=TAPE)["horizons"]["next_10"]
    assert (hour["flat_band_units"], hour["big_band_units"]) == (TAPE_FLAT_UNITS, TAPE_BIG_UNITS)
    for cut in (f"the {TAPE_FLAT_UNITS} cut", f"the {TAPE_BIG_UNITS} cut"):
        assert any(cut in c for c in move["criteria"].values())
    # every question's paths are read by its group, and the logged-only measures by nobody
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            assert set(paths_in(q)) <= set(g["reads"]), (g["id"], qid)
            assert not any(p in LOGGED_ONLY for p in paths_in(q)), qid
    assert set(doc["logged_only"]) == set(LOGGED_ONLY)


def test_the_copied_row_questions_match_the_live_doc_word_for_word():
    live = load_questions(TAPE.questions.parent / "sndk_pro.json")
    live_by = {qid: (g, q) for g in live["groups"] for qid, q in g["questions"].items()}
    doc = load_questions(LANE_DOC)
    copies = [g for g in doc["groups"] if g.get("copied_from")]
    assert [list(g["questions"]) for g in copies] == [["weight_side"], ["wall_return"], ["two_day_position"], ["iv_skew"]]
    for g in copies:
        for qid, q in g["questions"].items():
            src_group, src = live_by[qid]
            assert {k: v for k, v in q.items() if k != "copied_from"} == src, qid
            assert src["status"] == "live" and g["id"] == src_group["id"] and set(g["reads"]) <= set(src_group["reads"])


def test_the_packer_sends_the_move_and_the_range_and_never_the_logged_measures(scene_factory):
    doc = load_questions(LANE_DOC)
    state, _ = build_state(_lane(scene_factory, _window(9.0), _prior([1.0] * 5)))
    requests, skipped = build_requests(state, doc)
    tape = next(r for r in requests if r["id"] == "tape")
    assert set(tape["state"]["tape"]) == {"move_since_read", "range_since_read"} and set(tape["questions"]) == {"move_since_read", "range_since_read"}
    assert "tape" not in skipped
    first, _ = build_state(_lane(scene_factory, _window(9.0), _prior([1.0] * 5), last_read=None, now=at(9, 35)))
    requests, skipped = build_requests(first, doc)
    assert skipped["tape"] == {"move_since_read": "missing tape.move_since_read"}
    assert set(next(r for r in requests if r["id"] == "tape")["questions"]) == {"range_since_read"}


# ---- the service: the last read comes from the lane's own records

def _state(tmp_path, n_bars):
    (tmp_path / "sndk_reversion").mkdir(exist_ok=True)
    (tmp_path / "sndk_bars").mkdir(exist_ok=True)
    (tmp_path / "sndk_reversion" / f"{DAY}.jsonl").write_text(json.dumps(make_row(at(9, 33, ss=10), 1700.0)) + "\n")
    closes = [1700.0 + (2.0 if 10 <= i < 15 else 0.0) for i in range(n_bars)]     # 09:40 to 09:44 sit $2 higher
    (tmp_path / "sndk_bars" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(b) for b in bars_from_closes(closes, wick=3.0)) + "\n")
    for d, bars in _prior([1.0] * 5).items():
        (tmp_path / "sndk_bars" / f"{d}.jsonl").write_text("\n".join(json.dumps(b) for b in bars) + "\n")
    return tmp_path


def test_last_read_of_reads_the_lanes_own_records(tmp_path):
    assert last_read_of(tmp_path, DAY, at(9, 40)) is None
    (tmp_path / f"{DAY}.jsonl").write_text("\n".join([
        json.dumps({"row_ts": at(9, 35).isoformat()}), "not json", json.dumps({"no": "stamp"}),
        json.dumps({"row_ts": at(9, 40).isoformat()}), json.dumps({"row_ts": at(9, 45).isoformat()})]) + "\n")
    assert last_read_of(tmp_path, DAY, at(9, 45)) == at(9, 40)                  # the newest before now, never now itself
    assert last_read_of(tmp_path, DAY, at(9, 35)) is None


def test_the_second_read_of_the_day_measures_from_the_first(tmp_path):
    state = _state(tmp_path, 10)                                                 # bars to 09:39: the read is stamped 09:40
    doc = load_questions(LANE_DOC)
    out = state / "jev" / "lanes" / "tape"
    c1 = run_once(state, out, doc, False, DAY, lane=TAPE)
    assert c1["row_ts"] == at(9, 40).isoformat()
    rec1 = json.loads((out / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert rec1["omitted"]["tape.move_since_read"] == "the day's first read: no earlier read today to measure from"
    assert rec1["state"]["tape"]["range_since_read"].startswith("the range since the open, 10 minutes ago, is $")
    assert rec1["state"]["context"]["horizon"] == "the next 10 minutes"
    _state(tmp_path, 15)                                                         # bars to 09:44: the next read is stamped 09:45
    c2 = run_once(state, out, doc, False, DAY, lane=TAPE)
    assert c2["row_ts"] == at(9, 45).isoformat()
    rec2 = json.loads((out / f"{DAY}.jsonl").read_text().splitlines()[1])
    # at 09:45 the unit is measured from the tape: every slice ranges $6 (a $3 wick), and it is placed
    # against the five prior sessions' $2 units at this minute
    assert rec2["state"]["tape"]["move_since_read"] == ("since the last read, 5 minutes ago, price ended $2.0 above it, 0.33 of a tape unit ($6), "
                                                        "within the 0.35 cut, so it held")
    assert rec2["ruler"]["source"] == "tape" and rec2["ruler"]["unit_dollars"] == 6.0
    assert rec2["ruler"]["rank"] == {"band": "top third", "higher_than": 5, "of": 5}
    assert rec1["ruler"]["source"] == "held" and "rank" not in rec1["ruler"]
    # the card carries what the phone's strip shows: the bands in dollars and the stretch sentences
    assert c2["band"] == rec2["band"] == {"flat_dollars": 2.1, "big_dollars": 4.2, "flat_units": 0.35, "big_units": 0.7}
    assert c2["stretch"] == rec2["state"]["tape"] and c2["stretch"]["move_since_read"].endswith("so it held")
    assert c1["stretch"] == rec1["state"]["tape"] and "move_since_read" not in c1["stretch"]
    assert c1["omitted"]["tape.move_since_read"].startswith("the day's first read")
    from sndk_jev.lane import LIVE
    live = run_once(state, state / "jev", load_questions(LIVE.questions), False, DAY, lane=LIVE)
    assert not ({"lane", "ruler", "band", "stretch"} & set(live))           # the live card is as it was
    assert [q["id"] for q in c2["questions"] if q["id"] == "move_since_read"] == ["move_since_read"]
