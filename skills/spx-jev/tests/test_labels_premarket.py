"""The premarket family, offline: the five overnight labels and their three gates on synthetic nights in a
scratch overnight store, each size ranked against the last nights, each roll refused."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import night_row
from spx_jev import events, overnight, rolls
from spx_jev.labels import premarket
from spx_jev.labels.registry import NOT_BUILT, build_labels
from spx_jev.sessions import previous_trading_day

ET = ZoneInfo("America/New_York")
DAY = date(2026, 9, 24)            # a Thursday; its 20 prior nights run back past Labor Day, whose night sits out
READ = datetime(2026, 9, 24, 9, 28, 5, tzinfo=ET)
ES_PRICE, ZN_PRICE = 7700.0, 112.0
PER_PCT = ES_PRICE / 100 / 75.0    # sigma per percent in the scene's pre-open ruler (prior close 7700, sigma 75)
CLAIMS = events.Event(datetime(2026, 9, 24, 8, 30, tzinfo=ET), None, "JOBLESS_CLAIMS", events.PRE_OPEN)
# The option codes of each label's question: every figure's verdict is one of them.
CODES = {
    "overnight.es_move": {"big_down", "down", "flat", "up", "big_up"},
    "overnight.range_vs_normal": {"quiet_night", "normal_night", "wide_night"},
    "overnight.gap_origin": {"made_early", "made_in_europe", "made_at_release", "made_late"},
    "overnight.release_reaction": {"shrugged", "extended", "held", "faded"},
    "overnight.bond_gap": {"overnight_ahead_up", "overnight_ahead_down", "in_line"},
}


def _path(day: date, knots: list[tuple[datetime, float]]):
    """A price path in percent from the night's prior close, straight between ``knots`` and flat past the last."""
    start = overnight.night_window(day)[0] + timedelta(minutes=overnight.NIGHT_LEAD_MIN)
    points = [(start, 0.0)] + knots

    def pct_at(t: datetime) -> float:
        for (a, x), (b, y) in zip(points, points[1:]):
            if t <= b:
                return x if t <= a else x + (y - x) * (t - a) / (b - a)
        return points[-1][1]
    return pct_at


def _rows(day: date, symbol: str, price: float, pct_at) -> list[dict]:
    """The night's 5-minute bars of ``symbol`` from the store's window start to the open, each closing on the path."""
    start = overnight.night_window(day)[0]
    until = datetime.combine(day, time(9, 30), tzinfo=ET)
    return [night_row(symbol, t, price * (1 + pct_at(t + timedelta(minutes=5)) / 100), minutes=5, day=day.isoformat())
            for t in overnight.expected_slots(symbol, 5, start, until)]


def _open_at(day: date) -> datetime:
    return datetime.combine(day, time(9, 30), tzinfo=ET)


def _prior_days(n: int = 20) -> list[date]:
    out, d = [], DAY
    while len(out) < n:
        d = previous_trading_day(d)
        out.append(d)
    return out[::-1]


def _save_prior(state_dir, moves: list[float], bonds: list[float] | None = None) -> None:
    """The last nights on file, oldest first: /ES straight from its prior close to ``moves[k]`` percent at the open,
    and /ZN likewise to ``bonds[k]``."""
    for k, (d, m) in enumerate(zip(_prior_days(len(moves)), moves)):
        rows = _rows(d, "/ES", ES_PRICE, _path(d, [(_open_at(d), m)]))
        if bonds is not None:
            rows += _rows(d, "/ZN", ZN_PRICE, _path(d, [(_open_at(d), bonds[k])]))
        overnight.write_night(state_dir, d.isoformat(), rows)


def _spread(n: int = 20) -> list[float]:
    """Nights' moves of 0.05% to 1.00%, alternating sides."""
    return [0.05 * (k + 1) * (-1) ** k for k in range(n)]


def _at(hh: int, mm: int, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=ET)


@pytest.fixture
def calendar(monkeypatch):
    """Today's calendar rows, set by the test: none unless it passes some."""
    rows: list[events.Event] = []
    monkeypatch.setattr(events, "on_day", lambda day, path=events.CALENDAR: [e for e in rows if e.start.date() == day])
    return rows


