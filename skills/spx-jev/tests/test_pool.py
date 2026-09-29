"""The learning loop: the forecasts at a read, the day-level update, the e-processes, the seal, the rebuild, the phone switch."""
from __future__ import annotations

import json
import math
from datetime import timedelta

import pytest

from conftest import at
from spx_jev import pool
from spx_jev.pool import (NO_CHANGE, REFERENCE, W0, apply_session, bet, cold_state, coverage, membership, new_eprocess,
                          shown, snapshot, statuses, update)
from spx_jev.scores import floored

CLOCK = {"up": 0.2, "flat": 0.6, "down": 0.2}


class FakeBaseline:
    version = "v1:test"

    def clock(self, h, now):
        return dict(CLOCK)

    def whole_day(self, h):
        return dict(CLOCK)


JEV = {"up": 0.6, "flat": 0.2, "down": 0.1, "unsure": 0.1}
LIVE_CLOCK = {"up": 0.1, "flat": 0.8, "down": 0.1}
SHOWN = {"up": 0.35, "flat": 0.5, "down": 0.1, "unsure": 0.05}
MEMBERS = {"q_a": "v1", "q_b": "v1"}


def _snap(state=None, answers=None, t=None, jev=JEV):
    return snapshot(state or cold_state(), FakeBaseline(), "next_30", t or at(11, 2), jev, LIVE_CLOCK, SHOWN,
                    {"q_a": {"yes": 0.9, "no": 0.1}} if answers is None else answers, MEMBERS, {"q_a"})


def test_overlapping_windows_are_counted_once():
    assert coverage([at(10, 0), at(10, 30), at(11, 0)], 60) == [1.0, 0.5, 0.5]
    assert coverage([at(10, 0), at(10, 2)], 30) == pytest.approx([1.0, 2 / 30])
    assert coverage([at(10, 2), at(10, 32)], 30) == [1.0, 1.0]


def test_at_a_read_the_mixes_run_from_the_reference_to_jev_and_an_untaught_question_is_the_reference():
    s = _snap()
    r = s["experts"][REFERENCE]
    assert r == s["experts"]["clock_cal"] == s["experts"]["clock"] == floored(CLOCK) == pytest.approx(s["block_members"][NO_CHANGE], abs=1e-4)
    j = {k: JEV[k] / 1.0 + 0.1 * CLOCK[k] for k in CLOCK}                     # JEV's unsure spread by the reference
    assert s["experts"]["jev_share_1.0"] == pytest.approx(floored(j), abs=1e-4)
    assert s["experts"]["blend50"] == pytest.approx(floored({k: 0.5 * (JEV[k] + 0.1 * LIVE_CLOCK[k]) + 0.5 * LIVE_CLOCK[k] for k in CLOCK}), abs=1e-4)
    assert s["block_members"]["q_a"] == pytest.approx(r, abs=1e-4)         # empty tables: no tilt
    assert s["awake"] == ["q_a"] and s["fresh"] == ["q_a"] and "q_b" not in s["block_members"]
    assert s["blend50_exact"] == SHOWN and math.isclose(sum(s["pool"].values()), 1.0, abs_tol=1e-3)
    assert snapshot(cold_state(), FakeBaseline(), "next_30", at(11, 2), JEV, None, SHOWN, {}, MEMBERS, set())["left_out"].startswith("no time-of-day odds")


def _session(state, outcome_by_read, jev=JEV, answers=None, day="2026-09-18"):
    reads = []
    for k, y in enumerate(outcome_by_read):
        t = at(10, 2, day=day) + timedelta(minutes=30 * k)
        reads.append({"row_ts": t.isoformat(), "snapshot": _snap(state, answers, t, jev), "outcome": y})
    return reads


