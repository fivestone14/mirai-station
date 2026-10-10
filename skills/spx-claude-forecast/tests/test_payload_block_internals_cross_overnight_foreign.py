"""The internals, cross_asset, overnight and foreign blocks: each rule on synthetic inputs, then one read of the
real store."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import write_json, write_jsonl
from payload_fixtures import DAY, SIGMA, FakeMarket, at, fake_inputs, make_row, station_state_dir

from spx_claude_forecast import station_stores
from spx_claude_forecast.payload import units
from spx_claude_forecast.payload.blocks.cross_asset import build_cross_asset_block
from spx_claude_forecast.payload.blocks.foreign import build_foreign_block
from spx_claude_forecast.payload.blocks.internals import build_internals_block
from spx_claude_forecast.payload.blocks.overnight import build_overnight_block
from spx_claude_forecast.payload.frozen_inputs import previous_session_day

PRIOR_DAY = "2026-10-08"
CUT = at(14, 30, 12)
LAST_BAR = at(14, 30)                 # the 14:29 breadth bar is known when its minute finishes


def absent(result, path: str) -> str | None:
    return next((a["why"] for a in result.absent if a["path"] == path), None)


# --- internals --------------------------------------------------------------------------------------------

def breadth_market(now_adv=1586.0, now_dec=1150.0, earlier_adv=1500.0, earlier_dec=1010.0) -> FakeMarket:
    earlier = LAST_BAR - timedelta(minutes=30)
    return FakeMarket({"$ADVN": [(earlier, earlier_adv), (LAST_BAR, now_adv)],
                       "$DECN": [(earlier, earlier_dec), (LAST_BAR, now_dec)],
                       "$TICK": [(LAST_BAR, 151.0)], "$VOLD": [(LAST_BAR, 1.5e9)]})


def test_internals_add_is_advancers_less_decliners_at_the_cut(tmp_path):
    result = build_internals_block(fake_inputs(tmp_path, cut=CUT, market=breadth_market()))
    assert result.data == {"add_live_approx": 436, "add_30m_change": -54, "adv_share_pct": 58}
    assert absent(result, "internals.tick_vold") == "same_day_wrong"
    assert all(isinstance(v, int) for v in result.data.values())


def test_internals_absent_when_the_counts_are_not_quoted(tmp_path):
    result = build_internals_block(fake_inputs(tmp_path, cut=CUT, market=FakeMarket({"$TICK": [(LAST_BAR, 151.0)]})))
    assert result.is_empty and absent(result, "internals") == "not_quoted"
    assert absent(result, "internals.tick_vold") == "same_day_wrong"


def test_internals_absent_when_the_breadth_feed_stopped(tmp_path):
    stale = FakeMarket({"$ADVN": [(at(11, 0), 1500.0)], "$DECN": [(at(11, 0), 1000.0)]})
    result = build_internals_block(fake_inputs(tmp_path, cut=CUT, market=stale))
    assert result.is_empty and absent(result, "internals") == "stale"


def test_internals_30m_change_needs_a_value_half_an_hour_ago(tmp_path):
    recent = FakeMarket({"$ADVN": [(LAST_BAR, 1586.0)], "$DECN": [(LAST_BAR, 1150.0)]})
    result = build_internals_block(fake_inputs(tmp_path, cut=CUT, market=recent))
    assert result.data == {"add_live_approx": 436, "adv_share_pct": 58}
    assert absent(result, "internals.add_30m_change") == "no_value_30m_ago"


# --- cross_asset ------------------------------------------------------------------------------------------

def snapshot_context(tmp_path: Path, taken: datetime, quotes: dict) -> None:
    """One context snapshot with Schwab's prior-close field, as the context job writes it."""
    write_jsonl(station_stores.context_file(tmp_path / "state", DAY), [{"ts": taken.isoformat(), "quotes": quotes}])


def vol_closes(**over) -> dict:
    closes = {"$VIX": [{"day": PRIOR_DAY, "close": 15.41}], "$VIX9D": [{"day": PRIOR_DAY, "close": 12.21}],
              "$TNX": [{"day": PRIOR_DAY, "close": 52.31}]}
    closes.update(over)
    return closes


