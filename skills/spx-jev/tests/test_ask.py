"""The packer, the question templates and the sender."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from spx_jev.ask import (GROUP_CAP, build_requests, confidence, constants_named, get_path, load_questions, paths_in, pick,
                         summarize)
from spx_jev.cuts import QUESTION_CONSTANTS
from spx_jev.hour import PER_READ, average_request, average_window, load_hour_doc
from spx_jev.labels.registry import build_labels
from spx_jev.lane import LANES
from spx_jev.schedule import not_due

QUESTIONS = Path(__file__).resolve().parent.parent / "questions"
SHIPPED = sorted(QUESTIONS.glob("*.json"))
# a number followed by a unit a threshold is written in: none may be typed into a doc by hand
TYPED_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|sigma|vol points?|times|of a (?:tape )?unit|tape units?)")
NAMED = re.compile(r"\{[^{}]*\}")
# what a premarket question may cut on: rank bands, how many nights it ranks against, the half-way share and the
# one-to-one line (definitions, not lines), and window lengths; anything else is a fixed line (premarket-data-plan section 3)
RANK_CONSTANTS = {"third_lo", "third_hi", "top_fifth", "bottom_fifth", "night_rank_count", "overnight_rank_min_nights",
                  "same_clock_min_sessions", "gap_half_share", "one_ratio"}
# the owner's rule (2026-09-27): no question JEV is asked sizes or judges a market measure on a fixed line; every
# number its text names is one of these kinds, and a size is the measure's rank against the same clock on recent sessions
DEFINITIONS = {
    "rank band edges": {"third_lo", "third_hi", "top_fifth", "bottom_fifth", "half_rank", "skew_steep_rank", "skew_flat_rank",
                        "flow_lean_rank", "tick_burst_pct", "thin_volume_pct", "big_print_pct", "wall_touch_siege_percentile"},
    "lookbacks and counts": {"night_rank_count", "overnight_rank_min_nights", "same_clock_min_sessions", "big_min_prints",
                             "defense_min_events", "open_cluster_min", "tick_cluster", "mega_count", "spread_count"},
    "clocks and calendar": {"accept_minutes", "cross_lookback_min", "event_due_min", "event_digest_min", "speaker_window_min",
                            "shock_lookback_min", "shock_fresh_min", "one_day", "opex_week_days", "after_opex_days",
                            "rebal_window_days", "month_turn_days"},
    "shares that define a word": {"gap_half_share", "giveback_third", "range_top_share", "range_bottom_share", "move_burst_share",
                                  "mega_one_name_share", "one_name_share", "stress_retreat_share", "stress_hold_share",
                                  "one_ratio"},
    "touch tolerances": {"gap_touch_sigma", "ib_break_sigma", "value_edge_sigma", "level_reach_sigma",
                         "reaction_extend_sigma", "settle_seat_sigma", "outside_buffer_sigma", "vwap_touch_sigma",
                         "round_near_sigma"},
    # an inverted curve, SPY's one-cent tick, and the morning brief's own scale are not sizes of a market move
    "fixed by what they mean": {"vix_curve_flat", "spy_spread_tight", "brief_dir_min", "brief_conf_min"},
    # a break past a heavy strike is too rare to give a same-minute history to rank how far past against
    "no history to rank against": {"wall_touch_sigma"},
}


def _texts(value):
    """Every string inside a question field."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)


def _shipped_questions():
    for path in SHIPPED:
        raw = json.loads(path.read_text())
        for g in raw.get("groups") or [{"questions": raw["questions"]}]:
            for qid, q in g["questions"].items():
                yield path, qid, q


def test_paths_in_reads_backticks():
    q = {"instructions": "Read `price.recent_move` and `gex`. Now?", "criteria": {"a": "see `iv.term_structure`"}}
    assert paths_in(q) == ["price.recent_move", "gex", "iv.term_structure"]


def test_get_path_treats_empty_group_as_missing():
    assert get_path({"price": {}}, "price") is None
    assert get_path({"price": {"x": "y"}}, "price.x") == "y"
    assert get_path({"price": {"x": "y"}}, "price.z") is None


