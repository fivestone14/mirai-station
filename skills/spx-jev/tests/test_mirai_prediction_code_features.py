"""The code feature builder: the catalog, the label parsers against the mapping's real archive sentences (tests/data/
ready_mapping.json), the bars questions on synthetic bars, silence on unknown templates, the matrix's code columns, the
raw-first write in the hook, and the backfill. No host state."""
from __future__ import annotations

import json
import random
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import PRIOR_DAYS, at, bars_from_closes

from spx_jev.mirai_prediction import answer_matrix, code_feature_inputs, paths, read_hook
from spx_jev.mirai_prediction.answer_matrix import Row, build_answer_matrix
from spx_jev.mirai_prediction.backfill_code_features import run_backfill
from spx_jev.mirai_prediction.code_features import (BARS_ANSWERERS, LABEL_PARSERS, PREMARKET_QUESTIONS, MarketHistory, answer_code_features,
                                                    bars_up_to, load_catalog)
from spx_jev.mirai_prediction.code_features_market import MARKET_ANSWERERS
from spx_jev.mirai_prediction.voice_fits import DayFits

MAPPING = [m for name in ("ready_mapping.json", "phase2_mapping.json")
           for m in json.loads((Path(__file__).parent / "data" / name).read_text(encoding="utf-8"))]
CATALOG = {q["id"]: q for q in load_catalog()}
DAY = "2026-09-18"
SIGMA = 75.0
NATIVE_BOOK = [{"ts": "2026-09-01T09:30:00-04:00", "gex_source": "native"}]      # a diary row from SPX's own options book

# The question pressure test of 2026-10-07 (the mapping files are the merged set's record and stay as they were): the
# questions that left the catalog, the ones that came in, and the archived examples whose answer it changed on purpose.
ROUND_2_DROPPED = {"OPTIONS-12", "FLOW-09", "LEVELS-04", "BREADTH-08", "MACRO-04", "MACRO-05", "MACRO-06", "MACRO-08", "EVENTS-01",
                   "EVENTS-05", "BREADTH-11", "MACRO-10",
                   "OPTIONS-09"}                 # dropped on its own condition: V 0.90 with VOLATILITY-12 after the fix
ROUND_2_NEW = {"VOLATILITY-18", "MACRO-09-ARC"}
NON_VOTING = {"BREADTH-09", "VOLATILITY-12", "VOLATILITY-17", "MACRO-02", "SENTIMENT-02", "OPTIONS-02"}
ROUND_2_ANSWERS = {      # (mapping id, read) -> (catalog id, answer)
    ("TREND-03", "live 2026-09-29T14:31"): ("TREND-03", None),                      # from 14:45: at 14:30 the leg is TREND-01's
    ("TREND-03", "live 2026-09-30T14:30"): ("TREND-03", None),
    ("TREND-06", "live 2026-09-30T13:30"): ("TREND-06", "reversed"),                # 'cancelling' renamed
    ("VOLATILITY-16", "live 2026-09-28T15:00"): ("VOLATILITY-16", "normal, no event"),     # a Fed governor does not load the ruler
    ("VOLATILITY-16", "live 2026-09-29T11:31"): ("VOLATILITY-16", "swollen, no event"),
    ("VOLATILITY-16", "live 2026-09-30T13:30"): ("VOLATILITY-16", "swollen, no event"),    # '; none of them is ...'
    ("LEVELS-01", "live 2026-09-29T11:31"): ("LEVELS-01", "no real gap"),
    ("LEVELS-02", "live 2026-09-28T15:00"): ("LEVELS-02", "far below"),             # position only
    ("LEVELS-02", "live 2026-09-29T11:31"): ("LEVELS-02", "near the close"),
    ("LEVELS-02", "live 2026-09-30T13:30"): ("LEVELS-02", "near the close"),
    ("BREADTH-01", "live 2026-09-30T13:30"): ("BREADTH-01", "more up"),             # the middle third keeps its side
    ("FLOW-03", "live 2026-09-28T15:00"): ("FLOW-03", "normal"),                     # the size's own rank, 11 of 20
    ("FLOW-03", "live 2026-09-29T11:31"): ("FLOW-03", "normal"),                     # 13 of 20
    ("MACRO-10", "premarket 2026-09-28T09:28"): ("MACRO-09-ARC", "held"),           # merged: the arc word only
    ("MACRO-10", "premarket 2026-09-29T09:28"): ("MACRO-09-ARC", "built"),
    ("MACRO-10", "premarket 2026-09-30T09:28"): ("MACRO-09-ARC", "built"),
    ("EVENTS-04", "live 2026-09-28T14:01"): ("EVENTS-04", "down burst held"),
    ("EVENTS-04", "live 2026-10-01T10:30"): ("EVENTS-04", None),                    # inside a release's first 15 minutes: EVENTS-03's
    ("EVENTS-04", "live 2026-10-02T10:31"): ("EVENTS-04", "down burst given back"),
}


def nested(labels: dict[str, str]) -> dict:
    out: dict = {}
    for key, sentence in labels.items():
        group, name = key.split(".", 1)
        out.setdefault(group, {})[name] = sentence
    return out


