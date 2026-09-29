"""The leadership family: equal weight against cap weight, semis, the size funds, the sector funds, the
largest stocks and the opening tells, each against its usual multiple of the index and ranked against the same
minute of the prior sessions, point in time."""
from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from conftest import DAY, at, bars_from_closes
from spx_jev.cuts import MIN_RANK_SESSIONS, SAME_CLOCK_MIN_SESSIONS
from spx_jev.labels.leadership import LABELS, WEIGHTS_FILE, build_leadership_labels
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.market_context import SYMBOLS
from spx_jev.state_builder import MarketContext
import usual_link_fixtures
from usual_link_fixtures import (DRIFT_STEP, NOW, PRIOR_DAYS, READ_MINUTE, SIGMA, follow, index_closes, known, prior_drift, scene_with,
                                 shape)

JUMP = 175                     # a minute inside both the 30- and the 10-minute window before NOW
NAMES = ("NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA")
WEIGHTS = {"NVDA": 0.08, "MSFT": 0.07, "AAPL": 0.065, "AMZN": 0.04, "GOOGL": 0.021, "GOOG": 0.018, "META": 0.03, "AVGO": 0.025,
           "TSLA": 0.021, "BRK.B": 0.017}
MULTIPLES = {"SPY": 1.0, "RSP": 0.66, "SMH": 1.6, "QQQ": 1.2, "IWM": 1.1, "XLF": 1.1,
             **{s: 0.6 + 0.08 * k for k, s in enumerate(SYMBOLS["sectors"]) if s != "XLF"},
             **{s: 1.0 + 0.1 * k for k, s in enumerate(NAMES)}}


# Every other fund or name drifts the other way on the prior days, so on each of them a different set of them moved
# by a move above the bottom third of its own: a count of them ranks with a spread.
ALTERNATE_SECTORS = {s: 1.0 if k % 2 == 0 else -1.0 for k, s in enumerate(SYMBOLS["sectors"])}
ALTERNATE_NAMES = {s: 1.0 if k % 2 == 0 else -1.0 for k, s in enumerate(NAMES)}
CANCELLING_JUMPS = {s: {JUMP: 0.01 if k % 2 else -0.01} for k, s in enumerate(NAMES)}   # half the names jump up, half down
HEAVY_DAYS = 17                # prior days enough for 11 sessions whose trading day before is on file too


def sigma_move(x: float, move_points: float = 0.0) -> float:
    """The stock return worth ``x`` SPX sigma at the read's spot."""
    return x * SIGMA / index_closes(move_points)[READ_MINUTE - 1]


def read(move_points: float = 0.0, jumps: dict[str, dict[int, float]] | None = None, drifts: dict[str, float] | None = None,
         drop: tuple[str, ...] = (), weights: dict | None = None, tmp_path=None, now=NOW, drift_scales: dict[str, float] | None = None,
         days: int = PRIOR_DAYS, shift: float = 0.0):
    """A read at ``now``; a symbol in ``drift_scales`` drifts that multiple of the prior days' drift, and ``shift``
    moves today's whole index by that many points (where today opened against yesterday's close)."""
    closes = [c + shift for c in index_closes(move_points)]
    today = {s: follow(closes, m, jumps=(jumps or {}).get(s), drift=(drifts or {}).get(s, 0.0))
             for s, m in MULTIPLES.items() if s not in drop}
    state_dir = None
    if weights is not None:
        (tmp_path / "spx_leaders").mkdir(exist_ok=True)
        (tmp_path / WEIGHTS_FILE).write_text(json.dumps(weights))
        state_dir = tmp_path
    return scene_with(closes, today, MULTIPLES, now=now, state_dir=state_dir, drift_scales=drift_scales, days=days)


def labels(scene):
    ls = build_leadership_labels(scene)
    return {f"{g}.{k}": s for g, v in ls.state.items() for k, s in v.items()}, ls.omitted, ls.gates


def test_every_label_is_omitted_without_the_market_context():
    _, omitted, gates = labels(replace(read(), market=None))
    assert omitted == {p: "no market-context snapshot today" for p in LABELS}
    # a fact that could not be measured is not "nothing happened": its question is left awake, missing the label
    assert gates == {"heavyweight_gap_split": None, "single_name_shock": None}


# ---- equal weight against cap weight

