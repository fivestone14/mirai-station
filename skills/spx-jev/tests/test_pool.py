"""The learning loop: the forecasts at a read, the day-level update, the e-processes, the seal, the rebuild, the shown blend."""
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
    assert r == s["experts"]["baseline_cal"] == s["experts"]["baseline"] == floored(CLOCK) == pytest.approx(s["block_members"][NO_CHANGE], abs=1e-4)
    j = {k: JEV[k] / 1.0 + 0.1 * CLOCK[k] for k in CLOCK}                     # JEV's unsure spread by the reference
    assert s["experts"]["jev_share_1.0"] == pytest.approx(floored(j), abs=1e-4)
    assert s["experts"]["blend50"] == pytest.approx(floored({k: 0.5 * (JEV[k] + 0.1 * LIVE_CLOCK[k]) + 0.5 * LIVE_CLOCK[k] for k in CLOCK}), abs=1e-4)
    assert s["block_members"]["q_a"] == pytest.approx(r, abs=1e-4)         # empty tables: no tilt
    assert s["awake"] == ["q_a"] and s["fresh"] == ["q_a"] and "q_b" not in s["block_members"]
    assert s["blend50_exact"] == SHOWN and math.isclose(sum(s["pool"].values()), 1.0, abs_tol=1e-3)
    assert snapshot(cold_state(), FakeBaseline(), "next_30", at(11, 2), JEV, None, SHOWN, {}, MEMBERS, set())["left_out"].startswith("no time-of-day odds")


def test_a_read_under_a_refitted_baseline_is_calibrated_from_nothing_not_by_the_old_baselines_table():
    """The table learned against the old baseline is reset when the next session is applied under the new one, so a
    read before then is calibrated as that session will be learned: from nothing."""
    state = cold_state()
    state["cal"] = {"O": {"up": 3.0, "flat": 0.5, "down": 0.5}, "E": {"up": 0.8, "flat": 2.4, "down": 0.8}}
    state["reference_version"] = FakeBaseline.version
    tilted_cal = _snap(state)["experts"]["baseline_cal"]
    assert tilted_cal["up"] > floored(CLOCK)["up"] + 0.05                   # the same baseline: its table still applies
    state["reference_version"] = "v1:before-the-refit"
    s = _snap(state)
    assert s["experts"]["baseline_cal"] == s["experts"][REFERENCE] == floored(CLOCK)
    assert state["cal"]["O"]["up"] == 3.0                                  # the snapshot never writes the state


def _session(state, outcome_by_read, jev=JEV, answers=None, day="2026-09-18"):
    reads = []
    for k, y in enumerate(outcome_by_read):
        t = at(10, 2, day=day) + timedelta(minutes=30 * k)
        reads.append({"row_ts": t.isoformat(), "snapshot": _snap(state, answers, t, jev), "outcome": y})
    return reads


def test_one_session_moves_the_weights_by_the_day_mean_loss_against_the_reference():
    """The golden vector: two reads, 30 minutes apart, both up. The move weights follow ETA per read times the
    day's two reads times each expert's day-mean move loss less the reference's, clipped, then Fixed-Share."""
    state = cold_state()
    membership(state, MEMBERS, "2026-09-18")
    reads = _session(state, ["up", "up"])
    log = apply_session(state, "2026-09-18", reads, 30, True)
    assert log["coverage"] == [1.0, 1.0]
    f = {n: floored(x) for n, x in reads[0]["snapshot"]["experts"].items()}
    loss = {n: -math.log(1 - f[n]["flat"]) for n in W0}                    # moved: the move loss is -ln m
    gap = {n: max(-pool.GAP_CLIP, min(pool.GAP_CLIP, loss[n] - loss[REFERENCE])) for n in W0}
    raw = {n: math.log(W0[n]) - pool.ETA * 2 * gap[n] for n in W0}
    top = max(raw.values())
    p = {n: math.exp(v - top) for n, v in raw.items()}
    s = sum(p.values())
    want = {n: (1 - pool.ALPHA) * p[n] / s + pool.ALPHA * W0[n] for n in W0}
    got = pool._prob(state["top"]["M"])
    assert got == pytest.approx(want, rel=1e-12)
    assert got["jev_share_1.0"] > W0["jev_share_1.0"] and got["blend50"] < W0["blend50"]    # JEV called a move, the blend flat
    assert [round(got[n], 6) for n in ("jev_share_1.0", "blend50", "questions")] == [0.062829, 0.298486, 0.278744]
    assert round(pool._prob(state["top"]["D"])["blend50"], 6) == 0.330548
    # two questions share 0.8 of the block's prior, over the 0.25 cap: the excess goes to "no change"
    assert pool._prob(state["block"]["M"]) == pytest.approx({NO_CHANGE: 0.5, "q_a": 0.25, "q_b": 0.25})
    # the tables count every read: two reads answered yes at 0.9
    assert log["evidence"]["q_a"]["days"] == 1 and state["tables"]["q_a"]["C"]["yes"]["up"] == pytest.approx(1.8)