def test_one_session_moves_the_weights_by_the_day_mean_loss_against_the_reference():
    """The golden vector: two reads, 30 minutes apart, both up. The move weights follow each expert's
    day-mean move loss less the reference's, capped, then Fixed-Share."""
    state = cold_state()
    membership(state, MEMBERS, "2026-09-18")
    reads = _session(state, ["up", "up"])
    log = apply_session(state, "2026-09-18", reads, 30, True, None)
    assert log["coverage"] == [1.0, 1.0]
    f = {n: floored(x) for n, x in reads[0]["snapshot"]["experts"].items()}
    loss = {n: -math.log(1 - f[n]["flat"]) for n in W0}                    # moved: the move loss is -ln m
    raw = {n: math.log(W0[n]) - (loss[n] - loss[REFERENCE]) for n in W0}
    top = max(raw.values())
    p = {n: math.exp(v - top) for n, v in raw.items()}
    s = sum(p.values())
    want = {n: (1 - pool.ALPHA) * p[n] / s + pool.ALPHA * W0[n] for n in W0}
    got = pool._prob(state["top"]["M"])
    assert got == pytest.approx(want, rel=1e-12)
    assert got["jev_share_1.0"] > W0["jev_share_1.0"] and got["blend50"] < W0["blend50"]    # JEV called a move, the blend flat
    assert [round(got[n], 6) for n in ("jev_share_1.0", "blend50", "questions")] == [0.078313, 0.294358, 0.256746]
    assert round(pool._prob(state["top"]["D"])["blend50"], 6) == 0.360306
    # two questions share 0.8 of the block's prior, over the 0.25 cap: the excess goes to "no change"
    assert pool._prob(state["block"]["M"]) == pytest.approx({NO_CHANGE: 0.5, "q_a": 0.25, "q_b": 0.25})
    assert log["evidence"]["q_a"]["days"] == 1 and state["tables"]["q_a"]["C"]["yes"]["up"] == pytest.approx(0.9)


def test_a_questions_tables_forget_a_session_it_slept_through():
    """06 step 7 decays the tables every sealed session: a question asleep all day keeps no tilt at full strength."""
    state = cold_state()
    membership(state, MEMBERS, "2026-09-18")
    apply_session(state, "2026-09-18", _session(state, ["up", "up"]), 30, True, None)
    asleep = _session(state, ["up", "up"], answers={}, day="2026-09-21")
    apply_session(state, "2026-09-21", asleep, 30, True, None)
    assert state["tables"]["q_a"]["C"]["yes"]["up"] == pytest.approx(0.9 * pool.TABLE_DECAY)
    assert "q_b" not in state["tables"]                                    # never awake: no table made for it


def test_a_questions_evidence_counts_only_the_reads_it_was_awake_on():
    """Two questions that carry no information (each forecasts "no change" whenever awake), one awake all day
    and one on 2 of 12 reads, beside one that knows the outcome: neither is credited with the block's skill
    on the reads it slept through, so both end where they began."""
    r = floored(CLOCK)
    state = cold_state()
    outcomes = ["up", "flat", "flat", "down", "flat", "up"] * 2
    for d in range(30):
        day = (at(10, 2, day="2026-10-01") + timedelta(days=d)).date().isoformat()
        membership(state, {"q_skill": "v1", "q_all_day": "v1", "q_late": "v1"}, day)
        reads = []
        for i, y in enumerate(outcomes):
            members = {NO_CHANGE: r, "q_skill": floored({k: 0.6 if k == y else 0.2 for k in CLOCK}), "q_all_day": r}
            if i >= 10:
                members["q_late"] = r
            experts = {n: r for n in W0}
            experts["questions"] = floored(pool.mixed(members, state["block"]))
            reads.append({"row_ts": (at(10, 2, day=day) + timedelta(minutes=30 * i)).isoformat(), "outcome": y,
                          "snapshot": {"experts": experts, "block_members": members, "q_probs": {q: {"a": 1.0} for q in members if q != NO_CHANGE},
                                       "pool": r, "blend50_exact": CLOCK, "raw_clock": CLOCK}})
        apply_session(state, day, reads, 30, True, None)
    ev = state["evidence"]
    for q in ("q_all_day", "q_late"):
        assert ev[q]["days"] == 30
        assert [ev[q][part][side]["e"] for part in ("move", "direction") for side in ("better", "worse")] == [1.0] * 4
    assert ev["q_skill"]["move"]["better"]["e"] > 1.0 and ev["q_skill"]["direction"]["better"]["e"] > 1.0


