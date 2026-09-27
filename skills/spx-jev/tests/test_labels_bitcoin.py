"""The bitcoin family, offline: /MBT's night against the S&P futures' and its weekend path before the open, and
its half hours, streak, link and last five sessions against the index in the session; every size a rank
against the last nights or sessions, a roll never measured, a stale feed asleep."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from conftest import DAY, ET, at, bars_from_closes, night_row
from spx_jev.cuts import OVERNIGHT_RANK_MIN_NIGHTS, WINDOW_10_MIN
from spx_jev.labels import bitcoin
from spx_jev.labels.bitcoin import GATE_OF, PREMARKET, NightStore, build_bitcoin_labels, prior_close
from spx_jev.labels.registry import build_labels
from spx_jev.sessions import previous_trading_day
from spx_jev.state_builder import MarketContext
from usual_link_fixtures import NOW, follow, index_closes, known, scene_with, shape

SESSION = ("xasset.btc_gap_30min", "xasset.btc_gap_streak", "xasset.btc_link", "xasset.btc_five_day")
MONDAY = "2026-09-21"


def written(ls) -> dict[str, str]:
    return {f"{g}.{k}": s for g, v in ls.state.items() for k, s in v.items()}


def write_night(root: Path, day: str, rows: list[dict]) -> None:
    folder = root / "spx_jev" / "overnight"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{day}.jsonl", "a") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))


def write_table(root: Path, rolls: list[dict] = (), current: dict | None = None) -> None:
    folder = root / "spx_jev" / "overnight"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rolls.json").write_text(json.dumps({"schema_version": 1, "current": current or {}, "rolls": list(rolls)}))


def mbt_roll(at_: datetime) -> dict:
    return {"symbol": "/MBT", "day": at_.date().isoformat(), "at": at_.isoformat(), "from": "/MBTU26", "to": "/MBTV26"}


def trading_days_before(day: str, n: int) -> list[date]:
    """The ``n`` trading days before ``day``, oldest first."""
    out, d = [], date.fromisoformat(day)
    for _ in range(n):
        d = previous_trading_day(d)
        out.append(d)
    return out[::-1]


def one_bar(symbol: str, ends: datetime, close: float, day: date) -> dict:
    return night_row(symbol, ends - timedelta(minutes=1), close, day=day.isoformat())


# ---- before the open: bitcoin's night against the S&P futures' ------------------------------------

READ = time(9, 28)
ES, MBT = 7700.0, 85000.0


def night(day: date, es_pct: float, btc_pct: float, until: time = READ) -> list[dict]:
    """A night into ``day``: /ES and /MBT at the prior close, and moved by the given percents at ``until``."""
    close, read = prior_close(day), datetime.combine(day, until, tzinfo=ET)
    return [one_bar("/ES", close, ES, day), one_bar("/MBT", close, MBT, day),
            one_bar("/ES", read, ES * (1 + es_pct / 100), day), one_bar("/MBT", read, MBT * (1 + btc_pct / 100), day)]


def prior_nights(root: Path, day: str = DAY, multiple: float = 2.0) -> None:
    """The 20 nights before ``day``: /ES up or down 0.1% (+, -, -, + in turn, so its moves are unrelated to the night's
    order), bitcoin ``multiple`` times that and a part of its own from -0.95% to +0.95% (night k's is (k - 9.5) / 10), so
    bitcoin's gap ranks in night order. Bitcoin's weekend nights count as short against its weeknights (it trades
    through the weekend), so the rank has 16: Labor Day's night and the three Mondays sit out."""
    for k, d in enumerate(trading_days_before(day, 20)):
        es = 0.1 * (1, -1, -1, 1)[k % 4]
        write_night(root, d.isoformat(), night(d, es, multiple * es + (k - 9.5) / 10))


def premarket_read(premarket_scene_factory, root: Path, es_pct: float, btc_pct: float, day: str = DAY, now: time = READ):
    d = date.fromisoformat(day)
    rows = night(d, es_pct, btc_pct, until=now)
    return premarket_scene_factory(datetime.combine(d, now, tzinfo=ET), rows, state_dir=root)


@pytest.mark.parametrize("btc_pct, verdict, cut, awake", [
    (2.6, "btc_ahead_up", "top fifth", True),
    (-0.4, "btc_ahead_down", "bottom fifth", True),
    (0.6, "in_line", "middle fifths", False),
])
def test_bitcoin_beyond_the_futures_night_ranks_in_fifths_of_the_last_nights(tmp_path, premarket_scene_factory, btc_pct, verdict, cut, awake):
    prior_nights(tmp_path)
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, btc_pct))
    s = written(ls)["overnight.btc_vs_futures"]
    assert s.startswith("S&P futures stand 0.31 sigma above their 16:00 price; since then bitcoin futures (/MBT) ")
    assert "over those nights bitcoin up has gone with stocks up" in s
    fig = ls.figures["overnight.btc_vs_futures"]
    assert fig["kind"] == "rank" and fig["cut"] == cut and fig["verdict"] == verdict
    assert (ls.gates["btc_overnight_vs_futures"] is None) == awake
    if not awake:
        assert s.endswith("so bitcoin is in line with the futures") and "between the top and bottom fifths" in ls.gates["btc_overnight_vs_futures"]