def test_equal_weight_lagging_the_index_by_a_top_third_split():
    got, _, _ = labels(read(move_points=30.0, jumps={"RSP": {JUMP: -sigma_move(0.13, 30.0)}}))
    s = got["leaders.equal_weight_vs_cap_30m"]
    assert shape(s) == ("equal-weight RSP usually moves # times the index (SPY), so # sigma was expected over the last # minutes; "
                        "it rose # sigma, # sigma below that, by size higher than # of the last # sessions at this minute, top third: a split")
    assert "0.66 times" in s and "so +0.27 sigma was expected" in s and "0.13 sigma below that" in s


@pytest.mark.parametrize("units, beats, verdict", [(7.5, 7, "top third: a split"), (6.5, 6, "middle third: no split"),
                                                   (3.5, 3, "bottom third: no split")])
def test_equal_weight_split_by_its_third(monkeypatch, units, beats, verdict):
    """Prior day k drifts (k + 1) steps a minute past its multiple, so its split is k + 1 units: a split between day
    k's and day k + 1's beats k + 1 of the 10, and the thirds' edges fall at beating 4 and 7."""
    monkeypatch.setattr(usual_link_fixtures, "prior_drift", lambda k: (k + 1) * DRIFT_STEP)
    got, _, _ = labels(read(drifts={"RSP": units * DRIFT_STEP}))
    assert got["leaders.equal_weight_vs_cap_30m"].endswith(f"by size higher than {beats} of the last 10 sessions at this minute, {verdict}")


def test_equal_weight_needs_both_funds_fresh_and_their_history():
    reason = "needs a price for RSP now and 30 minutes ago"
    assert labels(read(drop=("RSP",)))[1]["leaders.equal_weight_vs_cap_30m"] == reason
    scene = read()
    stale = {s: [(t, v) for t, v in pts if t <= at(12, 20)] if s == "SPY" else pts for s, pts in scene.market.known.items()}
    assert labels(replace(scene, market=MarketContext(stale)))[1]["leaders.equal_weight_vs_cap_30m"] == reason.replace("RSP", "SPY")
    no_history = replace(scene, prior_markets={})
    assert labels(no_history)[1]["leaders.equal_weight_vs_cap_30m"] == (
        f"needs {MIN_RANK_SESSIONS} prior sessions of half hours with RSP and SPY to know the usual multiple")


# ---- semis and the opening tells, ranked by the clock

@pytest.mark.parametrize("jump, verdict", [
    (0.2, "beat that by # sigma, ahead upward: in the top fifth for this half hour, higher than # of the last # sessions at this minute"),
    (-0.2, "trailed that by # sigma, ahead downward: in the bottom fifth for this half hour, higher than # of the last # sessions at this minute"),
])
def test_semis_ahead_either_way(jump, verdict):
    got, _, _ = labels(read(move_points=10.5, jumps={"SMH": {JUMP: sigma_move(jump, 10.5)}}))
    s = got["leaders.semis_vs_index_30m"]
    assert shape(s) == "chips (SMH) usually move # times the index; over the last # minutes they " + verdict
    assert "usually move 1.6 times" in s and ("higher than 10 of the last 10" if jump > 0 else "higher than 0 of the last 10") in s


@pytest.mark.parametrize("between, verdict, beats", [(7.5, "ahead upward: in the top fifth", 8), (6.5, "in line: between the top and bottom fifths", 7)])
def test_semis_top_fifth_boundary(between, verdict, beats):
    """Prior day k drifts in order of k, so a drift between day 7's and day 8's beats 8 of the 10: the top fifth exactly."""
    got, _, _ = labels(read(move_points=10.5, drifts={"SMH": prior_drift(0) + between * DRIFT_STEP}))
    s = got["leaders.semis_vs_index_30m"]
    assert verdict in s and f"higher than {beats} of the last 10 sessions at this minute" in s


def test_semis_above_their_multiple_but_below_the_usual_for_this_minute(monkeypatch):
    """Every prior session ran further past its multiple at this minute, so a small beat ranks in the bottom fifth:
    the sentence says why the beat still reads ahead downward."""
    monkeypatch.setattr(usual_link_fixtures, "prior_drift", lambda k: (k + 1) * DRIFT_STEP)
    got, _, _ = labels(read(move_points=10.5, drifts={"SMH": 0.2 * DRIFT_STEP, "XLF": 0.2 * DRIFT_STEP}))
    assert got["leaders.semis_vs_index_30m"] == (
        "chips (SMH) usually move 1.6 times the index; over the last 30 minutes they beat that by 0.01 sigma, below the usual for "
        "this minute, ahead downward: in the bottom fifth for this half hour, higher than 0 of the last 10 sessions at this minute")
    assert got["tells.sector_lead_10m"].count("above their usual multiple of the index, below the usual for this minute, "
                                              "broke away downward, in the bottom fifth for this time") == 2


