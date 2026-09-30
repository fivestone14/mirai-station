"""Every threshold in one place: the labels, the sums, the tape lane and the question text read them here.

Measured cuts. Where they came from: ``spec/measure_cuts.py`` over the 48 SPX sessions on disk
(2026-07-10 and 2026-07-20 to 2026-09-25, each with a full day of minute bars and 100 or more diary
rows), written to ``spec/cuts.json`` with the percentile and sample size behind each number. One rule for all of
them: an SPX cut sits at the same percentile of the same measurement on SPX history as SNDK JEV's
cut sits on SNDK history, so it keeps SNDK's meaning without SNDK's scale. SPX's sigma is a full
day's expected move from the index's model volatility and its intraday moves are small against
it, so most SPX cuts land near half of SNDK's. The base rates are counted on SPX directly with the
SPX bands. A test holds every measured constant here to spec/cuts.json; re-measure, then copy the
numbers.

Declared cuts, at the bottom, are splits and ratios whose meaning is their own words ("more than 60%
on one side", "twice a typical minute", "the VIX above the three-month VIX"); they are not fitted.
spec/cuts.json records where each one falls on the same sessions, as the share of SPX and of SNDK
observations under it, and a test holds those records to the values here.

The question set's constants, below those, are every threshold the final question set's text names
(spec/question_set.json "constants"), by the same name, with the set's note beside each.

The question docs never carry a number of their own: their text names these by the keys of
QUESTION_CONSTANTS ("{move_rule_sigma}") and ask.load_questions fills them in.
"""
from __future__ import annotations

# A real 30-minute move (SNDK PRO's 0.15 sigma move rule, its 65th percentile of 30-minute moves).
MOVE_RULE_SIGMA = 0.09
# A heavy strike within reach (SNDK's 0.5 sigma, the median distance to the nearer wall).
WALL_NEAR_SIGMA = 0.63
# A real leg from the open, for the session's shape.
SHAPE_CUT_SIGMA = 0.09
# The at-the-money implied volatility flat band over 30 minutes, in vol points.
IV_FLAT_BAND_PTS = 0.8
# Realized movement over 30 minutes against the move priced for 30 minutes.
REALIZED_QUIET_RATIO = 0.38
REALIZED_WILD_RATIO = 0.61
# The nearer wall's share of the board's options weight.
WALL_THIN_SHARE = 0.03
WALL_THICK_SHARE = 0.098
# The single heaviest strike's share of the board's weight.
GRIP_SPREAD_SHARE = 0.035
GRIP_CONCENTRATED_SHARE = 0.159
# One strike holding more than this share of today's option volume is the busiest strike.
BUSIEST_STRIKE_SHARE = 0.11

# The sums (questions/spx_hour.json): flat within the band at the mark.
NEXT_30_FLAT_BAND_SIGMA = 0.07
NEXT_60_FLAT_BAND_SIGMA = 0.11
# How often each band happened on SPX, in percent of reads (spec/cuts.json base_rates).
NEXT_30_UP_PCT, NEXT_30_DOWN_PCT, NEXT_30_FLAT_PCT = 24, 21, 55
NEXT_60_UP_PCT, NEXT_60_DOWN_PCT, NEXT_60_FLAT_PCT = 22, 19, 59

# The opening lane's tape unit: held at this share of sigma until three 5-minute slices exist, and
# floored at this share after (the SPX floor is the smallest unit measured; SNDK's never bound).
RULER_HOLD_SIGMA = 0.12
RULER_FLOOR_SIGMA = 0.03
# The 10-minute sum's bands in tape units: flat within the first, big beyond the second.
TAPE_FLAT_UNITS = 0.42
TAPE_BIG_UNITS = 0.89
TAPE_DOWN_BIG_PCT, TAPE_DOWN_SMALL_PCT, TAPE_FLAT_PCT, TAPE_UP_SMALL_PCT, TAPE_UP_BIG_PCT = 18, 17, 36, 14, 15