@pytest.fixture
def read(premarket_scene_factory, tmp_path, calendar):
    """The premarket labels of tonight's night along ``knots`` (and /ZN's along ``bond_knots``) at ``now``."""
    def make(knots, now: datetime = READ, bond_knots=None):
        night = _rows(DAY, "/ES", ES_PRICE, _path(DAY, knots))
        if bond_knots is not None:
            night += _rows(DAY, "/ZN", ZN_PRICE, _path(DAY, bond_knots))
        return premarket.build_premarket_labels(premarket_scene_factory(now, night, state_dir=tmp_path))
    return make


def _verdict(ls, path: str) -> str:
    verdict = ls.figures[path]["verdict"]
    assert verdict in CODES[path]
    return verdict


# ---- overnight.es_move ---------------------------------------------------------------------------

def test_the_nights_move_is_placed_then_ranked_in_thirds(tmp_path, read):
    _save_prior(tmp_path, _spread())
    ls = read([(_at(9, 0), 1.2)])
    assert ls.state["overnight"]["es_move"] == ("at 09:28 S&P futures stand 1.23 sigma above their 16:00 price (up 1.20%), a move "
                                               "larger than 19 of the last 19 nights' moves to this time, top third")
    assert ls.figures["overnight.es_move"] == {"kind": "rank", "value": 1.232, "cut": "top third", "verdict": "big_up"}
    assert _verdict(read([(_at(9, 0), -1.2)]), "overnight.es_move") == "big_down"
    assert _verdict(read([(_at(9, 0), -0.5)]), "overnight.es_move") == "down"
    assert _verdict(read([(_at(9, 0), 0.5)]), "overnight.es_move") == "up"
    assert _verdict(read([(_at(9, 0), -0.02)]), "overnight.es_move") == "flat"


def test_under_the_minimum_of_nights_the_move_is_omitted_with_the_reason(tmp_path, read):
    _save_prior(tmp_path, _spread(5))
    ls = read([(_at(9, 0), 1.2)])
    assert ls.omitted["overnight.es_move"].startswith("overnight move not ranked: only 5 usable of the last 20 nights")
    assert ls.gates["gap_origin"].startswith("no overnight move to place: overnight move not ranked")


def test_a_read_off_the_premarket_lane_writes_nothing():
    from spx_jev.state_builder import Scene
    scene = Scene(row={"ts": READ.isoformat(), "spot": 7700.0}, rows_today=[], bars=[], prior_bars={}, now=READ, sigma=75.0)
    ls = premarket.build_premarket_labels(scene)
    assert not ls.paths() and not ls.gates


def test_without_a_ruler_or_a_state_folder_every_label_waits(premarket_scene_factory, tmp_path):
    ls = premarket.build_premarket_labels(premarket_scene_factory(READ, [], sigma=0.0, state_dir=tmp_path))
    assert set(ls.omitted) == set(premarket.LABELS) and set(ls.gates) == set(premarket.GATES)
    assert ls.omitted["overnight.es_move"].startswith("no pre-open ruler")
    assert premarket.build_premarket_labels(premarket_scene_factory(READ, [])).omitted["overnight.bond_gap"] == \
        "no state folder to read the last nights from"


# ---- rolls ---------------------------------------------------------------------------------------

def test_a_night_across_a_market_holiday_is_not_ranked_against_normal_nights(premarket_scene_factory, tmp_path):
    labor_day_night = date(2026, 9, 8)
    now = datetime.combine(labor_day_night, time(9, 28), tzinfo=ET)
    night = _rows(labor_day_night, "/ES", ES_PRICE, _path(labor_day_night, [(now, 1.0)]))
    ls = premarket.build_premarket_labels(premarket_scene_factory(now, night, state_dir=tmp_path))
    why = "the night spans a market holiday or follows a half day, unlike the nights it would be ranked against"
    assert ls.omitted == {path: why for path in premarket.LABELS}
    assert ls.gates == {qid: why for qid in premarket.GATES}