def test_semis_rank_needs_enough_trusted_sessions():
    scene = read()
    rulers = {d: SigmaRuler(SIGMA, "anchor" if k < SAME_CLOCK_MIN_SESSIONS - 1 else "live") for k, d in enumerate(scene.prior_bars)}
    assert labels(replace(scene, prior_rulers=rulers))[1]["leaders.semis_vs_index_30m"] == (
        f"its rank needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with SMH and $SPX at this minute, have {SAME_CLOCK_MIN_SESSIONS - 1}")


def test_semis_ignore_a_value_known_after_the_read():
    before, _, _ = labels(read(move_points=10.5))
    after, _, _ = labels(read(move_points=10.5, jumps={"SMH": {READ_MINUTE: 0.05}}))
    assert after["leaders.semis_vs_index_30m"] == before["leaders.semis_vs_index_30m"]
    assert "in line" in before["leaders.semis_vs_index_30m"]


def test_the_opening_tells_rank_semis_and_financials_over_10_minutes():
    got, _, _ = labels(read(jumps={"SMH": {JUMP: sigma_move(0.11)}, "XLF": {JUMP: -sigma_move(0.11)}}))
    assert shape(got["tells.sector_lead_10m"]) == (
        "over the last # minutes semiconductors (SMH) ran # sigma above their usual multiple of the index, broke away upward, "
        "in the top fifth for this time, higher than # of the last # sessions at this minute; financials (XLF) ran # sigma below "
        "their usual multiple of the index, broke away downward, in the bottom fifth for this time, higher than # of the last # "
        "sessions at this minute")
    quiet, _, _ = labels(read())
    assert quiet["tells.sector_lead_10m"].count("moved in line, between the top and bottom fifths for this time") == 2


def test_the_opening_tells_need_a_price_10_minutes_back():
    _, omitted, _ = labels(read(drop=("XLF",)))
    assert omitted["tells.sector_lead_10m"] == "needs a price for XLF now and 10 minutes ago"


@pytest.mark.parametrize("now, span", [(at(9, 35, ss=10), 4), (at(9, 40, ss=10), 9), (at(9, 41, ss=10), 10)])
def test_the_first_opening_reads_measure_since_the_first_finished_minute(tmp_path, now, span):
    """No price is known at 09:30: a 10-minute window that would start before 09:31 is cut to the minutes since, on
    today and on each prior day alike, and the sentence says how many."""
    got, _, _ = labels(weighed(tmp_path, now=now, jumps={"AAPL": {2: -0.019}}))
    assert got["tells.sector_lead_10m"].startswith(f"over the last {span} minutes semiconductors (SMH) ran ")
    assert got["tells.sector_lead_10m"].count("higher than 5 of the last 10 sessions at this minute") == 2
    s = got["leaders.single_name_10m"]
    assert s.startswith(f"in the last {span} minutes AAPL fell ") and f"times its usual {span}-minute move for this time of day" in s
    assert "by size higher than 10 of the last 10 sessions at this minute, every one: a name shock" in s
    assert s.endswith(f"none of the other 7 largest fell by a top-third {span}-minute move of its own, short of the 2-name spreading count")


# ---- size since the settled open

def test_size_funds_since_the_settled_open():
    got, _, _ = labels(read(jumps={"QQQ": {JUMP: sigma_move(0.9)}, "IWM": {JUMP: -sigma_move(0.9)}}))
    s = got["leaders.size_spread_day"]
    assert shape(s) == ("since #:#, after allowing for their usual swing against SPY, QQQ is # sigma ahead, higher than # of the last # "
                        "sessions at this minute, top third: ahead for this time of day; and IWM is # sigma behind, higher than # of the "
                        "last # sessions at this minute, bottom third: behind for this time of day; QQQ leads by # sigma, by size higher "
                        "than # of the last # sessions at this minute, top third: a lead")
    assert s.startswith("since 09:35") and "leads by 1.80 sigma" in s


