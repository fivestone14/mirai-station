"""The leadership family: equal weight against cap weight, semis, the size funds, the sector funds, the
largest stocks and the opening tells, each against its usual multiple of the index, point in time."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from conftest import DAY, at
from spx_jev.cuts import MIN_RANK_SESSIONS
from spx_jev.labels.leadership import LABELS, WEIGHTS_FILE, build_leadership_labels
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.market_context import SYMBOLS
from spx_jev.state_builder import MarketContext
from usual_link_fixtures import DRIFT_STEP, NOW, READ_MINUTE, SIGMA, follow, index_closes, known, prior_drift, scene_with, shape

JUMP = 175                     # a minute inside both the 30- and the 10-minute window before NOW
NAMES = ("NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA")
WEIGHTS = {"NVDA": 0.08, "MSFT": 0.07, "AAPL": 0.065, "AMZN": 0.04, "GOOGL": 0.021, "GOOG": 0.018, "META": 0.03, "AVGO": 0.025,
           "TSLA": 0.021, "BRK.B": 0.017}
MULTIPLES = {"SPY": 1.0, "RSP": 0.66, "SMH": 1.6, "QQQ": 1.2, "IWM": 1.1, "XLF": 1.1,
             **{s: 0.6 + 0.08 * k for k, s in enumerate(SYMBOLS["sectors"]) if s != "XLF"},
             **{s: 1.0 + 0.1 * k for k, s in enumerate(NAMES)}}


def sigma_move(x: float, move_points: float = 0.0) -> float:
    """The stock return worth ``x`` SPX sigma at the read's spot."""
    return x * SIGMA / index_closes(move_points)[READ_MINUTE - 1]


def read(move_points: float = 0.0, jumps: dict[str, dict[int, float]] | None = None, drifts: dict[str, float] | None = None,
         drop: tuple[str, ...] = (), weights: dict | None = None, tmp_path=None, now=NOW):
    closes = index_closes(move_points)
    today = {s: follow(closes, m, jumps=(jumps or {}).get(s), drift=(drifts or {}).get(s, 0.0))
             for s, m in MULTIPLES.items() if s not in drop}
    state_dir = None
    if weights is not None:
        (tmp_path / "spx_leaders").mkdir(exist_ok=True)
        (tmp_path / WEIGHTS_FILE).write_text(json.dumps(weights))
        state_dir = tmp_path
    return scene_with(closes, today, MULTIPLES, now=now, state_dir=state_dir)


def labels(scene):
    ls = build_leadership_labels(scene)
    return {f"{g}.{k}": s for g, v in ls.state.items() for k, s in v.items()}, ls.omitted, ls.gates


def test_every_label_is_omitted_without_the_market_context():
    _, omitted, gates = labels(replace(read(), market=None))
    assert omitted == {p: "no market-context snapshot today" for p in LABELS}
    assert gates == {"heavyweight_gap_split": "leaders.heavyweight_gap is not measured: no market-context snapshot today"}


# ---- equal weight against cap weight

def test_equal_weight_lagging_the_index_past_the_split_rule():
    got, _, _ = labels(read(move_points=30.0, jumps={"RSP": {JUMP: -sigma_move(0.13, 30.0)}}))
    s = got["leaders.equal_weight_vs_cap_30m"]
    assert shape(s) == ("equal-weight RSP usually moves # times the index (SPY), so # sigma was expected over the last # minutes; "
                        "it rose # sigma, # sigma below that, more than the # sigma split rule")
    assert "0.66 times" in s and "so +0.27 sigma was expected" in s and "0.13 sigma below that" in s


@pytest.mark.parametrize("gap, verdict", [(0.11, "more than"), (0.09, "within")])
def test_equal_weight_split_rule_boundary(gap, verdict):
    got, _, _ = labels(read(move_points=10.5, jumps={"RSP": {JUMP: sigma_move(gap, 10.5)}}))
    assert got["leaders.equal_weight_vs_cap_30m"].endswith(f"sigma above that, {verdict} the 0.1 sigma split rule")


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


@pytest.mark.parametrize("between, verdict, beats", [(7.5, "ahead upward: in the top fifth", 8), (6.5, "in line: in neither the top nor the bottom fifth", 7)])
def test_semis_top_fifth_boundary(between, verdict, beats):
    """Prior day k drifts in order of k, so a drift between day 7's and day 8's beats 8 of the 10: the top fifth exactly."""
    got, _, _ = labels(read(move_points=10.5, drifts={"SMH": prior_drift(0) + between * DRIFT_STEP}))
    s = got["leaders.semis_vs_index_30m"]
    assert verdict in s and f"higher than {beats} of the last 10 sessions at this minute" in s