def test_a_score_answer_is_learned_by_its_level_names_like_a_choice():
    """A score named by its levels (hour.named_levels) is a soft answer over those names, and the day's
    tables count the outcome under each level as they do under a choice's options."""
    entry = {"pick": "up", "probabilities": {"strong_down": 0.0, "down": 0.1, "nowhere": 0.3, "up": 0.6, "strong_up": 0.0}, "score": 2.5}
    soft = pool.soft_answer(entry)
    assert soft == pytest.approx(entry["probabilities"])
    state = cold_state()
    membership(state, MEMBERS, "2026-09-18")
    apply_session(state, "2026-09-18", _session(state, ["up", "up"], answers={"q_a": soft}), 30, True, None)
    assert set(state["tables"]["q_a"]["C"]) == set(entry["probabilities"])
    assert state["tables"]["q_a"]["C"]["up"]["up"] == pytest.approx(0.6)


def test_the_bet_is_sized_from_the_past_and_grows_on_steady_evidence():
    ep = new_eprocess()
    bet(ep, 0.8)
    assert ep["e"] == 1.0 and ep["n"] == 1                                 # no past, no bet
    for _ in range(40):
        bet(ep, 0.3)
    assert ep["e"] > pool.PROMOTE_E
    worse = new_eprocess()
    for _ in range(40):
        bet(worse, -0.3)
    assert worse["e"] == 1.0                                               # it never bets on the side the evidence is against


def test_earning_needs_ebh_over_every_version_ever_tested_and_twenty_days():
    state = cold_state()
    membership(state, {f"q{i}": "v1" for i in range(40)}, "2026-09-18")
    ev = state["evidence"]["q0"]
    ev["move"]["better"]["e"], ev["days"] = 300.0, 25                      # K = 40: a lone flag needs 40 / (0.10 * 1) = 400
    assert statuses(state) == [] and ev["status"]["move"] is None
    ev["move"]["better"]["e"] = 450.0
    assert statuses(state) == [{"question": "q0", "part": "move", "from": None, "to": "earning"}]
    ev["days"] = 10
    assert statuses(state)[0]["to"] is None                                # under twenty days, not yet


def test_a_question_joins_at_its_prior_share_leaves_to_the_archive_and_restarts_on_new_words():
    state = cold_state()
    assert membership(state, {"q_a": "v1"}, "d1")["joined"] == ["q_a"]
    assert pool._prob(state["block"]["M"]) == pytest.approx({NO_CHANGE: 0.2, "q_a": 0.8})
    state["block"]["M"] = pool._logs({NO_CHANGE: 0.5, "q_a": 0.5})
    membership(state, {"q_a": "v1", "q_b": "v1"}, "d2")
    v = pool._prob(state["block"]["M"])
    assert v["q_b"] == pytest.approx(0.4) and v[NO_CHANGE] == pytest.approx(v["q_a"])   # the others' ratio kept
    changes = membership(state, {"q_a": "v2"}, "d3")
    assert changes == {"joined": ["q_a"], "retired": ["q_b"], "new_version": ["q_a"]}
    assert set(state["archived"]) == {"q_a@v1", "q_b@v1"} and state["family"] == ["q_a@v1", "q_b@v1", "q_a@v2"]