def test_size_funds_in_line_and_close_together():
    """QQQ and IWM drift opposite ways on the prior days, so their spread since the open ranks with a spread."""
    got, _, _ = labels(read(jumps={s: {JUMP: sigma_move(0.1)} for s in ("QQQ", "IWM")}, drift_scales={"IWM": -1.0}))
    assert shape(got["leaders.size_spread_day"]).endswith(
        "QQQ is # sigma ahead, higher than # of the last # sessions at this minute, middle third: in line; and IWM is # sigma ahead, "
        "higher than # of the last # sessions at this minute, middle third: in line; the two are # sigma apart, by size higher than "
        "# of the last # sessions at this minute, bottom third: neither leads")


def test_one_size_fund_ahead_and_the_other_behind_with_an_ordinary_gap_is_its_own_case(monkeypatch):
    """QQQ in the top third of its own, IWM in the bottom third of its own and the gap between them no wider than
    usual: neither leads, and the label says one is ahead and one behind rather than leaving small_led to guess."""
    from spx_jev.labels.ranks import SameClockRank
    from spx_jev.labels.usual_link import AgainstIndex
    ranked = {"QQQ and SPY since": SameClockRank(8, 10), "IWM and SPY since": SameClockRank(1, 10),
              "QQQ, IWM and SPY since": SameClockRank(4, 10)}
    real = AgainstIndex.rank
    monkeypatch.setattr(AgainstIndex, "rank", lambda self, value, measure, what: next(
        ((r, None) for key, r in ranked.items() if what.startswith(key)), None) or real(self, value, measure, what))
    got, _, _ = labels(read(jumps={"QQQ": {JUMP: sigma_move(0.1)}, "IWM": {JUMP: -sigma_move(0.1)}}))
    assert shape(got["leaders.size_spread_day"]).endswith(
        "top third: ahead for this time of day; and IWM is # sigma behind, higher than # of the last # sessions at this minute, bottom "
        "third: behind for this time of day; the two are # sigma apart, by size higher than # of the last # sessions at this minute, "
        "middle third: neither leads, one ahead and one behind")


def test_a_size_fund_ahead_of_its_multiple_but_behind_the_usual_for_this_minute(monkeypatch):
    monkeypatch.setattr(usual_link_fixtures, "prior_drift", lambda k: (k + 1) * DRIFT_STEP)
    got, _, _ = labels(read(drifts={"QQQ": 0.2 * DRIFT_STEP}))
    assert ("QQQ is 0.04 sigma ahead, below the usual for this minute, higher than 0 of the last 10 sessions at this minute, "
            "bottom third: behind for this time of day") in got["leaders.size_spread_day"]


def test_size_funds_wait_for_the_settled_open():
    _, omitted, _ = labels(read(now=at(9, 35)))
    assert omitted["leaders.size_spread_day"] == "the settled open is not in until 09:35"


# ---- the sector funds

def test_sector_funds_moving_one_way():
    """Alternate funds drift opposite ways on the prior days, so 6 or 5 of them moved above the bottom third of their own on most of them."""
    got, _, _ = labels(read(move_points=60.0, drift_scales=ALTERNATE_SECTORS))
    assert shape(got["sectors.agreement_30m"]) == (
        "over the last # minutes # of # sector funds moved the index's way, each by a move above the bottom third of "
        "its own at this minute; that count is higher than # of the last # sessions at this minute, top third: one way; sector "
        "dispersion beyond each fund's usual multiple was # sigma, higher than # of the last # sessions at this minute, bottom third: "
        "not wide")
    assert "11 of 11 sector funds" in got["sectors.agreement_30m"] and "higher than 8 of the last 10" in got["sectors.agreement_30m"]


def test_sector_funds_split_under_a_wide_dispersion():
    against = {s: {JUMP: -sigma_move(1.0, 60.0)} for s in SYMBOLS["sectors"][:5]}
    got, _, _ = labels(read(move_points=60.0, jumps=against, drift_scales=ALTERNATE_SECTORS))
    s = got["sectors.agreement_30m"]
    assert "6 of 11 sector funds" in s and "higher than 4 of the last 10 sessions at this minute, middle third: not one way" in s
    assert s.endswith("higher than 10 of the last 10 sessions at this minute, top third: wide")


def test_sector_funds_need_every_fund():
    _, omitted, _ = labels(read(drop=("XLRE",)))
    assert omitted["sectors.agreement_30m"] == "needs a price for XLRE now and 30 minutes ago"


