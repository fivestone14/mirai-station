"""The sentiment block (moods from the markets around the S&P) and the leveraged funds' closing rebalance in close_signals."""
from __future__ import annotations

import json
from datetime import timedelta

from payload_fixtures import DAY, FakeMarket, absent_reasons, at, fake_inputs, make_row, prior_days

from spx_claude_forecast import jsonl_store, recorder, paths as forecast_paths
from spx_claude_forecast.payload.blocks.close_signals import build_close_signals_block
from spx_claude_forecast.payload.blocks.foreign import build_foreign_block
from spx_claude_forecast.payload.blocks.sentiment import build_sentiment_block
from spx_claude_forecast.payload.frozen_inputs import load_leveraged_fund_sizes

CUT = at(14, 30, 12)
# today's quotes at the cut and Schwab's prior close for each: Japan down, the dollar up, the note down, copper up,
# ARKK up more than SPY, crash protection dearer than yesterday, Nasdaq fear above the VIX
QUOTES = {"/NKD": (68_500.0, 69_000.0), "UUP": (29.20, 29.02), "/ZT": (101.80, 101.90), "/HG": (6.80, 6.57),
          "ARKK": (91.0, 87.6), "SPY": (781.0, 781.15), "$SKEW": (154.3, 141.3), "$VXN": (22.6, 20.5), "$VIX": (16.1, 15.4)}


def _market(quotes: dict, when) -> FakeMarket:
    return FakeMarket({symbol: [(when - timedelta(minutes=1), last)] for symbol, (last, _) in quotes.items()})


def _prior_markets(sessions: int = 12) -> dict[str, FakeMarket]:
    """Twelve prior sessions, each quoting every symbol at the same clock and at its day's end, moving a little more
    each session back so the ranks have a spread; the base of each session's move is the session before's last."""
    markets = {}
    for i, day in enumerate(reversed(prior_days(sessions))):          # oldest first
        points = {}
        for symbol, (last, prior) in QUOTES.items():
            level = prior * (1 + 0.001 * i)
            points[symbol] = [(at(14, 30, day=day), level * (1 + 0.0005 * (i % 4))), (at(16, 0, day=day), level)]
        markets[day] = FakeMarket(points)
    return markets


def test_the_sentiment_block_gives_each_mood_as_a_move_since_the_prior_close_ranked_at_the_same_clock(tmp_path):
    inputs = fake_inputs(tmp_path, cut=CUT, market=_market(QUOTES, CUT), prior_markets=_prior_markets(),
                         snapshot_prior_closes={symbol: prior for symbol, (_, prior) in QUOTES.items()})
    block = build_sentiment_block(inputs).data
    assert block["nikkei_futures_pct_since_close"]["v"] == -0.72 and block["dollar_pct_since_close"]["v"] == 0.62
    assert block["two_year_note_price_bp_since_close"]["v"] == -9.8 and block["copper_pct_since_close"]["v"] == 3.5
    assert block["arkk_pct_since_close"]["v"] == 3.88 and block["arkk_minus_spy_pct_since_close"]["v"] == 3.9
    assert block["skew_index"]["v"] == 154.3 and block["skew_index_minus_prior_close"] == 13.0
    assert block["vxn"]["v"] == 22.6 and block["vxn_minus_vix"] == 6.5
    for field in ("nikkei_futures_pct_since_close", "copper_pct_since_close", "arkk_minus_spy_pct_since_close", "skew_index"):
        assert block[field]["r"][1] >= 10, field                        # ranked against the prior sessions at this clock
    assert absent_reasons(build_sentiment_block(inputs)) == {}
    assert not any(abs(v) >= 1000 for v in json.loads(json.dumps(block)).values() if isinstance(v, (int, float)))


def test_a_mood_symbol_that_is_missing_stale_or_without_a_prior_close_is_declared_and_the_block_without_any_is_absent(tmp_path):
    quotes = {k: v for k, v in QUOTES.items() if k != "/HG"}
    market = _market(quotes, CUT)
    market.known["UUP"] = [(CUT - timedelta(minutes=9), 29.20)]      # the dollar's quote stopped nine minutes ago
    prior_closes = {symbol: prior for symbol, (_, prior) in QUOTES.items() if symbol != "/ZT"}
    inputs = fake_inputs(tmp_path, cut=CUT, market=market, snapshot_prior_closes=prior_closes)
    result = build_sentiment_block(inputs)
    gaps = absent_reasons(result)
    assert gaps["sentiment.copper_pct_since_close"] == "not_quoted" and gaps["sentiment.dollar_pct_since_close"] == "stale"
    assert gaps["sentiment.two_year_note_price_bp_since_close"] == "no_prior_close"
    assert "r" not in result.data["nikkei_futures_pct_since_close"]        # no prior sessions: a value, no rank
    empty = fake_inputs(tmp_path, cut=CUT, market=FakeMarket({"$VIX": [(CUT, 15.0)]}))
    assert absent_reasons(build_sentiment_block(empty)) == {"sentiment": "not_recorded"}


