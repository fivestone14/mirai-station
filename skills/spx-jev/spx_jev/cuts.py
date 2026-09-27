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
# An outlier day against last night's close.
OUTLIER_DAY_SIGMA = 0.53
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

# The sums (questions/spx_hour.json): flat within the band at the mark; large past the furthest-point cut.
NEXT_30_FLAT_BAND_SIGMA = 0.07
NEXT_30_LARGE_BAND_SIGMA = 0.16
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

# What a question doc may name in braces. The key is the constant's name in lower case.
QUESTION_CONSTANTS = {name.lower(): value for name, value in dict(globals()).items()
                      if name.isupper() and isinstance(value, (int, float))}