ROTATION_DRIFTS = {**{s: 1.0 for s in ("XLF", "XLY", "XLI")}, **{s: -1.0 for s in ("XLU", "XLP", "XLV")}}


@pytest.mark.parametrize("group, gap, verdict", [
    (("XLF", "XLY", "XLI"), 0.3, "beat utilities, staples and health care by # sigma, higher than # of the last # sessions at this minute, "
                                 "top third: the sensitive sectors ahead"),
    (("XLU", "XLP", "XLV"), 0.3, "trailed utilities, staples and health care by # sigma, higher than # of the last # sessions at this "
                                 "minute, bottom third: the defensives ahead"),
    (("XLU", "XLP", "XLV"), 0.02, "trailed utilities, staples and health care by # sigma, higher than # of the last # sessions at this "
                                  "minute, middle third: level"),
])
def test_rotation_between_sensitive_and_defensive_sectors(group, gap, verdict):
    got, _, _ = labels(read(jumps={s: {JUMP: sigma_move(gap)} for s in group}, drift_scales=ROTATION_DRIFTS))
    assert shape(got["leaders.rotation_30m"]) == (
        "over the last # minutes, after allowing for each sector's usual link to the index, financials, discretionary and "
        "industrials " + verdict)


# ---- the largest stocks, from the index weights on file

def test_the_largest_names_need_the_index_weights(tmp_path):
    _, omitted, gates = labels(read())
    megacap = ("leaders.heavyweight_gap", "leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m", "leaders.single_name_10m")
    assert {p: omitted[p] for p in megacap} == {p: "no state folder to read the index weights from" for p in megacap}
    assert gates == {"heavyweight_gap_split": None, "single_name_shock": None}
    _, omitted, _ = labels(replace(read(), state_dir=tmp_path))
    assert omitted["leaders.pull_vs_rest_30m"] == f"no index weights: {WEIGHTS_FILE} is not written yet"
    _, omitted, _ = labels(read(weights={"as_of": "2026-09-19", "weights": WEIGHTS}, tmp_path=tmp_path))
    assert omitted["leaders.pull_vs_rest_30m"] == f"the index weights in {WEIGHTS_FILE} carry no date on or before {DAY}"
    _, omitted, _ = labels(read(weights={"as_of": "2026-09-17", "weights": {"NVDA": 0.08}}, tmp_path=tmp_path))
    assert omitted["leaders.pull_vs_rest_30m"] == f"the index weights in {WEIGHTS_FILE} name 1 stocks, fewer than the 8 largest"


def weighed(tmp_path, **kw):
    return read(weights={"as_of": "2026-09-17", "weights": WEIGHTS}, tmp_path=tmp_path, **kw)


def test_the_largest_names_move_together(tmp_path):
    got, _, _ = labels(weighed(tmp_path, move_points=60.0, drift_scales=ALTERNATE_NAMES))
    s = got["leaders.megacap_cohesion_30m"]
    assert shape(s) == ("over the last # minutes # of the # largest stocks rose and # fell, each by a move above "
                        "the bottom third of its own at this minute; the larger count, #, is higher than # of the last # sessions at "
                        "this minute, top third: moving together up; NVDA alone supplied #% of the index's # sigma rise, under the #% "
                        "one-name share; the index's move was higher than # of the last # sessions at this minute, top third: a real move")
    assert s.startswith("over the last 30 minutes 8 of the 8 largest") and "the larger count, 8, is higher than 8 of the last 10" in s


def test_one_name_supplies_the_index_move(tmp_path):
    got, _, _ = labels(weighed(tmp_path, move_points=40.0, jumps={"AAPL": {JUMP: 0.04}}))
    s = got["leaders.megacap_cohesion_30m"]
    assert "AAPL alone supplied 59% of the index's 0.54 sigma rise, past the 33% one-name share" in s and s.endswith("top third: a real move")


def test_the_largest_names_cancelling_on_a_flat_index(tmp_path):
    got, _, _ = labels(weighed(tmp_path, jumps=CANCELLING_JUMPS, drift_scales=ALTERNATE_NAMES))
    s = got["leaders.megacap_cohesion_30m"]
    assert s.startswith("over the last 30 minutes 4 of the 8 largest stocks rose and 4 fell, each by a move above the bottom third of its own")
    assert "the larger count, 4, is higher than 0 of the last 10 sessions at this minute, bottom third: not moving together" in s
    assert s.endswith("the index's move was higher than 0 of the last 10 sessions at this minute, bottom third: no real move")