def test_requests_carry_only_the_slice_and_skip_questions_with_missing_labels():
    doc = {"groups": [
        {"id": "a", "reads": ["context", "price.recent_move", "breadth.tick_lean"],
         "questions": {"q1": {"type": "noul", "instructions": "Read `price.recent_move`.", "criteria": {"true": "t", "false": "f"}},
                       "q2": {"type": "noul", "instructions": "Read `breadth.tick_lean`.", "criteria": {"true": "t", "false": "f"}}}},
        {"id": "b", "reads": ["breadth"],
         "questions": {"q3": {"type": "noul", "instructions": "Read `breadth`.", "criteria": {"true": "t", "false": "f"}}}},
    ]}
    state = {"context": {"symbol": "SPX"}, "price": {"recent_move": "rose", "vs_vwap": "above"}, "gex": {"x": "y"}}
    reqs, skipped = build_requests(state, doc)
    assert [r["id"] for r in reqs] == ["a"]
    assert reqs[0]["state"] == {"context": {"symbol": "SPX"}, "price": {"recent_move": "rose"}}
    assert list(reqs[0]["questions"]) == ["q1"]
    assert skipped == {"a": {"q2": "missing breadth.tick_lean"}, "b": {"q3": "missing breadth", "*": "nothing to ask in this group this read"}}


def test_a_label_the_group_does_not_read_is_named_not_called_missing():
    doc = {"groups": [{"id": "a", "reads": ["context", "price.recent_move"],
                       "questions": {"q1": {"type": "noul", "instructions": "Read `price.vs_vwap`.", "criteria": {"true": "t", "false": "f"}}}}]}
    state = {"context": {"symbol": "SPX"}, "price": {"recent_move": "rose", "vs_vwap": "above"}}
    reqs, skipped = build_requests(state, doc)
    assert reqs == [] and skipped["a"]["q1"] == "the group does not read price.vs_vwap"


def test_a_gated_question_is_asked_only_when_its_gate_says_awake():
    doc = {"groups": [{"id": "a", "reads": ["context"], "questions": {
        q: {"type": "noul", "sleep_when": "nothing happened", "instructions": "Read `context.symbol`.", "criteria": {"true": "t", "false": "f"}}
        for q in ("awake", "asleep", "undecided")}}]}
    reqs, skipped = build_requests({"context": {"symbol": "SPX"}}, doc, gates={"awake": None, "asleep": "no shock in the last hour"})
    assert list(reqs[0]["questions"]) == ["awake"]
    assert skipped["a"] == {"asleep": "asleep: no shock in the last hour", "undecided": "asleep: no label family decides its gate"}


def test_a_dark_question_is_never_asked():
    doc = {"groups": [{"id": "a", "reads": ["context"], "questions": {
        "q": {"status": "dark", "type": "noul", "instructions": "Read `context.symbol`.", "criteria": {"true": "t", "false": "f"}}}}]}
    reqs, skipped = build_requests({"context": {"symbol": "SPX"}}, doc)
    assert reqs == [] and skipped["a"]["q"].startswith("dark:")


# ---- the templates

def test_every_shipped_doc_loads_with_its_constants_filled():
    for lane in LANES.values():
        questions = [q for g in load_questions(lane.questions, lane.key)["groups"] for q in g["questions"].values()]
        hour = load_hour_doc(lane=lane)
        questions += [q for qid, q in hour["questions"].items() if qid != lane.average]
        # the average-price sum's window is filled at each read: it is checked as it is sent, a 28-minute window here
        questions += list(average_request({}, average_window(28, 5.0), hour, lane=lane)["questions"].values())
        left = [t for q in questions for t in _texts(q) if NAMED.search(t)]
        assert not left, f"lane {lane.name} kept an unfilled name: {left[:2]}"
    assert {p.name for p in SHIPPED} == {lane.questions.name for lane in LANES.values()} | {lane.hour_doc.name for lane in LANES.values()}