# ---- declared, not fitted (spec/cuts.json "declared": where each falls on SPX and SNDK history)
# A share between these is "about even"; outside them, a lean (options weight, dealers' delta, the
# expected move's sides, the call-put split).
EVEN_SPLIT_LOW, EVEN_SPLIT_HIGH = 0.40, 0.60
MINUTE_WIDTH_CUT = 2.0          # a minute wider than twice today's typical minute
TURNOVER_LOW, TURNOVER_HIGH = 1.0, 3.0   # contracts traded against the standing open position
PATH_ORDERLY, PATH_CHOPPY = 0.60, 0.30   # path efficiency of a 30-minute move: net move over distance travelled
PACE_BIGGER, PACE_SMALLER = 1.5, 2.0 / 3.0   # the last 10 minutes against the 10 before
PAUSE_BRIEF_MIN, PAUSE_LONG_MIN = 3, 10      # pauses inside a 30-minute move
PULLBACK_SHARE = 0.25
RSI_OVERSOLD, RSI_OVERBOUGHT = 30, 70
VIX_CURVE_FLAT = 1.0            # VIX over the three-month VIX: at or above it, near-term fear is priced above longer-term
# The siege box's own verdict cuts on a wall touch's SPY volume, a percentile of normal for its clock
# window (skills/siege/siege/contracts.py): a siege at or above the first, quiet at or below the second.
WALL_TOUCH_SIEGE_PERCENTILE, WALL_TOUCH_QUIET_PERCENTILE = 70, 30
# The 0DTE book's last hour before its settle: its gamma decays fastest then, and its at-the-money
# implied volatility moved a median 2.1 vol points in 30 minutes against 0.8 earlier in the day
# (spec/cuts.json, iv_change30_median_pts), the expiry clock rather than the market.
ZERO_DTE_LAST_HOUR_MIN = 60
# A follow-on (the Fed's press conference) is read with the release it follows when due within this many minutes of it.
FOLLOW_ON_MIN = 45
# The premarket lane's sums (questions/spx_premarket_hour.json) are graded from the settled open: flat within
# this band 10 minutes on, and within NEXT_30_FLAT_BAND_SIGMA 30 minutes on. Not measured yet: the live
# sum's 30-minute band scaled by the square root of 10 over 30.
OPEN_10_FLAT_BAND_SIGMA = 0.04
# The average-price grade's guards (integral.py), each a count, a clock or a rank against the same window on the recent sessions.
INTEGRAL_MISSING_BARS_MAX = 1   # bars a window may miss and still be graded on its average, never the mark bar
BAD_TICK_PCT = 0.999            # share of the same window's 1-minute moves a bad tick's jump, and its jump back, are at or above
STALE_READ_MIN = 2              # a read whose spot no bar traded in this many minutes up to its row minute is stale
SHARP_MOVE_TOP = 2              # a window's biggest minute is sharp in the top 2 of it and the last 20 sessions' (top 2 of 21)
TIER_MIN_RIGHT_CALLS = 10       # fewest right calls on the box's recent sessions a right call's strength tier is ranked among