def cohesion_answer(sentence: str) -> str:
    """The megacap_cohesion option the label's sentence answers, by the question's criteria in their order: one_name
    first, then together_up / together_down, then cancelling, else drifting."""
    up, down = map(int, re.match(r"over the last \d+ minutes (\d+) of the \d+ largest stocks rose and (\d+) fell", sentence).groups())
    if "one-name share" in sentence and ", past the " in sentence and sentence.endswith(": a real move"):
        return "one_name"
    together = re.search(r": moving together (up|down);", sentence)
    if together:
        return f"together_{together.group(1)}"
    return "cancelling" if up >= 2 and down >= 2 else "drifting"


@pytest.mark.parametrize("kw, answer", [
    ({"move_points": 60.0, "drift_scales": ALTERNATE_NAMES}, "together_up"),
    ({"move_points": -60.0, "drift_scales": ALTERNATE_NAMES}, "together_down"),
    ({"move_points": 40.0, "jumps": {"AAPL": {JUMP: 0.04}}}, "one_name"),
    ({"move_points": 40.0, "jumps": {"AAPL": {JUMP: 0.04}}, "drift_scales": ALTERNATE_NAMES}, "one_name"),  # moving together up too
    ({"jumps": CANCELLING_JUMPS, "drift_scales": ALTERNATE_NAMES}, "cancelling"),
    ({"move_points": 10.5, "jumps": {"AAPL": {JUMP: 0.01}}}, "drifting"),         # past the one-name share of no real move
    ({}, "drifting"),
])
def test_the_megacap_sentence_answers_one_option_by_the_criteria_order(tmp_path, kw, answer):
    got, _, _ = labels(weighed(tmp_path, **kw))
    assert cohesion_answer(got["leaders.megacap_cohesion_30m"]) == answer


@pytest.mark.parametrize("move, jump, lead, rest, verdict", [
    (0.0, 0.0, "bottom", "bottom", "neither part moved (both in the bottom third)"),
    (60.0, 0.0, "top", "top", "both parts moved (each above the bottom third), the same way"),
    (60.0, -0.02, "top", "top", "both parts moved (each above the bottom third), in opposite directions"),
    (15.0, 0.004, "top", "bottom", "only the largest names moved (above the bottom third)"),
    (60.0, -0.01, "bottom", "top", "only the rest moved (above the bottom third)"),
])
def test_the_largest_names_against_the_rest(tmp_path, move, jump, lead, rest, verdict):
    got, _, _ = labels(weighed(tmp_path, move_points=move, jumps={s: {JUMP: jump} for s in NAMES}))
    s = got["leaders.pull_vs_rest_30m"]
    assert shape(s) == (f"over the last # minutes the # largest names (#% of the index) contributed # sigma, by size higher than # of the "
                        f"last # sessions at this minute, {lead} third, and the other #% contributed # sigma, by size higher than # of the "
                        f"last # sessions at this minute, {rest} third; {verdict}")
    assert "the 8 largest names (37% of the index)" in s                         # GOOG counted with GOOGL, BRK.B left out


def test_a_single_name_shock_alone_and_spreading_wakes_its_question(tmp_path):
    got, _, gates = labels(weighed(tmp_path, jumps={"AAPL": {JUMP: -0.019}}))
    s = got["leaders.single_name_10m"]
    assert shape(s) == ("in the last # minutes AAPL fell #%, # times its usual #-minute move for this time of day, by size higher than # "
                        "of the last # sessions at this minute, every one: a name shock, worth # sigma of SPX at its #% weight; none of "
                        "the other # largest fell by a top-third #-minute move of its own, short of the #-name spreading count")
    assert "fell 1.9%" in s and "at its 6.5% weight" in s and "higher than 10 of the last 10" in s
    assert gates["single_name_shock"] is None
    got, _, _ = labels(weighed(tmp_path, jumps={"AAPL": {JUMP: -0.019}, "MSFT": {JUMP: -0.008}, "META": {JUMP: -0.008}}))
    assert got["leaders.single_name_10m"].endswith("2 of the other 7 largest also fell by a top-third 10-minute move of their own, "
                                                   "at least the 2-name spreading count")