def _roll_at(state_dir, at: datetime, symbol: str = "/ES", current: str = "/ESZ26") -> None:
    folder = state_dir / overnight.OVERNIGHT_SUBDIR
    found = {"rolls": [{"symbol": symbol, "day": at.date().isoformat(), "at": at.isoformat()}], "rejected": [],
             "windows_without_roll": [], "typical_step": 0.01, "unit": "percent"}
    rolls.save(folder, rolls.merge(rolls.load(folder), symbol, found, "2026-08-01", current))


def test_a_roll_tonight_refuses_every_measure_of_the_futures(tmp_path, read):
    _save_prior(tmp_path, _spread(), [0.3 * m for m in _spread()])
    _roll_at(tmp_path, _at(2, 0))
    ls = read([(_at(9, 0), 1.2)], bond_knots=[(_at(9, 0), 0.3)])
    rolled = "/ES rolled to the next contract overnight"
    assert ls.omitted["overnight.es_move"].startswith(rolled)
    assert ls.omitted["overnight.range_vs_normal"].startswith(rolled)
    assert rolled in ls.gates["gap_origin"]
    assert rolled in ls.gates["overnight_bonds_vs_gap"]


def test_a_roll_night_in_the_base_sits_out_of_the_rank(tmp_path, read):
    _save_prior(tmp_path, _spread())
    _roll_at(tmp_path, datetime.combine(_prior_days()[-3], time(3, 0), tzinfo=ET))
    assert "of the last 18 nights" in read([(_at(9, 0), 1.2)]).state["overnight"]["es_move"]   # Labor Day's night and the roll


def test_a_roll_the_quote_shows_before_the_table_waits(tmp_path, read):
    _save_prior(tmp_path, _spread())
    folder = tmp_path / overnight.OVERNIGHT_SUBDIR
    rolls.save(folder, {**rolls.load(folder), "current": {"/ES": "/ESZ26"}})
    line = {"day": DAY.isoformat(), "saved_at": _at(9, 26).isoformat(), "symbols": {"/ES": {"contract_quoted": "/ESH27"}}}
    overnight.manifest_path(tmp_path).write_text(json.dumps(line) + "\n")
    ls = read([(_at(9, 0), 1.2)])
    for path in ("overnight.es_move", "overnight.range_vs_normal"):
        assert ls.omitted[path].startswith("Schwab quotes /ESH27 but the roll table is still on /ESZ26")
    assert "es_move" in read([(_at(9, 0), 1.2)], now=_at(9, 20)).state["overnight"]          # before the save that quoted it


# ---- overnight.range_vs_normal -------------------------------------------------------------------

def test_the_nights_range_since_the_reopen_is_ranked_in_thirds(tmp_path, read):
    _save_prior(tmp_path, _spread())
    wide = read([(_at(1, 0), 1.5), (_at(9, 0), 0.1)])
    assert _verdict(wide, "overnight.range_vs_normal") == "wide_night"
    assert wide.state["overnight"]["range_vs_normal"].startswith(
        "at 09:28 S&P futures stand 0.10 sigma above their 16:00 price; since the 18:00 reopen their high-low range is 1.4")
    assert _verdict(read([(_at(9, 0), 0.02)]), "overnight.range_vs_normal") == "quiet_night"
    assert _verdict(read([(_at(9, 0), 0.5)]), "overnight.range_vs_normal") == "normal_night"


# ---- overnight.gap_origin ------------------------------------------------------------------------

def test_the_gap_is_placed_in_the_stretch_that_moved_most_unusually(tmp_path, read):
    _save_prior(tmp_path, _spread())
    ls = read([(_at(2, 30), 0.0), (_at(3, 30), 1.0)])
    assert _verdict(ls, "overnight.gap_origin") == "made_in_europe" and ls.gates["gap_origin"] is None
    assert "the most unusual stretch was Europe's open (02:30 to 03:30), up 1.03 sigma" in ls.state["overnight"]["gap_origin"]
    assert _verdict(read([(_at(0, 0), 1.0)]), "overnight.gap_origin") == "made_early"
    assert _verdict(read([(_at(9, 0), 0.0), (_at(9, 25), 1.0)]), "overnight.gap_origin") == "made_late"


