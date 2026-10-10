"""The options and flow blocks: a stand-in book drops the whole options block, a native row's levels and regimes
come out as distances and words, the flow block drops on a missing, stale or failed tape, SPY's volume is set
against the recorder's copies, and the real 10-09 14:30 read builds both with no price level or date in them."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import write_json, write_jsonl
from payload_fixtures import DAY, SIGMA, SPOT, at, fake_inputs, make_row, station_state_dir

from spx_claude_forecast import station_stores
from spx_claude_forecast.paths import ensure_folders
from spx_claude_forecast.payload.blocks.flow import build_flow_block
from spx_claude_forecast.payload.blocks.options import build_options_block
from spx_claude_forecast.payload.frozen_inputs import load_frozen_inputs

CUT = at(14, 30, 12)
PRIOR_DAYS = ["2026-10-08", "2026-10-07", "2026-10-06", "2026-10-05", "2026-10-02", "2026-10-01", "2026-09-30", "2026-09-29",
              "2026-09-28", "2026-09-25", "2026-09-24", "2026-09-23"]


def native_views(**over) -> dict:
    gv = {"regime": "long_gamma", "regime_source": "0dte", "regime_0dte_vol": "short_gamma", "regime_tenor": "long_gamma",
          "flip": SPOT - 41.0, "magnet": SPOT - 13.0, "charm_wall": SPOT + 7.0, "call_wall_gamma": SPOT + 2.0, "put_wall_gamma": SPOT - 13.0,
          "gamma_above_spot": 600.0, "gamma_below_spot": 400.0, "pin_top_share": 0.0995,
          "net_by_strike_tenor": [[SPOT - 100, -30.0], [SPOT - 50, -20.0], [SPOT + 50, 60.0], [SPOT + 100, 40.0]],
          "vol_side_by_strike": [[SPOT - 5, 300, 320], [SPOT + 5, 200, 180]],
          "oi_side_by_strike": [[SPOT - 5, 50, 50], [SPOT + 5, 30, 30]],
          "net_gex": 36.3e9, "net_gex_tenor": 82.9e9}
    gv.update(over)
    return gv


def native_row(ts, **over) -> dict:
    row = make_row(ts, gex_views=native_views(), net_exposure={"prev_close_gamma": -23.3e9},
                   call_wall_tenor=SPOT + 187.0, put_wall_tenor=SPOT - 313.0, atm_iv=0.0844)
    row.update(over)
    return row


def absent_paths(result) -> dict[str, str]:
    return {a["path"]: a["why"] for a in result.absent}


# --- options ---------------------------------------------------------------------------------------------

def test_a_stand_in_or_unknown_book_drops_the_whole_options_block(tmp_path):
    proxy = build_options_block(fake_inputs(tmp_path, row=native_row(CUT), options_book="stand_in"))
    assert proxy.is_empty and proxy.absent == [{"path": "options", "why": "stand_in_book"}]
    unknown = build_options_block(fake_inputs(tmp_path, row=native_row(CUT), options_book=None))
    assert unknown.absent == [{"path": "options", "why": "options_book_unknown"}]


def test_a_native_row_gives_regimes_net_gamma_and_levels_as_distances(tmp_path):
    earlier = native_row(CUT - timedelta(minutes=31), atm_iv=0.0805)
    row = native_row(CUT)
    inputs = fake_inputs(tmp_path, row=row, rows_today=[earlier, row])
    result = build_options_block(inputs)
    d = result.data
    assert d["book"] == "native"
    assert d["regime"] == {"0dte_oi": "long", "0dte_volume": "short", "0_7dte_oi": "long"}
    assert d["net_gex_usd_bn_per_1pct"] == {"0dte": 36.3, "0_7dte": 82.9, "at_prior_close_0_7dte": -23.3}
    assert d["gamma_above_pct"] == {"0dte": {"v": 60}, "1_7dte": {"v": 67}}      # no prior sessions: no rank
    assert d["top_strike_share_pct"] == {"v": 10.0}
    levels = {e["what"]: e["sig"] for e in d["levels_minus_price_sig"]}
    assert levels == {"gamma_flip": round(-41.0 / SIGMA, 2), "call_side_heavy": round(2.0 / SIGMA, 2), "charm_wall": round(7.0 / SIGMA, 2),
                      "magnet": round(-13.0 / SIGMA, 2), "put_side_heavy": round(-13.0 / SIGMA, 2),
                      "call_wall_1_7dte": round(187.0 / SIGMA, 2), "put_wall_1_7dte": round(-313.0 / SIGMA, 2)}
    assert all("r" not in e for e in d["levels_minus_price_sig"])
    assert d["heavy_strikes_moved_30m"] is False
    assert d["turnover_x_oi"] == 6.2
    assert d["atm_iv_30m_volpts"] == 0.39
    assert "skew_put25_minus_call25_volpts" not in d
    assert absent_paths(result) == {"options.skew_put25_minus_call25_volpts": "raw_tape_missing"}


def test_moved_heavy_strikes_blended_fallback_and_missing_fields_are_declared(tmp_path):
    earlier = native_row(CUT - timedelta(minutes=30), gex_views=native_views(call_wall_gamma=SPOT + 12.0))
    row = native_row(CUT, gex_views=native_views(regime_source="blended", flip=None, pin_top_share=None, vol_side_by_strike=[]),
                     put_wall_tenor=None, net_exposure={})
    result = build_options_block(fake_inputs(tmp_path, row=row, rows_today=[earlier, row]))
    d, why = result.data, absent_paths(result)
    assert d["heavy_strikes_moved_30m"] is True
    assert d["regime"] == {"0dte_volume": "short", "0_7dte_oi": "long"}
    assert why["options.regime.0dte_oi"] == "blended_book_fallback"
    assert why["options.levels_minus_price_sig.gamma_flip"] == "not_in_diary"
    assert why["options.levels_minus_price_sig.put_wall_1_7dte"] == "not_in_diary"
    assert why["options.top_strike_share_pct"] == "not_in_diary"
    assert why["options.turnover_x_oi"] == "not_in_diary"
    assert why["options.net_gex_usd_bn_per_1pct.at_prior_close_0_7dte"] == "not_in_diary"
    assert d["net_gex_usd_bn_per_1pct"] == {"0dte": 36.3, "0_7dte": 82.9}


def test_without_a_row_half_an_hour_ago_the_half_hour_changes_are_absent(tmp_path):
    row = native_row(CUT)
    result = build_options_block(fake_inputs(tmp_path, row=row, rows_today=[row]))
    why = absent_paths(result)
    assert why["options.heavy_strikes_moved_30m"] == "no_row_30m_ago"
    assert why["options.atm_iv_30m_volpts"] == "no_row_30m_ago"


# --- flow ------------------------------------------------------------------------------------------------

def tape_line(ts, tilt: float = 0.054, told: float = 0.54, trades: int = 1500) -> dict:
    return {"ts": ts.isoformat(), "engine": "lob_flow",
            "snapshot": {"tilt": tilt, "determinate_share": told, "tape_trades": trades, "defense": {}}}


def spy_line(ts, spread: float = 0.01, size: int = 400) -> dict:
    return {"ts": ts.isoformat(), "engine": "spy_depth", "snapshot": {},
            "baseline_rows": [{"key": "size|spy|mid|1430|mid", "value": size}, {"key": "spread|spy|mid|1430|mid", "value": spread}]}


def write_tape(state_dir: Path, day: str, lines: list[dict]) -> None:
    write_jsonl(station_stores.lob_flow_agg_file(state_dir, day), lines)


def test_flow_is_absent_without_a_tape_and_on_a_stale_one(tmp_path):
    inputs = fake_inputs(tmp_path)
    assert build_flow_block(inputs).absent == [{"path": "flow", "why": "tape_missing"}]
    write_tape(inputs.state_dir, DAY, [tape_line(CUT - timedelta(minutes=4))])
    assert build_flow_block(inputs).absent == [{"path": "flow", "why": "tape_stale"}]
    write_tape(inputs.state_dir, DAY, [tape_line(CUT + timedelta(seconds=30))])      # written after the cut: not known yet
    assert build_flow_block(inputs).absent == [{"path": "flow", "why": "tape_missing"}]


def test_flow_fails_its_content_check_on_no_trades_or_an_unusual_count(tmp_path):
    inputs = fake_inputs(tmp_path, prior_bars={d: [] for d in PRIOR_DAYS})
    for d in PRIOR_DAYS:
        write_tape(inputs.state_dir, d, [tape_line(at(14, 29, 30, day=d), tilt=0.01, trades=1000)])
    write_tape(inputs.state_dir, DAY, [tape_line(CUT - timedelta(seconds=40), trades=0)])
    assert build_flow_block(inputs).absent == [{"path": "flow", "why": "tape_content_failed"}]
    write_tape(inputs.state_dir, DAY, [tape_line(CUT - timedelta(seconds=40), trades=250)])      # under 0.3x the usual 1000
    assert build_flow_block(inputs).absent == [{"path": "flow", "why": "tape_content_failed"}]
    write_tape(inputs.state_dir, DAY, [tape_line(CUT - timedelta(seconds=40), trades=3500)])     # over 3x
    assert build_flow_block(inputs).absent == [{"path": "flow", "why": "tape_content_failed"}]
    write_tape(inputs.state_dir, DAY, [tape_line(CUT - timedelta(seconds=40), trades=1500)])
    result = build_flow_block(inputs)
    assert result.data["tilt_15m"] == {"v": 0.054, "r": [12, 12]} and result.data["told_share"] == 0.54
    why = absent_paths(result)
    assert why["flow.lean_30m_vs_usual"] == "raw_tape_missing" and why["flow.big_prints_10m"] == "raw_tape_missing"
    assert why["flow.premium_pace_30m_x"] == "raw_tape_missing" and why["flow.spy_spread_cents"] == "not_recorded"
    assert why["flow.spy_volume_30m_x"] == "no_spy_minutes"


def test_spy_volume_is_set_against_the_recorders_copies_at_the_same_minutes(tmp_path):
    today = {str(m): 10_000 for m in range(570, 870)}          # 09:30 to 14:29, flat: the last 30 minutes hold 300,000
    today["875"] = 99_999                                       # a minute after the cut must not count
    inputs = fake_inputs(tmp_path, prior_bars={d: [] for d in PRIOR_DAYS}, spy_minute_volumes=today)
    write_tape(inputs.state_dir, DAY, [tape_line(CUT - timedelta(seconds=40)), spy_line(CUT - timedelta(seconds=40), spread=0.02)])
    for i, d in enumerate(PRIOR_DAYS):
        per_minute = 20_000 + 1_000 * i                         # median over the 12 sessions is 25,500 a minute
        write_json(inputs.paths.siege_spy_minutes_file(d), {"day": d, "minute_volumes": {str(m): per_minute for m in range(570, 960)}})
    result = build_flow_block(inputs)
    assert result.data["spy_volume_30m_x"] == {"v": round(300_000 / (30 * 25_500), 2), "r": [0, 12]}
    assert result.data["spy_spread_cents"] == 2


def test_spy_volume_needs_half_the_window_and_a_live_feed(tmp_path):
    inputs = fake_inputs(tmp_path, spy_minute_volumes={str(m): 10_000 for m in range(570, 850)})      # stops 20 minutes before the cut
    write_tape(inputs.state_dir, DAY, [tape_line(CUT - timedelta(seconds=40))])
    assert absent_paths(build_flow_block(inputs))["flow.spy_volume_30m_x"] == "spy_feed_stopped"
    thin = {str(m): 10_000 for m in range(570, 840)}
    thin.update({"868": 10_000, "869": 10_000})                 # the feed is live, but the window has 2 of 30 minutes
    inputs = fake_inputs(tmp_path, spy_minute_volumes=thin)
    assert absent_paths(build_flow_block(inputs))["flow.spy_volume_30m_x"] == "spy_minutes_incomplete"


# --- the real store --------------------------------------------------------------------------------------

REAL_READ = "live:2026-10-09T14:30:12.458122-04:00"
WHOLE_NUMBER = re.compile(r"(?<![\w.])-?\d+(?![\d.])")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@pytest.mark.skipif(station_state_dir() is None, reason="needs the station's state")
def test_the_real_1430_read_builds_both_blocks_without_a_price_level_or_a_date():
    state_dir = station_state_dir()
    inputs = load_frozen_inputs(state_dir, ensure_folders(state_dir), REAL_READ)
    options, flow = build_options_block(inputs), build_flow_block(inputs)
    o = options.data
    assert o["book"] == "native"
    assert set(o["regime"]) == {"0dte_oi", "0dte_volume", "0_7dte_oi"} and set(o["regime"].values()) <= {"long", "short", "uncertain"}
    assert set(o["net_gex_usd_bn_per_1pct"]) == {"0dte", "0_7dte", "at_prior_close_0_7dte"}
    assert {e["what"] for e in o["levels_minus_price_sig"]} >= {"gamma_flip", "call_side_heavy", "charm_wall", "magnet", "put_side_heavy"}
    flip = next(e for e in o["levels_minus_price_sig"] if e["what"] == "gamma_flip")
    assert "r" in flip and "r" in o["gamma_above_pct"]["0dte"] and "r" in o["top_strike_share_pct"]
    assert isinstance(o["heavy_strikes_moved_30m"], bool) and o["turnover_x_oi"] > 0
    assert "v" in o["skew_put25_minus_call25_volpts"] and isinstance(o["atm_iv_30m_volpts"], float)
    f = flow.data
    assert "r" in f["tilt_15m"] and 0 <= f["told_share"] <= 1
    assert "r" in f["lean_30m_vs_usual"] and isinstance(f["big_prints_10m"], int)
    assert "r" in f["premium_pace_30m_x"] and "r" in f["spy_volume_30m_x"] and isinstance(f["spy_spread_cents"], int)
    for block in (o, f):
        text = json.dumps(block)
        assert not DATE.search(text), text
        assert all(abs(int(n)) < 1000 for n in WHOLE_NUMBER.findall(text)), text
    assert not any(a["path"] in ("options", "flow") for a in options.absent + flow.absent)