def record(row_ts: str, labels: dict[str, str] | None = None, **more) -> dict:
    return {"read_id": f"live:{row_ts}", "lane": "live", "row_ts": row_ts, "sigma": SIGMA, "labels": nested(labels or {}), **more}


# ---------------------------------------------------------------- the catalog

def test_catalog_has_the_79_columns_with_valid_layers_groups_and_options():
    rows = load_catalog()
    assert len(rows) == 79 and len({q["id"] for q in rows}) == 79              # 78 questions; MACRO-09 answers in two columns
    for q in rows:
        assert q["layer"] in (1, 2) and isinstance(q["group"], str) and "/" in q["group"] and "(" not in q["group"]
        assert q["options"] and len(set(q["options"])) == len(q["options"]) and all(isinstance(o, str) and o for o in q["options"])
        assert q["method"] in ("label", "bars", "market_value") and q["title"] and q["notes"]
        assert sum((q["id"] in LABEL_PARSERS, q["id"] in BARS_ANSWERERS, q["id"] in MARKET_ANSWERERS)) == 1 and "status" not in q
        assert q.get("votes", True) in (True, False)
        if q["method"] == "label":
            assert q["label_keys"]
    assert {q["id"] for q in rows if q.get("votes") is False} == NON_VOTING
    assert {q["new_id"] for q in MAPPING} - ROUND_2_DROPPED | ROUND_2_NEW == set(CATALOG)
    assert not (set(LABEL_PARSERS) | set(BARS_ANSWERERS) | set(MARKET_ANSWERERS)) - set(CATALOG)       # no answerer left behind


def test_the_pressure_tests_shared_groups_give_a_counted_twice_fact_one_vote():
    """The pairs that measured one fact in two groups now share one; the groups they left are gone."""
    group = {qid: q["group"] for qid, q in CATALOG.items()}
    assert group["FLOW-02"] == group["FLOW-07"] == group["TREND-01"] == "trend/last_30m_move"
    assert group["TREND-03"] == group["TREND-05"] == group["TREND-09"] == "trend/leg_aging" and group["TREND-08"] == "trend/pace_change"
    assert group["TREND-07"] == group["FLOW-01"] and group["VOLATILITY-08"] == group["LEVELS-01"] == group["LEVELS-02"]
    assert group["VOLATILITY-18"] == group["VOLATILITY-04"] and group["MACRO-09-ARC"] == group["MACRO-09"]
    assert group["EVENTS-04"] == "events/burst_state" and group["OPTIONS-05"] == "options/strike_defense"
    assert not {"trend/late_leg", "flow/signed_flow", "volatility/gap_vs_implied", "levels/prior_day_location"} & set(group.values())


# ---------------------------------------------------------------- the label parsers on the mapping's real sentences

# the prototype's three out-of-option strings and what the builder answers instead
EXPECTED_INSTEAD = {"inside (old 09-28 template, side not stated)": None, "flat (old template, from rank)": "flat",
                    "ahead down (contrarian: lean up)": "ahead down", "ahead up (contrarian: lean down)": "ahead up"}
LABEL_EXAMPLES = [(m["new_id"], ex) for m in MAPPING if m["method"] == "label" and m["new_id"] not in ROUND_2_DROPPED - {"MACRO-10"}
                  for ex in m["examples"]]


@pytest.mark.parametrize("qid,example", LABEL_EXAMPLES, ids=[f"{q}:{e['read']}" for q, e in LABEL_EXAMPLES])
def test_each_label_parser_reproduces_the_archived_example(qid, example):
    qid, expected = ROUND_2_ANSWERS.get((qid, example["read"]), (qid, EXPECTED_INSTEAD.get(example["answer"], example["answer"])))
    rec = record(example["row_ts"], example["labels"])
    premarket = qid in PREMARKET_QUESTIONS or qid == "LEVELS-08"
    history = MarketHistory(premarket_reads=[{**rec, "lane": "premarket"}] if premarket else [], diary_rows=NATIVE_BOOK)
    answers = answer_code_features(rec, [], history)
    assert answers[qid] == expected
    assert expected is None or expected in CATALOG[qid]["options"]


def test_the_options_book_questions_are_silent_on_a_spy_stand_in_book():
    """On 10-06/07 every diary row was SPY's book scaled to SPX: OPTIONS-01/02/03/04/06/09/10/11 answer only on SPX's own."""
    example = next(ex for m in MAPPING if m["new_id"] == "OPTIONS-01" for ex in m["examples"])
    rec = record(example["row_ts"], example["labels"])
    stand_in = [{"ts": "2026-09-01T09:30:00-04:00", "gex_source": "spy_proxy×10.0361"}]
    assert answer_code_features(rec, [], MarketHistory(diary_rows=NATIVE_BOOK))["OPTIONS-01"] is not None
    assert answer_code_features(rec, [], MarketHistory(diary_rows=stand_in))["OPTIONS-01"] is None
    assert answer_code_features(rec, [], MarketHistory())["OPTIONS-01"] is None                  # no diary row: not known to be native