def test_semis_rank_needs_enough_trusted_sessions():
    scene = read()
    rulers = {d: SigmaRuler(SIGMA, "anchor" if k < MIN_RANK_SESSIONS - 1 else "live") for k, d in enumerate(scene.prior_bars)}
    assert labels(replace(scene, prior_rulers=rulers))[1]["leaders.semis_vs_index_30m"] == (
        f"needs {MIN_RANK_SESSIONS} prior sessions with SMH at this minute, have {MIN_RANK_SESSIONS - 1}")


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
    assert quiet["tells.sector_lead_10m"].count("moved in line, in neither the top nor the bottom fifth for this time") == 2


def test_the_opening_tells_need_a_price_10_minutes_back():
    _, omitted, _ = labels(read(drop=("XLF",)))
    assert omitted["tells.sector_lead_10m"] == "needs a price for XLF now and 10 minutes ago"
    early = at(9, 40)
    _, omitted, _ = labels(read(now=early))
    assert omitted["tells.sector_lead_10m"] == "needs a price for SMH, $SPX now and 10 minutes ago"


# ---- size since the settled open

def test_size_funds_since_the_settled_open():
    got, _, _ = labels(read(jumps={"QQQ": {JUMP: sigma_move(0.22)}, "IWM": {JUMP: -sigma_move(0.18)}}))
    s = got["leaders.size_spread_day"]
    assert shape(s) == ("since #:#, after allowing for their usual swing against SPY, QQQ is # sigma ahead, past the # sigma own-swing "
                        "rule, and IWM is # sigma behind, past the # sigma own-swing rule; QQQ leads by # sigma, past the # sigma "
                        "leadership rule")
    assert s.startswith("since 09:35") and "leads by 0.40 sigma" in s


def test_size_funds_in_line_and_close_together():
    got, _, _ = labels(read(jumps={"QQQ": {JUMP: sigma_move(0.08)}, "IWM": {JUMP: -sigma_move(0.05)}}))
    assert shape(got["leaders.size_spread_day"]).endswith(
        "QQQ is in line, # sigma ahead, within the # sigma own-swing rule, and IWM is in line, # sigma behind, within the # sigma "
        "own-swing rule; the two are # sigma apart, within the # sigma leadership rule")


def test_size_funds_wait_for_the_settled_open():
    _, omitted, _ = labels(read(now=at(9, 35)))
    assert omitted["leaders.size_spread_day"] == "the settled open is not in until 09:35"


# ---- the sector funds

def test_sector_funds_moving_one_way():
    got, _, _ = labels(read(move_points=10.5))
    assert shape(got["sectors.agreement_30m"]) == (
        "over the last # minutes # of # sector funds moved the index's way past their own move rule, at least the #-fund one-way "
        "count; sector dispersion beyond each fund's usual multiple was # sigma, under the # sigma wide line")
    assert "11 of 11 sector funds" in got["sectors.agreement_30m"]


def test_sector_funds_split_under_a_wide_dispersion():
    against = {s: {JUMP: -sigma_move(1.5, 10.5)} for s in SYMBOLS["sectors"][:4]}
    got, _, _ = labels(read(move_points=10.5, jumps=against))
    s = got["sectors.agreement_30m"]
    assert "7 of 11 sector funds" in s and "short of the 8-fund one-way count" in s and s.endswith("past the 0.5 sigma wide line")


def test_sector_funds_need_every_fund():
    _, omitted, _ = labels(read(drop=("XLRE",)))
    assert omitted["sectors.agreement_30m"] == "needs a price for XLRE now and 30 minutes ago"


@pytest.mark.parametrize("group, gap, verdict", [
    (("XLF", "XLY", "XLI"), 0.14, "beat utilities, staples and health care by # sigma, past the # sigma rotation rule"),
    (("XLU", "XLP", "XLV"), 0.14, "trailed utilities, staples and health care by # sigma, past the # sigma rotation rule"),
    (("XLU", "XLP", "XLV"), 0.06, "trailed utilities, staples and health care by # sigma, within the # sigma rotation rule"),
])
def test_rotation_between_sensitive_and_defensive_sectors(group, gap, verdict):
    got, _, _ = labels(read(jumps={s: {JUMP: sigma_move(gap)} for s in group}))
    assert shape(got["leaders.rotation_30m"]) == (
        "over the last # minutes, after allowing for each sector's usual link to the index, financials, discretionary and "
        "industrials " + verdict)