def test_when_bitcoin_has_gone_against_stocks_its_run_up_is_ahead_in_the_stocks_down_direction(tmp_path, premarket_scene_factory):
    prior_nights(tmp_path, multiple=-2.0)
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 1.0))
    s = written(ls)["overnight.btc_vs_futures"]
    assert "bitcoin up has gone with stocks down" in s and s.endswith("so bitcoin is ahead in the stocks-down direction")
    assert ls.figures["overnight.btc_vs_futures"]["verdict"] == "btc_ahead_down" and ls.figures["overnight.btc_vs_futures"]["value"] > 0


def test_one_night_far_out_does_not_turn_which_way_bitcoin_goes_with_stocks(tmp_path, premarket_scene_factory):
    """Nights where bitcoin follows the futures twice over, but for the last, when it rose 8% as they fell 0.5%: a
    least-squares fit turns the multiple negative on that one night."""
    days = trading_days_before(DAY, 20)
    for k, d in enumerate(days[:-1]):
        es = (0.05 + 0.01 * k) * (1, -1, -1, 1)[k % 4]
        write_night(tmp_path, d.isoformat(), night(d, es, 2 * es + (k - 9.5) / 200))
    write_night(tmp_path, days[-1].isoformat(), night(days[-1], -0.5, 8.0))
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6))
    assert "over those nights bitcoin up has gone with stocks up" in written(ls)["overnight.btc_vs_futures"]
    assert ls.figures["overnight.btc_vs_futures"]["verdict"] == "btc_ahead_up"


def monday_night() -> list[dict]:
    """The night into MONDAY: bitcoin up 5% over the weekend, then both up from the futures' first minute after the reopen."""
    d = date.fromisoformat(MONDAY)
    close, reopen = bitcoin.weekend_edges(d)
    first = reopen + timedelta(minutes=1)
    rows = [one_bar("/ES", close, ES, d), one_bar("/MBT", close, MBT, d), one_bar("/ES", first, ES, d), one_bar("/MBT", first, MBT * 1.05, d)]
    return rows + [one_bar("/ES", at(9, 28, MONDAY), ES * 1.003, d), one_bar("/MBT", at(9, 28, MONDAY), MBT * 1.05 * 1.006, d)]


def test_after_a_weekend_bitcoin_s_night_runs_from_the_futures_reopen_so_its_weekend_is_left_to_the_weekend_path(tmp_path, premarket_scene_factory):
    prior_nights(tmp_path, day=MONDAY)
    ls = build_bitcoin_labels(premarket_scene_factory(at(9, 28, MONDAY), monday_night(), state_dir=tmp_path))
    s = written(ls)["overnight.btc_vs_futures"]
    assert s.startswith("S&P futures stand 0.31 sigma above their 16:00 price; since the S&P futures reopened at 18:00 Sunday "
                        "bitcoin futures (/MBT) rose")
    assert ls.figures["overnight.btc_vs_futures"]["verdict"] == "in_line"