def test_no_threshold_is_typed_into_a_doc_by_hand():
    for path, qid, q in _shipped_questions():
        for key in ("ask", "instructions", "criteria"):
            for text in _texts(q.get(key)):
                hits = TYPED_NUMBER.findall(NAMED.sub("", text))
                assert not hits, f"{path.name} {qid}.{key} types {hits} by hand; name the constant in braces"


def test_every_name_a_doc_asks_for_is_a_constant_the_code_defines():
    for path, qid, q in _shipped_questions():
        for key in ("ask", "instructions", "criteria"):
            for text in _texts(q.get(key)):
                for name in constants_named(text):
                    # the average-price sum's window length is the one name filled at each read, from its window
                    ok = name in QUESTION_CONSTANTS or (name in PER_READ and qid in {lane.average for lane in LANES.values()})
                    assert ok, f"{path.name} {qid} names {name}, which cuts.py does not define"


def test_an_unknown_constant_stops_the_load(tmp_path):
    doc = {"groups": [{"id": "g", "reads": [], "questions": {"q": {"type": "noul", "instructions": "Read `price.recent_move`.",
                                                                   "criteria": {"true": "moved more than {no_such_cut} sigma", "false": "f"}}}}]}
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="no_such_cut"):
        load_questions(p)


def test_a_filled_constant_is_the_code_number_and_a_format_spec_is_honoured(tmp_path):
    doc = {"groups": [{"id": "g", "reads": [], "questions": {"q": {"type": "noul", "instructions": "Read `gex.weight_side`.",
                                                                   "criteria": {"true": "more than {even_split_high:.0%} above, {move_rule_sigma} sigma", "false": "f"}}}}]}
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(doc))
    q = load_questions(p)["groups"][0]["questions"]["q"]
    assert q["criteria"]["true"] == f"more than 60% above, {QUESTION_CONSTANTS['move_rule_sigma']} sigma"


def _spec_paths():
    spec = Path(__file__).resolve().parent.parent / "spec"
    engine = json.loads((spec / "labels.json").read_text())["labels"]
    question_set = json.loads((spec / "question_set.json").read_text())["labels"]
    return {lab["path"] for lab in engine} | {lab["name"] for lab in question_set}


def test_every_question_reads_only_labels_the_spec_knows():
    known = _spec_paths()
    groups = {p.split(".")[0] for p in known}
    for lane in LANES.values():
        for g in load_questions(lane.questions, lane.key)["groups"]:
            for qid, q in g["questions"].items():
                for p in paths_in(q):
                    assert p in known or p in groups, f"{qid} reads {p}, which no label in spec/labels.json provides"


def test_a_full_read_asks_every_question_due_awake_and_with_its_labels(full_scene):
    """At the 12:32 read every live and shadow question its schedule asks is asked, unless it sleeps or a
    label it reads is not written; each question left out says which of those it was."""
    labels = build_labels(full_scene)
    lane = LANES["live"]
    doc = load_questions(lane.questions, lane.key)
    skip = not_due(doc, lane, full_scene.now)
    reqs, skipped = build_requests(labels.state, doc, skip=skip, gates=labels.gates)
    asked = {qid for r in reqs for qid in r["questions"]}
    expected = {qid for g in doc["groups"] for qid, q in g["questions"].items()
                if q["status"] in ("live", "shadow") and qid not in skip and (not q.get("sleep_when") or labels.gates.get(qid, "") is None)
                and all(get_path(labels.state, p) is not None for p in paths_in(q))}
    assert asked == expected and {"price_move_5way", "leg_vs_day_side"} <= asked
    kinds = ("dark:", "not on its schedule", "asked on its other lane", "held from its other lane", "a day constant", "asleep:", "missing ")
    assert all(why.startswith(kinds) for g in skipped.values() for qid, why in g.items() if qid != "*"), skipped
    for r in reqs:
        assert r["state"]["context"]["symbol"] == "SPX"
        for q in r["questions"].values():
            assert set(q) <= {"type", "instructions", "criteria"}, "only JEV's own fields may be sent"