# ---- the largest stocks, from the index weights on file

def test_the_largest_names_need_the_index_weights(tmp_path):
    _, omitted, gates = labels(read())
    megacap = ("leaders.heavyweight_gap", "leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m", "leaders.single_name_10m")
    assert {p: omitted[p] for p in megacap} == {p: "no state folder to read the index weights from" for p in megacap}
    assert gates["heavyweight_gap_split"] == "leaders.heavyweight_gap is not measured: no state folder to read the index weights from"
    _, omitted, _ = labels(replace(read(), state_dir=tmp_path))
    assert omitted["leaders.pull_vs_rest_30m"] == f"no index weights: {WEIGHTS_FILE} is not written yet"
    _, omitted, _ = labels(read(weights={"as_of": "2026-09-19", "weights": WEIGHTS}, tmp_path=tmp_path))
    assert omitted["leaders.pull_vs_rest_30m"] == f"the index weights in {WEIGHTS_FILE} carry no date on or before {DAY}"
    _, omitted, _ = labels(read(weights={"as_of": "2026-09-17", "weights": {"NVDA": 0.08}}, tmp_path=tmp_path))
    assert omitted["leaders.pull_vs_rest_30m"] == f"the index weights in {WEIGHTS_FILE} name 1 stocks, fewer than the 8 largest"


def weighed(tmp_path, **kw):
    return read(weights={"as_of": "2026-09-17", "weights": WEIGHTS}, tmp_path=tmp_path, **kw)


def test_the_largest_names_move_together(tmp_path):
    got, _, _ = labels(weighed(tmp_path, move_points=10.5))
    s = got["leaders.megacap_cohesion_30m"]
    assert shape(s) == ("over the last # minutes # of the # largest stocks rose past their own move rule and # fell past it, at least "
                        "the #-name together count; NVDA alone supplied #% of the index's # sigma rise, under the #% one-name share; "
                        "the index move was past the # sigma move rule")
    assert s.startswith("over the last 30 minutes 8 of the 8 largest")


def test_one_name_supplies_the_index_move(tmp_path):
    got, _, _ = labels(weighed(tmp_path, move_points=10.5, jumps={"AAPL": {JUMP: 0.01}}))
    assert "AAPL alone supplied" in got["leaders.megacap_cohesion_30m"] and "past the 33% one-name share" in got["leaders.megacap_cohesion_30m"]


def test_the_largest_names_cancelling_on_a_flat_index(tmp_path):
    jumps = {s: {JUMP: 0.005 if k % 2 else -0.005} for k, s in enumerate(NAMES)}
    got, _, _ = labels(weighed(tmp_path, jumps=jumps))
    s = got["leaders.megacap_cohesion_30m"]
    assert "4 of the 8 largest stocks rose past their own move rule and 4 fell past it, short of the 6-name together count" in s
    assert s.endswith("the index move was within the 0.09 sigma move rule")


@pytest.mark.parametrize("move, jump, verdict", [
    (0.0, 0.0, "neither passed the 0.1 sigma pull rule"),
    (30.0, 0.0, "both passed the 0.1 sigma pull rule, the same way"),
    (0.0, 0.004, "both passed the 0.1 sigma pull rule, in opposite directions"),
    (15.0, 0.004, "only the largest names passed the 0.1 sigma pull rule"),
])
def test_the_largest_names_against_the_rest(tmp_path, move, jump, verdict):
    got, _, _ = labels(weighed(tmp_path, move_points=move, jumps={s: {JUMP: jump} for s in NAMES}))
    s = got["leaders.pull_vs_rest_30m"]
    assert shape(s) == ("over the last # minutes the # largest names (#% of the index) contributed # sigma and the other #% contributed "
                        "# sigma; " + verdict.replace("0.1", "#"))
    assert "the 8 largest names (37% of the index)" in s                         # GOOG counted with GOOGL, BRK.B left out


def test_a_single_name_shock_alone_and_spreading(tmp_path):
    got, _, _ = labels(weighed(tmp_path, jumps={"AAPL": {JUMP: -0.019}}))
    s = got["leaders.single_name_10m"]
    assert shape(s) == ("in the last # minutes AAPL fell #%, # times its usual #-minute move for this time of day, past the #-times "
                        "name-shock rule, worth # sigma of SPX at its #% weight; none of the other # largest fell past its usual "
                        "#-minute move, short of the #-name spreading count")
    assert "fell 1.9%" in s and "at its 6.5% weight" in s
    got, _, _ = labels(weighed(tmp_path, jumps={"AAPL": {JUMP: -0.019}, "MSFT": {JUMP: -0.008}, "META": {JUMP: -0.008}}))
    assert got["leaders.single_name_10m"].endswith("2 of the other 7 largest also fell past their usual 10-minute move, "
                                                   "at least the 2-name spreading count")