def test_a_questions_tables_forget_a_session_it_slept_through():
    """06 step 7 decays the tables every sealed session: a question asleep all day keeps no tilt at full strength."""
    state = cold_state()
    membership(state, MEMBERS, "2026-09-18")
    apply_session(state, "2026-09-18", _session(state, ["up", "up"]), 30, True)
    asleep = _session(state, ["up", "up"], answers={}, day="2026-09-21")
    apply_session(state, "2026-09-21", asleep, 30, True)
    assert state["tables"]["q_a"]["C"]["yes"]["up"] == pytest.approx(1.8 * pool.TABLE_DECAY)
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
                                       "members": {"q_skill": "v1", "q_all_day": "v1", "q_late": "v1"},
                                       "pool": r, "blend50_exact": CLOCK, "raw_baseline": CLOCK}})
        apply_session(state, day, reads, 30, True)
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
    apply_session(state, "2026-09-18", _session(state, ["up", "up"], answers={"q_a": soft}), 30, True)
    assert set(state["tables"]["q_a"]["C"]) == set(entry["probabilities"])
    assert state["tables"]["q_a"]["C"]["up"]["up"] == pytest.approx(1.2)                     # two reads at 0.6


def test_centring_gives_a_one_answer_question_no_credit_and_keeps_only_what_tells_answers_apart():
    """R calls flat too rarely, so every answer was followed by flat more than R expected. A question that always gives
    one answer is then exactly R; a question with two answers keeps the gap between them and loses the shared miss."""
    r = {"up": 0.3, "flat": 0.4, "down": 0.3}
    one = {"C": {"same": {"up": 6.0, "flat": 28.0, "down": 6.0}}, "X": {"same": {"up": 12.0, "flat": 16.0, "down": 12.0}}}
    assert pool.tilted(r, {"same": 1.0}, one, r) == pytest.approx(r)
    two = {"C": {"rising": {"up": 8.0, "flat": 10.0, "down": 2.0}, "falling": {"up": 2.0, "flat": 10.0, "down": 8.0}},
           "X": {"rising": {"up": 6.0, "flat": 8.0, "down": 6.0}, "falling": {"up": 6.0, "flat": 8.0, "down": 6.0}}}
    rising = pool.tilted(r, {"rising": 1.0}, two, r)
    assert rising["up"] > r["up"] > rising["down"]                          # its own signal survives
    assert rising["flat"] == pytest.approx(r["flat"], abs=0.03)             # the flat both answers share is cancelled
    assert pool.tilted(r, {"rising": 1.0}, {}, r) == pytest.approx(r)       # empty tables: no tilt


def test_a_weight_step_is_eta_per_read_and_a_wild_day_is_clipped():
    logs = {"ref": 0.0, "good": 0.0, "wild": 0.0}
    prior = {n: 1 / 3 for n in logs}
    _, rec = pool.weights_step(logs, {"ref": 1.0, "good": 0.98, "wild": 4.0}, "ref", prior, 11.0)
    assert rec["good"]["capped"] == pytest.approx(pool.ETA * 11 * 0.02) and not rec["good"]["bound"]
    assert rec["wild"]["capped"] == pytest.approx(-pool.ETA * 11 * pool.GAP_CLIP) and rec["wild"]["bound"]
    assert rec["ref"]["capped"] == 0.0


def test_the_freeze_watches_the_pools_own_experts_not_the_question_block():
    state = cold_state()
    membership(state, {f"q{i}": "v1" for i in range(40)}, "2026-09-18")
    apply_session(state, "2026-09-18", _session(state, ["up", "up"], answers={}), 30, True)
    [[_, total, _]] = state["cap_binds"]
    assert total == 2 * len(W0)                                             # move and direction over the ten experts