@pytest.mark.parametrize("labels, hhmm, answer", [
    ({"price.afternoon_leg": "since 14:00 price has risen 0.13 sigma, larger than 17 of the last 19 sessions at this minute, top third: a strong move"}, "14:45", "strong up"),
    ({"price.prior_close_push": "over the last 30 minutes price rose 0.01 sigma, bottom third; it sits 0.40 sigma above yesterday's close, top third, away from yesterday's close"}, "11:00", "with the day's side"),
    ({"gap.fill_progress": "the gap down is losing ground after 296 minutes: price sits 0.31 sigma below yesterday's close and keeps 46% of the 0.68 sigma gap, below the half line; it has not touched yesterday's close (within 0.02 sigma); the gap was larger than 15 of the last 19 days' gaps, top third, a real gap"}, "14:31", "fading"),
    ({"gap.fill_progress": "the gap up is losing ground after 90 minutes: price sits 0.01 sigma above yesterday's close and keeps 4% of the 0.30 sigma gap, below the half line; it first touched yesterday's close (within 0.02 sigma) 12 minutes ago; the gap was larger than 9 of the last 19 days' gaps, middle third, a real gap"}, "11:00", "filled"),
    ({"leaders.equal_weight_vs_cap_30m": "equal-weight RSP usually moves 0.82 times the index (SPY), so 0.10 sigma was expected over the last 30 minutes; it rose 0.02 sigma, 0.08 sigma below that, by size higher than 9 of the last 19 sessions at this minute, middle third: no split"}, "11:00", "more down"),
    ({"leaders.equal_weight_vs_cap_30m": "equal-weight RSP usually moves 0.82 times the index (SPY), so 0.10 sigma was expected over the last 30 minutes; it rose 0.11 sigma, 0.01 sigma above that, by size higher than 2 of the last 19 sessions at this minute, bottom third: no split"}, "11:00", "as expected"),
    ({"sectors.agreement_30m": "over the last 30 minutes 9 of 11 sector funds moved the index's way, each by a move above the bottom third of its own at this minute; that count is higher than 18 of the last 20 sessions at this minute, top third: one way; sector dispersion beyond each fund's usual multiple was 0.06 sigma, higher than 2 of the last 19 sessions at this minute, bottom third: tight"}, "11:00", "more together"),
    ({"liquidity.spy_quote": "over the last 5 minutes SPY's quoted spread has been 1 cent, wider than on 0 of the last 20 sessions at this minute, at the tight tick; the size showing at SPY's best bid and offer combined is in the bottom fifth for 12:31, higher than 2 of the last 20 sessions at this minute"}, "12:31", "thin"),
    ({"skew.put_tilt_vs_usual": "same-day 25-delta puts are priced 2.0 vol points above 25-delta calls, 0.10 of the at-the-money level; steeper than 14 of the last 18 sessions at 11:00"}, "11:00", "usual"),     # 0.78: under the label's top fifth
    ({"skew.put_tilt_vs_usual": "same-day 25-delta puts are priced 2.4 vol points above 25-delta calls, 0.12 of the at-the-money level; steeper than 16 of the last 18 sessions at 11:00"}, "11:00", "steep"),
    ({"vol.ruler_event_load": "normal: the same-day straddle prices a 30-minute move 0.6 times what the tape's recent 5-minute ranges scale to, higher than 9 of the last 18 sessions at 11:00 (between the top and bottom fifths); on the event calendar today: the job openings report at 10:00, the Fed's rate decision at 14:00; the Fed's rate decision is still ahead; this morning's sigma ruler is 0.90 times its 19-session median, larger than 3 of the last 19 sessions' morning rulers, bottom third"}, "11:00", "compressed, event ahead"),
    ({"vol.ruler_event_load": "normal: the same-day straddle prices a 30-minute move 0.6 times what the tape's recent 5-minute ranges scale to, higher than 9 of the last 18 sessions at 15:00 (between the top and bottom fifths); on the event calendar today: the Fed's rate decision at 14:00; the Fed's rate decision came out 60 minutes ago; this morning's sigma ruler is 0.90 times its 19-session median, larger than 3 of the last 19 sessions' morning rulers, bottom third"}, "15:00", "compressed, event released"),
    ({"shock.burst": "16 minutes ago price rose 0.19 sigma in 5 minutes, larger than the biggest five-minute move of the hour to that minute on 19 of the last 19 sessions, past the shock rule; not at a scheduled release time; the burst ended 11 minutes ago, older than the 10-minute fresh window; since then price has given back 60% of it, past the half line"}, "13:30", "up burst given back"),
])
def test_the_round_2_parsers_on_the_label_wordings(labels, hhmm, answer):
    assert answer_code_features(record(f"{DAY}T{hhmm}:30-04:00", labels), [], MarketHistory())[next(
        q for q in CATALOG if CATALOG[q]["label_keys"] and set(CATALOG[q]["label_keys"]) >= set(labels) and q in LABEL_PARSERS)] == answer


def test_the_old_volatility_16_wording_without_its_none_clause_falls_back_to_the_loading_events():
    """Labels before ~10-02 list the calendar without '; none of them is': only the Fed's decision, testimony or Jackson Hole count."""
    old = ("normal: the same-day straddle prices a 30-minute move 0.6 times what the tape's recent 5-minute ranges scale to, higher than 9 "
           "of the last 18 sessions at 11:00 (between the top and bottom fifths); on the event calendar today: the quarter-end close at 16:00, "
           "a Fed governor at 12:40; this morning's sigma ruler is 1.19 times its 19-session median, larger than 15 of the last 19 sessions' "
           "morning rulers, top third")
    assert answer_code_features(record(f"{DAY}T11:00:30-04:00", {"vol.ruler_event_load": old}), [], MarketHistory())["VOLATILITY-16"] == "swollen, no event"