def test_a_questions_version_changes_with_any_word_jev_is_sent_and_with_its_options_order():
    from spx_jev.ask import load_questions
    from spx_jev.lane import LIVE
    by_id = {qid: q for g in load_questions(LIVE.questions, LIVE.key)["groups"] for qid, q in g["questions"].items()}
    choice = next(q for q in by_id.values() if isinstance(q.get("criteria"), dict))
    score = next(q for q in by_id.values() if isinstance(q.get("criteria"), list) and q.get("options"))
    first = next(iter(choice["criteria"]))
    reworded = {**choice, "criteria": {**choice["criteria"], first: "a different meaning"}}
    reordered = {**score, "options": list(reversed(score["options"]))}
    assert pool.question_version(reworded) != pool.question_version(choice)
    assert pool.question_version(reordered) != pool.question_version(score)
    assert pool.question_version(dict(choice)) == pool.question_version(choice)


SURE_FLAT = {"up": 0.01, "flat": 0.98, "down": 0.01, "unsure": 0.0}
PHONE_HOUR = {"pick": "flat", "probabilities": SHOWN, "by": {"next_30": {"pick": "flat", "probabilities": SHOWN}}}


def _days(state, first, n, outcomes):
    """n sessions from October ``first`` on, JEV sure of flat each read."""
    for d in range(first, first + n):
        day = f"2026-10-{d:02d}"
        apply_session(state, day, _session(state, outcomes, jev=SURE_FLAT, day=day), 30, True, None)


def test_on_day_one_the_phone_shows_the_blend_exactly_as_before():
    """The switch is on, but a pool with no days behind it is not promoted, so the sum is untouched."""
    state = cold_state()
    assert pool.POOL_ON_PHONE is True and state["phone"]["shows"] == "blend"
    assert shown(PHONE_HOUR, {"next_30": _snap(state)}, state) == {**PHONE_HOUR, "shown_source": "blend50_exact"}


def test_nineteen_winning_days_leave_the_phone_on_the_blend():
    state = cold_state()
    membership(state, MEMBERS, "2026-09-01")
    _days(state, 1, pool.MIN_DAYS - 1, ["flat", "flat"])
    assert state["phone"]["promote"]["e"] >= pool.PROMOTE_E and state["phone"]["shows"] == "blend"
    assert shown(PHONE_HOUR, {"next_30": _snap(state)}, state)["shown_source"] == "blend50_exact"


def test_without_the_simulation_gates_passed_no_amount_of_evidence_puts_the_pool_on_the_phone():
    """06 requires the simulation's acceptance gates before any promotion: until they are marked passed,
    evidence well past the bar leaves the phone on the blend and the log says why."""
    assert pool.SIM_GATES_PASSED is False
    state = cold_state()
    membership(state, MEMBERS, "2026-09-01")
    for d in range(1, 26):
        day = f"2026-10-{d:02d}"
        log = apply_session(state, day, _session(state, ["flat", "flat"], jev=SURE_FLAT, day=day), 30, True, None)
    assert state["phone"]["promote"]["e"] >= pool.PROMOTE_E and state["phone"]["promote"]["n"] >= pool.MIN_DAYS
    assert state["phone"]["shows"] == "blend" and log["phone"].startswith("held on the blend")
    assert shown(PHONE_HOUR, {"next_30": _snap(state)}, state)["shown_source"] == "blend50_exact"


def test_the_pool_is_promoted_after_twenty_winning_days_and_the_phone_shows_it_with_the_blend_beside_it(monkeypatch):
    monkeypatch.setattr(pool, "SIM_GATES_PASSED", True)
    state = cold_state()
    membership(state, MEMBERS, "2026-09-01")
    _days(state, 1, 25, ["flat", "flat"])
    assert state["phone"]["shows"] == "pool" and state["phone"]["promote"]["n"] >= pool.MIN_DAYS
    snaps = {"next_30": _snap(state)}
    on = shown(PHONE_HOUR, snaps, state)
    assert on["shown_source"] == "pool_v1" and on["probabilities"] == snaps["next_30"]["pool"]
    assert on["blend50_exact"] == on["by"]["next_30"]["blend50_exact"] == snaps["next_30"]["blend50_exact"] == SHOWN
    monkeypatch.setattr(pool, "POOL_ON_PHONE", False)
    assert shown(PHONE_HOUR, snaps, state) == {**PHONE_HOUR, "shown_source": "blend50_exact"}