def vol_market(taken: datetime, vix=14.87, vix3m=17.85, vix9d=11.18, tnx_pct=5.25) -> FakeMarket:
    return FakeMarket({"$VIX": [(taken, vix)], "$VIX3M": [(taken, vix3m)], "$VIX9D": [(taken, vix9d)], "$TNX": [(taken, tnx_pct)]})


def test_cross_asset_vix_curve_from_the_row_and_the_quotes(tmp_path):
    taken = at(14, 30, 5)
    snapshot_context(tmp_path, taken, {"$VIX": {"last": 14.87, "close": 15.41}, "$VIX3M": {"last": 17.85, "close": 18.08}})
    inputs = fake_inputs(tmp_path, cut=CUT, market=vol_market(taken), daily_closes=vol_closes(), row=make_row(CUT, vix_ts=0.833))
    result = build_cross_asset_block(inputs)
    assert result.data == {"vix": 14.87, "vix_minus_prior_close": -0.54, "vix_vix3m": 0.833, "vix_vix3m_prior_close": 0.852,
                           "backwardated": False, "vix9d_vix": 0.752, "ten_year_bp_since_close": {"v": 1.9}}
    assert absent(result, "cross_asset.vx_futures") == "schwab_refuses_symbol"


def test_cross_asset_backwardation_is_vix_over_vix3m_above_one(tmp_path):
    taken = at(14, 30, 5)
    snapshot_context(tmp_path, taken, {"$VIX3M": {"last": 18.0, "close": 18.08}})
    inputs = fake_inputs(tmp_path, cut=CUT, market=vol_market(taken, vix=20.0, vix3m=18.0), daily_closes=vol_closes(),
                         row=make_row(CUT, vix_ts=None))
    result = build_cross_asset_block(inputs)
    assert result.data["vix_vix3m"] == 1.111 and result.data["backwardated"] is True


def test_cross_asset_ratios_wait_for_the_quotes_to_leave_the_prior_close_at_the_open(tmp_path):
    taken = at(9, 30, 46)
    snapshot_context(tmp_path, taken, {"$VIX3M": {"last": 18.08, "close": 18.08}})
    inputs = fake_inputs(tmp_path, cut=at(9, 30, 50), market=vol_market(taken, vix=15.19, vix3m=18.08, vix9d=12.21),
                         daily_closes=vol_closes(), row=make_row(at(9, 30, 50), vix_ts=0.84))
    result = build_cross_asset_block(inputs)
    assert result.data["vix"] == 15.19 and "vix_vix3m" not in result.data and "backwardated" not in result.data
    assert absent(result, "cross_asset.vix_vix3m") == "stale_at_open"
    assert absent(result, "cross_asset.vix9d_vix") == "stale_at_open"


def test_cross_asset_ten_year_is_judged_at_the_treasury_close_and_pinned_in_bp(tmp_path):
    cut = at(15, 30, 3)
    market = FakeMarket({"$TNX": [(at(14, 59, 50), 5.244), (cut, 5.244)]})
    result = build_cross_asset_block(fake_inputs(tmp_path, cut=cut, market=market, daily_closes=vol_closes()))
    assert result.data["ten_year_bp_since_close"] == {"v": 1.3}
    stopped = FakeMarket({"$TNX": [(at(14, 0), 5.244)]})
    result = build_cross_asset_block(fake_inputs(tmp_path, cut=cut, market=stopped, daily_closes=vol_closes()))
    assert absent(result, "cross_asset.ten_year_bp_since_close") == "stale"