def test_macro_09_and_its_arc_answer_the_first_cash_hours_only_and_a_night_with_no_finished_leg_is_quiet():
    night = {"premarket.legs": "oldest first: after hours and Asia quiet so far. No finished leg of the night moved",
             "premarket.arc": "futures are 0.03 sigma below their 16:00 price; no finished leg of the night moved past the bottom third of the last nights"}
    pre = {**record(f"{DAY}T09:28:00-04:00", night), "lane": "premarket"}
    early = answer_code_features(record(f"{DAY}T10:32:00-04:00"), [], MarketHistory(premarket_reads=[pre]))
    late = answer_code_features(record(f"{DAY}T11:02:00-04:00"), [], MarketHistory(premarket_reads=[pre]))
    assert (early["MACRO-09"], early["MACRO-09-ARC"]) == ("night quiet", "no real move") and late["MACRO-09"] is late["MACRO-09-ARC"] is None


def test_premarket_questions_take_the_days_last_premarket_read_before_the_read():
    night = {"overnight.range_vs_normal": "the night's range is in the top third of prior nights", "premarket.legs": "x so far. net move is quiet"}
    earlier = {**record(f"{DAY}T08:05:00-04:00", {"overnight.range_vs_normal": "bottom third"}), "lane": "premarket"}
    latest = {**record(f"{DAY}T09:28:00-04:00", night), "lane": "premarket"}
    later = {**record(f"{DAY}T12:00:00-04:00", {"overnight.range_vs_normal": "middle third"}), "lane": "premarket"}
    answers = answer_code_features(record(f"{DAY}T10:02:00-04:00"), [], MarketHistory(premarket_reads=[earlier, latest, later]))
    assert answers["LEVELS-08"] == "large" and answers["MACRO-09"] == "night quiet" and answers["MACRO-09-ARC"] is None
    assert answer_code_features(record(f"{DAY}T10:02:00-04:00"), [], MarketHistory())["LEVELS-08"] is None


# ---------------------------------------------------------------- silence, never an error

CALENDAR_QUESTIONS = ("EVENTS-02", "EVENTS-03")      # read the skill's own calendar, which covers DAY


def silent_values(answers: dict) -> set:
    """Every answer but the calendar questions', which answer from calendar/events.json even with no other data."""
    assert all(answers[q] is None or answers[q] in CATALOG[q]["options"] for q in CALENDAR_QUESTIONS)
    return {v for q, v in answers.items() if q not in CALENDAR_QUESTIONS}


def test_unknown_templates_and_missing_data_answer_none_without_raising():
    keys = {k for q in load_catalog() for k in q["label_keys"]}
    garbage = record(f"{DAY}T11:00:00-04:00", {k: "lorem ipsum dolor sit amet" for k in keys})
    assert silent_values(answer_code_features(garbage, [], MarketHistory())) == {None}
    assert silent_values(answer_code_features({"row_ts": f"{DAY}T11:00:00-04:00"}, [], None)) == {None}
    broken_bars = [{"ts": f"{DAY}T09:30:00-04:00"}] * 100
    assert silent_values(answer_code_features({"row_ts": f"{DAY}T11:00:00-04:00", "sigma": "?"}, broken_bars, MarketHistory())) == {None}
    assert set(answer_code_features(record(f"{DAY}T11:00:00-04:00"), [], MarketHistory()).keys()) == set(CATALOG)


# ---------------------------------------------------------------- the bars questions on synthetic bars