# ---- the sender

def test_send_turns_every_network_failure_into_a_scrubbed_runtime_error(monkeypatch):
    import urllib.error
    from spx_jev import ask
    key = "apikey_" + "a" * 24 + "_" + "b" * 24
    calls, naps = [], []

    def urlopen_timeout(req, timeout=0):
        calls.append("t")
        raise TimeoutError("timed out")
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen_timeout)
    monkeypatch.setattr(ask.time, "sleep", naps.append)
    with pytest.raises(RuntimeError) as e:
        ask.send({"id": "g", "state": {}, "questions": {}}, api_key=key)
    assert "unreachable" in str(e.value) and "after 3 tries" in str(e.value) and "TimeoutError" in str(e.value) and key not in str(e.value)
    assert calls == ["t", "t", "t"] and naps == [4.0], "a timeout is retried at once, then once more a few seconds on"

    class Body:
        def read(self):
            return f"bad request, your key {key} is wrong".encode()

        def close(self):
            pass

    def urlopen_http(req, timeout=0):
        raise urllib.error.HTTPError("u", 400, "bad", {}, Body())
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen_http)
    with pytest.raises(RuntimeError) as e:
        ask.send({"id": "g", "state": {}, "questions": {}}, api_key=key)
    assert "HTTP 400" in str(e.value) and "[key redacted]" in str(e.value) and key not in str(e.value)


@pytest.mark.parametrize("code, headers, waited", [(529, {}, 1.0), (429, {"Retry-After": "2"}, 2.0),
                                                    (503, {"Retry-After": "30"}, 2.0), (500, {"Retry-After": "soon"}, 1.0)])
def test_an_overloaded_or_rate_limited_jev_is_retried_once_after_a_short_pause(monkeypatch, code, headers, waited):
    import urllib.error
    from spx_jev import ask
    calls, naps = [], []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"answers": {}}'

    class Body:
        def read(self):
            return b"busy"

        def close(self):
            pass

    def urlopen(req, timeout=0):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", code, "busy", headers, Body())
        return Answer()
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ask.time, "sleep", naps.append)
    assert ask.send({"id": "g", "state": {}, "questions": {}}, api_key="k") == {"answers": {}}
    assert len(calls) == 2 and naps == [waited]


class _Answer:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"answers": {}}'


class _Busy:
    def read(self):
        return b"busy"

    def close(self):
        pass


@pytest.mark.parametrize("code, headers, waited", [(529, {}, [1.0, 4.0]), (503, {"Retry-After": "30"}, [2.0, 5.0]),
                                                    (429, {"Retry-After": "0"}, [3.0])])
def test_a_jev_still_failing_after_the_first_retry_is_tried_once_more_a_few_seconds_on(monkeypatch, code, headers, waited):
    import urllib.error
    from spx_jev import ask
    calls, naps = [], []

    def urlopen(req, timeout=0):
        calls.append(req)
        if len(calls) < 3:
            raise urllib.error.HTTPError("u", code, "busy", headers, _Busy())
        return _Answer()
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ask.time, "sleep", naps.append)
    assert ask.send({"id": "g", "state": {}, "questions": {}}, api_key="k") == {"answers": {}}
    assert len(calls) == 3 and naps == waited


class _Clock:
    """time.monotonic and time.sleep for ask.send on one fake clock, a try that times out costing its timeout."""
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_a_retry_is_made_only_when_it_can_end_by_the_deadline(monkeypatch):
    from spx_jev import ask
    clock, calls = _Clock(), []

    def urlopen(req, timeout=0):
        calls.append(clock.t)
        clock.t += timeout
        raise TimeoutError("timed out")
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ask, "time", clock)
    with pytest.raises(RuntimeError, match="after 2 tries"):
        ask.send({"id": "g", "state": {}, "questions": {}}, api_key="k", deadline=25.0)
    assert calls == [0.0, 10.0], "the last retry would end at 34 s, past the 25 s deadline"