def test_cross_asset_ten_year_rank_is_of_the_move_size_at_the_same_clock(tmp_path):
    days = [DAY]
    while len(days) < 12:
        days.append(previous_session_day(days[-1]))
    closes = [{"day": d, "close": 52.0 + k * 0.1} for k, d in enumerate(days[1:])]     # each prior day's close, newest first
    prior_markets = {}
    for k, d in enumerate(days[1:11]):
        move_bp = (k + 1) * 0.5 * (-1 if k % 2 else 1)                                  # sizes 0.5 .. 5.0 bp, both ways
        prior_markets[d] = FakeMarket({"$TNX": [(at(14, 30, day=d), (closes[k + 1]["close"]) / 10 + move_bp / 100)]})
    market = FakeMarket({"$TNX": [(at(14, 30, 5), 52.0 / 10 - 0.021)]})                 # -2.1 bp today
    inputs = fake_inputs(tmp_path, cut=CUT, market=market, daily_closes={"$TNX": closes[::-1]}, prior_markets=prior_markets)
    result = build_cross_asset_block(inputs)
    assert result.data["ten_year_bp_since_close"] == {"v": -2.1, "r": [4, 10]}


# --- overnight --------------------------------------------------------------------------------------------

ES_CLOSE, ZN_CLOSE = 7800.0, 104.0
# (start, end, points): each move sits strictly inside one of the station's stretches of the night into 10-09
ES_MOVES = (("2026-10-08T20:00", "2026-10-09T00:00", 30.0),      # asia
            ("2026-10-09T02:35", "2026-10-09T03:25", -5.0),      # europe_open
            ("2026-10-09T04:00", "2026-10-09T07:00", 2.0),       # europe_morning
            ("2026-10-09T08:35", "2026-10-09T08:40", -3.0),      # report_window
            ("2026-10-09T08:50", "2026-10-09T09:20", 1.0))       # last_stretch
ZN_MOVES = (("2026-10-08T22:00", "2026-10-09T06:00", -0.2),)


def path_price(base: float, moves: tuple, t: datetime) -> float:
    price = base
    for start, end, points in moves:
        a, b = at(int(start[11:13]), int(start[14:16]), day=start[:10]), at(int(end[11:13]), int(end[14:16]), day=end[:10])
        price += points * min(max((t - a) / (b - a), 0.0), 1.0)
    return price


def night_rows(symbol: str, base: float, moves: tuple, contract: str, minutes: int = 5) -> list[dict]:
    """Five-minute bars in the store's own format from the prior session's last minutes to the open."""
    rows, t = [], at(15, 55, day=PRIOR_DAY)
    while t < at(9, 30):
        o, c = path_price(base, moves, t), path_price(base, moves, t + timedelta(minutes=minutes))
        session = "regular" if t < at(16, 0, day=PRIOR_DAY) else "premarket" if t >= at(4, 0) else "overnight"
        rows.append({"schema_version": 1, "day": DAY, "ts": t.isoformat(), "symbol": symbol, "contract": contract,
                     "contract_from": "roll_table", "bar_minutes": minutes, "open": o, "high": max(o, c) + 0.25,
                     "low": min(o, c) - 0.25, "close": c, "volume": 100.0, "session": session,
                     "source": "schwab_price_history", "saved_at": at(9, 26).isoformat(), "flags": []})
        t += timedelta(minutes=minutes)
    return rows


def write_night(tmp_path: Path, day: str = DAY, extra_rows: list[dict] | None = None) -> Path:
    rows = night_rows("/ES", ES_CLOSE, ES_MOVES, "/ESZ26") + night_rows("/ZN", ZN_CLOSE, ZN_MOVES, "/ZNZ26") + (extra_rows or [])
    return write_jsonl(tmp_path / "state" / "spx_jev" / "overnight" / f"{day}.jsonl", rows)


def test_overnight_reads_the_night_from_the_store_in_sig(tmp_path):
    write_night(tmp_path)
    result = build_overnight_block(fake_inputs(tmp_path, cut=CUT))
    assert result.absent == []
    assert result.data["es_vs_close_sig"] == {"v": units.sig(25.0, SIGMA)}            # 30 - 5 + 2 - 3 + 1, no nights to rank
    assert result.data["range_sig"] == {"v": units.sig(30.5, SIGMA)}                  # 7830.25 high, 7799.75 low
    assert result.data["legs_sig"] == {"after_close": 0.0, "asia": units.sig(30.0, SIGMA), "europe_open": units.sig(-5.0, SIGMA),
                                       "europe_morning": units.sig(2.0, SIGMA), "pre_report": 0.0,
                                       "report_window": units.sig(-3.0, SIGMA), "last_stretch": units.sig(1.0, SIGMA)}
    assert result.data["gap_origin"] == "asia"
    assert result.data["zn_pct"] == -0.19