def test_an_s_and_p_roll_at_the_reopen_drops_only_the_location_clause(tmp_path, premarket_scene_factory):
    """The futures' move from Friday's close spans their roll at the Sunday reopen; bitcoin's night from the reopen does not."""
    prior_nights(tmp_path, day=MONDAY)
    es_roll = {"symbol": "/ES", "day": MONDAY, "at": datetime(2026, 9, 20, 18, 0, tzinfo=ET).isoformat(), "from": "/ESU26", "to": "/ESZ26"}
    write_table(tmp_path, [es_roll])
    ls = build_bitcoin_labels(premarket_scene_factory(at(9, 28, MONDAY), monday_night(), state_dir=tmp_path))
    assert written(ls)["overnight.btc_vs_futures"].startswith("since the S&P futures reopened at 18:00 Sunday bitcoin futures (/MBT) rose")


def test_the_night_is_refused_across_a_roll_and_a_prior_roll_night_sits_out(tmp_path, premarket_scene_factory):
    prior_nights(tmp_path)
    write_table(tmp_path, [mbt_roll(datetime(2026, 9, 17, 17, 10, tzinfo=ET))])
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6))
    why = ls.omitted["overnight.btc_vs_futures"]
    assert why.startswith("bitcoin futures (/MBT) rolled to the next contract overnight") and ls.gates["btc_overnight_vs_futures"] == why
    # the same roll a night earlier leaves tonight measured, against one night fewer
    write_table(tmp_path, [mbt_roll(datetime(2026, 9, 16, 17, 10, tzinfo=ET))])
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6))
    assert "of the last 15 nights" in written(ls)["overnight.btc_vs_futures"]


def test_a_roll_the_quote_shows_before_the_table_does_refuses_the_night(tmp_path, premarket_scene_factory):
    prior_nights(tmp_path)
    write_table(tmp_path, current={"/ES": "/ESZ26", "/MBT": "/MBTV26"})
    manifest = tmp_path / "spx_jev" / "overnight" / "manifest.jsonl"
    line = {"day": DAY, "saved_at": at(9, 26).isoformat(), "symbols": {"/ES": {"contract_quoted": "/ESZ26"}, "/MBT": {"contract_quoted": "/MBTX26"}}}
    manifest.write_text(json.dumps(line) + "\n")
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6))
    assert ls.omitted["overnight.btc_vs_futures"].startswith("Schwab quotes /MBTX26 but the roll table is still on /MBTV26")
    assert ls.omitted["weekend.btc_path"] == "not the first session after a weekend or a holiday"
    # a save after the read is not seen by it
    manifest.write_text(json.dumps({**line, "saved_at": at(9, 29).isoformat()}) + "\n")
    assert "overnight.btc_vs_futures" in written(build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6)))


ROLL_FRIDAY = "2026-09-25"        # the last Friday of September: /MBT rolled at 17:11 the Thursday evening before


def quote(root: Path, day: str, saved_at: datetime, mbt: str) -> None:
    line = {"day": day, "saved_at": saved_at.isoformat(), "symbols": {"/ES": {"contract_quoted": "/ESZ26"}, "/MBT": {"contract_quoted": mbt}}}
    (root / "spx_jev" / "overnight" / "manifest.jsonl").write_text(json.dumps(line) + "\n")


