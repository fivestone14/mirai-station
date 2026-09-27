"""The 20-night rank, offline: thirds, the minimum of usable nights, the skip rules, and es_move's guard."""
from __future__ import annotations

import json
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


def test_a_stretch_moves_between_the_newest_prices_by_its_edges_on_either_resolution():
    t = datetime(2026, 9, 23, 3, 0, tzinfo=ET)
    rows = [_bar(t - timedelta(minutes=2), 6700.0), _bar(t + timedelta(minutes=30), 6733.5),
            dict(_bar(t + timedelta(minutes=55), 6720.0), bar_minutes=5)]
    move = night_ranks.window_move(rows, "/ES", t, t + timedelta(hours=1))
    assert round(move.pct, 4) == 0.2985 and move.start_at == t - timedelta(minutes=1) and move.end_at == t + timedelta(hours=1)
    assert night_ranks.price_by(rows, "/ES", t + timedelta(minutes=45)) == (6733.5, t + timedelta(minutes=31))
    assert night_ranks.window_move(rows, "/ES", t - timedelta(hours=1), t) is None      # no price by the start
    assert night_ranks.window_move(rows, "/ZN", t, t + timedelta(hours=1)) is None


def test_the_same_stretch_is_measured_on_the_last_nights_with_their_skips(tmp_path):
    prior = _nights_before(DAY, 20)
    for k, d in enumerate(prior):
        _save(tmp_path, d, 0.1 * (k + 1))

    def window(d: date) -> tuple[datetime, datetime]:
        return overnight.night_window(d)[0] + timedelta(minutes=overnight.NIGHT_LEAD_MIN), datetime.combine(d, time(9, 27), tzinfo=ET)
    base = night_ranks.prior_window_nights(tmp_path, DAY, "/ES", window, rolls.load(tmp_path), lambda m: m.pct)
    assert [n.day for n in base] == [d.isoformat() for d in prior]
    assert {n.day: n.skip for n in base if n.skip} == {"2026-09-08": "holiday"}
    assert [round(n.value, 4) for n in base if n.skip is None][:3] == [0.1, 0.2, 0.3]


def test_a_stretchs_range_spans_its_bars_of_either_resolution_leaving_flagged_ones_out():
    t = datetime(2026, 9, 23, 3, 0, tzinfo=ET)
    rows = [_bar(t - timedelta(minutes=1), 6800.0), dict(_bar(t, 6700.0), high=6710.0, low=6690.0),
            dict(_bar(t + timedelta(minutes=10), 6705.0), bar_minutes=5, high=6720.0),
            dict(_bar(t + timedelta(minutes=20), 6000.0), flags=["ohlc_inconsistent"]), _bar(t + timedelta(minutes=59), 6900.0)]
    rng = night_ranks.window_range(rows, "/ES", t, t + timedelta(minutes=59))
    assert round(rng.pct, 4) == round(100 * (6720 / 6690 - 1), 4)
    assert (rng.start_at, rng.end_at) == (t + timedelta(minutes=1), t + timedelta(minutes=15))
    assert night_ranks.window_range(rows, "/ZN", t, t + timedelta(hours=1)) is None


def test_the_same_stretchs_range_is_measured_on_the_last_nights(tmp_path):
    for k, d in enumerate(_nights_before(DAY, 20)):
        _save(tmp_path, d, 0.1 * (k + 1))

    def window(d: date) -> tuple[datetime, datetime]:
        return overnight.night_window(d)[0], datetime.combine(d, time(9, 27), tzinfo=ET)
    base = night_ranks.prior_window_ranges(tmp_path, DAY, "/ES", window, rolls.load(tmp_path))
    assert {n.day: n.skip for n in base if n.skip} == {"2026-09-08": "holiday"}
    assert [round(n.value, 4) for n in base if n.skip is None][:2] == [0.1, 0.2]


def test_the_quoted_contract_is_the_newest_saves_by_the_read(tmp_path):
    lines = [{"day": DAY.isoformat(), "saved_at": f"{DAY}T{hm}:00-04:00", "symbols": {"/ES": {"contract_quoted": c}}}
             for hm, c in (("09:26", "/ESZ26"), ("16:20", "/ESH27"))]
    overnight.manifest_path(tmp_path).parent.mkdir(parents=True)
    overnight.manifest_path(tmp_path).write_text("".join(json.dumps(line) + "\n" for line in lines))
    assert night_ranks.quoted_contract(tmp_path, DAY, "/ES", datetime.combine(DAY, time(9, 28), tzinfo=ET)) == "/ESZ26"
    assert night_ranks.quoted_contract(tmp_path, DAY, "/ES", datetime.combine(DAY, time(17, 0), tzinfo=ET)) == "/ESH27"
    assert night_ranks.quoted_contract(tmp_path, DAY, "/ES", datetime.combine(DAY, time(9, 0), tzinfo=ET)) is None
    assert night_ranks.quoted_contract(tmp_path, DAY, "/ZN", datetime.combine(DAY, time(17, 0), tzinfo=ET)) is None


def test_a_save_whose_quote_failed_does_not_hide_a_roll_an_earlier_save_saw(tmp_path):
    lines = [{"day": DAY.isoformat(), "saved_at": f"{DAY}T{hm}:00-04:00", "symbols": {"/ES": {"contract_quoted": c}}}
             for hm, c in (("09:26", "/ESH27"), ("09:28", None))]
    overnight.manifest_path(tmp_path).parent.mkdir(parents=True)
    overnight.manifest_path(tmp_path).write_text("".join(json.dumps(line) + "\n" for line in lines))
    quoted = night_ranks.quoted_contract(tmp_path, DAY, "/ES", datetime.combine(DAY, time(9, 28, 30), tzinfo=ET))
    assert quoted == "/ESH27"
    assert night_ranks.roll_pending({"current": {"/ES": "/ESZ26"}}, "/ES", quoted)


def test_a_rewritten_night_is_read_afresh(tmp_path):
    for d in _nights_before(DAY, 20):
        _save(tmp_path, d, 0.2)
    _save(tmp_path, DAY, 0.3)
    now = datetime.combine(DAY, time(9, 27), tzinfo=ET)
    assert night_ranks.es_move(tmp_path, now)["move_pct"] == 0.3
    _save(tmp_path, DAY, -0.45)
    assert night_ranks.es_move(tmp_path, now)["move_pct"] == -0.45


def test_ranked_move_reads_the_rows_it_is_given(tmp_path):
    for k, d in enumerate(_nights_before(DAY, 20)):
        _save(tmp_path, d, 0.05 * (k + 1))
    close = overnight.night_window(DAY)[0] + timedelta(minutes=overnight.NIGHT_LEAD_MIN)
    rows = [_bar(close - timedelta(minutes=1), 6700.0), _bar(datetime.combine(DAY, time(9, 20), tzinfo=ET), 6700.0 * 1.0055)]
    ranked, why = night_ranks.ranked_move(rows, tmp_path, DAY, "/ES", time(9, 27), rolls.load(tmp_path))
    assert why == "" and round(ranked.move.pct, 4) == 0.55
    assert (ranked.rank.larger_than, ranked.rank.of) == (9, 19)                         # Labor Day's 0.50% night sits out
    ranked, why = night_ranks.ranked_move(rows, tmp_path, DAY, "/ES", time(9, 40), rolls.load(tmp_path))
    assert ranked is None and why == "no /ES price at its prior close or within the last 10 minutes in the overnight store"