def test_overnight_is_cut_at_a_read_before_the_open(tmp_path):
    write_night(tmp_path)
    result = build_overnight_block(fake_inputs(tmp_path, cut=at(8, 20)))
    assert set(result.data["legs_sig"]) == {"after_close", "asia", "europe_open", "europe_morning", "pre_report"}
    assert result.data["es_vs_close_sig"] == {"v": units.sig(27.0, SIGMA)}            # the report window has not come
    assert result.data["gap_origin"] == "asia"


def test_overnight_gap_origin_is_the_largest_leg_in_the_direction_of_the_night(tmp_path):
    moves = (("2026-10-08T20:00", "2026-10-09T00:00", 20.0),      # asia, the largest by size but against the night
             ("2026-10-09T02:35", "2026-10-09T03:25", -18.0),     # europe_open
             ("2026-10-09T04:00", "2026-10-09T07:00", -15.0))     # europe_morning
    rows = night_rows("/ES", ES_CLOSE, moves, "/ESZ26")
    write_jsonl(tmp_path / "state" / "spx_jev" / "overnight" / f"{DAY}.jsonl", rows)
    result = build_overnight_block(fake_inputs(tmp_path, cut=CUT))
    assert result.data["gap_origin"] == "europe_open"
    assert absent(result, "overnight.zn_pct") == "no_zn_bars"


def test_overnight_absent_without_the_nights_file(tmp_path):
    result = build_overnight_block(fake_inputs(tmp_path, cut=CUT))
    assert result.is_empty and absent(result, "overnight") == "no_overnight_file"


def test_overnight_leaves_out_a_move_across_a_contract_roll(tmp_path):
    write_night(tmp_path)
    write_json(tmp_path / "state" / "spx_jev" / "overnight" / "rolls.json",
               {"schema_version": 1, "current": {"/ES": "/ESZ26"}, "rejected": [], "windows_without_roll": {},
                "rolls": [{"symbol": "/ES", "day": DAY, "at": at(1, 0).isoformat(), "from": "/ESU26", "to": "/ESZ26"}]})
    result = build_overnight_block(fake_inputs(tmp_path, cut=CUT))
    assert absent(result, "overnight.es_vs_close_sig") == "contract_roll"
    assert absent(result, "overnight.range_sig") == "contract_roll"
    assert absent(result, "overnight.legs_sig.asia") == "contract_roll"
    assert absent(result, "overnight.gap_origin") == "leg_missing"
    assert "asia" not in result.data["legs_sig"] and result.data["legs_sig"]["after_close"] == 0.0
    assert result.data["zn_pct"] == -0.19


# --- foreign ----------------------------------------------------------------------------------------------

def test_foreign_absent_when_no_foreign_symbol_is_quoted(tmp_path):
    result = build_foreign_block(fake_inputs(tmp_path, cut=CUT, market=vol_market(at(14, 30, 5))))
    assert result.is_empty and absent(result, "foreign") == "not_recorded"