@pytest.mark.parametrize("start, end, thursday", [
    (datetime(2026, 9, 24, 16, 0, tzinfo=ET), datetime(2026, 9, 25, 9, 28, tzinfo=ET), date(2026, 9, 24)),
    (datetime(2026, 9, 23, 16, 0, tzinfo=ET), datetime(2026, 9, 24, 16, 0, tzinfo=ET), None),
    (datetime(2026, 9, 25, 16, 0, tzinfo=ET), datetime(2026, 9, 28, 9, 28, tzinfo=ET), None),
    (datetime(2026, 8, 21, 16, 0, tzinfo=ET), datetime(2026, 9, 4, 16, 0, tzinfo=ET), date(2026, 8, 27)),
])
def test_the_calendar_knows_the_thursday_evening_bitcoin_rolls_on(start, end, thursday):
    assert bitcoin.expiry_roll_between(start, end) == thursday


def test_the_night_before_the_last_friday_expiry_is_refused_before_the_table_or_the_quote_shows_the_roll(tmp_path, premarket_scene_factory):
    prior_nights(tmp_path, day=ROLL_FRIDAY)
    write_table(tmp_path, current={"/ES": "/ESZ26", "/MBT": "/MBTU26"})
    quote(tmp_path, ROLL_FRIDAY, at(9, 26, ROLL_FRIDAY), "/MBTU26")
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6, day=ROLL_FRIDAY))
    why = ls.omitted["overnight.btc_vs_futures"]
    assert why == ("bitcoin futures (/MBT) rolls to the next contract on the evening of Thursday 09-24, before Friday's expiry, so the "
                   "night since Thursday 16:00 would be partly the spread between two contracts")
    assert ls.gates["btc_overnight_vs_futures"] == why
    # the night before, into the Thursday, is measured
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6, day="2026-09-24"))
    assert "overnight.btc_vs_futures" in written(ls)


def test_a_stale_bitcoin_feed_sleeps_the_question(tmp_path, premarket_scene_factory):
    prior_nights(tmp_path)
    d = date.fromisoformat(DAY)
    rows = night(d, 0.3, 2.6, until=time(9, 10))
    rows = [r for r in rows if r["symbol"] == "/MBT"] + [r for r in night(d, 0.3, 2.6) if r["symbol"] == "/ES"]
    ls = build_bitcoin_labels(premarket_scene_factory(at(9, 28), rows, state_dir=tmp_path))
    why = ls.omitted["overnight.btc_vs_futures"]
    assert why == f"the newest bitcoin futures (/MBT) bar finished at 09:10, more than {WINDOW_10_MIN} minutes before the read"
    assert ls.gates["btc_overnight_vs_futures"] == why


def test_too_few_nights_omit_the_label_with_the_reason(tmp_path, premarket_scene_factory):
    for d in trading_days_before(DAY, 3):
        write_night(tmp_path, d.isoformat(), night(d, 0.1, 0.2))
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6))
    assert ls.omitted["overnight.btc_vs_futures"] == ("bitcoin's gap to the futures not ranked: only 3 of the last 20 nights have both "
                                                      f"on one contract, fewer than {OVERNIGHT_RANK_MIN_NIGHTS}")


def test_a_premarket_read_writes_only_the_premarket_labels_and_decides_only_their_gates(tmp_path, premarket_scene_factory):
    prior_nights(tmp_path)
    ls = build_bitcoin_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6))
    assert ls.paths() == set(PREMARKET) and set(ls.gates) == {GATE_OF[p] for p in PREMARKET}
    registry = build_labels(premarket_read(premarket_scene_factory, tmp_path, 0.3, 2.6))
    assert not any(p in registry.paths() for p in SESSION)


# ---- before the open: the weekend path ------------------------------------------------------------

def weekend(day: date, weekend_pct: float, reopen_pct: float, until: time = READ) -> list[dict]:
    """/MBT at the last cash close, at the S&P futures' reopen the evening before ``day`` and at ``until``, and /ES
    unchanged since the close."""
    close, reopen = bitcoin.weekend_edges(day)
    at_reopen = MBT * (1 + weekend_pct / 100)
    read = datetime.combine(day, until, tzinfo=ET)
    return [one_bar("/MBT", close, MBT, day), one_bar("/MBT", reopen, at_reopen, day),
            one_bar("/MBT", read, at_reopen * (1 + reopen_pct / 100), day), one_bar("/ES", close, ES, day), one_bar("/ES", read, ES, day)]


