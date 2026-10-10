"""Plain words for the things Claude's reasons point at, so the phone (and a newcomer reading latest.json) never
meets a dotted path.

A reason is ``{input_field_path, pushes_toward, horizon}`` (spec/record_formats.md, the reply's ``reasons``).
``input_field_path`` is a dotted path into the scene (``tape.rv30_vs_clock_x``); the words for every path the
payload can carry are listed here by block, and a path this file does not know gets its block's name and its
own words with the underscores taken out, so a new field is readable before it is listed. The station's own
code questions (``code.TREND-01``) are named by their id.
"""
from __future__ import annotations

PUSHES_TOWARD_WORDS = {"up": "points up", "down": "points down",
                       "bigger_move": "points to a bigger move", "smaller_move": "points to a smaller move"}
HORIZON_WORDS = {"next_30_minutes": "next 30 min", "next_60_minutes": "next 60 min", "to_close": "to the close",
                 "all": "every horizon"}

# the scene's blocks, as a reader would call them
BLOCK_WORDS = {"data_sources": "the feeds", "clock": "the clock", "calendar": "the calendar", "scale": "the day's scale",
               "tape": "the tape", "open_signals": "the open", "close_signals": "the close", "options": "the options",
               "flow": "the options flow", "internals": "market breadth", "cross_asset": "other markets",
               "overnight": "overnight", "news": "the headlines", "code": "the station's questions",
               "precedents": "the past moments"}