def test_a_single_name_short_of_every_prior_session_is_no_shock_and_sleeps_its_question(tmp_path):
    got, _, gates = labels(weighed(tmp_path, jumps={"AAPL": {READ_MINUTE: -0.05}}))
    s = got["leaders.single_name_10m"]
    assert "not every one: no name shock" in s
    name = s.split()[5]                                           # "in the last 10 minutes NVDA rose ..."
    assert shape(gates["single_name_shock"]) == ("none of the # largest stocks moved more over the last # minutes than on every one of "
                                                 f"its own recent sessions at this minute (the furthest, {name}, higher than # of the "
                                                 "last # sessions at this minute)")


# ---- the heavyweight gap and its gate

def gapped(tmp_path, name: str, ret: float, now=NOW, days: int = HEAVY_DAYS, shift: float = 0.0):
    """A read whose ``name`` opened ``ret`` from yesterday's close; the other names opened flat."""
    scene = weighed(tmp_path, now=now, days=days, shift=shift)
    yesterday = next(iter(scene.prior_bars))
    closed = at(16, 0, yesterday)
    today = dict(scene.market.known)
    for s in NAMES:
        y = scene.prior_markets[yesterday].last(s, closed)
        today.update(known(DAY, {s: [y * (1 + (ret if s == name else 0.0))] * 390}))
    return replace(scene, market=MarketContext(today))


def test_a_heavyweight_gap_wakes_its_question(tmp_path):
    got, _, gates = labels(gapped(tmp_path, "NVDA", 0.059))
    s = got["leaders.heavyweight_gap"]
    assert shape(s) == ("from yesterday's close to the settled open NVDA (#% of the index) rose #%, adding # sigma to the open by "
                        "itself, larger than # of the last # sessions' largest single-name gaps, top third: a heavyweight shock; the rest "
                        "of the index gapped down # sigma, larger than # of the last # sessions' rest-of-index gaps, top third: the rest "
                        "gapped too")
    assert "NVDA (8.0% of the index) rose 5.9%, adding 0.48 sigma" in s and "larger than 11 of the last 11 sessions'" in s
    assert gates["heavyweight_gap_split"] is None


def test_a_heavyweight_gap_with_the_rest_barely_moving(tmp_path):
    """Today's index opens as far above yesterday's close as NVDA carried it, so the rest of the index barely moved."""
    got, _, gates = labels(gapped(tmp_path, "NVDA", 0.059, shift=36.0))
    assert got["leaders.heavyweight_gap"].endswith("the rest of the index gapped up 0.05 sigma, larger than 1 of the last 11 sessions' "
                                                   "rest-of-index gaps, bottom third: the rest barely moved")
    assert gates["heavyweight_gap_split"] is None


def test_a_small_heavyweight_gap_sleeps_its_question(tmp_path):
    got, _, gates = labels(gapped(tmp_path, "MSFT", -0.01))
    assert ("MSFT (7.0% of the index) fell 1.0%, taking 0.07 sigma from the open by itself, larger than 1 of the last 11 sessions' "
            "largest single-name gaps, bottom third: no heavyweight shock") in got["leaders.heavyweight_gap"]
    assert gates["heavyweight_gap_split"] == ("no one heavyweight's overnight contribution was in the top third of the last 11 sessions' "
                                              "(the largest, MSFT, 0.07 sigma, bottom third)")


def test_the_heavyweight_gap_needs_ten_prior_sessions_with_the_session_before(tmp_path):
    """Of 10 prior days, 7 have the trading day before them on file too: too few to rank the gap, so the label is omitted and
    its question sleeps."""
    _, omitted, gates = labels(gapped(tmp_path, "NVDA", 0.059, days=PRIOR_DAYS))
    why = (f"its rank needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with yesterday's close, a settled open and every heavyweight's "
           "price at both, have 7")
    assert omitted["leaders.heavyweight_gap"] == why and gates["heavyweight_gap_split"] is None


def test_the_heavyweight_gap_waits_for_the_settled_open(tmp_path):
    _, omitted, gates = labels(gapped(tmp_path, "NVDA", 0.059, now=at(9, 34, ss=30)))
    why = "the settled open (the close of the 09:34 bar) is not in yet"
    assert omitted["leaders.heavyweight_gap"] == why and gates["heavyweight_gap_split"] is None
    scene = gapped(tmp_path, "NVDA", 0.059)
    scene = replace(scene, market=MarketContext({s: pts for s, pts in scene.market.known.items() if s != "TSLA"}))
    _, omitted, gates = labels(scene)
    assert omitted["leaders.heavyweight_gap"] == "needs a price for TSLA at yesterday's close and at the settled open"
    assert gates["heavyweight_gap_split"] is None