EVEN_WEEKENDS = [-1.5 + 0.2 * k for k in range(16)]
# The weekends before 2026-09-14 as saved, most of them up, and one more to make 16.
UP_WEEKENDS = [0.47, 2.26, 2.8, 1.41, -0.92, 2.63, 0.09, 0.43, 0.99, 0.97, 0.31, -0.2, 1.08, 1.4, -0.89, 0.6]


def prior_weekends(root: Path, legs: list[float] = EVEN_WEEKENDS) -> None:
    """The weekends before MONDAY since CME bitcoin traded round the clock (16 of them): weekend legs ``legs`` percent
    (by default from -1.5% upward by 0.2%), each reopen leg the weekend's way by (k - 7.5) / 10 percent."""
    days = [d for d in trading_days_before(MONDAY, 90) if bitcoin.after_break(d) and d >= date(2026, 6, 1)]
    assert len(days) == len(legs) == 16
    for k, (d, w) in enumerate(zip(days, legs)):
        write_night(root, d.isoformat(), weekend(d, w, (k - 7.5) / 10 * (1 if w >= 0 else -1)))


@pytest.mark.parametrize("reopen_pct, verdict, kept", [(-2.0, "extended", "it kept going"), (0.0, "held", "it held about where the weekend left it"),
                                                       (2.0, "reversed", "it turned back")])
def test_a_big_weekend_then_the_reopen_leg_taken_the_weekend_s_way(tmp_path, premarket_scene_factory, reopen_pct, verdict, kept):
    prior_weekends(tmp_path)
    d = date.fromisoformat(MONDAY)
    ls = build_bitcoin_labels(premarket_scene_factory(datetime.combine(d, READ, tzinfo=ET), weekend(d, -4.0, reopen_pct), state_dir=tmp_path))
    s = written(ls)["weekend.btc_path"]
    assert s.startswith("S&P futures stand 0.00 sigma above their 16:00 price; bitcoin futures (/MBT) fell ")
    assert "from the Friday 16:00 close to the S&P futures' reopen at 18:00 Sunday, larger than 16 of the last 16 weekends by size, top third" in s
    assert s.endswith(kept)
    assert ls.figures["weekend.btc_path"]["verdict"] == verdict and ls.gates["btc_weekend_path"] is None


def test_an_ordinary_weekend_sleeps_the_question_and_keeps_the_label(tmp_path, premarket_scene_factory):
    prior_weekends(tmp_path)
    d = date.fromisoformat(MONDAY)
    ls = build_bitcoin_labels(premarket_scene_factory(datetime.combine(d, READ, tzinfo=ET), weekend(d, 0.0, 0.0), state_dir=tmp_path))
    assert "weekend.btc_path" in written(ls)
    assert ls.gates["btc_weekend_path"].startswith("an ordinary weekend for bitcoin")


def test_a_small_fall_among_mostly_up_weekends_is_an_ordinary_weekend(tmp_path, premarket_scene_factory):
    prior_weekends(tmp_path, UP_WEEKENDS)
    d = date.fromisoformat(MONDAY)
    ls = build_bitcoin_labels(premarket_scene_factory(datetime.combine(d, READ, tzinfo=ET), weekend(d, -0.05, 0.0), state_dir=tmp_path))
    assert "larger than 0 of the last 16 weekends by size, bottom third" in written(ls)["weekend.btc_path"]
    assert ls.gates["btc_weekend_path"] == "an ordinary weekend for bitcoin: its weekend leg is not in the top third by size of the last 16 weekends"