FIELD_WORDS = {
    # data_sources: how old each feed was at the cut
    "data_sources.cut_at": "the time the scene was cut",
    "data_sources.spx_bars.last_finished": "when the last finished 5-minute bar ended",
    "data_sources.spx_bars.age_s": "the age of the last price bar",
    "data_sources.options_diary.row_at": "when the options diary row was written",
    "data_sources.options_diary.age_s": "the age of the options diary row",
    "data_sources.options_diary.book": "which options book the diary row stands on",
    "data_sources.options_diary.coverage": "how much of the options book the diary row covers",
    "data_sources.market_feed.snapshot_at": "when the market feed was last read",
    "data_sources.market_feed.age_s": "the age of the market feed",
    "data_sources.market_feed.failed": "whether the market feed failed",
    "data_sources.tape.fold_at": "when the tape was last folded",
    "data_sources.tape.age_s": "the age of the tape",
    "data_sources.tape.trades_15m_vs_usual_x": "trades on the tape in the last 15 minutes against the usual",
    "data_sources.tape.content": "what the tape holds",
    "data_sources.headlines.last_capture": "when headlines were last captured",
    "data_sources.headlines.age_s": "the age of the headlines",
    "data_sources.load_notes": "notes from loading the inputs",
    # clock
    "clock.min_after_open": "minutes since the open",
    "clock.min_to_close": "minutes to the close",
    "clock.phase": "the part of the session",
    # calendar
    "calendar.day_class": "what kind of day it is",
    "calendar.today": "today's scheduled events",
    "calendar.next_high_tier.kind": "the next big scheduled event",
    "calendar.next_high_tier.sessions_ahead": "sessions until the next big scheduled event",
    "calendar.fomc.phase": "where we are in the Fed meeting cycle",
    "calendar.fomc.sessions_to_next": "sessions until the next Fed meeting",
    "calendar.month.trading_day": "the trading day of the month",
    "calendar.month.days_left": "trading days left in the month",
    "calendar.month.turn_window": "whether this is the turn of the month",
    "calendar.month.pension_stock_minus_bond_mtd_pts": "stocks against bonds this month, for month-end rebalancing",
    "calendar.expiry.today": "whether options expire today",
    "calendar.expiry.monthly_sessions_ahead": "sessions until the monthly options expiry",
    # scale
    "scale.sigma_src": "where the day's expected move comes from",
    "scale.sigma_live_x": "the live expected move against the morning's",
    "scale.atm_iv_pct": "the at-the-money implied volatility",
    "scale.straddle_open_sig": "the move the straddle priced at the open",
    "scale.straddle_left_sig": "the move the straddle still prices",
    "scale.straddle_used_x": "how much of the straddle's priced move is used up",
    "scale.vix1d": "the one-day VIX",
    "scale.vix1d_prior_close": "the one-day VIX at yesterday's close",
    "scale.realized_30m_vs_priced_x": "the last 30 minutes' realized move against the priced move",
    # tape
    "tape.price_minus_close_sig": "price against yesterday's close",
    "tape.price_minus_open_sig": "price against the open",
    "tape.m30_sig": "the move over the last 30 minutes",
    "tape.m60_sig": "the move over the last 60 minutes",
    "tape.range_sig": "the day's range",
    "tape.range_pos": "where price sits in the day's range",
    "tape.five_session_pos": "where price sits in the last five sessions' range",
    "tape.price_minus_vwap_sig": "price against VWAP",
    "tape.vwap_last_touch_min_ago": "minutes since price last touched VWAP",
    "tape.high.minus_price_sig": "the day's high above price",
    "tape.high.at": "when the day's high was set",
    "tape.high.min_ago": "minutes since the day's high",
    "tape.low.minus_price_sig": "the day's low below price",
    "tape.low.at": "when the day's low was set",
    "tape.low.min_ago": "minutes since the day's low",
    "tape.prior_high.minus_price_sig": "yesterday's high against price",
    "tape.prior_high.price_above_for_min": "minutes price has held above yesterday's high",
    "tape.path_eff_since_open": "how straight the path since the open has been",
    "tape.open_crosses": "how many times price has crossed the open",
    "tape.rv30_vs_clock_x": "the last 30 minutes' realized move against the usual for this time of day",
    "tape.last_6x5m_sig": "the last six 5-minute moves",
    "tape.half_hours_vs_close_sig": "each half hour against yesterday's close",
    # open_signals
    "open_signals.gap.settled_sig": "the opening gap, settled",
    "open_signals.gap.settled_pct": "the opening gap in percent",
    "open_signals.gap.print_pct": "the gap at the first print",
    "open_signals.gap.x_straddle": "the gap as a multiple of the straddle",
    "open_signals.gap.filled": "whether the gap has filled",
    "open_signals.gap.kept_pct": "how much of the gap is kept",
    "open_signals.gap.fill_by_close_base_pct": "how often a gap like this fills by the close",
    "open_signals.gap.fill_base_n": "how many sessions the gap-fill rate counts",
    "open_signals.gap.base_on": "what the gap-fill rate is counted on",
    "open_signals.noise_band.sigma_pct": "the width of the opening noise band",
    "open_signals.noise_band.side": "which side of the opening noise band price is on",
    "open_signals.noise_band.dist_bp": "how far price is from the opening noise band",
    "open_signals.noise_band.open_basis": "what the opening noise band is drawn from",
    "open_signals.opt_imbalance_10m.tilt": "the tilt of options trades in the first 10 minutes",
    "open_signals.opt_imbalance_10m.trades": "options trades in the first 10 minutes",
    "open_signals.opt_imbalance_10m.feed_ok": "whether the options trade feed was healthy",
    # close_signals
    "close_signals.r1.bp": "the first-hour move",
    "close_signals.r1.to": "when the first-hour move was measured to",
    "close_signals.r12_bp": "the move over the first two hours",
    "close_signals.rrod_bp": "the end-of-day reversal",
    "close_signals.gamma_open.regime": "the dealer gamma regime at the open",
    "close_signals.gamma_open.book": "the options book at the open",
    "close_signals.gamma_open.at": "when the open's gamma was read",
    "close_signals.gamma_open.net_gex_usd_bn_per_1pct.0dte": "net dealer gamma at the open, same-day options",
    "close_signals.gamma_open.net_gex_usd_bn_per_1pct.0_7dte": "net dealer gamma at the open, options within a week",
    "close_signals.gamma_open.price_minus_flip_sig": "price against the gamma flip level at the open",
    "close_signals.rrod_gate": "the end-of-day reversal gate",
    # options
    "options.book": "which options book this read stands on",
    "options.regime.0dte_oi": "the gamma regime by same-day open interest",
    "options.regime.0dte_volume": "the gamma regime by same-day volume",
    "options.regime.0_7dte_oi": "the gamma regime by open interest within a week",
    "options.net_gex_usd_bn_per_1pct.0dte": "net dealer gamma, same-day options",
    "options.net_gex_usd_bn_per_1pct.0_7dte": "net dealer gamma, options within a week",
    "options.net_gex_usd_bn_per_1pct.at_prior_close_0_7dte": "net dealer gamma at yesterday's close, options within a week",
    "options.gamma_above_pct.0dte": "the share of same-day gamma above price",
    "options.gamma_above_pct.1_7dte": "the share of this week's gamma above price",
    "options.top_strike_share_pct": "how much of the gamma sits at the biggest strike",
    "options.levels_minus_price_sig": "the big option levels against price",
    "options.heavy_strikes_moved_30m": "whether the heavy strikes moved in the last 30 minutes",
    "options.turnover_x_oi": "options volume against open interest",
    "options.skew_put25_minus_call25_volpts": "the put-call skew",
    "options.atm_iv_30m_volpts": "the change in implied volatility over the last 30 minutes",
    # flow
    "flow.tilt_15m": "the tilt of options flow in the last 15 minutes",
    "flow.told_share": "the share of trades the tape could read",
    "flow.lean_30m_vs_usual": "the lean of the last 30 minutes' flow against the usual",
    "flow.big_prints_10m": "big trades in the last 10 minutes",
    "flow.big_prints_bullish_pct": "the share of big trades that were bullish",
    "flow.premium_pace_30m_x": "the pace of options premium in the last 30 minutes against the usual",
    "flow.spy_spread_cents": "the SPY bid-ask spread",
    # internals
    "internals.add_live_approx": "advancers minus decliners, live",
    "internals.adv_share_pct": "the share of stocks advancing",
    "internals.add_30m_change": "the change in advancers minus decliners over the last 30 minutes",
    # cross_asset
    "cross_asset.vix": "the VIX",
    "cross_asset.vix_minus_prior_close": "the VIX against yesterday's close",
    "cross_asset.vix_vix3m": "the VIX against the three-month VIX",
    "cross_asset.backwardated": "whether the VIX curve is backwardated",
    "cross_asset.vix9d_vix": "the nine-day VIX against the VIX",
    "cross_asset.vix_vix3m_prior_close": "the VIX term ratio at yesterday's close",
    "cross_asset.ten_year_bp_since_close": "the ten-year yield since yesterday's close",
    # overnight
    "overnight.es_vs_close_sig": "the futures overnight against yesterday's close",
    "overnight.range_sig": "the overnight range",
    "overnight.legs_sig.after_close": "the move after yesterday's close",
    "overnight.legs_sig.asia": "the move during Asian hours",
    "overnight.legs_sig.europe_open": "the move at the European open",
    "overnight.legs_sig.europe_morning": "the move through the European morning",
    "overnight.legs_sig.pre_report": "the move before the morning data",
    "overnight.legs_sig.report_window": "the move around the morning data",
    "overnight.legs_sig.last_stretch": "the move in the last stretch before the open",
    "overnight.gap_origin": "which overnight leg made the gap",
    "overnight.zn_pct": "the overnight move in Treasury futures",
    # news
    "news.titles": "the headlines",
    "news.captured_60m": "headlines captured in the last 60 minutes",
    "news.index_mover_items_60m": "index-moving headlines in the last 60 minutes",
    "news.kinds": "the kinds of headlines",
    "news.newest_index_mover_min_ago": "minutes since the newest index-moving headline",
    "news.stale_dropped": "stale headlines dropped",
    # precedents
    "precedents.rule": "how the past moments were matched",
    "precedents.candidate_sessions": "how many sessions the past moments were drawn from",
    "precedents.shown": "how many past moments were shown",
    "precedents.order": "the order the past moments were shown in",
    "precedents.cards": "the past moments shown",
    "precedents.now.gap_sig": "the opening gap",
    "precedents.now.price_minus_close_sig": "price against yesterday's close",
    "precedents.now.last_30_minutes_sig": "the move over the last 30 minutes",
    "precedents.now.range_sig": "the day's range",
    "precedents.now.range_position": "where price sits in the day's range",
    "precedents.now.price_minus_vwap_sig": "price against VWAP",
    "precedents.now.vix_term_ratio": "the VIX against the three-month VIX",
}


def field_words(input_field_path: str) -> str:
    """The words for a dotted path: listed words, a code question by its id, else the block and the leaf in words."""
    path = str(input_field_path or "")
    if path in FIELD_WORDS:
        return FIELD_WORDS[path]
    block, _, rest = path.partition(".")
    if block == "code" and rest:
        return f"the station's question {rest}"
    leaf = rest.replace(".", " ").replace("_", " ").strip()
    block_words = BLOCK_WORDS.get(block, block.replace("_", " "))
    return f"{block_words}: {leaf}" if leaf else block_words


def reason_in_words(reason: dict | None) -> dict | None:
    """The reason with its words beside its codes: ``input_field_path_in_words``, ``pushes_toward_in_words`` and
    ``horizon_in_words``; None stays None."""
    if not isinstance(reason, dict):
        return None
    pushes, horizon = reason.get("pushes_toward"), reason.get("horizon")
    return {**reason,
            "input_field_path_in_words": field_words(reason.get("input_field_path")),
            "pushes_toward_in_words": PUSHES_TOWARD_WORDS.get(pushes, str(pushes or "").replace("_", " ")),
            "horizon_in_words": HORIZON_WORDS.get(horizon, str(horizon or "").replace("_", " "))}