def test_a_single_name_under_the_shock_rule_and_after_the_read(tmp_path):
    got, _, _ = labels(weighed(tmp_path, jumps={"AAPL": {READ_MINUTE: -0.05}}))
    assert "under the 3-times name-shock rule" in got["leaders.single_name_10m"]


# ---- the heavyweight gap and its gate

def gapped(tmp_path, name: str, ret: float, now=NOW):
    """A read whose ``name`` opened ``ret`` from yesterday's close; the other names opened flat."""
    scene = weighed(tmp_path, now=now)
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
                        "itself, past the # sigma shock rule; the rest of the index gapped down # sigma, past the # sigma rest-gap rule")
    assert "NVDA (8.0% of the index) rose 5.9%, adding 0.49 sigma" in s
    assert gates == {"heavyweight_gap_split": None}


def test_a_small_heavyweight_gap_sleeps_its_question(tmp_path):
    got, _, gates = labels(gapped(tmp_path, "MSFT", -0.01))
    assert "MSFT (7.0% of the index) fell 1.0%, taking 0.07 sigma from the open by itself, under the 0.15 sigma shock rule" in got["leaders.heavyweight_gap"]
    assert got["leaders.heavyweight_gap"].endswith("gapped up 0.13 sigma, past the 0.1 sigma rest-gap rule")
    assert gates == {"heavyweight_gap_split": "no one heavyweight moved the index past the 0.15 sigma shock rule overnight (the largest, MSFT, 0.07 sigma)"}


def test_the_heavyweight_gap_waits_for_the_settled_open(tmp_path):
    _, omitted, gates = labels(gapped(tmp_path, "NVDA", 0.059, now=at(9, 34, ss=30)))
    why = "the settled open (the close of the 09:34 bar) is not in yet"
    assert omitted["leaders.heavyweight_gap"] == why and gates == {"heavyweight_gap_split": f"leaders.heavyweight_gap is not measured: {why}"}
    scene = gapped(tmp_path, "NVDA", 0.059)
    scene = replace(scene, market=MarketContext({s: pts for s, pts in scene.market.known.items() if s != "TSLA"}))
    _, omitted, gates = labels(scene)
    assert omitted["leaders.heavyweight_gap"] == "needs a price for TSLA at yesterday's close and at the settled open"
    assert gates["heavyweight_gap_split"].startswith("leaders.heavyweight_gap is not measured")


# ---- every label, point in time and without its inputs

def test_no_label_moves_on_values_known_after_the_read(tmp_path):
    scene = gapped(tmp_path, "NVDA", 0.059)
    later = {s: [(t, v * (1.5 if t > NOW else 1.0)) for t, v in pts] for s, pts in scene.market.known.items()}
    got, _, _ = labels(scene)
    assert set(got) == set(LABELS)
    assert labels(replace(scene, market=MarketContext(later)))[0] == got


def test_an_estimated_ruler_says_so(tmp_path):
    scene = gapped(tmp_path, "NVDA", 0.059)
    got, _, _ = labels(replace(scene, rows_today=scene.rows_today[1:]))    # no row by 09:40: the earliest live sigma stands in
    assert set(got) == set(LABELS)
    assert all(s.endswith("; ruler estimated") for s in got.values())
    assert not any("ruler estimated" in s for s in labels(scene)[0].values())


@pytest.mark.parametrize("path, dropped, reason", [
    ("leaders.semis_vs_index_30m", "SMH", "needs a price for SMH now and 30 minutes ago"),
    ("leaders.size_spread_day", "IWM", "needs a price for IWM at the settled open and now"),
    ("leaders.rotation_30m", "XLV", "needs a price for XLV now and 30 minutes ago"),
    ("leaders.megacap_cohesion_30m", "AVGO", "needs a price for AVGO now and 30 minutes ago"),
    ("leaders.pull_vs_rest_30m", "AVGO", "needs a price for AVGO now and 30 minutes ago"),
    ("leaders.single_name_10m", "AVGO", "needs a price for AVGO now and 10 minutes ago"),
])
def test_a_label_without_its_symbol_is_omitted_with_the_reason(tmp_path, path, dropped, reason):
    _, omitted, _ = labels(weighed(tmp_path, drop=(dropped,)))
    assert omitted[path] == reason