def test_the_weekend_path_is_asked_only_after_a_weekend_or_a_holiday(tmp_path, premarket_scene_factory):
    prior_weekends(tmp_path)
    tuesday = date(2026, 9, 22)
    ls = build_bitcoin_labels(premarket_scene_factory(datetime.combine(tuesday, READ, tzinfo=ET), [], state_dir=tmp_path))
    assert ls.omitted["weekend.btc_path"] == "not the first session after a weekend or a holiday"
    assert ls.gates["btc_weekend_path"] == "not the first session after a weekend or a holiday"
    assert bitcoin.after_break(date(2026, 9, 8))          # the Tuesday after Labor Day


def test_a_weekend_across_the_bitcoin_roll_is_refused(tmp_path, premarket_scene_factory):
    prior_weekends(tmp_path)
    write_table(tmp_path, [mbt_roll(datetime(2026, 9, 20, 17, 10, tzinfo=ET))])
    d = date.fromisoformat(MONDAY)
    ls = build_bitcoin_labels(premarket_scene_factory(datetime.combine(d, READ, tzinfo=ET), weekend(d, -4.0, 2.0), state_dir=tmp_path))
    assert ls.omitted["weekend.btc_path"].startswith("bitcoin futures (/MBT) rolled to the next contract since the Friday close")


# ---- in the session: the half hour, the streak and the link against the index ---------------------

MULTIPLES = {"/MBT": 1.5}


def session_read(jumps: dict[int, float] | None = None, multiple: float = 1.5, wiggle: float = 0.0, drop_today: bool = False, state_dir=None):
    closes = index_closes(10.5)
    today = {} if drop_today else {"/MBT": follow(closes, multiple, jumps=jumps, wiggle=wiggle)}
    return scene_with(closes, today, {"/MBT": multiple}, wiggles={"/MBT": 0.0002}, state_dir=state_dir)


def session_labels(scene):
    ls = build_bitcoin_labels(scene)
    return written(ls), ls


GAP = ("over the last # minutes bitcoin futures (/MBT) {moved} # times its normal half-hour gap more than the index's move usually brings it, "
       "{fifth} for this half hour, higher than # of the last # sessions at this minute; over the last # sessions bitcoin up has gone "
       "with stocks {stocks}, so {verdict}")


@pytest.mark.parametrize("jump, multiple, moved, fifth, stocks, verdict, code", [
    (0.004, 1.5, "rose", "in the top fifth", "up", "bitcoin is ahead in the stocks-up direction", "btc_ahead_up"),
    (-0.004, 1.5, "fell", "in the bottom fifth", "up", "bitcoin is ahead in the stocks-down direction", "btc_ahead_down"),
    (0.004, -1.5, "rose", "in the top fifth", "down", "bitcoin is ahead in the stocks-down direction", "btc_ahead_down"),
])
def test_bitcoin_ahead_of_the_index_in_the_half_hour(jump, multiple, moved, fifth, stocks, verdict, code):
    got, ls = session_labels(session_read({175: jump}, multiple=multiple))
    assert shape(got["xasset.btc_gap_30min"]) == GAP.format(moved=moved, fifth=fifth, stocks=stocks, verdict=verdict)
    assert ls.figures["xasset.btc_gap_30min"]["verdict"] == code and ls.gates["btc_gap_30m"] is None


def test_a_half_hour_in_the_middle_fifths_is_in_line_and_asleep():
    got, ls = session_labels(session_read())
    assert got["xasset.btc_gap_30min"].endswith("so bitcoin is in line with the index")
    assert ls.figures["xasset.btc_gap_30min"]["verdict"] == "in_line" and "between the top and bottom fifths" in ls.gates["btc_gap_30m"]


def test_three_half_hours_the_same_way_are_a_streak():
    got, ls = session_labels(session_read({100: 0.002, 130: 0.002, 160: 0.002}))
    s = got["xasset.btc_gap_streak"]
    assert s.startswith("over the last 3 half hours (to 11:32, 12:02 and 12:32) bitcoin futures (/MBT) rose more than the index's move "
                        "usually brings it each time; together ")
    assert s.endswith("so a streak in the stocks-up direction") and "top third for this time" in s
    assert ls.figures["xasset.btc_gap_streak"]["verdict"] == "streak_up" and ls.gates["btc_gap_streak"] is None