def test_a_read_whose_every_request_times_out_still_ends_inside_a_minute(monkeypatch):
    """The worst read: every group request and the sums time out on every try. The groups get all three
    tries inside SEND_GROUPS_S, the sums what is left of SEND_READ_S, and the read ends under 60 s."""
    from spx_jev import ask, service
    clock, tries = _Clock(), {}

    def urlopen(req, timeout=0):
        rid = json.loads(req.data)["questions"]
        tries.setdefault(next(iter(rid)), []).append(clock.t)
        clock.t += timeout
        raise TimeoutError("timed out")
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ask, "time", clock)
    out = ask.send_all([{"id": "g", "state": {}, "questions": {"q": {}}}], api_key="k", deadline=service.SEND_GROUPS_S)
    assert "after 3 tries" in out["g"]["error"] and tries["q"] == [0.0, 10.0, 24.0]
    with pytest.raises(RuntimeError, match="after 2 tries"):
        ask.send({"id": "hour", "state": {}, "questions": {"next_10": {}}}, api_key="k", deadline=service.SEND_READ_S)
    assert tries["next_10"] == [34.0, 44.0] and clock.t < 60.0


def test_send_all_sends_every_request_together_and_keeps_errors():
    from spx_jev.ask import send_all

    def fake(req, api_key=None, timeout=10.0, deadline=None):
        if req["id"] == "slow":
            raise TimeoutError("handshake operation timed out")
        return {"model": "jev-1", "answers": {q: {"type": "noul", "noul": 0.9} for q in req["questions"]}}
    out = send_all([{"id": "slow", "questions": {}}, {"id": "ok", "questions": {"q": {}}}], sender=fake)
    assert out["ok"]["answers"]["q"]["noul"] == 0.9
    assert out["slow"] == {"error": "TimeoutError: handshake operation timed out"}
    assert send_all([], sender=fake) == {}


def test_the_answer_readers_and_the_summary():
    ans = {"answers": {
        "a": {"type": "noul", "noul": 0.81},
        "b": {"type": "choice", "choice": "up", "probabilities": {"up": 0.7, "down": 0.3}, "confidence": 0.55},
        "c": {"type": "score", "score": 1.4, "confidence": 0.6, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}},
    }}
    assert pick(ans["answers"]["a"]) == "true" and round(confidence(ans["answers"]["a"]), 2) == 0.62
    assert pick(ans["answers"]["b"]) == "up" and confidence(ans["answers"]["b"]) == 0.55
    assert pick(ans["answers"]["c"]) == "2" and confidence(ans["answers"]["c"]) == 0.6
    lines = summarize(ans)
    assert lines[0] == "a: yes 0.81" and lines[1].startswith("b: up 0.70  confidence 0.55") and lines[2].startswith("c: score 1.40")


def test_each_lane_asks_its_share_of_one_doc_with_its_own_schedule_and_horizon():
    live = {qid: q for g in load_questions(LANES["live"].questions, "thirty_minute")["groups"] for qid, q in g["questions"].items()}
    tape = {qid: q for g in load_questions(LANES["tape"].questions, "opening_five_minute")["groups"] for qid, q in g["questions"].items()}
    premarket = {qid: q for g in load_questions(LANES["premarket"].questions, "premarket")["groups"] for qid, q in g["questions"].items()}
    assert len(live) == 104 and len(tape) == 28 and len(premarket) == 12 and "gap_size" not in live and "price_move_5way" not in tape
    assert tape["vix_stir"]["schedule"] == {"every_min": 5, "from": "09:40", "to": "10:30"} and tape["vix_stir"]["horizon"] == "10min_opening"
    assert live["vix_stir"]["schedule"] == {"every_min": 30, "from": "10:02", "to": "15:32"} and live["vix_stir"]["horizon"] == "30min"
    assert tape["open_vs_prior_range"]["schedule"] == {"at": ["09:35"], "hold": True} and live["open_vs_prior_range"]["schedule"] == {"hold_until": "11:32"}
    assert live["vix_stir"]["instructions"] == tape["vix_stir"]["instructions"]
    assert "overnight_move_vs_expected" not in live and "overnight_move_vs_expected" not in tape    # asked only before the open
    assert premarket["overnight_move_vs_expected"]["schedule"] == {"at": ["09:28"]} and not set(premarket) & (set(live) | set(tape))