# ---- the question set's constants (spec/question_set.json "constants"), in the set's order, each with its
# note from there. By the owner's rule (2026-09-27) none sizes or judges a market measure: each is a window, a
# clock, a lookback or minimum count, a rank band edge, a share that defines a word, a touch tolerance or a unit.
# The few it shares with the cuts above (vix_curve_flat, the siege percentiles) are defined once, above. A test
# holds every one of them to the set.
GAP_HALF_SHARE = 0.5  # half-way share for gap kept, shock giveback, reaction giveback
GAP_TOUCH_SIGMA = 0.02  # touch tolerance for yesterday's close
OVERNIGHT_RANK_MIN_NIGHTS = 10  # fewest usable prior nights (no roll, holiday or short night) to rank an overnight measure against
NIGHT_RANK_COUNT = 20  # nights (or sessions, or weekends) a same-clock rank looks back over
SAME_CLOCK_MIN_SESSIONS = 10  # fewest usable prior sessions to rank a session measure against at the same clock (the owner's rank rule)
GIVEBACK_THIRD = 0.33  # stall line for an opening drive
RANGE_TOP_SHARE = 0.75  # top quarter of today's range
RANGE_BOTTOM_SHARE = 0.25  # bottom quarter of today's range
OPEN_CLUSTER_MIN = 3  # bursts that make a cluster
TICK_BURST_PCT = 0.95  # same-minute percentile of $TICK bar highs/lows that counts as a burst
BIG_PRINT_PCT = 0.999  # share of the same minutes' single 0DTE trades a large trade is at or above
BIG_MIN_PRINTS = 5  # minimum large trades
THIN_VOLUME_PCT = 0.2  # same-clock percentile under which new volume is too thin
MIN_RANK_SESSIONS = 5  # fewest prior sessions for a same-clock median or a link fit; a rank needs SAME_CLOCK_MIN_SESSIONS
SKEW_STEEP_RANK = 0.8  # top-fifth rank of the tilt against the recent sessions at this minute
SKEW_FLAT_RANK = 0.2  # bottom-fifth rank of the tilt against the recent sessions at this minute
TOP_FIFTH = 0.8  # top-fifth rank
BOTTOM_FIFTH = 0.2  # bottom-fifth rank
HALF_RANK = 0.5  # median rank
THIRD_HI = 0.67  # top-third rank
THIRD_LO = 0.33  # bottom-third rank
WINDOW_10_MIN = 10  # window in minutes
WINDOW_30_MIN = 30  # window in minutes
WINDOW_60_MIN = 60  # window in minutes
MOVE_BURST_SHARE = 0.8  # one 5-minute chunk's share of the 30-minute move for a burst
IB_BREAK_SIGMA = 0.03  # first-hour break buffer
ONE_RATIO = 1.0  # one-to-one line
SPY_SPREAD_TIGHT = 0.015  # SPY spread in dollars: 1 cent
EVENT_DUE_MIN = 60  # event due window
EVENT_DIGEST_MIN = 120  # digest window after a release
SPEAKER_WINDOW_MIN = 60  # Fed speaker window
TICK_CLUSTER = 3  # TICK extreme readings in 30 minutes
STRESS_RETREAT_SHARE = 0.3  # VIX retreat share from its session high for exhaustion
STRESS_HOLD_SHARE = 0.1  # VIX retreat share under which a bounce is not believed
CUSHION_THIN = 0.5  # share of today's call-heavy balance left after a move
WALL_TOUCH_SIGMA = 0.1  # beyond or back from a wall
DEFENSE_MIN_EVENTS = 10  # hits for a defense read
VALUE_EDGE_SIGMA = 0.02  # beyond yesterday's value area
ACCEPT_MINUTES = 20  # minutes beyond a level for acceptance
LEVEL_REACH_SIGMA = 0.05  # pressing a level
CROSS_LOOKBACK_MIN = 20  # round-level cross window
ROUND_NEAR_SIGMA = 0.06  # near a round 50/100-point level
ON_BREAK_SIGMA = 0.05  # beyond an overnight edge
ON_NEAR_SIGMA = 0.15  # near an overnight edge
SECTOR_COUNT = 11  # sector funds
MEGA_COUNT = 8  # megacap basket size
MEGA_ONE_NAME_SHARE = 0.33  # one name's share of the index move
ONE_DAY = 1  # one trading day
OPEX_WEEK_DAYS = 4  # trading days before monthly expiry
AFTER_OPEX_DAYS = 5  # trading days after
REBAL_WINDOW_DAYS = 2  # last trading days of the month for rebalancing
MONTH_TURN_DAYS = 3  # first trading days of the month
SHOCK_LOOKBACK_MIN = 60  # shock lookback
SHOCK_FRESH_MIN = 10  # fresh shock window
REACTION_EXTEND_SIGMA = 0.05  # beyond the reaction end
ONE_NAME_SHARE = 0.35  # megacap share of a burst
SPREAD_COUNT = 2  # other giants for spreading
SETTLE_SEAT_SIGMA = 0.05  # seated for the settle
OUTSIDE_BUFFER_SIGMA = 0.03  # buffer beyond a level
BRIEF_DIR_MIN = 0.2  # brief lean on a -1..+1 scale
BRIEF_CONF_MIN = 0.3  # brief confidence
FLOW_LEAN_RANK = 0.8  # flow lean rank vs clock
OFI_Z = 1.0  # order-flow imbalance z
COLLAR_NEAR_SIGMA = 0.5  # near a quarter-end collar strike

# What a question doc may name in braces. The key is the constant's name in lower case.
QUESTION_CONSTANTS = {name.lower(): value for name, value in dict(globals()).items()
                      if name.isupper() and isinstance(value, (int, float))}