def test_foreign_returns_against_the_prior_sessions_last_quote(tmp_path):
    prior_end = at(15, 59, 30, day=PRIOR_DAY)
    prior = FakeMarket({"$N225": [(prior_end, 40000.0)], "$FCHI": [(prior_end, 8000.0)], "/6J": [(prior_end, 0.0068)]})
    today = FakeMarket({"$N225": [(at(9, 31), 39992.0), (at(14, 30, 5), 39992.0)],
                        "$FCHI": [(at(9, 31), 8060.0), (at(11, 31), 8076.0), (at(14, 30, 5), 8076.0)],
                        "/6J": [(at(14, 30, 5), 0.0068 / 1.02)]})
    result = build_foreign_block(fake_inputs(tmp_path, cut=CUT, market=today, prior_markets={PRIOR_DAY: prior}))
    assert result.data == {"tokyo_ret_pct": -0.02, "europe_ret_pct": 0.95, "europe_at": "11:31", "usdjpy_1d_pct": 2.0, "usdjpy_shock": True}
    assert absent(result, "foreign.dax_ret_pct") == "not_recorded"
    assert absent(result, "foreign.korea_taiwan") == "include_later"


def test_foreign_needs_the_prior_session_for_a_return(tmp_path):
    today = FakeMarket({"$DAX": [(at(14, 30, 5), 24000.0)]})
    result = build_foreign_block(fake_inputs(tmp_path, cut=CUT, market=today))
    assert result.data == {} and absent(result, "foreign.dax_ret_pct") == "no_prior_session_quote"


# --- the real store ---------------------------------------------------------------------------------------

READ_ID = "live:2026-10-09T14:30:12.458122-04:00"
DATE_STRING = re.compile(r"\d{4}-\d{2}-\d{2}")
# Issue counts, not price levels: advancers less decliners can pass a thousand on a broad day.
COUNT_FIELDS = ("add_live_approx", "add_30m_change")


def leaks(block: str, data) -> list[str]:
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)) and abs(node) >= 1000 and float(node).is_integer() and path.split(".")[-1] not in COUNT_FIELDS:
            found.append(f"{path}={node}")
        elif isinstance(node, str) and DATE_STRING.search(node):
            found.append(f"{path}={node!r}")
    walk(json.loads(json.dumps(data)), block)
    return found


@pytest.mark.skipif(station_state_dir() is None, reason="needs the station's state")
def test_real_read_builds_all_four_blocks_without_a_price_or_a_date():
    from spx_claude_forecast.paths import ensure_folders
    from spx_claude_forecast.payload.frozen_inputs import load_frozen_inputs
    state_dir = station_state_dir()
    inputs = load_frozen_inputs(state_dir, ensure_folders(state_dir), READ_ID)
    internals = build_internals_block(inputs)
    assert set(internals.data) == {"add_live_approx", "add_30m_change", "adv_share_pct"}
    assert all(isinstance(v, int) for v in internals.data.values()) and 0 <= internals.data["adv_share_pct"] <= 100
    assert absent(internals, "internals.tick_vold") == "same_day_wrong"
    cross = build_cross_asset_block(inputs)
    assert set(cross.data) == {"vix", "vix_minus_prior_close", "vix_vix3m", "vix_vix3m_prior_close", "backwardated", "vix9d_vix",
                               "ten_year_bp_since_close"}
    assert cross.data["backwardated"] == (cross.data["vix_vix3m"] > 1.0)
    assert len(cross.data["ten_year_bp_since_close"]["r"]) == 2 and cross.data["ten_year_bp_since_close"]["r"][1] >= 10
    assert absent(cross, "cross_asset.vx_futures") == "schwab_refuses_symbol"
    overnight = build_overnight_block(inputs)
    assert set(overnight.data) == {"es_vs_close_sig", "range_sig", "gap_origin", "zn_pct", "legs_sig"}
    assert set(overnight.data["legs_sig"]) == {"after_close", "asia", "europe_open", "europe_morning", "pre_report", "report_window", "last_stretch"}
    assert overnight.data["gap_origin"] in overnight.data["legs_sig"]
    assert overnight.data["es_vs_close_sig"]["r"][1] >= 10 and overnight.data["range_sig"]["v"] >= 0
    foreign = build_foreign_block(inputs)
    assert foreign.is_empty and absent(foreign, "foreign") == "not_recorded"
    for block, result in (("internals", internals), ("cross_asset", cross), ("overnight", overnight), ("foreign", foreign)):
        assert leaks(block, result.data) == []
        assert leaks(block, result.absent) == []
