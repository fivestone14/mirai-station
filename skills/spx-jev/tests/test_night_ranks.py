"""The 20-night rank, offline: thirds, the minimum of usable nights, the skip rules, and es_move's guard."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from spx_jev import night_ranks, overnight, rolls
from spx_jev.cuts import OVERNIGHT_RANK_MIN_NIGHTS
from spx_jev.night_ranks import Night, mark_short, rank_night
from spx_jev.sessions import previous_trading_day

ET = ZoneInfo("America/New_York")
DAY = date(2026, 9, 23)                                   # a Wednesday; its 20 prior nights run back past Labor Day


def _bar(ts: datetime, close: float) -> dict:
    return {"schema_version": 1, "day": None, "ts": ts.isoformat(), "symbol": "/ES", "contract": "/ESZ26",
            "contract_from": "roll_table", "bar_minutes": 1, "open": close, "high": close, "low": close, "close": close,
            "volume": 1.0, "session": "overnight", "source": "test", "saved_at": "", "flags": []}


def _save(state_dir, day: date, move_pct: float, read_clock: time = time(9, 26)):
    """One night on file: /ES at 6700 at the prior close and ``move_pct`` away at ``read_clock``."""
    close = overnight.night_window(day)[0] + timedelta(minutes=overnight.NIGHT_LEAD_MIN)
    rows = [_bar(close - timedelta(minutes=1), 6700.0),
            _bar(datetime.combine(day, read_clock, tzinfo=ET), 6700.0 * (1 + move_pct / 100))]
    overnight.write_night(state_dir, day.isoformat(), [dict(r, day=day.isoformat()) for r in rows])


def _nights_before(day: date, n: int) -> list[date]:
    out = []
    while len(out) < n:
        day = previous_trading_day(day)
        out.append(day)
    return out[::-1]


def test_a_value_is_ranked_in_thirds_against_the_usable_nights():
    nights = [Night(f"d{k}", float(k)) for k in range(20)]
    rank, why = rank_night(15.5, nights)
    assert why is None and (rank.larger_than, rank.of, rank.band) == (16, 20, "top third")
    assert rank_night(0.5, nights)[0].band == "bottom third" and rank_night(9.5, nights)[0].band == "middle third"
    assert rank.words("moves") == "larger than 16 of the last 20 nights' moves, top third"


def test_only_the_last_twenty_nights_count_and_skipped_ones_sit_out():
    nights = [Night("old", 100.0)] * 5 + [Night(f"d{k}", float(k), "roll" if k < 3 else None) for k in range(20)]
    rank, _ = rank_night(10.0, nights)
    assert rank.of == 17 and rank.larger_than == 7                                     # 3..9 are under 10; 'old' is past the window


def test_under_the_minimum_the_rank_is_omitted_with_the_reason():
    nights = ([Night(f"r{k}", None, "roll") for k in range(4)] + [Night(f"h{k}", None, "holiday") for k in range(7)]
              + [Night(f"u{k}", 1.0) for k in range(OVERNIGHT_RANK_MIN_NIGHTS - 1)])
    rank, why = rank_night(2.0, nights)
    assert rank is None and why == f"only 9 usable of the last 20 nights (holiday 7, roll 4 left out), fewer than {OVERNIGHT_RANK_MIN_NIGHTS}"


def test_a_night_well_under_the_usual_coverage_is_short():
    nights = mark_short([Night(f"d{k}", 1.0, None, 1.0) for k in range(9)] + [Night("thin", 1.0, None, 0.85),
                                                                             Night("fine", 1.0, None, 0.95)])
    assert [n.day for n in nights if n.skip == "short"] == ["thin"]


def test_es_move_ranks_the_nights_size_and_names_its_side(tmp_path):
    for k, d in enumerate(_nights_before(DAY, 20)):
        _save(tmp_path, d, 0.05 * (k + 1) * (-1) ** k)                                  # sizes 0.05% to 1.00%, both ways
    _save(tmp_path, DAY, -0.62)
    out = night_ranks.es_move(tmp_path, datetime.combine(DAY, time(9, 27), tzinfo=ET))
    assert out["side"] == "down" and out["move_pct"] == -0.62
    assert out["rank"] == {"band": "middle third", "larger_than": 11, "of": 19}         # Labor Day's night (0.50%) sits out
    assert out["words"] == "since 16:00 yesterday S&P futures are down, a move larger than 11 of the last 19 nights' moves to this time, middle third"


def test_es_move_refuses_a_night_across_a_roll_and_skips_roll_nights_in_its_base(tmp_path):
    prior = _nights_before(DAY, 20)
    for k, d in enumerate(prior):
        _save(tmp_path, d, 0.1 * (k + 1))
    roll_night = prior[-5]
    at = datetime.combine(roll_night, time(3, 0), tzinfo=ET)
    found = {"rolls": [{"symbol": "/ES", "day": roll_night.isoformat(), "at": at.isoformat()}], "rejected": [],
             "windows_without_roll": [], "typical_step": 0.01, "unit": "percent"}
    folder = tmp_path / overnight.OVERNIGHT_SUBDIR
    table = rolls.merge(rolls.load(folder), "/ES", found, "2026-08-01", "/ESZ26")
    rolls.save(folder, table)
    base = night_ranks.prior_nights(tmp_path, DAY, "/ES", time(9, 27), table, lambda m: abs(m.pct))
    assert {n.day: n.skip for n in base if n.skip} == {"2026-09-08": "holiday", roll_night.isoformat(): "roll"}

    _save(tmp_path, DAY, 0.3)
    table["rolls"][0]["at"] = datetime.combine(DAY, time(2, 0), tzinfo=ET).isoformat()   # the roll falls inside tonight
    rolls.save(folder, table)
    assert "rolled to the next contract" in night_ranks.es_move(tmp_path, datetime.combine(DAY, time(9, 27), tzinfo=ET))["omitted"]


def test_es_move_waits_for_a_roll_the_quote_shows_and_the_table_has_not_located(tmp_path):
    folder = tmp_path / overnight.OVERNIGHT_SUBDIR
    rolls.save(folder, {**rolls.load(folder), "current": {"/ES": "/ESZ26"}})
    out = night_ranks.es_move(tmp_path, datetime.combine(DAY, time(9, 27), tzinfo=ET), quoted="/ESH27")
    assert out["omitted"].startswith("Schwab quotes /ESH27 but the roll table is still on /ESZ26")


def test_es_move_is_omitted_without_enough_nights_or_a_price(tmp_path):
    now = datetime.combine(DAY, time(9, 27), tzinfo=ET)
    assert night_ranks.es_move(tmp_path, now)["omitted"].startswith("no /ES price")
    for d in _nights_before(DAY, 5):
        _save(tmp_path, d, 0.2)
    _save(tmp_path, DAY, 0.3)
    assert night_ranks.es_move(tmp_path, now)["omitted"].startswith("overnight move not ranked: only 5 usable of the last 20 nights")
    assert night_ranks.es_move(tmp_path, datetime(2026, 9, 20, 9, 27, tzinfo=ET)) == {"omitted": "2026-09-20 is not a market day"}