def test_the_report_window_is_the_release_only_on_a_report_day(tmp_path, read, calendar):
    _save_prior(tmp_path, _spread())
    knots = [(_at(8, 30), 0.0), (_at(8, 45), 1.0)]
    assert _verdict(read(knots), "overnight.gap_origin") == "made_late"
    calendar.append(CLAIMS)
    assert _verdict(read(knots), "overnight.gap_origin") == "made_at_release"


def test_a_day_the_calendar_does_not_cover_has_no_report_window_to_place_the_gap_in(tmp_path, read, monkeypatch):
    _save_prior(tmp_path, _spread())
    monkeypatch.setattr(events, "uncovered", lambda day, path=events.CALENDAR, tier=None: "the event calendar is kept only from 2026-10-01")
    ls = read([(_at(8, 0), 0.0), (_at(8, 25), 1.0)])
    assert ls.gates["gap_origin"] == "where the report window falls is unknown: the event calendar is kept only from 2026-10-01"
    assert "overnight.gap_origin" in ls.omitted


def test_a_quiet_night_has_no_gap_to_place(tmp_path, read):
    _save_prior(tmp_path, _spread())
    ls = read([(_at(9, 0), 0.02)])
    assert ls.gates["gap_origin"].endswith("bottom third: too small a gap to place")
    assert "overnight.gap_origin" in ls.omitted


# ---- overnight.release_reaction ------------------------------------------------------------------

@pytest.mark.parametrize("after, verdict", [(1.3, "extended"), (0.8, "held"), (0.6, "held"), (0.2, "faded")])
def test_the_reports_reaction_then_what_futures_did_with_it(tmp_path, read, calendar, after, verdict):
    _save_prior(tmp_path, _spread())
    calendar.append(CLAIMS)
    ls = read([(_at(8, 30), 0.0), (_at(8, 45), 0.8), (_at(9, 15), after)])
    assert _verdict(ls, "overnight.release_reaction") == verdict and ls.gates["release_reaction_path"] is None


def test_a_small_reaction_is_shrugged_and_its_words_say_the_move_since(tmp_path, read, calendar):
    _save_prior(tmp_path, _spread())
    calendar.append(CLAIMS)
    ls = read([(_at(8, 30), 0.0), (_at(8, 45), 0.001), (_at(9, 15), -0.3)])
    assert _verdict(ls, "overnight.release_reaction") == "shrugged"
    assert ls.state["overnight"]["release_reaction"].endswith("bottom third; since 08:45 they moved down 0.31 sigma")
    assert "the weekly jobless claims report at 08:30 moved them up 0.00 sigma by 08:45" in ls.state["overnight"]["release_reaction"]


def test_a_claims_only_day_is_asked_and_a_day_without_a_report_sleeps(tmp_path, read, calendar):
    _save_prior(tmp_path, _spread())
    knots = [(_at(8, 30), 0.0), (_at(8, 45), 0.8)]
    assert read(knots).gates["release_reaction_path"] == "no report before the open on the calendar today"
    calendar.append(CLAIMS)
    assert read(knots).gates["release_reaction_path"] is None


def test_a_read_before_the_window_closes_waits_for_it(tmp_path, read, calendar):
    _save_prior(tmp_path, _spread())
    calendar.append(CLAIMS)
    ls = read([(_at(8, 0), 0.4)], now=_at(8, 5))
    assert ls.gates["release_reaction_path"] == ("the weekly jobless claims report comes out at 08:30 and its reaction window "
                                                 "runs to 08:45, after this read")


def test_missing_bars_around_the_report_put_it_to_sleep(tmp_path, premarket_scene_factory, calendar):
    _save_prior(tmp_path, _spread())
    calendar.append(CLAIMS)
    night = [r for r in _rows(DAY, "/ES", ES_PRICE, _path(DAY, [(_at(9, 0), 0.5)]))
             if not _at(8, 0) <= datetime.fromisoformat(r["ts"]) < _at(8, 40)]
    ls = premarket.build_premarket_labels(premarket_scene_factory(READ, night, state_dir=tmp_path))
    assert ls.gates["release_reaction_path"] == "/ES bars missing around the weekly jobless claims report at 08:30"