def closes(n: int, start: float = 7700.0, step: float = 0.0, noise: float = 0.0, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        p += step + rng.uniform(-noise, noise)
        out.append(p)
    return out


def prior_bars(days=PRIOR_DAYS, n: int = 390, noise: float = 1.0, step: float = 0.0) -> dict[str, list[dict]]:
    """Prior sessions that wander: every minute's measures have a spread to rank against."""
    return {d: bars_from_closes(closes(n, noise=noise, step=step, seed=i + 1), day=d) for i, d in enumerate(days)}


def ask(qid: str, today: list[dict], hhmm: str = "11:30", history: MarketHistory | None = None, **more):
    row_ts = f"{DAY}T{hhmm}:30-04:00"
    rec = record(row_ts, **more)
    return answer_code_features(rec, bars_up_to(today, row_ts), history or MarketHistory(spx_bars_by_day=prior_bars()))[qid]


def test_bars_up_to_keeps_only_finished_minutes():
    bars = bars_from_closes([1.0] * 5, day=DAY)
    assert len(bars_up_to(bars, f"{DAY}T09:33:00-04:00")) == 3 and len(bars_up_to(bars, f"{DAY}T09:32:59-04:00")) == 2


def test_trend_10_break_held_failed_or_none():
    base = [7700.0 + (i % 5) * 0.5 for i in range(110)]                                       # a 7700-7702 band
    assert ask("TREND-10", bars_from_closes(base + [7701.0] * 10, wick=0.1)) == "none"
    assert ask("TREND-10", bars_from_closes(base + [7720.0] * 10, wick=0.1)) == "up held"
    assert ask("TREND-10", bars_from_closes(base + [7720.0] * 5 + [7690.0] * 5, wick=0.1)) == "up failed"
    assert ask("TREND-10", bars_from_closes(base + [7680.0] * 10, wick=0.1)) == "down held"
    assert ask("TREND-10", bars_from_closes(base[:50], wick=0.1)) is None                       # under 70 bars
    assert ask("TREND-10", bars_from_closes(base + [7720.0] * 10, wick=0.1), sigma=None) is None


TWENTY_DAYS = PRIOR_DAYS + ("2026-09-02", "2026-09-01", "2026-08-31", "2026-08-28", "2026-08-27", "2026-08-26", "2026-08-25", "2026-08-24",
                           "2026-08-21", "2026-08-20")


def test_trend_05_one_way_hour_against_the_same_minute():
    history = MarketHistory(spx_bars_by_day=prior_bars(days=TWENTY_DAYS))
    assert ask("TREND-05", bars_from_closes(closes(120, step=0.5)), history=history) == "one-way up"
    assert ask("TREND-05", bars_from_closes(closes(120, step=-0.5)), history=history) == "one-way down"
    assert ask("TREND-05", bars_from_closes([7700.0] * 120), history=history) == "no hour move"
    zigzag = [7700.0 + (12.0 if i % 10 < 5 else 0.0) for i in range(115)] + [7703.0 + 3.0 * i for i in range(5)]
    assert ask("TREND-05", bars_from_closes(zigzag), history=history) == "two-way up"
    assert ask("TREND-05", bars_from_closes(closes(120, step=0.5)), history=MarketHistory()) is None   # no history to rank against
    # the efficiency is ranked only against the hours that moved: ten sessions leave too few of them
    assert ask("TREND-05", bars_from_closes(closes(120, step=0.5))) is None


def test_trend_05_ranks_efficiency_only_against_the_hours_that_moved():
    """Prior hours that went nowhere were all perfectly one-way (efficiency 1): against every hour today's tidy rise ranks
    low; against the hours that moved (which wander) it ranks high."""
    still = {d: bars_from_closes([7700.0 + 0.001 * i for i in range(390)], day=d) for d in TWENTY_DAYS[:7]}
    moving = prior_bars(days=TWENTY_DAYS[7:], noise=3.0)
    history = MarketHistory(spx_bars_by_day={**still, **moving})
    assert ask("TREND-05", bars_from_closes(closes(120, step=0.5, noise=0.6, seed=7)), history=history) == "one-way up"


def two_phase(last_step: float, day: str = DAY) -> list[dict]:
    """Rises 1 point a minute to 11:19, then ``last_step`` a minute: the pace ratio at 11:30 is exactly ``last_step``."""
    return bars_from_closes([7700.0 + i for i in range(110)] + [7809.0 + last_step * i for i in range(1, 11)], day=day)


def test_trend_08_reversing_accelerating_fading():
    history = MarketHistory(spx_bars_by_day={d: two_phase(0.2 * (i + 1), d) for i, d in enumerate(PRIOR_DAYS)})   # ratios 0.2 .. 2.0
    assert ask("TREND-08", two_phase(-0.8), history=history) == "up reversing"
    assert ask("TREND-08", two_phase(-0.3), history=history) == "up fading"         # opposite but within 0.05 sigma (3.75 points): ranked
    assert ask("TREND-08", two_phase(3.0), history=history) == "up accelerating"
    assert ask("TREND-08", two_phase(1.0), history=history) == "up steady"
    assert ask("TREND-08", two_phase(0.001), history=history) == "up fading"
    falling = bars_from_closes([7900.0 - i for i in range(110)] + [7791.0 + 0.8 * i for i in range(1, 11)])
    assert ask("TREND-08", falling, history=history) == "down reversing"
    quiet_leg = bars_from_closes([7700.0] * 100 + [7700.0 + 0.15 * i for i in range(1, 21)])   # d20 under 0.05 sigma: no leg to age
    assert ask("TREND-08", quiet_leg, history=history) is None


def test_trend_09_leg_and_pullback():
    assert ask("TREND-09", bars_from_closes([7700.0] * 120)) == "no leg"
    straight = closes(120, step=0.5)
    assert ask("TREND-09", bars_from_closes(straight)) == "up leg shallow-slow"
    fast_pullback = [7700.0] * 60 + [7700.0 + 0.8 * i for i in range(1, 51)] + [7740.0 - 1.6 * i for i in range(1, 11)]
    assert ask("TREND-09", bars_from_closes(fast_pullback)) == "up leg deep-or-fast"
    assert ask("TREND-09", bars_from_closes(closes(120, step=-0.5))) == "down leg shallow-slow"


def test_trend_09_a_pullback_from_an_extreme_two_minutes_old_is_not_fast():
    """2.5 points off a high set 2 minutes ago read 'fast' (2.5 / 2 a minute against 29.5 / 58): 'fast' waits 5 minutes."""
    young = [7700.0] * 60 + [7700.0 + 0.5 * i for i in range(1, 59)] + [7727.0, 7727.0]
    assert ask("TREND-09", bars_from_closes(young)) == "up leg shallow-slow"


def test_trend_12_burst_when_one_minute_dwarfs_the_median():
    calm = bars_from_closes([7700.0] * 120, wick=0.5)
    assert ask("TREND-12", calm) == "normal"
    spike = bars_from_closes([7700.0] * 119 + [7701.0], wick=0.5)
    spike[-1]["high"], spike[-1]["low"] = 7720.0, 7690.0
    assert ask("TREND-12", spike) == "burst"


def test_levels_09_the_opening_range_from_10_05():
    history = MarketHistory(spx_bars_by_day=prior_bars(noise=0.5))
    wide = bars_from_closes([7700.0, 7720.0, 7690.0, 7725.0, 7700.0] + [7700.0] * 60)
    narrow = bars_from_closes([7700.0] * 65, wick=0.05)
    assert ask("LEVELS-09", wide, "09:40", history) is None and ask("LEVELS-09", narrow, "10:04", history) is None    # the first five minutes are not asked
    wide_later = bars_from_closes([7700.0] * 5 + [7700.0 + (i % 2) * 30 for i in range(60)])
    assert ask("LEVELS-09", wide_later, "10:30", history) == "wide" and ask("LEVELS-09", narrow, "10:30", history) == "narrow"


BOTH_EDGES = ("price is back inside the first hour's range; the first hour spanned 0.31 sigma; since 10:30 price broke both edges: above "
              "its high {up} minutes ago, reached 0.03 sigma beyond it, further than 0 of the 14 first-hour breaks the last 19 sessions had "
              "made by this minute, bottom third; and below its low {down} minutes ago, reached 0.40 sigma beyond it, further than 12 of the 14 "
              "first-hour breaks the last 19 sessions had made by this minute, top third")


def test_levels_06_inside_the_buffer_and_both_edges_broken():
    buffer = {"range.first_hour": "price is above the first hour's high, 0.01 sigma beyond it; the first hour spanned 0.40 sigma; since 10:30 "
                                  "no bar has gone more than the 0.03 sigma break buffer past either edge"}
    assert ask("LEVELS-06", [], "11:31", labels=buffer) == "inside"                       # within the buffer: no break at all
    assert ask("LEVELS-06", [], "12:30", labels={"range.first_hour": BOTH_EDGES.format(up=2, down=111)}) == "up break failed"
    assert ask("LEVELS-06", [], "12:30", labels={"range.first_hour": BOTH_EDGES.format(up=111, down=2)}) == "down break failed"
    held = {"range.first_hour": "price is below the first hour's low, 0.27 sigma beyond it; the first hour spanned 0.31 sigma; since 10:30 price "
                                "broke below its low 52 minutes ago, reached 0.40 sigma beyond it, further than 10 of the 11 first-hour breaks the "
                                "last 19 sessions had made by this minute, top third, and never broke its high"}
    assert ask("LEVELS-06", [], "11:31", labels=held) == "down break holding"


def test_levels_06_the_box_broken_both_ways_takes_the_side_the_bars_broke_last():
    both = {"range.box_status": "price is inside the opening box; earlier today it broke out both ways and came back; the box is 0.21 sigma wide"}
    up_then_down = [7700.0] * 32 + [7710.0] + [7700.0] * 5 + [7690.0] + [7700.0] * 6           # 10:02 above the box, 10:08 below it
    assert ask("LEVELS-06", bars_from_closes(up_then_down), "10:15", labels=both) == "down break failed"
    down_then_up = [7700.0] * 32 + [7690.0] + [7700.0] * 5 + [7710.0] + [7700.0] * 6
    assert ask("LEVELS-06", bars_from_closes(down_then_up), "10:15", labels=both) == "up break failed"
    assert ask("LEVELS-06", [], "10:15", labels=both) is None                              # no bars: the side cannot be told


def es_night(day: str, span: float) -> list[dict]:
    """A night's 1-minute /ES bars from the 18:00 reopen to 09:29 swinging over ``span`` points."""
    start = at(18, 0, day) - timedelta(days=1)
    out = []
    for i in range(15 * 60 + 30):
        price = 7700.0 + span * ((i % 60) / 59)
        out.append({"ts": (start + timedelta(minutes=i)).isoformat(), "open": price, "high": price, "low": price, "close": price})
    return out


def test_levels_08_falls_back_to_the_nights_own_range_without_a_premarket_label():
    prior = {d: es_night(d, 10.0 + i) for i, d in enumerate(PRIOR_DAYS)}
    wide = MarketHistory(es_night_bars_by_day={**prior, DAY: es_night(DAY, 40.0)})
    narrow = MarketHistory(es_night_bars_by_day={**prior, DAY: es_night(DAY, 2.0)})
    assert ask("LEVELS-08", [], "10:02", wide) == "large" and ask("LEVELS-08", [], "10:02", narrow) == "small"
    labelled = MarketHistory(es_night_bars_by_day=wide.es_night_bars_by_day, premarket_reads=[
        {**record(f"{DAY}T09:28:00-04:00", {"overnight.range_vs_normal": "since the 18:00 reopen their range is 0.2 sigma, bottom third"}), "lane": "premarket"}])
    assert ask("LEVELS-08", [], "10:02", labelled) == "small"                            # the label wins when there is one
    assert ask("LEVELS-08", [], "10:02", MarketHistory(es_night_bars_by_day={DAY: es_night(DAY, 40.0)})) is None


def test_flow_08_drive_test_then_drive_rotation():
    prior = prior_bars()
    prior_high = max(b["high"] for b in prior[PRIOR_DAYS[0]])
    history = MarketHistory(spx_bars_by_day=prior)
    drive = bars_from_closes([7700.0 + 1.0 * i for i in range(70)])
    assert ask("FLOW-08", drive, "10:32", history) == "drive up"
    assert ask("FLOW-08", bars_from_closes([7700.0 - 1.0 * i for i in range(70)]), "10:32", history) == "drive down"
    assert ask("FLOW-08", drive, "10:02", history) is None                     # from 10:30: at 10:00 the window is TREND-02's
    back_through = bars_from_closes([7700.0, 7700.0, 7690.0] + [7690.0 + 1.5 * i for i in range(1, 68)])   # dips 10 under the settled open
    assert ask("FLOW-08", back_through, "10:32", history) != "drive up"
    tested = bars_from_closes([7700.0, 7702.0, prior_high - 0.5, prior_high + 0.5] + [prior_high - 1.0 * i for i in range(1, 67)])
    assert ask("FLOW-08", tested, "10:32", history) == "test-then-drive down"
    # a reference tagged only after the drive already reached the far edge is no test-then-drive
    one_level = MarketHistory(spx_bars_by_day={PRIOR_DAYS[0]: bars_from_closes([7700.0] * 389 + [7712.0], day=PRIOR_DAYS[0], wick=0.5)})
    fall = [7705.0, 7705.0] + [7705.0 - 3.0 * i for i in range(1, 6)]                              # 09:32-09:36 at the low edge
    tag_late = fall + [7690.0 + 2.2 * i for i in range(1, 10)] + [7709.8 - 1.4 * i for i in range(1, 15)] + [7690.0] * 40
    assert ask("FLOW-08", bars_from_closes(tag_late, wick=0.5), "10:32", one_level) == "rotation"
    tag_first = [7705.0, 7705.0, 7709.0, 7711.0] + [7711.0 - 1.5 * i for i in range(1, 15)] + [7690.0] * 52
    assert ask("FLOW-08", bars_from_closes(tag_first, wick=0.5), "10:32", one_level) == "test-then-drive down"
    chop = bars_from_closes([7700.0 + (i % 4) * 5 for i in range(70)])                      # ends mid-range
    assert ask("FLOW-08", chop, "10:32", history) == "rotation"



# ---------------------------------------------------------------- the raw record and the matrix

def test_record_code_features_writes_once_and_rereads_the_stored_line(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(code_feature_inputs, "load_market_history", lambda *a: MarketHistory())
    rec = record(f"{DAY}T11:00:00-04:00", {"levels.break_armed": "a break upward is armed, 0.3 sigma above"})
    first = code_feature_inputs.record_code_features(root, tmp_path, "live", DAY, rec, "live")
    again = code_feature_inputs.record_code_features(root, tmp_path, "live", DAY, {**rec, "labels": {}}, "live")
    lines = paths.read_json_lines(paths.raw_code_features_file(root, DAY))
    assert first["LEVELS-11"] == "armed up" and again == first and len(lines) == 1
    assert lines[0]["read_id"] == rec["read_id"] and lines[0]["source"] == "live" and lines[0]["day"] == DAY and lines[0]["created_at"]


def base_row(read_id: str, day: str) -> Row:
    return Row(read_id=read_id, row_ts=f"{day}T11:00:00-04:00", day=day, sum_id="average_30", historical_odds_probs={"up": 0.2, "flat": 0.6, "down": 0.2},
               jev_own_probs=None, shown_probs=None, pool_v1_probs=None, outcome="flat", learn_exclude=False)


def test_matrix_takes_the_code_columns_and_leaves_a_read_without_a_line_silent(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(answer_matrix, "load_base_rows", lambda *a: [base_row("live:a", "2026-09-16"), base_row("live:b", "2026-09-17")])
    monkeypatch.setattr(answer_matrix, "load_jev_answers", lambda *a: ({"live:a": {"q1": "yes"}}, {"q1": "g"}, {"q1": "2026-09-16"}))
    paths.append_json_line(paths.raw_code_features_file(root, "2026-09-16"), {"read_id": "live:a", "lane": "live", "answers": {"TREND-01": "big up"}})
    paths.append_json_line(paths.raw_code_features_file(root, "2026-09-18"), {"read_id": "live:b", "lane": "live", "answers": {"TREND-01": "flat"}})
    m = build_answer_matrix(tmp_path, "live", "average_30", before_day="2026-09-18")
    assert {c for c in m.columns if c.startswith("code:")} == {f"code:{q}" for q in CATALOG}
    assert not any(c.startswith(("level:", "fact:")) for c in m.columns) and m.columns["jev:q1"]["layer"] == 3
    a, b = m.rows
    assert a.answers["code:TREND-01"] == "big up" and a.answers["jev:q1"] == "yes" and a.answers["code:LEVELS-11"] is None
    assert b.answers["code:TREND-01"] is None and b.answers["jev:q1"] is None            # its line is on a day not before 09-18
    assert set(a.answers) == set(b.answers) == set(m.columns)


def test_the_hook_records_the_code_features_before_forecasting_and_never_raises(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(read_hook, "data_root", lambda s: root)
    monkeypatch.setattr(code_feature_inputs, "load_market_history", lambda *a: MarketHistory())
    empty = answer_matrix.AnswerMatrix(lane="live", sum_id="average_30", built_for_day=DAY, rows=[], columns={})
    monkeypatch.setattr(read_hook, "day_fits_for", lambda *a: DayFits(fits={}, matrix=empty, fit_day=None))

    def boom(*a, **k):
        raise RuntimeError("the forecast step broke")
    monkeypatch.setattr(read_hook, "todays_rows", boom)
    rec = record(f"{DAY}T11:00:00-04:00", {"levels.break_armed": "no break is armed, and none expired"})
    with pytest.raises(RuntimeError):
        read_hook.forecast_read(tmp_path, "live", DAY, rec["read_id"], read_record=rec, hour_record={})
    lines = paths.read_json_lines(paths.raw_code_features_file(root, DAY))
    assert len(lines) == 1 and lines[0]["answers"]["LEVELS-11"] == "nothing armed"
    monkeypatch.setattr(read_hook, "load_archive_read", lambda *a: rec)
    result = read_hook.after_read(tmp_path, "live", rec["read_id"])                          # the detached hook: logged, not raised
    assert result["written"] == 0 and "error" in result


def test_a_code_feature_failure_is_logged_and_the_read_goes_on(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")

    def boom(*a, **k):
        raise OSError("no bars")
    monkeypatch.setattr(read_hook, "record_code_features", boom)
    assert read_hook.code_features_for_read(root, tmp_path, "live", DAY, record(f"{DAY}T11:00:00-04:00"), "live", None) == {}
    log = paths.read_json_lines(paths.job_run_log_file(root))
    assert log[-1]["job_name"] == "code_features" and log[-1]["ok"] is False and "OSError" in log[-1]["error"]


def test_backfill_writes_every_archived_live_read_once(tmp_path):
    archive = tmp_path / "spx_jev" / "archive"
    archive.mkdir(parents=True)
    reads = [record(f"{DAY}T10:02:00-04:00", {"levels.break_armed": "a break downward is armed"}, kind="read"),
             record(f"{DAY}T10:32:00-04:00", {"calendar.expiry_phase": "today is inside the 4-day opex-week window"}, kind="read"),
             {**record(f"{DAY}T09:28:00-04:00", {"premarket.legs": "x so far. net move is quiet"}, kind="read"), "lane": "premarket"}]
    (archive / f"{DAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reads), encoding="utf-8")
    report = run_backfill(tmp_path, "live", DAY, DAY)
    assert report["days"] == {DAY: {"reads": 2, "written": 2, "skipped": 0}}
    assert report["questions"]["LEVELS-11"] == {"answered": 1, "none": 1} and report["questions"]["SESSION-01"] == {"answered": 1, "none": 1}
    assert report["questions"]["MACRO-09"] == {"answered": 2, "none": 0}                     # the premarket read serves both live reads
    root = tmp_path / "spx_jev" / "mirai_prediction"
    lines = paths.read_json_lines(paths.raw_code_features_file(root, DAY))
    assert [l["read_id"] for l in lines] == [reads[0]["read_id"], reads[1]["read_id"]] and all(l["source"] == "backfill" for l in lines)
    assert run_backfill(tmp_path, "live", DAY, DAY)["days"][DAY] == {"reads": 2, "written": 0, "skipped": 2}
    assert len(paths.read_json_lines(paths.raw_code_features_file(root, DAY))) == 2            # idempotent


def test_a_malformed_bar_line_is_skipped_and_the_rest_still_load(tmp_path):
    from spx_jev.mirai_prediction import code_feature_inputs as inputs
    folder = tmp_path / inputs.LIVE_BARS_SUBDIR
    folder.mkdir(parents=True)
    good = {"ts": "2026-10-06T09:31:00-04:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    bad_ts = {"ts": "not a time", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    missing = {"ts": "2026-10-06T09:32:00-04:00", "open": 1.0}
    (folder / "2026-10-06.jsonl").write_text("\n".join(json.dumps(b) for b in (bad_ts, good, missing)) + "\n")
    bars = inputs.load_day_bars(tmp_path, "2026-10-06")
    assert [b["close"] for b in bars] == [1.5]


def test_market_history_loads_each_part_on_its_own(tmp_path, monkeypatch):
    from spx_jev.mirai_prediction import code_feature_inputs as inputs

    def boom(*a, **k):
        raise RuntimeError("the store cannot be read")
    monkeypatch.setattr(inputs, "load_spx_bars_by_day", boom)
    monkeypatch.setattr(inputs, "load_night_bars_by_day", lambda *a, **k: {"2026-10-05": [{"ts": "t"}]})
    monkeypatch.setattr(inputs, "load_spy_minute_volumes", boom)
    history = inputs.load_market_history(tmp_path, "live", "2026-10-06")
    assert history.spx_bars_by_day == {} and history.es_night_bars_by_day == {"2026-10-05": [{"ts": "t"}]}