def test_half_hours_that_point_different_ways_are_no_streak():
    got, ls = session_labels(session_read({100: 0.002, 130: -0.003, 160: 0.002}))
    assert "went beyond the index's move by turns (rose more, fell more, rose more)" in got["xasset.btc_gap_streak"]
    assert ls.figures["xasset.btc_gap_streak"]["verdict"] == "no_streak"
    assert ls.gates["btc_gap_streak"] == "the last three half hours point different ways"


@pytest.mark.parametrize("wiggle, verdict, how", [(0.0, "tight", "more tightly than on most recent sessions"),
                                                  (0.001, "loose", "more loosely than on most recent sessions")])
def test_how_tightly_bitcoin_and_the_index_move_together_today(wiggle, verdict, how):
    got, ls = session_labels(session_read(wiggle=wiggle))
    s = got["xasset.btc_link"]
    assert s.startswith(f"since 09:35 bitcoin futures (/MBT) and the index have moved together five minutes by five minutes {how}: link ")
    assert s.endswith("over the last 10 sessions bitcoin up has gone with stocks up")
    assert ls.figures["xasset.btc_link"]["verdict"] == verdict and ls.gates["btc_link_today"] is None


def test_the_link_needs_the_morning_before_10_32():
    closes = index_closes()
    scene = scene_with(closes, {"/MBT": follow(closes, 1.5)}, MULTIPLES, now=at(10, 20, ss=10))
    _, ls = session_labels(scene)
    assert ls.omitted["xasset.btc_link"] == "needs 11 five-minute returns of /MBT and $SPX since 09:35"


def test_a_stale_bitcoin_feed_sleeps_the_session_questions():
    scene = session_read()
    pts = scene.market.known["/MBT"]
    stale = replace(scene, market=MarketContext({**scene.market.known, "/MBT": [p for p in pts if p[0] <= NOW - timedelta(minutes=15)]}))
    _, ls = session_labels(stale)
    why = f"no bitcoin futures (/MBT) price in the last {WINDOW_10_MIN} minutes"
    for path in ("xasset.btc_gap_30min", "xasset.btc_gap_streak", "xasset.btc_link"):
        assert ls.omitted[path] == why and ls.gates[GATE_OF[path]] == why


def test_the_prior_sessions_read_from_the_overnight_store_as_from_their_market_context(tmp_path):
    """Before the context job quoted /MBT, a prior session's bitcoin comes from the overnight store: the same prices give the same labels."""
    from_context = session_read({175: 0.004})
    for day, m in from_context.prior_markets.items():
        write_night(tmp_path, day, [night_row("/MBT", t - timedelta(minutes=1), v, day=day) for t, v in m.known["/MBT"]])
    from_store = replace(session_read({175: 0.004}, state_dir=tmp_path), prior_markets={})
    a, _ = session_labels(from_context)
    b, _ = session_labels(from_store)
    for path in ("xasset.btc_gap_30min", "xasset.btc_gap_streak", "xasset.btc_link"):
        assert a[path] == b[path]


def test_a_session_read_leaves_the_premarket_labels_alone(full_scene):
    ls = build_bitcoin_labels(full_scene)
    assert ls.paths() == set(SESSION) and set(ls.gates) == {GATE_OF[p] for p in SESSION}
    assert ls.omitted["xasset.btc_five_day"] == "no overnight store to read bitcoin's session closes from"


# ---- in the session: the last five sessions -------------------------------------------------------