# ---- overnight.bond_gap --------------------------------------------------------------------------

def _bonds(beta: float) -> list[float]:
    """Each night's /ZN move: ``beta`` times the S&P futures' move, give or take a little."""
    return [beta * m + 0.01 * ((k % 5) - 2) for k, m in enumerate(_spread())]


def test_bonds_ahead_of_the_gap_by_the_last_nights_fit(tmp_path, read):
    _save_prior(tmp_path, _spread(), _bonds(0.3))
    ls = read([(_at(9, 0), 0.5)], bond_knots=[(_at(9, 0), 0.5)])
    assert _verdict(ls, "overnight.bond_gap") == "overnight_ahead_up" and ls.gates["overnight_bonds_vs_gap"] is None
    assert ls.state["overnight"]["bond_gap"].startswith("at 09:28 ten-year Treasury futures stand 0.50% above their 16:00 price; "
                                                        "over the last 19 nights they moved 0.30% per 1% in S&P futures, the same way")
    assert _verdict(read([(_at(9, 0), 0.5)], bond_knots=[(_at(9, 0), -0.2)]), "overnight.bond_gap") == "overnight_ahead_down"
    assert _verdict(read([(_at(9, 0), 0.5)], bond_knots=[(_at(9, 0), 0.15)]), "overnight.bond_gap") == "in_line"


def test_the_fits_sign_sets_which_way_bonds_point(tmp_path, read):
    _save_prior(tmp_path, _spread(), _bonds(-0.3))
    ls = read([(_at(9, 0), 0.0)], bond_knots=[(_at(9, 0), 0.4)])
    assert _verdict(ls, "overnight.bond_gap") == "overnight_ahead_down"
    assert "per 1% in S&P futures, the other way" in ls.state["overnight"]["bond_gap"]


def test_bonds_unlinked_to_the_futures_sleep_rather_than_point_them_on_noise(tmp_path, read):
    unlinked = [0.2 * (-1) ** (k // 2) for k in range(20)]     # each night's sign against the futures' alternates in pairs
    _save_prior(tmp_path, _spread(), unlinked)
    ls = read([(_at(9, 0), 0.5)], bond_knots=[(_at(9, 0), 0.1)])
    assert ls.gates["overnight_bonds_vs_gap"] == ("bonds and S&P futures showed no steady link over the last 19 nights: "
                                                  "leaving out a single night turns the fit's sign")
    assert "overnight.bond_gap" in ls.omitted


def test_bonds_sleep_without_enough_nights_or_a_bond_price(tmp_path, read):
    _save_prior(tmp_path, _spread())
    _save_prior(tmp_path, _spread(5), _bonds(0.3)[-5:])
    ls = read([(_at(9, 0), 0.5)], bond_knots=[(_at(9, 0), 0.5)])
    assert ls.gates["overnight_bonds_vs_gap"] == ("the bonds' fit needs 10 of the last nights with /ES and /ZN both measured on "
                                                  "one contract, has 5")
    assert read([(_at(9, 0), 0.5)]).gates["overnight_bonds_vs_gap"].startswith("no /ZN price at its prior close")


# ---- the family in the registry ------------------------------------------------------------------

def test_a_premarket_read_builds_every_label_the_registry_accounts_for(tmp_path, premarket_scene_factory, calendar):
    _save_prior(tmp_path, _spread(), _bonds(0.3))
    calendar.append(CLAIMS)
    night = (_rows(DAY, "/ES", ES_PRICE, _path(DAY, [(_at(8, 30), 0.2), (_at(8, 45), 0.8), (_at(9, 15), 0.9)]))
             + _rows(DAY, "/ZN", ZN_PRICE, _path(DAY, [(_at(9, 0), 0.3)])))
    ls = build_labels(premarket_scene_factory(READ, night, state_dir=tmp_path))
    assert not {p for p in premarket.LABELS if ls.omitted.get(p) == NOT_BUILT}
    assert {p for p in premarket.LABELS if p in ls.figures} == set(premarket.LABELS)
    assert all(ls.gates[q] is None for q in premarket.GATES)