def test_the_premarket_lane_asks_jev_only_at_its_0848_and_0928_checkpoints():
    """Every checkpoint builds its labels; JEV is asked only at the reads a question's schedule names."""
    lane = LANES["premarket"]
    premarket = [q for g in load_questions(lane.questions, lane.key)["groups"] for q in g["questions"].values()]
    asked = {t for q in premarket for t in q["schedule"]["at"]}
    assert asked == {"08:48", "09:28"} and asked <= set(lane.schedule)


def test_a_premarket_question_reads_only_what_a_premarket_read_writes_and_a_session_question_never_does():
    from spx_jev.labels.registry import FAMILIES
    before_open = {p for f in FAMILIES for p in f.premarket}
    only_before_open = {p for f in FAMILIES for p in f.premarket_only}
    for lane in LANES.values():
        for g in load_questions(lane.questions, lane.key)["groups"]:
            for qid, q in g["questions"].items():
                reads = set(paths_in(q))
                if lane.key == "premarket":
                    assert reads <= before_open, f"{qid} reads {sorted(reads - before_open)}, which a premarket read does not build"
                else:
                    assert not reads & only_before_open, f"{qid} reads {sorted(reads & only_before_open)}, built only before the open"


def test_a_premarket_question_ranks_against_the_last_nights_and_never_cuts_on_a_fixed_line():
    """No fixed cut-offs before the open (premarket-data-plan section 3): every size is a third or a fifth of a rank,
    in JEV's words and in the rules the code answers by (code_criteria) alike."""
    raw = json.loads((QUESTIONS / "spx_questions.json").read_text())
    for g in raw["groups"]:
        if g["lane"] != "premarket":
            continue
        for qid, q in g["questions"].items():
            named = {n for key in ("instructions", "criteria", "code_criteria", "sleep_when") for t in _texts(q.get(key))
                     for n in constants_named(t)}
            fixed = {n for n in named - RANK_CONSTANTS if not re.fullmatch(r"window_\d+_min", n)}
            assert not fixed, f"{qid} cuts on {sorted(fixed)}"


def test_no_question_jev_is_asked_cuts_a_market_measure_on_a_fixed_line():
    """Every number an asked question names (JEV's words, the code's rules and its sleep) is a definition, never a line a
    measure is sized or judged against: those are ranks against the same clock on recent sessions."""
    allowed = set().union(*DEFINITIONS.values())
    assert allowed <= set(QUESTION_CONSTANTS), sorted(allowed - set(QUESTION_CONSTANTS))
    raw = json.loads((QUESTIONS / "spx_questions.json").read_text())
    for g in raw["groups"]:
        for qid, q in g["questions"].items():
            if q["status"] == "dark":
                continue
            named = {n for key in ("instructions", "criteria", "code_criteria", "sleep_when") for t in _texts(q.get(key))
                     for n in constants_named(t)}
            fixed = {n for n in named - allowed if not re.fullmatch(r"window_\d+_min", n)}
            assert not fixed, f"{qid} cuts on {sorted(fixed)}"


def test_a_group_over_the_cap_stops_the_load(tmp_path):
    q = {"type": "noul", "instructions": "Read `context.symbol`.", "criteria": {"true": "t", "false": "f"}}
    p = tmp_path / "doc.json"
    p.write_text(json.dumps({"groups": [{"id": "big", "reads": [], "questions": {f"q{i}": q for i in range(GROUP_CAP + 1)}}]}))
    with pytest.raises(ValueError, match="over the cap of 8"):
        load_questions(p)


def test_the_shipped_doc_is_what_the_question_set_makes():
    import subprocess
    import sys
    writer = Path(__file__).resolve().parent.parent / "spec" / "write_question_docs.py"
    done = subprocess.run([sys.executable, str(writer), "--check"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