def test_a_question_reworded_mid_session_learns_nothing_from_its_old_versions_reads():
    state = cold_state()
    membership(state, {"q_a": "v2", "q_b": "v1"}, "2026-09-18")
    reads = _session(state, ["up", "up"])
    reads[0]["snapshot"]["members"] = {"q_a": "v1", "q_b": "v1"}            # the morning's read asked the old wording
    reads[1]["snapshot"]["members"] = {"q_a": "v2", "q_b": "v1"}
    log = apply_session(state, "2026-09-18", reads, 30, True)
    assert state["tables"]["q_a"]["C"]["yes"]["up"] == pytest.approx(0.9)   # the afternoon's read alone
    assert log["evidence"]["q_a"]["days"] == 1


def test_a_refitted_reference_starts_the_question_tables_again(tmp_path):
    _write_session(tmp_path, "2026-09-14", [("up", "up"), ("down", "down")])
    update(tmp_path, today="2026-09-15")
    assert json.loads((tmp_path / "pool_30.json").read_text())["tables"]["q_a"]["C"]
    _write_session(tmp_path, "2026-09-15", [("up", "up")])
    hour = tmp_path / "hour" / "2026-09-15.jsonl"
    recs = [json.loads(l) for l in hour.read_text().splitlines()]
    for r in recs:
        for s in r["pool"].values():
            s["reference_version"] = "v2:refitted"
    hour.write_text("".join(json.dumps(r) + "\n" for r in recs))
    update(tmp_path, today="2026-09-16")
    state = json.loads((tmp_path / "pool_30.json").read_text())
    # only the new session's one read is left in the table, nothing from the old reference's two
    assert state["reference_version"] == "v2:refitted"
    assert sum(math.fsum(row.values()) for row in state["tables"]["q_a"]["C"].values()) == pytest.approx(1.0)


def test_the_bet_is_sized_from_the_past_and_grows_on_steady_evidence():
    ep = new_eprocess()
    bet(ep, 0.8)
    assert ep["e"] == 1.0 and ep["n"] == 1                                 # no past, no bet
    for _ in range(40):
        bet(ep, 0.3)
    assert ep["e"] > pool.SUPPRESS_E
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


PHONE_HOUR = {"pick": "flat", "probabilities": SHOWN, "by": {"next_30": {"pick": "flat", "probabilities": SHOWN}}}


def test_the_end_price_sums_always_show_the_exact_blend():
    """The pool never reaches the phone: the sums are returned as they were, said under shown_source (the promotion
    that once put the pool there was retired on 2026-10-07; Pool 2 takes the call over after the read)."""
    assert shown(PHONE_HOUR) == {**PHONE_HOUR, "shown_source": "blend50_exact"}


def test_a_state_saved_with_the_retired_phone_block_loads_without_it_and_is_never_written_with_it_again(tmp_path):
    state = {**cold_state(), "phone": {"shows": "blend", "since": None, "promote": new_eprocess(), "harm_60": new_eprocess()}}
    (tmp_path / "pool_30.json").write_text(json.dumps(state))
    loaded = pool.load_state(tmp_path, 30)
    assert "phone" not in loaded and loaded["constants_hash"] == pool.CONSTANTS_HASH
    pool.save_state(tmp_path, 30, loaded)
    assert "phone" not in json.loads((tmp_path / "pool_30.json").read_text())


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