def test_the_foreign_block_carries_hong_kong_beside_tokyo_and_europe(tmp_path):
    prior_day = prior_days(1)[0]
    market = FakeMarket({"$HSI": [(at(4, 0), 24_211.0)], "$N225": [(at(2, 0), 69_031.0)]})
    prior = FakeMarket({"$HSI": [(at(4, 0, day=prior_day), 23_785.8)], "$N225": [(at(2, 0, day=prior_day), 69_042.1)]})
    inputs = fake_inputs(tmp_path, cut=CUT, market=market, prior_markets={prior_day: prior})
    result = build_foreign_block(inputs)
    assert result.data["hong_kong_ret_pct"] == 1.79 and result.data["tokyo_ret_pct"] == -0.02
    assert absent_reasons(result)["foreign.europe_ret_pct"] == "not_recorded"


SIZES = {"recorded_at": f"{DAY}T08:41:00-04:00",
         "funds": {"TQQQ": {"assets_usd": 40e9, "leverage": 3.0, "index": "QQQ"}, "SPXL": {"assets_usd": 7e9, "leverage": 3.0, "index": "$SPX"},
                   "SPXU": {"assets_usd": 0.4e9, "leverage": -3.0, "index": "$SPX"}, "SOXL": {"assets_usd": 20e9, "leverage": 3.0, "index": "SMH"}},
         "missing": []}


def test_the_closing_rebalance_sums_each_funds_size_and_leverage_against_its_own_index_move(tmp_path):
    """Nasdaq down 1% and the S&P down 0.5% at the cut: every fund, long and inverse, becomes a seller into the close
    (assets x L x (L - 1) x move); the chip index has no fresh quote, so SOXL is named as left out."""
    market = FakeMarket({"QQQ": [(CUT - timedelta(minutes=1), 594.0)], "SMH": [(CUT - timedelta(minutes=20), 300.0)]})
    inputs = fake_inputs(tmp_path, cut=CUT, market=market, snapshot_prior_closes={"QQQ": 600.0, "SMH": 298.0},
                         daily_closes={"$SPX": [{"day": prior_days(1)[0], "close": 7851.30}]}, row=make_row(CUT, spot=7812.04))
    inputs.leveraged_fund_sizes = SIZES
    result = build_close_signals_block(inputs)
    estimate = result.data["leveraged_fund_rebalance"]
    assert estimate["by_index_usd_bn"] == {"nasdaq_100": -2.4, "spx": -0.23}         # 40bn x 6 x -1% ; (7bn x 6 + 0.4bn x 12) x -0.5%
    assert estimate["into_close_usd_bn"] == -2.63 and estimate["more_usd_bn_per_1pct"] == 2.87
    assert estimate["funds_counted"] == 3 and estimate["funds_left_out"] == ["SOXL"] and estimate["sizes_age_h"] == 5.8
    assert "close_signals.leveraged_fund_rebalance" not in absent_reasons(result)


def test_the_closing_rebalance_is_absent_without_recorded_sizes_or_a_fresh_index_move(tmp_path):
    inputs = fake_inputs(tmp_path, cut=CUT)
    assert absent_reasons(build_close_signals_block(inputs))["close_signals.leveraged_fund_rebalance"] == "no_fund_sizes_recorded"
    inputs.leveraged_fund_sizes = SIZES                                   # sizes, but no market and no prior S&P close
    assert absent_reasons(build_close_signals_block(inputs))["close_signals.leveraged_fund_rebalance"] == "no_index_move_at_cut"


def test_the_recorder_writes_one_line_of_fund_sizes_per_run_and_a_read_takes_the_newest_it_could_know(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    answered = {"TQQQ": {"assets_usd": 38.7e9, "leverage": 3.0}, "SPXU": {"assets_usd": 0.41e9, "leverage": -3.0}}
    morning, evening = at(8, 41), at(16, 26)
    assert recorder.record_leveraged_fund_sizes(paths, now=morning, fetch=lambda symbols: answered) == {"written": 1, "funds": 2, "missing": 13}
    assert recorder.record_leveraged_fund_sizes(paths, now=evening, fetch=lambda symbols: {}) == {"written": 0, "why": "Schwab answered for no fund"}
    assert recorder.record_leveraged_fund_sizes(paths, now=evening, fetch=lambda symbols: {"TQQQ": answered["TQQQ"]})["funds"] == 1
    lines = jsonl_store.read_json_lines(paths.leveraged_fund_sizes_file)
    assert len(lines) == 2 and lines[0]["funds"]["SPXU"] == {"assets_usd": 0.41e9, "leverage": -3.0, "index": "$SPX"}
    assert "SOXL" in lines[0]["missing"] and lines[0]["recorded_at"].startswith(f"{DAY}T08:41")
    notes: list[str] = []
    assert load_leveraged_fund_sizes(paths, at(14, 30), notes)["recorded_at"].startswith(f"{DAY}T08:41")     # the evening line is after the cut
    assert load_leveraged_fund_sizes(paths, at(17, 0), notes)["funds"].keys() == {"TQQQ"}
    assert load_leveraged_fund_sizes(paths, at(8, 0), notes) is None and notes[-1].startswith("leveraged fund sizes: none")