def test_the_heavyweight_gap_needs_yesterdays_bars(tmp_path):
    """With the newest session missing, the gap would run from an older close: a two-day move read as overnight."""
    scene = gapped(tmp_path, "NVDA", 0.059)
    older = dict(list(scene.prior_bars.items())[1:])
    _, omitted, gates = labels(replace(scene, prior_bars=older))
    why = "yesterday's bars are not on file: the newest stored session is 2026-09-16"
    assert omitted["leaders.heavyweight_gap"] == why
    assert gates["heavyweight_gap_split"] is None


# ---- every label, point in time, under the session count and without its inputs

def test_no_label_moves_on_values_known_after_the_read(tmp_path):
    scene = gapped(tmp_path, "NVDA", 0.059)
    later = {s: [(t, v * (1.5 if t > NOW else 1.0)) for t, v in pts] for s, pts in scene.market.known.items()}
    got, _, _ = labels(scene)
    assert set(got) == set(LABELS)
    assert labels(replace(scene, market=MarketContext(later)))[0] == got
    spx_after = [c + (40.0 if i >= READ_MINUTE else 0.0) for i, c in enumerate(index_closes())]
    assert labels(replace(scene, bars=bars_from_closes(spx_after)))[0] == got


def test_an_estimated_ruler_says_so(tmp_path):
    scene = gapped(tmp_path, "NVDA", 0.059)
    got, _, _ = labels(replace(scene, rows_today=scene.rows_today[1:]))    # no row by 09:40: the earliest live sigma stands in
    assert set(got) == set(LABELS)
    assert all(s.endswith(" (ruler estimated)") for s in got.values())
    assert not any("ruler estimated" in s for s in labels(scene)[0].values())


@pytest.mark.parametrize("path, what", [
    ("leaders.equal_weight_vs_cap_30m", "RSP and SPY over the half hour to this minute"),
    ("leaders.semis_vs_index_30m", "SMH and $SPX at this minute"),
    ("tells.sector_lead_10m", "SMH and $SPX at this minute"),
    ("leaders.size_spread_day", "QQQ and SPY since the settled open to this minute"),
    ("sectors.agreement_30m", "a 30-minute move of XLK at this minute"),
    ("leaders.rotation_30m", "every sensitive and defensive sector fund and $SPX over the half hour to this minute"),
    ("leaders.megacap_cohesion_30m", "a 30-minute move of NVDA at this minute"),
    ("leaders.pull_vs_rest_30m", "every one of the 8 largest stocks and $SPX over the half hour to this minute"),
    ("leaders.single_name_10m", "a 10-minute move of NVDA at this minute"),
])
def test_a_rank_under_ten_sessions_omits_its_label_with_the_reason(tmp_path, path, what):
    """With one prior day's market context gone, 9 sessions are left: each rank is omitted, never judged on fewer."""
    scene = weighed(tmp_path, move_points=10.5)
    _, omitted, gates = labels(replace(scene, prior_markets=dict(list(scene.prior_markets.items())[1:])))
    assert omitted[path] == f"its rank needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with {what}, have {SAME_CLOCK_MIN_SESSIONS - 1}"
    if path == "leaders.single_name_10m":
        assert gates["single_name_shock"] is None


@pytest.mark.parametrize("path, dropped, reason", [
    ("leaders.semis_vs_index_30m", "SMH", "needs a price for SMH now and 30 minutes ago"),
    ("leaders.size_spread_day", "IWM", "needs a price for IWM at the settled open and now"),
    ("leaders.rotation_30m", "XLV", "needs a price for XLV now and 30 minutes ago"),
    ("leaders.megacap_cohesion_30m", "AVGO", "needs a price for AVGO now and 30 minutes ago"),
    ("leaders.pull_vs_rest_30m", "AVGO", "needs a price for AVGO now and 30 minutes ago"),
    ("leaders.single_name_10m", "AVGO", "needs a price for AVGO now and 10 minutes ago"),
])
def test_a_label_without_its_symbol_is_omitted_with_the_reason(tmp_path, path, dropped, reason):
    _, omitted, gates = labels(weighed(tmp_path, drop=(dropped,)))
    assert omitted[path] == reason
    if path == "leaders.single_name_10m":
        assert gates["single_name_shock"] is None
