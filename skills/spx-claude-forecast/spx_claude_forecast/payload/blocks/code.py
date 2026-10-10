"""The code block: up to twelve of the read's code-feature answers, in the catalog's own words, for the question
groups whose facts no other block of the scene already carries.

The station answers 79 code questions per read (spx_jev.mirai_prediction.code_features) and stores them under
raw/code_features/; the loader hands them over as ``{qid: option or None}``. Most of them restate a number
another block gives exactly (the move since the open, the distance to the flip, SPY's volume pace), so the
block keeps only the groups listed in CODE_GROUPS: the composites and residuals (a break held or failed, a
leg's age, fear beyond what price explains, an extreme confirmed or not) that no block's numbers add up to.
Every catalog group is in CODE_GROUPS or in COVERED_GROUPS, which names the block field that carries it; a
test holds the two lists to the catalog so a new group is placed on purpose. The catalog carries no usual
answer today; when one appears under USUAL_ANSWER_KEY, an answer that merely matches it yields its slot.
"""
from __future__ import annotations

from ... import station_stores
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs

MAX_CODE_ANSWERS = 12          # the cap in build_plan.md row 16; the block is the second thing trimmed when the scene is over its size
USUAL_ANSWER_KEY = "usual_answer"   # not in today's catalog: an answer equal to it says nothing new and yields its slot
# The groups kept: composites and residuals no block of the scene carries as a number. Why each stays: a leg's age,
# the day's follow-through, a break held or failed, the latest leg against the day, trend against chop and a burst
# bar are shapes of the tape the tape block's sums do not say; vol, skew and VVIX beyond what price explains, the
# front curve's shift, the event ruler and the VIX's open surprise are residuals the scale and cross_asset blocks
# do not take; the overnight location, a tiring edge, the strike defense and the option quote width are not in the
# options or overnight blocks; participation, lockstep, rotation and the heavyweights are not in internals; the
# opening type, ease of movement and the volume profile are not in flow; the macro gap, the rates tape, a burst's
# state, the release's reaction path, bitcoin's lead and the leveraged-ETF tilt have no block at all.
CODE_GROUPS = frozenset({
    "trend/last_30m_move", "trend/leg_aging", "trend/day_character", "trend/pace_change", "trend/leg_vs_day",
    "trend/trend_vs_chop", "shared/break_held_or_failed", "shared/burst", "shared/extreme_confirmation",
    "volatility/vol_vs_price", "volatility/skew_change", "volatility/vol_of_vol", "volatility/front_curve",
    "volatility/event_ruler", "volatility/vix_open_surprise", "volatility/day_range_regime",
    "levels/overnight_location", "levels/edge_fatigue", "options/strike_defense", "options/option_quote_width",
    "breadth/participation", "breadth/sector_lockstep", "breadth/risk_rotation", "breadth/heavyweight_pull",
    "breadth/heavyweight_in_play", "flow/ease_of_movement", "flow/opening_type", "flow/volume_profile",
    "macro/macro_gap", "macro/rates_activity", "events/burst_state", "events/reaction_path",
    "sentiment/bitcoin_lead", "sentiment/leveraged_etf_tilt",
})
# The groups left out, each with the scene field that already carries its fact as a number.
COVERED_GROUPS = {
    "trend/move_from_open": "open_signals.noise_band, tape.price_minus_open_sig",
    "volatility/implied_size": "scale.straddle_used_x, scale.straddle_left_sig",
    "volatility/realized_size": "tape.rv30_vs_clock_x",
    "volatility/range_used": "tape.range_sig",
    "volatility/skew_level": "options.skew_rr25_volpts",
    "volatility/range_expansion": "tape.range_sig (ranked)",
    "volatility/front_curve_level": "cross_asset.vix9d_vix, cross_asset.backwardated",
    "levels/prior_close": "tape.price_minus_close_sig, open_signals.gap",
    "levels/today_range_position": "tape.range_pos, tape.high, tape.low",
    "levels/opening_size": "overnight.range_sig, open_signals.noise_band",
    "levels/barrier_room": "options.levels_minus_price_sig, tape.prior_high",
    "options/gamma_state": "options.levels_minus_price_sig (gamma_flip)",
    "shared/pinned": "options.top_strike_share_pct, options.levels_minus_price_sig (magnet)",
    "options/magnet": "options.levels_minus_price_sig (magnet, charm_wall), options.heavy_strikes_moved_30m",
    "options/option_flow": "flow.tilt_15m, flow.lean_30m_vs_usual",
    "flow/vwap_stretch": "tape.price_minus_vwap_sig, tape.vwap_last_touch_min_ago",
    "flow/volume_pace": "flow.spy_volume_30m_x",
    "macro/overnight_direction": "overnight.legs_sig",
    "events/event_day": "calendar.day_class, calendar.today",
    "session/expiry_cycle": "calendar.expiry",
    "session/month_turn_flow": "calendar.month",
}


def build_code_block(inputs: FrozenInputs) -> BlockResult:
    """The read's code answers for the uncovered groups, in catalog order, at most MAX_CODE_ANSWERS."""
    answers = {qid: answer for qid, answer in (inputs.code_answers or {}).items() if answer is not None}
    if not answers:
        return whole_block_absent("code", "no_code_answers")
    result = BlockResult()
    catalog = _code_features().load_catalog()
    for qid in sorted(set(answers) - {question["id"] for question in catalog}):
        result.leave_out(f"code.{qid}", "qid_not_in_catalog")
    candidates: list[tuple[bool, int, str, str]] = []       # (says nothing new, catalog index, qid, answer)
    for index, question in enumerate(catalog):
        qid, answer = question["id"], answers.get(question["id"])
        if answer is None or question.get("group") not in CODE_GROUPS:
            continue
        if answer not in (question.get("options") or ()):
            result.leave_out(f"code.{qid}", "answer_not_in_catalog_options")
            continue
        candidates.append((answer == question.get(USUAL_ANSWER_KEY), index, qid, answer))
    kept = sorted(sorted(candidates)[:MAX_CODE_ANSWERS], key=lambda c: c[1])
    if not kept:
        result.leave_out("code", "no_uncovered_code_answers")
    result.data = {qid: answer for _, _, qid, answer in kept}
    return result


def _code_features():
    return station_stores.import_spx_jev("mirai_prediction.code_features")