def test_a_promoted_pool_that_starts_losing_hands_the_phone_back_to_the_blend(monkeypatch):
    monkeypatch.setattr(pool, "SIM_GATES_PASSED", True)
    state = cold_state()
    membership(state, MEMBERS, "2026-09-01")
    _days(state, 1, 25, ["flat", "flat"])
    since = state["phone"]["since"]
    # losing days already behind it, one short of the demotion bar
    state["phone"]["demote"] = {"e": pool.DEMOTE_E - 0.1, "n": 10, "sum": 10.0, "sum_sq": 10.0}
    _days(state, 26, 1, ["up", "down"])
    assert state["phone"]["shows"] == "blend" and state["phone"]["since"] == "2026-10-26" > since
    assert state["phone"]["promote"] == new_eprocess()
    assert shown(PHONE_HOUR, {"next_30": _snap(state)}, state)["shown_source"] == "blend50_exact"


# ---- the update over the lane's files

def _write_session(out, day, outcomes, with_pool=True, graded=True, event_at=None):
    """Three reads at :02 and :32 with their sums, snapshots and grades on disk."""
    (out / "hour").mkdir(parents=True, exist_ok=True)
    lines, grades = [], []
    for k, (y30, y60) in enumerate(outcomes):
        t = at(10, 2, day=day) + timedelta(minutes=30 * k)
        rec = {"row_ts": t.isoformat(), "spot": 7700.0, "sigma": 75.0, "by": {"next_30": {"probabilities": SHOWN}},
               "event": {"within_30": True} if event_at == k else None}
        if with_pool:
            rec["pool"] = {h: snapshot(cold_state(), FakeBaseline(), h, t, JEV, LIVE_CLOCK, SHOWN, {"q_a": {"yes": 0.7, "no": 0.3}},
                                       MEMBERS, {"q_a"}) for h in ("next_30", "next_60")}
        lines.append(rec)
        g = {"row_ts": t.isoformat(), "horizons": ["next_30", "next_60"] if graded else ["next_30"],
             "pending": [] if graded else ["next_60"], "skipped": {}, "next_30": {"band": y30}}
        if graded:
            g["next_60"] = {"band": y60}
        grades.append(g)
    (out / "hour" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    with open(out / "grades.jsonl", "a") as f:
        f.write("".join(json.dumps(g) + "\n" for g in grades))


def test_the_update_applies_sealed_sessions_once_and_a_rebuild_from_the_records_is_identical(tmp_path):
    days = ["2026-09-14", "2026-09-15", "2026-09-16"]
    for d, outs in zip(days, ([("up", "up")] * 3, [("flat", "flat")] * 3, [("down", "flat"), ("flat", "down"), ("up", "up")])):
        _write_session(tmp_path / "a", d, outs)
        _write_session(tmp_path / "b", d, outs)
    assert update(tmp_path / "a", today="2026-09-17") == {"next_60": "2026-09-16: applied, 3 reads", "next_30": "2026-09-16: applied, 3 reads"}
    first = (tmp_path / "a" / "pool_30.json").read_bytes()
    assert update(tmp_path / "a", today="2026-09-17")["next_30"] == "nothing new to apply"
    assert (tmp_path / "a" / "pool_30.json").read_bytes() == first          # applying a session twice is a no-op
    update(tmp_path / "b", today="2026-09-15")
    update(tmp_path / "b", today="2026-09-17")                               # one night at a time lands in the same place
    for name in ("pool_30.json", "pool_60.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    state = json.loads(first)
    assert state["last_session_applied"] == "2026-09-16" and state["evidence"]["q_a"]["days"] == 3


def test_an_unsealed_session_stops_the_update_and_one_before_the_loop_is_passed_over(tmp_path):
    _write_session(tmp_path, "2026-09-11", [("up", "up")], with_pool=False)
    _write_session(tmp_path, "2026-09-14", [("up", "up")], graded=False)
    _write_session(tmp_path, "2026-09-15", [("up", "up")])
    said = update(tmp_path, today="2026-09-16")
    assert said["next_30"] == "2026-09-14: unsealed; stopped"
    state = json.loads((tmp_path / "pool_30.json").read_text())
    assert state["last_session_applied"] == "2026-09-11" and state["evidence"] == {}
    log = [json.loads(l) for l in (tmp_path / pool.LOG_NAME).read_text().splitlines()]
    assert [l.get("why") for l in log] == ["no read carries a snapshot: before the loop"] * 2 + ["unsealed: a read still has a horizon to grade"]


def test_a_state_made_under_other_constants_stops_the_update(tmp_path):
    _write_session(tmp_path, "2026-09-14", [("up", "up")])
    update(tmp_path, today="2026-09-15")
    for name in ("pool_30.json", "pool_60.json"):
        path = tmp_path / name
        path.write_text(json.dumps({**json.loads(path.read_text()), "constants_hash": "deadbeefdeadbeef"}))
    _write_session(tmp_path, "2026-09-15", [("up", "up")])
    assert update(tmp_path, today="2026-09-16")["next_30"] == "constants changed; stopped"
    assert json.loads((tmp_path / "pool_30.json").read_text())["last_session_applied"] == "2026-09-14"
    log = [json.loads(l) for l in (tmp_path / pool.LOG_NAME).read_text().splitlines()]
    assert log[-1]["applied"] is False and log[-1]["why"].startswith("constants changed")


def test_a_session_with_a_read_missing_its_snapshot_fails_closed_and_an_event_read_is_left_out(tmp_path):
    _write_session(tmp_path, "2026-09-14", [("up", "up"), ("flat", "flat")], event_at=1)
    update(tmp_path, today="2026-09-15")
    log = [json.loads(l) for l in (tmp_path / pool.LOG_NAME).read_text().splitlines()]
    by_h = {l["horizon"]: l for l in log}
    assert list(by_h["next_30"]["manifest"]["excluded"].values()) == ["a scheduled event inside the 30-minute window"]
    assert len(by_h["next_60"]["manifest"]["excluded"]) == 1
    hour = tmp_path / "hour" / "2026-09-15.jsonl"
    _write_session(tmp_path, "2026-09-15", [("up", "up"), ("up", "up")])
    recs = [json.loads(l) for l in hour.read_text().splitlines()]
    del recs[1]["pool"]
    hour.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert update(tmp_path, today="2026-09-16")["next_30"] == "2026-09-15: some reads carry no snapshot; stopped"


def test_the_loop_leaves_out_what_the_read_recorded_beyond_the_tier_1_tag():
    """06's guardrails: a 10:00 release, a 14:00 one, a Fed speaker, a monthly expiry's close and the month's last
    close keep a read out of the loop though the weights' tier-1 tag says nothing."""
    from datetime import datetime
    from spx_jev.events import ET, learn_exclude
    jolts = learn_exclude(datetime(2026, 9, 29, 9, 32, tzinfo=ET), (30, 60))           # JOLTS at 10:00
    opex = learn_exclude(datetime(2026, 10, 16, 15, 32, tzinfo=ET), (30, 60))          # a monthly expiry's close
    assert jolts == {"30": True, "60": True} and opex == {"30": True, "60": True}
    assert learn_exclude(datetime(2026, 10, 16, 14, 32, tzinfo=ET), (30, 60)) == {"30": False, "60": False}
    assert pool.event_inside({"event": None, "learn_exclude": jolts}, 30)
    assert not pool.event_inside({"event": {"within_30": True}, "learn_exclude": {"30": False}}, 30)
    assert pool.event_inside({"event": {"within_30": True}}, 30)                       # a record from before learn_exclude


# ---- the premarket lane: its own state, one window a day

def test_reads_forecasting_one_window_count_the_same_in_their_day():
    """The premarket reads all forecast the half hour after the settled open: by their own clocks the 09:05
    read would cover only the part of its window the 08:48 read did not."""
    by_clock, same = cold_state(), cold_state()
    for state in (by_clock, same):
        membership(state, MEMBERS, "2026-09-18")
    reads = [{"row_ts": at(hh, mm).isoformat(), "snapshot": _snap(same, t=at(hh, mm)), "outcome": "up"} for hh, mm in ((8, 48), (9, 5), (9, 28))]
    assert apply_session(by_clock, "2026-09-18", reads, 30, True, None)["coverage"] == pytest.approx([1.0, 17 / 30, 23 / 30])
    assert apply_session(same, "2026-09-18", reads, 30, True, None, same_window=True)["coverage"] == [1.0, 1.0, 1.0]


def test_the_premarket_loop_keeps_its_own_files_and_learns_nothing_from_reads_without_a_snapshot(tmp_path):
    from spx_jev.lane import PREMARKET
    out = PREMARKET.folder(tmp_path)
    (out / "hour").mkdir(parents=True)
    left_out = {"left_out": "no time-of-day odds this read, so today's blend is JEV alone and blend50 cannot be formed"}
    recs = [{"row_ts": at(hh, mm).isoformat(), "by": {"open_30": {"probabilities": SHOWN}}, "pool": {h: left_out for h in PREMARKET.horizons}}
            for hh, mm in ((8, 48), (9, 28))]
    (out / "hour" / "2026-09-18.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (out / "grades.jsonl").write_text("".join(json.dumps({"row_ts": r["row_ts"], "horizons": ["open_10", "open_30"], "pending": [], "skipped": {},
                                                           "open_10": {"band": "up"}, "open_30": {"band": "up"}}) + "\n" for r in recs))
    assert update(out, today="2026-09-21", lane=PREMARKET) == {"open_30": "2026-09-18: applied, 0 reads", "open_10": "2026-09-18: applied, 0 reads"}
    assert sorted(p.name for p in out.glob("pool_*")) == ["pool_10.json", "pool_30.json", "pool_log.jsonl"]
    assert not list(tmp_path.glob("spx_jev/pool_*"))
    log = [json.loads(l) for l in (out / pool.LOG_NAME).read_text().splitlines()]
    assert {l["applied"] for l in log} == {False} and set(log[0]["manifest"]["excluded"].values()) == {f"no snapshot: {left_out['left_out']}"}
    w = pool.PoolWeights.learn([], {"q1": {"up"}}, out, PREMARKET)
    assert w.pool["last_session_applied"] == "2026-09-18" and w.pool["applied"] == {"open_30": "nothing new to apply", "open_10": "nothing new to apply"}


def test_the_pool_on_the_phone_takes_the_primary_the_sum_names(monkeypatch):
    state = cold_state()
    state["phone"]["shows"] = "pool"
    snaps = {"open_10": _snap(state, jev={"up": 0.1, "flat": 0.8, "down": 0.1}), "open_30": _snap(state)}
    hour = {"pick": "flat", "probabilities": SHOWN, "primary": "open_30",
            "by": {"open_10": {"pick": "flat", "probabilities": SHOWN}, "open_30": {"pick": "flat", "probabilities": SHOWN}}}
    monkeypatch.setattr(pool, "POOL_ON_PHONE", True)
    on = shown(hour, snaps, state)
    assert on["probabilities"] == snaps["open_30"]["pool"] != snaps["open_10"]["pool"]