def five_day_state(root: Path, last_five: float) -> None:
    """26 sessions before DAY: the index up 0.1% a day with a 0.2% wobble, bitcoin twice that with a part of its own
    (0.1% times sin(1.5 k), which leaves the last five sessions mid-rank), and over them ``last_five`` percent more a day."""
    spx, btc = 7000.0, MBT
    days = trading_days_before(DAY, 26)
    (root / "reversion" / "bars").mkdir(parents=True, exist_ok=True)
    for k, d in enumerate(days):
        index_day = 0.001 + 0.002 * (-1) ** k
        spx *= 1 + index_day
        btc *= 1 + 2 * index_day + 0.001 * math.sin(1.5 * k) + (last_five / 100 if k >= len(days) - 5 else 0.0)
        close = datetime.combine(d, time(16), tzinfo=ET)
        last_bar = {"ts": (close - timedelta(minutes=1)).isoformat(), "open": spx, "high": spx, "low": spx, "close": spx}
        (root / "reversion" / "bars" / f"{d.isoformat()}-SPX.json").write_text(json.dumps([last_bar]))
        write_night(root, d.isoformat(), [one_bar("/MBT", close, btc, d)])


def five_day_read(scene_factory, root: Path):
    now = at(9, 35, ss=5)
    return replace(scene_factory(now, bars_from_closes([7700.0] * 5)), state_dir=root)


@pytest.mark.parametrize("last_five, verdict, word, fifth", [(-1.0, "btc_lagging", "worse", "in the bottom fifth"),
                                                             (1.0, "btc_ahead", "better", "in the top fifth"),
                                                             (0.0, "in_line", "worse", "between the top and bottom fifths")])
def test_bitcoin_s_last_five_sessions_against_the_index(tmp_path, scene_factory, last_five, verdict, word, fifth):
    five_day_state(tmp_path, last_five)
    ls = build_bitcoin_labels(five_day_read(scene_factory, tmp_path))
    s = written(ls).get("xasset.btc_five_day")
    assert s.startswith(f"over the last 5 sessions bitcoin futures (/MBT) did ") and f"normal weeks {word} than the index's move would match, {fifth} " in s
    assert ls.figures["xasset.btc_five_day"]["verdict"] == verdict
    assert (ls.gates["btc_five_day_lead"] is None) == (verdict != "in_line")


def test_a_roll_inside_the_five_sessions_sleeps_the_question(tmp_path, scene_factory):
    five_day_state(tmp_path, -1.0)
    write_table(tmp_path, [mbt_roll(datetime(2026, 9, 16, 17, 10, tzinfo=ET))])
    ls = build_bitcoin_labels(five_day_read(scene_factory, tmp_path))
    why = ls.omitted["xasset.btc_five_day"]
    assert why.startswith("bitcoin futures (/MBT) rolled to the next contract inside the last 5 sessions") and ls.gates["btc_five_day_lead"] == why


def test_the_five_sessions_are_refused_while_the_quote_shows_a_roll_the_table_has_not_located(tmp_path, scene_factory):
    five_day_state(tmp_path, -1.0)
    write_table(tmp_path, current={"/ES": "/ESZ26", "/MBT": "/MBTV26"})
    quote(tmp_path, DAY, at(9, 26), "/MBTX26")
    ls = build_bitcoin_labels(five_day_read(scene_factory, tmp_path))
    assert ls.omitted["xasset.btc_five_day"] == ("Schwab quotes /MBTX26 but the roll table is still on /MBTV26: the switch is not located "
                                                 "yet, so bitcoin futures (/MBT)'s last 5 sessions may span two contracts")


# ---- the store ------------------------------------------------------------------------------------

def test_the_store_reads_only_bitcoin_and_prefers_the_minute_bar(tmp_path):
    t = at(10, 0)
    write_night(tmp_path, DAY, [night_row("/ES", t, 7700.0), night_row("/MBT", t - timedelta(minutes=4), 1.0, minutes=5),
                                night_row("/MBT", t, 2.0), night_row("/BTC", t, 3.0, contract="/MBTV26")])
    store = NightStore(tmp_path)
    assert {r["symbol"] for r in store.rows(DAY)} == {"/MBT"}
    assert store.known(DAY) == [(t + timedelta(minutes=1), 2.0)]
    assert NightStore(None).rows(DAY) == []