def test_a_state_and_snapshots_saved_before_the_experts_were_named_by_source_carry_on_as_if_saved_after(tmp_path):
    """The live files of 2026-09-29: a state saved with the frozen experts named clock and clock_cal, its reference's
    version under ``baseline`` and the constants hashed under those names, and reads whose snapshots say raw_clock. The
    loop reads them under today's names and lands on the same bytes as one that never had the old names."""
    back = {new: old for old, new in pool.LEGACY_NAMES.items()}
    days = ["2026-09-14", "2026-09-15", "2026-09-16"]
    for d, outs in zip(days, ([("up", "up")] * 3, [("flat", "flat")] * 3, [("down", "flat"), ("flat", "down"), ("up", "up")])):
        for side in ("now", "before"):
            _write_session(tmp_path / side, d, outs)
    for side in ("now", "before"):
        update(tmp_path / side, today=days[1])
    old = tmp_path / "before"
    for name in ("pool_30.json", "pool_60.json"):
        s = json.loads((old / name).read_text())
        s["top"] = {k: {back.get(n, n): w for n, w in top.items()} for k, top in s["top"].items()}
        s["baseline"], s["constants_hash"] = s.pop("reference_version"), pool.LEGACY_CONSTANTS_HASH
        (old / name).write_text(json.dumps(s))
    for d in days[1:]:
        recs = [json.loads(l) for l in (old / "hour" / f"{d}.jsonl").read_text().splitlines()]
        for r in recs:
            for h, s in r["pool"].items():
                s = {back.get(k, k): v for k, v in s.items()}
                s["experts"] = {back.get(n, n): f for n, f in s["experts"].items()}
                s["baseline"] = s.pop("reference_version")
                r["pool"][h] = s
        (old / "hour" / f"{d}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert "raw_clock" in (old / "hour" / f"{days[2]}.jsonl").read_text() and "clock_cal" in (old / "pool_30.json").read_text()
    assert pool.load_state(old, 30)["constants_hash"] == pool.CONSTANTS_HASH and "baseline_cal" in pool.load_state(old, 30)["top"]["M"]
    for side in ("now", "before"):
        assert update(tmp_path / side, today="2026-09-17")["next_30"] == "2026-09-16: applied, 3 reads"
    for name in ("pool_30.json", "pool_60.json"):
        assert (old / name).read_bytes() == (tmp_path / "now" / name).read_bytes()
    state = json.loads((old / "pool_30.json").read_text())
    assert state["reference_version"] == FakeBaseline.version and "baseline" not in state and set(state["top"]["M"]) == set(W0)


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


def test_a_state_made_under_other_constants_is_kept_and_learned_again_from_the_records(tmp_path):
    """A change of constants never stops the loop: the old state goes to archive/, the loop learns again from cold,
    the sessions the old state had applied replayed with no bets (their weights and tables as a fresh run's, every
    e-process back at 1), and the next session learned as usual. Snapshots written under the old constants are
    formed again from the rebuilt state."""
    for side in ("old", "fresh"):
        _write_session(tmp_path / side, "2026-09-14", [("up", "up"), ("flat", "down")])
    update(tmp_path / "old", today="2026-09-15")
    for name in ("pool_30.json", "pool_60.json"):
        path = tmp_path / "old" / name
        path.write_text(json.dumps({**json.loads(path.read_text()), "constants_hash": "deadbeefdeadbeef"}))
    planted = (tmp_path / "old" / "pool_30.json").read_bytes()
    hour = tmp_path / "old" / "hour" / "2026-09-14.jsonl"
    recs = [json.loads(l) for l in hour.read_text().splitlines()]
    for r in recs:                                                         # snapshots from before the constants changed
        for s in r["pool"].values():
            s["constants_hash"] = "deadbeefdeadbeef"
    hour.write_text("".join(json.dumps(r) + "\n" for r in recs))
    for side in ("old", "fresh"):
        _write_session(tmp_path / side, "2026-09-15", [("up", "up")])
    assert update(tmp_path / "old", today="2026-09-16") == {"next_60": "2026-09-15: applied, 1 reads", "next_30": "2026-09-15: applied, 1 reads"}
    update(tmp_path / "fresh", today="2026-09-16")
    [kept] = (tmp_path / "old" / "archive").glob("pool_30.json.pre-deadbeefdeadbeef-*")
    assert kept.read_bytes() == planted
    old, fresh = (json.loads((tmp_path / side / "pool_30.json").read_text()) for side in ("old", "fresh"))
    assert old["constants_hash"] == pool.CONSTANTS_HASH and old["last_session_applied"] == "2026-09-15"
    assert old["tables"] == fresh["tables"] and old["top"] == fresh["top"] and old["block"] == fresh["block"]
    assert old["evidence"]["q_a"]["days"] == 1 < fresh["evidence"]["q_a"]["days"] == 2      # the replayed day bet nothing
    log = [json.loads(l) for l in (tmp_path / "old" / pool.LOG_NAME).read_text().splitlines()]
    at_rebuild = next(i for i, l in enumerate(log) if "rebuild" in l)
    assert log[at_rebuild]["rebuild"]["replay_until"] == "2026-09-14" and log[at_rebuild]["rebuild"]["to"] == pool.CONSTANTS_HASH
    replayed = [l for l in log[at_rebuild:] if l.get("session") == "2026-09-14"]
    assert len(replayed) == 2 and all(l["replay"] and "evidence" not in l for l in replayed)


def test_a_rebuild_stopped_part_way_still_bets_nothing_on_the_days_it_replays_when_it_goes_on(tmp_path):
    """The replay's last day is kept in the state: a rebuild that meets an unsealed session saves a cold state, and the
    next run replays the old days without bets all the same (09-30 review: otherwise it bets on days already learned)."""
    _write_session(tmp_path, "2026-09-14", [("up", "up")])
    _write_session(tmp_path, "2026-09-15", [("flat", "flat")])
    update(tmp_path, today="2026-09-16")
    for name in ("pool_30.json", "pool_60.json"):
        path = tmp_path / name
        path.write_text(json.dumps({**json.loads(path.read_text()), "constants_hash": "deadbeefdeadbeef"}))
    grades = tmp_path / "grades.jsonl"
    kept = grades.read_text()
    grades.write_text("".join(l + "\n" for l in kept.splitlines() if "2026-09-14" not in l))      # 09-14 not graded yet
    assert update(tmp_path, today="2026-09-16")["next_30"] == "2026-09-14: unsealed; stopped"
    assert json.loads((tmp_path / "pool_30.json").read_text())["replay_until"] == "2026-09-15"
    grades.write_text(kept)
    update(tmp_path, today="2026-09-16")
    state = json.loads((tmp_path / "pool_30.json").read_text())
    assert state["last_session_applied"] == "2026-09-15"
    assert state["evidence"]["q_a"]["days"] == 0


def test_an_old_snapshot_is_formed_again_in_any_run_and_one_from_another_reference_stops_it(tmp_path):
    """A snapshot written under other constants is formed again whenever it turns up, not only in a rebuild, from the
    Baseline's long-run shares; when its tables were learned under another reference than that Baseline's, it cannot
    be formed faithfully, so the run stops (fail closed) and nothing is applied."""
    _write_session(tmp_path, "2026-09-14", [("up", "up"), ("down", "down")])
    update(tmp_path, today="2026-09-15", baseline=FakeBaseline())
    _write_session(tmp_path, "2026-09-15", [("up", "up")])
    hour = tmp_path / "hour" / "2026-09-15.jsonl"
    fresh = hour.read_text()
    recs = [json.loads(l) for l in fresh.splitlines()]
    for r in recs:                                                         # written by older code: no hash, no shares
        for s in r["pool"].values():
            s.pop("constants_hash"), s.pop("long_run")
    hour.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert update(tmp_path, today="2026-09-16", baseline=FakeBaseline())["next_30"] == "2026-09-15: applied, 1 reads"

    class Refitted(FakeBaseline):
        version = "v2:refitted"
    other = tmp_path / "other"
    _write_session(other, "2026-09-14", [("up", "up"), ("down", "down")])
    update(other, today="2026-09-15", baseline=FakeBaseline())
    (other / "hour" / "2026-09-15.jsonl").write_text(hour.read_text())
    with open(other / "grades.jsonl", "a") as f:
        f.write("".join(l + "\n" for l in (tmp_path / "grades.jsonl").read_text().splitlines() if "2026-09-15" in l))
    said = update(other, today="2026-09-16", baseline=Refitted())
    assert said["next_60"] == "2026-09-15: a snapshot cannot be formed again; stopped"
    assert json.loads((other / "pool_60.json").read_text())["last_session_applied"] == "2026-09-14"


def test_a_snapshot_formed_under_other_forming_settings_stops_a_rebuild(tmp_path):
    """The experts a rebuild keeps as written (the JEV mixes, the blend, the calibration) are only kept when the read
    formed them under today's settings, which each snapshot fingerprints; otherwise the run stops (fail closed)."""
    _write_session(tmp_path, "2026-09-14", [("up", "up")])
    hour = tmp_path / "hour" / "2026-09-14.jsonl"
    recs = [json.loads(l) for l in hour.read_text().splitlines()]
    assert recs[0]["pool"]["next_30"]["forming_hash"] == pool.FORMING_HASH
    for r in recs:
        for s in r["pool"].values():
            s["constants_hash"], s["forming_hash"] = "deadbeefdeadbeef", "0000000000000000"
    hour.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert update(tmp_path, today="2026-09-15", baseline=FakeBaseline())["next_60"] == "2026-09-14: a snapshot cannot be formed again; stopped"
    assert json.loads((tmp_path / "pool_60.json").read_text())["last_session_applied"] is None


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
    read would cover only the part of its window the 08:48 read did not. Forecasting one window, they are one
    observation between them, each a third of it."""
    by_clock, same = cold_state(), cold_state()
    for state in (by_clock, same):
        membership(state, MEMBERS, "2026-09-18")
    reads = [{"row_ts": at(hh, mm).isoformat(), "snapshot": _snap(same, t=at(hh, mm)), "outcome": "up"} for hh, mm in ((8, 48), (9, 5), (9, 28))]
    assert apply_session(by_clock, "2026-09-18", reads, 30, True)["coverage"] == pytest.approx([1.0, 17 / 30, 23 / 30])
    assert apply_session(same, "2026-09-18", reads, 30, True, same_window=True)["coverage"] == pytest.approx([1 / 3] * 3)


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
